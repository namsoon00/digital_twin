"""graph_maintenance: generations through explicit injected capabilities."""

from digital_twin.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from typing import Dict
from typing import Iterable
from typing import List
import time
from .generations_ports import GraphMaintenanceGenerationsStore, GraphMaintenanceGenerationsRuntime


def box_snapshot_instance_exists(_store: GraphMaintenanceGenerationsStore, driver, imported, box: str, snapshot_id: str, type_label: str) -> bool:
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    query = (
        "match $item isa " + str(type_label)
        + ", has ontology-box " + typedb_string(box)
        + ", has ontology-snapshot-id " + typedb_string(snapshot_id)
        + "; limit 1;"
    )
    with driver.transaction(_store.database, TransactionType.READ) as tx:
        return bool(_store.read_rows_in_transaction(tx, query, [], label="typedb.abox-candidate-exists"))


def box_manifest_instance_exists(_store: GraphMaintenanceGenerationsStore, driver, imported, box: str, manifest_id: str, type_label: str, world_id: str='') -> bool:
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    clean_world_id = str(world_id or "").strip()
    query = (
        "match $item isa " + str(type_label)
        + ", has ontology-box " + typedb_string(box)
        + ", has ontology-manifest-id " + typedb_string(manifest_id)
        + (", has ontology-world-id " + typedb_string(clean_world_id) if clean_world_id else "")
        + "; limit 1;"
    )
    with driver.transaction(_store.database, TransactionType.READ) as tx:
        return bool(_store.read_rows_in_transaction(tx, query, [], label="typedb.abox-manifest-candidate-exists"))


def box_manifest_delete_batch_query(_store: GraphMaintenanceGenerationsStore, box: str, manifest_id: str, type_label: str, batch_size: int, world_id: str='') -> str:
    variable = "$r" if str(type_label) == "ontology-assertion" else "$n"
    clean_world_id = str(world_id or "").strip()
    return (
        "match " + variable + " isa " + str(type_label)
        + ", has ontology-box " + typedb_string(box)
        + ", has ontology-manifest-id " + typedb_string(manifest_id)
        + (", has ontology-world-id " + typedb_string(clean_world_id) if clean_world_id else "")
        + "; limit " + str(max(1, int(batch_size or 1))) + "; delete " + variable + ";"
    )


def delete_box_manifest_rows_in_batches(_store: GraphMaintenanceGenerationsStore, driver, imported, box: str, manifest_id: str, batch_size: int=None, max_batches: int=None, world_id: str='', *, _bindings: GraphMaintenanceGenerationsRuntime) -> Dict[str, object]:
    """Clear a retry candidate by its exact Manifest provenance.

    Scoped generations can be shared by retained Manifests, so a realtime
    retry must not enumerate or delete every generation that happens to
    have the same scope fingerprint. ``ontology-manifest-id`` identifies
    only the incomplete candidate being retried, including its marker.
    """
    clean_box = str(box or "").strip()
    clean_manifest_id = str(manifest_id or "").strip()
    clean_world_id = str(world_id or "").strip()
    if not clean_box or not clean_manifest_id:
        return {"status": "skipped", "deletedBatchCount": 0}
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    configured_batch_size = _store.abox_delete_batch_size() if batch_size is None else int(batch_size or 0)
    safe_batch_size = max(1, min(5000, configured_batch_size))
    safe_max_batches = None if max_batches is None else max(0, int(max_batches or 0))
    deleted_batches = 0
    remaining_types: List[str] = []
    for type_label in ["ontology-assertion", "ontology-node"]:
        while _store.box_manifest_instance_exists(
            driver,
            imported,
            clean_box,
            clean_manifest_id,
            type_label,
            world_id=clean_world_id,
        ):
            if safe_max_batches is not None and deleted_batches >= safe_max_batches:
                remaining_types.append(type_label)
                break
            query = _store.box_manifest_delete_batch_query(
                clean_box,
                clean_manifest_id,
                type_label,
                safe_batch_size,
                world_id=clean_world_id,
            )

            def delete_batch():
                with _bindings.typedb_operation_timeout(_store.write_operation_timeout_seconds(), "TypeDB ABox manifest candidate delete batch"):
                    with driver.transaction(
                        _store.database,
                        TransactionType.WRITE,
                        options=_store.write_transaction_options(),
                    ) as tx:
                        tx.query(query).resolve()
                        tx.commit()

            _store.with_typedb_retries(delete_batch)
            deleted_batches += 1
        if remaining_types:
            break
    if safe_max_batches is not None and deleted_batches >= safe_max_batches:
        for type_label in ["ontology-assertion", "ontology-node"]:
            if _store.box_manifest_instance_exists(
                driver,
                imported,
                clean_box,
                clean_manifest_id,
                type_label,
                world_id=clean_world_id,
            ) and type_label not in remaining_types:
                remaining_types.append(type_label)
    return {
        "status": "partial" if remaining_types else "ok",
        "ontologyBox": clean_box,
        "worldviewManifestId": clean_manifest_id,
        "worldId": clean_world_id,
        "batchSize": safe_batch_size,
        "maxBatches": safe_max_batches,
        "deletedBatchCount": deleted_batches,
        "remainingRowTypes": remaining_types,
    }


def box_snapshot_delete_batch_query(_store: GraphMaintenanceGenerationsStore, box: str, snapshot_id: str, type_label: str, batch_size: int) -> str:
    variable = "$r" if str(type_label) == "ontology-assertion" else "$n"
    return (
        "match " + variable + " isa " + str(type_label)
        + ", has ontology-box " + typedb_string(box)
        + ", has ontology-snapshot-id " + typedb_string(snapshot_id)
        + "; limit " + str(max(1, int(batch_size or 1))) + "; delete " + variable + ";"
    )


def box_snapshot_external_relation_references(_store: GraphMaintenanceGenerationsStore, driver, imported, box: str, snapshot_id: str, limit: int=5) -> List[Dict[str, object]]:
    """Return relations in other generations that still use these nodes.

    TypeDB role players are physical entities. Deleting a retired node
    generation while a relation from another generation still links to it
    silently damages that relation, even when the relation's own generation
    remains protected by the active Manifest. Generation retention must
    therefore close over physical role-player references, not only direct
    Manifest generation ids.
    """

    clean_box = str(box or "").strip()
    clean_snapshot_id = str(snapshot_id or "").strip()
    if not clean_box or not clean_snapshot_id:
        return []
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    bounded_limit = max(1, min(20, int(limit or 5)))
    query = (
        "match "
        "$n isa ontology-node, "
        "has ontology-box " + typedb_string(clean_box) + ", "
        "has ontology-snapshot-id " + typedb_string(clean_snapshot_id) + ", "
        "has ontology-storage-id $nodeStorageId; "
        "$r isa ontology-assertion, "
        "has ontology-box $relationBox, "
        "has ontology-snapshot-id $relationSnapshotId, "
        "has ontology-storage-id $relationStorageId; "
        "{ $r links (source: $n); } or { $r links (target: $n); }; "
        "$relationSnapshotId != " + typedb_string(clean_snapshot_id) + "; "
        "limit " + str(bounded_limit) + ";"
    )
    with driver.transaction(_store.database, TransactionType.READ) as tx:
        return _store.read_rows_in_transaction(
            tx,
            query,
            [
                "nodeStorageId",
                "relationBox",
                "relationSnapshotId",
                "relationStorageId",
            ],
            label="typedb.abox-generation-external-relation-reference",
        )


def delete_box_snapshot_rows_in_batches(_store: GraphMaintenanceGenerationsStore, driver, imported, box: str, snapshot_id: str, batch_size: int=None, max_batches: int=None, deadline_monotonic: float=None, *, _bindings: GraphMaintenanceGenerationsRuntime) -> Dict[str, object]:
    """Delete one inactive ABox generation in short TypeDB writes.

    ``max_batches`` turns the operation into a bounded maintenance slice.
    The active projection path uses that mode so historical cleanup cannot
    consume an entire realtime reasoning cycle.
    """
    clean_box = str(box or "").strip()
    clean_snapshot_id = str(snapshot_id or "").strip()
    if not clean_box or not clean_snapshot_id:
        return {"status": "skipped", "deletedBatchCount": 0}
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    configured_batch_size = _store.abox_delete_batch_size() if batch_size is None else int(batch_size or 0)
    safe_batch_size = max(1, min(5000, configured_batch_size))
    safe_max_batches = None if max_batches is None else max(0, int(max_batches or 0))
    started_at = time.monotonic()
    deleted_batches = 0
    remaining_types: List[str] = []
    time_budget_exhausted = False
    external_references = _store.box_snapshot_external_relation_references(
        driver,
        imported,
        clean_box,
        clean_snapshot_id,
    )
    if external_references:
        return {
            "status": "protected-external-relation-reference",
            "ontologyBox": clean_box,
            "aboxSnapshotId": clean_snapshot_id,
            "batchSize": safe_batch_size,
            "maxBatches": safe_max_batches,
            "deletedBatchCount": 0,
            "remainingRowTypes": ["ontology-node"],
            "timeBudgetExhausted": False,
            "resumeRequired": True,
            "externalRelationReferenceCount": len(external_references),
            "externalRelationReferences": external_references,
            "reason": (
                "The ABox node generation is still referenced by a relation "
                "in another physical generation."
            ),
            "durationMs": int((time.monotonic() - started_at) * 1000),
        }
    for type_label in ["ontology-assertion", "ontology-node"]:
        while True:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                time_budget_exhausted = True
                remaining_types.append(type_label)
                break
            if not _store.box_snapshot_instance_exists(
                driver,
                imported,
                clean_box,
                clean_snapshot_id,
                type_label,
            ):
                break
            if safe_max_batches is not None and deleted_batches >= safe_max_batches:
                remaining_types.append(type_label)
                break
            query = _store.box_snapshot_delete_batch_query(
                clean_box,
                clean_snapshot_id,
                type_label,
                safe_batch_size,
            )

            def delete_batch():
                with _bindings.typedb_operation_timeout(_store.write_operation_timeout_seconds(), "TypeDB ABox candidate delete batch"):
                    with driver.transaction(
                        _store.database,
                        TransactionType.WRITE,
                        options=_store.write_transaction_options(),
                    ) as tx:
                        tx.query(query).resolve()
                        tx.commit()

            _store.with_typedb_retries(delete_batch)
            deleted_batches += 1
        if remaining_types:
            break
    if (
        not time_budget_exhausted
        and safe_max_batches is not None
        and deleted_batches >= safe_max_batches
    ):
        for type_label in ["ontology-assertion", "ontology-node"]:
            if _store.box_snapshot_instance_exists(
                driver,
                imported,
                clean_box,
                clean_snapshot_id,
                type_label,
            ) and type_label not in remaining_types:
                remaining_types.append(type_label)
    return {
        "status": "partial" if remaining_types else "ok",
        "ontologyBox": clean_box,
        "aboxSnapshotId": clean_snapshot_id,
        "batchSize": safe_batch_size,
        "maxBatches": safe_max_batches,
        "deletedBatchCount": deleted_batches,
        "remainingRowTypes": remaining_types,
        "timeBudgetExhausted": time_budget_exhausted,
        "resumeRequired": bool(remaining_types),
        "durationMs": int((time.monotonic() - started_at) * 1000),
    }


def discard_abox_generation(_store: GraphMaintenanceGenerationsStore, snapshot_id: str, *, _bindings: GraphMaintenanceGenerationsRuntime) -> Dict[str, object]:
    """Delete one failed, inactive candidate generation immediately."""
    clean_snapshot_id = str(snapshot_id or "").strip()
    if not clean_snapshot_id:
        return {
            "configured": bool(_store.address),
            "status": "skipped",
            "graphStore": "typedb",
            "aboxSnapshotId": "",
            "reason": "ABox snapshot id is empty.",
        }
    if _store.scoped_manifest_metadata(clean_snapshot_id):
        return _store.discard_scoped_abox_manifest(clean_snapshot_id)
    active = _store.active_abox_metadata()
    if str(active.get("aboxSnapshotId") or "").strip() == clean_snapshot_id:
        return {
            "configured": True,
            "status": "protected-active",
            "graphStore": "typedb",
            "aboxSnapshotId": clean_snapshot_id,
            "reason": "The active ABox generation cannot be discarded.",
        }
    imported = _store.driver_imports()
    if imported[0] is None:
        return {
            "configured": True,
            "status": "driver-missing",
            "graphStore": "typedb",
            "aboxSnapshotId": clean_snapshot_id,
            "reason": str(imported[1])[:180],
        }
    try:
        def operation():
            driver = _store.open_driver(imported)
            try:
                _store.ensure_database(driver)
                return _store.delete_box_snapshot_rows_in_batches(
                    driver,
                    imported,
                    "ABox",
                    clean_snapshot_id,
                )
            finally:
                _store.close_driver(driver)

        result = _store.with_typedb_retries(operation)
        return {
            "configured": True,
            "graphStore": "typedb",
            **dict(result or {}),
        }
    except Exception as error:  # noqa: BLE001 - cleanup state remains visible to the circuit breaker.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "aboxSnapshotId": clean_snapshot_id,
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": str(error)[:220],
        }


def delete_box_rows_in_batches(_store: GraphMaintenanceGenerationsStore, driver, imported, boxes: Iterable[str], *, _bindings: GraphMaintenanceGenerationsRuntime) -> Dict[str, object]:
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    batch_size = _store.abox_delete_batch_size()
    deleted_batches = 0
    for box in sorted({str(item or "").strip() for item in boxes or [] if str(item or "").strip()}):
        for type_label in ["ontology-assertion", "ontology-node"]:
            while _store.box_instance_exists(driver, imported, box, type_label):
                query = _store.box_delete_batch_query(box, type_label, batch_size)

                def delete_batch():
                    with _bindings.typedb_operation_timeout(_store.write_operation_timeout_seconds(), "TypeDB ABox delete batch"):
                        with driver.transaction(
                            _store.database,
                            TransactionType.WRITE,
                            options=_store.write_transaction_options(),
                        ) as tx:
                            tx.query(query).resolve()
                            tx.commit()

                _store.with_typedb_retries(delete_batch)
                deleted_batches += 1
    return {
        "status": "ok",
        "boxes": sorted({str(item or "").strip() for item in boxes or [] if str(item or "").strip()}),
        "batchSize": batch_size,
        "deletedBatchCount": deleted_batches,
    }


def delete_world_abox_control_rows(_store: GraphMaintenanceGenerationsStore, driver, imported, world_id: str='', *, _bindings: GraphMaintenanceGenerationsRuntime) -> Dict[str, object]:
    """Replace only one world's pointer and activation journal.

    Historical code replaced every ``ABoxControl`` record during one
    account's activation.  World-aware controls are intentionally
    independent, so the delete predicate includes the durable world
    attribute whenever the caller has an explicit world id.
    """
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    query = (
        'match $n isa ontology-node, has ontology-box "ABoxControl"'
        + (", has ontology-world-id " + typedb_string(world_id) if str(world_id or "").strip() else "")
        + "; delete $n;"
    )

    def operation():
        with _bindings.typedb_operation_timeout(_store.write_operation_timeout_seconds(), "TypeDB world ABox control swap"):
            with driver.transaction(
                _store.database,
                TransactionType.WRITE,
                options=_store.write_transaction_options(),
            ) as tx:
                tx.query(query).resolve()
                tx.commit()

    _store.with_typedb_retries(operation)
    return {"status": "ok", "worldId": str(world_id or "")}


def cleanup_inactive_abox_candidates(_store: GraphMaintenanceGenerationsStore, driver, imported, active_snapshot_id: str='') -> Dict[str, object]:
    """Remove incomplete candidate generations without touching the active ABox.

    Candidate writes are committed in bounded batches. If a process exits
    between node and relation batches, those partial generations must not
    accumulate indefinitely or create ambiguous endpoint matches for the
    next candidate. The active pointer is the only generation preserved.
    """
    active = str(active_snapshot_id or "").strip()
    candidates = _store.abox_candidate_snapshot_ids()
    stale_candidates = [snapshot_id for snapshot_id in candidates if snapshot_id != active]
    deleted_batches = 0
    if active:
        for snapshot_id in stale_candidates:
            result = _store.delete_box_snapshot_rows_in_batches(
                driver,
                imported,
                "ABox",
                snapshot_id,
            )
            deleted_batches += int(number_or_none(result.get("deletedBatchCount")) or 0)
    elif candidates:
        result = _store.delete_box_rows_in_batches(driver, imported, ["ABox"])
        deleted_batches += int(number_or_none(result.get("deletedBatchCount")) or 0)

    # ABoxStaging belongs to the previous two-box rollout. It is never an
    # active generation in the pointer model and can be cleaned safely.
    legacy = _store.delete_box_rows_in_batches(driver, imported, ["ABoxStaging"])
    deleted_batches += int(number_or_none(legacy.get("deletedBatchCount")) or 0)
    return {
        "status": "ok",
        "activeAboxSnapshotId": active,
        "candidateSnapshotIds": candidates,
        "removedCandidateSnapshotIds": stale_candidates,
        "deletedBatchCount": deleted_batches,
        "legacyStagingCleanup": legacy,
    }


def drain_inactive_abox_generations_incrementally(_store: GraphMaintenanceGenerationsStore, driver, imported, active_snapshot_id: str='', excluded_snapshot_ids: Iterable[str]=None) -> Dict[str, object]:
    """Reclaim a bounded slice of inactive ABox generations.

    Native inference and notification delivery must not wait for a full
    historical deletion. The active pointer is already verified before
    this method runs, and pending hand-offs are never cleaned here.
    """
    active = str(active_snapshot_id or "").strip()
    excluded = {
        str(item or "").strip()
        for item in excluded_snapshot_ids or []
        if str(item or "").strip()
    }
    if not active:
        return {
            "status": "skipped",
            "reason": "No active ABox generation is available for safe incremental cleanup.",
            "activeAboxSnapshotId": active,
            "deletedBatchCount": 0,
        }
    pending = _store.pending_abox_activation()
    if str(pending.get("status") or "") == "pending":
        return {
            "status": "skipped",
            "reason": "ABox activation is pending native inference.",
            "activeAboxSnapshotId": active,
            "pendingAboxSnapshotId": str(pending.get("candidateAboxSnapshotId") or ""),
            "deletedBatchCount": 0,
        }
    max_batches = _store.abox_incremental_cleanup_max_batches_per_save()
    if max_batches <= 0:
        return {
            "status": "skipped",
            "reason": "Incremental ABox cleanup is disabled by runtime setting.",
            "activeAboxSnapshotId": active,
            "deletedBatchCount": 0,
        }
    candidates = [
        snapshot_id
        for snapshot_id in _store.abox_candidate_snapshot_ids()
        if snapshot_id != active and snapshot_id not in excluded
    ]
    markers = _store.abox_projection_marker_rows()
    marker_by_snapshot: Dict[str, Dict[str, object]] = {}
    for marker in markers:
        snapshot_id = str(marker.get("aboxSnapshotId") or marker.get("snapshotId") or "").strip()
        if snapshot_id and snapshot_id in candidates:
            previous = marker_by_snapshot.get(snapshot_id)
            if previous is None or (
                str(marker.get("updatedAt") or ""), str(marker.get("id") or "")
            ) > (
                str(previous.get("updatedAt") or ""), str(previous.get("id") or "")
            ):
                marker_by_snapshot[snapshot_id] = marker
    incomplete = sorted(snapshot_id for snapshot_id in candidates if snapshot_id not in marker_by_snapshot)
    completed_newest_first = sorted(
        marker_by_snapshot,
        key=lambda snapshot_id: (
            str(marker_by_snapshot[snapshot_id].get("updatedAt") or ""),
            str(marker_by_snapshot[snapshot_id].get("id") or ""),
            snapshot_id,
        ),
        reverse=True,
    )
    keep_count = _store.abox_inactive_generation_keep_count()
    retained = completed_newest_first[:keep_count]
    completed_oldest_first = list(reversed(completed_newest_first[keep_count:]))
    targets = incomplete + completed_oldest_first
    deleted_batches = 0
    attempted: List[str] = []
    slices: List[Dict[str, object]] = []
    remaining_budget = max_batches
    for snapshot_id in targets:
        if remaining_budget <= 0:
            break
        cleanup = _store.delete_box_snapshot_rows_in_batches(
            driver,
            imported,
            "ABox",
            snapshot_id,
            batch_size=_store.abox_incremental_cleanup_batch_size(),
            max_batches=remaining_budget,
        )
        attempted.append(snapshot_id)
        slices.append(cleanup)
        deleted = int(number_or_none(cleanup.get("deletedBatchCount")) or 0)
        deleted_batches += deleted
        remaining_budget = max(0, remaining_budget - deleted)
        if str(cleanup.get("status") or "") == "partial":
            break
    remaining = [snapshot_id for snapshot_id in targets if snapshot_id not in attempted]
    if slices and str(slices[-1].get("status") or "") == "partial":
        remaining = [str(slices[-1].get("aboxSnapshotId") or "")] + remaining
    return {
        "status": "partial" if remaining else "ok",
        "activeAboxSnapshotId": active,
        "excludedSnapshotIds": sorted(excluded),
        "candidateSnapshotIds": candidates,
        "retainedInactiveSnapshotIds": retained,
        "cleanupTargetSnapshotIds": targets,
        "attemptedSnapshotIds": attempted,
        "remainingSnapshotIds": [item for item in remaining if item],
        "batchSize": _store.abox_incremental_cleanup_batch_size(),
        "maxBatches": max_batches,
        "deletedBatchCount": deleted_batches,
        "slices": slices,
    }


def prune_inactive_abox_generations(_store: GraphMaintenanceGenerationsStore, driver, imported, active_snapshot_id: str='', keep_inactive_count: int=None, max_generations: int=None) -> Dict[str, object]:
    """Bound retention to completed ABox generations after activation.

    This intentionally operates on completion markers, not every physical
    ABox row. A marker is written only after the candidate rows are present
    and verified; preserving the active pointer plus recent marked
    generations makes deletion safe in the single-writer activation path.
    Unmarked interrupted candidates remain available for retry diagnostics
    and are cleared only when that exact snapshot is retried.
    """
    active = str(active_snapshot_id or "").strip()
    active_metadata: Dict[str, object] = {}
    if active.startswith("abox-manifest:") or not active:
        try:
            active_metadata = _store.active_abox_metadata()
        except Exception:
            active_metadata = {}
    if str(active_metadata.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION:
        return _store.prune_inactive_scoped_abox_manifests_in_driver(
            driver,
            imported,
            active_manifest_id=active or str(
                active_metadata.get("worldviewManifestId") or active_metadata.get("aboxSnapshotId") or ""
            ),
            keep_inactive_count=keep_inactive_count,
            max_manifests=max_generations,
        )
    keep_count = (
        _store.abox_inactive_generation_keep_count()
        if keep_inactive_count is None
        else max(0, min(5, int(keep_inactive_count or 0)))
    )
    max_count = (
        _store.abox_inactive_generation_max_prune_per_save()
        if max_generations is None
        else max(0, min(10, int(max_generations or 0)))
    )
    markers = _store.abox_projection_marker_rows()
    marker_by_snapshot: Dict[str, Dict[str, object]] = {}
    for marker in markers:
        snapshot_id = str(marker.get("aboxSnapshotId") or marker.get("snapshotId") or "").strip()
        if not snapshot_id or snapshot_id == active:
            continue
        previous = marker_by_snapshot.get(snapshot_id)
        if previous is None or (
            str(marker.get("updatedAt") or ""), str(marker.get("id") or "")
        ) > (
            str(previous.get("updatedAt") or ""), str(previous.get("id") or "")
        ):
            marker_by_snapshot[snapshot_id] = marker
    ordered_inactive = sorted(
        marker_by_snapshot,
        key=lambda snapshot_id: (
            str(marker_by_snapshot[snapshot_id].get("updatedAt") or ""),
            str(marker_by_snapshot[snapshot_id].get("id") or ""),
            snapshot_id,
        ),
        reverse=True,
    )
    retained = ordered_inactive[:keep_count]
    # Preserve the most recent completed predecessor, then drain the
    # oldest backlog first. This keeps a useful rollback/audit generation
    # while reducing the worst historical amplification immediately.
    removable = list(reversed(ordered_inactive[keep_count:]))[:max_count]
    deleted_batches = 0
    removed = []
    for snapshot_id in removable:
        result = _store.delete_box_snapshot_rows_in_batches(driver, imported, "ABox", snapshot_id)
        deleted_batches += int(number_or_none(result.get("deletedBatchCount")) or 0)
        removed.append(snapshot_id)
    return {
        "status": "ok",
        "activeAboxSnapshotId": active,
        "keepInactiveGenerationCount": keep_count,
        "maxGenerationsPerSave": max_count,
        "completedInactiveCandidateCount": len(ordered_inactive),
        "retainedInactiveSnapshotIds": retained,
        "removedCandidateSnapshotIds": removed,
        "remainingInactiveCandidateCount": max(0, len(ordered_inactive) - len(removed)),
        "deletedBatchCount": deleted_batches,
    }


def clear_boxes_in_batches(_store: GraphMaintenanceGenerationsStore, boxes: Iterable[str]) -> Dict[str, object]:
    clean_boxes = sorted({str(item or "").strip() for item in boxes or [] if str(item or "").strip()})
    if not clean_boxes:
        return {"status": "skipped", "boxes": [], "deletedBatchCount": 0}
    imported = _store.driver_imports()
    if imported[0] is None:
        return {"status": "driver-missing", "boxes": clean_boxes, "reason": str(imported[1])[:180]}
    try:
        def operation():
            driver = _store.open_driver(imported)
            try:
                _store.ensure_database(driver)
                _store.ensure_schema(driver, imported)
                return _store.delete_box_rows_in_batches(driver, imported, clean_boxes)
            finally:
                _store.close_driver(driver)
        return _store.with_typedb_retries(operation)
    except Exception as error:  # noqa: BLE001 - preserve the original write failure while reporting cleanup state.
        return {"status": "error", "boxes": clean_boxes, "reason": str(error)[:180]}
