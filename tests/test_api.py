from datetime import datetime

from fastapi.testclient import TestClient

from mi_health.api.app import create_app
from mi_health.models import DailyActivity
from tests.conftest import make_key


def test_api_auth_envelope_and_scopes(ctx):
    settings, db, store = ctx
    db.upsert_daily([
        DailyActivity(
            id="d1", provider="mi_fitness", source_type="cloud_session", user_id="u1",
            date="2026-11-03", steps=1234, distance_m=1000, active_kcal=50,
            collected_at=datetime(2026, 11, 3, 12, 0),
        )
    ])
    read = make_key(db, "read")
    sync = make_key(db, "read,sync")
    app = create_app(settings, db, store, mcp=None)
    client = TestClient(app)

    assert client.get("/api/summary?start_date=2026-11-03&end_date=2026-11-03").status_code == 401
    assert (
        client.get(
            "/api/summary?start_date=2026-11-03&end_date=2026-11-03",
            headers={"Authorization": f"Bearer {read}"},
        ).json()["data"][0]["steps"]
        == 1234
    )
    assert (
        client.post("/api/sync", json={}, headers={"Authorization": f"Bearer {read}"}).status_code
        == 403
    )
    response = client.post(
        "/api/sync", json={"data_types": ["daily_activity"]}, headers={"Authorization": f"Bearer {sync}"}
    )
    assert response.status_code == 202
    assert response.json()["data"][0]["data_type"] == "daily_activity"

    assert (
        client.get(
            "/api/summary?start_date=bad&end_date=2026-11-03",
            headers={"Authorization": f"Bearer {read}"},
        ).status_code
        == 400
    )
    assert (
        client.get(
            "/api/summary?start_date=2026-11-03&end_date=2026-11-03",
            headers={"Authorization": "Bearer invalid"},
        ).status_code
        == 401
    )
