from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from mi_health.api.auth import generate_key
from mi_health.config import Settings
from mi_health.credentials import CredentialStore
from mi_health.db import Database


@pytest.fixture
def ctx(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "test.db",
        master_key=Fernet.generate_key().decode(),
        region="cn",
        host="127.0.0.1",
        port=8330,
        backfill_days=90,
        raw_retention_days=180,
        high_interval=1800,
        sleep_interval=7200,
        other_interval=43200,
        admin_password_hash=None,
        session_secret="test-session-secret",
        log_level="INFO",
        allowed_hosts=["testserver", "127.0.0.1", "127.0.0.1:*"],
    )
    db = Database(settings.db_path)
    store = CredentialStore(db, settings.master_key, settings.region)
    store.set("u1", "pass-token", "cn")
    return settings, db, store


def make_key(db: Database, scopes: str = "read") -> str:
    token, hashed, prefix = generate_key()
    db.create_api_key(hashed, prefix, "test", scopes)
    return token
