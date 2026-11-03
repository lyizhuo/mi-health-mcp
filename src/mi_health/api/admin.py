"""Admin session, API-key management, and Xiaomi QR login."""

from __future__ import annotations

import json
import secrets
import time
import uuid
from typing import Any

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from mi_health.api.auth import generate_key, normalize_scopes
from mi_health.db import Database

router = APIRouter()
LOGIN_URL_ENDPOINT = "https://account.xiaomi.com/longPolling/loginUrl"
LOGIN_PREFIX = "&&&START&&&"
_hasher = PasswordHasher()


class LoginRequest(BaseModel):
    password: str


class CreateKeyRequest(BaseModel):
    label: str | None = None
    scopes: list[str] | str = "read"


def _store(request: Request):
    return request.app.state.store


def _db(request: Request) -> Database:
    return request.app.state.db


def _verify_admin(request: Request) -> dict[str, Any]:
    session = request.session
    if not session.get("admin"):
        raise HTTPException(status_code=401, detail="admin login required")
    return session


def _require_csrf(request: Request, session: dict[str, Any]) -> None:
    token = request.headers.get("x-csrf-token", "")
    if not token or not secrets.compare_digest(token, str(session.get("csrf", ""))):
        raise HTTPException(status_code=403, detail="invalid CSRF token")


def _check_password(settings, password: str) -> None:
    if not settings.admin_password_hash:
        raise HTTPException(status_code=503, detail="admin password is not configured")
    try:
        _hasher.verify(settings.admin_password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError) as exc:
        raise HTTPException(status_code=401, detail="invalid password") from exc


@router.post("/api/auth/login")
async def login(body: LoginRequest, request: Request) -> dict[str, Any]:
    _check_password(request.app.state.settings, body.password)
    request.session.clear()
    request.session["admin"] = True
    request.session["csrf"] = secrets.token_urlsafe(24)
    return {
        "status": "ok",
        "data": {"authenticated": True, "csrf_token": request.session["csrf"]},
    }


@router.post("/api/auth/logout")
async def logout(request: Request) -> dict[str, Any]:
    request.session.clear()
    return {"status": "ok", "data": {"authenticated": False}}


@router.get("/api/auth/me")
async def me(request: Request) -> dict[str, Any]:
    session = request.session
    authenticated = bool(session.get("admin"))
    return {
        "status": "ok",
        "data": {
            "authenticated": authenticated,
            "csrf_token": session.get("csrf") if authenticated else None,
        },
    }


@router.get("/api/keys")
async def list_keys(request: Request) -> dict[str, Any]:
    _verify_admin(request)
    data = _db(request).list_api_keys()
    return {"status": "ok", "data": data, "meta": {"count": len(data)}}


@router.post("/api/keys", status_code=201)
async def create_key(body: CreateKeyRequest, request: Request) -> dict[str, Any]:
    session = _verify_admin(request)
    _require_csrf(request, session)
    scopes = normalize_scopes(body.scopes)
    token, hashed, prefix = generate_key()
    _db(request).create_api_key(hashed, prefix, body.label, ",".join(sorted(scopes)))
    return {
        "status": "ok",
        "data": {
            "token": token,
            "prefix": prefix,
            "label": body.label,
            "scopes": sorted(scopes),
        },
        "meta": {"note": "store this token now; it is shown only once"},
    }


@router.delete("/api/keys/{prefix}")
async def revoke_key(prefix: str, request: Request) -> dict[str, Any]:
    session = _verify_admin(request)
    _require_csrf(request, session)
    if not _db(request).revoke_api_key(prefix):
        raise HTTPException(status_code=404, detail="key prefix not found")
    return {"status": "ok", "data": {"prefix": prefix, "revoked": True}}


def _parse_login_payload(text: str) -> dict[str, Any]:
    if text.startswith(LOGIN_PREFIX):
        text = text[len(LOGIN_PREFIX) :]
    return json.loads(text)


@router.post("/api/auth/qr/start")
async def qr_start(request: Request, region: str = "cn") -> dict[str, Any]:
    session = _verify_admin(request)
    _require_csrf(request, session)
    params = {
        "_qrsize": "480",
        "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
        "callback": "https://sts.api.io.mi.com/sts",
        "_hasLogo": "false",
        "sid": "xiaomiio",
        "serviceParam": "",
        "_locale": "zh_CN",
        "_dc": str(int(time.time() * 1000)),
    }
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=20) as client:
            resp = await client.get(LOGIN_URL_ENDPOINT, params=params)
            resp.raise_for_status()
            data = _parse_login_payload(resp.text)
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Xiaomi login service failed: {exc}") from exc
    if not data.get("lp") or not data.get("loginUrl"):
        raise HTTPException(status_code=502, detail="Xiaomi did not return a login URL")
    token = uuid.uuid4().hex
    request.app.state.qr_sessions[token] = {
        "lp": data["lp"],
        "login_url": data["loginUrl"],
        "region": region or "cn",
        "created_at": time.time(),
        "expires_in": int(data.get("timeout", 300)),
        "status": "waiting",
    }
    return {
        "status": "ok",
        "data": {
            "qr_token": token,
            "login_url": data["loginUrl"],
            "expires_in": int(data.get("timeout", 300)),
        },
        "meta": {"instruction": "scan the login_url QR with the Xiaomi app"},
    }


@router.get("/api/auth/qr/poll")
async def qr_poll(token: str, request: Request) -> dict[str, Any]:
    _verify_admin(request)
    sessions = request.app.state.qr_sessions
    sess = sessions.get(token)
    if sess is None:
        raise HTTPException(status_code=404, detail="unknown qr token")
    if sess["status"] == "confirmed":
        return {"status": "ok", "data": {"status": "confirmed", "user_id": sess.get("user_id")}}
    if time.time() - sess["created_at"] > sess["expires_in"] + 10:
        sess["status"] = "expired"
        return {"status": "ok", "data": {"status": "expired"}}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(sess["lp"])
    except httpx.HTTPError:
        return {"status": "ok", "data": {"status": "waiting"}}
    if resp.status_code != 200:
        return {"status": "ok", "data": {"status": "waiting"}}
    try:
        body = _parse_login_payload(resp.text)
    except (json.JSONDecodeError, ValueError):
        return {"status": "ok", "data": {"status": "waiting"}}
    if body.get("code") != 0 or not body.get("userId") or not body.get("passToken"):
        return {"status": "ok", "data": {"status": "waiting"}}
    user_id = str(body["userId"])
    _store(request).set(user_id, str(body["passToken"]), sess["region"])
    sess["status"] = "confirmed"
    sess["user_id"] = user_id
    return {"status": "ok", "data": {"status": "confirmed", "user_id": user_id}}
