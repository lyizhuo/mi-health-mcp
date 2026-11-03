import json

import pytest

from mi_health.xiaomi.client import MiFitnessCloudAdapter, _is_authentication_error


async def _collect(items):
    return [item async for item in items]


def test_auth_error_detection():
    assert _is_authentication_error(401, "denied")
    assert _is_authentication_error(0, "session expired")
    assert not _is_authentication_error(500, "temporary")


def test_nonnegative_int_preserves_zero():
    adapter = MiFitnessCloudAdapter(user_id="u1", pass_token="p1")
    assert adapter._optional_nonnegative_int(0) == 0
    assert adapter._optional_nonnegative_int("76") == 76
    assert adapter._optional_nonnegative_int(None) is None


@pytest.mark.asyncio
async def test_iter_sleep_maps_hrv(monkeypatch):
    adapter = MiFitnessCloudAdapter(user_id="u1", pass_token="p1")
    adapter._connected = True
    adapter._client = object()
    payload = {
        "bedtime": 1761667200,
        "wake_up_time": 1761696000,
        "duration": 480,
        "sleep_score": 85,
        "items": [{"start_time": 1761667200, "end_time": 1761670800, "state": 1}],
        "avg_hrv": 76,
        "min_hrv": 39,
        "max_hrv": 107,
        "hrv_median": 75,
        "hrv_std": 0,
        "hrv_lower_quantile": 52,
        "hrv_upper_quantile": 102,
        "hrv_base_min": 34,
        "hrv_base_max": 76,
        "hrv_analysis_timestamp": 1761690000,
    }

    async def fake_fetch(key, start_date, end_date, region=None):
        assert key == "sleep"
        return [{"time": 1761696000, "zone_offset": 28800, "value": json.dumps(payload)}]

    monkeypatch.setattr(adapter, "_fetch_key", fake_fetch)
    rows = await _collect(adapter.iter_sleep_sessions("2026-10-28", "2026-10-28"))
    assert rows[0].avg_hrv_ms == 76
    assert rows[0].hrv_std_ms == 0
    assert rows[0].hrv_analysis_at is not None
    assert rows[0].stages[0].stage == "deep"
