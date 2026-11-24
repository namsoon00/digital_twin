"""Bounded, read-only diagnostics for the operational history maintenance worker."""
from datetime import timedelta
from pathlib import Path
import subprocess

from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_retention import mysql_operational_space_reclaim_candidates
from .storage_observation import parse_time


def _recent(timestamp, now, minutes):
    if not timestamp:
        return False
    elapsed = now - parse_time(timestamp)
    return timedelta(0) <= elapsed < timedelta(minutes=minutes)


def _allocated_mb(path, device):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return 0.0
    if stat.st_dev != device or path.is_symlink():
        return None
    result = subprocess.run(
        ["nice", "-n", "10", "du", "-skx", str(path)],
        capture_output=True, timeout=1.5, check=True,
    )
    return round(int(result.stdout.split()[0]) / 1024, 1)


def _directory_sample(snapshot, old, root, now):
    home = Path.home()
    paths = {
        "MySQL": root / "mysql-runtime",
        "history-archive": root / "history-archive",
        "Xcode DerivedData": home / "Library/Developer/Xcode/DerivedData",
        "Gradle caches": home / ".gradle/caches",
        "npm cache": home / ".npm/_cacache",
        "pip cache": home / "Library/Caches/pip",
        "Homebrew cache": home / "Library/Caches/Homebrew",
    }
    device = root.stat().st_dev
    sizes = {"TypeDB": snapshot.get("typedbTotalSizeMb")}
    for label, path in paths.items():
        try:
            sizes[label] = _allocated_mb(path, device)
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            sizes[label] = None
    directories = {}
    for label, size in sizes.items():
        previous = old.get(label, {})
        if size is None:
            directories[label] = {**previous, "available": False}
            continue
        entry = {"available": True, "sizeMb": size, "measuredAt": now.isoformat()}
        if _recent(previous.get("measuredAt"), now, 60):
            entry["changeMb"] = round(size - previous["sizeMb"], 1)
            entry["since"] = previous["measuredAt"]
        directories[label] = entry
    return directories


def _candidate_plan(settings):
    store = MySQLOperationalConnection({
        **settings, "_skipOperationalSchemaBootstrap": "1",
        "_skipOperationalHistoryRetention": "1", "mysqlOperationTimeoutSeconds": "2",
    })
    with store.connect() as connection:
        return mysql_operational_space_reclaim_candidates(
            connection, minimum_reclaim_mb=int(settings.get("mysqlPhysicalCompactionMinReclaimMb") or 256),
            maximum_tables=int(settings.get("mysqlPhysicalCompactionMaxTablesPerRun") or 3),
        )


def collect_maintenance_diagnostics(settings, snapshot, previous, now, *, root):
    result = dict(previous)
    if not _recent(result.get("sampledAt"), now, 15):
        result.pop("freeChangeMb", None)
        if (_recent(result.get("sampledAt"), now, 60) and result.get("freeMb") is not None
                and snapshot.get("freeMb") is not None):
            result["freeChangeMb"] = round(snapshot["freeMb"] - result["freeMb"], 1)
        result["freeMb"] = snapshot.get("freeMb")
        result["directories"] = _directory_sample(snapshot, result.get("directories", {}), root, now)
        result["sampledAt"] = now.isoformat()
    if snapshot.get("mysqlCapacityStage") not in {"warning", "restricted", "critical", "core-only"}:
        result.pop("mysqlPlan", None)
    elif not _recent(result.get("mysqlPlan", {}).get("checkedAt"), now, 15):
        plan = {"checkedAt": now.isoformat()}
        try:
            plan["candidates"] = _candidate_plan(settings)
        except Exception as error:  # A failed metadata probe must never block retention.
            plan["errorType"] = type(error).__name__
        result["mysqlPlan"] = plan
    return result
