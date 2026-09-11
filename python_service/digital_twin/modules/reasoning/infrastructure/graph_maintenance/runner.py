"""graph_maintenance: runner through explicit injected capabilities."""

from digital_twin.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.abox_persistence.world_calls import (
    typedb_call_for_world,
)
from typing import Dict
import time
from .runner_ports import GraphMaintenanceRunnerStore, GraphMaintenanceRunnerRuntime


def run_deferred_maintenance(
    _store: GraphMaintenanceRunnerStore,
    payload: Dict[str, object] = None,
    *,
    _bindings: GraphMaintenanceRunnerRuntime
) -> Dict[str, object]:
    """Prune inactive graph generations after a verified cycle or while idle.

    This is operational retention, never an investment-rule step. It uses
    the same durable writer lease as ABox activation, so maintenance
    cannot delete a generation that a live native inference still needs.
    """
    if not bool(getattr(_store, "address", "")):
        return {
            "configured": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
        }
    options = dict(payload or {})
    requested_world_id = str(options.get("worldId") or options.get("ontologyWorldId") or "").strip()
    requested_world_type_values = options.get("worldTypes")
    if not isinstance(requested_world_type_values, (list, tuple, set)):
        requested_world_type_values = str(
            requested_world_type_values or options.get("worldType") or ""
        ).split(",")
    requested_world_types = {
        str(item or "").strip().lower()
        for item in requested_world_type_values
        if str(item or "").strip()
    }
    requested_manifest_limit = number_or_none(
        options.get("maxInactiveManifests")
        if options.get("maxInactiveManifests") is not None
        else options.get("maxManifests")
    )
    maintenance_manifest_limit = (
        _store.deferred_maintenance_abox_max_manifests()
        if requested_manifest_limit is None
        else max(1, min(20, int(requested_manifest_limit)))
    )
    requested_delete_batch_limit = number_or_none(
        options.get("maxAboxDeleteBatches")
        if options.get("maxAboxDeleteBatches") is not None
        else options.get("maxDeleteBatches")
    )
    maintenance_delete_batch_limit = (
        _store.deferred_maintenance_abox_max_delete_batches()
        if requested_delete_batch_limit is None
        else max(1, min(50, int(requested_delete_batch_limit)))
    )
    requested_delete_batch_size = number_or_none(
        options.get("aboxDeleteBatchSize")
        if options.get("aboxDeleteBatchSize") is not None
        else options.get("deleteBatchSize")
    )
    maintenance_delete_batch_size = (
        _store.deferred_maintenance_abox_delete_batch_size()
        if requested_delete_batch_size is None
        else max(10, min(500, int(requested_delete_batch_size)))
    )
    requested_duration_limit = number_or_none(
        options.get("maxDurationSeconds")
        if options.get("maxDurationSeconds") is not None
        else options.get("timeBudgetSeconds")
    )
    maintenance_duration_limit = (
        None
        if requested_duration_limit is None
        else max(5, min(300, int(requested_duration_limit)))
    )
    requested_keep_inactive = number_or_none(
        options.get("keepInactiveManifests")
        if options.get("keepInactiveManifests") is not None
        else options.get("keep_inactive_manifests")
    )
    maintenance_keep_inactive = (
        None if requested_keep_inactive is None else max(0, min(5, int(requested_keep_inactive)))
    )
    requested_orphan_limit = number_or_none(
        options.get("maxOrphanGenerations")
        if options.get("maxOrphanGenerations") is not None
        else options.get("orphanMaxGenerations")
    )
    # Normal workers retain the small runtime default. An explicit
    # migration/repair can safely drain more invisible generations while
    # holding the same per-world writer lease.
    maintenance_orphan_limit = (
        0 if requested_orphan_limit is None else max(1, min(256, int(requested_orphan_limit)))
    )
    started_at = time.perf_counter()

    # A single global maintenance pass used to inspect the last account's
    # active pointer.  Once PortfolioWorlds are independent, retention has
    # to acquire and release the corresponding world lease separately.  A
    # no-world invocation remains a legacy migration fallback only.
    if not requested_world_id:
        worlds = [
            item
            for item in _store.list_ontology_worlds()
            if isinstance(item, dict) and str(item.get("worldId") or "").strip()
        ]
        if requested_world_types:
            worlds = [
                item
                for item in worlds
                if (
                    str(item.get("worldType") or "").strip().lower() in requested_world_types
                    or str(item.get("worldId") or "").split(":", 1)[0].strip().lower()
                    in requested_world_types
                )
            ]
        if worlds:
            results = []
            for world in worlds:
                world_id = str(world.get("worldId") or "").strip()
                result = _store.run_deferred_maintenance(
                    {
                        **options,
                        "worldId": world_id,
                    }
                )
                results.append(
                    {
                        "worldId": world_id,
                        "worldType": str(world.get("worldType") or ""),
                        "status": str(result.get("status") or ""),
                        "result": result,
                    }
                )
            statuses = {str(item.get("status") or "") for item in results}
            return {
                "configured": True,
                "status": (
                    "partial"
                    if statuses.intersection({"error", "partial", "deferred-write-lease"})
                    else "ok"
                ),
                "graphStore": "typedb",
                "maintenanceMode": "per-active-world",
                "worldTypes": sorted(requested_world_types),
                "worldCount": len(results),
                "worlds": results,
                "durationMs": int((time.perf_counter() - started_at) * 1000),
            }
        if requested_world_types:
            return {
                "configured": True,
                "status": "ok",
                "graphStore": "typedb",
                "maintenanceMode": "per-active-world",
                "worldTypes": sorted(requested_world_types),
                "worldCount": 0,
                "worlds": [],
                "durationMs": int((time.perf_counter() - started_at) * 1000),
            }

    lease = _store.acquire_scoped_abox_write_lease(
        "ontology-deferred-maintenance",
        world_id=requested_world_id,
    )
    if not lease.get("acquired"):
        return {
            "configured": True,
            "status": "deferred-write-lease",
            "graphStore": "typedb",
            "worldId": requested_world_id,
            "reason": "A live ABox activation or native inference owns the graph writer lease.",
            "durationMs": int((time.perf_counter() - started_at) * 1000),
        }
    try:
        # Orphan candidates are a separate repair concern. Treating the
        # default zero as the adapter's "delete four" default made a
        # normal manifest-retention pass perform an unrelated, expensive
        # scan and deletion before it could reclaim one retired Manifest.
        # Only an explicit repair request may spend this maintenance slot
        # on orphan generations.
        orphan_result = (
            _store.prune_orphan_scoped_abox_candidates(
                requested_world_id,
                max_generation_count=maintenance_orphan_limit,
            )
            if maintenance_orphan_limit > 0
            else {
                "configured": True,
                "status": "not-requested",
                "graphStore": "typedb",
                "worldId": requested_world_id,
                "reason": "Routine scoped ABox retention skips orphan-candidate repair.",
                "maxGenerationCount": 0,
            }
        )
        abox_result = _store.prune_inactive_scoped_abox_manifests(
            requested_world_id,
            keep_inactive_count=maintenance_keep_inactive,
            max_manifests=maintenance_manifest_limit,
            max_delete_batches=maintenance_delete_batch_limit,
            delete_batch_size=maintenance_delete_batch_size,
            max_duration_seconds=maintenance_duration_limit,
        )
        abox_slice_incomplete = bool(
            abox_result.get("timeBudgetExhausted")
            or abox_result.get("resumeRequired")
            or str(abox_result.get("status") or "") == "partial"
        )
        legacy_result: Dict[str, object] = {
            "status": "not-required",
            "deletedGenerationIds": [],
        }
        # Scoped manifests reuse several immutable scope generations, so
        # generic ABox pruning must not scan every ABox snapshot. Legacy
        # complete-world snapshots have their own stable prefixes and can
        # be safely reclaimed once a scoped Manifest is active.
        active_abox: Dict[str, object] = {}
        if not abox_slice_incomplete and not requested_world_id:
            active_abox = _store.active_abox_metadata(requested_world_id)
        if (
            not abox_slice_incomplete
            and not requested_world_id
            and str(active_abox.get("scopedAboxManifestVersion") or "")
            == SCOPED_ABOX_MANIFEST_VERSION
        ):
            pending = _store.pending_abox_activation(requested_world_id)
            active_scope_ids = {
                str(value or "").strip()
                for value in dict(active_abox.get("scopeGenerationIds") or {}).values()
                if str(value or "").strip()
            }
            legacy_candidates = []
            if str(pending.get("status") or "") != "pending":
                for snapshot_id in _store.abox_candidate_snapshot_ids():
                    clean_snapshot_id = str(snapshot_id or "").strip()
                    if (
                        clean_snapshot_id
                        and clean_snapshot_id not in active_scope_ids
                        and clean_snapshot_id.startswith(("abox-material:", "abox-snapshot:"))
                    ):
                        legacy_candidates.append(clean_snapshot_id)
            legacy_slices = [
                _store.discard_abox_generation(snapshot_id) for snapshot_id in legacy_candidates[:2]
            ]
            legacy_result = {
                "status": (
                    "ok"
                    if not legacy_slices
                    or all(str(item.get("status") or "") == "ok" for item in legacy_slices)
                    else "partial"
                ),
                "candidateGenerationIds": legacy_candidates,
                "deletedGenerationIds": [
                    str(item.get("aboxSnapshotId") or "")
                    for item in legacy_slices
                    if str(item.get("status") or "") == "ok"
                ],
                "cleanup": legacy_slices,
            }
        inference_result: Dict[str, object] = {
            "status": "not-required",
            "reason": "No active InferenceBox generation was found.",
        }
        reader = getattr(_store, "read_inference_generation_records", None)
        pruner = getattr(_store, "prune_inferencebox_generations", None)
        if abox_slice_incomplete:
            inference_result = {
                "status": "deferred-maintenance-slice",
                "reason": "The scoped ABox cleanup slice is resumable; InferenceBox retention resumes after it releases the writer.",
            }
        elif callable(reader) and callable(pruner):
            records = typedb_call_for_world(
                reader,
                published_only=True,
                world_id=requested_world_id,
            )
            active_generation_id = str(
                (records[0] if records else {}).get("generationId") or ""
            ).strip()
            if active_generation_id:
                inference_result = typedb_call_for_world(
                    pruner,
                    active_generation_id,
                    keep_count=max(
                        1,
                        int(
                            number_or_none(options.get("inferenceKeepCount"))
                            or getattr(_store, "inference_generation_keep_count", 1)
                        ),
                    ),
                    world_id=requested_world_id,
                )
        statuses = {
            str(orphan_result.get("status") or ""),
            str(abox_result.get("status") or ""),
            str(legacy_result.get("status") or ""),
            str(inference_result.get("status") or ""),
        }
        maintenance_partial = bool(
            statuses.intersection({"error", "partial", "deferred-write-lease"})
        )
        return {
            "configured": True,
            "status": "partial" if maintenance_partial else "ok",
            "graphStore": "typedb",
            "worldId": requested_world_id,
            "maintenanceMode": "legacy-global" if not requested_world_id else "world-scoped",
            "maxInactiveManifests": maintenance_manifest_limit,
            "maxAboxDeleteBatches": maintenance_delete_batch_limit,
            "aboxDeleteBatchSize": maintenance_delete_batch_size,
            "maxDurationSeconds": maintenance_duration_limit,
            "maxOrphanGenerations": maintenance_orphan_limit,
            "orphanScopedAbox": orphan_result,
            "abox": abox_result,
            "legacyAbox": legacy_result,
            "inference": inference_result,
            "durationMs": int((time.perf_counter() - started_at) * 1000),
        }
    except Exception as error:  # noqa: BLE001 - a later idle window can retry retention.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "worldId": requested_world_id,
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": str(error)[:220],
            "durationMs": int((time.perf_counter() - started_at) * 1000),
        }
    finally:
        try:
            _store.release_scoped_abox_write_lease(lease)
        except Exception:
            pass
