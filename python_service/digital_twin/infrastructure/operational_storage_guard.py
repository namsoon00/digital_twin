"""Shared filesystem headroom policy for optional workers and maintenance."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable, Dict, Mapping

from .settings import data_dir


def _integer(value: object, fallback: int, minimum: int = 0, maximum: int = 1024 * 1024) -> int:
    try:
        parsed = int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def operational_storage_health(
    settings: Mapping[str, object] = None,
    probe_path: Path = None,
    disk_usage_provider: Callable = None,
) -> Dict[str, object]:
    """Classify shared-disk pressure before optional database writes start."""

    configured = dict(settings or {})
    minimum_free_mb = _integer(configured.get("operationalMinimumFreeSpaceMb"), 16384, 1024)
    critical_free_mb = min(
        minimum_free_mb,
        _integer(configured.get("operationalCriticalFreeSpaceMb"), 8192, 512),
    )
    pressure_free_percent = _integer(
        configured.get("operationalStoragePressureFreePercent"),
        10,
        1,
        50,
    )
    path = Path(probe_path) if probe_path is not None else data_dir()
    target = path if path.exists() else path.parent
    try:
        usage = (disk_usage_provider or shutil.disk_usage)(target)
    except OSError as error:
        return {
            "ready": False,
            "nonEssentialWritesAllowed": False,
            "status": "unavailable",
            "cleanupMode": "emergency",
            "reason": "운영 저장소의 디스크 여유 공간을 확인하지 못했습니다: " + str(error)[:180],
            "probePath": str(path),
            "minimumFreeMb": minimum_free_mb,
            "criticalFreeMb": critical_free_mb,
        }

    free_bytes = int(getattr(usage, "free", 0) or 0)
    total_bytes = int(getattr(usage, "total", 0) or 0)
    free_mb = round(free_bytes / 1024 / 1024, 1)
    free_percent = round((free_bytes / total_bytes * 100), 2) if total_bytes else 0.0
    critical = free_bytes < critical_free_mb * 1024 * 1024
    guarded = free_bytes < minimum_free_mb * 1024 * 1024
    pressure = guarded or free_percent < pressure_free_percent
    status = (
        "critical-low-disk"
        if critical
        else "guarded-low-disk"
        if guarded
        else "pressure"
        if pressure
        else "ready"
    )
    return {
        "ready": not critical,
        "nonEssentialWritesAllowed": not guarded,
        "status": status,
        "cleanupMode": "emergency" if critical else "accelerated" if pressure else "normal",
        "reason": (
            "비필수 데이터 분석 쓰기를 보류하고 정리를 우선합니다."
            if guarded
            else "디스크 사용률이 높아 이력 정리를 가속합니다."
            if pressure
            else ""
        ),
        "probePath": str(path),
        "minimumFreeMb": minimum_free_mb,
        "criticalFreeMb": critical_free_mb,
        "pressureFreePercent": pressure_free_percent,
        "freeMb": free_mb,
        "freePercent": free_percent,
        "totalMb": round(total_bytes / 1024 / 1024, 1),
    }


def storage_directory_size_bytes(path: Path) -> int:
    """Read a directory's apparent size without loading any stored payload."""

    target = Path(path)
    try:
        if target.is_file() or target.is_symlink():
            return max(0, int(target.stat().st_size))
    except OSError:
        return 0
    if not target.exists():
        return 0
    total = 0
    pending = [target]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False) or entry.is_symlink():
                            total += max(0, int(entry.stat(follow_symlinks=False).st_size))
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def operational_storage_inventory(
    settings: Mapping[str, object] = None,
    data_path: Path = None,
    disk_usage_provider: Callable = None,
    size_provider: Callable[[Path], int] = None,
) -> Dict[str, object]:
    """Return bounded component sizes for capacity alerts and maintenance decisions."""

    configured = dict(settings or {})
    root = Path(data_path) if data_path is not None else data_dir()
    size = size_provider or storage_directory_size_bytes
    health = operational_storage_health(
        configured,
        probe_path=root,
        disk_usage_provider=disk_usage_provider,
    )
    typedb_root = root / "typedb-data"
    mysql_root = root / "mysql-runtime"
    typedb_wal = sum(size(path) for path in typedb_root.glob("*/wal"))
    typedb_checkpoint = sum(size(path) for path in typedb_root.glob("*/checkpoint"))
    root_logs = sum(size(path) for path in root.glob("*.log"))
    typedb_logs = size(root / "typedb-logs")
    return {
        **health,
        "typedbSizeMb": round(size(typedb_root) / 1024 / 1024, 1),
        "typedbWalMb": round(typedb_wal / 1024 / 1024, 1),
        "typedbCheckpointMb": round(typedb_checkpoint / 1024 / 1024, 1),
        "typedbLimitMb": _integer(configured.get("typedbDataMaxSizeMb"), 4096, 256),
        "mysqlSizeMb": round(size(mysql_root) / 1024 / 1024, 1),
        "mysqlLimitMb": _integer(configured.get("operationalMySqlDataMaxSizeMb"), 4096, 256),
        "logSizeMb": round((root_logs + typedb_logs) / 1024 / 1024, 1),
        "logLimitMb": _integer(configured.get("operationalLogMaxSizeMb"), 512, 32),
    }


def accelerated_mysql_cleanup_settings(
    settings: Mapping[str, object],
    storage: Mapping[str, object],
) -> Dict[str, object]:
    """Apply internal, bounded cleanup overrides without changing saved policy."""

    configured = dict(settings or {})
    mode = str((storage or {}).get("cleanupMode") or "normal")
    if mode == "emergency":
        configured.update({
            "_effectiveOperationalHistoryRetentionBatchSize": "500",
            "_effectiveMysqlMinimalRetentionBatchSize": "1000",
            "_effectiveMysqlMinimalRetentionMaxDeleteBytes": str(256 * 1024 * 1024),
            "_effectiveMysqlMinimalRetentionMaxRunSeconds": "60",
        })
    elif mode == "accelerated":
        configured.update({
            "_effectiveOperationalHistoryRetentionBatchSize": "500",
            "_effectiveMysqlMinimalRetentionBatchSize": "500",
            "_effectiveMysqlMinimalRetentionMaxDeleteBytes": str(128 * 1024 * 1024),
            "_effectiveMysqlMinimalRetentionMaxRunSeconds": "45",
        })
    return configured
