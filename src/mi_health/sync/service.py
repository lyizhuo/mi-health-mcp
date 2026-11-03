"""Sync one data type from Xiaomi cloud into SQLite."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from mi_health.config import Settings
from mi_health.credentials import CredentialStore
from mi_health.db import Database
from mi_health.xiaomi.client import MiFitnessCloudAdapter

logger = logging.getLogger(__name__)

DATA_TYPES = [
    "daily_activity",
    "sleep",
    "heart_rate",
    "workouts",
    "body_measurements",
    "spo2",
    "stress",
    "abnormal_heart_beat",
]

# Re-fetch a small overlap so late-arriving/updated cloud records are not missed.
LOOKBACK_DAYS = {
    "daily_activity": 2,
    "heart_rate": 2,
    "spo2": 2,
    "stress": 2,
    "abnormal_heart_beat": 7,
    "sleep": 7,
    "workouts": 30,
    "body_measurements": 30,
}


def _today(region: str) -> date:
    tz = ZoneInfo("Asia/Shanghai") if region in ("", "cn") else UTC
    return datetime.now(tz).date()


def _record_at(data_type: str, record: Any) -> datetime | None:
    if data_type == "daily_activity":
        return None
    return getattr(record, "end_at", None) or getattr(record, "timestamp", None) or getattr(record, "start_at", None)


class SyncService:
    def __init__(self, db: Database, store: CredentialStore, settings: Settings):
        self.db = db
        self.store = store
        self.settings = settings

    def _client(self, creds) -> MiFitnessCloudAdapter:
        client = MiFitnessCloudAdapter(
            user_id=creds.user_id, pass_token=creds.pass_token, region=creds.region
        )
        client.http_timeout = 20.0
        client.request_retries = 3
        client.max_pages = 200
        return client

    async def sync_type(
        self,
        data_type: str,
        start_date: str | None = None,
        end_date: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        if data_type not in DATA_TYPES:
            raise ValueError(f"unknown data type: {data_type}")
        creds = self.store.require()
        end = date.fromisoformat(end_date) if end_date else _today(creds.region)
        state = self.db.get_sync_state(creds.user_id, data_type)

        if start_date:
            start = date.fromisoformat(start_date)
        elif force or not state or not state.get("last_record_at"):
            start = end - timedelta(days=self.settings.backfill_days - 1)
        else:
            last = date.fromisoformat(str(state["last_record_at"])[:10])
            overlap = end - timedelta(days=LOOKBACK_DAYS[data_type] - 1)
            start = min(last, overlap)

        if start > end:
            start = end

        run_id = self.db.start_run(creds.user_id, data_type)
        client = self._client(creds)
        records = 0
        last_record_at: str | None = None
        chunks: list[dict[str, Any]] = []
        try:
            if not await client.connect():
                raise RuntimeError(client.last_error or "cannot connect to Mi Fitness cloud")
            cursor = start
            while cursor <= end:
                chunk_end = min(cursor + timedelta(days=6), end)
                chunk_start_text = cursor.isoformat()
                chunk_end_text = chunk_end.isoformat()
                rows = [row async for row in self._iter(client, data_type, chunk_start_text, chunk_end_text)]
                written = self._upsert(data_type, rows)
                chunk_last = self._max_record(data_type, rows)
                if chunk_last:
                    last_record_at = max(last_record_at or "", chunk_last)
                records += written
                chunks.append(
                    {
                        "start_date": chunk_start_text,
                        "end_date": chunk_end_text,
                        "records": written,
                        "data_as_of": chunk_last,
                    }
                )
                self.db.set_sync_state(
                    creds.user_id,
                    data_type,
                    last_record_at=last_record_at,
                    status="ok",
                )
                cursor = chunk_end + timedelta(days=1)
            self.db.finish_run(run_id, status="ok", records=records)
            return {
                "status": "ok",
                "data_type": data_type,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "records": records,
                "chunks": chunks,
            }
        except Exception as exc:  # noqa: BLE001 - reported to API/UI
            message = f"{type(exc).__name__}: {exc}"
            logger.warning("sync %s failed: %s", data_type, message)
            self.db.set_sync_state(
                creds.user_id,
                data_type,
                last_record_at=last_record_at,
                status="error",
                error=message,
            )
            self.db.finish_run(run_id, status="error", records=records, error=message)
            return {
                "status": "error",
                "data_type": data_type,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "records": records,
                "error": message,
                "chunks": chunks,
            }
        finally:
            await client.close()

    async def _iter(self, client: MiFitnessCloudAdapter, data_type: str, start: str, end: str):
        if data_type == "daily_activity":
            async for row in client.iter_daily_activity(start, end):
                yield row
        elif data_type == "sleep":
            async for row in client.iter_sleep_sessions(start, end):
                yield row
        elif data_type == "heart_rate":
            async for row in client.iter_heart_rate(start, end):
                yield row
        elif data_type == "workouts":
            async for row in client.iter_workouts(start, end):
                yield row
        elif data_type == "body_measurements":
            async for row in client.iter_body_measurements(start, end):
                yield row
        elif data_type == "spo2":
            async for row in client.iter_spo2(start, end):
                yield row
        elif data_type == "stress":
            async for row in client.iter_stress(start, end):
                yield row
        elif data_type == "abnormal_heart_beat":
            async for row in client.iter_abnormal_heart_beat(start, end):
                yield row
        else:
            raise ValueError(f"unknown data type: {data_type}")

    def _upsert(self, data_type: str, rows: list[Any]) -> int:
        method: Callable[[list[Any]], int] = {
            "daily_activity": self.db.upsert_daily,
            "sleep": self.db.upsert_sleep,
            "heart_rate": self.db.upsert_heart_rate,
            "workouts": self.db.upsert_workouts,
            "body_measurements": self.db.upsert_body,
            "spo2": self.db.upsert_spo2,
            "stress": self.db.upsert_stress,
            "abnormal_heart_beat": self.db.upsert_abnormal,
        }[data_type]
        return method(rows)

    def _max_record(self, data_type: str, rows: list[Any]) -> str | None:
        if data_type == "daily_activity":
            dates = [r.date for r in rows]
            return max(dates) if dates else None
        stamps = [_record_at(data_type, r) for r in rows]
        stamps = [s for s in stamps if s]
        return max(stamps).isoformat() if stamps else None

    def run(self, data_type: str, start_date: str | None = None, end_date: str | None = None, force: bool = False) -> dict:
        return asyncio.run(self.sync_type(data_type, start_date, end_date, force))
