"""API key authentication and MCP auth context."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import HTTPException, Request

from mi_health.db import Database

KEY_PREFIX = "mhk_"
SCOPES = {"read", "sync"}
current_auth: ContextVar[AuthContext | None] = ContextVar("current_auth", default=None)
_LAST_TOUCH: dict[str, float] = {}


@dataclass(frozen=True)
class AuthContext:
    key_id: str
    prefix: str
    label: str | None
    scopes: frozenset[str]


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_key() -> tuple[str, str, str]:
    token = KEY_PREFIX + secrets.token_urlsafe(32)
    return token, hash_key(token), token[:12]


def normalize_scopes(raw: str | Iterable[str]) -> frozenset[str]:
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    scopes = {item.strip() for item in items if item.strip()}
    unknown = scopes - SCOPES
    if unknown:
        raise ValueError(f"unknown scopes: {', '.join(sorted(unknown))}")
    return frozenset(scopes or {"read"})


def extract_token(headers: dict[bytes, bytes]) -> str | None:
    auth = headers.get(b"authorization", b"").decode("latin-1").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    key = headers.get(b"x-api-key", b"").decode("latin-1").strip()
    return key or None


def authenticate(db: Database, token: str) -> AuthContext | None:
    row = db.find_api_key(hash_key(token))
    if not row or row["revoked"]:
        return None
    now = time.time()
    if now - _LAST_TOUCH.get(row["id"], 0) > 60:
        db.touch_api_key(row["id"])
        _LAST_TOUCH[row["id"]] = now
    return AuthContext(
        key_id=row["id"],
        prefix=row["prefix"],
        label=row["label"],
        scopes=normalize_scopes(row["scopes"]),
    )


async def require_key(request: Request) -> AuthContext:
    token = extract_token({k.encode(): v.encode() for k, v in request.headers.items()})
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    ctx = authenticate(request.app.state.db, token)
    if not ctx:
        raise HTTPException(
            status_code=401,
            detail="Invalid or revoked API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.auth = ctx
    return ctx


def require_scope(scope: str):
    async def dependency(request: Request) -> AuthContext:
        ctx = await require_key(request)
        if scope not in ctx.scopes:
            raise HTTPException(status_code=403, detail=f"API key lacks '{scope}' scope")
        return ctx

    return dependency


def require_current_scope(scope: str) -> AuthContext:
    ctx = current_auth.get()
    if not ctx:
        raise PermissionError("Unauthenticated MCP request")
    if scope not in ctx.scopes:
        raise PermissionError(f"API key lacks '{scope}' scope")
    return ctx


class MCPAuthMiddleware:
    """ASGI middleware for the mounted MCP app."""

    def __init__(self, app, db: Database):
        self.app = app
        self.db = db

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        token = extract_token(headers)
        ctx = authenticate(self.db, token) if token else None
        if not ctx:
            body = json.dumps({"status": "error", "error": {"message": "unauthorized"}}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        reset = current_auth.set(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            current_auth.reset(reset)
