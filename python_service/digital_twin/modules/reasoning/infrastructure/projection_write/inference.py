"""Inference implementation; facade-independent dependencies."""

from __future__ import annotations
from .inference_ports import InferencePort
from digital_twin.domain.incremental_inference_equivalence import (
    compare_incremental_rule_states,
)
from digital_twin.domain.ontology_change_impact import compact_inference_impact_plan
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_rulebox_governance import (
    rulebox_rules_hash as compute_rulebox_rules_hash,
)
from digital_twin.domain.ontology_runtime_operations import (
    native_rule_failure_diagnostic,
)
from digital_twin.domain.portfolio import AccountSnapshot
from digital_twin.domain.world_partitioned_reasoning import (
    WORLD_PARTITIONED_REASONING_VERSION,
    partitioned_phase_impact_plan,
)
from digital_twin.infrastructure.graph_store_rulebox import rulebox_rules_to_payload
from digital_twin.modules.reasoning.domain.projection_facts import rule_id_from_payload
from typing import Dict, List
import time


def attach_graph_store_inference_result(
    _store: InferencePort,
    result: Dict[str, object],
    snapshot: AccountSnapshot,
    target_symbols: List[str] = None,
    inference_impact_plan: Dict[str, object] = None,
    world_id: str = "",
    candidate_scope_plan: List[Dict[str, object]] = None,
    rulebox_rules_hash: str = "",
    tbox_fingerprint: str = "",
    preflight_graph: PortfolioOntology = None,
    preflight_manifest_id: str = "",
) -> None:
    if not hasattr(_store.repository, "run_rulebox"):
        return
    inference_symbols = _store.inference_symbols(snapshot, target_symbols)
    compact_impact_plan = (
        compact_inference_impact_plan(inference_impact_plan or {})
        if inference_impact_plan
        else {}
    )
    reasoning_context = (
        dict(result.get("reasoningContext") or {})
        if isinstance(result.get("reasoningContext"), dict)
        else {}
    )
    if compact_impact_plan:
        result.setdefault("inferenceImpactPlan", compact_impact_plan)
    active_key = _store.active_graph_store_key(result)
    world_id = str(
        world_id
        or result.get("worldId")
        or (
            (result.get("ontologyWorld") or {}).get("worldId")
            if isinstance(result.get("ontologyWorld"), dict)
            else ""
        )
        or ""
    ).strip()
    runtime_stages = result.setdefault("runtimeStages", {})
    world_partition = (
        _store.world_rule_partition({"rules": _store.rulebox_rules_for_impact()})
        if _store.world_partitioned_reasoning_enabled()
        else {}
    )
    if _store.world_partitioned_reasoning_enabled():
        if compact_impact_plan:
            compact_impact_plan = compact_inference_impact_plan(
                partitioned_phase_impact_plan(
                    compact_impact_plan,
                    world_partition,
                    "account-overlay",
                )
            )
            result["inferenceImpactPlan"] = compact_impact_plan
            projection_scope = result.get("projectionScope")
            if isinstance(projection_scope, dict):
                projection_scope["inferenceImpactPlan"] = compact_impact_plan
        catalog_rule_ids = list(world_partition.get("overlayRuleIds") or [])
        result_slot_rulebox_hash = compute_rulebox_rules_hash(
            rulebox_rules_to_payload(world_partition.get("overlayRules") or [])
        )
    else:
        catalog_rule_ids = [
            rule_id_from_payload(rule)
            for rule in _store.rulebox_rules_for_impact()
            if rule_id_from_payload(rule) and rule.get("enabled", True) is not False
        ]
        result_slot_rulebox_hash = str(rulebox_rules_hash or "")
    # Private handoff to the MySQL execution-proof writer. This catalogue
    # never enters the ABox or the user-facing ontology snapshot.
    result["_ruleResultSlotCatalogRuleIds"] = catalog_rule_ids
    result["_ruleResultSlotRulesHash"] = result_slot_rulebox_hash
    selection_context = {
        "reusable": False,
        "matchedRuleIds": [],
        "matchedRuleCount": 0,
    }
    equivalence_audit_requested = False
    adaptive_target_sharding_profile: Dict[str, object] = {}
    if active_key == "typedb":
        adaptive_target_sharding_profile = (
            _store.adaptive_native_rule_target_sharding_profile(
                snapshot,
                world_id=world_id,
                rulebox_rules_hash=rulebox_rules_hash,
            )
        )
        # This is bounded operational telemetry for the audit record. It
        # never becomes an ABox property or a user-facing investment fact.
        result["nativeRuleAdaptiveTargetSharding"] = {
            "status": str(adaptive_target_sharding_profile.get("status") or ""),
            "source": str(adaptive_target_sharding_profile.get("source") or ""),
            "sampledRunCount": int(
                adaptive_target_sharding_profile.get("sampledRunCount") or 0
            ),
            "compatibleAuditRunCount": int(
                adaptive_target_sharding_profile.get("compatibleAuditRunCount") or 0
            ),
            "preemptiveRuleIds": list(
                adaptive_target_sharding_profile.get("preemptiveRuleIds") or []
            )[:20],
        }
    inference_write_lease: Dict[str, object] = {}
    if active_key == "typedb":
        inference_write_lease = _store.acquire_inference_write_lease(
            result, world_id=world_id
        )
        if inference_write_lease.get("acquired") is False:
            lease_summary = {
                key: value
                for key, value in dict(inference_write_lease or {}).items()
                if key != "propertiesJson"
                and not key.startswith("_")
                and key != "projectionCoordinatorLeaseOwned"
            }
            result["inferenceWriteLease"] = lease_summary
            deferred_status = str(
                inference_write_lease.get("status") or "deferred-inference-write-lease"
            ).strip()
            if deferred_status not in {
                "deferred-projection-coordinator",
                "deferred-inference-write-lease",
            }:
                deferred_status = "deferred-inference-write-lease"
            reason = str(
                inference_write_lease.get("reason")
                or "다른 ABox 활성화 또는 TypeDB 네이티브 추론 세대가 실행 중입니다."
            )
            result["ruleboxExecution"] = {
                "configured": True,
                "status": deferred_status,
                "graphStore": "typedb",
                "source": "typedbNativeRule",
                "nativeTypeDbReasoningUsed": False,
                "reason": reason,
            }
            result["inferenceBox"] = {
                "configured": True,
                "status": deferred_status,
                "graphStore": "typedb",
                "source": "typedbInferenceBox",
                "nativeTypeDbReasoningUsed": False,
                "reason": reason,
            }
            result["aboxStaged"] = bool(result.get("saved"))
            result["saved"] = False
            result["status"] = deferred_status
            result["preservedActiveGeneration"] = True
            result["retryable"] = True
            result["recommendedRetryAfterSeconds"] = int(
                inference_write_lease.get("recommendedRetryAfterSeconds") or 10
            )
            result["reason"] = reason
            return
        if inference_write_lease:
            result["inferenceWriteLease"] = {
                key: value
                for key, value in dict(inference_write_lease or {}).items()
                if key != "propertiesJson"
                and not key.startswith("_")
                and key != "projectionCoordinatorLeaseOwned"
            }
        if bool(compact_impact_plan.get("nativeRuleSelectionEligible")):
            # Read the old aligned InferenceBox only after owning the
            # writer lease. This makes the reuse proof and the following
            # ABox pointer transition one serialized operation.
            selection_started = time.perf_counter()
            # Reuse only the compact MySQL audit proof. A missing proof no
            # longer triggers a full TypeDB InferenceBox read before the
            # changed candidate rules can run.
            selection_context = _store.audited_prior_rule_selection_context(
                snapshot,
                inference_symbols,
                candidate_scope_plan=candidate_scope_plan,
                rulebox_rules_hash=result_slot_rulebox_hash,
                tbox_fingerprint=tbox_fingerprint,
                world_id=world_id,
                requested_fact_families=compact_impact_plan.get("requestedFactFamilies")
                or [],
                requested_fact_families_by_symbol=compact_impact_plan.get(
                    "requestedFactFamiliesBySymbol"
                )
                or {},
            )
            shared_selection_context = _store.shared_inference_selection_context(
                compact_impact_plan,
                reasoning_context,
                inference_symbols,
                selection_context,
            )
            if bool(shared_selection_context.get("reusable")):
                selection_context = shared_selection_context
                result["sharedInferenceExecutionReuse"] = {
                    key: value
                    for key, value in shared_selection_context.items()
                    if key
                    in {
                        "reusable",
                        "proofSource",
                        "targetSymbols",
                        "sharedSnapshotIds",
                        "marketRuleCatalogIds",
                        "matchedRuleCount",
                        "candidateRuleCount",
                        "deferredMarketRuleCount",
                        "fallbackReason",
                    }
                }
            if not selection_context:
                selection_context = {
                    "reusable": False,
                    "proofSource": "",
                    "matchedRuleIds": [],
                    "matchedRuleCount": 0,
                    "fallbackReason": "compact-prior-proof-unavailable",
                }
            runtime_stages["priorInferenceReuseReadMs"] = int(
                (time.perf_counter() - selection_started) * 1000
            )
            recomputed_impact_plan = _store.impact_plan_with_audited_candidates(
                compact_impact_plan,
                selection_context,
            )
            if isinstance(recomputed_impact_plan, dict) and recomputed_impact_plan:
                compact_impact_plan = compact_inference_impact_plan(
                    recomputed_impact_plan
                )
                result["inferenceImpactPlan"] = compact_impact_plan
                projection_scope = result.get("projectionScope")
                if isinstance(projection_scope, dict):
                    projection_scope["inferenceImpactPlan"] = compact_impact_plan
            equivalence_audit_requested = _store.incremental_equivalence_audit_selected(
                snapshot,
                inference_symbols,
                compact_impact_plan,
                selection_context,
            )
            result["priorInferenceReuse"] = {
                key: value
                for key, value in selection_context.items()
                if key
                not in {"matchedRuleIds", "inferenceImpactPlan", "ruleStatesBySymbol"}
            }
            if bool(selection_context.get("reusable")):
                result["_priorRuleStatesBySymbol"] = dict(
                    selection_context.get("ruleStatesBySymbol") or {}
                )
            if equivalence_audit_requested:
                result["incrementalEquivalenceAudit"] = {
                    "status": "requested-full-evaluation",
                    "verified": False,
                    "samplePct": _store.incremental_equivalence_audit_sample_pct(),
                    "reason": "A bounded sample is running the full TypeDB catalogue before slot reconciliation.",
                }
    try:
        if active_key == "typedb":
            preparer = getattr(
                _store.repository, "prepare_pending_abox_activation_for_inference", None
            )
            if callable(preparer):
                preparation_started = time.perf_counter()
                try:
                    preparation = _store.repository_world_call(
                        "prepare_pending_abox_activation_for_inference",
                        world_id=world_id,
                    )
                except (
                    Exception
                ) as error:  # noqa: BLE001 - never run native rules against an uncertain active pointer.
                    preparation = {"status": "error", "reason": str(error)[:180]}
                runtime_stages["aboxActivationPreparationMs"] = int(
                    (time.perf_counter() - preparation_started) * 1000
                )
                result["aboxActivationPreparation"] = preparation
                if str(preparation.get("status") or "") not in {
                    "skipped",
                    "ready",
                    "activated",
                }:
                    result["ruleboxExecution"] = {
                        "configured": True,
                        "status": "blocked-pending-abox-activation",
                        "graphStore": "typedb",
                        "source": "typedbNativeRule",
                        "nativeTypeDbReasoningUsed": False,
                        "reason": str(
                            preparation.get("reason")
                            or "ABox candidate could not be prepared for native inference."
                        )[:220],
                    }
                    result["inferenceBox"] = {
                        "configured": True,
                        "status": "pending-abox-activation",
                        "graphStore": "typedb",
                        "source": "typedbInferenceBox",
                        "nativeTypeDbReasoningUsed": False,
                        "reason": result["ruleboxExecution"]["reason"],
                    }
                    result["aboxStaged"] = bool(result.get("saved"))
                    result["saved"] = False
                    result["status"] = "blocked-pending-abox-activation"
                    result["preservedActiveGeneration"] = True
                    result["reason"] = result["ruleboxExecution"]["reason"]
                    return
        bootstrap_full_rule_coverage = bool(
            active_key == "typedb"
            and (
                not selection_context.get("reusable")
                or selection_context.get("coverageComplete") is False
            )
        )
        if bootstrap_full_rule_coverage:
            result.setdefault("priorInferenceReuse", {}).update(
                {
                    "bootstrapRequired": True,
                    "fallbackReason": str(
                        selection_context.get("fallbackReason")
                        or selection_context.get("reason")
                        or "coherent-rule-result-slot-proof-unavailable"
                    ),
                }
            )
        payload = {
            "worldId": world_id,
            "worldType": (
                str((result.get("ontologyWorld") or {}).get("worldType") or "")
                if isinstance(result.get("ontologyWorld"), dict)
                else ""
            ),
            "tenantId": (
                str((result.get("ontologyWorld") or {}).get("tenantId") or "")
                if isinstance(result.get("ontologyWorld"), dict)
                else ""
            ),
            "accountId": (
                str(
                    (result.get("ontologyWorld") or {}).get("accountId")
                    or snapshot.account_id
                    or ""
                )
                if isinstance(result.get("ontologyWorld"), dict)
                else str(snapshot.account_id or "")
            ),
            "symbols": inference_symbols,
            # Generation retention is intentionally outside the realtime
            # inference boundary. An idle maintenance pass prunes only
            # generations that are no longer active.
            "pruneOldGenerations": False,
            "inferenceSnapshotLimit": _store.inference_snapshot_limit(),
            "inferenceImpactPlan": compact_impact_plan,
            "reasoningSubjectKinds": list(
                []
                if bootstrap_full_rule_coverage
                else reasoning_context.get("subjectKinds") or []
            ),
            "reasoningSubjectIds": list(reasoning_context.get("subjectIds") or []),
            "reasoningAffectedSymbols": list(
                reasoning_context.get("affectedSymbols") or []
            ),
            "nativeRulePlannerTopology": dict(
                (result.get("nativeRulePlannerTopology") or {})
                if isinstance(result.get("nativeRulePlannerTopology"), dict)
                else {}
            ),
            "typedbNativeRuleSelectionEnabled": (
                "0"
                if equivalence_audit_requested or bootstrap_full_rule_coverage
                else _store.settings.get("typedbNativeRuleSelectionEnabled", "1")
            ),
            "priorInferenceReusable": bool(selection_context.get("reusable")),
            "priorMatchedRuleIds": list(selection_context.get("matchedRuleIds") or []),
            "priorInferenceProofSource": str(
                selection_context.get("proofSource") or ""
            ),
            "priorInferenceProofRunId": str(selection_context.get("proofRunId") or ""),
            "nativeRuleAdaptiveTargetShardingProfile": adaptive_target_sharding_profile,
        }
        if _store.world_partitioned_reasoning_enabled():
            payload.update(
                {
                    "ruleExecutionPhase": "account-overlay",
                    "worldPartitionedReasoningVersion": WORLD_PARTITIONED_REASONING_VERSION,
                }
            )
        if inference_write_lease.get("acquired"):
            payload["_inferenceWriteLeaseOwner"] = str(
                inference_write_lease.get("leaseOwner") or ""
            )
        if isinstance(preflight_graph, PortfolioOntology):
            # The graph was just validated and staged by this same writer
            # lease. TypeDB still evaluates every selected direct TypeQL rule; this
            # object can only prove an impossible condition and avoids a
            # second exact ABox read before that evaluation.
            payload["_nativePreflightProjectionGraph"] = preflight_graph
            payload["_nativePreflightProjectionManifestId"] = str(
                preflight_manifest_id
                or (preflight_graph.worldview or {}).get("worldviewManifestId")
                or (preflight_graph.worldview or {}).get("aboxSnapshotId")
                or ""
            )
        try:
            native_inference_started = time.perf_counter()
            execution = _store.repository.run_rulebox(payload)
        except (
            Exception
        ) as error:  # noqa: BLE001 - graph inference must not block monitoring.
            execution = {"status": "error", "reason": str(error)[:180]}
        finally:
            runtime_stages["nativeInferenceMs"] = int(
                (time.perf_counter() - native_inference_started) * 1000
            )
        if isinstance(execution, dict):
            execution.setdefault("graphStore", active_key)
            if active_key == "typedb":
                execution.setdefault("source", "typedbNativeRule")
        else:
            execution = {
                "status": "error",
                "reason": "non-dict RuleBox result",
                "graphStore": active_key,
            }
        result["ruleboxExecution"] = execution
        if (
            equivalence_audit_requested
            and str(execution.get("status") or "").lower() == "ok"
        ):
            result["incrementalEquivalenceAudit"] = compare_incremental_rule_states(
                selection_context.get("ruleStatesBySymbol") or {},
                execution,
                inference_symbols,
                compact_impact_plan.get("deferredRuleIds") or [],
            )
        if str(execution.get("status") or "") == "deferred-inference-write-lease":
            # Do not inspect an older generation or roll back a candidate
            # while the lease owner is still creating its aligned result.
            reason = str(
                execution.get("reason")
                or "Native inference is serialized by another writer."
            )
            result["inferenceBox"] = {
                "configured": True,
                "status": "deferred-inference-write-lease",
                "graphStore": active_key,
                "source": (
                    "typedbInferenceBox"
                    if active_key == "typedb"
                    else "graphInferenceBox"
                ),
                "nativeTypeDbReasoningUsed": False,
                "reason": reason,
            }
            result["saved"] = False
            result["status"] = "deferred-inference-write-lease"
            result["preservedActiveGeneration"] = True
            result["reason"] = reason
            return
        if str(execution.get("status") or "") == "invalid-abox-generation":
            # A stale InferenceBox can still be readable while the active
            # candidate cannot prove one source ABox generation. Never
            # let that unrelated durable readback finalize this candidate.
            result["inferenceBox"] = {
                "configured": True,
                "status": "invalid-abox-generation",
                "graphStore": active_key,
                "source": "typedbInferenceBox",
                "nativeTypeDbReasoningUsed": False,
                "reason": str(
                    execution.get("reason")
                    or "Native inference source ABox generation is invalid."
                ),
            }
            finalization_started = time.perf_counter()
            _store.reconcile_abox_activation_after_inference(
                result, inference_symbols, world_id=world_id
            )
            runtime_stages["aboxActivationFinalizationMs"] = int(
                (time.perf_counter() - finalization_started) * 1000
            )
            return
        native_failure = (
            native_rule_failure_diagnostic(execution, inference_symbols)
            if active_key == "typedb"
            else {}
        )
        if native_failure:
            # A failed native query leaves the previous durable
            # InferenceBox readable. Reading it here used to turn the
            # original TypeDB timeout into a misleading ABox/InferenceBox
            # alignment failure. Reconcile only to restore the prior ABox
            # generation, while retaining the actual blocking rule in the
            # operational audit payload.
            result["nativeRuleFailure"] = native_failure
            reason = str(
                native_failure.get("reason")
                or execution.get("reason")
                or "TypeDB native RuleBox execution did not complete."
            )
            result["inferenceBox"] = {
                "configured": True,
                "status": "native-rule-failed",
                "graphStore": active_key,
                "source": "typedbInferenceBox",
                "nativeTypeDbReasoningUsed": False,
                "nativeTypeDbReasoningCompleted": False,
                "nativeInferenceOutcome": "failed",
                "targetSymbols": list(
                    native_failure.get("targetSymbols") or inference_symbols
                ),
                "reason": reason,
            }
            finalization_started = time.perf_counter()
            _store.reconcile_abox_activation_after_inference(
                result, inference_symbols, world_id=world_id
            )
            runtime_stages["aboxActivationFinalizationMs"] = int(
                (time.perf_counter() - finalization_started) * 1000
            )
            if result.get("preservedActiveGeneration") and bool(
                native_failure.get("retryable")
            ):
                result["retryable"] = True
                result["recommendedRetryAfterSeconds"] = int(
                    native_failure.get("recommendedRetryAfterSeconds") or 30
                )
            return
        # A native RuleBox execution first builds an in-memory graph and
        # then writes it to TypeDB.  The old path expanded every durable
        # InferenceBox row again before an alert could proceed.  In the
        # production outbox path, prove the active marker/ABox pointer
        # instead and retain the already materialized rows in memory.  A
        # low-priority worker reads the detailed durable snapshot later.
        deferred_detail_readback = False
        memory_snapshot = (
            dict(execution.get("inferenceBox") or {})
            if isinstance(execution.get("inferenceBox"), dict)
            else {}
        )
        commit_proof_reader = getattr(
            _store.repository, "inferencebox_commit_proof", None
        )
        if (
            active_key == "typedb"
            and _store.inference_detail_outbox_enabled()
            and memory_snapshot
            and callable(commit_proof_reader)
        ):
            expected_generation_id = str(
                memory_snapshot.get("inferenceGenerationId")
                or execution.get("inferenceGenerationId")
                or ""
            ).strip()
            expected_source_abox_id = str(
                memory_snapshot.get("sourceAboxSnapshotId")
                or execution.get("sourceAboxSnapshotId")
                or result.get("aboxSnapshotId")
                or ""
            ).strip()
            if expected_generation_id and expected_source_abox_id:
                commit_proof_started = time.perf_counter()
                try:
                    commit_proof = _store.repository_world_call(
                        "inferencebox_commit_proof",
                        expected_generation_id,
                        expected_source_abox_id,
                        target_symbols=inference_symbols,
                        world_id=world_id,
                    )
                except (
                    Exception
                ) as error:  # noqa: BLE001 - legacy full readback remains the fail-closed fallback.
                    commit_proof = {
                        "status": "error",
                        "verified": False,
                        "reason": "TypeDB active InferenceBox commit proof failed: "
                        + str(error)[:180],
                    }
                runtime_stages["inferenceCommitProofMs"] = int(
                    (time.perf_counter() - commit_proof_started) * 1000
                )
                if isinstance(commit_proof, dict):
                    result["inferenceCommitProof"] = {
                        key: value
                        for key, value in commit_proof.items()
                        if key not in {"propertiesJson"}
                    }
                if isinstance(commit_proof, dict) and bool(
                    commit_proof.get("verified")
                ):
                    snapshot_payload = dict(memory_snapshot)
                    # Only marker/pointer-proven fields may override the
                    # in-memory materialization. Relations, traces, and
                    # calibration remain the output of this native run.
                    for key in [
                        "status",
                        "graphStore",
                        "worldId",
                        "inferenceGenerationId",
                        "sourceAboxSnapshotId",
                        "activeAboxSnapshotId",
                        "targetSymbols",
                        "targetCoverageStatus",
                        "nativeTypeDbReasoningCompleted",
                        "typedbNativeRuleEvaluationCompleted",
                        "nativeTypeDbReasoningUsed",
                        "typedbNativeRuleReasoningUsed",
                        "nativeInferenceOutcome",
                        "nativeInferenceNoMatch",
                        "generationAligned",
                        "querySource",
                        "typedbReadStatus",
                        "durableCommitProof",
                        "durableReadback",
                    ]:
                        if key in commit_proof:
                            snapshot_payload[key] = commit_proof[key]
                    snapshot_payload.setdefault("graphStore", active_key)
                    snapshot_payload.setdefault("source", "typedbInferenceBox")
                    snapshot_payload["requestedSymbols"] = sorted(
                        {
                            str(symbol or "").upper().strip()
                            for symbol in inference_symbols or []
                            if str(symbol or "").strip()
                        }
                    )
                    snapshot_payload["durableReadback"] = False
                    snapshot_payload["durableCommitProof"] = True
                    result["inferenceBox"] = snapshot_payload
                    deferred_detail_readback = True
        if (
            not deferred_detail_readback
            and active_key == "typedb"
            and hasattr(_store.repository, "inferencebox_snapshot")
        ):
            readback_started = time.perf_counter()
            try:
                snapshot_payload = _store.repository_world_call(
                    "inferencebox_snapshot",
                    symbols=inference_symbols,
                    limit=_store.inference_snapshot_limit(),
                    world_id=world_id,
                )
            except (
                Exception
            ) as error:  # noqa: BLE001 - fail closed when durable inference cannot be read.
                snapshot_payload = {
                    "status": "error",
                    "reason": "TypeDB InferenceBox 재조회 실패: " + str(error)[:180],
                    "graphStore": active_key,
                }
            if isinstance(snapshot_payload, dict):
                snapshot_payload = dict(snapshot_payload)
                snapshot_payload.setdefault("graphStore", active_key)
                snapshot_payload.setdefault("source", "typedbInferenceBox")
                snapshot_payload["durableReadback"] = True
                result["inferenceBox"] = snapshot_payload
            runtime_stages["inferenceDurableReadbackMs"] = int(
                (time.perf_counter() - readback_started) * 1000
            )
        elif not deferred_detail_readback and isinstance(
            execution.get("inferenceBox"), dict
        ):
            snapshot_payload = dict(execution.get("inferenceBox") or {})
            snapshot_payload.setdefault("graphStore", active_key)
            result["inferenceBox"] = snapshot_payload
        elif not deferred_detail_readback and hasattr(
            _store.repository, "inferencebox_snapshot"
        ):
            try:
                snapshot_payload = _store.repository_world_call(
                    "inferencebox_snapshot",
                    symbols=inference_symbols,
                    limit=_store.inference_snapshot_limit(),
                    world_id=world_id,
                )
            except Exception as error:  # noqa: BLE001 - snapshot read is best effort.
                snapshot_payload = {
                    "status": "error",
                    "reason": str(error)[:180],
                    "graphStore": active_key,
                }
            if isinstance(snapshot_payload, dict):
                snapshot_payload.setdefault("graphStore", active_key)
                if active_key == "typedb":
                    snapshot_payload.setdefault("source", "typedbInferenceBox")
                result["inferenceBox"] = snapshot_payload
        finalization_started = time.perf_counter()
        _store.reconcile_abox_activation_after_inference(
            result, inference_symbols, world_id=world_id
        )
        runtime_stages["aboxActivationFinalizationMs"] = int(
            (time.perf_counter() - finalization_started) * 1000
        )
        if deferred_detail_readback:
            detail_queue_started = time.perf_counter()
            verified_snapshot = (
                result.get("inferenceBox")
                if isinstance(result.get("inferenceBox"), dict)
                else {}
            )
            reusable = _store.inference_result_is_reusable(
                verified_snapshot,
                {
                    "aboxSnapshotId": str(
                        verified_snapshot.get("sourceAboxSnapshotId") or ""
                    )
                },
                inference_symbols,
            )
            if bool(result.get("saved")) and reusable:
                detail_receipt = _store.enqueue_inference_detail_readback(
                    result,
                    snapshot,
                    inference_symbols,
                    world_id=world_id,
                )
            else:
                detail_receipt = {
                    "status": "not-queued-inference-not-finalized",
                    "saved": False,
                    "eventuallyConsistent": False,
                    "reason": "The native generation was not finalized as the active alert-safe result.",
                }
            result["inferenceDetailOutbox"] = _store.inference_detail_outbox_summary(
                detail_receipt
            )
            runtime_stages["inferenceDetailOutboxQueueMs"] = int(
                (time.perf_counter() - detail_queue_started) * 1000
            )
    finally:
        if inference_write_lease.get("acquired"):
            result["inferenceWriteLeaseRelease"] = _store.release_inference_write_lease(
                inference_write_lease
            )
