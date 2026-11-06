"""Sync worker: scheduler + job consumer. Run beside the API, never inside it."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from mi_health.config import Settings
from mi_health.credentials import CredentialError, CredentialStore
from mi_health.db import Database
from mi_health.sync.service import DATA_TYPES, SyncService

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, db: Database, store: CredentialStore, settings: Settings):
        self.db = db
        self.store = store
        self.settings = settings
        self.service = SyncService(db, store, settings)
        self._bootstrapped = False

    def _bootstrap(self) -> None:
        if self._bootstrapped:
            return
        creds = self.store.get()
        if not creds:
            logger.warning("Xiaomi credentials not configured; worker waits")
            return
        for data_type in DATA_TYPES:
            if not self.db.get_sync_state(creds.user_id, data_type):
                end = datetime.now(UTC).date()
                start = end - timedelta(days=self.settings.backfill_days - 1)
                self.db.enqueue_job(creds.user_id, data_type, start.isoformat(), end.isoformat(), force=True)
        self._bootstrapped = True

    def _schedule_due(self) -> None:
        creds = self.store.get()
        if not creds:
            return
        now = datetime.now(UTC)
        for data_type in DATA_TYPES:
            state = self.db.get_sync_state(creds.user_id, data_type)
            due = True
            if state and state.get("last_synced_at"):
                last = datetime.fromisoformat(state["last_synced_at"])
                due = now - last >= timedelta(seconds=self.settings.interval_for(data_type))
            if due:
                self.db.enqueue_job(
                    creds.user_id,
                    data_type,
                    None,
                    None,
                    force=False,
                )

    def process_once(self) -> bool:
        self._bootstrap()
        self._schedule_due()
        job = self.db.claim_job()
        if not job:
            return False
        try:
            result = self.service.run(
                job["data_type"],
                job.get("start_date"),
                job.get("end_date"),
                bool(job.get("force")),
            )
            logger.info(
                "sync %s %s: %s record(s) (%s..%s)",
                job["data_type"],
                result["status"],
                result.get("records", 0),
                result.get("start_date"),
                result.get("end_date"),
            )
            self.db.finish_job(job["id"], status=result["status"], error=result.get("error"))
        except Exception as exc:  # noqa: BLE001 - job must not kill the worker
            logger.exception("job %s failed", job["id"])
            self.db.finish_job(job["id"], status="error", error=f"{type(exc).__name__}: {exc}")
        return True

    def run_forever(self, poll_seconds: int = 30) -> None:
        recovered = self.db.reset_running_jobs()
        if recovered:
            logger.warning("requeued %s orphaned sync job(s) from a previous worker", recovered)
        logger.info("sync worker started (poll=%ss)", poll_seconds)
        while True:
            try:
                worked = self.process_once()
            except CredentialError as exc:
                logger.warning("credentials unavailable: %s", exc)
                worked = False
            except Exception:
                logger.exception("worker loop error")
                worked = False
            if not worked:
                time.sleep(poll_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    settings.ensure_data_dir()
    db = Database(settings.db_path)
    store = CredentialStore(db, settings.master_key, settings.region)
    Worker(db, store, settings).run_forever()


if __name__ == "__main__":
    main()
