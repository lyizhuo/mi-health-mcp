"""Encrypted Xiaomi credential storage.

The passToken is a long-lived credential. It is encrypted with a master key
that comes from the environment (never stored in SQLite).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

from mi_health.db import Database

SETTING_KEY = "xiaomi_credentials"


class CredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    user_id: str
    pass_token: str
    region: str


class CredentialStore:
    def __init__(self, db: Database, master_key: str | None, default_region: str = "cn"):
        self.db = db
        self.master_key = master_key
        self.default_region = default_region

    def _fernet(self) -> Fernet:
        if not self.master_key:
            raise CredentialError(
                "MI_HEALTH_MASTER_KEY is required to store Xiaomi credentials "
                "(generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
            )
        try:
            return Fernet(self.master_key.encode())
        except (ValueError, TypeError) as exc:
            raise CredentialError("MI_HEALTH_MASTER_KEY must be a urlsafe base64 32-byte Fernet key") from exc

    def set(self, user_id: str, pass_token: str, region: str | None = None) -> None:
        payload = json.dumps(
            {"user_id": user_id, "pass_token": pass_token, "region": region or self.default_region},
            ensure_ascii=False,
        ).encode()
        token = self._fernet().encrypt(payload).decode()
        self.db.set_setting(SETTING_KEY, token)

    def clear(self) -> None:
        self.db.set_setting(SETTING_KEY, "")

    def get(self) -> Credentials | None:
        stored = self.db.get_setting(SETTING_KEY)
        if stored:
            try:
                payload = json.loads(self._fernet().decrypt(stored.encode()))
                return Credentials(
                    user_id=str(payload["user_id"]),
                    pass_token=str(payload["pass_token"]),
                    region=str(payload.get("region") or self.default_region),
                )
            except (InvalidToken, ValueError, KeyError, TypeError) as exc:
                raise CredentialError("stored Xiaomi credentials cannot be decrypted; check MI_HEALTH_MASTER_KEY") from exc

        user_id = os.environ.get("MI_HEALTH_XIAOMI_USER_ID")
        pass_token = os.environ.get("MI_HEALTH_XIAOMI_PASS_TOKEN")
        if user_id and pass_token:
            return Credentials(user_id, pass_token, os.environ.get("MI_HEALTH_REGION", self.default_region))
        return None

    def require(self) -> Credentials:
        creds = self.get()
        if not creds:
            raise CredentialError(
                "Xiaomi credentials are not configured; run `mi-health set-credentials` "
                "or use the QR login flow"
            )
        return creds
