"""manifest: save through explicit injected capabilities."""

from digital_twin.domain.abox_lifecycle import MANIFEST_PATCH_BOUNDARY_VERSION
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_current_state import CURRENT_STATE_ABOX_PERSISTENCE_MODE
from digital_twin.domain.ontology_scopes import (
    SCOPED_ABOX_MANIFEST_VERSION,
    SCOPED_ABOX_PERSISTENCE_MODE,
)
from digital_twin.modules.reasoning.infrastructure.abox_candidates.identity import (
    ontology_row_content_fingerprint,
    ontology_storage_id,
    relation_row_id,
)
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    native_rule_manifest_index_required,
    normalize_native_rule_evidence_read_index,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
)
from typing import Dict, Iterable
import time
from .save_ports import ManifestSaveStore, ManifestSaveRuntime


def save_scoped_abox_graph(
    _store: ManifestSaveStore,
    graph: PortfolioOntology,
    boxes: Iterable[str] = None,
    adopted_write_lease: Dict[str, object] = None,
    *,
    _bindings: ManifestSaveRuntime
) -> Dict[str, object]:
    """Stage changed scopes before native inference activates a Manifest."""
    logical_scope_plan = _store.scoped_abox_plan(graph)
    scope_plan = list(logical_scope_plan)
    worldview = dict(getattr(graph, "worldview", {}) or {})
    current_state_mode = _store.is_current_state_scoped_abox_graph(graph)
    current_state_persistence_mode = str(
        worldview.get("persistenceMode") or worldview.get("physicalStateMode") or ""
    ).strip()
    copy_on_write_mode = bool(
        current_state_mode and current_state_persistence_mode == CURRENT_STATE_ABOX_PERSISTENCE_MODE
    )
    target_patch = dict(worldview.get("targetScopedManifestPatch") or {})
    if str(target_patch.get("mode") or "") == "incremental-target-scoped-manifest-patch":
        patch_contract = dict(target_patch.get("manifestPatchContract") or {})
        patch_validation = dict(patch_contract.get("validation") or {})
        if (
            str(patch_contract.get("version") or "") != MANIFEST_PATCH_BOUNDARY_VERSION
            or not bool(patch_validation.get("valid"))
            or str(patch_validation.get("status") or "") != "valid"
        ):
            return {
                "configured": True,
                "saved": False,
                "status": "invalid-manifest-patch-contract",
                "graphStore": "typedb",
                "preservedActiveGeneration": True,
                "reason": "Incremental ABox persistence requires a validated Manifest patch contract.",
                "manifestPatchContract": patch_contract,
            }
    topology_migration = (
        dict((worldview.get("targetScopedManifestPatch") or {}).get("scopeTopologyMigration") or {})
        if isinstance(worldview.get("targetScopedManifestPatch"), dict)
        else {}
    )
    world_id = str(worldview.get("worldId") or "").strip()
    manifest_id = str(
        worldview.get("worldviewManifestId") or worldview.get("aboxSnapshotId") or ""
    ).strip()
    inference_target_symbols = clean_symbols_from_payload(
        worldview.get("inferenceTargetSymbols") or worldview.get("targetSymbols") or []
    )
    if not logical_scope_plan or not manifest_id:
        return {
            "configured": True,
            "saved": False,
            "status": "invalid-scoped-abox",
            "graphStore": "typedb",
            "reason": "Scoped ABox graph has no complete scope plan or Manifest id.",
        }
    # The active pointer protects read consistency, but individual scoped
    # writes commit in bounded batches. Take a durable lease before even
    # looking at the active Manifest so two workers cannot delete a shared
    # macro/reference generation while each is staging a successor.
    adopted_write_lease = dict(adopted_write_lease or {})
    adopted_owner = str(
        adopted_write_lease.get("leaseOwner") or adopted_write_lease.get("owner") or ""
    ).strip()
    lease_is_adopted = bool(adopted_owner)
    if lease_is_adopted:
        current_lease = _store.scoped_abox_write_lease_status(world_id)
        if (
            str(current_lease.get("status") or "") != "held"
            or str(current_lease.get("leaseOwner") or "") != adopted_owner
        ):
            return {
                "configured": True,
                "saved": False,
                "status": "invalid-scoped-write-lease",
                "graphStore": "typedb",
                "aboxSnapshotId": manifest_id,
                "worldviewManifestId": manifest_id,
                "worldId": world_id,
                "preservedActiveGeneration": True,
                "reason": "The adopted scoped ABox write lease is no longer owned by this projection.",
            }
        write_lease = {
            **adopted_write_lease,
            "acquired": True,
            "leaseOwner": adopted_owner,
            "worldId": world_id,
        }
    else:
        write_lease = _store.acquire_scoped_abox_write_lease(manifest_id, world_id=world_id)
        if not write_lease.get("acquired"):
            return {
                "configured": True,
                "saved": False,
                "status": "deferred-scoped-write-lease",
                "graphStore": "typedb",
                "aboxSnapshotId": manifest_id,
                "worldviewManifestId": manifest_id,
                "preservedActiveGeneration": True,
                "reason": "Another scoped ABox projection is still staging or activating a Worldview Manifest.",
                "writeLease": {
                    key: value
                    for key, value in dict(write_lease or {}).items()
                    if key != "propertiesJson"
                },
            }
    lease_released = False

    def release_write_lease() -> Dict[str, object]:
        nonlocal lease_released
        if lease_released:
            return {"status": "already-released"}
        lease_released = True
        if lease_is_adopted:
            return {
                "status": "adopted-by-caller",
                "leaseOwner": adopted_owner,
                "worldId": world_id,
            }
        try:
            return _store.release_scoped_abox_write_lease(write_lease)
        except (
            Exception
        ) as error:  # noqa: BLE001 - expiry protects the next retry if release fails.
            return {"status": "error", "reason": str(error)[:180]}

    try:
        fresh_world_bootstrap = _store.fresh_candidate_world_bootstrap_required(world_id)
        pending_before = (
            {
                "status": "skipped-fresh-candidate",
                "reason": "A newly created blue-green candidate has no pending PortfolioWorld ABox.",
            }
            if fresh_world_bootstrap
            else _store.pending_abox_activation(world_id)
        )
    except Exception as error:  # noqa: BLE001 - do not overlap two uncertain generations.
        release = release_write_lease()
        return {
            "configured": True,
            "saved": False,
            "status": "pending-abox-activation-unreadable",
            "graphStore": "typedb",
            "aboxSnapshotId": manifest_id,
            "worldviewManifestId": manifest_id,
            "preservedActiveGeneration": True,
            "reason": "Pending ABox activation could not be read: " + str(error)[:180],
            "writeLeaseRelease": release,
        }
    if str(pending_before.get("status") or "") == "pending":
        pending_manifest_id = str(pending_before.get("candidateAboxSnapshotId") or "").strip()
        release = release_write_lease()
        same_manifest = pending_manifest_id == manifest_id
        return {
            "configured": True,
            "saved": False,
            "status": (
                "staged-scoped-manifest" if same_manifest else "deferred-pending-scoped-manifest"
            ),
            "graphStore": "typedb",
            "aboxSnapshotId": pending_manifest_id or manifest_id,
            "worldviewManifestId": pending_manifest_id or manifest_id,
            "preservedActiveGeneration": True,
            "pendingAboxActivation": pending_before,
            "reason": (
                "This Worldview Manifest is already staged and awaits native inference."
                if same_manifest
                else "A different staged Worldview Manifest must finish or roll back before another ABox write."
            ),
            "writeLeaseRelease": release,
        }

    try:
        active_before = {} if fresh_world_bootstrap else _store.active_abox_metadata(world_id)
    except Exception:
        active_before = {}
    scoped_active = (
        str(active_before.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION
    )
    migration_mode = str(worldview.get("currentStateMigrationMode") or "").strip().lower()
    active_manifest_index_repair: Dict[str, object] = {}
    if scoped_active and native_rule_manifest_index_required(active_before):
        active_index = normalize_native_rule_evidence_read_index(
            active_before.get("nativeRuleEvidenceReadIndex"),
            planner_topology=active_before.get("nativeRulePlannerTopology"),
        )
        if str(active_index.get("status") or "") != "ok":
            active_manifest_index_repair = _store.repair_active_manifest_native_rule_evidence_index(
                active_before,
                world_id=world_id,
                expected_manifest_id=str(
                    active_before.get("worldviewManifestId")
                    or active_before.get("aboxSnapshotId")
                    or ""
                ),
                stable_write_lease_held=True,
            )
            if str(active_manifest_index_repair.get("status") or "") not in {"ok", "unchanged"}:
                release = release_write_lease()
                return {
                    "configured": True,
                    "saved": False,
                    "status": "active-manifest-evidence-index-repair-failed",
                    "graphStore": "typedb",
                    "aboxSnapshotId": manifest_id,
                    "worldviewManifestId": manifest_id,
                    "worldId": world_id,
                    "preservedActiveGeneration": True,
                    "reason": (
                        "The active Manifest evidence index could not be repaired, so a partial successor was not staged. "
                        + str(active_manifest_index_repair.get("reason") or "")[:180]
                    ),
                    "activeManifestEvidenceIndexRepair": active_manifest_index_repair,
                    "writeLeaseRelease": release,
                }
            active_before = dict(_store.active_abox_metadata(world_id) or {})
    semantic_changed_scope_ids = _store.scoped_abox_semantic_changed_scope_ids(
        logical_scope_plan,
        active_before,
        current_state_mode=current_state_mode,
        migration_mode=migration_mode,
        current_state_persistence_mode=current_state_persistence_mode,
    )
    changed_scope_ids = _store.scoped_abox_changed_scope_ids(
        logical_scope_plan,
        active_before,
        current_state_mode=current_state_mode,
        migration_mode=migration_mode,
        current_state_persistence_mode=current_state_persistence_mode,
        relation_rebind_root_scope_ids=(
            dict(worldview.get("targetScopedManifestPatch") or {}).get("relationRebindRootScopeIds")
            if isinstance(worldview.get("targetScopedManifestPatch"), dict)
            else None
        ),
    )
    rebind_only_relation_scope_ids = _store.scoped_abox_rebind_only_relation_scope_ids(
        logical_scope_plan,
        semantic_changed_scope_ids,
        changed_scope_ids,
    )
    persistence_graph = graph
    if current_state_mode:
        scope_plan = _store.current_state_physical_scope_plan(
            logical_scope_plan,
            active_before,
            changed_scope_ids,
            world_id,
            persistence_mode=current_state_persistence_mode,
            transition_id=str(worldview.get("projectionRunId") or manifest_id),
        )
        persistence_graph = _store.current_state_physical_graph(graph, scope_plan)
    previous_manifest_id = str(
        active_before.get("worldviewManifestId") or active_before.get("aboxSnapshotId") or ""
    ).strip()
    if scoped_active and previous_manifest_id == manifest_id and not changed_scope_ids:
        release_write_lease()
        return {
            "configured": True,
            "saved": False,
            "status": "unchanged-scoped-manifest",
            "graphStore": "typedb",
            "aboxSnapshotId": manifest_id,
            "worldviewManifestId": manifest_id,
            "changedScopeIds": [],
            "scopePlan": scope_plan,
            "activeAbox": active_before,
            "currentStateMigrationMode": migration_mode,
        }
    if scoped_active and previous_manifest_id == manifest_id:
        release = release_write_lease()
        return {
            "configured": True,
            "saved": False,
            "status": "invalid-scoped-manifest-reuse",
            "graphStore": "typedb",
            "aboxSnapshotId": manifest_id,
            "worldviewManifestId": manifest_id,
            "worldId": world_id,
            "changedScopeIds": changed_scope_ids,
            "preservedActiveGeneration": True,
            "reason": "A scoped ABox Manifest must change whenever its scope generation changes.",
            "writeLeaseRelease": release,
        }
    deferred_scope_ids = {
        str(value or "").strip()
        for value in target_patch.get("deferredScopeIds") or []
        if str(value or "").strip()
    }
    active_generations = dict(active_before.get("scopeGenerationIds") or {})
    native_index_reuse_scope_ids = _store.scoped_abox_native_index_reuse_scope_ids(
        target_patch,
        active_generations,
        changed_scope_ids,
        scope_plan,
    )
    candidate_deferred_scope_ids = deferred_scope_ids.union(native_index_reuse_scope_ids)
    active_reuse_plan = _store.scoped_abox_active_reuse_scope_ids(
        scope_plan,
        active_generations,
        changed_scope_ids,
        candidate_deferred_scope_ids,
        rebind_only_relation_scope_ids,
    )
    active_reuse_scope_ids = list(active_reuse_plan.get("scopeIds") or [])
    active_relation_endpoint_scope_ids = list(
        active_reuse_plan.get("relationEndpointScopeIds") or []
    )
    active_scope_rows: Dict[str, object] = {
        "status": "ok",
        "scopeIds": [],
        "nodeRows": [],
        "relationRows": [],
        "endpointNodeRows": [],
        "countsByScope": {},
    }
    active_scope_read_started = time.monotonic()
    if current_state_mode and scoped_active and active_reuse_scope_ids:
        try:
            active_scope_rows = _store.read_active_scoped_abox_rows(
                active_before,
                active_reuse_scope_ids,
                world_id=world_id,
            )
        except (
            Exception
        ) as error:  # noqa: BLE001 - preserve the active Manifest on an uncertain semantic rebind.
            release = release_write_lease()
            return {
                "configured": True,
                "saved": False,
                "status": "active-scope-semantic-reuse-read-failed",
                "graphStore": "typedb",
                "aboxSnapshotId": manifest_id,
                "worldviewManifestId": manifest_id,
                "worldId": world_id,
                "preservedActiveGeneration": True,
                "reason": "Active scoped rows required for an exact relation rebind could not be read: "
                + str(error)[:180],
                "activeReuseScopeIds": active_reuse_scope_ids,
                "writeLeaseRelease": release,
            }
        if str(active_scope_rows.get("status") or "") != "ok":
            release = release_write_lease()
            return {
                "configured": True,
                "saved": False,
                "status": "active-scope-semantic-reuse-incomplete",
                "graphStore": "typedb",
                "aboxSnapshotId": manifest_id,
                "worldviewManifestId": manifest_id,
                "worldId": world_id,
                "preservedActiveGeneration": True,
                "reason": str(
                    active_scope_rows.get("reason") or "Active scoped rows are incomplete."
                )[:220],
                "activeReuseScopeIds": active_reuse_scope_ids,
                "failedScopes": list(active_scope_rows.get("failedScopes") or []),
                "writeLeaseRelease": release,
            }
    active_scope_read_ms = round(
        (time.monotonic() - active_scope_read_started) * 1000,
        1,
    )

    exact_candidate_rows: Dict[str, object] = {}
    if current_state_mode:
        current_node_rows, current_relation_rows = _store.graph_persistence_rows(persistence_graph)
        exact_candidate_rows = _store.scoped_abox_candidate_persistence_rows(
            current_node_rows,
            current_relation_rows,
            active_scope_rows,
            scope_plan,
            semantic_changed_scope_ids,
            changed_scope_ids,
            candidate_deferred_scope_ids,
            manifest_id,
        )
        if str(exact_candidate_rows.get("status") or "") != "ok":
            release = release_write_lease()
            candidate_failure = {
                key: exact_candidate_rows.get(key)
                for key in [
                    "status",
                    "reason",
                    "scopeId",
                    "relationType",
                    "source",
                    "target",
                    "endpointRole",
                    "endpointId",
                    "endpointStorageId",
                    "knownEndpointScopeIds",
                    "knownEndpointGenerationIds",
                    "candidateManifestId",
                    "expectedGenerationId",
                    "actualGenerationId",
                    "expectedRelationCount",
                    "currentRelationCount",
                    "failedScopes",
                ]
                if key in exact_candidate_rows
            }
            return {
                "configured": True,
                "saved": False,
                "status": str(
                    exact_candidate_rows.get("status") or "candidate-semantic-reconciliation-failed"
                ),
                "graphStore": "typedb",
                "aboxSnapshotId": manifest_id,
                "worldviewManifestId": manifest_id,
                "worldId": world_id,
                "preservedActiveGeneration": True,
                "reason": str(
                    exact_candidate_rows.get("reason")
                    or "Candidate semantic rows could not be reconciled."
                )[:220],
                "failedScopes": list(exact_candidate_rows.get("failedScopes") or []),
                "rebindOnlyRelationScopeIds": rebind_only_relation_scope_ids,
                "candidateSemanticReconciliationFailure": candidate_failure,
                "writeLeaseRelease": release,
            }

    native_manifest_index_started = time.monotonic()
    native_manifest_index = _store.prepare_scoped_manifest_native_rule_indexes(
        persistence_graph,
        active_before,
        persistence_rows=(
            (
                exact_candidate_rows.get("candidateNodeRows") or [],
                exact_candidate_rows.get("candidateRelationRows") or [],
            )
            if current_state_mode
            else None
        ),
    )
    if str(native_manifest_index.get("status") or "") not in {
        "local-complete",
        "merged",
        "not-required-source-world",
    }:
        release = release_write_lease()
        return {
            "configured": True,
            "saved": False,
            "status": "native-manifest-evidence-index-incomplete",
            "graphStore": "typedb",
            "aboxSnapshotId": manifest_id,
            "worldviewManifestId": manifest_id,
            "worldId": world_id,
            "preservedActiveGeneration": True,
            "reason": (
                "The candidate Manifest did not produce a complete physical evidence index; it was not staged. "
                + str(native_manifest_index.get("reason") or "")[:180]
            ),
            "nativeManifestEvidenceIndex": native_manifest_index,
            "activeManifestEvidenceIndexRepair": active_manifest_index_repair,
            "nativeRuleIndexReuseScopeIds": native_index_reuse_scope_ids,
            "writeLeaseRelease": release,
        }
    if current_state_mode:
        node_rows = list(exact_candidate_rows.get("nodeRows") or [])
        relation_rows = list(exact_candidate_rows.get("relationRows") or [])
    else:
        node_rows, relation_rows = _store.scoped_abox_persistence_rows(
            persistence_graph,
            changed_scope_ids,
        )
    scope_rows = {str(item.get("scopeId") or ""): item for item in scope_plan}
    verification: Dict[str, object] = {}
    timing: Dict[str, object] = {
        "startedAt": _bindings.utc_now(),
        "nativeManifestEvidenceIndex": native_manifest_index,
        "activeManifestEvidenceIndexRepair": active_manifest_index_repair,
        "nativeManifestEvidenceIndexMs": round(
            (time.monotonic() - native_manifest_index_started) * 1000,
            1,
        ),
        "activeScopeSemanticReuseMs": active_scope_read_ms,
        "activeScopeSemanticReuse": {
            "status": str(active_scope_rows.get("status") or ""),
            "scopeIds": list(active_scope_rows.get("scopeIds") or []),
            "relationEndpointScopeIds": active_relation_endpoint_scope_ids,
            "nativeRuleIndexScopeIds": native_index_reuse_scope_ids,
            "nativeRuleIndexScopeCount": len(native_index_reuse_scope_ids),
            "nodeCount": len(active_scope_rows.get("nodeRows") or []),
            "relationCount": len(active_scope_rows.get("relationRows") or []),
            "endpointNodeCount": len(active_scope_rows.get("endpointNodeRows") or []),
        },
        "candidateSemanticReconciliation": {
            key: exact_candidate_rows.get(key)
            for key in [
                "status",
                "semanticChangedScopeIds",
                "physicalChangedScopeIds",
                "rebindOnlyRelationScopeIds",
                "deferredScopeIds",
                "currentFallbackRelationScopeIds",
                "reusedActiveNodeCount",
                "reusedActiveRelationCount",
                "reboundRelationCount",
            ]
            if key in exact_candidate_rows
        },
    }
    save_started_at = time.monotonic()
    imported = _store.driver_imports()
    if imported[0] is None:
        release_write_lease()
        return _store.driver_missing_result(imported[1], graph)
    orphan_cleanup: Dict[str, object] = {
        "status": "deferred",
        "reason": "Orphan scoped ABox candidates are reclaimed by idle maintenance.",
    }
    operation_attempt_count = 0
    try:

        def operation():
            nonlocal operation_attempt_count
            operation_attempt_count += 1
            driver = _store.open_driver(imported)
            try:
                _store.ensure_database(driver)
                _store.ensure_schema(driver, imported)
                timing["orphanCandidateCleanupMs"] = 0.0
                timing["orphanCandidateCleanup"] = dict(orphan_cleanup or {})
                cleanup_started = time.monotonic()
                candidate_generation_ids = {
                    str((scope_rows.get(scope_id) or {}).get("generationId") or "")
                    for scope_id in changed_scope_ids
                    if str((scope_rows.get(scope_id) or {}).get("generationId") or "")
                }
                # Only retry rows created for this exact Manifest. A broad
                # snapshot inventory made a first projection scan all
                # historical ABox generations before it wrote any data.
                # Incomplete candidates from older manifests are handled
                # by the idle orphan-maintenance pass under the same lease.
                timing["stage"] = "candidate-cleanup"
                candidate_cleanup = (
                    {
                        "status": "skipped-fresh-candidate-first-attempt",
                        "deletedBatchCount": 0,
                        "reason": "The manager created this candidate database before the replay process started.",
                    }
                    if fresh_world_bootstrap and operation_attempt_count == 1
                    else _store.delete_box_manifest_rows_in_batches(
                        driver,
                        imported,
                        "ABox",
                        manifest_id,
                        world_id=world_id,
                    )
                )
                timing["operationAttemptCount"] = operation_attempt_count
                timing["candidateScopeGenerationCount"] = len(candidate_generation_ids)
                timing["candidateManifestCleanup"] = candidate_cleanup
                timing["candidateCleanupMs"] = round((time.monotonic() - cleanup_started) * 1000, 1)
                write_started = time.monotonic()
                timing["stage"] = "changed-scope-write"
                write_progress: Dict[str, object] = {}
                timing["changedScopeWriteProgress"] = write_progress
                if current_state_mode:
                    if copy_on_write_mode:
                        # A fresh generation has no rows to compare or
                        # delete. The complete changed scope is written
                        # before the active Manifest can move, and retired
                        # generations are reclaimed by the maintenance
                        # worker after activation.
                        before_inventory = {"nodes": {}, "relations": {}}
                        timing["currentStateInventoryReadMs"] = 0.0
                        delta_plan = _store.current_state_delta_plan(
                            node_rows,
                            relation_rows,
                            before_inventory,
                        )
                        delta_delete = {
                            "status": "deferred-copy-on-write-retention",
                            "deletedIdentityCount": 0,
                            "queryCount": 0,
                            "transactionCount": 0,
                            "durationMs": 0,
                        }
                        timing["currentStateWriteStrategy"] = "copy-on-write-fresh-generation-v4"
                    else:
                        inventory_started = time.monotonic()
                        before_inventory = _store.current_state_slot_inventory(
                            driver,
                            imported,
                            candidate_generation_ids,
                        )
                        timing["currentStateInventoryReadMs"] = round(
                            (time.monotonic() - inventory_started) * 1000,
                            1,
                        )
                        delta_plan = _store.current_state_delta_plan(
                            node_rows,
                            relation_rows,
                            before_inventory,
                        )
                        delta_delete = _store.delete_current_state_storage_ids(
                            driver,
                            imported,
                            delta_plan.get("nodeStorageIdsToDelete") or [],
                            delta_plan.get("relationStorageIdsToDelete") or [],
                        )
                        timing["currentStateWriteStrategy"] = "legacy-dual-slot-delta-v1"
                    write_plan = _store.write_persistence_rows(
                        driver,
                        imported,
                        delta_plan.get("nodeRowsToInsert") or [],
                        delta_plan.get("relationRowsToInsert") or [],
                        telemetry=write_progress,
                        assume_missing_storage=True,
                    )
                    expected_counts = _store.scoped_abox_counts_by_scope(
                        delta_plan.get("nodeRows") or [],
                        delta_plan.get("relationRows") or [],
                    )
                    reused_counts = _store.scoped_abox_counts_by_scope(
                        delta_plan.get("reusedNodeRows") or [],
                        delta_plan.get("reusedRelationRows") or [],
                    )
                    write_plan.update(
                        {
                            "requestedNodeCount": len(delta_plan.get("nodeRows") or []),
                            "requestedRelationCount": len(delta_plan.get("relationRows") or []),
                            "insertedNodeCount": len(delta_plan.get("nodeRowsToInsert") or []),
                            "insertedRelationCount": len(
                                delta_plan.get("relationRowsToInsert") or []
                            ),
                            "reusedNodeCount": len(delta_plan.get("reusedNodeRows") or []),
                            "reusedRelationCount": len(delta_plan.get("reusedRelationRows") or []),
                            "expectedCountsByScope": expected_counts,
                            "reusedCountsByScope": reused_counts,
                            "physicalStateMode": current_state_persistence_mode,
                            "deltaDelete": delta_delete,
                            "changedNodeScopeIds": list(
                                delta_plan.get("changedNodeScopeIds") or []
                            ),
                            "requestedRelationBreakdown": _store.scoped_abox_relation_breakdown(
                                delta_plan.get("relationRows") or []
                            ),
                            "insertedRelationBreakdown": _store.scoped_abox_relation_breakdown(
                                delta_plan.get("relationRowsToInsert") or []
                            ),
                            "reusedRelationBreakdown": _store.scoped_abox_relation_breakdown(
                                delta_plan.get("reusedRelationRows") or []
                            ),
                        }
                    )
                    timing["currentStateDeltaPlan"] = {
                        "requestedNodeCount": write_plan["requestedNodeCount"],
                        "requestedRelationCount": write_plan["requestedRelationCount"],
                        "insertedNodeCount": write_plan["insertedNodeCount"],
                        "insertedRelationCount": write_plan["insertedRelationCount"],
                        "reusedNodeCount": write_plan["reusedNodeCount"],
                        "reusedRelationCount": write_plan["reusedRelationCount"],
                        "deletedIdentityCount": int(delta_delete.get("deletedIdentityCount") or 0),
                    }
                else:
                    write_plan = _store.write_persistence_rows(
                        driver,
                        imported,
                        node_rows,
                        relation_rows,
                        telemetry=write_progress,
                        assume_missing_storage=fresh_world_bootstrap,
                    )
                timing["changedScopeWritePlan"] = write_plan
                timing["changedScopeWriteMs"] = round((time.monotonic() - write_started) * 1000, 1)
                verification_started = time.monotonic()
                timing["stage"] = "changed-scope-verification"
                # The write plan has already verified every reused physical
                # row by storage identity. Re-reading every one after a
                # successful commit doubled the largest live projection
                # read. Two grouped counts verify newly inserted rows by
                # exact manifest/scope/generation instead.
                changed_scope_rows = [
                    scope_rows.get(scope_id) or {} for scope_id in changed_scope_ids
                ]
                expected_counts_by_scope = dict(write_plan.get("expectedCountsByScope") or {})
                reused_counts_by_scope = dict(write_plan.get("reusedCountsByScope") or {})
                if current_state_mode and not copy_on_write_mode:
                    node_storage_ids_to_delete = set(delta_plan.get("nodeStorageIdsToDelete") or [])
                    relation_storage_ids_to_delete = set(
                        delta_plan.get("relationStorageIdsToDelete") or []
                    )
                    inserted_node_rows = list(delta_plan.get("nodeRowsToInsert") or [])
                    inserted_relation_rows = list(delta_plan.get("relationRowsToInsert") or [])
                    inserted_node_storage_ids = [
                        ontology_storage_id(row, row.get("id"), "node")
                        for row in inserted_node_rows
                    ]
                    inserted_relation_storage_ids = [
                        ontology_storage_id(
                            row,
                            relation_row_id(row),
                            "relation",
                        )
                        for row in inserted_relation_rows
                    ]
                    post_write_started = time.monotonic()
                    inserted_inventory = _store.current_state_storage_inventory(
                        driver,
                        imported,
                        inserted_node_storage_ids,
                        inserted_relation_storage_ids,
                    )
                    timing["currentStatePostWriteVerificationMs"] = round(
                        (time.monotonic() - post_write_started) * 1000,
                        1,
                    )
                    post_inventory = {
                        "nodes": {
                            storage_id: item
                            for storage_id, item in dict(
                                before_inventory.get("nodes") or {}
                            ).items()
                            if storage_id not in node_storage_ids_to_delete
                        },
                        "relations": {
                            storage_id: item
                            for storage_id, item in dict(
                                before_inventory.get("relations") or {}
                            ).items()
                            if storage_id not in relation_storage_ids_to_delete
                        },
                    }
                    post_inventory["nodes"].update(inserted_inventory.get("nodes") or {})
                    post_inventory["relations"].update(inserted_inventory.get("relations") or {})
                    actual_counts_by_scope: Dict[str, Dict[str, int]] = {}
                    for key, count_key in [
                        ("nodes", "entityCount"),
                        ("relations", "relationCount"),
                    ]:
                        for item in dict(post_inventory.get(key) or {}).values():
                            scope_id = str(item.get("scopeId") or "")
                            actual_counts_by_scope.setdefault(
                                scope_id,
                                {"entityCount": 0, "relationCount": 0},
                            )[count_key] += 1
                    desired_node_rows = list(delta_plan.get("nodeRows") or [])
                    desired_relation_rows = list(delta_plan.get("relationRows") or [])
                    desired_fingerprints = {
                        ontology_storage_id(row, row.get("id"), "node"): str(
                            row.get("contentFingerprint")
                            or ontology_row_content_fingerprint(row, "node")
                        )
                        for row in desired_node_rows
                    }
                    desired_fingerprints.update(
                        {
                            ontology_storage_id(
                                row,
                                relation_row_id(row),
                                "relation",
                            ): str(
                                row.get("contentFingerprint")
                                or ontology_row_content_fingerprint(row, "relation")
                            )
                            for row in desired_relation_rows
                        }
                    )
                    actual_fingerprints = {
                        storage_id: str(item.get("contentFingerprint") or "")
                        for key in ["nodes", "relations"]
                        for storage_id, item in dict(post_inventory.get(key) or {}).items()
                    }
                    missing_storage_ids = [
                        storage_id
                        for storage_id in desired_fingerprints
                        if storage_id not in actual_fingerprints
                    ]
                    stale_storage_ids = [
                        storage_id
                        for storage_id, fingerprint in desired_fingerprints.items()
                        if storage_id in actual_fingerprints
                        and actual_fingerprints.get(storage_id) != fingerprint
                    ]
                    missing_or_stale = [
                        *missing_storage_ids,
                        *stale_storage_ids,
                    ]
                    if missing_or_stale:
                        raise RuntimeError(
                            "Current-state ABox delta verification failed for "
                            + str(len(missing_or_stale))
                            + " physical facts"
                            + " missing="
                            + str(len(missing_storage_ids))
                            + " stale="
                            + str(len(stale_storage_ids))
                            + " sample="
                            + ",".join(missing_or_stale[:5])
                            + "."
                        )
                else:
                    inserted_counts_by_scope = _store.scoped_abox_scope_row_counts_batch(
                        changed_scope_rows,
                        manifest_id=manifest_id,
                        world_id=world_id,
                    )
                    actual_counts_by_scope = _store.merged_scoped_abox_counts(
                        inserted_counts_by_scope,
                        reused_counts_by_scope,
                    )
                timing["changedScopeStorageIdentityVerification"] = {
                    "status": "ok",
                    "mode": (
                        "current-state-delta-exact-write-verification"
                        if current_state_mode and not copy_on_write_mode
                        else (
                            "copy-on-write-manifest-scope-count"
                            if copy_on_write_mode
                            else "manifest-scope-count"
                        )
                    ),
                    "manifestScopedReadCount": (
                        0 if current_state_mode and not copy_on_write_mode else 2
                    ),
                    "reusedStorageIdentityCount": (
                        int(write_plan.get("reusedNodeCount") or 0)
                        + int(write_plan.get("reusedRelationCount") or 0)
                    ),
                    "conflictCount": 0,
                }
                for scope_id in changed_scope_ids:
                    scope_plan_row = scope_rows.get(scope_id) or {}
                    expected = expected_counts_by_scope.get(scope_id) or {}
                    generation_id = str(scope_plan_row.get("generationId") or "")
                    actual = actual_counts_by_scope.get(scope_id) or {
                        "entityCount": 0,
                        "relationCount": 0,
                    }
                    valid = actual.get("entityCount") == int(
                        expected.get("entityCount") or 0
                    ) and actual.get("relationCount") == int(expected.get("relationCount") or 0)
                    verification[scope_id] = {
                        "status": "ok" if valid else "incomplete",
                        "generationId": generation_id,
                        "expectedEntityCount": int(expected.get("entityCount") or 0),
                        "expectedRelationCount": int(expected.get("relationCount") or 0),
                        "actualEntityCount": int(actual.get("entityCount") or 0),
                        "actualRelationCount": int(actual.get("relationCount") or 0),
                    }
                timing["changedScopeVerificationMs"] = round(
                    (time.monotonic() - verification_started) * 1000, 1
                )
                failed = [
                    scope_id
                    for scope_id, item in verification.items()
                    if str(item.get("status") or "") != "ok"
                ]
                if failed:
                    raise RuntimeError(
                        "Scoped ABox candidate verification failed for " + ", ".join(failed)
                    )
                marker_graph = _store.scoped_manifest_marker_graph(
                    persistence_graph,
                    scope_plan,
                    changed_scope_ids,
                )
                if not marker_graph.entities:
                    raise RuntimeError("Scoped ABox candidate has no Manifest marker.")
                pending_graph = _store.scoped_manifest_pending_graph(
                    persistence_graph,
                    scope_plan,
                    active_before,
                    inference_target_symbols=inference_target_symbols,
                )
                if not pending_graph.entities:
                    raise RuntimeError("Scoped ABox candidate has no activation journal.")
                staged_targets = clean_symbols_from_payload(
                    pending_graph.entities[0].properties.get("targetSymbols") or []
                )
                if staged_targets != inference_target_symbols:
                    raise RuntimeError(
                        "Scoped ABox activation journal target symbols do not match the requested inference scope."
                    )
                # The immutable candidate marker and its activation
                # journal are both control facts for this exact staged
                # generation. Persist them in one transaction after the
                # physical scope rows verify. This removes one TypeDB
                # round trip without moving the active pointer.
                control_write_started = time.monotonic()
                timing["stage"] = "manifest-control-write"
                control_graph = PortfolioOntology(
                    str(graph.portfolio_id or "typedb-scoped-control"),
                    entities=[*marker_graph.entities, *pending_graph.entities],
                )
                _store.write_graph(driver, imported, control_graph, delete_boxes=[])
                timing["manifestControlWriteMs"] = round(
                    (time.monotonic() - control_write_started) * 1000,
                    1,
                )
                timing["manifestControlEntityCount"] = len(control_graph.entities)
                timing["stage"] = "candidate-staged"
            finally:
                _store.close_driver(driver)

        _store.with_scoped_abox_candidate_verification_retry(
            operation,
            timing=timing,
            verification=verification,
        )
        active_after = _store.active_abox_metadata(world_id)
        pending_after = _store.pending_abox_activation(world_id)
        if (
            str(pending_after.get("status") or "") != "pending"
            or str(pending_after.get("candidateAboxSnapshotId") or "") != manifest_id
            or clean_symbols_from_payload(pending_after.get("targetSymbols") or [])
            != inference_target_symbols
        ):
            raise RuntimeError(
                "Scoped ABox activation journal verification failed after candidate staging."
            )
        timing["totalMs"] = round((time.monotonic() - save_started_at) * 1000, 1)
        activation_status = "staged" if previous_manifest_id != manifest_id else "unchanged"
        release = release_write_lease()
        relation_persistence = _store.scoped_abox_relation_persistence_summary(
            dict(timing.get("changedScopeWritePlan") or {}),
        )
        # ``typedbFreshCandidateRebuild`` is a provisioning hint, not a
        # permanent runtime mode. Once any scoped Manifest has been
        # staged, later target patches must merge with the active
        # topology and physical evidence index.
        _store._fresh_candidate_rebuild = False
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "entityCount": len(node_rows),
            "relationCount": len(relation_rows),
            "aboxSnapshotId": manifest_id,
            "worldviewManifestId": manifest_id,
            "worldId": world_id,
            "worldType": str(worldview.get("worldType") or ""),
            "tenantId": str(worldview.get("tenantId") or ""),
            "accountId": str(worldview.get("accountId") or graph.portfolio_id or ""),
            "scopePlan": scope_plan,
            "logicalScopePlan": logical_scope_plan if current_state_mode else scope_plan,
            "changedScopeIds": changed_scope_ids,
            "physicalStateMode": (
                current_state_persistence_mode
                if current_state_mode
                else SCOPED_ABOX_PERSISTENCE_MODE
            ),
            "scopeTopologyVersion": str(worldview.get("scopeTopologyVersion") or ""),
            "scopeTopologyMigration": topology_migration,
            "boundedScopeCount": len(
                [
                    item
                    for item in scope_plan
                    if ":bucket:" in str(item.get("scopeId") or "")
                    or ":window:" in str(item.get("scopeId") or "")
                ]
            ),
            "changedBoundedScopeCount": len(
                [
                    scope_id
                    for scope_id in changed_scope_ids
                    if ":bucket:" in scope_id or ":window:" in scope_id
                ]
            ),
            "pendingAboxActivation": pending_after,
            "changedScopeEntityCount": len(node_rows),
            "changedScopeRelationCount": len(relation_rows),
            "relationPersistence": relation_persistence,
            "orphanCandidateCleanup": orphan_cleanup,
            "writeLease": {
                key: value
                for key, value in dict(write_lease or {}).items()
                if key != "propertiesJson"
            },
            "writeLeaseRelease": release,
            "aboxPersistenceVerification": {
                "status": "ok",
                "persistenceMode": (
                    current_state_persistence_mode
                    if current_state_mode
                    else SCOPED_ABOX_PERSISTENCE_MODE
                ),
                "scopeVerification": verification,
                "activePointer": active_after,
                "activation": {
                    "status": activation_status,
                    "snapshotId": manifest_id,
                    "previousSnapshotId": previous_manifest_id,
                    "atomic": True,
                    "activationRequired": activation_status == "staged",
                    "finalizationRequired": activation_status == "staged",
                },
                "timing": timing,
            },
        }
    except Exception as error:  # noqa: BLE001 - preserve the prior Manifest on candidate failure.
        failed_candidate_cleanup: Dict[str, object] = {
            "status": "deferred",
            "reason": "Failed scoped candidate cleanup is deferred to idle maintenance.",
        }
        release = release_write_lease()
        relation_persistence = _store.scoped_abox_relation_persistence_summary(
            dict(timing.get("changedScopeWritePlan") or {}),
        )
        reason_code = _bindings.typedb_error_code(error)
        failed_scope_verification = {
            scope_id: dict(value or {})
            for scope_id, value in verification.items()
            if str((value or {}).get("status") or "") != "ok"
        }
        return {
            "configured": True,
            "saved": False,
            "status": "error",
            "graphStore": "typedb",
            "aboxSnapshotId": manifest_id,
            "worldviewManifestId": manifest_id,
            "worldId": world_id,
            "worldType": str(worldview.get("worldType") or ""),
            "tenantId": str(worldview.get("tenantId") or ""),
            "accountId": str(worldview.get("accountId") or graph.portfolio_id or ""),
            "changedScopeIds": changed_scope_ids,
            "scopePlan": scope_plan,
            "logicalScopePlan": logical_scope_plan if current_state_mode else scope_plan,
            "scopeTopologyVersion": str(worldview.get("scopeTopologyVersion") or ""),
            "scopeTopologyMigration": topology_migration,
            "preservedActiveGeneration": bool(previous_manifest_id),
            "physicalStateMode": (
                current_state_persistence_mode
                if current_state_mode
                else SCOPED_ABOX_PERSISTENCE_MODE
            ),
            "reasonCode": reason_code,
            "reason": str(error)[:220],
            "retryable": reason_code
            in {
                "typedbConnectionError",
                "typedbTimeout",
            },
            "scopeVerification": verification,
            "candidateVerificationFailure": {
                "status": "failed" if failed_scope_verification else "not-applicable",
                "failedScopeIds": sorted(failed_scope_verification),
                "failedScopes": failed_scope_verification,
                "retryAttempted": bool(timing.get("candidateVerificationRetryAttempted")),
                "retryCount": int(timing.get("candidateVerificationRetryCount") or 0),
                "retryDeferred": bool(timing.get("candidateVerificationRetryDeferred")),
                "operationAttemptCount": operation_attempt_count,
            },
            "timing": timing,
            "relationPersistence": relation_persistence,
            "orphanCandidateCleanup": orphan_cleanup,
            "failedCandidateCleanup": failed_candidate_cleanup,
            "writeLease": {
                key: value
                for key, value in dict(write_lease or {}).items()
                if key != "propertiesJson"
            },
            "writeLeaseRelease": release,
        }
