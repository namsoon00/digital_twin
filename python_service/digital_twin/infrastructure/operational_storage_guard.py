"""Shared filesystem headroom policy for optional workers and maintenance."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Callable, Dict, Mapping

from .settings import data_dir


_MYSQL_STORAGE_METADATA_CACHE: Dict[str, object] = {}


def clear_mysql_storage_metadata_cache() -> None:
    _MYSQL_STORAGE_METADATA_CACHE.clear()


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
    minimum_free_mb = _integer(configured.get("operationalMinimumFreeSpaceMb"), 12288, 1024)
    critical_free_mb = min(
        minimum_free_mb,
        _integer(configured.get("operationalCriticalFreeSpaceMb"), 6144, 512),
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


def storage_directory_physical_size_bytes(path: Path) -> int:
    """Return allocated filesystem bytes, deduplicating hard-linked files.

    TypeDB checkpoints commonly hard-link immutable SST files from ``storage``.
    Summing every pathname makes a 1 GB graph look like 2 GB or more and can
    trigger needless capacity alerts or graph rebuilds.  Capacity decisions
    need allocated blocks, not the apparent size of every directory entry.
    """

    target = Path(path)
    try:
        if target.is_file():
            stat = target.stat()
            blocks = int(getattr(stat, "st_blocks", 0) or 0)
            return max(0, blocks * 512 if blocks > 0 else int(stat.st_size))
    except OSError:
        return 0
    if not target.exists():
        return 0
    total = 0
    seen = set()
    pending = [target]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        stat = entry.stat(follow_symlinks=False)
                        identity = (int(stat.st_dev), int(stat.st_ino))
                        if identity in seen:
                            continue
                        seen.add(identity)
                        blocks = int(getattr(stat, "st_blocks", 0) or 0)
                        total += max(0, blocks * 512 if blocks > 0 else int(stat.st_size))
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def mysql_storage_metadata(
    settings: Mapping[str, object],
    now_provider: Callable[[], float] = None,
) -> Dict[str, object]:
    """Return cheap logical allocation metadata without joining application tables."""

    configured = dict(settings or {})
    enabled = str(configured.get("mysqlStorageMetadataEnabled") or "1").strip().lower() \
        not in {"", "0", "false", "no", "off", "disabled"}
    configured_runtime = bool(
        str(configured.get("mysqlHost") or configured.get("mysqlUrl") or "").strip()
        or str(configured.get("mysqlRuntimeManaged") or "").strip().lower() in {"1", "true", "yes", "on"}
    )
    if not enabled or not configured_runtime:
        return {"mysqlMetadataStatus": "disabled"}
    now = float((now_provider or time.monotonic)())
    database = str(configured.get("mysqlDatabase") or "orbit_alpha")
    cache_seconds = _integer(configured.get("mysqlStorageMetadataCacheSeconds"), 300, 30, 3600)
    cached_at = float(_MYSQL_STORAGE_METADATA_CACHE.get("cachedAt") or 0.0)
    if (
        _MYSQL_STORAGE_METADATA_CACHE.get("database") == database
        and now - cached_at < cache_seconds
    ):
        return dict(_MYSQL_STORAGE_METADATA_CACHE.get("payload") or {})
    try:
        from .mysql_operational_connection import MySQLOperationalConnection

        probe_settings = {
            **configured,
            "_skipOperationalSchemaBootstrap": "1",
            "_skipOperationalHistoryRetention": "1",
            "mysqlOperationTimeoutSeconds": str(min(
                10,
                _integer(configured.get("mysqlOperationTimeoutSeconds"), 10, 1, 60),
            )),
        }
        store = MySQLOperationalConnection(probe_settings)
        with store.connect() as connection:
            cursor = connection.execute(
                """
                SELECT COALESCE(SUM(data_length), 0) AS dataBytes,
                       COALESCE(SUM(index_length), 0) AS indexBytes,
                       COALESCE(SUM(data_free), 0) AS reclaimableBytes
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                """
            )
            row = cursor.fetchone() or {}
        data_bytes = int(row.get("dataBytes") or 0)
        index_bytes = int(row.get("indexBytes") or 0)
        reclaimable_bytes = int(row.get("reclaimableBytes") or 0)
        payload = {
            "mysqlMetadataStatus": "available",
            "mysqlLiveDataMb": round((data_bytes + index_bytes) / 1024 / 1024, 1),
            "mysqlReclaimableMb": round(reclaimable_bytes / 1024 / 1024, 1),
            "mysqlAllocatedTableMb": round(
                (data_bytes + index_bytes + reclaimable_bytes) / 1024 / 1024,
                1,
            ),
        }
    except Exception as error:  # noqa: BLE001 - optional diagnostics must not gate writes.
        payload = {
            "mysqlMetadataStatus": "unavailable",
            "mysqlMetadataReason": str(error)[:180],
        }
    _MYSQL_STORAGE_METADATA_CACHE.clear()
    _MYSQL_STORAGE_METADATA_CACHE.update({
        "database": database,
        "cachedAt": now,
        "payload": payload,
    })
    return dict(payload)


def operational_storage_inventory(
    settings: Mapping[str, object] = None,
    data_path: Path = None,
    disk_usage_provider: Callable = None,
    size_provider: Callable[[Path], int] = None,
    mysql_metadata_provider: Callable[[Mapping[str, object]], Mapping[str, object]] = None,
) -> Dict[str, object]:
    """Return bounded component sizes for capacity alerts and maintenance decisions."""

    configured = dict(settings or {})
    root = Path(data_path) if data_path is not None else data_dir()
    apparent_size = size_provider or storage_directory_size_bytes
    physical_size = size_provider or storage_directory_physical_size_bytes
    health = operational_storage_health(
        configured,
        probe_path=root,
        disk_usage_provider=disk_usage_provider,
    )
    typedb_root = root / "typedb-data"
    mysql_root = root / "mysql-runtime"
    typedb_physical = physical_size(typedb_root)
    typedb_apparent = apparent_size(typedb_root)
    typedb_wal = sum(physical_size(path) for path in typedb_root.glob("*/wal"))
    # This is a useful TypeDB diagnostic, but checkpoint files can be hard
    # linked to the active store and must not be added again to capacity.
    typedb_checkpoint = sum(apparent_size(path) for path in typedb_root.glob("*/checkpoint"))
    root_logs = sum(apparent_size(path) for path in root.glob("*.log"))
    typedb_logs = apparent_size(root / "typedb-logs")
    mysql_size_mb = round(apparent_size(mysql_root) / 1024 / 1024, 1)
    mysql_metadata = dict(
        (mysql_metadata_provider or mysql_storage_metadata)(configured) or {}
    )
    mysql_limit_mb = _integer(
        configured.get("operationalMySqlDataMaxSizeMb"),
        16384,
        256,
    )
    mysql_usage_percent = round(mysql_size_mb / mysql_limit_mb * 100, 1) if mysql_limit_mb else 0.0
    mysql_cleanup_percent = _integer(
        configured.get("operationalStorageComponentCleanupPercent"),
        70,
        50,
        99,
    )
    mysql_warning_percent = max(
        mysql_cleanup_percent,
        _integer(configured.get("operationalStorageComponentWarningPercent"), 80, 50, 99),
    )
    mysql_restrict_percent = max(
        mysql_warning_percent,
        _integer(configured.get("operationalStorageComponentAlertPercent"), 90, 50, 99),
    )
    mysql_critical_percent = max(
        mysql_restrict_percent,
        _integer(configured.get("operationalStorageComponentCriticalPercent"), 95, 50, 100),
    )
    mysql_stage = "normal"
    if mysql_usage_percent >= 100:
        mysql_stage = "core-only"
    elif mysql_usage_percent >= mysql_critical_percent:
        mysql_stage = "critical"
    elif mysql_usage_percent >= mysql_restrict_percent:
        mysql_stage = "restricted"
    elif mysql_usage_percent >= mysql_warning_percent:
        mysql_stage = "warning"
    elif mysql_usage_percent >= mysql_cleanup_percent:
        mysql_stage = "maintenance"

    cleanup_mode = str(health.get("cleanupMode") or "normal")
    if mysql_stage in {"critical", "core-only"}:
        cleanup_mode = "emergency"
    elif mysql_stage in {"maintenance", "warning", "restricted"} and cleanup_mode == "normal":
        cleanup_mode = "accelerated"
    non_essential_writes_allowed = bool(health.get("nonEssentialWritesAllowed", True))
    if mysql_stage in {"restricted", "critical", "core-only"}:
        non_essential_writes_allowed = False
    reason = str(health.get("reason") or "")
    if not reason and mysql_stage in {"restricted", "critical", "core-only"}:
        reason = "MySQL 운영 한도에 가까워 비필수 분석 쓰기를 보류하고 핵심 투자 이력을 보호합니다."
    elif not reason and mysql_stage in {"maintenance", "warning"}:
        reason = "MySQL 점유율 기준에 따라 이력 정리를 가속합니다."
    return {
        **health,
        "nonEssentialWritesAllowed": non_essential_writes_allowed,
        "cleanupMode": cleanup_mode,
        "reason": reason,
        "typedbSizeMb": round(typedb_physical / 1024 / 1024, 1),
        "typedbApparentSizeMb": round(typedb_apparent / 1024 / 1024, 1),
        "typedbSharedLinkedMb": round(max(0, typedb_apparent - typedb_physical) / 1024 / 1024, 1),
        "typedbWalMb": round(typedb_wal / 1024 / 1024, 1),
        "typedbCheckpointMb": round(typedb_checkpoint / 1024 / 1024, 1),
        "typedbCheckpointReferencedMb": round(typedb_checkpoint / 1024 / 1024, 1),
        "typedbLimitMb": _integer(configured.get("typedbDataMaxSizeMb"), 8192, 256),
        "mysqlSizeMb": mysql_size_mb,
        **mysql_metadata,
        "mysqlLimitMb": mysql_limit_mb,
        "mysqlUsagePercent": mysql_usage_percent,
        "mysqlCapacityStage": mysql_stage,
        "mysqlCleanupThresholdPercent": mysql_cleanup_percent,
        "mysqlWarningThresholdPercent": mysql_warning_percent,
        "mysqlRestrictThresholdPercent": mysql_restrict_percent,
        "mysqlCriticalThresholdPercent": mysql_critical_percent,
        "mysqlHardLimitReached": mysql_stage == "core-only",
        "coreWritesOnly": mysql_stage == "core-only",
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
