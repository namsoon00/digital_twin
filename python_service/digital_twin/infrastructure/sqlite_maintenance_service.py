import json
import signal
import time
from pathlib import Path
from typing import Dict

from .sqlite.health import DEFAULT_RETENTION_DAYS, run_sqlite_maintenance, sqlite_health_snapshot


class SQLiteMaintenanceRunner:
    def __init__(
        self,
        path: Path = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        archive_old_data: bool = False,
        compact_app_store: bool = True,
        vacuum: bool = False,
    ):
        self.path = path
        self.retention_days = max(1, int(retention_days or DEFAULT_RETENTION_DAYS))
        self.archive_old_data = bool(archive_old_data)
        self.compact_app_store = bool(compact_app_store)
        self.vacuum = bool(vacuum)

    def run_once(self) -> Dict[str, object]:
        return run_sqlite_maintenance(
            path=self.path,
            checkpoint=True,
            optimize=True,
            recover_processing=True,
            cleanup_old_data=True,
            archive_old_data=self.archive_old_data,
            retention_days=self.retention_days,
            compact_app_store=self.compact_app_store,
            vacuum=self.vacuum,
        )

    def status(self) -> Dict[str, object]:
        return sqlite_health_snapshot(self.path)


class SQLiteMaintenanceScheduler:
    def __init__(self, runner: SQLiteMaintenanceRunner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(15 * 60, int(interval_seconds or 60 * 60))
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        print("Python SQLite maintenance worker started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                result = self.runner.run_once()
                cleanup = result.get("cleanup") or {}
                print(json.dumps({
                    "sqliteMaintenance": "ok",
                    "deletedTotal": int(cleanup.get("deletedTotal") or 0),
                    "retentionDays": cleanup.get("retentionDays"),
                    "freelistBytes": (result.get("health") or {}).get("freelistBytes"),
                    "checkpoint": result.get("checkpoint"),
                    "optimized": bool(result.get("optimized")),
                    "vacuumed": bool(result.get("vacuumed")),
                }, ensure_ascii=False, sort_keys=True))
            except Exception as error:  # noqa: BLE001 - long-running maintenance must keep scheduling.
                print("Python SQLite maintenance worker error: " + str(error))
            elapsed = time.monotonic() - started
            sleep_seconds = max(1.0, self.interval_seconds - elapsed)
            end_at = time.monotonic() + sleep_seconds
            while self.running and time.monotonic() < end_at:
                time.sleep(min(1.0, end_at - time.monotonic()))
