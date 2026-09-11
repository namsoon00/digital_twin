"""Runtime Support runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.infrastructure.typedb_storage_guard import TypeDBCapacityGuard

DISABLED_SETTING_VALUES = {"0", "false", "no", "off", "disabled"}


def ontology_graph_store_epoch(settings=None) -> str:
    """Return the durable identity of the currently mounted TypeDB store."""
    import json
    from digital_twin.infrastructure.settings import data_dir

    configured = dict(settings or {})
    explicit = str(configured.get("ontologyGraphStoreEpoch") or "").strip()
    if explicit:
        return explicit
    marker = {}
    try:
        marker = json.loads(
            (data_dir() / "typedb-retention.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, json.JSONDecodeError):
        marker = {}
    store_epoch = str(marker.get("graphStoreEpoch") or "").strip()
    if not store_epoch:
        timestamps = [
            str(marker.get(key) or "").strip()
            for key in [
                "blueGreenCutoverActivatedAt",
                "blueGreenCutoverAt",
                "blueGreenRollbackAt",
            ]
            if str(marker.get(key) or "").strip()
        ]
        store_epoch = max(timestamps, default="initial-store")
    return "|".join([
        str(configured.get("typedbAddress") or "127.0.0.1:1729").strip(),
        str(configured.get("typedbDatabase") or "orbit_alpha_ontology").strip(),
        store_epoch,
    ])


def ontology_graph_single_writer_enabled(settings=None) -> bool:
    value = str(
        dict(settings or {}).get("ontologyGraphSingleWriterEnabled", "1")
        or ""
    ).strip().lower()
    return value not in DISABLED_SETTING_VALUES


def setting_truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_SETTING_VALUES


def typedb_capacity_guard(settings, role: str, state_store=None) -> TypeDBCapacityGuard:
    """Build a role-aware guard that reuses the maintenance worker sample."""
    from digital_twin.infrastructure.typedb_storage_guard import TypeDBCapacityGuard

    reader = getattr(state_store, "load", None)
    return TypeDBCapacityGuard(
        settings,
        role=role,
        capacity_state_loader=reader if callable(reader) else None,
    )
