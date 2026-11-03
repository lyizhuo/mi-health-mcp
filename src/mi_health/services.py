"""Shared query layer for REST and MCP."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from mi_health.config import Settings
from mi_health.credentials import CredentialStore
from mi_health.db import Database
from mi_health.sync.service import DATA_TYPES


class NotConfiguredError(RuntimeError):
    pass


class QueryService:
    def __init__(self, db: Database, store: CredentialStore, settings: Settings):
        self.db = db
        self.store = store
        self.settings = settings

    def credentials(self):
        creds = self.store.get()
        if not creds:
            raise NotConfiguredError("Xiaomi credentials are not configured")
        return creds

    def user_id(self) -> str:
        return self.credentials().user_id

    def meta(self, data_type: str, count: int) -> dict[str, Any]:
        state = self.db.get_sync_state(self.user_id(), data_type)
        last_synced = state.get("last_synced_at") if state else None
        interval = self.settings.interval_for(data_type)
        stale = True
        next_sync_at = None
        if last_synced:
            try:
                last_dt = datetime.fromisoformat(last_synced)
                stale = datetime.now(UTC) - last_dt > timedelta(seconds=interval * 2)
                next_sync_at = (last_dt + timedelta(seconds=interval)).isoformat()
            except ValueError:
                stale = True
        return {
            "count": count,
            "data_as_of": (state or {}).get("last_record_at"),
            "last_synced_at": last_synced,
            "next_sync_at": next_sync_at,
            "stale": stale,
            "sync_status": (state or {}).get("status", "never"),
            "sync_error": (state or {}).get("error"),
            "source": "mi_fitness_cloud",
        }

    def envelope(self, data_type: str, data: Any) -> dict[str, Any]:
        count = len(data) if isinstance(data, list) else 1
        return {"status": "ok", "data": data, "meta": self.meta(data_type, count)}

    @staticmethod
    def parse_date(value: str, name: str) -> str:
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError(f"{name} must be YYYY-MM-DD") from exc

    def summary(self, start_date: str, end_date: str) -> dict[str, Any]:
        start = self.parse_date(start_date, "start_date")
        end = self.parse_date(end_date, "end_date")
        data = self.db.query_summary(self.user_id(), start, end)
        return self.envelope("daily_activity", data)

    def sleep(self, start_date: str, end_date: str, include_naps: bool = True) -> dict[str, Any]:
        start = self.parse_date(start_date, "start_date")
        end = self.parse_date(end_date, "end_date")
        data = self.db.query_sleep(self.user_id(), start, end, include_naps)
        return self.envelope("sleep", data)

    def heart_rate(
        self, start_date: str, end_date: str, sample_type: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        start = self.parse_date(start_date, "start_date")
        end = self.parse_date(end_date, "end_date")
        if limit is not None and limit < 0:
            raise ValueError("limit must be >= 0")
        data = self.db.query_heart_rate(
            self.user_id(), start, end, sample_type, limit if limit is not None else 10000
        )
        return self.envelope("heart_rate", data)

    def workouts(self, start_date: str, end_date: str) -> dict[str, Any]:
        start = self.parse_date(start_date, "start_date")
        end = self.parse_date(end_date, "end_date")
        return self.envelope("workouts", self.db.query_workouts(self.user_id(), start, end))

    def body_measurements(self, start_date: str, end_date: str) -> dict[str, Any]:
        start = self.parse_date(start_date, "start_date")
        end = self.parse_date(end_date, "end_date")
        return self.envelope("body_measurements", self.db.query_body(self.user_id(), start, end))

    def spo2(self, start_date: str, end_date: str, limit: int | None = None) -> dict[str, Any]:
        start = self.parse_date(start_date, "start_date")
        end = self.parse_date(end_date, "end_date")
        data = self.db.query_spo2(self.user_id(), start, end, limit if limit is not None else 10000)
        return self.envelope("spo2", data)

    def stress(self, start_date: str, end_date: str, limit: int | None = None) -> dict[str, Any]:
        start = self.parse_date(start_date, "start_date")
        end = self.parse_date(end_date, "end_date")
        data = self.db.query_stress(self.user_id(), start, end, limit if limit is not None else 10000)
        return self.envelope("stress", data)

    def abnormal_heart_beat(self, start_date: str, end_date: str) -> dict[str, Any]:
        start = self.parse_date(start_date, "start_date")
        end = self.parse_date(end_date, "end_date")
        return self.envelope(
            "abnormal_heart_beat", self.db.query_abnormal(self.user_id(), start, end)
        )

    def coverage(self) -> dict[str, Any]:
        data = self.db.coverage(self.user_id())
        return {"status": "ok", "data": data, "meta": {"count": len(data), "source": "local_cache"}}

    def status(self) -> dict[str, Any]:
        creds = self.credentials()
        states = self.db.list_sync_states(creds.user_id)
        stale = {s["data_type"]: self.meta(s["data_type"], 0)["stale"] for s in states}
        return {
            "status": "ok",
            "data": {
                "configured": True,
                "user_id": creds.user_id,
                "region": creds.region,
                "sync": states,
                "stale": stale,
            },
            "meta": {"source": "local_cache"},
        }

    def metric_series(
        self, metric: str, start_date: str, end_date: str, granularity: str = "day"
    ) -> dict[str, Any]:
        allowed = {"steps", "distance_m", "active_kcal"}
        if metric not in allowed:
            raise ValueError(f"metric must be one of {sorted(allowed)}")
        summary = self.summary(start_date, end_date)["data"]
        series = [{"date": row["date"], "value": row.get(metric, 0)} for row in summary]
        if granularity == "week":
            bucket: dict[str, float] = {}
            for item in series:
                d = date.fromisoformat(item["date"])
                key = (d - timedelta(days=d.weekday())).isoformat()
                bucket[key] = bucket.get(key, 0) + float(item["value"] or 0)
            series = [{"date": k, "value": v} for k, v in sorted(bucket.items())]
        elif granularity == "month":
            bucket = {}
            for item in series:
                key = item["date"][:7] + "-01"
                bucket[key] = bucket.get(key, 0) + float(item["value"] or 0)
            series = [{"date": k, "value": v} for k, v in sorted(bucket.items())]
        else:
            granularity = "day"
        return {"status": "ok", "data": series, "meta": {**self.meta("daily_activity", len(series)), "granularity": granularity}}

    def enqueue_sync(
        self,
        data_types: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        creds = self.credentials()
        types = data_types or DATA_TYPES
        unknown = sorted(set(types) - set(DATA_TYPES))
        if unknown:
            raise ValueError(f"unknown data types: {', '.join(unknown)}")
        start = self.parse_date(start_date, "start_date") if start_date else None
        end = self.parse_date(end_date, "end_date") if end_date else None
        jobs = []
        for data_type in types:
            job_id = self.db.enqueue_job(creds.user_id, data_type, start, end, force)
            job = self.db.get_job(job_id)
            jobs.append(job or {"id": job_id, "data_type": data_type, "status": "pending"})
        return {"status": "accepted", "data": jobs, "meta": {"count": len(jobs)}}

    def job(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        return {"status": "ok", "data": job, "meta": {"source": "local"}}
