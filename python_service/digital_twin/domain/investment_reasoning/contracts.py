"""Small, versioned values shared by inference and AI judgement stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Dict, Iterable, Mapping, Optional, Tuple

from ..hypothesis_catalog import hypothesis_family_definition
from ..ontology_rule_knowledge import rule_knowledge_basis_from_rows

from ..ontology_change_impact import requested_scope_families_for_event_fact_types


INVESTMENT_REASONING_CONTRACT_VERSION = "investment-reasoning-case-v2"
FACT_DELTA_VERSION = "investment-fact-delta-v2"
INFERENCE_RESULT_VERSION = "investment-inference-result-v2"
RULE_EVALUATION_RECORD_VERSION = "investment-rule-evaluation-record-v1"
AI_JUDGMENT_RESULT_VERSION = "investment-ai-judgment-result-v2"
DECISION_SYNTHESIS_VERSION = "investment-decision-synthesis-v4"

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


def _stable_id(prefix: str, *values: object) -> str:
    material = json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return prefix + ":" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


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
    subject_ids: Tuple[str, ...] = ()
    subject_revisions: Dict[str, str] = field(default_factory=dict)
    version: str = FACT_DELTA_VERSION

    @classmethod
    def from_request(cls, request) -> "FactDelta":
        context = _mapping(getattr(request, "context", {}))
        fact_types = _texts(getattr(request, "fact_types", ()), uppercase=True)
        work_classes = _texts(context.get("workClasses") or [], uppercase=True)
        subject_revisions = {
            str(subject_id or "").strip(): str(revision or "").strip()
            for subject_id, revision in _mapping(context.get("subjectRevisions")).items()
            if str(subject_id or "").strip() and str(revision or "").strip()
        }
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
            subject_ids=_texts(context.get("subjectIds") or subject_revisions.keys()),
            subject_revisions=dict(sorted(subject_revisions.items())),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in [
            "source_event_ids", "account_ids", "symbols", "fact_types",
            "scope_families", "work_classes", "subject_ids",
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
            subject_ids=_texts(payload.get("subject_ids") or payload.get("subjectIds")),
            subject_revisions={
                str(subject_id or "").strip(): str(revision or "").strip()
                for subject_id, revision in _mapping(
                    payload.get("subject_revisions") or payload.get("subjectRevisions")
                ).items()
                if str(subject_id or "").strip() and str(revision or "").strip()
            },
            version=str(payload.get("version") or FACT_DELTA_VERSION),
        )


@dataclass(frozen=True)
class HypothesisRecord:
    hypothesis_id: str
    family_id: str = ""
    label: str = ""
    causal_signature: str = ""
    candidate_action: str = ""
    supporting_rule_ids: Tuple[str, ...] = ()
    supporting_evidence_ids: Tuple[str, ...] = ()
    counter_evidence_ids: Tuple[str, ...] = ()
    causal_trace_ids: Tuple[str, ...] = ()
    assumptions: Tuple[str, ...] = ()
    invalidation_conditions: Tuple[str, ...] = ()
    horizon: str = ""
    validation_state: str = ""
    theory_family: str = ""
    thesis_family: str = ""
    evidence_independence_key: str = ""
    prediction_target: str = ""
    expected_direction: str = ""
    expected_outcome: str = ""
    competing_family_ids: Tuple[str, ...] = ()
    outcome_metric: str = ""
    falsification_contract: str = ""
    knowledge_basis: Dict[str, object] = field(default_factory=dict)
    claim_contract: Dict[str, object] = field(default_factory=dict)
    qualification: Dict[str, object] = field(default_factory=dict)
    evidence_state: str = ""
    approval_status: str = ""
    verification_status: str = ""
    status: str = ""
    scope_state: str = ""
    scope_version: str = ""
    market_hypothesis_id: str = ""
    market_world_id: str = ""
    market_id: str = ""
    market_causal_signature: str = ""
    account_hypothesis_overlay_id: str = ""
    portfolio_world_id: str = ""
    account_id: str = ""
    subject_symbol: str = ""
    inference_generation_id: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HypothesisRecord":
        payload = dict(value or {})
        supporting_rule_ids = _texts(
            payload.get("supportingRuleIds") or payload.get("supporting_rule_ids")
        )
        primary_rule_id = supporting_rule_ids[0] if supporting_rule_ids else ""
        knowledge_basis = rule_knowledge_basis_from_rows(
            primary_rule_id,
            [payload],
        ).to_dict() if primary_rule_id else dict(
            payload.get("knowledgeBasis") or payload.get("knowledge_basis") or {}
        )
        thesis_family = str(
            payload.get("thesisFamily")
            or payload.get("thesis_family")
            or knowledge_basis.get("thesisFamily")
            or ""
        )
        family_definition = hypothesis_family_definition(thesis_family)
        return cls(
            hypothesis_id=str(payload.get("hypothesisId") or payload.get("hypothesis_id") or ""),
            family_id=str(payload.get("familyId") or payload.get("family_id") or ""),
            label=str(payload.get("label") or payload.get("claim") or payload.get("title") or ""),
            causal_signature=str(
                payload.get("causalSignature") or payload.get("causal_signature") or ""
            ),
            candidate_action=str(payload.get("candidateAction") or payload.get("candidate_action") or "").upper(),
            supporting_rule_ids=supporting_rule_ids,
            supporting_evidence_ids=_texts(payload.get("supportingEvidenceIds") or payload.get("supporting_evidence_ids")),
            counter_evidence_ids=_texts(payload.get("counterEvidenceIds") or payload.get("counter_evidence_ids")),
            causal_trace_ids=_texts(
                payload.get("causalTraceIds")
                or payload.get("causalPathIds")
                or payload.get("causal_trace_ids")
                or payload.get("causal_path_ids")
            ),
            assumptions=_texts(payload.get("assumptions")),
            invalidation_conditions=_texts(
                payload.get("invalidationConditions") or payload.get("invalidation_conditions")
            ),
            horizon=str(payload.get("horizon") or ""),
            validation_state=str(payload.get("validationState") or payload.get("validation_state") or ""),
            theory_family=str(
                payload.get("theoryFamily")
                or payload.get("theory_family")
                or knowledge_basis.get("theoryFamily")
                or ""
            ),
            thesis_family=thesis_family,
            evidence_independence_key=str(
                payload.get("evidenceIndependenceKey")
                or payload.get("evidence_independence_key")
                or knowledge_basis.get("evidenceIndependenceKey")
                or ""
            ),
            prediction_target=str(
                payload.get("predictionTarget")
                or payload.get("prediction_target")
                or (family_definition.prediction_target if family_definition else "")
            ),
            expected_direction=str(
                payload.get("expectedDirection")
                or payload.get("expected_direction")
                or (family_definition.expected_direction if family_definition else "")
            ),
            expected_outcome=str(
                payload.get("expectedOutcome")
                or payload.get("expected_outcome")
                or (family_definition.expected_outcome if family_definition else "")
            ),
            competing_family_ids=_texts(
                payload.get("competingFamilyIds")
                or payload.get("competing_family_ids")
                or (family_definition.competing_family_ids if family_definition else ())
            ),
            outcome_metric=str(
                payload.get("outcomeMetric")
                or payload.get("outcome_metric")
                or (family_definition.outcome_metric if family_definition else "")
            ),
            falsification_contract=str(
                payload.get("falsificationContract")
                or payload.get("falsification_contract")
                or (family_definition.falsification_contract if family_definition else "")
            ),
            knowledge_basis=knowledge_basis,
            claim_contract=dict(
                payload.get("claimContract")
                or payload.get("claim_contract")
                or {}
            ),
            qualification=dict(payload.get("qualification") or {}),
            evidence_state=str(
                payload.get("evidenceState") or payload.get("evidence_state") or ""
            ).lower(),
            approval_status=str(
                payload.get("approvalStatus") or payload.get("approval_status") or ""
            ).lower(),
            verification_status=str(
                payload.get("verificationStatus")
                or payload.get("verification_status")
                or payload.get("validationState")
                or payload.get("validation_state")
                or ""
            ).lower(),
            status=str(payload.get("status") or "").lower(),
            scope_state=str(
                payload.get("scopeState") or payload.get("scope_state") or ""
            ).lower(),
            scope_version=str(
                payload.get("scopeVersion") or payload.get("scope_version") or ""
            ),
            market_hypothesis_id=str(
                payload.get("marketHypothesisId")
                or payload.get("market_hypothesis_id")
                or ""
            ),
            market_world_id=str(
                payload.get("marketWorldId") or payload.get("market_world_id") or ""
            ),
            market_id=str(payload.get("marketId") or payload.get("market_id") or ""),
            market_causal_signature=str(
                payload.get("marketCausalSignature")
                or payload.get("market_causal_signature")
                or ""
            ),
            account_hypothesis_overlay_id=str(
                payload.get("accountHypothesisOverlayId")
                or payload.get("account_hypothesis_overlay_id")
                or ""
            ),
            portfolio_world_id=str(
                payload.get("portfolioWorldId")
                or payload.get("portfolio_world_id")
                or ""
            ),
            account_id=str(payload.get("accountId") or payload.get("account_id") or ""),
            subject_symbol=str(payload.get("subjectSymbol") or payload.get("subject_symbol") or "").upper(),
            inference_generation_id=str(
                payload.get("inferenceGenerationId")
                or payload.get("inference_generation_id")
                or ""
            ),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in [
            "supporting_rule_ids", "supporting_evidence_ids", "counter_evidence_ids",
            "causal_trace_ids", "assumptions", "invalidation_conditions",
            "competing_family_ids",
        ]:
            payload[key] = list(payload[key])
        return payload


@dataclass(frozen=True)
class ActionAlternative:
    """One graph-authored action path and the hypotheses that support it."""

    action: str
    hypothesis_ids: Tuple[str, ...] = ()
    supporting_rule_ids: Tuple[str, ...] = ()
    supporting_evidence_ids: Tuple[str, ...] = ()
    counter_evidence_ids: Tuple[str, ...] = ()
    evidence_conflict_ids: Tuple[str, ...] = ()
    invalidation_conditions: Tuple[str, ...] = ()
    decision_eligible: bool = False

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ActionAlternative":
        payload = dict(value or {})
        return cls(
            action=str(payload.get("action") or "").upper(),
            hypothesis_ids=_texts(payload.get("hypothesis_ids") or payload.get("hypothesisIds")),
            supporting_rule_ids=_texts(payload.get("supporting_rule_ids") or payload.get("supportingRuleIds")),
            supporting_evidence_ids=_texts(payload.get("supporting_evidence_ids") or payload.get("supportingEvidenceIds")),
            counter_evidence_ids=_texts(payload.get("counter_evidence_ids") or payload.get("counterEvidenceIds")),
            evidence_conflict_ids=_texts(payload.get("evidence_conflict_ids") or payload.get("evidenceConflictIds")),
            invalidation_conditions=_texts(payload.get("invalidation_conditions") or payload.get("invalidationConditions")),
            decision_eligible=bool(payload.get("decision_eligible") or payload.get("decisionEligible")),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in [
            "hypothesis_ids", "supporting_rule_ids", "supporting_evidence_ids",
            "counter_evidence_ids", "evidence_conflict_ids", "invalidation_conditions",
        ]:
            payload[key] = list(payload[key])
        return payload


@dataclass(frozen=True)
class DecisionSynthesis:
    """Stable handoff from TypeDB alternatives to the AI judgement boundary."""

    synthesis_id: str
    account_id: str
    symbol: str
    source_abox_snapshot_id: str
    inference_generation_id: str
    graph_candidate_action: str = ""
    investment_view_action: str = ""
    execution_action: str = "NO_ACTION"
    execution_disposition: str = "judgement-blocked"
    decision_effect: str = ""
    decision_disposition: str = ""
    action_authority: str = "observe"
    allowed_actions: Tuple[str, ...] = ()
    blocked_actions: Tuple[str, ...] = ()
    alternatives: Tuple[ActionAlternative, ...] = ()
    eligible_hypothesis_ids: Tuple[str, ...] = ()
    reference_hypothesis_ids: Tuple[str, ...] = ()
    selected_rule_id: str = ""
    portfolio_constraint_rule_ids: Tuple[str, ...] = ()
    execution_constraint_rule_ids: Tuple[str, ...] = ()
    data_quality_rule_ids: Tuple[str, ...] = ()
    review_level: str = ""
    data_state: str = ""
    change_state: str = ""
    conflict_state: str = ""
    missing_data: Tuple[str, ...] = ()
    next_checks: Tuple[str, ...] = ()
    reversal_conditions: Tuple[str, ...] = ()
    judgement_blocked: bool = False
    graph_trace_complete: bool = False
    evidence_state: str = ""
    hypothesis_state: str = ""
    action_state: str = ""
    ai_state: str = ""
    version: str = DECISION_SYNTHESIS_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "DecisionSynthesis":
        payload = dict(value or {})
        return cls(
            synthesis_id=str(payload.get("synthesis_id") or payload.get("synthesisId") or ""),
            account_id=str(payload.get("account_id") or payload.get("accountId") or ""),
            symbol=str(payload.get("symbol") or "").upper(),
            source_abox_snapshot_id=str(payload.get("source_abox_snapshot_id") or payload.get("sourceAboxSnapshotId") or ""),
            inference_generation_id=str(payload.get("inference_generation_id") or payload.get("inferenceGenerationId") or ""),
            graph_candidate_action=str(payload.get("graph_candidate_action") or payload.get("graphCandidateAction") or "").upper(),
            investment_view_action=str(payload.get("investment_view_action") or payload.get("investmentViewAction") or "").upper(),
            execution_action=str(payload.get("execution_action") or payload.get("executionAction") or "NO_ACTION").upper(),
            execution_disposition=str(payload.get("execution_disposition") or payload.get("executionDisposition") or "judgement-blocked"),
            decision_effect=str(payload.get("decision_effect") or payload.get("decisionEffect") or "").lower(),
            decision_disposition=str(payload.get("decision_disposition") or payload.get("decisionDisposition") or "").lower(),
            action_authority=str(payload.get("action_authority") or payload.get("actionAuthority") or "observe").lower(),
            allowed_actions=_texts(payload.get("allowed_actions") or payload.get("allowedActions"), uppercase=True),
            blocked_actions=_texts(payload.get("blocked_actions") or payload.get("blockedActions"), uppercase=True),
            alternatives=tuple(
                ActionAlternative.from_dict(item)
                for item in payload.get("alternatives") or []
                if isinstance(item, Mapping)
            ),
            eligible_hypothesis_ids=_texts(payload.get("eligible_hypothesis_ids") or payload.get("eligibleHypothesisIds")),
            reference_hypothesis_ids=_texts(payload.get("reference_hypothesis_ids") or payload.get("referenceHypothesisIds")),
            selected_rule_id=str(payload.get("selected_rule_id") or payload.get("selectedRuleId") or ""),
            portfolio_constraint_rule_ids=_texts(payload.get("portfolio_constraint_rule_ids") or payload.get("portfolioConstraintRuleIds")),
            execution_constraint_rule_ids=_texts(payload.get("execution_constraint_rule_ids") or payload.get("executionConstraintRuleIds")),
            data_quality_rule_ids=_texts(payload.get("data_quality_rule_ids") or payload.get("dataQualityRuleIds")),
            review_level=str(payload.get("review_level") or payload.get("reviewLevel") or ""),
            data_state=str(payload.get("data_state") or payload.get("dataState") or ""),
            change_state=str(payload.get("change_state") or payload.get("changeState") or ""),
            conflict_state=str(payload.get("conflict_state") or payload.get("conflictState") or ""),
            missing_data=_texts(payload.get("missing_data") or payload.get("missingData")),
            next_checks=_texts(payload.get("next_checks") or payload.get("nextChecks")),
            reversal_conditions=_texts(payload.get("reversal_conditions") or payload.get("reversalConditions")),
            judgement_blocked=bool(payload.get("judgement_blocked") or payload.get("judgementBlocked")),
            graph_trace_complete=bool(payload.get("graph_trace_complete") or payload.get("graphTraceComplete")),
            evidence_state=str(payload.get("evidence_state") or payload.get("evidenceState") or ""),
            hypothesis_state=str(payload.get("hypothesis_state") or payload.get("hypothesisState") or ""),
            action_state=str(payload.get("action_state") or payload.get("actionState") or ""),
            ai_state=str(payload.get("ai_state") or payload.get("aiState") or ""),
            version=str(payload.get("version") or DECISION_SYNTHESIS_VERSION),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in [
            "allowed_actions", "blocked_actions", "eligible_hypothesis_ids",
            "reference_hypothesis_ids", "missing_data", "next_checks",
            "reversal_conditions", "portfolio_constraint_rule_ids",
            "execution_constraint_rule_ids", "data_quality_rule_ids",
        ]:
            payload[key] = list(payload[key])
        payload["alternatives"] = [item.to_dict() for item in self.alternatives]
        return payload


@dataclass(frozen=True)
class ConditionEvidence:
    """One persisted rule condition with the observation that satisfied it."""

    condition_id: str
    kind: str = ""
    role: str = "required"
    field: str = ""
    operator: str = ""
    expected_value: object = None
    observed_value: object = None
    result: str = "matched"
    relation_type: str = ""
    relation_id: str = ""
    target_id: str = ""
    target_kind: str = ""
    source: str = ""
    source_as_of: str = ""
    freshness: str = ""
    evidence_ids: Tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ConditionEvidence":
        payload = dict(value or {})
        shape = _mapping(payload.get("ruleConditionShape"))
        relation_id = str(payload.get("relationId") or "")
        evidence_ids = _texts(payload.get("evidenceIds") or payload.get("evidence_ids"))
        if relation_id and relation_id not in evidence_ids:
            evidence_ids = _texts((*evidence_ids, relation_id))
        return cls(
            condition_id=str(payload.get("condition_id") or payload.get("conditionId") or shape.get("conditionId") or ""),
            kind=str(payload.get("kind") or shape.get("kind") or ""),
            role=str(payload.get("role") or shape.get("role") or "required"),
            field=str(payload.get("field") or shape.get("field") or ""),
            operator=str(payload.get("operator") or shape.get("operator") or ""),
            expected_value=payload.get("expected_value", payload.get("expectedValue", shape.get("value"))),
            observed_value=payload.get("observed_value", payload.get("observedValue")),
            result=str(payload.get("result") or payload.get("status") or ("matched" if not payload.get("absenceSatisfied") else "absence-matched")),
            relation_type=str(payload.get("relation_type") or payload.get("relationType") or shape.get("relationType") or ""),
            relation_id=relation_id,
            target_id=str(payload.get("target_id") or payload.get("targetId") or ""),
            target_kind=str(payload.get("target_kind") or payload.get("targetKind") or shape.get("targetKind") or ""),
            source=str(payload.get("source") or payload.get("observationSource") or ""),
            source_as_of=str(payload.get("source_as_of") or payload.get("sourceAsOf") or payload.get("observedAt") or ""),
            freshness=str(payload.get("freshness") or payload.get("freshnessStatus") or ""),
            evidence_ids=evidence_ids,
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["evidence_ids"] = list(self.evidence_ids)
        return payload


@dataclass(frozen=True)
class PremiseLineage:
    """Exact shared-world lineage used by an account-overlay rule match."""

    shared_generation_id: str = ""
    source_abox_snapshot_id: str = ""
    premise_proof_id: str = ""
    original_rule_id: str = ""
    evidence_ids: Tuple[str, ...] = ()
    status: str = "unavailable"

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "PremiseLineage":
        payload = dict(value or {})
        generation_id = str(payload.get("shared_generation_id") or payload.get("sharedGenerationId") or payload.get("sharedPremiseInferenceGenerationId") or "")
        snapshot_id = str(payload.get("source_abox_snapshot_id") or payload.get("sourceAboxSnapshotId") or payload.get("sharedPremiseSourceAboxSnapshotId") or "")
        proof_id = str(payload.get("premise_proof_id") or payload.get("premiseProofId") or "")
        original_rule_id = str(payload.get("original_rule_id") or payload.get("originalRuleId") or payload.get("sourceRuleId") or "")
        evidence_ids = _texts(payload.get("evidence_ids") or payload.get("evidenceIds"))
        status = str(payload.get("status") or ("available" if generation_id or snapshot_id or proof_id else "unavailable"))
        return cls(generation_id, snapshot_id, proof_id, original_rule_id, evidence_ids, status)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["evidence_ids"] = list(self.evidence_ids)
        return payload


@dataclass(frozen=True)
class RuleMatchProof:
    """Immutable proof that links a matched rule to concrete ABox evidence."""

    proof_id: str
    rule_id: str
    trace_id: str = ""
    subject_id: str = ""
    matched: bool = True
    conditions: Tuple[ConditionEvidence, ...] = ()
    evidence_ids: Tuple[str, ...] = ()
    premise_lineage: PremiseLineage = field(default_factory=PremiseLineage)
    status: str = "available"

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RuleMatchProof":
        payload = dict(value or {})
        conditions = tuple(
            ConditionEvidence.from_dict(item)
            for item in payload.get("conditions") or payload.get("matchedConditions") or []
            if isinstance(item, Mapping)
        )
        rule_id = str(payload.get("rule_id") or payload.get("ruleId") or "")
        trace_id = str(payload.get("trace_id") or payload.get("traceId") or payload.get("id") or "")
        evidence_ids = _texts(payload.get("evidence_ids") or payload.get("evidenceIds") or payload.get("evidenceRelationIds"))
        evidence_ids = _texts((*evidence_ids, *(evidence_id for condition in conditions for evidence_id in condition.evidence_ids)))
        proof_id = str(payload.get("proof_id") or payload.get("proofId") or "") or _stable_id("rule-proof", rule_id, trace_id, evidence_ids)
        lineage = payload.get("premise_lineage") or payload.get("premiseLineage") or {}
        if not lineage:
            lineage = next((
                item.get("premiseLineage")
                for item in payload.get("conditions") or payload.get("matchedConditions") or []
                if isinstance(item, Mapping) and isinstance(item.get("premiseLineage"), Mapping)
            ), {})
        return cls(
            proof_id=proof_id,
            rule_id=rule_id,
            trace_id=trace_id,
            subject_id=str(payload.get("subject_id") or payload.get("subjectId") or payload.get("sourceId") or ""),
            matched=bool(payload.get("matched", True)),
            conditions=conditions,
            evidence_ids=evidence_ids,
            premise_lineage=PremiseLineage.from_dict(lineage if isinstance(lineage, Mapping) else {}),
            status=str(payload.get("status") or ("available" if conditions or evidence_ids else "legacy-unavailable")),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["conditions"] = [item.to_dict() for item in self.conditions]
        payload["evidence_ids"] = list(self.evidence_ids)
        payload["premise_lineage"] = self.premise_lineage.to_dict()
        return payload


@dataclass(frozen=True)
class RuleEvaluationRecord:
    """One rule evaluation retained with the reasoning generation that ran it."""

    evaluation_id: str
    account_id: str
    rule_id: str
    source_abox_snapshot_id: str = ""
    inference_generation_id: str = ""
    matched: bool = True
    selected: bool = False
    decision_eligible: bool = False
    failure_reason: str = ""
    proof: RuleMatchProof = field(default_factory=lambda: RuleMatchProof("", "", status="legacy-unavailable"))
    version: str = RULE_EVALUATION_RECORD_VERSION

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RuleEvaluationRecord":
        payload = dict(value or {})
        proof_value = payload.get("proof") or payload.get("matchProof") or {}
        proof = RuleMatchProof.from_dict(proof_value if isinstance(proof_value, Mapping) else {})
        return cls(
            evaluation_id=str(payload.get("evaluation_id") or payload.get("evaluationId") or ""),
            account_id=str(payload.get("account_id") or payload.get("accountId") or ""),
            rule_id=str(payload.get("rule_id") or payload.get("ruleId") or proof.rule_id),
            source_abox_snapshot_id=str(payload.get("source_abox_snapshot_id") or payload.get("sourceAboxSnapshotId") or ""),
            inference_generation_id=str(payload.get("inference_generation_id") or payload.get("inferenceGenerationId") or ""),
            matched=bool(payload.get("matched", True)),
            selected=bool(payload.get("selected")),
            decision_eligible=bool(payload.get("decision_eligible") or payload.get("decisionEligible")),
            failure_reason=str(payload.get("failure_reason") or payload.get("failureReason") or ""),
            proof=proof,
            version=str(payload.get("version") or RULE_EVALUATION_RECORD_VERSION),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["proof"] = self.proof.to_dict()
        return payload


def rule_evaluation_records_from_projection_results(
    projection_results: Mapping[str, object],
) -> Tuple[RuleEvaluationRecord, ...]:
    """Project already-returned InferenceBox traces into compact audit records."""

    records = []
    seen = set()
    for account_id, raw_projection in (projection_results or {}).items():
        projection = _mapping(raw_projection)
        inference = _mapping(projection.get("inferenceBox"))
        snapshot_id = str(inference.get("sourceAboxSnapshotId") or projection.get("sourceAboxSnapshotId") or "")
        generation_id = str(inference.get("inferenceGenerationId") or projection.get("inferenceGenerationId") or "")
        shared_lineage = {
            "sharedGenerationId": inference.get("sharedPremiseInferenceGenerationId"),
            "sourceAboxSnapshotId": inference.get("sharedPremiseSourceAboxSnapshotId"),
        }
        for raw_trace in inference.get("traces") or []:
            trace = _mapping(raw_trace)
            properties = _mapping(trace.get("properties"))
            merged = {**properties, **trace}
            rule_id = str(merged.get("ruleId") or merged.get("sourceRuleId") or "")
            trace_id = str(merged.get("traceId") or merged.get("id") or merged.get("inferenceTraceId") or "")
            if not rule_id:
                continue
            key = (str(account_id), rule_id, trace_id)
            if key in seen:
                continue
            seen.add(key)
            condition_lineage = next((
                item.get("premiseLineage")
                for item in merged.get("matchedConditions") or []
                if isinstance(item, Mapping) and isinstance(item.get("premiseLineage"), Mapping)
            ), {})
            lineage = {
                **condition_lineage,
                **shared_lineage,
                "premiseProofId": merged.get("premiseProofId"),
                "originalRuleId": merged.get("originalRuleId") or merged.get("sourceRuleId"),
                "evidenceIds": merged.get("sharedPremiseEvidenceIds") or [],
            }
            proof = RuleMatchProof.from_dict({
                **merged,
                "ruleId": rule_id,
                "traceId": trace_id,
                "conditions": merged.get("matchedConditions") or [],
                "premiseLineage": lineage,
            })
            evaluation_id = _stable_id("rule-evaluation", account_id, generation_id, rule_id, trace_id)
            records.append(RuleEvaluationRecord(
                evaluation_id=evaluation_id,
                account_id=str(account_id),
                rule_id=rule_id,
                source_abox_snapshot_id=snapshot_id,
                inference_generation_id=generation_id,
                matched=True,
                selected=bool(merged.get("selected") or merged.get("isSelected")),
                decision_eligible=bool(merged.get("decisionEligible") or merged.get("evidenceUsableForJudgement")),
                proof=proof,
            ))
    return tuple(records)


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
    rule_evaluations: Tuple[RuleEvaluationRecord, ...] = ()
    duration_ms: int = 0
    version: str = INFERENCE_RESULT_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in [
            "source_abox_snapshot_ids", "inference_generation_ids",
            "verified_account_ids", "failed_account_ids",
        ]:
            payload[key] = list(payload[key])
        payload["rule_evaluations"] = [item.to_dict() for item in self.rule_evaluations]
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
            rule_evaluations=tuple(
                RuleEvaluationRecord.from_dict(item)
                for item in payload.get("rule_evaluations") or payload.get("ruleEvaluations") or []
                if isinstance(item, Mapping)
            ),
            duration_ms=max(0, int(payload.get("duration_ms") or payload.get("durationMs") or 0)),
            version=str(payload.get("version") or INFERENCE_RESULT_VERSION),
        )


@dataclass(frozen=True)
class AIJudgmentResult:
    request_id: str
    result_id: str
    action: str
    confidence: Optional[float]
    selected_hypothesis_id: str
    validation_state: str
    rationale: str
    supporting_evidence_ids: Tuple[str, ...] = ()
    opposing_evidence_ids: Tuple[str, ...] = ()
    rejected_candidate_reason: str = ""
    next_observations: Tuple[str, ...] = ()
    reversal_conditions: Tuple[str, ...] = ()
    follow_up_conditions: Tuple[Dict[str, object], ...] = ()
    unsupported_follow_ups: Tuple[Dict[str, object], ...] = ()
    reviewed_hypothesis_ids: Tuple[str, ...] = ()
    hypothesis_reviews: Tuple[Dict[str, object], ...] = ()
    comparison_state: str = ""
    model: str = ""
    reasoning_effort: str = ""
    latency_ms: int = 0
    version: str = AI_JUDGMENT_RESULT_VERSION

    @classmethod
    def from_result(cls, result, response: Mapping[str, object]) -> "AIJudgmentResult":
        payload = dict(response or {})
        comparison = payload.get("hypothesisComparison")
        comparison = dict(comparison or {}) if isinstance(comparison, Mapping) else {}
        selected_id = str(
            payload.get("selectedHypothesisId")
            or comparison.get("selectedHypothesisId")
            or ""
        )
        reviews = [
            dict(item)
            for item in (
                comparison.get("hypotheses")
                or payload.get("hypotheses")
                or []
            )
            if isinstance(item, Mapping)
        ]
        selected_review = next((
            item for item in reviews
            if str(item.get("hypothesisId") or "") == selected_id
        ), {})
        reviewed_hypothesis_ids = _texts(
            item.get("hypothesisId")
            for item in reviews
            if item.get("hypothesisId")
        )
        confidence = (
            payload.get("confidence")
            if payload.get("confidence") not in (None, "")
            else payload.get("judgmentStrength")
        )
        try:
            confidence_value = float(confidence) if confidence not in (None, "") else None
        except (TypeError, ValueError):
            confidence_value = None
        return cls(
            request_id=str(getattr(result, "request_id", "") or ""),
            result_id=str(getattr(result, "result_id", "") or ""),
            action=str(payload.get("action") or "").upper(),
            confidence=confidence_value,
            selected_hypothesis_id=selected_id,
            validation_state=str(
                payload.get("validationState")
                or getattr(result, "validation_state", "")
                or "conditional"
            ),
            rationale=str(payload.get("summary") or payload.get("opinion") or ""),
            supporting_evidence_ids=_texts(
                payload.get("supportingEvidenceIds")
                or payload.get("evidenceIds")
                or selected_review.get("reviewedSupportingEvidenceIds")
                or selected_review.get("supportingEvidenceIds")
            ),
            opposing_evidence_ids=_texts(
                payload.get("opposingEvidenceIds")
                or payload.get("counterEvidenceIds")
                or selected_review.get("reviewedCounterEvidenceIds")
                or selected_review.get("counterEvidenceIds")
            ),
            rejected_candidate_reason=str(
                payload.get("candidateAdjustmentReason")
                or payload.get("rejectedCandidateReason")
                or payload.get("disagreementReason")
                or ""
            ),
            next_observations=_texts(payload.get("nextChecks") or payload.get("nextObservations")),
            reversal_conditions=_texts(
                payload.get("reversalConditions")
                or ([payload.get("invalidationCondition")] if payload.get("invalidationCondition") else [])
            ),
            follow_up_conditions=tuple(
                dict(item)
                for item in payload.get("followUpConditions") or []
                if isinstance(item, Mapping)
            ),
            unsupported_follow_ups=tuple(
                dict(item)
                for item in payload.get("unsupportedFollowUps") or []
                if isinstance(item, Mapping)
            ),
            reviewed_hypothesis_ids=reviewed_hypothesis_ids,
            hypothesis_reviews=tuple(reviews),
            comparison_state=str(
                comparison.get("hypothesisComparisonState")
                or comparison.get("status")
                or payload.get("hypothesisComparisonState")
                or ""
            ),
            model=str(getattr(result, "model", "") or ""),
            reasoning_effort=str(getattr(result, "reasoning_effort", "") or ""),
            latency_ms=max(0, int(getattr(result, "latency_ms", 0) or 0)),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        for key in [
            "supporting_evidence_ids", "opposing_evidence_ids",
            "next_observations", "reversal_conditions", "reviewed_hypothesis_ids",
        ]:
            payload[key] = list(payload[key])
        payload["hypothesis_reviews"] = [dict(item) for item in self.hypothesis_reviews]
        payload["follow_up_conditions"] = [dict(item) for item in self.follow_up_conditions]
        payload["unsupported_follow_ups"] = [dict(item) for item in self.unsupported_follow_ups]
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AIJudgmentResult":
        payload = dict(value or {})
        try:
            confidence = (
                float(payload.get("confidence"))
                if payload.get("confidence") not in (None, "")
                else None
            )
        except (TypeError, ValueError):
            confidence = None
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
            follow_up_conditions=tuple(
                dict(item)
                for item in payload.get("follow_up_conditions") or payload.get("followUpConditions") or []
                if isinstance(item, Mapping)
            ),
            unsupported_follow_ups=tuple(
                dict(item)
                for item in payload.get("unsupported_follow_ups") or payload.get("unsupportedFollowUps") or []
                if isinstance(item, Mapping)
            ),
            reviewed_hypothesis_ids=_texts(
                payload.get("reviewed_hypothesis_ids") or payload.get("reviewedHypothesisIds")
            ),
            hypothesis_reviews=tuple(
                dict(item)
                for item in payload.get("hypothesis_reviews") or payload.get("hypothesisReviews") or []
                if isinstance(item, Mapping)
            ),
            comparison_state=str(payload.get("comparison_state") or payload.get("comparisonState") or ""),
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
