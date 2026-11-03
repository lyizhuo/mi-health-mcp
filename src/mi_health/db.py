"""SQLite storage (WAL) with batched writes and sync/job bookkeeping."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mi_health.models import (
    AbnormalHeartBeatEvent,
    BodyMeasurement,
    DailyActivity,
    HeartRateSample,
    SleepSession,
    SpO2Sample,
    StressSample,
    Workout,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_activity (
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    steps INTEGER NOT NULL DEFAULT 0,
    distance_m REAL NOT NULL DEFAULT 0,
    active_kcal REAL NOT NULL DEFAULT 0,
    total_kcal REAL,
    floors INTEGER,
    active_minutes INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, date)
);

CREATE TABLE IF NOT EXISTS heart_rate_samples (
    user_id TEXT NOT NULL,
    id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    bpm INTEGER NOT NULL,
    sample_type TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, id)
);
CREATE INDEX IF NOT EXISTS idx_hr_user_ts ON heart_rate_samples(user_id, timestamp);

CREATE TABLE IF NOT EXISTS sleep_sessions (
    user_id TEXT NOT NULL,
    sleep_id TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    time_asleep_minutes INTEGER NOT NULL,
    time_awake_minutes INTEGER NOT NULL,
    sleep_score INTEGER,
    is_nap INTEGER NOT NULL DEFAULT 0,
    stages_json TEXT NOT NULL DEFAULT '[]',
    avg_hrv_ms INTEGER,
    min_hrv_ms INTEGER,
    max_hrv_ms INTEGER,
    hrv_median_ms INTEGER,
    hrv_std_ms INTEGER,
    hrv_lower_quantile_ms INTEGER,
    hrv_upper_quantile_ms INTEGER,
    hrv_base_min_ms INTEGER,
    hrv_base_max_ms INTEGER,
    hrv_analysis_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, sleep_id)
);
CREATE INDEX IF NOT EXISTS idx_sleep_user_start ON sleep_sessions(user_id, start_at);

CREATE TABLE IF NOT EXISTS workouts (
    user_id TEXT NOT NULL,
    workout_id TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    distance_m REAL,
    calories_kcal REAL,
    avg_heart_rate_bpm INTEGER,
    max_heart_rate_bpm INTEGER,
    avg_pace_sec_per_km REAL,
    max_pace_sec_per_km REAL,
    total_steps INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, workout_id)
);

CREATE TABLE IF NOT EXISTS body_measurements (
    user_id TEXT NOT NULL,
    id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    weight_kg REAL NOT NULL,
    bmi REAL,
    body_fat_pct REAL,
    muscle_mass_kg REAL,
    water_pct REAL,
    bone_mass_kg REAL,
    visceral_fat_score INTEGER,
    basal_metabolism_kcal INTEGER,
    metabolic_age INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, id)
);

CREATE TABLE IF NOT EXISTS spo2_samples (
    user_id TEXT NOT NULL,
    id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    spo2_pct INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, id)
);
CREATE INDEX IF NOT EXISTS idx_spo2_user_ts ON spo2_samples(user_id, timestamp);

CREATE TABLE IF NOT EXISTS stress_samples (
    user_id TEXT NOT NULL,
    id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    stress_score INTEGER NOT NULL,
    level TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, id)
);
CREATE INDEX IF NOT EXISTS idx_stress_user_ts ON stress_samples(user_id, timestamp);

CREATE TABLE IF NOT EXISTS abnormal_heart_beat_events (
    user_id TEXT NOT NULL,
    id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, id)
);

CREATE TABLE IF NOT EXISTS sync_state (
    user_id TEXT NOT NULL,
    data_type TEXT NOT NULL,
    last_record_at TEXT,
    last_synced_at TEXT,
    status TEXT NOT NULL DEFAULT 'never',
    error TEXT,
    PRIMARY KEY (user_id, data_type)
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    data_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    records INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS sync_jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    data_type TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    force INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON sync_jobs(status, created_at);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,
    label TEXT,
    scopes TEXT NOT NULL DEFAULT 'read',
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _write(self):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    # ------------------------------------------------------------------ upserts

    def upsert_daily(self, rows: Iterable[DailyActivity]) -> int:
        values = [
            (r.user_id, r.date, r.steps, r.distance_m, r.active_kcal, r.total_kcal, r.floors, r.active_minutes)
            for r in rows
        ]
        if not values:
            return 0
        with self._write() as conn:
            conn.executemany(
                """
                INSERT INTO daily_activity(user_id,date,steps,distance_m,active_kcal,total_kcal,floors,active_minutes)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id,date) DO UPDATE SET
                    steps=excluded.steps, distance_m=excluded.distance_m,
                    active_kcal=excluded.active_kcal, total_kcal=excluded.total_kcal,
                    floors=excluded.floors, active_minutes=excluded.active_minutes,
                    updated_at=CURRENT_TIMESTAMP
                """,
                values,
            )
        return len(values)

    def upsert_heart_rate(self, rows: Iterable[HeartRateSample]) -> int:
        values = [(r.user_id, r.id, _iso(r.timestamp), r.bpm, r.sample_type) for r in rows]
        if not values:
            return 0
        with self._write() as conn:
            conn.executemany(
                """
                INSERT INTO heart_rate_samples(user_id,id,timestamp,bpm,sample_type)
                VALUES (?,?,?,?,?)
                ON CONFLICT(user_id,id) DO UPDATE SET
                    timestamp=excluded.timestamp, bpm=excluded.bpm,
                    sample_type=excluded.sample_type, updated_at=CURRENT_TIMESTAMP
                """,
                values,
            )
        return len(values)

    def upsert_sleep(self, rows: Iterable[SleepSession]) -> int:
        values = [
            (
                r.user_id,
                r.sleep_id,
                _iso(r.start_at),
                _iso(r.end_at),
                r.duration_minutes,
                r.time_asleep_minutes,
                r.time_awake_minutes,
                r.sleep_score,
                int(r.is_nap),
                json.dumps([s.model_dump() for s in r.stages], ensure_ascii=False),
                r.avg_hrv_ms,
                r.min_hrv_ms,
                r.max_hrv_ms,
                r.hrv_median_ms,
                r.hrv_std_ms,
                r.hrv_lower_quantile_ms,
                r.hrv_upper_quantile_ms,
                r.hrv_base_min_ms,
                r.hrv_base_max_ms,
                _iso(r.hrv_analysis_at),
            )
            for r in rows
        ]
        if not values:
            return 0
        with self._write() as conn:
            conn.executemany(
                """
                INSERT INTO sleep_sessions(
                    user_id,sleep_id,start_at,end_at,duration_minutes,time_asleep_minutes,
                    time_awake_minutes,sleep_score,is_nap,stages_json,avg_hrv_ms,min_hrv_ms,
                    max_hrv_ms,hrv_median_ms,hrv_std_ms,hrv_lower_quantile_ms,
                    hrv_upper_quantile_ms,hrv_base_min_ms,hrv_base_max_ms,hrv_analysis_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id,sleep_id) DO UPDATE SET
                    start_at=excluded.start_at, end_at=excluded.end_at,
                    duration_minutes=excluded.duration_minutes,
                    time_asleep_minutes=excluded.time_asleep_minutes,
                    time_awake_minutes=excluded.time_awake_minutes,
                    sleep_score=excluded.sleep_score, is_nap=excluded.is_nap,
                    stages_json=excluded.stages_json, avg_hrv_ms=excluded.avg_hrv_ms,
                    min_hrv_ms=excluded.min_hrv_ms, max_hrv_ms=excluded.max_hrv_ms,
                    hrv_median_ms=excluded.hrv_median_ms, hrv_std_ms=excluded.hrv_std_ms,
                    hrv_lower_quantile_ms=excluded.hrv_lower_quantile_ms,
                    hrv_upper_quantile_ms=excluded.hrv_upper_quantile_ms,
                    hrv_base_min_ms=excluded.hrv_base_min_ms,
                    hrv_base_max_ms=excluded.hrv_base_max_ms,
                    hrv_analysis_at=excluded.hrv_analysis_at, updated_at=CURRENT_TIMESTAMP
                """,
                values,
            )
        return len(values)

    def upsert_workouts(self, rows: Iterable[Workout]) -> int:
        values = [
            (
                r.user_id, r.workout_id, r.activity_type, _iso(r.start_at), _iso(r.end_at),
                r.duration_minutes, r.distance_m, r.calories_kcal, r.avg_heart_rate_bpm,
                r.max_heart_rate_bpm, r.avg_pace_sec_per_km, r.max_pace_sec_per_km, r.total_steps,
            )
            for r in rows
        ]
        if not values:
            return 0
        with self._write() as conn:
            conn.executemany(
                """
                INSERT INTO workouts(user_id,workout_id,activity_type,start_at,end_at,
                    duration_minutes,distance_m,calories_kcal,avg_heart_rate_bpm,
                    max_heart_rate_bpm,avg_pace_sec_per_km,max_pace_sec_per_km,total_steps)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id,workout_id) DO UPDATE SET
                    activity_type=excluded.activity_type, start_at=excluded.start_at,
                    end_at=excluded.end_at, duration_minutes=excluded.duration_minutes,
                    distance_m=excluded.distance_m, calories_kcal=excluded.calories_kcal,
                    avg_heart_rate_bpm=excluded.avg_heart_rate_bpm,
                    max_heart_rate_bpm=excluded.max_heart_rate_bpm,
                    avg_pace_sec_per_km=excluded.avg_pace_sec_per_km,
                    max_pace_sec_per_km=excluded.max_pace_sec_per_km,
                    total_steps=excluded.total_steps, updated_at=CURRENT_TIMESTAMP
                """,
                values,
            )
        return len(values)

    def upsert_body(self, rows: Iterable[BodyMeasurement]) -> int:
        values = [
            (
                r.user_id, r.id, _iso(r.timestamp), r.weight_kg, r.bmi, r.body_fat_pct,
                r.muscle_mass_kg, r.water_pct, r.bone_mass_kg, r.visceral_fat_score,
                r.basal_metabolism_kcal, r.metabolic_age,
            )
            for r in rows
        ]
        if not values:
            return 0
        with self._write() as conn:
            conn.executemany(
                """
                INSERT INTO body_measurements(user_id,id,timestamp,weight_kg,bmi,body_fat_pct,
                    muscle_mass_kg,water_pct,bone_mass_kg,visceral_fat_score,
                    basal_metabolism_kcal,metabolic_age)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id,id) DO UPDATE SET
                    timestamp=excluded.timestamp, weight_kg=excluded.weight_kg, bmi=excluded.bmi,
                    body_fat_pct=excluded.body_fat_pct, muscle_mass_kg=excluded.muscle_mass_kg,
                    water_pct=excluded.water_pct, bone_mass_kg=excluded.bone_mass_kg,
                    visceral_fat_score=excluded.visceral_fat_score,
                    basal_metabolism_kcal=excluded.basal_metabolism_kcal,
                    metabolic_age=excluded.metabolic_age, updated_at=CURRENT_TIMESTAMP
                """,
                values,
            )
        return len(values)

    def upsert_spo2(self, rows: Iterable[SpO2Sample]) -> int:
        values = [(r.user_id, r.id, _iso(r.timestamp), r.spo2_pct) for r in rows]
        if not values:
            return 0
        with self._write() as conn:
            conn.executemany(
                """
                INSERT INTO spo2_samples(user_id,id,timestamp,spo2_pct) VALUES (?,?,?,?)
                ON CONFLICT(user_id,id) DO UPDATE SET timestamp=excluded.timestamp,
                    spo2_pct=excluded.spo2_pct, updated_at=CURRENT_TIMESTAMP
                """,
                values,
            )
        return len(values)

    def upsert_stress(self, rows: Iterable[StressSample]) -> int:
        values = [(r.user_id, r.id, _iso(r.timestamp), r.stress_score, r.level) for r in rows]
        if not values:
            return 0
        with self._write() as conn:
            conn.executemany(
                """
                INSERT INTO stress_samples(user_id,id,timestamp,stress_score,level) VALUES (?,?,?,?,?)
                ON CONFLICT(user_id,id) DO UPDATE SET timestamp=excluded.timestamp,
                    stress_score=excluded.stress_score, level=excluded.level,
                    updated_at=CURRENT_TIMESTAMP
                """,
                values,
            )
        return len(values)

    def upsert_abnormal(self, rows: Iterable[AbnormalHeartBeatEvent]) -> int:
        values = [
            (r.user_id, r.id, r.event_id, _iso(r.start_at), _iso(r.end_at), r.duration_seconds)
            for r in rows
        ]
        if not values:
            return 0
        with self._write() as conn:
            conn.executemany(
                """
                INSERT INTO abnormal_heart_beat_events(user_id,id,event_id,start_at,end_at,duration_seconds)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(user_id,id) DO UPDATE SET event_id=excluded.event_id,
                    start_at=excluded.start_at, end_at=excluded.end_at,
                    duration_seconds=excluded.duration_seconds, updated_at=CURRENT_TIMESTAMP
                """,
                values,
            )
        return len(values)

    # ------------------------------------------------------------------ queries

    def query_summary(self, user_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_activity WHERE user_id=? AND date BETWEEN ? AND ? ORDER BY date",
                (user_id, start_date, end_date),
            ).fetchall()
        return [dict(r) for r in rows]

    def query_sleep(self, user_id: str, start_date: str, end_date: str, include_naps: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sleep_sessions WHERE user_id=? AND substr(start_at,1,10) BETWEEN ? AND ?"
        if not include_naps:
            sql += " AND is_nap=0"
        sql += " ORDER BY start_at"
        with self._connect() as conn:
            rows = conn.execute(sql, (user_id, start_date, end_date)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["stages"] = json.loads(item.pop("stages_json") or "[]")
            except json.JSONDecodeError:
                item["stages"] = []
            result.append(item)
        return result

    def query_heart_rate(
        self, user_id: str, start_date: str, end_date: str, sample_type: str | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM heart_rate_samples WHERE user_id=? AND substr(timestamp,1,10) BETWEEN ? AND ?"
        params: list[Any] = [user_id, start_date, end_date]
        if sample_type:
            sql += " AND sample_type=?"
            params.append(sample_type)
        sql += " ORDER BY timestamp"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def query_workouts(self, user_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workouts WHERE user_id=? AND substr(start_at,1,10) BETWEEN ? AND ? ORDER BY start_at",
                (user_id, start_date, end_date),
            ).fetchall()
        return [dict(r) for r in rows]

    def query_body(self, user_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM body_measurements WHERE user_id=? AND substr(timestamp,1,10) BETWEEN ? AND ? ORDER BY timestamp",
                (user_id, start_date, end_date),
            ).fetchall()
        return [dict(r) for r in rows]

    def query_spo2(
        self, user_id: str, start_date: str, end_date: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM spo2_samples WHERE user_id=? AND substr(timestamp,1,10) BETWEEN ? AND ? ORDER BY timestamp"
        params: list[Any] = [user_id, start_date, end_date]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def query_stress(
        self, user_id: str, start_date: str, end_date: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM stress_samples WHERE user_id=? AND substr(timestamp,1,10) BETWEEN ? AND ? ORDER BY timestamp"
        params: list[Any] = [user_id, start_date, end_date]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def query_abnormal(self, user_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM abnormal_heart_beat_events WHERE user_id=? AND substr(start_at,1,10) BETWEEN ? AND ? ORDER BY start_at",
                (user_id, start_date, end_date),
            ).fetchall()
        return [dict(r) for r in rows]

    def coverage(self, user_id: str) -> list[dict[str, Any]]:
        specs = {
            "daily_activity": ("daily_activity", "date"),
            "sleep": ("sleep_sessions", "start_at"),
            "heart_rate": ("heart_rate_samples", "timestamp"),
            "workouts": ("workouts", "start_at"),
            "body_measurements": ("body_measurements", "timestamp"),
            "spo2": ("spo2_samples", "timestamp"),
            "stress": ("stress_samples", "timestamp"),
            "abnormal_heart_beat": ("abnormal_heart_beat_events", "start_at"),
        }
        result = []
        with self._connect() as conn:
            for data_type, (table, column) in specs.items():
                row = conn.execute(
                    f"SELECT MIN({column}) AS first, MAX({column}) AS last, COUNT(*) AS count "
                    f"FROM {table} WHERE user_id=?",
                    (user_id,),
                ).fetchone()
                result.append(
                    {
                        "data_type": data_type,
                        "first": row["first"],
                        "last": row["last"],
                        "count": row["count"],
                    }
                )
        return result

    # --------------------------------------------------------------- sync state

    def get_sync_state(self, user_id: str, data_type: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_state WHERE user_id=? AND data_type=?", (user_id, data_type)
            ).fetchone()
        return dict(row) if row else None

    def list_sync_states(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM sync_state WHERE user_id=?", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def set_sync_state(
        self,
        user_id: str,
        data_type: str,
        *,
        last_record_at: str | None,
        status: str,
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._write() as conn:
            conn.execute(
                """
                INSERT INTO sync_state(user_id,data_type,last_record_at,last_synced_at,status,error)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(user_id,data_type) DO UPDATE SET
                    last_record_at=COALESCE(excluded.last_record_at, sync_state.last_record_at),
                    last_synced_at=excluded.last_synced_at, status=excluded.status,
                    error=excluded.error
                """,
                (user_id, data_type, last_record_at, now, status, error),
            )

    def start_run(self, user_id: str, data_type: str) -> int:
        with self._write() as conn:
            cur = conn.execute(
                "INSERT INTO sync_runs(user_id,data_type,started_at,status) VALUES (?,?,?,?)",
                (user_id, data_type, datetime.now(UTC).isoformat(), "running"),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, status: str, records: int, error: str | None = None) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE sync_runs SET finished_at=?, status=?, records=?, error=? WHERE id=?",
                (datetime.now(UTC).isoformat(), status, records, error, run_id),
            )

    # ------------------------------------------------------------------ jobs

    def enqueue_job(
        self, user_id: str, data_type: str, start_date: str | None, end_date: str | None, force: bool = False
    ) -> str:
        job_id = f"job_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}_{data_type}"
        with self._write() as conn:
            existing = conn.execute(
                "SELECT id FROM sync_jobs WHERE data_type=? AND status IN ('pending','running') LIMIT 1",
                (data_type,),
            ).fetchone()
            if existing:
                return str(existing["id"])
            conn.execute(
                """
                INSERT INTO sync_jobs(id,user_id,data_type,start_date,end_date,force,status,created_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (job_id, user_id, data_type, start_date, end_date, int(force), "pending", datetime.now(UTC).isoformat()),
            )
        return job_id

    def claim_job(self) -> dict[str, Any] | None:
        with self._write() as conn:
            row = conn.execute(
                "SELECT * FROM sync_jobs WHERE status='pending' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE sync_jobs SET status='running', started_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), row["id"]),
            )
            result = dict(row)
            result["status"] = "running"
            return result

    def finish_job(self, job_id: str, *, status: str, error: str | None = None) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE sync_jobs SET status=?, finished_at=?, error=? WHERE id=?",
                (status, datetime.now(UTC).isoformat(), error, job_id),
            )

    def reset_running_jobs(self) -> int:
        """Requeue jobs left running by a worker restart (single-worker design)."""
        with self._write() as conn:
            conn.execute(
                "UPDATE sync_runs SET status='error', finished_at=?, error='worker restarted' "
                "WHERE status='running'",
                (datetime.now(UTC).isoformat(),),
            )
            cur = conn.execute(
                "UPDATE sync_jobs SET status='pending', started_at=NULL WHERE status='running'"
            )
            return cur.rowcount

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sync_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ keys

    def create_api_key(self, key_hash: str, prefix: str, label: str | None, scopes: str) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO api_keys(id,key_hash,prefix,label,scopes,created_at) VALUES (?,?,?,?,?,?)",
                (f"key_{prefix}", key_hash, prefix, label, scopes, datetime.now(UTC).isoformat()),
            )

    def find_api_key(self, key_hash: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM api_keys WHERE key_hash=?", (key_hash,)).fetchone()
        return dict(row) if row else None

    def touch_api_key(self, key_id: str) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE api_keys SET last_used_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), key_id),
            )

    def list_api_keys(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,prefix,label,scopes,created_at,last_used_at,revoked FROM api_keys ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def revoke_api_key(self, prefix: str) -> bool:
        with self._write() as conn:
            cur = conn.execute(
                "UPDATE api_keys SET revoked=1 WHERE prefix=? AND revoked=0", (prefix,)
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------ settings

    def get_setting(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._write() as conn:
            conn.execute(
                """
                INSERT INTO settings(key,value,updated_at) VALUES (?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, datetime.now(UTC).isoformat()),
            )


def default_date_range(days: int) -> tuple[str, str]:
    end = datetime.now(UTC).date()
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()
