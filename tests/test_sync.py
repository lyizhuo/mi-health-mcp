from datetime import datetime

import pytest

from mi_health.models import DailyActivity
from mi_health.sync.service import SyncService


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
