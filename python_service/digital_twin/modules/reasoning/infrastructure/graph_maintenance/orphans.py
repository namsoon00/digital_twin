"""graph_maintenance: orphans through explicit injected capabilities."""

from digital_twin.infrastructure.graph_store_payloads import number_or_none
from typing import Dict
from .orphans_ports import GraphMaintenanceOrphansStore, GraphMaintenanceOrphansRuntime


def scoped_abox_orphan_cleanup_max_generations(
    _store: GraphMaintenanceOrphansStore,
    settings: Dict[str, object] = None,
    *,
    _bindings: GraphMaintenanceOrphansRuntime
) -> int:
    raw = (settings or _bindings.runtime_settings()).get(
        "typedbScopedABoxOrphanCleanupMaxGenerations"
    )
    parsed = number_or_none(raw)
    if parsed is None:
        parsed = 4
    # Inventory is cheap compared with deletion. Keep routine cleanup
    # short so it never monopolizes TypeDB's single writer before a live
    # market update can be projected.
    return max(1, min(20, int(parsed)))


def cleanup_orphan_scoped_abox_candidates(
    _store: GraphMaintenanceOrphansStore,
    driver,
    imported,
    max_generation_count: int = 0,
    world_id: str = "",
) -> Dict[str, object]:
    """Reclaim incomplete scoped candidates while the scoped write lease is held."""
    inventory = _store.scoped_abox_orphan_candidate_inventory(world_id)
    deleted_batches = 0
    removed_generation_ids = []
    failures = []
    candidates = list(inventory.get("candidateGenerationIds") or [])
    maximum = (
        _store.scoped_abox_orphan_cleanup_max_generations()
        if int(max_generation_count or 0) <= 0
        else max(1, int(max_generation_count))
    )
    selected = candidates[:maximum]
    for generation_id in selected:
        try:
            result = _store.delete_box_snapshot_rows_in_batches(
                driver,
                imported,
                "ABox",
                str(generation_id),
            )
            deleted_batches += int(number_or_none(result.get("deletedBatchCount")) or 0)
            if str(result.get("status") or "") in {"ok", "skipped"}:
                removed_generation_ids.append(str(generation_id))
            else:
                failures.append(
                    {
                        "generationId": str(generation_id),
                        "status": str(result.get("status") or "error"),
                        "reason": str(result.get("reason") or ""),
                    }
                )
        except (
            Exception
        ) as error:  # noqa: BLE001 - keep the candidate invisible and report cleanup state.
            failures.append(
                {
                    "generationId": str(generation_id),
                    "status": "error",
                    "reason": str(error)[:180],
                }
            )
    return {
        "status": "ok" if not failures and len(selected) == len(candidates) else "partial",
        "candidateManifestIds": list(inventory.get("candidateManifestIds") or []),
        "removedGenerationIds": removed_generation_ids,
        "deletedBatchCount": deleted_batches,
        "failures": failures,
        "remainingGenerationIds": candidates[len(selected) :],
        "maxGenerationCount": maximum,
    }


def prune_orphan_scoped_abox_candidates(
    _store: GraphMaintenanceOrphansStore,
    world_id: str = "",
    max_generation_count: int = 0,
    *,
    _bindings: GraphMaintenanceOrphansRuntime
) -> Dict[str, object]:
    """Run orphan candidate reclamation as deferred maintenance only."""
    imported = _store.driver_imports()
    if imported[0] is None:
        return {
            "configured": bool(getattr(_store, "address", "")),
            "status": "driver-missing",
            "graphStore": "typedb",
            "reason": str(imported[1])[:180],
        }
    try:

        def operation():
            driver = _store.open_driver(imported)
            try:
                _store.ensure_database(driver)
                _store.ensure_schema(driver, imported)
                return _store.cleanup_orphan_scoped_abox_candidates(
                    driver,
                    imported,
                    max_generation_count=max_generation_count,
                    world_id=world_id,
                )
            finally:
                _store.close_driver(driver)

        result = _store.with_typedb_retries(operation)
        return {
            "configured": True,
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            **dict(result or {}),
        }
    except Exception as error:  # noqa: BLE001 - leave invisible candidates for the next idle pass.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": str(error)[:220],
        }
