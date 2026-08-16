"""Ontology quality gate shared by decision and notification workflows."""

from typing import Dict, List

from ...domain.ontology_decision_state import (
    DATA_STATE_LABELS,
    REVIEW_LEVEL_LABELS,
    VALIDATION_STATE_LABELS,
)


def _dict_value(value: object) -> Dict[str, object]:
    return value if isinstance(value, dict) else {}


def ontology_quality_candidates(context: Dict[str, object]) -> List[Dict[str, object]]:
    context = _dict_value(context)
    metadata = _dict_value(context.get("metadata"))
    ontology = _dict_value(context.get("ontology"))
    metadata_ontology = _dict_value(metadata.get("ontology"))
    return [
        _dict_value(context.get("ontologyQuality")),
        _dict_value(context.get("ontologyQualitySample")),
        _dict_value(metadata.get("ontologyQuality")),
        _dict_value(metadata.get("ontologyQualitySample")),
        _dict_value(metadata_ontology.get("projection")),
        _dict_value(ontology.get("typedb")),
        _dict_value(metadata_ontology.get("typedb")),
    ]


def ontology_quality_gate_context(context: Dict[str, object], settings: Dict[str, object] = None) -> Dict[str, object]:
    del settings
    for candidate in ontology_quality_candidates(context):
        if not candidate:
            continue
        raw_status = str(
            candidate.get("status")
            or candidate.get("projectionStatus")
            or candidate.get("state")
            or ""
        ).strip().lower()
        errors = list(candidate.get("errors") or candidate.get("violations") or [])
        warnings = list(candidate.get("warnings") or candidate.get("qualityWarnings") or [])
        if raw_status in {"error", "failed", "unavailable", "missing", "blocked"}:
            validation_state = "blocked"
            data_state = "unavailable"
            reason = "온톨로지 연결 또는 추론 결과를 사용할 수 없어 투자 판단을 보류합니다."
        elif errors or warnings or raw_status in {"limited", "partial", "degraded", "stale"}:
            validation_state = "conditional"
            data_state = "partial"
            reason = "온톨로지 자료에 누락이나 경고가 있어 AI 의견을 조건부로 사용합니다."
        else:
            validation_state = "ready"
            data_state = "sufficient"
            reason = "온톨로지 연결과 필수 근거가 확인됐습니다."
        return {
            "enabled": True,
            "status": validation_state,
            "validationState": validation_state,
            "validationLabel": VALIDATION_STATE_LABELS[validation_state],
            "dataState": data_state,
            "dataStateLabel": DATA_STATE_LABELS[data_state],
            "qualitySampleId": str(candidate.get("qualitySampleId") or candidate.get("sampleId") or candidate.get("sample_id") or ""),
            "source": str(candidate.get("source") or "ontologyQuality"),
            "reason": reason,
            "errors": errors[:5],
            "warnings": warnings[:5],
        }
    return {
        "enabled": True,
        "status": "unknown",
        "validationState": "conditional",
        "validationLabel": VALIDATION_STATE_LABELS["conditional"],
        "dataState": "partial",
        "dataStateLabel": DATA_STATE_LABELS["partial"],
        "reason": "온톨로지 품질 상태가 없어 AI 의견을 조건부로 사용합니다.",
    }


def apply_ontology_quality_gate_to_response(response, gate: Dict[str, object]) -> None:
    if not response or not isinstance(gate, dict):
        return
    gate_state = str(gate.get("validationState") or "conditional")
    if gate_state == "ready":
        return
    reason = str(gate.get("reason") or "온톨로지 자료 상태 때문에 AI 의견을 조건부로 사용합니다.")
    if reason not in response.validation_reasons:
        response.validation_reasons.append(reason)
    if reason not in response.validation_warnings:
        response.validation_warnings.append(reason)
    if gate_state == "blocked":
        response.validation_state = "blocked"
        response.validation_label = VALIDATION_STATE_LABELS["blocked"]
        response.data_state = "unavailable"
        response.data_state_label = DATA_STATE_LABELS["unavailable"]
        response.review_level = "blocked"
        response.review_label = REVIEW_LEVEL_LABELS["blocked"]
    elif response.validation_state == "ready":
        response.validation_state = "conditional"
        response.validation_label = VALIDATION_STATE_LABELS["conditional"]
        if response.data_state == "sufficient":
            response.data_state = "partial"
            response.data_state_label = DATA_STATE_LABELS["partial"]
