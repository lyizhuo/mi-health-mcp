from datetime import UTC, datetime, timedelta

import pytest

from mi_health.models import DailyActivity
from mi_health.sync.service import SyncService
from mi_health.sync.worker import Worker


class FakeClient:
    last_error = None

    async def connect(self):
        return True

    async def close(self):
        return None

    async def iter_daily_activity(self, start_date, end_date):
        yield DailyActivity(
            id="d1",
            provider="mi_fitness",
            source_type="cloud_session",
            user_id="u1",
            date="2026-11-03",
            steps=1234,
            distance_m=1000,
            active_kcal=50,
            collected_at=datetime(2026, 11, 3, 12, 0),
        )


def test_sleep_uses_high_frequency_interval(ctx):
    settings, _, _ = ctx
    assert settings.interval_for("sleep") == settings.high_interval


def test_scheduler_polls_sleep_every_high_interval(ctx):
    settings, db, store = ctx
    worker = Worker(db, store, settings)

    def sleep_jobs() -> int:
        with db._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM sync_jobs WHERE data_type='sleep'"
            ).fetchone()
        return int(row[0])

    def mark_synced(minutes_ago: int) -> None:
        synced_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
        with db._write() as conn:
            conn.execute(
                "INSERT INTO sync_state(user_id,data_type,last_synced_at,status) VALUES (?,?,?,?) "
                "ON CONFLICT(user_id,data_type) DO UPDATE SET last_synced_at=excluded.last_synced_at",
                ("u1", "sleep", synced_at.isoformat(), "ok"),
            )

    mark_synced(29)
    worker._schedule_due()
    assert sleep_jobs() == 0

    mark_synced(31)
    worker._schedule_due()
    assert sleep_jobs() == 1


def test_worker_requeues_orphaned_jobs(ctx):
    _, db, _ = ctx
    job_id = db.enqueue_job("u1", "daily_activity", None, None)
    with db._connect() as conn:
        conn.execute("UPDATE sync_jobs SET status='running' WHERE id=?", (job_id,))
        conn.commit()
    assert db.reset_running_jobs() == 1
    assert db.get_job(job_id)["status"] == "pending"


@pytest.mark.asyncio
async def test_sync_type_batches_and_updates_state(ctx, monkeypatch):
    settings, db, store = ctx
    service = SyncService(db, store, settings)
    monkeypatch.setattr(service, "_client", lambda creds: FakeClient())

    result = await service.sync_type(
        "daily_activity", "2026-11-03", "2026-11-03", force=True
    )

    assert result["status"] == "ok"
    assert result["records"] == 1
    rows = db.query_summary("u1", "2026-11-03", "2026-11-03")
    assert rows[0]["steps"] == 1234
    state = db.get_sync_state("u1", "daily_activity")
    assert state["status"] == "ok"
    assert state["last_record_at"] == "2026-11-03"
