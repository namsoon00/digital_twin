"""Point-in-time quality contract for a V2 ontology decision handoff."""

from __future__ import annotations

import hashlib
import json
from typing import Dict, Mapping


QUALITY_CONTRACT_VERSION = "ontology-decision-quality-v1"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _list(value: object):
    if isinstance(value, (str, bytes)):
        text = str(value or "").strip()
        return [text] if text else []
    return [str(item or "").strip() for item in value or [] if str(item or "").strip()]


def _fingerprint(payload: Mapping[str, object]) -> str:
    serialized = json.dumps(dict(payload or {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_ontology_decision_quality_snapshot(context: Mapping[str, object]) -> Dict[str, object]:
    """Derive one immutable quality view from the exact V2 decision packet.

    The function intentionally uses only data captured with the notification.
    It never reads the active graph, so a historical alert remains reproducible
    after the active ABox, InferenceBox, or RuleBox changes.
    """

    values = _mapping(context)
    metadata = _mapping(values.get("metadata"))
    synthesis = _mapping(values.get("v2DecisionSynthesis")) or _mapping(
        metadata.get("v2DecisionSynthesis")
    )
    if not synthesis:
        return {}

    relation = _mapping(values.get("ontologyRelationContext")) or _mapping(
        metadata.get("ontologyRelationContext")
    )
    graph = _mapping(relation.get("graphStoreInference"))
    reasoning_case = _mapping(values.get("investmentReasoningCase"))
    freshness = _mapping(values.get("dataFreshness"))

    source_abox_snapshot_id = _text(
        synthesis.get("source_abox_snapshot_id"),
        synthesis.get("sourceAboxSnapshotId"),
        relation.get("sourceAboxSnapshotId"),
        graph.get("sourceAboxSnapshotId"),
    )
    inference_generation_id = _text(
        synthesis.get("inference_generation_id"),
        synthesis.get("inferenceGenerationId"),
        relation.get("inferenceGenerationId"),
        graph.get("inferenceGenerationId"),
    )
    relation_source_id = _text(relation.get("sourceAboxSnapshotId"), graph.get("sourceAboxSnapshotId"))
    relation_generation_id = _text(relation.get("inferenceGenerationId"), graph.get("inferenceGenerationId"))
    case_source_ids = _list(
        reasoning_case.get("sourceAboxSnapshotIds")
        or reasoning_case.get("source_abox_snapshot_ids")
    )
    case_generation_ids = _list(
        reasoning_case.get("inferenceGenerationIds")
        or reasoning_case.get("inference_generation_ids")
    )

    errors = []
    warnings = []
    decision_limitations = []
    if not source_abox_snapshot_id:
        errors.append("V2 결정 합성에 원천 ABox 스냅샷 ID가 없습니다.")
    if not inference_generation_id:
        errors.append("V2 결정 합성에 추론 세대 ID가 없습니다.")
    if relation_source_id and source_abox_snapshot_id and relation_source_id != source_abox_snapshot_id:
        errors.append("결정 합성과 관계 컨텍스트의 ABox 스냅샷이 일치하지 않습니다.")
    if relation_generation_id and inference_generation_id and relation_generation_id != inference_generation_id:
        errors.append("결정 합성과 관계 컨텍스트의 추론 세대가 일치하지 않습니다.")
    if case_source_ids and source_abox_snapshot_id not in case_source_ids:
        errors.append("결정 합성의 ABox 스냅샷이 추론 케이스에 포함되지 않습니다.")
    if case_generation_ids and inference_generation_id not in case_generation_ids:
        errors.append("결정 합성의 추론 세대가 추론 케이스에 포함되지 않습니다.")

    graph_trace_complete = bool(
        synthesis.get("graph_trace_complete") or synthesis.get("graphTraceComplete")
    )
    generation_aligned = relation.get("generationAligned") is not False
    native_reasoning_used = bool(
        relation.get("nativeTypeDbReasoningUsed")
        or graph.get("nativeTypeDbReasoningUsed")
        or graph_trace_complete
    )
    if not graph_trace_complete:
        errors.append("동일 세대의 TypeDB 추론 경로가 완전하게 연결되지 않았습니다.")
    if not generation_aligned:
        errors.append("활성 ABox와 추론 세대가 일치하지 않습니다.")
    if not native_reasoning_used:
        errors.append("TypeDB 네이티브 추론 완료 기록이 없습니다.")

    data_state = _text(
        synthesis.get("data_state"),
        synthesis.get("dataState"),
        relation.get("dataState"),
        _mapping(relation.get("decision")).get("dataState"),
        "partial",
    ).lower()
    if data_state not in {"sufficient", "partial", "unavailable"}:
        data_state = "partial"
    missing_data = _list(synthesis.get("missing_data") or synthesis.get("missingData"))
    if data_state == "partial":
        decision_limitations.append("결정에 필요한 자료가 일부만 확인됐습니다.")
    elif data_state == "unavailable":
        decision_limitations.append("결정에 필요한 핵심 자료를 사용할 수 없습니다.")
    if missing_data:
        decision_limitations.append("결정 합성에 누락 자료가 기록되어 있습니다.")

    freshness_status = _text(
        values.get("dataFreshnessStatus"),
        freshness.get("status"),
        "unknown",
    ).lower()
    if freshness_status in {"stale", "missing", "unavailable"}:
        decision_limitations.append("판단 자료의 신선도 조건을 충족하지 못했습니다.")
    judgement_blocked = bool(
        synthesis.get("judgement_blocked") or synthesis.get("judgementBlocked")
    )
    if judgement_blocked:
        decision_limitations.append("TypeDB 결정 합성이 실행 판단을 보류했습니다.")

    eligible_ids = _list(
        synthesis.get("eligible_hypothesis_ids")
        or synthesis.get("eligibleHypothesisIds")
    )
    allowed_actions = _list(synthesis.get("allowed_actions") or synthesis.get("allowedActions"))
    pipeline_validation = "failed" if errors else "passed"
    evidence_quality = data_state
    decision_confidence = (
        "blocked" if judgement_blocked or data_state == "unavailable"
        else "conditional" if data_state != "sufficient" or not eligible_ids
        else "ready"
    )
    execution_eligibility = (
        "blocked" if errors or judgement_blocked or data_state == "unavailable"
        else "eligible" if allowed_actions and eligible_ids
        else "review-only"
    )
    validation_state = "blocked" if errors else "conditional" if warnings else "ready"

    snapshot = {
        "contractVersion": QUALITY_CONTRACT_VERSION,
        "status": validation_state,
        "validationState": validation_state,
        "dataState": data_state,
        "source": "v2-decision-synthesis",
        "caseId": _text(values.get("investmentReasoningCaseId"), reasoning_case.get("caseId")),
        "requestId": _text(reasoning_case.get("requestId")),
        "deploymentId": _text(reasoning_case.get("deploymentId"), relation.get("reasoningEngineDeploymentId")),
        "releaseFingerprint": _text(reasoning_case.get("releaseFingerprint"), relation.get("reasoningEngineReleaseFingerprint")),
        "synthesisId": _text(synthesis.get("synthesis_id"), synthesis.get("synthesisId")),
        "sourceAboxSnapshotId": source_abox_snapshot_id,
        "inferenceGenerationId": inference_generation_id,
        "generatedAt": _text(relation.get("inferenceGenerationAt"), values.get("referenceDate")),
        "generationAligned": generation_aligned,
        "graphTraceComplete": graph_trace_complete,
        "nativeTypeDbReasoningUsed": native_reasoning_used,
        "freshnessStatus": freshness_status,
        "judgementBlocked": judgement_blocked,
        "eligibleHypothesisCount": len(eligible_ids),
        "allowedActions": allowed_actions,
        "missingData": missing_data,
        "decisionLimitations": decision_limitations,
        "pipelineValidation": pipeline_validation,
        "evidenceQuality": evidence_quality,
        "decisionConfidence": decision_confidence,
        "executionEligibility": execution_eligibility,
        "errors": errors,
        "warnings": warnings,
    }
    snapshot["fingerprint"] = _fingerprint(snapshot)
    snapshot["qualitySampleId"] = "ontology-quality:" + snapshot["fingerprint"][:24]
    return snapshot
