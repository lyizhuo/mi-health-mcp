"""Runtime settings loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Process settings.

    Secrets (master key, admin password hash, session secret) are never stored in
    the repository; they come from the environment or a secret manager.
    """

    data_dir: Path
    db_path: Path
    master_key: str | None
    region: str
    host: str
    port: int
    backfill_days: int
    raw_retention_days: int
    high_interval: int
    other_interval: int
    admin_password_hash: str | None
    session_secret: str | None
    log_level: str
    allowed_hosts: list[str]
    https_only: bool = False
    web_dir: Path = Path("web/dist")

    @classmethod
    def from_env(cls, **overrides: object) -> Settings:
        data_dir = Path(os.environ.get("MI_HEALTH_DATA_DIR", Path.home() / ".local/share/mi-health"))
        values = {
            "data_dir": data_dir,
            "db_path": Path(os.environ.get("MI_HEALTH_DB_PATH", data_dir / "mi_health.db")),
            "master_key": os.environ.get("MI_HEALTH_MASTER_KEY"),
            "region": os.environ.get("MI_HEALTH_REGION", "cn"),
            "host": os.environ.get("MI_HEALTH_HOST", "127.0.0.1"),
            "port": int(os.environ.get("MI_HEALTH_PORT", "8330")),
            "backfill_days": int(os.environ.get("MI_HEALTH_BACKFILL_DAYS", "90")),
            "raw_retention_days": int(os.environ.get("MI_HEALTH_RAW_RETENTION_DAYS", "180")),
            "high_interval": int(os.environ.get("MI_HEALTH_SYNC_HIGH_INTERVAL", "1800")),
            "other_interval": int(os.environ.get("MI_HEALTH_SYNC_OTHER_INTERVAL", "43200")),
            "admin_password_hash": os.environ.get("MI_HEALTH_ADMIN_PASSWORD_HASH"),
            "session_secret": os.environ.get("MI_HEALTH_SESSION_SECRET"),
            "log_level": os.environ.get("MI_HEALTH_LOG_LEVEL", "INFO"),
            "allowed_hosts": [h.strip() for h in os.environ.get("MI_HEALTH_ALLOWED_HOSTS", "127.0.0.1,127.0.0.1:*,localhost,localhost:*").split(",") if h.strip()],
            "https_only": os.environ.get("MI_HEALTH_HTTPS_ONLY", "0").lower() in ("1", "true", "yes"),
            "web_dir": Path(os.environ.get("MI_HEALTH_WEB_DIR", "web/dist")),
        }
        values.update(overrides)
        return cls(**values)  # type: ignore[arg-type]

    def interval_for(self, data_type: str) -> int:
        if data_type in {"daily_activity", "sleep", "heart_rate", "spo2", "stress", "abnormal_heart_beat"}:
            return self.high_interval
        return self.other_interval

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
