"""native_execution: profile through explicit injected capabilities."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from digital_twin.modules.model_registry.contracts import rulebox_rules_hash
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
from digital_twin.modules.reasoning.domain.ontology_subject_fanout import evaluate_subject_fanout_comparison
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.infrastructure.graph_store_rulebox import rulebox_rules_from_payload
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import typedb_bool
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    typedb_native_rule_evidence_read_index_for_execution,
    typedb_native_rule_planner_topology_for_execution,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
)
from typing import Dict, Iterable, List
import hashlib
import json
import time
from .profile_ports import NativeExecutionProfileStore, NativeExecutionProfileRuntime


def abox_generation_identity(metadata: Dict[str, object]) -> Dict[str, object]:
    """Build a compact identity for one immutable active ABox generation."""
    values = dict(metadata or {})
    scope_generations = values.get("scopeGenerationIds")
    scope_generations = dict(scope_generations or {}) if isinstance(scope_generations, dict) else {}
    identity_payload = {
        "worldId": str(values.get("worldId") or ""),
        "aboxSnapshotId": str(values.get("aboxSnapshotId") or ""),
        "worldviewManifestId": str(values.get("worldviewManifestId") or ""),
        "activePointerId": str(values.get("activePointerId") or ""),
        "materialFingerprint": str(values.get("materialFingerprint") or ""),
        "scopeTopologyVersion": str(values.get("scopeTopologyVersion") or ""),
        "scopeGenerationIds": {
            str(key): str(value) for key, value in sorted(scope_generations.items()) if str(key)
        },
    }
    encoded = json.dumps(
        identity_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return {
        "status": str(values.get("status") or ""),
        "worldId": identity_payload["worldId"],
        "aboxSnapshotId": identity_payload["aboxSnapshotId"],
        "worldviewManifestId": identity_payload["worldviewManifestId"],
        "activePointerId": identity_payload["activePointerId"],
        "materialFingerprint": identity_payload["materialFingerprint"],
        "scopeGenerationCount": len(identity_payload["scopeGenerationIds"]),
        "fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def compact_native_rule_profile_rows(rows: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    """Aggregate target shards while retaining every executed rule ID."""
    grouped: Dict[str, Dict[str, object]] = {}
    for raw in rows or []:
        item = dict(raw or {}) if isinstance(raw, dict) else {}
        rule_id = str(item.get("ruleId") or "").strip()
        if not rule_id:
            continue
        target = grouped.setdefault(
            rule_id,
            {
                "ruleId": rule_id,
                "status": str(item.get("status") or "executed"),
                "queryMode": str(item.get("queryMode") or ""),
                "modelSignalInterpretationPolicy": bool(
                    item.get("modelSignalInterpretationPolicy")
                ),
                "modelSignalInterpretationPolicyId": str(
                    item.get("modelSignalInterpretationPolicyId") or ""
                ),
                "sharedModelSignalBridge": bool(item.get("sharedModelSignalBridge")),
                "bridgeSourceScope": str(item.get("bridgeSourceScope") or ""),
                "elapsedMs": 0,
                "queryDurationMs": 0,
                "queryCount": 0,
                "rowCount": 0,
                "workItemCount": 0,
                "candidateSymbols": [],
            },
        )
        target["elapsedMs"] += int(number_or_none(item.get("elapsedMs")) or 0)
        target["queryDurationMs"] += int(number_or_none(item.get("queryDurationMs")) or 0)
        target["queryCount"] += int(number_or_none(item.get("queryCount")) or 0)
        target["rowCount"] += int(number_or_none(item.get("rowCount")) or 0)
        target["workItemCount"] += 1
        target["modelSignalInterpretationPolicy"] = bool(
            target.get("modelSignalInterpretationPolicy")
            or item.get("modelSignalInterpretationPolicy")
        )
        target["sharedModelSignalBridge"] = bool(
            target.get("sharedModelSignalBridge") or item.get("sharedModelSignalBridge")
        )
        target["candidateSymbols"] = clean_symbols_from_payload(
            [
                *target["candidateSymbols"],
                *(item.get("candidateSymbols") or []),
            ]
        )
    return sorted(
        grouped.values(),
        key=lambda item: (int(item.get("elapsedMs") or 0), str(item.get("ruleId") or "")),
        reverse=True,
    )


def profile_native_rule_reads(
    _store: NativeExecutionProfileStore,
    payload: Dict[str, object] = None,
    *,
    _bindings: NativeExecutionProfileRuntime
) -> Dict[str, object]:
    """Replay the native read path without writing operational graph state.

    The method reads the active RuleBox and ABox, evaluates direct TypeQL
    predicates, loads the matched evidence graph, and builds an InferenceBox
    graph in memory. It never acquires a write lease, persists graph rows,
    or rotates generations.
    A sample is comparable only when the active ABox identity is unchanged
    before and after the complete read path.
    """
    values = dict(payload or {})
    world_id = str(values.get("worldId") or "").strip()
    target_symbols = clean_symbols_from_payload(
        values.get("symbols") or values.get("targetSymbols") or []
    )
    repeat_count = max(1, min(3, int(number_or_none(values.get("repeats")) or 2)))
    compare_subject_fanout = typedb_bool(values.get("compareSubjectFanout"))
    subject_parallelism = max(1, min(2, int(number_or_none(values.get("subjectParallelism")) or 2)))
    minimum_fanout_reduction_pct = max(
        0.0,
        min(95.0, float(number_or_none(values.get("minimumFanoutReductionPct")) or 40.0)),
    )
    requested_query_mode = "direct-typeql"
    requested_rule_ids = {
        str(item or "").strip() for item in values.get("ruleIds") or [] if str(item or "").strip()
    }
    report = {
        "configured": bool(_store.address),
        "status": "error",
        "graphStore": "typedb",
        "readOnly": True,
        "mutatedOperationalState": False,
        "writeMethodsInvoked": [],
        "excludedOperations": [
            "abox-write",
            "inferencebox-write",
            "generation-activation",
            "retention-cleanup",
        ],
        "worldId": world_id,
        "targetSymbols": target_symbols,
        "requestedRepeatCount": repeat_count,
        "requestedNativeQueryMode": requested_query_mode,
        "subjectFanoutComparisonRequested": compare_subject_fanout,
        "subjectParallelism": subject_parallelism,
        "minimumFanoutReductionPct": minimum_fanout_reduction_pct,
        "samples": [],
    }
    if not _store.address:
        report.update(
            {"status": "disabled", "reason": "TypeDB ontology storage is not configured."}
        )
        return report

    rulebox_started = time.perf_counter()
    snapshot = _store.rulebox_snapshot()
    report["ruleboxReadMs"] = int((time.perf_counter() - rulebox_started) * 1000)
    rules_payload = snapshot.get("rules") if isinstance(snapshot.get("rules"), list) else []
    if str(snapshot.get("status") or "") != "ok" or not rules_payload:
        report.update(
            {
                "status": "rulebox-not-ready",
                "reason": str(snapshot.get("reason") or "Active RuleBox is unavailable."),
            }
        )
        return report
    try:
        rules = [
            rule
            for rule in rulebox_rules_from_payload({"rules": rules_payload})
            if bool(rule.enabled)
        ]
    except Exception as error:  # noqa: BLE001 - profiler must report malformed operational rules.
        report.update({"status": "invalid-rulebox", "reason": str(error)[:220]})
        return report
    if requested_rule_ids:
        rules = [rule for rule in rules if str(rule.rule_id or "") in requested_rule_ids]
    if not rules:
        report.update(
            {"status": "no-rules", "reason": "No enabled RuleBox rule matched the requested IDs."}
        )
        return report

    report["nativeQueryMode"] = "direct-typeql"

    report["rulebox"] = {
        "ruleCount": len(rules),
        "sourceRulesHash": rulebox_rules_hash(rules_payload),
        "selectedRulesHash": rulebox_rules_hash([rule.to_dict() for rule in rules]),
        "requestedRuleIds": sorted(requested_rule_ids),
    }
    for sample_index in range(repeat_count):
        sample_started = time.perf_counter()
        stage_timings: Dict[str, int] = {}
        _store.reset_query_metrics()
        before_started = time.perf_counter()
        before_metadata = _store.active_abox_metadata(world_id)
        stage_timings["activeAboxBeforeMs"] = int((time.perf_counter() - before_started) * 1000)
        before_identity = _store.abox_generation_identity(before_metadata)
        sample: Dict[str, object] = {
            "sample": sample_index + 1,
            "status": "error",
            "generationFingerprint": str(before_identity.get("fingerprint") or ""),
            "generationBefore": before_identity,
            "validForComparison": False,
            "stageTimings": stage_timings,
        }
        if str(before_metadata.get("status") or "") != "ok":
            sample.update(
                {
                    "status": "abox-not-ready",
                    "reason": str(before_metadata.get("reason") or "Active ABox is unavailable."),
                    "wallClockMs": int((time.perf_counter() - sample_started) * 1000),
                    "typedbQueryMetrics": _store.query_metrics_snapshot(),
                }
            )
            report["samples"].append(sample)
            continue

        planner = typedb_native_rule_planner_topology_for_execution(
            before_metadata,
            target_symbols=target_symbols,
        )
        planner_topology = (
            dict(planner.get("topology") or {})
            if str(planner.get("status") or "") == "verified"
            else None
        )
        scoped_active_abox = (
            str(before_metadata.get("scopedAboxManifestVersion") or "")
            == SCOPED_ABOX_MANIFEST_VERSION
        )
        evidence_read_index = (
            typedb_native_rule_evidence_read_index_for_execution(
                before_metadata,
                target_symbols=target_symbols,
            )
            if scoped_active_abox
            else {
                "status": "legacy",
                "source": "legacy-active-membership",
                "index": {},
            }
        )
        native_started = time.perf_counter()
        try:
            native_result = _store.match_typedb_native_rules(
                rules,
                target_symbols=target_symbols,
                world_id=world_id,
                planner_topology=planner_topology,
                native_rule_parallelism=1,
                native_rule_target_parallelism=1,
                stable_abox_write_lease_held=False,
                evidence_read_index=evidence_read_index,
            )
        except Exception as error:  # noqa: BLE001 - retain an invalid diagnostic sample.
            native_result = {
                "status": "query-error",
                "reason": str(error)[:220],
                "executedRules": [],
                "skippedRules": [],
            }
        sample_materialization_evidence_index = dict(
            native_result.pop(
                "_materializationEvidenceReadIndex",
                evidence_read_index,
            )
            or {}
        )
        stage_timings["nativeRuleQueriesMs"] = int((time.perf_counter() - native_started) * 1000)
        native_status = str(native_result.get("status") or "error")
        sample_reason = str(native_result.get("reason") or "")[:220]
        core_evaluation_complete = bool(
            native_result.get("coreNativeInferenceEvaluationComplete")
            if "coreNativeInferenceEvaluationComplete" in native_result
            else native_status == "ok"
        )
        full_evaluation_complete = bool(
            native_result.get("nativeInferenceEvaluationComplete")
            if "nativeInferenceEvaluationComplete" in native_result
            else native_status == "ok"
        )
        graph_counts = {"entityCount": 0, "relationCount": 0, "inferenceRelationCount": 0}
        subject_fanout_probe: Dict[str, object] = {}
        if compare_subject_fanout:
            if len(target_symbols) < 2:
                subject_fanout_probe = {
                    "status": "rejected",
                    "acceptedForRuntime": False,
                    "reasonCodes": ["at-least-two-subjects-required"],
                    "subjectCount": len(target_symbols),
                }
            elif native_status == "ok" and core_evaluation_complete:
                fanout_started = time.perf_counter()

                def run_subject(subject_symbol: str) -> Dict[str, object]:
                    subject_planner = typedb_native_rule_planner_topology_for_execution(
                        before_metadata,
                        target_symbols=[subject_symbol],
                    )
                    subject_topology = (
                        dict(subject_planner.get("topology") or {})
                        if str(subject_planner.get("status") or "") == "verified"
                        else None
                    )
                    subject_evidence_index = (
                        typedb_native_rule_evidence_read_index_for_execution(
                            before_metadata,
                            target_symbols=[subject_symbol],
                        )
                        if scoped_active_abox
                        else evidence_read_index
                    )
                    subject_started = time.perf_counter()
                    try:
                        result = _store.match_typedb_native_rules(
                            rules,
                            target_symbols=[subject_symbol],
                            world_id=world_id,
                            planner_topology=subject_topology,
                            native_rule_parallelism=1,
                            native_rule_target_parallelism=1,
                            stable_abox_write_lease_held=False,
                            evidence_read_index=subject_evidence_index,
                        )
                    except Exception as error:  # noqa: BLE001 - comparison fails closed.
                        result = {
                            "status": "query-error",
                            "reason": str(error)[:220],
                            "coreNativeInferenceEvaluationComplete": False,
                            "nativeInferenceEvaluationComplete": False,
                            "matches": [],
                            "executedRules": [],
                            "skippedRules": [],
                        }
                    return {
                        "symbol": subject_symbol,
                        "durationMs": int((time.perf_counter() - subject_started) * 1000),
                        "result": result,
                    }

                subject_rows = []
                if subject_parallelism == 1:
                    subject_rows = [run_subject(symbol) for symbol in target_symbols]
                else:
                    with ThreadPoolExecutor(max_workers=subject_parallelism) as executor:
                        futures = {
                            executor.submit(run_subject, symbol): symbol
                            for symbol in target_symbols
                        }
                        for future in as_completed(futures):
                            subject_rows.append(future.result())
                    subject_rows.sort(key=lambda item: str(item.get("symbol") or ""))
                fanout_duration_ms = int((time.perf_counter() - fanout_started) * 1000)
                stage_timings["subjectFanoutProbeMs"] = fanout_duration_ms
                subject_fanout_probe = {
                    "pendingEvaluation": True,
                    "combinedResult": native_result,
                    "subjectResults": [dict(item.get("result") or {}) for item in subject_rows],
                    "combinedDurationMs": int(stage_timings.get("nativeRuleQueriesMs") or 0),
                    "fanoutDurationMs": fanout_duration_ms,
                    "subjectCount": len(subject_rows),
                    "subjectParallelism": subject_parallelism,
                    "subjects": [
                        {
                            "symbol": str(item.get("symbol") or ""),
                            "status": str((item.get("result") or {}).get("status") or "error"),
                            "durationMs": int(item.get("durationMs") or 0),
                            "matchedCount": int(
                                number_or_none((item.get("result") or {}).get("matchedCount")) or 0
                            ),
                            "coreEvaluationComplete": bool(
                                (item.get("result") or {}).get(
                                    "coreNativeInferenceEvaluationComplete"
                                )
                            ),
                        }
                        for item in subject_rows
                    ],
                }
            else:
                subject_fanout_probe = {
                    "status": "rejected",
                    "acceptedForRuntime": False,
                    "reasonCodes": ["combined-evaluation-incomplete"],
                    "subjectCount": len(target_symbols),
                }
        if native_status == "ok":
            try:
                graph_started = time.perf_counter()
                graph = _store.load_graph_for_native_matches(
                    native_result,
                    rules,
                    world_id=world_id,
                    **(
                        {
                            "evidence_read_index": sample_materialization_evidence_index,
                        }
                        if scoped_active_abox
                        else {}
                    ),
                )
                stage_timings["matchedGraphReadMs"] = int(
                    (time.perf_counter() - graph_started) * 1000
                )
                graph.worldview.update(
                    {
                        "worldId": world_id,
                        "worldType": str(before_metadata.get("worldType") or ""),
                        "tenantId": str(before_metadata.get("tenantId") or ""),
                        "accountId": str(before_metadata.get("accountId") or ""),
                    }
                )
                build_started = time.perf_counter()
                _bindings.materialize_typedb_native_matches(graph, rules, native_result)
                in_memory_inference = _bindings.typedb_inferencebox_graph(
                    graph,
                    generation_id="read-only-profile-" + str(sample_index + 1),
                    rulebox_metadata={
                        "worldId": world_id,
                        "readOnlyProfile": True,
                    },
                )
                stage_timings["inferenceGraphBuildMs"] = int(
                    (time.perf_counter() - build_started) * 1000
                )
                graph_counts = {
                    "entityCount": len(graph.entities),
                    "relationCount": len(graph.relations),
                    "inferenceEntityCount": len(in_memory_inference.entities),
                    "inferenceRelationCount": len(in_memory_inference.relations),
                }
            except (
                Exception
            ) as error:  # noqa: BLE001 - retain query evidence when graph read fails.
                native_status = "graph-read-error"
                sample_reason = str(error)[:220]

        after_started = time.perf_counter()
        try:
            after_metadata = _store.active_abox_metadata(world_id)
        except (
            Exception
        ) as error:  # noqa: BLE001 - a missing after identity invalidates only this sample.
            after_metadata = {"status": "error", "reason": str(error)[:220]}
        stage_timings["activeAboxAfterMs"] = int((time.perf_counter() - after_started) * 1000)
        after_identity = _store.abox_generation_identity(after_metadata)
        generation_unchanged = bool(
            str(before_identity.get("status") or "") == "ok"
            and str(after_identity.get("status") or "") == "ok"
            and str(before_identity.get("fingerprint") or "")
            == str(after_identity.get("fingerprint") or "")
        )
        if subject_fanout_probe.get("pendingEvaluation"):
            comparison = evaluate_subject_fanout_comparison(
                subject_fanout_probe.pop("combinedResult", {}),
                subject_fanout_probe.pop("subjectResults", []),
                combined_duration_ms=int(subject_fanout_probe.get("combinedDurationMs") or 0),
                fanout_duration_ms=int(subject_fanout_probe.get("fanoutDurationMs") or 0),
                generation_unchanged=generation_unchanged,
                minimum_reduction_pct=minimum_fanout_reduction_pct,
            )
            subject_fanout_probe.pop("pendingEvaluation", None)
            subject_fanout_probe.update(comparison)
        diagnostic_wall_clock_ms = int((time.perf_counter() - sample_started) * 1000)
        fanout_probe_ms = int(stage_timings.get("subjectFanoutProbeMs") or 0)
        sample.update(
            {
                "status": native_status,
                "reason": sample_reason,
                "generationAfter": after_identity,
                "generationUnchanged": generation_unchanged,
                "validForComparison": (
                    native_status == "ok" and core_evaluation_complete and generation_unchanged
                ),
                "wallClockMs": max(0, diagnostic_wall_clock_ms - fanout_probe_ms),
                "diagnosticWallClockMs": diagnostic_wall_clock_ms,
                "coreEvaluationComplete": core_evaluation_complete,
                "fullEvaluationComplete": full_evaluation_complete,
                "nativeCoverageStatus": str(native_result.get("nativeCoverageStatus") or ""),
                "supportingRuleFailureCount": int(
                    number_or_none(native_result.get("supportingRuleFailureCount")) or 0
                ),
                "blockingRuleFailureCount": int(
                    number_or_none(native_result.get("blockingRuleFailureCount")) or 0
                ),
                "executedRuleCount": int(
                    number_or_none(native_result.get("executedRuleCount")) or 0
                ),
                "executedRuleWorkCount": int(
                    number_or_none(native_result.get("executedRuleWorkCount")) or 0
                ),
                "skippedRuleCount": int(number_or_none(native_result.get("skippedRuleCount")) or 0),
                "matchedCount": int(number_or_none(native_result.get("matchedCount")) or 0),
                "readTransactionCount": int(
                    number_or_none(native_result.get("readTransactionCount")) or 0
                ),
                "readQueryCount": int(number_or_none(native_result.get("readQueryCount")) or 0),
                "parallelRuleExecution": bool(native_result.get("parallelRuleExecution")),
                "nativeRuleParallelism": int(
                    number_or_none(native_result.get("nativeRuleParallelism")) or 1
                ),
                "graphCounts": graph_counts,
                "rules": _store.compact_native_rule_profile_rows(
                    native_result.get("executedRules") or []
                ),
                "skippedRules": _store.compact_native_rule_profile_rows(
                    native_result.get("skippedRules") or []
                ),
                "typedbQueryMetrics": _store.query_metrics_snapshot(),
            }
        )
        if compare_subject_fanout:
            sample["subjectFanoutComparison"] = subject_fanout_probe
        report["samples"].append(sample)

    try:
        final_snapshot = _store.rulebox_snapshot()
    except Exception as error:  # noqa: BLE001 - an unverifiable RuleBox invalidates every sample.
        final_snapshot = {"status": "error", "reason": str(error)[:220]}
    final_rules_payload = (
        final_snapshot.get("rules") if isinstance(final_snapshot.get("rules"), list) else []
    )
    final_hash = rulebox_rules_hash(final_rules_payload) if final_rules_payload else ""
    report["rulebox"]["finalRulesHash"] = final_hash
    report["rulebox"]["unchanged"] = bool(
        final_hash and final_hash == report["rulebox"]["sourceRulesHash"]
    )
    for sample in report["samples"]:
        sample["ruleboxUnchanged"] = bool(report["rulebox"]["unchanged"])
        if not report["rulebox"]["unchanged"]:
            sample["validForComparison"] = False
    valid_count = sum(1 for item in report["samples"] if item.get("validForComparison"))
    fanout_comparisons = [
        dict(item.get("subjectFanoutComparison") or {})
        for item in report["samples"]
        if item.get("subjectFanoutComparison")
    ]
    if compare_subject_fanout:
        report["subjectFanoutGate"] = {
            "status": (
                "accepted"
                if fanout_comparisons
                and all(bool(item.get("acceptedForRuntime")) for item in fanout_comparisons)
                else "rejected"
            ),
            "acceptedForRuntime": bool(
                fanout_comparisons
                and all(bool(item.get("acceptedForRuntime")) for item in fanout_comparisons)
            ),
            "sampleCount": len(fanout_comparisons),
            "minimumFanoutReductionPct": minimum_fanout_reduction_pct,
            "reasonCodes": sorted(
                {
                    str(reason)
                    for item in fanout_comparisons
                    for reason in item.get("reasonCodes") or []
                    if str(reason)
                }
            ),
        }
    report.update(
        {
            "status": (
                "ok"
                if valid_count == repeat_count
                else "partial" if valid_count else "inconclusive"
            ),
            "validSampleCount": valid_count,
            "invalidSampleCount": repeat_count - valid_count,
            "reason": (
                "Every read-only sample used an unchanged active ABox generation."
                if valid_count == repeat_count
                else "At least one sample failed, crossed an active ABox generation, or observed a RuleBox change."
            ),
        }
    )
    return report
