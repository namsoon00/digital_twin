"""Non-destructive TypeDB storage headroom checks.

The graph can be rebuilt from source facts, but an exhausted filesystem can
corrupt an in-flight WAL/checkpoint before normal retention gets a chance to
run. This module only reports whether writes are safe; it never deletes or
resets TypeDB data.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Dict

from .settings import data_dir


def _int_value(value: object, fallback: int, lower: int = 0) -> int:
    try:
        parsed = int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, parsed)


def typedb_data_path(settings: Dict[str, object] = None) -> Path:
    configured = dict(settings or {})
    explicit = str(configured.get("typedbDataPath") or "").strip()
    return Path(explicit) if explicit else data_dir() / "typedb-data"


def typedb_storage_health(
    settings: Dict[str, object] = None,
    data_path: Path = None,
    disk_usage_provider: Callable = None,
) -> Dict[str, object]:
    """Return a cheap filesystem guard suitable for every reasoning cycle."""
    configured = dict(settings or {})
    enabled = str(configured.get("ontologyTypeDbEnabled", "1")).strip().lower() not in {
        "0", "false", "no", "off", "disabled",
    }
    path = Path(data_path) if data_path is not None else typedb_data_path(configured)
    minimum_free_mb = _int_value(configured.get("typedbMinimumFreeSpaceMb"), 4096)
    if not enabled:
        return {
            "ready": True,
            "status": "disabled",
            "dataPath": str(path),
            "minimumFreeMb": minimum_free_mb,
        }
    probe_path = path if path.exists() else path.parent
    try:
        usage = (disk_usage_provider or shutil.disk_usage)(probe_path)
    except OSError as error:
        return {
            "ready": False,
            "status": "unavailable",
            "reason": "TypeDB 저장소의 디스크 여유 공간을 확인하지 못했습니다: " + str(error)[:180],
            "dataPath": str(path),
            "minimumFreeMb": minimum_free_mb,
        }
    free_bytes = int(getattr(usage, "free", 0) or 0)
    total_bytes = int(getattr(usage, "total", 0) or 0)
    free_mb = round(free_bytes / 1024 / 1024, 1)
    ready = free_bytes >= minimum_free_mb * 1024 * 1024
    return {
        "ready": ready,
        "status": "ready" if ready else "blocked-low-disk",
        "reason": "" if ready else "TypeDB 쓰기를 보류합니다. 디스크 여유 " + str(free_mb) + "MB가 최소 " + str(minimum_free_mb) + "MB보다 적습니다.",
        "dataPath": str(path),
        "minimumFreeMb": minimum_free_mb,
        "freeMb": free_mb,
        "totalMb": round(total_bytes / 1024 / 1024, 1),
    }
