"""Small, versioned values shared by inference and AI judgement stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, Mapping, Tuple

from ..ontology_change_impact import requested_scope_families_for_event_fact_types


INVESTMENT_REASONING_CONTRACT_VERSION = "investment-reasoning-case-v1"
FACT_DELTA_VERSION = "investment-fact-delta-v1"
INFERENCE_RESULT_VERSION = "investment-inference-result-v1"
AI_JUDGMENT_RESULT_VERSION = "investment-ai-judgment-result-v1"

REASONING_LANE_REALTIME = "REALTIME"
REASONING_LANE_CONTEXT = "CONTEXT"
REASONING_LANE_RECONCILIATION = "RECONCILIATION"


def _texts(values: object, uppercase: bool = False) -> Tuple[str, ...]:
    if isinstance(values, str):
        values = [values]
    if values is None or isinstance(values, Mapping):
        return ()
    try:
        candidates = list(values)
    except TypeError:
        return ()
    result = []
    for value in candidates:
        text = str(value or "").strip()
        if uppercase:
            text = text.upper()
        if text and text not in result:
            result.append(text)
    return tuple(sorted(result))


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _reasoning_lane(fact_types: Iterable[str], work_classes: Iterable[str]) -> str:
    work = {str(value or "").upper() for value in work_classes or []}
    facts = {str(value or "").upper() for value in fact_types or []}
    if work.intersection({"RECONCILIATION", "MAINTENANCE", "BACKFILL"}):
        return REASONING_LANE_RECONCILIATION
    realtime_tokens = (
        "PRICE", "QUOTE", "TRADE", "ORDERBOOK", "INVESTOR_FLOW",
        "MARKET_OBSERVATION", "VOLUME", "POSITION_PNL",
    )
    if any(any(token in fact for token in realtime_tokens) for fact in facts):
        return REASONING_LANE_REALTIME
    return REASONING_LANE_CONTEXT


@dataclass(frozen=True)
class FactDelta:
    source_event_ids: Tuple[str, ...]
    account_ids: Tuple[str, ...]
    symbols: Tuple[str, ...]
    fact_types: Tuple[str, ...]
    scope_families: Tuple[str, ...]
    work_classes: Tuple[str, ...]
    source_observed_at: str
    lane: str
    version: str = FACT_DELTA_VERSION

    @classmethod
    def from_request(cls, request) -> "FactDelta":
        context = _mapping(getattr(request, "context", {}))
        fact_types = _texts(getattr(request, "fact_types", ()), uppercase=True)
        work_classes = _texts(context.get("workClasses") or [], uppercase=True)
        return cls(
            source_event_ids=_texts(getattr(request, "source_event_ids", ())),
            account_ids=_texts(getattr(request, "account_ids", ())),
            symbols=_texts(getattr(request, "symbols", ()), uppercase=True),
            fact_types=fact_types,
            scope_families=_texts(
                requested_scope_families_for_event_fact_types(fact_types)
            ),
            work_classes=work_classes,
            source_observed_at=str(getattr(request, "source_observed_at", "") or ""),
            lane=_reasoning_lane(fact_types, work_classes),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in [
            "source_event_ids", "account_ids", "symbols", "fact_types",
            "scope_families", "work_classes",
        ]:
            payload[key] = list(payload[key])
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "FactDelta":
        payload = dict(value or {})
        return cls(
            source_event_ids=_texts(payload.get("source_event_ids") or payload.get("sourceEventIds")),
            account_ids=_texts(payload.get("account_ids") or payload.get("accountIds")),
            symbols=_texts(payload.get("symbols"), uppercase=True),
            fact_types=_texts(payload.get("fact_types") or payload.get("factTypes"), uppercase=True),
            scope_families=_texts(payload.get("scope_families") or payload.get("scopeFamilies")),
            work_classes=_texts(payload.get("work_classes") or payload.get("workClasses"), uppercase=True),
            source_observed_at=str(payload.get("source_observed_at") or payload.get("sourceObservedAt") or ""),
            lane=str(payload.get("lane") or REASONING_LANE_CONTEXT),
            version=str(payload.get("version") or FACT_DELTA_VERSION),
        )


@dataclass(frozen=True)
class HypothesisRecord:
    hypothesis_id: str
    family_id: str = ""
    label: str = ""
    candidate_action: str = ""
    supporting_rule_ids: Tuple[str, ...] = ()
    supporting_evidence_ids: Tuple[str, ...] = ()
    counter_evidence_ids: Tuple[str, ...] = ()
    causal_trace_ids: Tuple[str, ...] = ()
    assumptions: Tuple[str, ...] = ()
    invalidation_conditions: Tuple[str, ...] = ()
    horizon: str = ""
    validation_state: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HypothesisRecord":
        payload = dict(value or {})
        return cls(
            hypothesis_id=str(payload.get("hypothesisId") or payload.get("hypothesis_id") or ""),
            family_id=str(payload.get("familyId") or payload.get("family_id") or ""),
            label=str(payload.get("label") or payload.get("claim") or payload.get("title") or ""),
            candidate_action=str(payload.get("candidateAction") or payload.get("candidate_action") or "").upper(),
            supporting_rule_ids=_texts(payload.get("supportingRuleIds") or payload.get("supporting_rule_ids")),
            supporting_evidence_ids=_texts(payload.get("supportingEvidenceIds") or payload.get("supporting_evidence_ids")),
            counter_evidence_ids=_texts(payload.get("counterEvidenceIds") or payload.get("counter_evidence_ids")),
            causal_trace_ids=_texts(payload.get("causalTraceIds") or payload.get("causal_trace_ids")),
            assumptions=_texts(payload.get("assumptions")),
            invalidation_conditions=_texts(
                payload.get("invalidationConditions") or payload.get("invalidation_conditions")
            ),
            horizon=str(payload.get("horizon") or ""),
            validation_state=str(payload.get("validationState") or payload.get("validation_state") or ""),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in [
            "supporting_rule_ids", "supporting_evidence_ids", "counter_evidence_ids",
            "causal_trace_ids", "assumptions", "invalidation_conditions",
        ]:
            payload[key] = list(payload[key])
        return payload


@dataclass(frozen=True)
class InferenceResult:
    source_abox_snapshot_ids: Tuple[str, ...] = ()
    inference_generation_ids: Tuple[str, ...] = ()
    verified_account_ids: Tuple[str, ...] = ()
    failed_account_ids: Tuple[str, ...] = ()
    relation_count: int = 0
    trace_count: int = 0
    trace_complete: bool = False
    projection_results: Dict[str, object] = field(default_factory=dict)
    duration_ms: int = 0
    version: str = INFERENCE_RESULT_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in [
            "source_abox_snapshot_ids", "inference_generation_ids",
            "verified_account_ids", "failed_account_ids",
        ]:
            payload[key] = list(payload[key])
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "InferenceResult":
        payload = dict(value or {})
        return cls(
            source_abox_snapshot_ids=_texts(payload.get("source_abox_snapshot_ids") or payload.get("sourceAboxSnapshotIds")),
            inference_generation_ids=_texts(payload.get("inference_generation_ids") or payload.get("inferenceGenerationIds")),
            verified_account_ids=_texts(payload.get("verified_account_ids") or payload.get("verifiedAccountIds")),
            failed_account_ids=_texts(payload.get("failed_account_ids") or payload.get("failedAccountIds")),
            relation_count=max(0, int(payload.get("relation_count") or payload.get("relationCount") or 0)),
            trace_count=max(0, int(payload.get("trace_count") or payload.get("traceCount") or 0)),
            trace_complete=bool(payload.get("trace_complete") or payload.get("traceComplete")),
            projection_results=_mapping(payload.get("projection_results") or payload.get("projectionResults")),
            duration_ms=max(0, int(payload.get("duration_ms") or payload.get("durationMs") or 0)),
            version=str(payload.get("version") or INFERENCE_RESULT_VERSION),
        )


@dataclass(frozen=True)
class AIJudgmentResult:
    request_id: str
    result_id: str
    action: str
    confidence: float
    selected_hypothesis_id: str
    validation_state: str
    rationale: str
    supporting_evidence_ids: Tuple[str, ...] = ()
    opposing_evidence_ids: Tuple[str, ...] = ()
    rejected_candidate_reason: str = ""
    next_observations: Tuple[str, ...] = ()
    reversal_conditions: Tuple[str, ...] = ()
    model: str = ""
    reasoning_effort: str = ""
    latency_ms: int = 0
    version: str = AI_JUDGMENT_RESULT_VERSION

    @classmethod
    def from_result(cls, result, response: Mapping[str, object]) -> "AIJudgmentResult":
        payload = dict(response or {})
        confidence = payload.get("confidence") or payload.get("judgmentStrength") or 0
        try:
            confidence_value = float(confidence or 0)
        except (TypeError, ValueError):
            confidence_value = 0.0
        return cls(
            request_id=str(getattr(result, "request_id", "") or ""),
            result_id=str(getattr(result, "result_id", "") or ""),
            action=str(payload.get("action") or "").upper(),
            confidence=confidence_value,
            selected_hypothesis_id=str(payload.get("selectedHypothesisId") or ""),
            validation_state=str(
                payload.get("validationState")
                or getattr(result, "validation_state", "")
                or "conditional"
            ),
            rationale=str(payload.get("summary") or payload.get("opinion") or ""),
            supporting_evidence_ids=_texts(
                payload.get("supportingEvidenceIds") or payload.get("evidenceIds")
            ),
            opposing_evidence_ids=_texts(
                payload.get("opposingEvidenceIds") or payload.get("counterEvidenceIds")
            ),
            rejected_candidate_reason=str(
                payload.get("candidateAdjustmentReason")
                or payload.get("rejectedCandidateReason")
                or ""
            ),
            next_observations=_texts(payload.get("nextChecks") or payload.get("nextObservations")),
            reversal_conditions=_texts(
                payload.get("reversalConditions")
                or ([payload.get("invalidationCondition")] if payload.get("invalidationCondition") else [])
            ),
            model=str(getattr(result, "model", "") or ""),
            reasoning_effort=str(getattr(result, "reasoning_effort", "") or ""),
            latency_ms=max(0, int(getattr(result, "latency_ms", 0) or 0)),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in [
            "supporting_evidence_ids", "opposing_evidence_ids",
            "next_observations", "reversal_conditions",
        ]:
            payload[key] = list(payload[key])
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AIJudgmentResult":
        payload = dict(value or {})
        try:
            confidence = float(payload.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        return cls(
            request_id=str(payload.get("request_id") or payload.get("requestId") or ""),
            result_id=str(payload.get("result_id") or payload.get("resultId") or ""),
            action=str(payload.get("action") or ""),
            confidence=confidence,
            selected_hypothesis_id=str(payload.get("selected_hypothesis_id") or payload.get("selectedHypothesisId") or ""),
            validation_state=str(payload.get("validation_state") or payload.get("validationState") or ""),
            rationale=str(payload.get("rationale") or ""),
            supporting_evidence_ids=_texts(payload.get("supporting_evidence_ids") or payload.get("supportingEvidenceIds")),
            opposing_evidence_ids=_texts(payload.get("opposing_evidence_ids") or payload.get("opposingEvidenceIds")),
            rejected_candidate_reason=str(payload.get("rejected_candidate_reason") or payload.get("rejectedCandidateReason") or ""),
            next_observations=_texts(payload.get("next_observations") or payload.get("nextObservations")),
            reversal_conditions=_texts(payload.get("reversal_conditions") or payload.get("reversalConditions")),
            model=str(payload.get("model") or ""),
            reasoning_effort=str(payload.get("reasoning_effort") or payload.get("reasoningEffort") or ""),
            latency_ms=max(0, int(payload.get("latency_ms") or payload.get("latencyMs") or 0)),
            version=str(payload.get("version") or AI_JUDGMENT_RESULT_VERSION),
        )


@dataclass(frozen=True)
class FinalDecision:
    action: str
    source: str
    selected_hypothesis_id: str = ""
    validation_state: str = ""
    reason: str = ""
    notification_job_id: str = ""
    published: bool = False

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "FinalDecision":
        payload = dict(value or {})
        return cls(
            action=str(payload.get("action") or ""),
            source=str(payload.get("source") or ""),
            selected_hypothesis_id=str(payload.get("selected_hypothesis_id") or payload.get("selectedHypothesisId") or ""),
            validation_state=str(payload.get("validation_state") or payload.get("validationState") or ""),
            reason=str(payload.get("reason") or ""),
            notification_job_id=str(payload.get("notification_job_id") or payload.get("notificationJobId") or ""),
            published=bool(payload.get("published")),
        )
