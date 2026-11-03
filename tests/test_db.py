from datetime import datetime

from mi_health.models import DailyActivity, HeartRateSample, SleepSession, SleepStage


def test_daily_and_heart_rate_roundtrip(ctx):
    _, db, _ = ctx
    assert db.upsert_daily([
        DailyActivity(
            id="d1", provider="mi_fitness", source_type="cloud_session", user_id="u1",
            date="2026-11-03", steps=1234, distance_m=1000, active_kcal=50,
        )
    ]) == 1
    assert db.upsert_heart_rate([
        HeartRateSample(
            id="hr1", provider="mi_fitness", source_type="cloud_session", user_id="u1",
            timestamp=datetime(2026, 11, 3, 12, 0), bpm=70, sample_type="passive",
        )
    ]) == 1

    summary = db.query_summary("u1", "2026-11-03", "2026-11-03")
    assert summary[0]["steps"] == 1234
    assert len(db.query_heart_rate("u1", "2026-11-03", "2026-11-03")) == 1


def test_sleep_hrv_roundtrip_and_stages(ctx):
    _, db, _ = ctx
    db.upsert_sleep([
        SleepSession(
            id="s1", provider="mi_fitness", source_type="cloud_session", user_id="u1",
            sleep_id="s1", start_at=datetime(2026, 10, 28, 0, 35),
            end_at=datetime(2026, 10, 28, 8, 44), duration_minutes=489,
            time_asleep_minutes=460, time_awake_minutes=29, sleep_score=85,
            stages=[SleepStage(stage="deep", minutes=100)], avg_hrv_ms=76,
            min_hrv_ms=39, max_hrv_ms=107, hrv_median_ms=75, hrv_std_ms=0,
            hrv_lower_quantile_ms=52, hrv_upper_quantile_ms=102,
            hrv_base_min_ms=34, hrv_base_max_ms=76,
            hrv_analysis_at=datetime(2026, 10, 28, 8, 0),
        )
    ])
    rows = db.query_sleep("u1", "2026-10-28", "2026-10-28")
    assert rows[0]["avg_hrv_ms"] == 76
    assert rows[0]["hrv_std_ms"] == 0
    assert rows[0]["stages"] == [{"stage": "deep", "minutes": 100}]


def test_sync_state_and_api_keys(ctx):
    _, db, _ = ctx
    db.set_sync_state("u1", "daily_activity", last_record_at="2026-11-03", status="ok")
    state = db.get_sync_state("u1", "daily_activity")
    assert state["status"] == "ok"
    db.create_api_key("hash", "mhk_prefix", "agent", "read,sync")
    assert db.find_api_key("hash")["scopes"] == "read,sync"
    assert db.revoke_api_key("mhk_prefix") is True
    assert db.find_api_key("hash")["revoked"] == 1
