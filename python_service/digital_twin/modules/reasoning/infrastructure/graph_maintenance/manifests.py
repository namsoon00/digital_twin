"""graph_maintenance: manifests through explicit injected capabilities."""

from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_scopes import SCOPED_ABOX_PERSISTENCE_MODE
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from typing import Dict
from typing import Iterable
import time
from .manifests_ports import GraphMaintenanceManifestsStore, GraphMaintenanceManifestsRuntime


def discard_scoped_abox_manifest_in_driver(_store: GraphMaintenanceManifestsStore, driver, imported, manifest_id: str, protected_generation_ids: Iterable[str]=None, world_id: str='', max_delete_batches: int=None, delete_batch_size: int=None) -> Dict[str, object]:
    """Delete a non-active Manifest and only generations no other Manifest needs.

    A bounded deletion may leave the Manifest marker in place. That is
    intentional: the next retention pass resumes the same immutable
    candidate, and the marker is removed only after all of its unshared
    scope generations have been reclaimed.
    """
    clean_manifest_id = str(manifest_id or "").strip()
    metadata = _store.scoped_manifest_metadata(clean_manifest_id, world_id)
    if str(metadata.get("status") or "") != "ok":
        return {
            "status": "skipped",
            "aboxSnapshotId": clean_manifest_id,
            "reason": "Scoped Manifest marker is not available for safe cleanup.",
            "deletedBatchCount": 0,
        }
    active = _store.active_abox_metadata(world_id)
    active_id = str(active.get("worldviewManifestId") or active.get("aboxSnapshotId") or "").strip()
    if active_id == clean_manifest_id:
        return {
            "status": "protected-active",
            "aboxSnapshotId": clean_manifest_id,
            "deletedBatchCount": 0,
        }
    protected = {
        str(item or "").strip()
        for item in protected_generation_ids or []
        if str(item or "").strip()
    }
    protected.update(
        str(item or "").strip()
        for item in dict(active.get("scopeGenerationIds") or {}).values()
        if str(item or "").strip()
    )
    deleted_batches = 0
    removed_generations = []
    retained_generations = []
    scope_cleanup_rows = []
    remaining_batch_budget = (
        None
        if max_delete_batches is None
        else max(0, min(1000, int(max_delete_batches or 0)))
    )
    bounded_delete_batch_size = (
        _store.deferred_maintenance_abox_delete_batch_size()
        if delete_batch_size is None
        else max(10, min(500, int(delete_batch_size or 0)))
    )

    def delete_snapshot(snapshot_id: str) -> Dict[str, object]:
        nonlocal deleted_batches, remaining_batch_budget
        cleanup = _store.delete_box_snapshot_rows_in_batches(
            driver,
            imported,
            "ABox",
            snapshot_id,
            batch_size=bounded_delete_batch_size,
            max_batches=remaining_batch_budget,
        )
        deleted = int(number_or_none(cleanup.get("deletedBatchCount")) or 0)
        deleted_batches += deleted
        if remaining_batch_budget is not None:
            remaining_batch_budget = max(0, remaining_batch_budget - deleted)
        return cleanup

    for generation_id in sorted({
        str(item or "").strip()
        for item in dict(metadata.get("scopeGenerationIds") or {}).values()
        if str(item or "").strip()
    }):
        if generation_id in protected:
            retained_generations.append(generation_id)
            continue
        # Once a physical delete has spent the per-run budget, do not
        # keep opening read/delete transactions for later scopes merely
        # to discover the same limit. The immutable Manifest marker stays
        # intact and the next background pass resumes from this scope.
        if remaining_batch_budget is not None and remaining_batch_budget <= 0:
            return {
                "status": "partial",
                "aboxSnapshotId": clean_manifest_id,
                "worldviewManifestId": clean_manifest_id,
                "removedScopeGenerationIds": removed_generations,
                "retainedSharedScopeGenerationIds": retained_generations,
                "deferredScopeGenerationIds": [generation_id],
                "scopeCleanup": scope_cleanup_rows,
                "deletedBatchCount": deleted_batches,
                "maxDeleteBatches": max_delete_batches,
                "deleteBatchSize": bounded_delete_batch_size,
                "remainingDeleteBatchBudget": remaining_batch_budget,
                "reason": "Scoped Manifest retention will resume after the bounded delete batch budget.",
            }
        cleanup = delete_snapshot(generation_id)
        scope_cleanup_rows.append(cleanup)
        if str(cleanup.get("status") or "") != "ok":
            return {
                "status": "partial" if str(cleanup.get("status") or "") == "partial" else str(cleanup.get("status") or "error"),
                "aboxSnapshotId": clean_manifest_id,
                "worldviewManifestId": clean_manifest_id,
                "removedScopeGenerationIds": removed_generations,
                "retainedSharedScopeGenerationIds": retained_generations,
                "scopeCleanup": scope_cleanup_rows,
                "deletedBatchCount": deleted_batches,
                "maxDeleteBatches": max_delete_batches,
                "deleteBatchSize": bounded_delete_batch_size,
                "remainingDeleteBatchBudget": remaining_batch_budget,
                "reason": "Scoped Manifest retention will resume after the bounded delete batch budget.",
            }
        removed_generations.append(generation_id)
    if remaining_batch_budget is not None and remaining_batch_budget <= 0:
        return {
            "status": "partial",
            "aboxSnapshotId": clean_manifest_id,
            "worldviewManifestId": clean_manifest_id,
            "removedScopeGenerationIds": removed_generations,
            "retainedSharedScopeGenerationIds": retained_generations,
            "scopeCleanup": scope_cleanup_rows,
            "deletedBatchCount": deleted_batches,
            "maxDeleteBatches": max_delete_batches,
            "deleteBatchSize": bounded_delete_batch_size,
            "remainingDeleteBatchBudget": remaining_batch_budget,
            "reason": "Scoped Manifest marker retention will resume after the bounded delete batch budget.",
        }
    marker_cleanup = delete_snapshot(clean_manifest_id)
    if str(marker_cleanup.get("status") or "") != "ok":
        return {
            "status": "partial" if str(marker_cleanup.get("status") or "") == "partial" else str(marker_cleanup.get("status") or "error"),
            "aboxSnapshotId": clean_manifest_id,
            "worldviewManifestId": clean_manifest_id,
            "removedScopeGenerationIds": removed_generations,
            "retainedSharedScopeGenerationIds": retained_generations,
            "scopeCleanup": scope_cleanup_rows,
            "markerCleanup": marker_cleanup,
            "deletedBatchCount": deleted_batches,
            "maxDeleteBatches": max_delete_batches,
            "deleteBatchSize": bounded_delete_batch_size,
            "remainingDeleteBatchBudget": remaining_batch_budget,
            "reason": "Scoped Manifest marker retention will resume after the bounded delete batch budget.",
        }
    return {
        "status": "ok",
        "aboxSnapshotId": clean_manifest_id,
        "worldviewManifestId": clean_manifest_id,
        "removedScopeGenerationIds": removed_generations,
        "retainedSharedScopeGenerationIds": retained_generations,
        "scopeCleanup": scope_cleanup_rows,
        "markerCleanup": marker_cleanup,
        "deletedBatchCount": deleted_batches,
        "maxDeleteBatches": max_delete_batches,
        "deleteBatchSize": bounded_delete_batch_size,
        "remainingDeleteBatchBudget": remaining_batch_budget,
    }


def discard_scoped_abox_manifest(_store: GraphMaintenanceManifestsStore, manifest_id: str, world_id: str='', *, _bindings: GraphMaintenanceManifestsRuntime) -> Dict[str, object]:
    clean_manifest_id = str(manifest_id or "").strip()
    imported = _store.driver_imports()
    if imported[0] is None:
        return _store.driver_missing_result(imported[1], PortfolioOntology("typedb-scoped-cleanup"))
    try:
        def operation():
            driver = _store.open_driver(imported)
            try:
                _store.ensure_database(driver)
                return _store.discard_scoped_abox_manifest_in_driver(
                    driver,
                    imported,
                    clean_manifest_id,
                    world_id=world_id,
                )
            finally:
                _store.close_driver(driver)

        result = _store.with_typedb_retries(operation)
        return {"configured": True, "graphStore": "typedb", **dict(result or {})}
    except Exception as error:  # noqa: BLE001 - the failed Manifest remains diagnosable.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "aboxSnapshotId": clean_manifest_id,
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": str(error)[:220],
        }


def delete_worldview_manifest_markers_batch(_store: GraphMaintenanceManifestsStore, driver, imported, manifest_ids: Iterable[str], world_id: str='', *, _bindings: GraphMaintenanceManifestsRuntime) -> Dict[str, object]:
    """Delete already-safe immutable Manifest markers in one short write."""
    clean_ids = list(dict.fromkeys(
        str(value or "").strip()
        for value in manifest_ids or []
        if str(value or "").strip()
    ))[:20]
    if not clean_ids:
        return {"status": "skipped", "deletedBatchCount": 0, "removedManifestIds": []}
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    query = (
        "match $n isa ontology-node, "
        'has ontology-kind "worldview-manifest-marker", '
        'has ontology-box "ABox"'
        + (", has ontology-world-id " + typedb_string(world_id) if str(world_id or "").strip() else "")
        + "; "
    )
    marker_patterns = [
        "$n has ontology-snapshot-id " + typedb_string(manifest_id) + ";"
        for manifest_id in clean_ids
    ]
    if len(marker_patterns) == 1:
        query += marker_patterns[0] + " "
    else:
        query += " or ".join("{ " + pattern + " }" for pattern in marker_patterns) + "; "
    query += "delete $n;"

    def delete_batch():
        with _bindings.typedb_operation_timeout(
            _store.write_operation_timeout_seconds(),
            "TypeDB ABox Manifest marker delete batch",
        ):
            with driver.transaction(
                _store.database,
                TransactionType.WRITE,
                options=_store.write_transaction_options(),
            ) as tx:
                tx.query(query).resolve()
                tx.commit()

    _store.with_typedb_retries(delete_batch)
    return {
        "status": "ok",
        "deletedBatchCount": 1,
        "removedManifestIds": clean_ids,
        "worldId": str(world_id or "").strip(),
    }


def prune_inactive_scoped_abox_manifests_in_driver(_store: GraphMaintenanceManifestsStore, driver, imported, active_manifest_id: str='', keep_inactive_count: int=None, max_manifests: int=None, max_delete_batches: int=None, delete_batch_size: int=None, world_id: str='', max_duration_seconds: int=None) -> Dict[str, object]:
    """Prune immutable Manifests without deleting generations still referenced.

    A scope generation is a shared immutable object: an unchanged macro or
    reference scope can be referenced by many historical Manifests.  The
    protected set therefore includes the active Manifest and all retained
    rollback Manifests before any old physical rows are removed.
    """
    started_at = time.monotonic()
    duration_limit = (
        None
        if max_duration_seconds is None
        else max(5, min(300, int(max_duration_seconds or 0)))
    )
    deadline_monotonic = (
        None if duration_limit is None else started_at + duration_limit
    )
    active = _store.active_abox_metadata(world_id)
    active_id = str(
        active_manifest_id
        or active.get("worldviewManifestId")
        or active.get("aboxSnapshotId")
        or ""
    ).strip()
    pending = _store.pending_abox_activation(world_id)
    if str(pending.get("status") or "") == "pending":
        return {
            "status": "skipped",
            "reason": "Scoped ABox activation is pending native inference.",
            "activeAboxSnapshotId": active_id,
            "pendingAboxSnapshotId": str(pending.get("candidateAboxSnapshotId") or ""),
            "deletedBatchCount": 0,
        }
    keep_count = (
        _store.abox_inactive_generation_keep_count()
        if keep_inactive_count is None
        else max(0, min(5, int(keep_inactive_count or 0)))
    )
    max_count = (
        _store.abox_inactive_generation_max_prune_per_save()
        if max_manifests is None
        else max(0, min(20, int(max_manifests or 0)))
    )
    max_batch_count = (
        _store.deferred_maintenance_abox_max_delete_batches()
        if max_delete_batches is None
        else max(1, min(50, int(max_delete_batches or 1)))
    )
    bounded_delete_batch_size = (
        _store.deferred_maintenance_abox_delete_batch_size()
        if delete_batch_size is None
        else max(10, min(500, int(delete_batch_size or 0)))
    )
    # Manifest JSON contains the complete scope plan and can be large.
    # Candidate selection needs only immutable ids and timestamps; load
    # full metadata only for the rollback marker and this turn's bounded
    # delete candidates after selection.
    manifest_identities: Dict[str, Dict[str, object]] = {}
    for marker in _store.worldview_manifest_marker_identity_rows(world_id):
        manifest_id = str(
            marker.get("worldviewManifestId")
            or marker.get("aboxSnapshotId")
            or marker.get("snapshotId")
            or ""
        ).strip()
        if not manifest_id or manifest_id == active_id:
            continue
        previous = manifest_identities.get(manifest_id)
        if previous is None or (
            str(marker.get("updatedAt") or ""), str(marker.get("id") or "")
        ) > (
            str(previous.get("updatedAt") or ""), str(previous.get("id") or "")
        ):
            manifest_identities[manifest_id] = {
                **dict(marker),
                "worldviewManifestId": manifest_id,
            }
    ordered_identities = sorted(
        manifest_identities.values(),
        key=lambda item: (
            str(item.get("updatedAt") or ""),
            str(item.get("worldviewManifestId") or ""),
        ),
        reverse=True,
    )
    retained_identities = ordered_identities[:keep_count]
    removable_identities = list(reversed(ordered_identities[keep_count:]))[:max_count]

    def load_selected_metadata(identity: Dict[str, object]) -> Dict[str, object]:
        manifest_id = str(identity.get("worldviewManifestId") or "").strip()
        metadata = dict(_store.scoped_manifest_metadata(manifest_id, world_id) or {})
        if str(metadata.get("status") or "") != "ok":
            return {}
        return {**metadata, "updatedAt": str(identity.get("updatedAt") or "")}

    retained = [
        metadata
        for metadata in (
            load_selected_metadata(identity)
            for identity in retained_identities
        )
        if metadata
    ]
    removable = [
        metadata
        for metadata in (
            load_selected_metadata(identity)
            for identity in removable_identities
        )
        if metadata
    ]
    selected_metadata_missing = (
        len(retained) != len(retained_identities)
        or len(removable) != len(removable_identities)
    )
    protected_generation_ids = {
        str(item or "").strip()
        for item in dict(active.get("scopeGenerationIds") or {}).values()
        if str(item or "").strip()
    }
    for metadata in retained:
        protected_generation_ids.update(
            str(item or "").strip()
            for item in dict(metadata.get("scopeGenerationIds") or {}).values()
            if str(item or "").strip()
        )
    removed = []
    removed_generation_ids = []
    attempted_generation_ids = []
    deleted_batches = 0
    cleanup_rows = []
    remaining_batch_budget = max_batch_count

    # Historical Manifests share immutable scope generations. Walking one
    # Manifest at a time therefore revisits the same generation whenever
    # adjacent observations reused an unchanged scope. Build the exact
    # retired generation set first and reclaim each physical generation
    # at most once per pass. Active and rollback generations remain
    # protected independently of how many removable Manifests reference
    # them.
    removable_generation_references = []
    for metadata in removable:
        removable_generation_references.extend(
            str(item or "").strip()
            for item in dict(metadata.get("scopeGenerationIds") or {}).values()
            if str(item or "").strip()
        )
    retired_generation_ids = []
    seen_retired_generation_ids = set()
    for generation_id in removable_generation_references:
        if (
            generation_id in protected_generation_ids
            or generation_id in seen_retired_generation_ids
        ):
            continue
        seen_retired_generation_ids.add(generation_id)
        retired_generation_ids.append(generation_id)
    generation_cleanup_rows = []
    protected_external_reference_generation_ids = []
    cleanup_partial = selected_metadata_missing
    time_budget_exhausted = False
    resume_generation_id = ""
    resume_manifest_id = ""
    marker_only_manifest_count = sum(
        1
        for metadata in removable
        if not {
            str(item or "").strip()
            for item in dict(metadata.get("scopeGenerationIds") or {}).values()
            if str(item or "").strip()
            and str(item or "").strip() not in protected_generation_ids
        }
    )
    marker_batch_reserve = min(
        marker_only_manifest_count,
        max(1, max_batch_count // 4) if max_batch_count >= 2 else 0,
    )
    for generation_id in retired_generation_ids:
        if (
            remaining_batch_budget <= marker_batch_reserve
            or (
                deadline_monotonic is not None
                and time.monotonic() >= deadline_monotonic
            )
        ):
            cleanup_partial = True
            time_budget_exhausted = bool(
                deadline_monotonic is not None
                and time.monotonic() >= deadline_monotonic
            )
            resume_generation_id = generation_id
            break
        attempted_generation_ids.append(generation_id)
        cleanup = _store.delete_box_snapshot_rows_in_batches(
            driver,
            imported,
            "ABox",
            generation_id,
            batch_size=bounded_delete_batch_size,
            max_batches=remaining_batch_budget,
            deadline_monotonic=deadline_monotonic,
        )
        generation_cleanup_rows.append(cleanup)
        deleted = int(number_or_none(cleanup.get("deletedBatchCount")) or 0)
        deleted_batches += deleted
        remaining_batch_budget = max(0, remaining_batch_budget - deleted)
        if str(cleanup.get("status") or "") == "ok":
            removed_generation_ids.append(generation_id)
        elif str(cleanup.get("status") or "") == "protected-external-relation-reference":
            # A node generation may still be a role player in a relation
            # generation selected later in this same maintenance slice, or
            # in the active/rollback graph. Keep the node intact and keep
            # draining other retired relation generations. A later pass can
            # reclaim it once the final external relation is gone.
            cleanup_partial = True
            protected_external_reference_generation_ids.append(generation_id)
            resume_generation_id = resume_generation_id or generation_id
        else:
            cleanup_partial = True
            time_budget_exhausted = bool(cleanup.get("timeBudgetExhausted"))
            resume_generation_id = generation_id
            break

    # A Manifest marker can disappear only after every physical generation
    # that it alone retained has been reclaimed. Markers whose scopes are
    # all protected or completed in this pass are safe to remove now.
    removed_generation_set = set(removed_generation_ids)
    safe_marker_ids = []
    for metadata in removable:
        manifest_id = str(metadata.get("worldviewManifestId") or metadata.get("aboxSnapshotId") or "").strip()
        if not manifest_id:
            continue
        required_generations = {
            str(item or "").strip()
            for item in dict(metadata.get("scopeGenerationIds") or {}).values()
            if str(item or "").strip() and str(item or "").strip() not in protected_generation_ids
        }
        if not required_generations.issubset(removed_generation_set):
            cleanup_partial = True
            resume_manifest_id = resume_manifest_id or manifest_id
            continue
        safe_marker_ids.append(manifest_id)
    if safe_marker_ids and remaining_batch_budget > 0 and not (
        deadline_monotonic is not None and time.monotonic() >= deadline_monotonic
    ):
        # A marker is one small node, so delete the independently verified
        # marker set in one transaction. Physical generations continue to
        # use the bounded row batches above.
        cleanup = _store.delete_worldview_manifest_markers_batch(
            driver,
            imported,
            safe_marker_ids,
            world_id=world_id,
        )
        cleanup_rows.append(cleanup)
        deleted = int(number_or_none(cleanup.get("deletedBatchCount")) or 0)
        deleted_batches += deleted
        remaining_batch_budget = max(0, remaining_batch_budget - deleted)
        if str(cleanup.get("status") or "") == "ok":
            removed.extend(cleanup.get("removedManifestIds") or safe_marker_ids)
        else:
            cleanup_partial = True
            resume_manifest_id = safe_marker_ids[0]
    elif safe_marker_ids:
        cleanup_partial = True
        time_budget_exhausted = bool(
            deadline_monotonic is not None and time.monotonic() >= deadline_monotonic
        )
        resume_manifest_id = safe_marker_ids[0]
    return {
        "status": "partial" if cleanup_partial else "ok",
        "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
        "activeAboxSnapshotId": active_id,
        "keepInactiveManifestCount": keep_count,
        "maxManifestsPerRun": max_count,
        "maxDeleteBatches": max_batch_count,
        "deleteBatchSize": bounded_delete_batch_size,
        "remainingDeleteBatchBudget": remaining_batch_budget,
        "markerOnlyManifestCount": marker_only_manifest_count,
        "markerDeleteBatchReserve": marker_batch_reserve,
        "completedInactiveManifestCount": len(ordered_identities),
        "retainedInactiveManifestIds": [
            str(item.get("worldviewManifestId") or item.get("aboxSnapshotId") or "")
            for item in retained
        ],
        "removedManifestIds": removed,
        "plannedRetiredScopeGenerationCount": len(retired_generation_ids),
        "attemptedRetiredScopeGenerationCount": len(attempted_generation_ids),
        "attemptedRetiredScopeGenerationIds": attempted_generation_ids[:100],
        "removedRetiredScopeGenerationCount": len(removed_generation_ids),
        "removedRetiredScopeGenerationIds": removed_generation_ids[:100],
        "protectedExternalRelationGenerationCount": len(
            protected_external_reference_generation_ids
        ),
        "protectedExternalRelationGenerationIds": (
            protected_external_reference_generation_ids[:100]
        ),
        "deduplicatedScopeGenerationReferenceCount": max(
            0,
            len(removable_generation_references) - len(set(removable_generation_references)),
        ),
        "remainingInactiveManifestCount": max(0, len(ordered_identities) - len(removed)),
        "deletedBatchCount": deleted_batches,
        "maxDurationSeconds": duration_limit,
        "durationMs": int((time.monotonic() - started_at) * 1000),
        "timeBudgetExhausted": time_budget_exhausted,
        "resumeRequired": cleanup_partial,
        "resumeGenerationId": resume_generation_id,
        "resumeManifestId": resume_manifest_id,
        "selectedMetadataMissing": selected_metadata_missing,
        "generationCleanup": generation_cleanup_rows,
        "cleanup": cleanup_rows,
    }


def prune_inactive_scoped_abox_manifests(_store: GraphMaintenanceManifestsStore, world_id: str='', keep_inactive_count: int=None, max_manifests: int=None, max_delete_batches: int=None, delete_batch_size: int=None, max_duration_seconds: int=None, *, _bindings: GraphMaintenanceManifestsRuntime) -> Dict[str, object]:
    """Run one bounded, reference-aware scoped ABox maintenance pass."""
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
                active = _store.active_abox_metadata(world_id)
                return _store.prune_inactive_scoped_abox_manifests_in_driver(
                    driver,
                    imported,
                    active_manifest_id=str(
                        active.get("worldviewManifestId") or active.get("aboxSnapshotId") or ""
                    ),
                    keep_inactive_count=keep_inactive_count,
                    max_manifests=max_manifests,
                    max_delete_batches=max_delete_batches,
                    delete_batch_size=delete_batch_size,
                    world_id=world_id,
                    max_duration_seconds=max_duration_seconds,
                )
            finally:
                _store.close_driver(driver)

        result = _store.with_typedb_retries(operation)
        return {"configured": True, "graphStore": "typedb", **dict(result or {})}
    except Exception as error:  # noqa: BLE001 - valid inference remains usable if maintenance is delayed.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": str(error)[:220],
        }
