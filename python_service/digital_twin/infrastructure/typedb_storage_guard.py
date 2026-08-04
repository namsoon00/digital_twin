"""Non-destructive TypeDB storage headroom checks.

The graph can be rebuilt from source facts, but an exhausted filesystem can
corrupt an in-flight WAL/checkpoint before normal retention gets a chance to
run. This module only reports whether writes are safe; it never deletes or
resets TypeDB data.
"""

from __future__ import annotations

import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Mapping

from ..domain.typedb_capacity_policy import evaluate_typedb_capacity_policy
from .operational_storage_guard import (
    storage_directory_physical_size_bytes,
    storage_directory_size_bytes,
)
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
    if "operationalMinimumFreeSpaceMb" in configured:
        minimum_free_mb = max(
            minimum_free_mb,
            _int_value(configured.get("operationalMinimumFreeSpaceMb"), minimum_free_mb),
        )
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


def typedb_storage_inventory(
    settings: Mapping[str, object] = None,
    data_path: Path = None,
    disk_usage_provider: Callable = None,
    size_provider: Callable[[Path], int] = None,
) -> Dict[str, object]:
    """Read only TypeDB's bounded storage footprint for a capacity decision.

    Operational capacity observations also measure MySQL and logs. Runtime
    TypeDB guards must stay inexpensive, so they scan only the TypeDB root and
    cache the result between writer turns.
    """

    configured = dict(settings or {})
    root = Path(data_path) if data_path is not None else typedb_data_path(configured)
    physical_size = size_provider or storage_directory_physical_size_bytes
    apparent_size = size_provider or storage_directory_size_bytes
    health = typedb_storage_health(
        configured,
        data_path=root,
        disk_usage_provider=disk_usage_provider,
    )
    typedb_physical = physical_size(root)
    typedb_apparent = apparent_size(root)
    wal_size = sum(physical_size(path) for path in root.glob("*/wal"))
    checkpoint_size = sum(apparent_size(path) for path in root.glob("*/checkpoint"))
    return {
        **health,
        "typedbSizeMb": round(typedb_physical / 1024 / 1024, 1),
        "typedbApparentSizeMb": round(typedb_apparent / 1024 / 1024, 1),
        "typedbSharedLinkedMb": round(max(0, typedb_apparent - typedb_physical) / 1024 / 1024, 1),
        "typedbWalMb": round(wal_size / 1024 / 1024, 1),
        "typedbCheckpointMb": round(checkpoint_size / 1024 / 1024, 1),
        "typedbCheckpointReferencedMb": round(checkpoint_size / 1024 / 1024, 1),
        "typedbLimitMb": _int_value(configured.get("typedbDataMaxSizeMb"), 8192, 256),
    }


class TypeDBCapacityGuard:
    """Cache a direct TypeDB capacity sample for one worker process.

    A new guard is constructed per worker, so it does not share mutable state
    across processes. Disk headroom remains checked on every call; only the
    recursive TypeDB directory scan is sampled at a bounded interval.
    """

    def __init__(
        self,
        settings: Mapping[str, object] = None,
        role: str = "reasoning",
        data_path: Path = None,
        disk_usage_provider: Callable = None,
        inventory_provider: Callable = None,
        monotonic_provider: Callable = None,
        capacity_state_loader: Callable = None,
    ):
        self.settings = dict(settings or {})
        self.role = str(role or "reasoning")
        self.data_path = Path(data_path) if data_path is not None else typedb_data_path(self.settings)
        self.disk_usage_provider = disk_usage_provider
        self.inventory_provider = inventory_provider or typedb_storage_inventory
        self.monotonic_provider = monotonic_provider or time.monotonic
        self.capacity_state_loader = capacity_state_loader
        self._last_snapshot: Dict[str, object] = {}
        self._last_sample_at = 0.0
        self._last_inventory_error = ""
        self._last_sample_source = ""

    def sample_interval_seconds(self) -> int:
        return max(5, min(600, _int_value(
            self.settings.get("typedbCapacityGuardCheckIntervalSeconds"),
            30,
        )))

    def stale_after_seconds(self) -> int:
        configured = _int_value(
            self.settings.get("typedbCapacityGuardStaleSeconds"),
            self.sample_interval_seconds() * 3,
        )
        return max(self.sample_interval_seconds(), min(3600, configured))

    def shared_sample_max_age_seconds(self) -> int:
        return max(30, min(1800, _int_value(
            self.settings.get("typedbCapacitySharedSampleMaxAgeSeconds"),
            180,
        )))

    @staticmethod
    def shared_sample_age_seconds(snapshot: Mapping[str, object]) -> float:
        raw = str((snapshot or {}).get("checkedAt") or "").strip()
        if not raw:
            return float("inf")
        try:
            observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return float("inf")
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds())

    def shared_snapshot(self) -> Dict[str, object]:
        if not callable(self.capacity_state_loader):
            return {}
        try:
            payload = self.capacity_state_loader()
        except Exception as error:  # noqa: BLE001 - a local direct sample remains the fallback.
            self._last_inventory_error = str(error)[:180]
            return {}
        values = dict(payload or {}) if isinstance(payload, dict) else {}
        nested = values.get("operationalStorageCapacity")
        if isinstance(nested, dict):
            values = dict(nested)
        if not values.get("typedbLimitMb"):
            return {}
        if self.shared_sample_age_seconds(values) > self.shared_sample_max_age_seconds():
            return {}
        # A high shared sample may have been recorded immediately before a
        # controlled TypeDB rotation. Confirm pressure from the filesystem so
        # workers do not remain blocked behind a now-stale MySQL observation.
        try:
            limit_mb = float(values.get("typedbLimitMb") or 0)
            size_mb = float(values.get("typedbSizeMb") or 0)
        except (TypeError, ValueError):
            return {}
        configured_limit_mb = _int_value(
            self.settings.get("typedbDataMaxSizeMb"),
            8192,
            256,
        )
        if int(limit_mb) != configured_limit_mb:
            # Runtime capacity settings can change between sampler cycles.
            # Do not apply a low-usage result computed against an obsolete
            # denominator; refresh directly from the TypeDB filesystem.
            return {}
        throttle_percent = _int_value(self.settings.get("typedbCapacityThrottlePercent"), 70)
        if limit_mb > 0 and size_mb / limit_mb * 100.0 >= throttle_percent:
            return {}
        return values

    def _inventory(self) -> Dict[str, object]:
        return dict(self.inventory_provider(
            self.settings,
            data_path=self.data_path,
            disk_usage_provider=self.disk_usage_provider,
        ) or {})

    def __call__(self) -> Dict[str, object]:
        now = float(self.monotonic_provider())
        disk = typedb_storage_health(
            self.settings,
            data_path=self.data_path,
            disk_usage_provider=self.disk_usage_provider,
        )
        age_seconds = max(0.0, now - self._last_sample_at) if self._last_snapshot else None
        if not self._last_snapshot or age_seconds is None or age_seconds >= self.sample_interval_seconds():
            shared = self.shared_snapshot()
            if shared:
                self._last_snapshot = shared
                self._last_sample_at = now
                self._last_sample_source = "operational-capacity"
                self._last_inventory_error = ""
                age_seconds = 0.0
            else:
                try:
                    self._last_snapshot = self._inventory()
                    self._last_sample_at = now
                    self._last_inventory_error = ""
                    self._last_sample_source = "direct-filesystem"
                    age_seconds = 0.0
                except Exception as error:  # noqa: BLE001 - capacity must fail closed when no fresh sample exists.
                    self._last_inventory_error = str(error)[:180]
                    age_seconds = max(0.0, now - self._last_sample_at) if self._last_snapshot else None

        if not self._last_snapshot or age_seconds is None or age_seconds > self.stale_after_seconds():
            reason = "TypeDB 용량 샘플을 확인하지 못해 그래프 쓰기를 보류합니다."
            if self._last_inventory_error:
                reason += " " + self._last_inventory_error
            return {
                **disk,
                "ready": False,
                "status": "unavailable-capacity-sample",
                "mode": "unavailable-capacity-sample",
                "role": self.role,
                "reason": reason,
                "rotationRequired": False,
                "capacitySampleAgeSeconds": age_seconds,
                "capacitySampleError": self._last_inventory_error,
            }

        policy = evaluate_typedb_capacity_policy(
            self._last_snapshot,
            settings=self.settings,
            role=self.role,
            disk_health=disk,
        )
        return {
            **disk,
            **policy,
            "dataPath": str(self.data_path),
            "typedbWalMb": self._last_snapshot.get("typedbWalMb"),
            "typedbCheckpointMb": self._last_snapshot.get("typedbCheckpointMb"),
            "typedbApparentSizeMb": self._last_snapshot.get("typedbApparentSizeMb"),
            "typedbSharedLinkedMb": self._last_snapshot.get("typedbSharedLinkedMb"),
            "capacitySampleAgeSeconds": round(float(age_seconds or 0.0), 1),
            "capacitySampleIntervalSeconds": self.sample_interval_seconds(),
            "capacitySampleSource": self._last_sample_source,
            "capacitySampleError": self._last_inventory_error,
        }
