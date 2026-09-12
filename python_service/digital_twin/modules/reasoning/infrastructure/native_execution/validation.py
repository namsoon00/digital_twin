"""native_execution: validation through explicit injected capabilities."""

from digital_twin.modules.model_registry.contracts import GraphInferenceRule
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.infrastructure.graph_store_rulebox import rulebox_rules_from_payload
from digital_twin.modules.reasoning.infrastructure.backend_constants import (
    TYPEDB_NATIVE_REASONING_MODE,
)
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import typedb_bool
from digital_twin.modules.reasoning.infrastructure.typeql.profiles import (
    typedb_native_reasoning_profile,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
)
from typing import Dict
from .validation_ports import NativeExecutionValidationStore, NativeExecutionValidationRuntime


def validate_rulebox_materialization(
    _store: NativeExecutionValidationStore,
    payload: Dict[str, object] = None,
    *,
    _bindings: NativeExecutionValidationRuntime
) -> Dict[str, object]:
    payload = payload if isinstance(payload, dict) else {}
    if not _store.address:
        return _bindings.NullTypeDBOntologyGraphRepository().validate_rulebox_materialization(
            payload
        )
    world_id = str(payload.get("worldId") or payload.get("ontologyWorldId") or "").strip()
    _store.reset_query_metrics()
    target_symbols = clean_symbols_from_payload(
        payload.get("symbols") or payload.get("targetSymbols") or payload.get("changedSymbols")
    )
    try:
        candidate_rules = rulebox_rules_from_payload({"rules": payload.get("rules") or []})
    except ValueError as error:
        return {
            "configured": True,
            "status": "invalid-rulebox",
            "graphStore": "typedb",
            "reason": str(error),
            "validationOnly": True,
            "mutatedOperationalRuleBox": False,
            "wroteInferenceBox": False,
            "candidateRuleCount": 0,
            "baselineInferenceBox": {},
            "diff": _bindings.materialization_preview_diff_payload({}, 0, 0, False),
        }
    enabled_rules = [
        GraphInferenceRule.from_dict({**rule.to_dict(), "enabled": True})
        for rule in candidate_rules
    ]
    native_profile = typedb_native_reasoning_profile(enabled_rules)
    try:
        abox_available = _store.has_box_rows("ABox", world_id=world_id)
        abox_metadata = _store.active_abox_metadata(world_id) if abox_available else {}
    except Exception as error:  # noqa: BLE001 - expose TypeDB read failures to strategy validation.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": "TypeDB ABox 조회 실패: " + str(error)[:180],
            "validationOnly": True,
            "mutatedOperationalRuleBox": False,
            "wroteInferenceBox": False,
            "candidateRuleCount": len(enabled_rules),
            "nativeReasoningProfile": native_profile,
            "baselineInferenceBox": {},
            "diff": _bindings.materialization_preview_diff_payload(
                {}, 0, len(enabled_rules), False
            ),
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
        }
    if not abox_available:
        return {
            "configured": True,
            "status": "missing-abox",
            "graphStore": "typedb",
            "reason": "TypeDB에 실행 가능한 ABox 그래프가 없습니다.",
            "validationOnly": True,
            "mutatedOperationalRuleBox": False,
            "wroteInferenceBox": False,
            "candidateRuleCount": len(enabled_rules),
            "nativeReasoningProfile": native_profile,
            "baselineInferenceBox": {},
            "diff": _bindings.materialization_preview_diff_payload(
                {}, 0, len(enabled_rules), False
            ),
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
        }
    if str(abox_metadata.get("status") or "") != "ok":
        return {
            "configured": True,
            "status": "incomplete-abox",
            "graphStore": "typedb",
            "reasonCode": "typedbIncompleteAbox",
            "reason": "TypeDB ABox 저장이 아직 완료되지 않아 후보 규칙 검증을 보류했습니다. "
            + str(
                abox_metadata.get("reason") or "완료 표식 또는 저장 건수를 다시 확인해야 합니다."
            )[:180],
            "validationOnly": True,
            "mutatedOperationalRuleBox": False,
            "wroteInferenceBox": False,
            "candidateRuleCount": len(enabled_rules),
            "nativeReasoningProfile": native_profile,
            "aboxMetadata": abox_metadata,
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
        }
    baseline_requested = payload.get("includeBaseline") is not False or typedb_bool(payload.get("policyOnly"))
    if not baseline_requested:
        # Candidate condition checks do not need a full historic InferenceBox
        # comparison. Keep execution/ABox checks mandatory and absence explicit.
        baseline_inferencebox = {"status": "not-requested", "relationCount": None, "traceCount": None}
    else:
        try:
            baseline_inferencebox = _store.inferencebox_snapshot_from_typedb(
                target_symbols,
                80,
                world_id=world_id,
            )
        except Exception as error:  # noqa: BLE001 - baseline diff is diagnostic only.
            baseline_inferencebox = {
                "status": "error",
                "graphStore": "typedb",
                "source": "typedbInferenceBox",
                "reasonCode": _bindings.typedb_error_code(error),
                "reason": "TypeDB InferenceBox 기준선 조회 실패: " + str(error)[:180],
                "relationCount": 0,
                "traceCount": 0,
            }
    if typedb_bool(payload.get("policyOnly")):
        # Hypothesis lifecycle and outcome contracts are read by the
        # lifecycle audit after native relation materialization.  Their
        # preview must not replace the governed RuleBox merely to
        # validate a non-predicate policy edit.
        return {
            "configured": True,
            "status": "ok",
            "graphStore": "typedb",
            "source": "typedbPolicyContractPreview",
            "reasoningMode": "typedb-read-only-policy-contract-preview",
            "reason": "현재 ABox·InferenceBox 기준선과 RuleBox 계약 형식을 읽기 전용으로 확인했습니다.",
            "validationOnly": True,
            "mutatedOperationalRuleBox": False,
            "wroteInferenceBox": False,
            "candidateRuleCount": len(enabled_rules),
            "candidateRuleIds": [rule.rule_id for rule in enabled_rules],
            "targetSymbols": target_symbols,
            "worldId": world_id,
            "baselineInferenceBox": baseline_inferencebox,
            "diff": _bindings.materialization_preview_diff_payload(
                baseline_inferencebox,
                0,
                len(enabled_rules),
                False,
            ),
            "nativeCandidateExecutionSkipped": True,
            "nativeReasoningProfile": native_profile,
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
        }
    try:
        native_match_result = _store.match_typedb_native_rules(
            enabled_rules,
            target_symbols=target_symbols,
            world_id=world_id,
        )
        native_query_used = str(native_match_result.get("status") or "") == "ok"
        matched_count = int(number_or_none(native_match_result.get("matchedCount")) or 0)
        diff = _bindings.materialization_preview_diff_payload(
            baseline_inferencebox, matched_count, len(enabled_rules), native_query_used,
        )
        if not baseline_requested:
            diff.update({"status": "not-requested", "baselineRelationCount": None,
                         "baselineTraceCount": None, "matchedMinusBaselineRelations": None})
        return {
            "configured": True,
            "status": "ok" if native_query_used else "error",
            "graphStore": "typedb",
            "source": "typedbCandidateRulePreview",
            "reasoningMode": TYPEDB_NATIVE_REASONING_MODE,
            "reason": (
                ""
                if native_query_used
                else "TypeDB direct TypeQL preview failed: "
                + str(native_match_result.get("reason") or "")[:180]
            ),
            "validationOnly": True,
            "mutatedOperationalRuleBox": False,
            "wroteInferenceBox": False,
            "candidateRuleCount": len(enabled_rules),
            "candidateRuleIds": [rule.rule_id for rule in enabled_rules],
            "targetSymbols": target_symbols,
            "worldId": world_id,
            "matchedCount": matched_count,
            "baselineInferenceBox": baseline_inferencebox,
            "baselineRequested": baseline_requested,
            "diff": diff,
            "nativeTypeDbReasoningUsed": native_query_used,
            "typedbDirectTypeqlUsed": native_query_used,
            "nativeMatchResult": {
                key: native_match_result.get(key)
                for key in [
                    "status",
                    "reason",
                    "reasonCode",
                    "nativeQueryUsed",
                    "indexedEvidenceQueryUsed",
                    "executedRuleCount",
                    "skippedRuleCount",
                    "matchedCount",
                    "executedRules",
                    "skippedRules",
                    "nativeExecutionMode",
                    "readTransactionCount",
                    "readQueryCount",
                    "executionPlan",
                    "blockingRule",
                    "typedbQueryMetrics",
                ]
                if key in native_match_result
            },
            "nativeReasoningProfile": native_profile,
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
        }
    except (
        Exception
    ) as error:  # noqa: BLE001 - strategy validation is diagnostic, not runtime judgement.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": "TypeDB candidate rule preview failed: " + str(error)[:180],
            "validationOnly": True,
            "mutatedOperationalRuleBox": False,
            "wroteInferenceBox": False,
            "candidateRuleCount": len(enabled_rules),
            "nativeReasoningProfile": native_profile,
            "baselineInferenceBox": (
                baseline_inferencebox if "baseline_inferencebox" in locals() else {}
            ),
            "diff": _bindings.materialization_preview_diff_payload(
                baseline_inferencebox if "baseline_inferencebox" in locals() else {},
                0,
                len(enabled_rules),
                False,
            ),
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
        }
