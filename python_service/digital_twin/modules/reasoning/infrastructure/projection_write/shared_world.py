"""Shared world implementation; facade-independent dependencies."""

from __future__ import annotations
from .shared_world_ports import SharedWorldPort
from digital_twin.modules.reasoning.domain.knowledge_world_projection import knowledge_world_coverage
from digital_twin.modules.reasoning.domain.market_world_projection import market_scope_plan_with_observation_times, market_world_coverage, merge_market_world_scope_manifest
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_native_rule_planning import merge_native_rule_planner_topology, native_rule_planner_manifest_fingerprint, native_rule_planner_topology
from digital_twin.modules.reasoning.domain.ontology_projection_fingerprint import active_material_fingerprint, apply_material_graph_identity, material_graph_fingerprint
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION, apply_scoped_abox_identity, apply_scoped_abox_repair_epochs, apply_scoped_manifest_plan, plan_target_scoped_manifest_patch, scoped_manifest_id
from digital_twin.modules.reasoning.domain.ontology_validator import validate_ontology
from digital_twin.modules.reasoning.domain.ontology_worlds import world_metadata
from typing import Dict


def project_shared_world_update(
    _store: SharedWorldPort,
    update: PortfolioOntology,
    shared_world,
    projection_kind: str = "market",
) -> Dict[str, object]:
    """Persist one account-independent shared-world update.

    Market observations and durable knowledge facts can arrive from any
    account cycle.  The active scoped Manifest merges only the changed
    shareable slice, preserving facts contributed by other accounts.  The
    MarketWorld and KnowledgeWorld have no account-specific RuleBox output
    and can be activated directly after ontology validation. A
    SharedPremiseWorld is different: its candidate Manifest remains staged
    until native rules produce an aligned InferenceBox generation.
    """
    kind = str(projection_kind or "market").strip().lower()
    if kind not in {"market", "knowledge", "premise"}:
        kind = "market"
    world_label = {
        "knowledge": "KnowledgeWorld",
        "premise": "SharedPremiseWorld",
    }.get(kind, "MarketWorld")
    status_prefix = kind + "-world"
    shared_contract_version = str(
        (update.worldview or {}).get("sharedWorldProjectionContractVersion") or ""
    ).strip()
    if _store.active_graph_store_key() != "typedb":
        return {
            **world_metadata(shared_world),
            "status": "skipped-non-typedb-store",
            "projectionKind": kind,
            "reason": "Shared "
            + world_label
            + " is enabled on the TypeDB ontology adapter.",
        }
    metadata_reader = getattr(_store.repository, "active_abox_metadata", None)
    scoped_saver = getattr(_store.repository, "save_scoped_abox_graph", None)
    if not callable(metadata_reader) or not callable(scoped_saver):
        return {
            **world_metadata(shared_world),
            "status": "deferred-adapter-not-scoped-" + status_prefix,
            "projectionKind": kind,
            "reason": "The active graph adapter cannot update a shared "
            + world_label
            + " through scoped Manifests yet.",
        }
    coordinator_lease = _store.acquire_projection_coordinator_lease(
        kind + "-world-merge",
        shared_world.world_id,
    )
    if not bool(coordinator_lease.get("acquired")):
        return {
            **world_metadata(shared_world),
            "status": "deferred-projection-coordinator",
            "preservedActiveGeneration": True,
            "projectionKind": kind,
            "retryable": True,
            "recommendedRetryAfterSeconds": int(
                coordinator_lease.get("recommendedRetryAfterSeconds") or 10
            ),
            "projectionCoordinator": _store.projection_coordinator_summary(
                coordinator_lease
            ),
            "reason": str(
                coordinator_lease.get("reason")
                or "다른 TypeDB World 투영이 데이터베이스 쓰기 경계를 사용 중입니다."
            )[:220],
        }
    merge_lease: Dict[str, object] = {}
    acquire_lease = getattr(_store.repository, "acquire_scoped_abox_write_lease", None)
    release_lease = getattr(_store.repository, "release_scoped_abox_write_lease", None)
    if callable(acquire_lease) and callable(release_lease) and callable(scoped_saver):
        try:
            merge_lease = _store.repository_world_call(
                "acquire_scoped_abox_write_lease",
                kind + "-world-merge",
                world_id=shared_world.world_id,
            )
        except (
            Exception
        ) as error:  # noqa: BLE001 - the portfolio world must remain independently usable.
            coordinator_release = _store.release_projection_coordinator_lease(
                coordinator_lease
            )
            return {
                **world_metadata(shared_world),
                "status": "deferred-" + status_prefix + "-write-lease",
                "projectionKind": kind,
                "reason": "Shared "
                + world_label
                + " write lease lookup failed: "
                + str(error)[:180],
                "projectionCoordinatorRelease": coordinator_release,
            }
        if not bool(merge_lease.get("acquired")):
            coordinator_release = _store.release_projection_coordinator_lease(
                coordinator_lease
            )
            return {
                **world_metadata(shared_world),
                "status": "deferred-" + status_prefix + "-write-lease",
                "preservedActiveGeneration": True,
                "projectionKind": kind,
                "reason": "Another account is merging or activating the shared "
                + world_label
                + ".",
                "writeLease": {
                    key: value
                    for key, value in dict(merge_lease or {}).items()
                    if key != "propertiesJson"
                },
                "projectionCoordinatorRelease": coordinator_release,
            }
    try:
        pending_activation_recovery: Dict[str, object] = {}
        if kind == "premise":
            pending_activation_recovery = _store.recover_pending_abox_activation(
                shared_world.world_id,
            )
            recovery_status = str(
                pending_activation_recovery.get("status") or "skipped"
            )
            if recovery_status not in {
                "skipped",
                "disabled",
                "finalized",
                "finalized-empty-target",
                "restored",
                "cleared-stale",
                "discarded-staged-shared-premise",
            }:
                return {
                    **world_metadata(shared_world),
                    "status": "deferred-premise-world-activation-recovery",
                    "saved": False,
                    "preservedActiveGeneration": True,
                    "projectionKind": kind,
                    "retryable": True,
                    "recommendedRetryAfterSeconds": int(
                        pending_activation_recovery.get("recommendedRetryAfterSeconds")
                        or 10
                    ),
                    "pendingAboxActivationRecovery": pending_activation_recovery,
                    "reason": str(
                        pending_activation_recovery.get("reason")
                        or "SharedPremiseWorld activation recovery must complete before a new projection."
                    )[:220],
                }
        else:
            pending_reader = getattr(_store.repository, "pending_abox_activation", None)
            pending = (
                _store.repository_world_call(
                    "pending_abox_activation",
                    world_id=shared_world.world_id,
                )
                if callable(pending_reader)
                else {"status": "empty"}
            )
            pending = (
                dict(pending or {})
                if isinstance(pending, dict)
                else {"status": "error"}
            )
            pending_status = str(pending.get("status") or "empty")
            if pending_status == "pending":
                candidate_id = str(pending.get("candidateAboxSnapshotId") or "").strip()
                activation_status = str(pending.get("activationStatus") or "").strip()
                activator = getattr(
                    _store.repository, "activate_scoped_abox_manifest", None
                )
                if (
                    not candidate_id
                    or activation_status != "staged-native-inference"
                    or not callable(activator)
                ):
                    return {
                        **world_metadata(shared_world),
                        "status": "deferred-" + status_prefix + "-activation-recovery",
                        "saved": False,
                        "preservedActiveGeneration": True,
                        "projectionKind": kind,
                        "retryable": True,
                        "recommendedRetryAfterSeconds": 10,
                        "pendingAboxActivationRecovery": pending,
                        "reason": "완료되지 않은 공용 월드 활성화 상태를 안전하게 복구할 수 없습니다.",
                    }
                recovered_activation = _store.repository_world_call(
                    "activate_scoped_abox_manifest",
                    candidate_id,
                    pending_activation=False,
                    world_id=shared_world.world_id,
                )
                recovered_activation = (
                    dict(recovered_activation or {})
                    if isinstance(recovered_activation, dict)
                    else {"status": "error"}
                )
                if str(recovered_activation.get("status") or "") != "ok":
                    return {
                        **world_metadata(shared_world),
                        "status": "deferred-" + status_prefix + "-activation-recovery",
                        "saved": False,
                        "preservedActiveGeneration": True,
                        "projectionKind": kind,
                        "retryable": True,
                        "recommendedRetryAfterSeconds": 10,
                        "pendingAboxActivationRecovery": pending,
                        "activation": recovered_activation,
                        "reason": str(
                            recovered_activation.get("reason")
                            or "공용 월드의 중단된 활성화를 완료하지 못했습니다."
                        )[:220],
                    }
                pending_activation_recovery = {
                    "status": "finalized",
                    "recoveryMode": "activate-interrupted-shared-world-manifest",
                    "candidateAboxSnapshotId": candidate_id,
                    "activation": recovered_activation,
                }
            elif pending_status == "empty":
                pending_activation_recovery = {"status": "skipped"}
            else:
                return {
                    **world_metadata(shared_world),
                    "status": "deferred-" + status_prefix + "-activation-recovery",
                    "saved": False,
                    "preservedActiveGeneration": True,
                    "projectionKind": kind,
                    "retryable": True,
                    "recommendedRetryAfterSeconds": 10,
                    "pendingAboxActivationRecovery": pending,
                    "reason": str(
                        pending.get("reason")
                        or "공용 월드의 중단된 활성화 상태를 읽지 못했습니다."
                    )[:220],
                }
        observed_at = str(
            (update.worldview or {}).get("sourceObservedAt")
            or (update.worldview or {}).get("marketObservedAt")
            or (update.worldview or {}).get("asOf")
            or ""
        )
        try:
            active_market = _store.repository_world_call(
                "active_abox_metadata",
                world_id=shared_world.world_id,
            )
        except (
            Exception
        ) as error:  # noqa: BLE001 - a shared read must never hold a portfolio projection.
            return {
                **world_metadata(shared_world),
                "status": "deferred-" + status_prefix + "-metadata",
                "preservedActiveGeneration": True,
                "projectionKind": kind,
                "reason": "Shared "
                + world_label
                + " Manifest could not be read: "
                + str(error)[:180],
            }
        active_market = (
            dict(active_market or {}) if isinstance(active_market, dict) else {}
        )
        active_status = str(active_market.get("status") or "empty").strip().lower()
        if active_status not in {"ok", "empty"}:
            return {
                **world_metadata(shared_world),
                "status": "deferred-" + status_prefix + "-metadata",
                "preservedActiveGeneration": True,
                "projectionKind": kind,
                "reason": str(
                    active_market.get("reason")
                    or "Shared " + world_label + " Manifest is not complete."
                )[:220],
            }
        if (
            active_status == "ok"
            and str(active_market.get("scopedAboxManifestVersion") or "")
            != SCOPED_ABOX_MANIFEST_VERSION
        ):
            return {
                **world_metadata(shared_world),
                "status": "deferred-" + status_prefix + "-legacy-manifest",
                "preservedActiveGeneration": True,
                "projectionKind": kind,
                "reason": "Shared "
                + world_label
                + " must be migrated to a scoped Manifest before incremental updates can preserve every active scope.",
            }
        active_contract_version = str(
            active_market.get("sharedWorldProjectionContractVersion") or ""
        ).strip()
        full_contract_rebuild = bool(
            active_status == "ok"
            and shared_contract_version
            and active_contract_version != shared_contract_version
        )
        update.worldview.update(
            {
                **world_metadata(shared_world),
                "sharedWorldProjection": kind,
                "marketWorldProjection": kind == "market",
                "knowledgeWorldProjection": kind == "knowledge",
                "sharedPremiseWorldProjection": kind == "premise",
                "marketContextMode": "shared-" + kind + "-world-direct-premises",
                "sharedWorldProjection": kind,
                "sharedWorldProjectionContractVersion": shared_contract_version,
                "sharedWorldFullRebuild": full_contract_rebuild,
            }
        )
        incoming_planner_topology = {}
        if kind == "premise":
            incoming_planner_topology = native_rule_planner_topology(update)
            update.worldview["nativeRulePlannerTopology"] = incoming_planner_topology
        incoming_fingerprint = material_graph_fingerprint(update)
        apply_material_graph_identity(
            update,
            shared_world.world_id,
            incoming_fingerprint,
            world_id=shared_world.world_id,
        )
        scoped = apply_scoped_abox_identity(
            update,
            shared_world.world_id,
            world_id=shared_world.world_id,
            tenant_id=shared_world.tenant_id,
            world_type=shared_world.world_type,
            world_account_id="",
        )
        incoming_scope_plan = market_scope_plan_with_observation_times(
            update,
            scoped.get("scopePlan") or [],
        )
        # Source observation clocks are manifest metadata, not material
        # facts. They preserve retention/freshness without creating a new
        # generation for every successful polling cycle.
        scoped["scopePlan"] = incoming_scope_plan
        update.worldview["scopePlan"] = incoming_scope_plan
        scope_repair = apply_scoped_abox_repair_epochs(
            update,
            active_market,
            (update.worldview or {}).get("scopeRepairRequestsBySymbol") or {},
        )
        if scope_repair.get("applied"):
            incoming_scope_plan = market_scope_plan_with_observation_times(
                update,
                (update.worldview or {}).get("scopePlan") or incoming_scope_plan,
            )
            update.worldview["scopePlan"] = incoming_scope_plan
        market_target_patch = {
            "status": (
                "full-contract-rebuild" if full_contract_rebuild else "full-manifest"
            ),
            "selectedIncomingScopeCount": len(incoming_scope_plan),
        }
        source_patch = dict(
            (update.worldview or {}).get("targetScopedManifestPatch") or {}
        )
        target_symbols = list(source_patch.get("targetSymbols") or [])
        if kind == "premise":
            update.worldview["inferenceTargetSymbols"] = list(target_symbols)
        if str(source_patch.get("status") or "") == "applied" and target_symbols:
            selection = plan_target_scoped_manifest_patch(
                update,
                active_market,
                target_symbols,
            )
            if selection.get("applied"):
                incoming_scope_plan = list(
                    selection.get("selectedIncomingScopePlan") or []
                )
                market_target_patch = {
                    "status": "applied",
                    "mode": "incremental-target-scoped-manifest-patch",
                    "targetSymbols": list(selection.get("targetSymbols") or []),
                    "replacementSymbols": list(
                        selection.get("replacementSymbols") or []
                    ),
                    "replacementRootScopeIds": list(
                        selection.get("replacementRootScopeIds") or []
                    ),
                    "selectedIncomingScopeCount": len(
                        selection.get("selectedIncomingScopeIds") or []
                    ),
                    "reusedActiveScopeCount": len(
                        selection.get("reusedActiveScopeIds") or []
                    ),
                    "deferredScopeCount": len(selection.get("deferredScopeIds") or []),
                    "manifestPatchContract": dict(
                        selection.get("manifestPatchContract") or {}
                    ),
                }
            else:
                market_target_patch = {
                    "status": str(selection.get("status") or "full-manifest-fallback"),
                    "mode": "full-manifest-fallback",
                    "targetSymbols": target_symbols,
                    "selectedIncomingScopeCount": len(incoming_scope_plan),
                }
        manifest_state = merge_market_world_scope_manifest(
            {} if full_contract_rebuild else active_market,
            incoming_scope_plan,
            observed_at=observed_at,
            retention_hours=_store.shared_world_retention_hours(kind),
            max_symbols=_store.shared_market_world_symbol_limit(),
        )
        scope_generations = dict(manifest_state.get("scopeGenerationIds") or {})
        if not scope_generations:
            return {
                **world_metadata(shared_world),
                "status": "skipped-empty-" + status_prefix + "-patch",
                "projectionKind": kind,
                "reason": "No shareable "
                + kind
                + " fact scope was produced by this portfolio observation.",
            }
        manifest_id = scoped_manifest_id(
            shared_world.world_id,
            scope_generations,
            world_id=shared_world.world_id,
        )
        fingerprint = str(
            manifest_state.get("materialFingerprint") or incoming_fingerprint
        )
        if kind == "premise":
            planner_topology = incoming_planner_topology
            if (
                active_status == "ok"
                and not full_contract_rebuild
                and str(market_target_patch.get("status") or "") == "applied"
            ):
                topology_merge = merge_native_rule_planner_topology(
                    dict(active_market.get("nativeRulePlannerTopology") or {}),
                    incoming_planner_topology,
                    (
                        market_target_patch.get("replacementSymbols")
                        if "replacementSymbols" in market_target_patch
                        else market_target_patch.get("targetSymbols") or []
                    ),
                )
                if str(topology_merge.get("status") or "") != "ok":
                    return {
                        **world_metadata(shared_world),
                        "status": "deferred-premise-world-planner-topology",
                        "saved": False,
                        "preservedActiveGeneration": True,
                        "projectionKind": kind,
                        "retryable": True,
                        "recommendedRetryAfterSeconds": 10,
                        "reason": str(
                            topology_merge.get("reason")
                            or "SharedPremiseWorld planner topology could not be merged."
                        )[:220],
                        "nativeRulePlannerTopologyMerge": topology_merge,
                    }
                planner_topology = dict(topology_merge.get("topology") or {})
                update.worldview["nativeRulePlannerTopologyIncoming"] = (
                    incoming_planner_topology
                )
                update.worldview["nativeRulePlannerTopologyMerge"] = {
                    key: topology_merge.get(key)
                    for key in [
                        "status",
                        "reason",
                        "replacedSymbols",
                        "retainedSymbols",
                        "activeSymbolCount",
                        "incomingSymbolCount",
                        "mergedSymbolCount",
                    ]
                }
            update.worldview["nativeRulePlannerTopology"] = planner_topology
            fingerprint = native_rule_planner_manifest_fingerprint(
                fingerprint,
                planner_topology,
            )
        # A selected link can still point to an untouched active market
        # fact. Rebind every in-memory endpoint to the merged manifest so
        # TypeDB writes the link against that active generation rather than
        # an intentionally deferred source generation.
        bound_manifest = apply_scoped_manifest_plan(
            update,
            manifest_state.get("scopePlan") or [],
            account_id=shared_world.world_id,
            world_id=shared_world.world_id,
            material_fingerprint=fingerprint,
        )
        manifest_id = str(bound_manifest.get("manifestId") or manifest_id)
        if (
            active_status == "ok"
            and not full_contract_rebuild
            and active_material_fingerprint(active_market) == fingerprint
        ):
            observation_refresh = {}
            refreshed_scope_ids = list(
                manifest_state.get("observationRefreshedScopeIds") or []
            )
            refresher = (
                getattr(
                    _store.repository, "refresh_market_world_observation_metadata", None
                )
                if kind == "market"
                else None
            )
            if refreshed_scope_ids and callable(refresher):
                try:
                    observation_refresh = _store.repository_world_call(
                        "refresh_market_world_observation_metadata",
                        manifest_id,
                        list(manifest_state.get("scopePlan") or []),
                        dict(manifest_state.get("marketScopeObservedAt") or {}),
                        adopted_write_lease=merge_lease,
                        world_id=shared_world.world_id,
                    )
                except (
                    Exception
                ) as error:  # noqa: BLE001 - retain the active facts when the metadata heartbeat fails.
                    observation_refresh = {
                        "status": "error",
                        "reason": str(error)[:180],
                    }
                if str(observation_refresh.get("status") or "") != "ok":
                    return {
                        **world_metadata(shared_world),
                        "status": "deferred-" + status_prefix + "-observation-metadata",
                        "saved": False,
                        "preservedActiveGeneration": True,
                        "projectionKind": kind,
                        "materialFingerprint": fingerprint,
                        "worldviewManifestId": str(
                            active_market.get("aboxSnapshotId") or manifest_id
                        ),
                        "projectionMode": "incremental-scoped-manifest-reuse",
                        "activeScopeCount": int(
                            manifest_state.get("activeScopeCount") or 0
                        ),
                        "activeSymbolCount": int(
                            manifest_state.get("activeSymbolCount") or 0
                        ),
                        "observationRefreshedScopeIds": refreshed_scope_ids,
                        "observationMetadata": observation_refresh,
                        "targetScopedManifestPatch": market_target_patch,
                        "reason": "공용 "
                        + ("시장" if kind == "market" else "지식")
                        + " 사실은 같지만 소스 관측 시각을 안전하게 갱신하지 못했습니다.",
                    }
            # A portfolio inference retry must not rewrite the shared
            # market generation when this account contributed no new
            # market facts. The portfolio ABox has a separate lifecycle,
            # so preserving this already active MarketWorld cannot hide a
            # candidate portfolio failure or a data freshness change.
            return {
                **world_metadata(shared_world),
                "status": "unchanged-material-facts",
                "saved": False,
                "preservedActiveGeneration": True,
                "projectionKind": kind,
                "materialFingerprint": fingerprint,
                "worldviewManifestId": str(
                    active_market.get("aboxSnapshotId") or manifest_id
                ),
                "projectionMode": "incremental-scoped-manifest-reuse",
                "activeScopeCount": int(manifest_state.get("activeScopeCount") or 0),
                "activeSymbolCount": int(manifest_state.get("activeSymbolCount") or 0),
                "retiredScopeIds": list(manifest_state.get("retiredScopeIds") or []),
                "changedIncomingScopeIds": list(
                    manifest_state.get("changedIncomingScopeIds") or []
                ),
                "reusedIncomingScopeIds": list(
                    manifest_state.get("reusedIncomingScopeIds") or []
                ),
                "observationRefreshedScopeIds": refreshed_scope_ids,
                "observationMetadata": observation_refresh,
                "targetScopedManifestPatch": market_target_patch,
                "pendingAboxActivationRecovery": pending_activation_recovery,
                "scopeRepair": {
                    key: scope_repair.get(key)
                    for key in [
                        "status",
                        "applied",
                        "requestedScopeIds",
                        "repairedScopeIds",
                        "retainedRepairScopeIds",
                    ]
                    if key in scope_repair
                },
                "reason": "공용 "
                + ("시장" if kind == "market" else "지식")
                + " 사실이 현재 활성 "
                + world_label
                + "와 같아 저장과 활성화를 생략했습니다.",
            }
        update.worldview.update(
            {
                "materialFingerprint": fingerprint,
                "aboxSnapshotId": manifest_id,
                "snapshotId": manifest_id,
                "worldviewManifestId": manifest_id,
                "scopePlan": list(manifest_state.get("scopePlan") or []),
                "scopeGenerationIds": scope_generations,
                "scopeFingerprints": dict(
                    manifest_state.get("scopeFingerprints") or {}
                ),
                "scopeFamilyCounts": dict(
                    manifest_state.get("scopeFamilyCounts") or {}
                ),
                "marketScopeObservedAt": dict(
                    manifest_state.get("marketScopeObservedAt") or {}
                ),
                "marketScopeObservedAtVersion": str(
                    manifest_state.get("marketScopeObservedAtVersion") or ""
                ),
                "sharedWorldProjectionMode": "incremental-scoped-manifest-patch",
                "sharedWorldActiveScopeCount": int(
                    manifest_state.get("activeScopeCount") or 0
                ),
                "sharedWorldActiveSymbolCount": int(
                    manifest_state.get("activeSymbolCount") or 0
                ),
                "sharedWorldRetiredScopeIds": list(
                    manifest_state.get("retiredScopeIds") or []
                ),
                "targetScopedManifestPatch": market_target_patch,
                "sharedWorldFullRebuild": full_contract_rebuild,
            }
        )
        validation = validate_ontology(update)
        coverage = (
            knowledge_world_coverage(update)
            if kind == "knowledge"
            else market_world_coverage(update)
        )
        coverage.update(
            {
                "coverageScope": "incoming-" + kind + "-patch",
                "projectionKind": kind,
                "activeScopeCount": int(manifest_state.get("activeScopeCount") or 0),
                "activeSymbolCount": int(manifest_state.get("activeSymbolCount") or 0),
                "retiredScopeCount": len(manifest_state.get("retiredScopeIds") or []),
                "changedIncomingScopeCount": len(
                    manifest_state.get("changedIncomingScopeIds") or []
                ),
                "reusedIncomingScopeCount": len(
                    manifest_state.get("reusedIncomingScopeIds") or []
                ),
                "observationRefreshedScopeCount": len(
                    manifest_state.get("observationRefreshedScopeIds") or []
                ),
                "targetScopedManifestPatch": market_target_patch,
            }
        )
        if validation.error_count:
            return {
                **world_metadata(shared_world),
                "status": "invalid-" + status_prefix,
                "projectionKind": kind,
                "materialFingerprint": fingerprint,
                "coverage": coverage,
                "validation": validation.to_dict(),
                "reason": "Shared "
                + kind
                + " observations failed ontology validation.",
            }
        # The shared lease covers Manifest metadata read -> scope patch ->
        # stage. MarketWorld and KnowledgeWorld can activate immediately.
        # SharedPremiseWorld keeps the candidate journal staged; one
        # repository transaction boundary activates it only while native
        # rules run and restores the predecessor on any incomplete result.
        save_result = (
            scoped_saver(update, adopted_write_lease=merge_lease)
            if merge_lease
            else scoped_saver(update)
        )
        save_result = (
            dict(save_result or {})
            if isinstance(save_result, dict)
            else {
                "saved": False,
                "status": "invalid-save-result",
            }
        )
        activation = {}
        premise_activation_deferred = bool(
            kind == "premise"
            and manifest_id
            and str(save_result.get("status") or "")
            in {
                "ok",
                "staged-scoped-manifest",
            }
            and str(save_result.get("aboxSnapshotId") or manifest_id) == manifest_id
        )
        save_status = str(save_result.get("status") or "error")
        save_failed = save_status not in {"ok", "staged-scoped-manifest"}
        if (
            kind != "premise"
            and manifest_id
            and str(save_result.get("status") or "") in {"ok", "staged-scoped-manifest"}
            and str(save_result.get("aboxSnapshotId") or manifest_id) == manifest_id
        ):
            activation = _store.repository_world_call(
                "activate_scoped_abox_manifest",
                manifest_id,
                pending_activation=False,
                world_id=shared_world.world_id,
            )
        elif premise_activation_deferred:
            activation = {
                "status": "staged-native-inference",
                "candidateAboxSnapshotId": manifest_id,
                "previousAboxSnapshotId": str(
                    active_market.get("worldviewManifestId")
                    or active_market.get("aboxSnapshotId")
                    or ""
                ),
                "reason": (
                    "SharedPremiseWorld candidate remains staged until an aligned "
                    "native InferenceBox generation is committed."
                ),
            }
        return {
            **world_metadata(shared_world),
            "projectionKind": kind,
            "status": (
                "staged-scoped-manifest"
                if premise_activation_deferred
                else (
                    "ok"
                    if str((activation or {}).get("status") or "") == "ok"
                    else save_status
                )
            ),
            "retryable": (
                bool(save_result.get("retryable", False)) if save_failed else False
            ),
            "recommendedRetryAfterSeconds": (
                int(save_result.get("recommendedRetryAfterSeconds") or 30)
                if save_failed and bool(save_result.get("retryable", False))
                else 0
            ),
            "reasonCode": (
                str(save_result.get("reasonCode") or "")[:96] if save_failed else ""
            ),
            "failureStage": "shared-world-save" if save_failed else "",
            "errorType": (
                str(save_result.get("errorType") or "")[:96] if save_failed else ""
            ),
            "reason": str(save_result.get("reason") or "")[:220] if save_failed else "",
            "materialFingerprint": fingerprint,
            "worldviewManifestId": manifest_id,
            "projectionMode": "incremental-scoped-manifest-patch",
            "activeScopeCount": int(manifest_state.get("activeScopeCount") or 0),
            "activeSymbolCount": int(manifest_state.get("activeSymbolCount") or 0),
            "retiredScopeIds": list(manifest_state.get("retiredScopeIds") or []),
            "changedIncomingScopeIds": list(
                manifest_state.get("changedIncomingScopeIds") or []
            ),
            "reusedIncomingScopeIds": list(
                manifest_state.get("reusedIncomingScopeIds") or []
            ),
            "observationRefreshedScopeIds": list(
                manifest_state.get("observationRefreshedScopeIds") or []
            ),
            "targetScopedManifestPatch": market_target_patch,
            "pendingAboxActivationRecovery": pending_activation_recovery,
            "fullRebuild": full_contract_rebuild,
            "coverage": coverage,
            "validation": validation.to_dict(),
            "save": save_result,
            "activation": activation,
            "activationDeferred": premise_activation_deferred,
            "pendingAboxActivation": dict(
                save_result.get("pendingAboxActivation") or {}
            ),
            "writeLease": {
                key: value
                for key, value in dict(merge_lease or {}).items()
                if key != "propertiesJson"
            },
            "projectionCoordinator": _store.projection_coordinator_summary(
                coordinator_lease
            ),
        }
    except (
        Exception
    ) as error:  # noqa: BLE001 - market sharing must never suppress account reasoning.
        error_type = type(error).__name__
        return {
            **world_metadata(shared_world),
            "projectionKind": kind,
            "status": "error",
            "retryable": True,
            "recommendedRetryAfterSeconds": 30,
            "reasonCode": "typedb-shared-world-projection-error",
            "failureStage": "shared-world-projection",
            "errorType": error_type,
            "reason": str(error)[:220],
        }
    finally:
        if merge_lease and callable(release_lease):
            try:
                release_lease(merge_lease)
            except (
                Exception
            ):  # noqa: BLE001 - durable expiry protects the next shared update.
                # The PortfolioWorld inference remains independent; the
                # short-lived shared lease will expire if a runtime stop
                # prevents its normal release.
                pass
        _store.release_projection_coordinator_lease(coordinator_lease)
