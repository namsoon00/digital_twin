from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Dict, Iterable, List, Optional

from .decision_evidence_contract import (
    decision_eligible_hypothesis_payload,
    hypothesis_decision_eligibility,
    hypothesis_set_evidence_summary,
)
from .hypothesis_scoping import (
    ACCOUNT_ONLY_SCOPE,
    HYPOTHESIS_SCOPE_VERSION,
    MARKET_SHARED_SCOPE,
    MIXED_SCOPE,
    SYSTEM_SAFETY_SCOPE,
    UNVERIFIED_SCOPE,
    inference_scope_assessment,
)
from .hypothesis_catalog import hypothesis_family_definition
from .hypothesis_outcome_contract import (
    HYPOTHESIS_OUTCOME_CONTRACT_VERSION,
    default_directional_criteria,
    merge_outcome_contracts,
    outcome_contract_fingerprint,
)
from .ontology_decision_state import DATA_STATES, REVIEW_LEVELS, VALIDATION_STATES
from .ontology_rule_knowledge import rule_knowledge_basis_from_rows
from .rule_claim_contract import (
    RuleClaimContract,
    hypothesis_qualification,
    resolved_rule_claim_contract,
    rule_claim_coverage,
)
from .ontology_schema import tbox_fingerprint
from .ontology_worlds import market_world
from .investment_reasoning_detail import reasoning_detail_snapshot


INVESTMENT_BRAIN_VERSION = "ontology-investment-brain-v5"
HYPOTHESIS_SET_VERSION = "typedb-causal-hypotheses-v6"
SYSTEM_ABSTENTION_TEMPLATE_ID = "hypothesis-template:system.evidence-sufficiency.v1"
META_INFERENCE_RELATION_TYPES = {
    "EXPLAINED_BY_TRACE",
    "HAS_INFERENCE_TRACE",
    "HAS_SIGNAL_CONFLICT",
    "HAS_WHY_NOW",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_investment_timestamp(value: object) -> Optional[datetime]:
    """Parse persisted decision timestamps into an aware UTC datetime.

    Early notification records used the user-facing ``KST`` display format,
    while newer records use ISO-8601. Outcome evaluation must treat both as
    the same temporal contract instead of comparing their raw strings.
    """
    text = str(value or "").strip()
    if not text:
        return None
    suffix_timezones = {
        " KST": timezone(timedelta(hours=9)),
        " UTC": timezone.utc,
        " GMT": timezone.utc,
    }
    timezone_hint = None
    upper = text.upper()
    for suffix, zone in suffix_timezones.items():
        if upper.endswith(suffix):
            text = text[:-len(suffix)].strip()
            timezone_hint = zone
            break
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone_hint or timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_investment_timestamp(value: object) -> str:
    parsed = parse_investment_timestamp(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else ""


def stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(item or "").strip() for item in parts)
    return prefix + ":" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def reasoning_case_decision_episode_id(
    case_id: object,
    account_id: object,
    symbol: object,
) -> str:
    """Keep the V2 reasoning case and notification projection on one episode."""

    return stable_id(
        "decision-episode-v2",
        str(case_id or "").strip(),
        str(account_id or "").strip(),
        str(symbol or "").strip().upper(),
    )


def scoped_decision_follow_ups(
    episode_id: str,
    conditions: Iterable[Dict[str, object]],
) -> List[Dict[str, object]]:
    """Give a reusable condition meaning an episode-owned persistence ID."""

    scoped = []
    for item in conditions or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        semantic_id = str(row.get("sourceConditionId") or row.get("conditionId") or "").strip()
        if not semantic_id:
            semantic_id = stable_id(
                "follow-up-meaning",
                row.get("field"),
                row.get("operator"),
                row.get("threshold"),
                row.get("purpose"),
            )
        row["sourceConditionId"] = semantic_id
        row["conditionId"] = stable_id("decision-follow-up", episode_id, semantic_id)
        scoped.append(row)
    return scoped


def unique_texts(values: Iterable[object], limit: int = 12) -> List[str]:
    rows: List[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in rows:
            rows.append(text)
        if len(rows) >= limit:
            break
    return rows


HYPOTHESIS_EVIDENCE_STATES = ("supported", "contested", "unresolved", "blocked")
HYPOTHESIS_EVIDENCE_STATE_LABELS = {
    "supported": "현재 근거로 확인됨",
    "contested": "반대 근거가 함께 있음",
    "unresolved": "추가 확인 필요",
    "blocked": "자료 문제로 판단 보류",
}
HYPOTHESIS_REVIEW_VERDICTS = ("supported", "weakened", "rejected", "unresolved", "unreviewed")
HYPOTHESIS_COMPARISON_STATES = ("completed", "partial", "incomplete", "fallback", "invalid-selection", "unavailable")


def known_state(value: object, allowed: Iterable[str], fallback: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in set(allowed) else fallback


@dataclass(frozen=True)
class InvestmentQuestion:
    question_id: str
    text: str
    intent: str = "investment-decision"
    subject_symbol: str = ""
    subject_name: str = ""
    horizon: str = "multi-horizon"
    account_id: str = ""
    asked_at: str = field(default_factory=utc_now_iso)
    source: str = "user"

    @classmethod
    def create(
        cls,
        text: str,
        subject_symbol: str = "",
        subject_name: str = "",
        account_id: str = "",
        asked_at: str = "",
        source: str = "user",
    ):
        clean = " ".join(str(text or "").split())
        stamp = asked_at or utc_now_iso()
        return cls(
            question_id=stable_id("investment-question", account_id, subject_symbol, clean, stamp),
            text=clean,
            intent=question_intent(clean),
            subject_symbol=str(subject_symbol or "").upper().strip(),
            subject_name=str(subject_name or "").strip(),
            horizon=question_horizon(clean),
            account_id=str(account_id or "").strip(),
            asked_at=stamp,
            source=str(source or "user"),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return camelize(payload)


@dataclass(frozen=True)
class ResearchTask:
    task_id: str
    question: str
    purpose: str
    required_evidence_types: List[str] = field(default_factory=list)
    related_hypothesis_ids: List[str] = field(default_factory=list)
    source_types: List[str] = field(default_factory=list)
    max_age_minutes: int = 180
    decision_relevance: str = "supporting"
    execution_mode: str = "cache-first-on-demand"
    result_evidence_ids: List[str] = field(default_factory=list)
    priority: int = 50
    status: str = "ready"

    def to_dict(self) -> Dict[str, object]:
        return camelize(asdict(self))


@dataclass(frozen=True)
class ResearchPlan:
    plan_id: str
    question_id: str
    tasks: List[ResearchTask] = field(default_factory=list)
    unresolved_questions: List[str] = field(default_factory=list)
    max_rounds: int = 2
    status: str = "ready"
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, object]:
        payload = camelize(asdict(self))
        payload["tasks"] = [item.to_dict() for item in self.tasks]
        return payload


@dataclass(frozen=True)
class HypothesisTemplate:
    template_id: str
    label: str
    version: str
    source_rule_ids: List[str]
    stance: str = "context"
    required_evidence_types: List[str] = field(default_factory=list)
    causal_path_pattern: List[str] = field(default_factory=list)
    approval_status: str = "approved-active"
    source: str = "typedb-native-rule"
    theory_family: str = ""
    thesis_family: str = ""
    knowledge_basis: Dict[str, object] = field(default_factory=dict)
    prediction_target: str = ""
    expected_direction: str = ""
    expected_outcome: str = ""
    default_horizon: str = ""
    competing_family_ids: List[str] = field(default_factory=list)
    outcome_metric: str = ""
    falsification_contract: str = ""
    claim_contract: Dict[str, object] = field(default_factory=dict)
    qualification: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return camelize(asdict(self))


@dataclass(frozen=True)
class HypothesisFamily:
    """A stable causal family behind one current-generation candidate.

    A family is not a new investment rule or a derived market fact. It is a
    durable identity for equivalent TypeDB rule paths so AI comparison does
    not spend its candidate budget on duplicated explanations.
    """

    family_id: str
    label: str
    causal_signature: str
    stance: str = "context"
    horizon: str = "multi-horizon"
    source_rule_ids: List[str] = field(default_factory=list)
    candidate_hypothesis_ids: List[str] = field(default_factory=list)
    source: str = "typedb-structural-signature"
    merged_rule_count: int = 0
    scope_state: str = UNVERIFIED_SCOPE
    market_hypothesis_id: str = ""
    account_overlay_ids: List[str] = field(default_factory=list)
    theory_family: str = ""
    thesis_family: str = ""

    def to_dict(self) -> Dict[str, object]:
        return camelize(asdict(self))


@dataclass(frozen=True)
class MarketHypothesis:
    """A non-actionable, account-independent causal hypothesis identity.

    These records remain in a PortfolioWorld decision episode as references.
    They deliberately do not turn a private decision or action into a
    MarketWorld fact. Identical market paths therefore share one deterministic
    identity across accounts without leaking account context.
    """

    market_hypothesis_id: str
    market_world_id: str
    market_id: str
    subject_symbol: str
    horizon: str
    causal_signature: str
    stance: str = "context"
    source_rule_ids: List[str] = field(default_factory=list)
    market_condition_ids: List[str] = field(default_factory=list)
    market_relation_types: List[str] = field(default_factory=list)
    source: str = "typedb-market-scope-projection"
    scope_state: str = MARKET_SHARED_SCOPE
    scope_version: str = HYPOTHESIS_SCOPE_VERSION

    def to_dict(self) -> Dict[str, object]:
        return camelize(asdict(self))


@dataclass(frozen=True)
class AccountHypothesisOverlay:
    """Private account context applied to a hypothesis family.

    It records ownership and action constraints only. TypeDB remains the
    source of the actual inference path and this object never creates a new
    investment action in Python.
    """

    account_overlay_id: str
    account_id: str
    portfolio_world_id: str
    family_id: str
    scope_state: str
    market_hypothesis_id: str = ""
    target_roles: List[str] = field(default_factory=list)
    action_policies: List[str] = field(default_factory=list)
    allowed_actions: List[str] = field(default_factory=list)
    blocked_actions: List[str] = field(default_factory=list)
    account_condition_ids: List[str] = field(default_factory=list)
    account_fields: List[str] = field(default_factory=list)
    account_relation_types: List[str] = field(default_factory=list)
    account_target_kinds: List[str] = field(default_factory=list)
    source_rule_ids: List[str] = field(default_factory=list)
    source: str = "typedb-account-context-projection"
    scope_version: str = HYPOTHESIS_SCOPE_VERSION

    def to_dict(self) -> Dict[str, object]:
        return camelize(asdict(self))


@dataclass(frozen=True)
class InvestmentHypothesis:
    hypothesis_id: str
    template_id: str
    template_label: str
    claim: str
    stance: str
    horizon: str
    evidence_state: str
    evidence_state_label: str
    supporting_evidence_ids: List[str] = field(default_factory=list)
    counter_evidence_ids: List[str] = field(default_factory=list)
    supporting_rule_ids: List[str] = field(default_factory=list)
    counter_rule_ids: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    invalidation_conditions: List[str] = field(default_factory=list)
    causal_path_ids: List[str] = field(default_factory=list)
    required_evidence_types: List[str] = field(default_factory=list)
    approval_status: str = "approved-active"
    verification_status: str = "unverified-current-generation"
    status: str = "candidate"
    historical_calibration: Dict[str, object] = field(default_factory=dict)
    family_id: str = ""
    causal_signature: str = ""
    family_source: str = "typedb-structural-signature"
    merged_rule_count: int = 1
    scope_state: str = UNVERIFIED_SCOPE
    scope_version: str = HYPOTHESIS_SCOPE_VERSION
    market_hypothesis_id: str = ""
    market_world_id: str = ""
    market_id: str = ""
    subject_symbol: str = ""
    market_causal_signature: str = ""
    market_condition_ids: List[str] = field(default_factory=list)
    market_relation_types: List[str] = field(default_factory=list)
    account_hypothesis_overlay_id: str = ""
    account_id: str = ""
    portfolio_world_id: str = ""
    account_condition_ids: List[str] = field(default_factory=list)
    account_fields: List[str] = field(default_factory=list)
    account_relation_types: List[str] = field(default_factory=list)
    account_target_kinds: List[str] = field(default_factory=list)
    target_roles: List[str] = field(default_factory=list)
    action_policies: List[str] = field(default_factory=list)
    allowed_actions: List[str] = field(default_factory=list)
    blocked_actions: List[str] = field(default_factory=list)
    theory_family: str = ""
    thesis_family: str = ""
    knowledge_basis: Dict[str, object] = field(default_factory=dict)
    prediction_target: str = ""
    expected_direction: str = ""
    expected_outcome: str = ""
    competing_family_ids: List[str] = field(default_factory=list)
    outcome_metric: str = ""
    falsification_contract: str = ""
    inference_generation_id: str = ""
    candidate_action: str = ""
    claim_contract: Dict[str, object] = field(default_factory=dict)
    qualification: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        payload = camelize(asdict(self))
        eligibility = hypothesis_decision_eligibility(payload)
        payload["decisionEligibility"] = eligibility["status"]
        payload["decisionEligibilityReasons"] = eligibility["reasons"]
        return payload


@dataclass(frozen=True)
class DecisionGuardrail:
    """A decision constraint that must never compete as an investment thesis."""

    guardrail_id: str
    guardrail_type: str
    label: str
    reason: str
    status: str = "active"
    required_checks: List[str] = field(default_factory=list)
    missing_data: List[str] = field(default_factory=list)
    blocked_actions: List[str] = field(default_factory=list)
    source: str = "system-safety-policy"
    source_rule_ids: List[str] = field(default_factory=list)
    knowledge_basis: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return camelize(asdict(self))


@dataclass(frozen=True)
class HypothesisReview:
    """The AI's bounded assessment of one graph-derived hypothesis.

    The hypothesis and its graph evidence remain immutable inputs.  A review
    can only reference those inputs; it cannot introduce a new causal path or
    evidence identifier into the decision record.
    """

    hypothesis_id: str
    verdict: str = "unreviewed"
    reasoning: str = ""
    reviewed_supporting_evidence_ids: List[str] = field(default_factory=list)
    reviewed_counter_evidence_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return camelize(asdict(self))


@dataclass(frozen=True)
class HypothesisComparisonAudit:
    reviews: List[HypothesisReview] = field(default_factory=list)
    selected_hypothesis_id: str = ""
    comparison_state: str = "unavailable"
    selection_source: str = "not-selected"
    invalid_hypothesis_ids: List[str] = field(default_factory=list)
    invalid_evidence_ids: List[str] = field(default_factory=list)
    duplicate_hypothesis_ids: List[str] = field(default_factory=list)
    unreviewed_hypothesis_ids: List[str] = field(default_factory=list)
    abstained: bool = False
    abstention_reason: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = camelize(asdict(self))
        payload["reviews"] = [item.to_dict() for item in self.reviews]
        return payload


@dataclass(frozen=True)
class HypothesisSet:
    hypothesis_set_id: str
    subject_symbol: str
    question_id: str
    hypotheses: List[InvestmentHypothesis]
    comparison_required: bool = True
    minimum_comparison_count: int = 1
    comparison_policy: str = "primary-counter-or-explicit-gap"
    inference_generation_id: str = ""
    families: List[HypothesisFamily] = field(default_factory=list)
    market_hypotheses: List[MarketHypothesis] = field(default_factory=list)
    account_overlays: List[AccountHypothesisOverlay] = field(default_factory=list)
    decision_guardrails: List[DecisionGuardrail] = field(default_factory=list)
    scope_version: str = HYPOTHESIS_SCOPE_VERSION
    created_at: str = field(default_factory=utc_now_iso)
    version: str = HYPOTHESIS_SET_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = camelize(asdict(self))
        payload["hypotheses"] = [item.to_dict() for item in self.hypotheses]
        payload["families"] = [item.to_dict() for item in self.families]
        payload["marketHypotheses"] = [item.to_dict() for item in self.market_hypotheses]
        payload["accountOverlays"] = [item.to_dict() for item in self.account_overlays]
        payload["decisionGuardrails"] = [item.to_dict() for item in self.decision_guardrails]
        payload["decisionEvidenceSummary"] = hypothesis_set_evidence_summary(payload)
        return payload


@dataclass
class ObservedOutcome:
    outcome_id: str
    episode_id: str
    observed_at: str
    price: float = 0.0
    profit_loss_rate: float = 0.0
    price_change_from_decision_pct: float = 0.0
    selected_hypothesis_status: str = "pending"
    contradicted_evidence_ids: List[str] = field(default_factory=list)
    payload: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return camelize(asdict(self))


@dataclass
class DecisionEpisode:
    episode_id: str
    account_id: str
    symbol: str
    subject_name: str
    question: InvestmentQuestion
    hypothesis_set: HypothesisSet
    action: str
    review_level: str
    data_state: str
    validation_state: str
    decision_readiness: str = "conditional"
    selected_hypothesis_id: str = ""
    hypothesis_reviews: List[HypothesisReview] = field(default_factory=list)
    hypothesis_comparison_state: str = "unavailable"
    hypothesis_selection_source: str = "not-selected"
    ai_execution: Dict[str, object] = field(default_factory=dict)
    decision_guardrails: List[Dict[str, object]] = field(default_factory=list)
    decision_abstention: Dict[str, object] = field(default_factory=dict)
    inference_generation_id: str = ""
    portfolio_id: str = ""
    mandate_id: str = ""
    mandate_version: str = ""
    source_abox_snapshot_id: str = ""
    action_plan_id: str = ""
    execution_episode_ids: List[str] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)
    counter_evidence_ids: List[str] = field(default_factory=list)
    unresolved_questions: List[str] = field(default_factory=list)
    decision_summary: str = ""
    investment_view: str = ""
    execution_decision: str = ""
    follow_up_conditions: List[Dict[str, object]] = field(default_factory=list)
    unsupported_follow_ups: List[Dict[str, object]] = field(default_factory=list)
    decided_at: str = field(default_factory=utc_now_iso)
    status: str = "active"
    source: str = "notification-ai"
    facts_at_decision: Dict[str, object] = field(default_factory=dict)
    research_plan: Dict[str, object] = field(default_factory=dict)
    research_audit: Dict[str, object] = field(default_factory=dict)
    outcomes: List[ObservedOutcome] = field(default_factory=list)
    engine_version: str = INVESTMENT_BRAIN_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = camelize(asdict(self))
        payload["engineVersion"] = str(self.engine_version or INVESTMENT_BRAIN_VERSION)
        payload["question"] = self.question.to_dict()
        payload["hypothesisSet"] = self.hypothesis_set.to_dict()
        payload["hypothesisReviews"] = [item.to_dict() for item in self.hypothesis_reviews]
        payload["aiExecution"] = dict(self.ai_execution or {})
        payload["decisionGuardrails"] = [dict(item) for item in self.decision_guardrails]
        payload["decisionAbstention"] = dict(self.decision_abstention or {})
        payload["researchPlan"] = dict(self.research_plan or {})
        payload["researchAudit"] = dict(self.research_audit or {})
        payload["outcomes"] = [item.to_dict() for item in self.outcomes]
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        payload = payload if isinstance(payload, dict) else {}
        question_payload = dict(payload.get("question") or {})
        hypothesis_payload = dict(payload.get("hypothesisSet") or payload.get("hypothesis_set") or {})
        question = InvestmentQuestion(
            question_id=str(question_payload.get("questionId") or question_payload.get("question_id") or ""),
            text=str(question_payload.get("text") or ""),
            intent=str(question_payload.get("intent") or "investment-decision"),
            subject_symbol=str(question_payload.get("subjectSymbol") or question_payload.get("subject_symbol") or ""),
            subject_name=str(question_payload.get("subjectName") or question_payload.get("subject_name") or ""),
            horizon=str(question_payload.get("horizon") or "multi-horizon"),
            account_id=str(question_payload.get("accountId") or question_payload.get("account_id") or ""),
            asked_at=str(question_payload.get("askedAt") or question_payload.get("asked_at") or ""),
            source=str(question_payload.get("source") or "user"),
        )
        hypotheses = []
        for item in hypothesis_payload.get("hypotheses") or []:
            if not isinstance(item, dict):
                continue
            hypotheses.append(InvestmentHypothesis(
                hypothesis_id=str(item.get("hypothesisId") or item.get("hypothesis_id") or ""),
                template_id=str(item.get("templateId") or item.get("template_id") or ""),
                template_label=str(item.get("templateLabel") or item.get("template_label") or ""),
                claim=str(item.get("claim") or ""),
                stance=str(item.get("stance") or "uncertain"),
                horizon=str(item.get("horizon") or "multi-horizon"),
                evidence_state=known_state(
                    item.get("evidenceState") or item.get("evidence_state"),
                    HYPOTHESIS_EVIDENCE_STATES,
                    "unresolved",
                ),
                evidence_state_label=str(
                    item.get("evidenceStateLabel")
                    or item.get("evidence_state_label")
                    or HYPOTHESIS_EVIDENCE_STATE_LABELS["unresolved"]
                ),
                supporting_evidence_ids=list(item.get("supportingEvidenceIds") or item.get("supporting_evidence_ids") or []),
                counter_evidence_ids=list(item.get("counterEvidenceIds") or item.get("counter_evidence_ids") or []),
                supporting_rule_ids=list(item.get("supportingRuleIds") or item.get("supporting_rule_ids") or []),
                counter_rule_ids=list(item.get("counterRuleIds") or item.get("counter_rule_ids") or []),
                assumptions=list(item.get("assumptions") or []),
                invalidation_conditions=list(item.get("invalidationConditions") or item.get("invalidation_conditions") or []),
                causal_path_ids=list(item.get("causalPathIds") or item.get("causal_path_ids") or []),
                required_evidence_types=list(item.get("requiredEvidenceTypes") or item.get("required_evidence_types") or []),
                approval_status=str(item.get("approvalStatus") or item.get("approval_status") or "approved-active"),
                verification_status=str(item.get("verificationStatus") or item.get("verification_status") or "unverified-current-generation"),
                status=str(item.get("status") or "candidate"),
                historical_calibration=dict(
                    item.get("historicalCalibration")
                    or item.get("historical_calibration")
                    or {}
                ) if isinstance(
                    item.get("historicalCalibration") or item.get("historical_calibration") or {},
                    dict,
                ) else {},
                family_id=str(item.get("familyId") or item.get("family_id") or ""),
                causal_signature=str(item.get("causalSignature") or item.get("causal_signature") or ""),
                family_source=str(
                    item.get("familySource")
                    or item.get("family_source")
                    or "typedb-structural-signature"
                ),
                merged_rule_count=int_setting(
                    item.get("mergedRuleCount") or item.get("merged_rule_count"),
                    max(1, len(item.get("supportingRuleIds") or item.get("supporting_rule_ids") or [])),
                    0,
                    1000,
                ),
                scope_state=str(item.get("scopeState") or item.get("scope_state") or UNVERIFIED_SCOPE),
                scope_version=str(item.get("scopeVersion") or item.get("scope_version") or HYPOTHESIS_SCOPE_VERSION),
                market_hypothesis_id=str(item.get("marketHypothesisId") or item.get("market_hypothesis_id") or ""),
                market_world_id=str(item.get("marketWorldId") or item.get("market_world_id") or ""),
                market_id=str(item.get("marketId") or item.get("market_id") or ""),
                subject_symbol=str(item.get("subjectSymbol") or item.get("subject_symbol") or ""),
                market_causal_signature=str(item.get("marketCausalSignature") or item.get("market_causal_signature") or ""),
                market_condition_ids=list(item.get("marketConditionIds") or item.get("market_condition_ids") or []),
                market_relation_types=list(item.get("marketRelationTypes") or item.get("market_relation_types") or []),
                account_hypothesis_overlay_id=str(item.get("accountHypothesisOverlayId") or item.get("account_hypothesis_overlay_id") or ""),
                account_id=str(item.get("accountId") or item.get("account_id") or ""),
                portfolio_world_id=str(item.get("portfolioWorldId") or item.get("portfolio_world_id") or ""),
                account_condition_ids=list(item.get("accountConditionIds") or item.get("account_condition_ids") or []),
                account_fields=list(item.get("accountFields") or item.get("account_fields") or []),
                account_relation_types=list(item.get("accountRelationTypes") or item.get("account_relation_types") or []),
                account_target_kinds=list(item.get("accountTargetKinds") or item.get("account_target_kinds") or []),
                target_roles=list(item.get("targetRoles") or item.get("target_roles") or []),
                action_policies=list(item.get("actionPolicies") or item.get("action_policies") or []),
                allowed_actions=list(item.get("allowedActions") or item.get("allowed_actions") or []),
                blocked_actions=list(item.get("blockedActions") or item.get("blocked_actions") or []),
                theory_family=str(item.get("theoryFamily") or item.get("theory_family") or ""),
                thesis_family=str(item.get("thesisFamily") or item.get("thesis_family") or ""),
                knowledge_basis=dict(item.get("knowledgeBasis") or item.get("knowledge_basis") or {}),
                prediction_target=str(item.get("predictionTarget") or item.get("prediction_target") or ""),
                expected_direction=str(item.get("expectedDirection") or item.get("expected_direction") or ""),
                expected_outcome=str(item.get("expectedOutcome") or item.get("expected_outcome") or ""),
                competing_family_ids=list(item.get("competingFamilyIds") or item.get("competing_family_ids") or []),
                outcome_metric=str(item.get("outcomeMetric") or item.get("outcome_metric") or ""),
                falsification_contract=str(item.get("falsificationContract") or item.get("falsification_contract") or ""),
                inference_generation_id=str(item.get("inferenceGenerationId") or item.get("inference_generation_id") or ""),
                candidate_action=str(item.get("candidateAction") or item.get("candidate_action") or "").strip().upper(),
                claim_contract=dict(item.get("claimContract") or item.get("claim_contract") or {}),
                qualification=dict(item.get("qualification") or {}),
            ))
        families = []
        for item in hypothesis_payload.get("families") or []:
            if not isinstance(item, dict):
                continue
            family_id = str(item.get("familyId") or item.get("family_id") or "").strip()
            if not family_id:
                continue
            families.append(HypothesisFamily(
                family_id=family_id,
                label=str(item.get("label") or family_id),
                causal_signature=str(item.get("causalSignature") or item.get("causal_signature") or ""),
                stance=str(item.get("stance") or "context"),
                horizon=str(item.get("horizon") or "multi-horizon"),
                source_rule_ids=list(item.get("sourceRuleIds") or item.get("source_rule_ids") or []),
                candidate_hypothesis_ids=list(
                    item.get("candidateHypothesisIds")
                    or item.get("candidate_hypothesis_ids")
                    or []
                ),
                source=str(item.get("source") or "typedb-structural-signature"),
                merged_rule_count=int_setting(
                    item.get("mergedRuleCount") or item.get("merged_rule_count"),
                    len(item.get("sourceRuleIds") or item.get("source_rule_ids") or []),
                    0,
                    1000,
                ),
                scope_state=str(item.get("scopeState") or item.get("scope_state") or UNVERIFIED_SCOPE),
                market_hypothesis_id=str(item.get("marketHypothesisId") or item.get("market_hypothesis_id") or ""),
                account_overlay_ids=list(item.get("accountOverlayIds") or item.get("account_overlay_ids") or []),
                theory_family=str(item.get("theoryFamily") or item.get("theory_family") or ""),
                thesis_family=str(item.get("thesisFamily") or item.get("thesis_family") or ""),
            ))
        if not families:
            families = hypothesis_families_from_hypotheses(hypotheses)
        market_hypotheses = []
        for item in hypothesis_payload.get("marketHypotheses") or hypothesis_payload.get("market_hypotheses") or []:
            if not isinstance(item, dict):
                continue
            market_hypothesis_id = str(item.get("marketHypothesisId") or item.get("market_hypothesis_id") or "").strip()
            if not market_hypothesis_id:
                continue
            market_hypotheses.append(MarketHypothesis(
                market_hypothesis_id=market_hypothesis_id,
                market_world_id=str(item.get("marketWorldId") or item.get("market_world_id") or ""),
                market_id=str(item.get("marketId") or item.get("market_id") or ""),
                subject_symbol=str(item.get("subjectSymbol") or item.get("subject_symbol") or ""),
                horizon=str(item.get("horizon") or "multi-horizon"),
                causal_signature=str(item.get("causalSignature") or item.get("causal_signature") or ""),
                stance=str(item.get("stance") or "context"),
                source_rule_ids=list(item.get("sourceRuleIds") or item.get("source_rule_ids") or []),
                market_condition_ids=list(item.get("marketConditionIds") or item.get("market_condition_ids") or []),
                market_relation_types=list(item.get("marketRelationTypes") or item.get("market_relation_types") or []),
                source=str(item.get("source") or "typedb-market-scope-projection"),
                scope_state=str(item.get("scopeState") or item.get("scope_state") or MARKET_SHARED_SCOPE),
                scope_version=str(item.get("scopeVersion") or item.get("scope_version") or HYPOTHESIS_SCOPE_VERSION),
            ))
        if not market_hypotheses:
            market_hypotheses = market_hypotheses_from_hypotheses(hypotheses)
        account_overlays = []
        for item in hypothesis_payload.get("accountOverlays") or hypothesis_payload.get("account_overlays") or []:
            if not isinstance(item, dict):
                continue
            overlay_id = str(item.get("accountOverlayId") or item.get("account_overlay_id") or "").strip()
            if not overlay_id:
                continue
            account_overlays.append(AccountHypothesisOverlay(
                account_overlay_id=overlay_id,
                account_id=str(item.get("accountId") or item.get("account_id") or ""),
                portfolio_world_id=str(item.get("portfolioWorldId") or item.get("portfolio_world_id") or ""),
                family_id=str(item.get("familyId") or item.get("family_id") or ""),
                scope_state=str(item.get("scopeState") or item.get("scope_state") or UNVERIFIED_SCOPE),
                market_hypothesis_id=str(item.get("marketHypothesisId") or item.get("market_hypothesis_id") or ""),
                target_roles=list(item.get("targetRoles") or item.get("target_roles") or []),
                action_policies=list(item.get("actionPolicies") or item.get("action_policies") or []),
                allowed_actions=list(item.get("allowedActions") or item.get("allowed_actions") or []),
                blocked_actions=list(item.get("blockedActions") or item.get("blocked_actions") or []),
                account_condition_ids=list(item.get("accountConditionIds") or item.get("account_condition_ids") or []),
                account_fields=list(item.get("accountFields") or item.get("account_fields") or []),
                account_relation_types=list(item.get("accountRelationTypes") or item.get("account_relation_types") or []),
                account_target_kinds=list(item.get("accountTargetKinds") or item.get("account_target_kinds") or []),
                source_rule_ids=list(item.get("sourceRuleIds") or item.get("source_rule_ids") or []),
                source=str(item.get("source") or "typedb-account-context-projection"),
                scope_version=str(item.get("scopeVersion") or item.get("scope_version") or HYPOTHESIS_SCOPE_VERSION),
            ))
        if not account_overlays:
            account_overlays = account_overlays_from_hypotheses(hypotheses)
        decision_guardrails = []
        for item in hypothesis_payload.get("decisionGuardrails") or hypothesis_payload.get("decision_guardrails") or []:
            if not isinstance(item, dict):
                continue
            guardrail_id = str(item.get("guardrailId") or item.get("guardrail_id") or "").strip()
            if not guardrail_id:
                continue
            decision_guardrails.append(DecisionGuardrail(
                guardrail_id=guardrail_id,
                guardrail_type=str(item.get("guardrailType") or item.get("guardrail_type") or "decision-integrity"),
                label=str(item.get("label") or "판단 안전 제한"),
                reason=str(item.get("reason") or ""),
                status=str(item.get("status") or "active"),
                required_checks=list(item.get("requiredChecks") or item.get("required_checks") or []),
                missing_data=list(item.get("missingData") or item.get("missing_data") or []),
                blocked_actions=list(item.get("blockedActions") or item.get("blocked_actions") or []),
                source=str(item.get("source") or "system-safety-policy"),
                source_rule_ids=list(item.get("sourceRuleIds") or item.get("source_rule_ids") or []),
                knowledge_basis=dict(item.get("knowledgeBasis") or item.get("knowledge_basis") or {}),
            ))
        hypothesis_set = HypothesisSet(
            hypothesis_set_id=str(hypothesis_payload.get("hypothesisSetId") or hypothesis_payload.get("hypothesis_set_id") or ""),
            subject_symbol=str(hypothesis_payload.get("subjectSymbol") or hypothesis_payload.get("subject_symbol") or ""),
            question_id=str(hypothesis_payload.get("questionId") or hypothesis_payload.get("question_id") or question.question_id),
            hypotheses=hypotheses,
            comparison_required=bool(hypothesis_payload.get("comparisonRequired", True)),
            minimum_comparison_count=int(
                hypothesis_payload.get("minimumComparisonCount")
                if hypothesis_payload.get("minimumComparisonCount") not in (None, "")
                else (0 if hypothesis_payload.get("comparisonRequired") is False else 3)
            ),
            comparison_policy=str(
                hypothesis_payload.get("comparisonPolicy")
                or hypothesis_payload.get("comparison_policy")
                or "legacy-fixed-count"
            ),
            inference_generation_id=str(hypothesis_payload.get("inferenceGenerationId") or ""),
            families=families,
            market_hypotheses=market_hypotheses,
            account_overlays=account_overlays,
            decision_guardrails=decision_guardrails,
            scope_version=str(hypothesis_payload.get("scopeVersion") or hypothesis_payload.get("scope_version") or HYPOTHESIS_SCOPE_VERSION),
            created_at=str(hypothesis_payload.get("createdAt") or utc_now_iso()),
            version=str(hypothesis_payload.get("version") or HYPOTHESIS_SET_VERSION),
        )
        reviews = []
        for item in payload.get("hypothesisReviews") or payload.get("hypothesis_reviews") or []:
            if not isinstance(item, dict):
                continue
            reviews.append(HypothesisReview(
                hypothesis_id=str(item.get("hypothesisId") or item.get("hypothesis_id") or ""),
                verdict=known_state(
                    item.get("verdict"),
                    HYPOTHESIS_REVIEW_VERDICTS,
                    "unreviewed",
                ),
                reasoning=str(item.get("reasoning") or ""),
                reviewed_supporting_evidence_ids=list(
                    item.get("reviewedSupportingEvidenceIds")
                    or item.get("reviewed_supporting_evidence_ids")
                    or []
                ),
                reviewed_counter_evidence_ids=list(
                    item.get("reviewedCounterEvidenceIds")
                    or item.get("reviewed_counter_evidence_ids")
                    or []
                ),
            ))
        outcomes = []
        for item in payload.get("outcomes") or []:
            if not isinstance(item, dict):
                continue
            outcomes.append(ObservedOutcome(
                outcome_id=str(item.get("outcomeId") or ""),
                episode_id=str(item.get("episodeId") or ""),
                observed_at=str(item.get("observedAt") or ""),
                price=float(item.get("price") or 0),
                profit_loss_rate=float(item.get("profitLossRate") or 0),
                price_change_from_decision_pct=float(item.get("priceChangeFromDecisionPct") or 0),
                selected_hypothesis_status=str(item.get("selectedHypothesisStatus") or "pending"),
                contradicted_evidence_ids=list(item.get("contradictedEvidenceIds") or []),
                payload=dict(item.get("payload") or {}),
            ))
        return cls(
            episode_id=str(payload.get("episodeId") or payload.get("episode_id") or ""),
            account_id=str(payload.get("accountId") or payload.get("account_id") or ""),
            symbol=str(payload.get("symbol") or "").upper(),
            subject_name=str(payload.get("subjectName") or payload.get("subject_name") or ""),
            question=question,
            hypothesis_set=hypothesis_set,
            action=str(payload.get("action") or "HOLD"),
            review_level=known_state(payload.get("reviewLevel") or payload.get("review_level"), REVIEW_LEVELS, "check"),
            data_state=known_state(payload.get("dataState") or payload.get("data_state"), DATA_STATES, "partial"),
            validation_state=known_state(
                payload.get("validationState") or payload.get("validation_state"),
                VALIDATION_STATES,
                "conditional",
            ),
            decision_readiness=known_state(
                payload.get("decisionReadiness") or payload.get("decision_readiness"),
                {"ready", "conditional", "insufficient"},
                "conditional",
            ),
            selected_hypothesis_id=str(payload.get("selectedHypothesisId") or ""),
            hypothesis_reviews=reviews,
            hypothesis_comparison_state=known_state(
                payload.get("hypothesisComparisonState") or payload.get("hypothesis_comparison_state"),
                HYPOTHESIS_COMPARISON_STATES,
                "unavailable",
            ),
            hypothesis_selection_source=str(
                payload.get("hypothesisSelectionSource")
                or payload.get("hypothesis_selection_source")
                or "not-selected"
            ),
            ai_execution=dict(
                payload.get("aiExecution")
                or payload.get("ai_execution")
                or {}
            ),
            decision_guardrails=[
                dict(item)
                for item in payload.get("decisionGuardrails") or payload.get("decision_guardrails") or []
                if isinstance(item, dict)
            ],
            decision_abstention=dict(
                payload.get("decisionAbstention")
                or payload.get("decision_abstention")
                or {}
            ),
            inference_generation_id=str(payload.get("inferenceGenerationId") or ""),
            portfolio_id=str(payload.get("portfolioId") or payload.get("portfolio_id") or ""),
            mandate_id=str(payload.get("mandateId") or payload.get("mandate_id") or ""),
            mandate_version=str(
                payload.get("mandateVersion")
                or payload.get("mandate_version")
                or payload.get("policyVersion")
                or ""
            ),
            source_abox_snapshot_id=str(
                payload.get("sourceAboxSnapshotId")
                or payload.get("source_abox_snapshot_id")
                or ""
            ),
            action_plan_id=str(payload.get("actionPlanId") or payload.get("action_plan_id") or ""),
            execution_episode_ids=[
                str(item)
                for item in payload.get("executionEpisodeIds") or payload.get("execution_episode_ids") or []
                if str(item or "").strip()
            ],
            evidence_ids=list(payload.get("evidenceIds") or []),
            counter_evidence_ids=list(payload.get("counterEvidenceIds") or []),
            unresolved_questions=list(payload.get("unresolvedQuestions") or []),
            decision_summary=str(payload.get("decisionSummary") or ""),
            investment_view=str(payload.get("investmentView") or payload.get("investment_view") or ""),
            execution_decision=str(payload.get("executionDecision") or payload.get("execution_decision") or ""),
            follow_up_conditions=[
                dict(item)
                for item in payload.get("followUpConditions") or payload.get("follow_up_conditions") or []
                if isinstance(item, dict)
            ],
            unsupported_follow_ups=[
                dict(item)
                for item in payload.get("unsupportedFollowUps") or payload.get("unsupported_follow_ups") or []
                if isinstance(item, dict)
            ],
            decided_at=str(payload.get("decidedAt") or utc_now_iso()),
            status=str(payload.get("status") or "active"),
            source=str(payload.get("source") or "notification-ai"),
            facts_at_decision=dict(payload.get("factsAtDecision") or {}),
            research_plan=dict(payload.get("researchPlan") or {}),
            research_audit=dict(payload.get("researchAudit") or {}),
            outcomes=outcomes,
            engine_version=str(
                payload.get("engineVersion")
                or payload.get("engine_version")
                or INVESTMENT_BRAIN_VERSION
            ),
        )


DECISION_EPISODE_ONTOLOGY_CONTEXT_VERSION = "decision-episode-ontology-context-v1"


def _ontology_context_count(value: object, fallback: int, upper: int) -> int:
    try:
        parsed = int(float(str(value)))
    except (TypeError, ValueError):
        parsed = fallback
    return max(1, min(upper, parsed))


def _ontology_context_text(value: object, limit: int = 480) -> str:
    if isinstance(value, str):
        return value[:max(1, limit)]
    if isinstance(value, (int, float, bool)):
        return str(value)[:max(1, limit)]
    return ""


def _ontology_context_values(value: object, limit: int = 24, text_limit: int = 240) -> List[str]:
    if isinstance(value, str):
        source: Iterable[object] = [value]
    elif isinstance(value, (list, tuple, set)):
        source = value
    else:
        return []
    result: List[str] = []
    for item in source:
        text = _ontology_context_text(item, text_limit).strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= max(1, limit):
            break
    return result


def _ontology_outcome_contract_context(value: object) -> Dict[str, object]:
    """Keep only the RuleBox-owned fields needed to review stored outcomes."""
    contract = value if isinstance(value, dict) else {}
    if not contract:
        return {}
    result = {
        "outcomeHorizonMinutes": list(contract.get("outcomeHorizonMinutes") or [])[:12],
        "requiredObservationDomains": _ontology_context_values(
            contract.get("requiredObservationDomains"),
            limit=12,
        ),
        "minimumIndependentEpisodes": contract.get("minimumIndependentEpisodes"),
        "maximumObservationDelayMinutes": contract.get("maximumObservationDelayMinutes"),
        "verificationFocus": _ontology_context_values(contract.get("verificationFocus"), limit=12),
        "evaluationScope": _ontology_context_text(contract.get("evaluationScope"), 120),
        "contractVersion": _ontology_context_text(contract.get("contractVersion"), 120),
        "selectedHypothesisId": _ontology_context_text(contract.get("selectedHypothesisId"), 180),
        "sourceRuleIds": _ontology_context_values(contract.get("sourceRuleIds")),
        "marketHypothesisId": _ontology_context_text(contract.get("marketHypothesisId"), 180),
        "accountHypothesisOverlayId": _ontology_context_text(contract.get("accountHypothesisOverlayId"), 180),
        "inferenceGenerationId": _ontology_context_text(contract.get("inferenceGenerationId"), 180),
        "contractFingerprint": _ontology_context_text(contract.get("contractFingerprint"), 180),
        "criteriaOrigin": _ontology_context_text(contract.get("criteriaOrigin"), 80),
        "effectiveAt": _ontology_context_text(contract.get("effectiveAt"), 80),
        "marketIndependenceKey": _ontology_context_text(contract.get("marketIndependenceKey"), 180),
        "accountIndependenceKey": _ontology_context_text(contract.get("accountIndependenceKey"), 180),
        "criteria": [
            {
                "criterionId": _ontology_context_text(item.get("criterionId"), 120),
                "label": _ontology_context_text(item.get("label"), 240),
                "role": _ontology_context_text(item.get("role"), 40),
                "metric": _ontology_context_text(item.get("metric"), 80),
                "operator": _ontology_context_text(item.get("operator"), 8),
                "threshold": item.get("threshold"),
                "unit": _ontology_context_text(item.get("unit"), 24),
                "horizonMinutes": item.get("horizonMinutes"),
                "required": bool(item.get("required", True)),
                "benchmarkSymbol": _ontology_context_text(item.get("benchmarkSymbol"), 40),
            }
            for item in list(contract.get("criteria") or [])[:12]
            if isinstance(item, dict)
        ],
    }
    return {key: item for key, item in result.items() if item not in (None, "", [], {})}


def decision_episode_ontology_context(
    episode: DecisionEpisode,
    maximum_hypotheses: int = 3,
    maximum_outcomes: int = 8,
) -> Dict[str, object]:
    """Build a bounded ABox memory view without serializing the full audit.

    Decision episodes remain complete in their own repository for audit and
    replay. The live portfolio ABox only needs a small, current-subject slice
    of the selected decision, competing hypotheses, and later observations.
    In particular, raw historical facts, full AI prompts, and research
    documents are not copied back into every realtime inference generation.
    """
    if not isinstance(episode, DecisionEpisode):
        return {}

    hypothesis_limit = _ontology_context_count(maximum_hypotheses, 3, 16)
    outcome_limit = _ontology_context_count(maximum_outcomes, 8, 32)
    selected_id = str(episode.selected_hypothesis_id or "").strip()
    source_hypotheses = list(episode.hypothesis_set.hypotheses or [])
    selected = next((item for item in source_hypotheses if item.hypothesis_id == selected_id), None)
    hypotheses = ([selected] if selected else []) + [
        item for item in source_hypotheses if not selected or item.hypothesis_id != selected.hypothesis_id
    ]
    hypotheses = hypotheses[:hypothesis_limit]
    hypothesis_ids = {str(item.hypothesis_id or "").strip() for item in hypotheses}
    family_ids = {str(item.family_id or "").strip() for item in hypotheses if item.family_id}
    market_ids = {
        str(item.market_hypothesis_id or "").strip()
        for item in hypotheses
        if item.market_hypothesis_id
    }
    overlay_ids = {
        str(item.account_hypothesis_overlay_id or "").strip()
        for item in hypotheses
        if item.account_hypothesis_overlay_id
    }

    def hypothesis_payload(item: InvestmentHypothesis) -> Dict[str, object]:
        return {
            "hypothesisId": str(item.hypothesis_id or ""),
            "templateId": str(item.template_id or ""),
            "templateLabel": _ontology_context_text(item.template_label),
            "claim": _ontology_context_text(item.claim),
            "stance": str(item.stance or ""),
            "horizon": str(item.horizon or ""),
            "evidenceState": str(item.evidence_state or ""),
            "evidenceStateLabel": _ontology_context_text(item.evidence_state_label),
            "status": str(item.status or ""),
            "supportingEvidenceIds": _ontology_context_values(item.supporting_evidence_ids),
            "counterEvidenceIds": _ontology_context_values(item.counter_evidence_ids),
            "supportingRuleIds": _ontology_context_values(item.supporting_rule_ids),
            "counterRuleIds": _ontology_context_values(item.counter_rule_ids),
            "assumptions": _ontology_context_values(item.assumptions, limit=12),
            "invalidationConditions": _ontology_context_values(item.invalidation_conditions, limit=12),
            "causalPathIds": _ontology_context_values(item.causal_path_ids),
            "requiredEvidenceTypes": _ontology_context_values(item.required_evidence_types, limit=12),
            "approvalStatus": str(item.approval_status or ""),
            "verificationStatus": str(item.verification_status or ""),
            "familyId": str(item.family_id or ""),
            "causalSignature": _ontology_context_text(item.causal_signature, 240),
            "familySource": str(item.family_source or ""),
            "mergedRuleCount": int(item.merged_rule_count or 0),
            "scopeState": str(item.scope_state or ""),
            "scopeVersion": str(item.scope_version or ""),
            "marketHypothesisId": str(item.market_hypothesis_id or ""),
            "marketWorldId": str(item.market_world_id or ""),
            "marketId": str(item.market_id or ""),
            "marketCausalSignature": _ontology_context_text(item.market_causal_signature, 240),
            "marketConditionIds": _ontology_context_values(item.market_condition_ids),
            "marketRelationTypes": _ontology_context_values(item.market_relation_types),
            "accountHypothesisOverlayId": str(item.account_hypothesis_overlay_id or ""),
            "accountConditionIds": _ontology_context_values(item.account_condition_ids),
            "accountFields": _ontology_context_values(item.account_fields),
            "accountRelationTypes": _ontology_context_values(item.account_relation_types),
            "accountTargetKinds": _ontology_context_values(item.account_target_kinds),
            "targetRoles": _ontology_context_values(item.target_roles),
            "actionPolicies": _ontology_context_values(item.action_policies),
        }

    outcome_rows: List[Dict[str, object]] = []
    for outcome in list(episode.outcomes or [])[-outcome_limit:]:
        payload = outcome.payload if isinstance(outcome.payload, dict) else {}
        outcome_rows.append({
            "outcomeId": str(outcome.outcome_id or ""),
            "episodeId": str(outcome.episode_id or episode.episode_id or ""),
            "observedAt": str(outcome.observed_at or ""),
            "price": outcome.price,
            "profitLossRate": outcome.profit_loss_rate,
            "priceChangeFromDecisionPct": outcome.price_change_from_decision_pct,
            "selectedHypothesisStatus": str(outcome.selected_hypothesis_status or ""),
            "payload": {
                "horizonMinutes": payload.get("horizonMinutes"),
                "targetAt": _ontology_context_text(payload.get("targetAt"), 80),
                "observationTiming": _ontology_context_text(payload.get("observationTiming"), 80),
                "calibrationEligibility": _ontology_context_text(payload.get("calibrationEligibility"), 120),
                "observationSource": _ontology_context_text(payload.get("observationSource"), 160),
                "sourceAsOf": _ontology_context_text(payload.get("sourceAsOf"), 80),
                "dataQuality": _ontology_context_text(payload.get("dataQuality"), 120),
                "missingObservationDomains": _ontology_context_values(payload.get("missingObservationDomains"), limit=12),
                "missingRequiredMetricIds": _ontology_context_values(payload.get("missingRequiredMetricIds"), limit=12),
                "evaluationMode": _ontology_context_text(payload.get("mode"), 80),
                "contractFingerprint": _ontology_context_text(payload.get("contractFingerprint"), 180),
                "marketIndependenceKey": _ontology_context_text(payload.get("marketIndependenceKey"), 180),
                "accountIndependenceKey": _ontology_context_text(payload.get("accountIndependenceKey"), 180),
                "criterionAssessments": [
                    {
                        "criterionId": _ontology_context_text(item.get("criterionId"), 120),
                        "label": _ontology_context_text(item.get("label"), 200),
                        "role": _ontology_context_text(item.get("role"), 40),
                        "metric": _ontology_context_text(item.get("metric"), 80),
                        "state": _ontology_context_text(item.get("state"), 32),
                        "observedValue": item.get("observedValue"),
                        "threshold": item.get("threshold"),
                        "unit": _ontology_context_text(item.get("unit"), 24),
                    }
                    for item in list(payload.get("criterionAssessments") or [])[:12]
                    if isinstance(item, dict)
                ],
                "benchmarkSymbol": _ontology_context_text(payload.get("benchmarkSymbol"), 40),
                "benchmarkReturnPct": payload.get("benchmarkReturnPct"),
                "excessReturnPct": payload.get("excessReturnPct"),
            },
        })

    reviews = [
        {
            "hypothesisId": str(review.hypothesis_id or ""),
            "verdict": str(review.verdict or ""),
            "reasoning": _ontology_context_text(review.reasoning),
            "reviewedSupportingEvidenceIds": _ontology_context_values(review.reviewed_supporting_evidence_ids),
            "reviewedCounterEvidenceIds": _ontology_context_values(review.reviewed_counter_evidence_ids),
        }
        for review in episode.hypothesis_reviews or []
        if str(review.hypothesis_id or "") in hypothesis_ids
    ]
    outcome_contract = _ontology_outcome_contract_context(
        (episode.facts_at_decision or {}).get("hypothesisOutcomeContract")
        if isinstance(episode.facts_at_decision, dict)
        else {}
    )
    return {
        "contextVersion": DECISION_EPISODE_ONTOLOGY_CONTEXT_VERSION,
        "episodeId": str(episode.episode_id or ""),
        "accountId": str(episode.account_id or ""),
        "symbol": str(episode.symbol or "").upper(),
        "subjectName": _ontology_context_text(episode.subject_name, 240),
        "question": {
            "questionId": str(episode.question.question_id or ""),
            "text": _ontology_context_text(episode.question.text),
            "intent": str(episode.question.intent or ""),
            "subjectSymbol": str(episode.question.subject_symbol or "").upper(),
            "subjectName": _ontology_context_text(episode.question.subject_name, 240),
            "horizon": str(episode.question.horizon or ""),
            "accountId": str(episode.question.account_id or ""),
            "askedAt": str(episode.question.asked_at or ""),
            "source": str(episode.question.source or ""),
        },
        "hypothesisSet": {
            "hypothesisSetId": str(episode.hypothesis_set.hypothesis_set_id or ""),
            "subjectSymbol": str(episode.hypothesis_set.subject_symbol or "").upper(),
            "questionId": str(episode.hypothesis_set.question_id or ""),
            "comparisonRequired": bool(episode.hypothesis_set.comparison_required),
            "minimumComparisonCount": episode.hypothesis_set.minimum_comparison_count,
            "inferenceGenerationId": str(episode.hypothesis_set.inference_generation_id or ""),
            "version": str(episode.hypothesis_set.version or ""),
            "hypotheses": [hypothesis_payload(item) for item in hypotheses],
            "families": [
                {
                    "familyId": str(item.family_id or ""),
                    "label": _ontology_context_text(item.label),
                    "causalSignature": _ontology_context_text(item.causal_signature, 240),
                    "stance": str(item.stance or ""),
                    "horizon": str(item.horizon or ""),
                    "sourceRuleIds": _ontology_context_values(item.source_rule_ids),
                    "candidateHypothesisIds": [
                        value for value in _ontology_context_values(item.candidate_hypothesis_ids)
                        if value in hypothesis_ids
                    ],
                    "source": str(item.source or ""),
                    "mergedRuleCount": int(item.merged_rule_count or 0),
                    "scopeState": str(item.scope_state or ""),
                    "marketHypothesisId": str(item.market_hypothesis_id or ""),
                    "accountOverlayIds": [
                        value for value in _ontology_context_values(item.account_overlay_ids)
                        if value in overlay_ids
                    ],
                }
                for item in episode.hypothesis_set.families or []
                if str(item.family_id or "") in family_ids
            ],
            "marketHypotheses": [
                {
                    "marketHypothesisId": str(item.market_hypothesis_id or ""),
                    "marketWorldId": str(item.market_world_id or ""),
                    "marketId": str(item.market_id or ""),
                    "subjectSymbol": str(item.subject_symbol or "").upper(),
                    "horizon": str(item.horizon or ""),
                    "causalSignature": _ontology_context_text(item.causal_signature, 240),
                    "stance": str(item.stance or ""),
                    "sourceRuleIds": _ontology_context_values(item.source_rule_ids),
                    "marketConditionIds": _ontology_context_values(item.market_condition_ids),
                    "marketRelationTypes": _ontology_context_values(item.market_relation_types),
                    "source": str(item.source or ""),
                    "scopeState": str(item.scope_state or ""),
                    "scopeVersion": str(item.scope_version or ""),
                }
                for item in episode.hypothesis_set.market_hypotheses or []
                if str(item.market_hypothesis_id or "") in market_ids
            ],
            "accountOverlays": [
                {
                    "accountOverlayId": str(item.account_overlay_id or ""),
                    "accountId": str(item.account_id or ""),
                    "portfolioWorldId": str(item.portfolio_world_id or ""),
                    "familyId": str(item.family_id or ""),
                    "scopeState": str(item.scope_state or ""),
                    "marketHypothesisId": str(item.market_hypothesis_id or ""),
                    "targetRoles": _ontology_context_values(item.target_roles),
                    "actionPolicies": _ontology_context_values(item.action_policies),
                    "allowedActions": _ontology_context_values(item.allowed_actions),
                    "blockedActions": _ontology_context_values(item.blocked_actions),
                    "accountConditionIds": _ontology_context_values(item.account_condition_ids),
                    "accountFields": _ontology_context_values(item.account_fields),
                    "accountRelationTypes": _ontology_context_values(item.account_relation_types),
                    "accountTargetKinds": _ontology_context_values(item.account_target_kinds),
                    "sourceRuleIds": _ontology_context_values(item.source_rule_ids),
                    "source": str(item.source or ""),
                    "scopeVersion": str(item.scope_version or ""),
                }
                for item in episode.hypothesis_set.account_overlays or []
                if str(item.account_overlay_id or "") in overlay_ids
            ],
        },
        "action": str(episode.action or ""),
        "reviewLevel": str(episode.review_level or ""),
        "dataState": str(episode.data_state or ""),
        "validationState": str(episode.validation_state or ""),
        "selectedHypothesisId": selected_id,
        "hypothesisReviews": reviews,
        "hypothesisComparisonState": str(episode.hypothesis_comparison_state or ""),
        "hypothesisSelectionSource": str(episode.hypothesis_selection_source or ""),
        "inferenceGenerationId": str(episode.inference_generation_id or ""),
        "unresolvedQuestions": _ontology_context_values(episode.unresolved_questions, limit=12),
        "decidedAt": str(episode.decided_at or ""),
        "status": str(episode.status or ""),
        "source": str(episode.source or ""),
        "factsAtDecision": (
            {"hypothesisOutcomeContract": outcome_contract}
            if outcome_contract else {}
        ),
        "researchPlan": {},
        "researchAudit": {},
        "outcomes": outcome_rows,
    }


@dataclass(frozen=True)
class LearningProposal:
    proposal_id: str
    title: str
    reason: str
    source_episode_ids: List[str]
    affected_rule_ids: List[str]
    proposed_change: Dict[str, object]
    status: str = "review-required"
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, object]:
        return camelize(asdict(self))


@dataclass(frozen=True)
class NovelHypothesisProposal:
    proposal_id: str
    account_id: str
    symbol: str
    title: str
    claim: str
    causal_path: List[str]
    supporting_evidence_ids: List[str]
    counter_evidence_ids: List[str]
    required_evidence_types: List[str]
    invalidation_conditions: List[str]
    source_question_id: str = ""
    status: str = "review-required"
    source: str = "ai-research-planner"
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, object]:
        return camelize(asdict(self))


def question_intent(text: str) -> str:
    compact = str(text or "").lower()
    if any(term in compact for term in ["왜", "이유", "근거"]):
        return "explain-decision"
    if any(term in compact for term in ["사도", "매수", "추가매수", "진입"]):
        return "entry-decision"
    if any(term in compact for term in ["팔", "매도", "축소", "손절"]):
        return "exit-decision"
    if any(term in compact for term in ["위험", "리스크", "손실"]):
        return "risk-review"
    if any(term in compact for term in ["가설", "시나리오", "가능성"]):
        return "hypothesis-review"
    return "investment-decision"


def question_horizon(text: str) -> str:
    compact = str(text or "").lower()
    if any(term in compact for term in ["오늘", "장중", "단타", "당일"]):
        return "intraday"
    if any(term in compact for term in ["이번 주", "단기", "며칠"]):
        return "short-term"
    if any(term in compact for term in ["장기", "몇 년", "1년"]):
        return "long-term"
    if any(term in compact for term in ["중기", "몇 달", "분기"]):
        return "medium-term"
    return "multi-horizon"


def default_question(
    subject: Dict[str, object],
    facts: Dict[str, object],
    inference_generation_id: str = "",
    account_id: str = "",
) -> InvestmentQuestion:
    symbol = str(subject.get("symbol") or facts.get("symbol") or "").upper().strip()
    name = str(subject.get("name") or facts.get("name") or symbol)
    source = "관심종목" if facts.get("isWatchlist") else "보유종목"
    return InvestmentQuestion.create(
        name + " " + source + "에서 현재 행동을 바꿀 만큼 중요한 변화가 있는가?",
        subject_symbol=symbol,
        subject_name=name,
        account_id=account_id,
        asked_at=str(facts.get("observedAt") or inference_generation_id or utc_now_iso()),
        source="system-self-question",
    )


def hypothesis_scope_context(
    context: Dict[str, object],
    subject: Dict[str, object],
    facts: Dict[str, object],
    question: InvestmentQuestion,
) -> Dict[str, str]:
    """Collect ownership identifiers without turning them into rule inputs."""
    context = context if isinstance(context, dict) else {}
    subject = subject if isinstance(subject, dict) else {}
    facts = facts if isinstance(facts, dict) else {}
    account_id = str(
        context.get("accountId")
        or context.get("account_id")
        or facts.get("accountId")
        or facts.get("account_id")
        or question.account_id
        or ""
    ).strip()
    portfolio_world_id = str(
        context.get("portfolioWorldId")
        or context.get("portfolio_world_id")
        or context.get("worldId")
        or ""
    ).strip()
    if portfolio_world_id and not portfolio_world_id.lower().startswith("portfolio:"):
        portfolio_world_id = ""
    market_id = str(
        context.get("marketId")
        or context.get("market_id")
        or subject.get("market")
        or facts.get("market")
        or "global"
    ).strip()
    market_world_id = str(
        context.get("marketWorldId")
        or context.get("market_world_id")
        or ""
    ).strip()
    if not market_world_id:
        market_world_id = market_world(market_id).world_id
    return {
        "accountId": account_id,
        "portfolioWorldId": portfolio_world_id,
        "marketId": market_id,
        "marketWorldId": market_world_id,
    }


def metadata_text_values(rows: Iterable[Dict[str, object]], *keys: str) -> List[str]:
    values: List[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for key in keys:
            value = row.get(key)
            if isinstance(value, (list, tuple, set)):
                values.extend(str(item) for item in value if str(item or "").strip())
            elif value not in (None, ""):
                values.append(str(value))
    return unique_texts(values, 24)


def account_overlay_metadata(
    rows: Iterable[Dict[str, object]],
    traces: Iterable[Dict[str, object]],
    matches: Iterable[Dict[str, object]],
) -> Dict[str, List[str]]:
    metadata_rows = [
        dict(item)
        for item in list(rows or []) + list(traces or []) + list(matches or [])
        if isinstance(item, dict)
    ]
    return {
        "targetRoles": metadata_text_values(metadata_rows, "targetRole", "target_role"),
        "actionPolicies": metadata_text_values(metadata_rows, "actionPolicy", "action_policy"),
        "allowedActions": metadata_text_values(metadata_rows, "allowedActions", "allowed_actions"),
        "blockedActions": metadata_text_values(metadata_rows, "blockedActions", "blocked_actions"),
    }


def account_overlay_id_for_scope(
    scope_context: Dict[str, str],
    family_id: str,
    scope_assessment: Dict[str, object],
    metadata: Dict[str, List[str]],
) -> str:
    account_id = str(scope_context.get("accountId") or "").strip()
    portfolio_world_id = str(scope_context.get("portfolioWorldId") or "").strip()
    account_identity = portfolio_world_id or account_id
    if not account_identity:
        return ""
    shape = {
        "scopeState": scope_assessment.get("scopeState"),
        "accountConditionIds": sorted(scope_assessment.get("accountConditionIds") or []),
        "accountFields": sorted(scope_assessment.get("accountFields") or []),
        "accountRelationTypes": sorted(scope_assessment.get("accountRelationTypes") or []),
        "accountTargetKinds": sorted(scope_assessment.get("accountTargetKinds") or []),
        "targetRoles": sorted(metadata.get("targetRoles") or []),
        "actionPolicies": sorted(metadata.get("actionPolicies") or []),
        "allowedActions": sorted(metadata.get("allowedActions") or []),
        "blockedActions": sorted(metadata.get("blockedActions") or []),
    }
    signature = json.dumps(shape, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return stable_id("account-hypothesis-overlay", account_identity, family_id, signature)


def hypothesis_set_from_relation_context(
    relation_context: Dict[str, object],
    question: Optional[InvestmentQuestion] = None,
) -> Dict[str, object]:
    context = relation_context if isinstance(relation_context, dict) else {}
    subject = context.get("subject") if isinstance(context.get("subject"), dict) else {}
    facts = context.get("facts") if isinstance(context.get("facts"), dict) else {}
    account_id = str(
        context.get("accountId")
        or context.get("account_id")
        or facts.get("accountId")
        or facts.get("account_id")
        or ""
    ).strip()
    question = question or default_question(
        subject,
        facts,
        str(context.get("inferenceGenerationId") or ""),
        account_id=account_id,
    )
    scope_context = hypothesis_scope_context(context, subject, facts, question)
    hypothesis_set, research_plan = build_competing_hypotheses(
        subject=subject,
        facts=facts,
        relations=(context.get("graphStoreInference") or {}).get("relations") if isinstance(context.get("graphStoreInference"), dict) else [],
        traces=(context.get("graphStoreInference") or {}).get("traces") if isinstance(context.get("graphStoreInference"), dict) else [],
        matches=context.get("activeRules") or context.get("matchedRules") or [],
        signal_conflicts=context.get("signalConflicts") or {},
        missing_data=context.get("missingData") or facts.get("missingData") or [],
        inference_generation_id=str(context.get("inferenceGenerationId") or ""),
        question=question,
        policy=context.get("hypothesisPolicy") if isinstance(context.get("hypothesisPolicy"), dict) else {},
        scope_context=scope_context,
    )
    return {
        "question": question.to_dict(),
        "hypothesisTemplates": hypothesis_templates_from_hypotheses(hypothesis_set.hypotheses),
        "hypothesisSet": hypothesis_set.to_dict(),
        "researchPlan": research_plan.to_dict(),
        "selfQuestions": list(research_plan.unresolved_questions),
        "epistemicState": epistemic_state(hypothesis_set, research_plan),
    }


def build_competing_hypotheses(
    subject: Dict[str, object],
    facts: Dict[str, object],
    relations: Iterable[Dict[str, object]],
    traces: Iterable[Dict[str, object]],
    matches: Iterable[Dict[str, object]],
    signal_conflicts: Dict[str, object],
    missing_data: Iterable[object],
    inference_generation_id: str,
    question: InvestmentQuestion,
    policy: Dict[str, object] = None,
    scope_context: Dict[str, str] = None,
) -> tuple:
    symbol = str(subject.get("symbol") or facts.get("symbol") or question.subject_symbol or "").upper().strip()
    name = str(subject.get("name") or facts.get("name") or question.subject_name or symbol)
    relation_rows = [dict(item) for item in relations or [] if isinstance(item, dict)]
    trace_rows = [dict(item) for item in traces or [] if isinstance(item, dict)]
    match_rows = [dict(item) for item in matches or [] if isinstance(item, dict)]
    policy = policy if isinstance(policy, dict) else {}
    scope_context = scope_context if isinstance(scope_context, dict) else {}
    minimum_count = int_setting(policy.get("minimumIndependentEvidenceFamilies"), 1, 1, 6)
    maximum_count = int_setting(policy.get("maximumComparisonCount"), 8, minimum_count, 12)
    hypothesis_seed = stable_id("hypothesis-set", question.question_id, inference_generation_id, symbol)
    rule_keys = ordered_rule_ids(match_rows, trace_rows, relation_rows)
    hypotheses = [
        hypothesis_from_inference_rule(
            hypothesis_seed,
            symbol,
            name,
            question,
            inference_generation_id,
            rule_id,
            relations_for_rule(relation_rows, rule_id),
            traces_for_rule(trace_rows, rule_id),
            matches_for_rule(match_rows, rule_id),
            relation_rows,
            scope_context,
        )
        for rule_id in rule_keys
    ]
    hypotheses = [item for item in hypotheses if item is not None]
    hypotheses = compact_hypotheses_by_causal_family(
        hypotheses,
        hypothesis_seed,
        name,
        question,
    )
    hypotheses = diverse_hypotheses(hypotheses, maximum_count)
    decision_guardrails = decision_guardrails_for_context(
        hypotheses,
        name,
        question,
        missing_data,
        signal_conflicts,
        minimum_count,
    )
    decision_guardrails = rule_guardrails_for_matches(
        rule_keys,
        relation_rows,
        trace_rows,
        match_rows,
        question,
    ) + decision_guardrails
    research_plan = research_plan_for_hypotheses(question, hypotheses, missing_data, signal_conflicts)
    return HypothesisSet(
        hypothesis_set_id=hypothesis_seed,
        subject_symbol=symbol,
        question_id=question.question_id,
        hypotheses=hypotheses,
        minimum_comparison_count=minimum_count,
        comparison_policy="primary-counter-or-explicit-gap",
        inference_generation_id=inference_generation_id,
        families=hypothesis_families_from_hypotheses(hypotheses),
        market_hypotheses=market_hypotheses_from_hypotheses(hypotheses),
        account_overlays=account_overlays_from_hypotheses(hypotheses),
        decision_guardrails=decision_guardrails,
    ), research_plan


def int_setting(value: object, fallback: int, lower: int, upper: int) -> int:
    try:
        parsed = int(float(str(value if value not in (None, "") else fallback)))
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, min(upper, parsed))


def row_rule_id(item: Dict[str, object]) -> str:
    return str(item.get("ruleId") or item.get("rule_id") or item.get("sourceRuleId") or "").strip()


def ordered_rule_ids(*groups: Iterable[Dict[str, object]]) -> List[str]:
    result: List[str] = []
    for rows in groups:
        for item in rows or []:
            rule_id = row_rule_id(item)
            if rule_id and rule_id not in result:
                result.append(rule_id)
    return result


def relations_for_rule(rows: Iterable[Dict[str, object]], rule_id: str) -> List[Dict[str, object]]:
    return [item for item in rows or [] if row_rule_id(item) == rule_id]


def traces_for_rule(rows: Iterable[Dict[str, object]], rule_id: str) -> List[Dict[str, object]]:
    return [item for item in rows or [] if row_rule_id(item) == rule_id]


def matches_for_rule(rows: Iterable[Dict[str, object]], rule_id: str) -> List[Dict[str, object]]:
    return [item for item in rows or [] if row_rule_id(item) == rule_id]


def primary_inference_rows(rows: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    primary = [
        item for item in rows or []
        if str(item.get("type") or "").upper() not in META_INFERENCE_RELATION_TYPES
    ]
    return primary or list(rows or [])


def hypothesis_stance(rows: Iterable[Dict[str, object]], matches: Iterable[Dict[str, object]]) -> str:
    polarities = {relation_polarity(item) for item in primary_inference_rows(rows)}
    if "risk" in polarities and "support" not in polarities:
        return "risk"
    if "support" in polarities and "risk" not in polarities:
        return "support"
    return "context"


def hypothesis_evidence_state(
    rows: Iterable[Dict[str, object]],
    traces: Iterable[Dict[str, object]],
    matches: Iterable[Dict[str, object]],
) -> str:
    evidence_rows = primary_inference_rows(rows)
    if any(item.get("evidenceUsableForJudgement") is False for item in evidence_rows):
        return "blocked"
    if not evidence_rows and not list(traces or []) and not list(matches or []):
        return "unresolved"
    polarities = {relation_polarity(item) for item in evidence_rows}
    if "risk" in polarities and "support" in polarities:
        return "contested"
    return "supported"


def causal_label(name: str, rows: Iterable[Dict[str, object]], traces: Iterable[Dict[str, object]], matches: Iterable[Dict[str, object]]) -> str:
    candidates = []
    for item in traces or []:
        candidates.append(item.get("label"))
    for item in primary_inference_rows(rows):
        candidates.extend([item.get("targetLabel"), item.get("aiInfluenceLabel"), item.get("label")])
    for item in matches or []:
        candidates.extend([item.get("label"), item.get("promptHint") or item.get("prompt_hint")])
    label = next((str(item).strip() for item in candidates if str(item or "").strip()), "TypeDB 인과 경로")
    prefix = str(name or "").strip() + " · "
    if prefix.strip() and label.startswith(prefix):
        label = label[len(prefix):]
    return label[:420]


def trace_requirements(traces: Iterable[Dict[str, object]]) -> List[str]:
    values: List[str] = []
    for trace in traces or []:
        for condition in trace.get("matchedConditions") or []:
            if not isinstance(condition, dict):
                continue
            value = condition.get("relationType") or condition.get("field") or condition.get("conditionId")
            if value:
                values.append(value)
    return unique_texts(values, 16)


def trace_condition_ids(traces: Iterable[Dict[str, object]]) -> List[str]:
    values = []
    for trace in traces or []:
        values.extend(trace.get("matchedConditionIds") or [])
    return unique_texts(values, 16)


def hypothesis_from_inference_rule(
    hypothesis_seed: str,
    symbol: str,
    name: str,
    question: InvestmentQuestion,
    inference_generation_id: str,
    rule_id: str,
    rows: List[Dict[str, object]],
    traces: List[Dict[str, object]],
    matches: List[Dict[str, object]],
    all_rows: List[Dict[str, object]],
    scope_context: Dict[str, str] = None,
) -> Optional[InvestmentHypothesis]:
    if not rule_id or not (rows or traces or matches):
        return None
    knowledge_basis = rule_knowledge_basis_from_rows(rule_id, rows, traces, matches)
    if not knowledge_basis.requires_hypothesis:
        return None
    stance = hypothesis_stance(rows, matches)
    evidence_rows = primary_inference_rows(rows)
    evidence_ids = relation_ids(evidence_rows)
    causal_paths = unique_texts(
        [item.get("id") or item.get("inferenceTraceId") for item in traces + rows],
        16,
    )
    if not evidence_ids and causal_paths:
        evidence_ids = list(causal_paths)
    opposite_rows = [
        item for item in all_rows
        if row_rule_id(item) != rule_id
        and relation_polarity(item) in ({"support"} if stance == "risk" else {"risk"} if stance == "support" else {"risk", "support"})
    ]
    condition_ids = trace_condition_ids(traces)
    requirements = trace_requirements(traces)
    label = causal_label(name, rows, traces, matches)
    template_id = "hypothesis-template:" + rule_id
    evidence_state = hypothesis_evidence_state(rows, traces, matches)
    family_definition = hypothesis_family_definition(knowledge_basis.thesis_family)
    raw_claim = next((
        item.get("claimContract") or item.get("claim_contract")
        for item in [*rows, *traces, *matches]
        if isinstance(item.get("claimContract") or item.get("claim_contract"), dict)
    ), {})
    claim_contract = resolved_rule_claim_contract({
        "ruleId": rule_id,
        "label": label,
        "knowledgeBasis": knowledge_basis.to_dict(),
        "claimContract": raw_claim,
    }, knowledge_basis)
    causal_signature, family_source = causal_signature_for_rule(
        rule_id,
        stance,
        rows,
        traces,
        matches,
    )
    scope_context = scope_context if isinstance(scope_context, dict) else {}
    scope_assessment = inference_scope_assessment(traces, matches, rows, stance)
    scope_state = str(scope_assessment.get("scopeState") or UNVERIFIED_SCOPE)
    family_id = stable_id(
        "hypothesis-family",
        str(symbol or question.subject_symbol or name).upper(),
        question.horizon,
        causal_signature,
        scope_state,
    )
    market_id = str(scope_context.get("marketId") or "global").upper()
    market_world_id = str(scope_context.get("marketWorldId") or "")
    market_causal_signature = str(scope_assessment.get("marketCausalSignature") or "")
    market_hypothesis_id = ""
    if scope_state == MARKET_SHARED_SCOPE and market_causal_signature and market_world_id:
        market_hypothesis_id = stable_id(
            "market-hypothesis",
            market_world_id,
            str(symbol or question.subject_symbol or name).upper(),
            question.horizon,
            market_causal_signature,
        )
    overlay_metadata = account_overlay_metadata(rows, traces, matches)
    candidate_action = next((
        str(item.get("candidateAction") or item.get("candidate_action") or "").strip().upper()
        for item in [*rows, *traces, *matches]
        if str(item.get("candidateAction") or item.get("candidate_action") or "").strip()
    ), "")
    account_overlay_id = account_overlay_id_for_scope(
        scope_context,
        family_id,
        scope_assessment,
        overlay_metadata,
    )
    return InvestmentHypothesis(
        hypothesis_id=stable_id("hypothesis-instance", hypothesis_seed, family_id, rule_id),
        template_id=template_id,
        template_label=label,
        claim=name + "에서 TypeDB가 확인한 '" + label + "' 인과 경로가 현재 상황을 설명한다.",
        stance=stance,
        horizon=question.horizon,
        evidence_state=evidence_state,
        evidence_state_label=HYPOTHESIS_EVIDENCE_STATE_LABELS[evidence_state],
        supporting_evidence_ids=evidence_ids,
        counter_evidence_ids=relation_ids(opposite_rows),
        supporting_rule_ids=[rule_id],
        counter_rule_ids=unique_texts([row_rule_id(item) for item in opposite_rows]),
        assumptions=[
            "TypeDB 성립 조건 " + ", ".join(condition_ids[:6]) + "이 판단 기간에도 유효합니다."
        ] if condition_ids else ["현재 TypeDB 추론 경로와 원천 데이터의 유효시각이 판단 기간에도 유효합니다."],
        invalidation_conditions=[
            "TypeDB 조건 " + (", ".join(condition_ids[:6]) if condition_ids else rule_id) + "이 다음 추론 세대에서 성립하지 않거나 반대 인과 경로가 더 강해집니다."
        ],
        causal_path_ids=causal_paths,
        required_evidence_types=requirements,
        approval_status="approved-active",
        verification_status="typedb-current-generation",
        family_id=family_id,
        causal_signature=causal_signature,
        family_source=family_source,
        merged_rule_count=1,
        scope_state=scope_state,
        scope_version=str(scope_assessment.get("scopeVersion") or HYPOTHESIS_SCOPE_VERSION),
        market_hypothesis_id=market_hypothesis_id,
        market_world_id=market_world_id if market_hypothesis_id else "",
        market_id=market_id if market_hypothesis_id else "",
        subject_symbol=str(symbol or question.subject_symbol or name).upper(),
        market_causal_signature=market_causal_signature,
        market_condition_ids=list(scope_assessment.get("marketConditionIds") or []),
        market_relation_types=list(scope_assessment.get("marketRelationTypes") or []),
        account_hypothesis_overlay_id=account_overlay_id,
        account_id=str(scope_context.get("accountId") or ""),
        portfolio_world_id=str(scope_context.get("portfolioWorldId") or ""),
        account_condition_ids=list(scope_assessment.get("accountConditionIds") or []),
        account_fields=list(scope_assessment.get("accountFields") or []),
        account_relation_types=list(scope_assessment.get("accountRelationTypes") or []),
        account_target_kinds=list(scope_assessment.get("accountTargetKinds") or []),
        target_roles=list(overlay_metadata.get("targetRoles") or []),
        action_policies=list(overlay_metadata.get("actionPolicies") or []),
        allowed_actions=list(overlay_metadata.get("allowedActions") or []),
        blocked_actions=list(overlay_metadata.get("blockedActions") or []),
        theory_family=knowledge_basis.theory_family,
        thesis_family=knowledge_basis.thesis_family,
        knowledge_basis=knowledge_basis.to_dict(),
        prediction_target=family_definition.prediction_target if family_definition else "",
        expected_direction=family_definition.expected_direction if family_definition else stance,
        expected_outcome=family_definition.expected_outcome if family_definition else "",
        competing_family_ids=list(family_definition.competing_family_ids) if family_definition else [],
        outcome_metric=family_definition.outcome_metric if family_definition else "",
        falsification_contract=family_definition.falsification_contract if family_definition else "",
        inference_generation_id=str(inference_generation_id or ""),
        candidate_action=candidate_action,
        claim_contract=claim_contract.to_dict(),
        qualification=hypothesis_qualification(claim_contract),
    )


def causal_signature_for_rule(
    rule_id: str,
    stance: str,
    rows: Iterable[Dict[str, object]],
    traces: Iterable[Dict[str, object]],
    matches: Iterable[Dict[str, object]],
) -> tuple:
    """Create a conservative, current-observation-independent causal signature.

    We only compact candidates when TypeDB supplied the same condition shape.
    A matching action, label, or current price is never enough to merge two
    mechanisms. If the trace has no condition details, keeping the source rule
    separate is safer than guessing that two paths mean the same thing.
    """
    rows = [dict(item) for item in rows or [] if isinstance(item, dict)]
    traces = [dict(item) for item in traces or [] if isinstance(item, dict)]
    matches = [dict(item) for item in matches or [] if isinstance(item, dict)]
    explicit_keys = sorted({
        signature_text(item.get("hypothesisFamilyKey") or item.get("hypothesis_family_key"))
        for item in rows + traces + matches
        if signature_text(item.get("hypothesisFamilyKey") or item.get("hypothesis_family_key"))
    })
    if explicit_keys:
        return (
            "rulebox-family:" + "|".join(explicit_keys) + "|stance=" + signature_text(stance),
            "rulebox-explicit-family-key",
        )

    condition_shapes = [
        condition
        for row in traces + matches
        for condition in row.get("ruleConditionShapes") or row.get("rule_condition_shapes") or []
        if isinstance(condition, dict)
    ]
    if not condition_shapes:
        condition_shapes = [
            condition.get("ruleConditionShape") or condition.get("rule_condition_shape")
            for row in traces + matches
            for condition in row.get("matchedConditions") or []
            if isinstance(condition, dict)
            and isinstance(condition.get("ruleConditionShape") or condition.get("rule_condition_shape"), dict)
        ]
    condition_signatures = sorted({
        condition_structural_signature(condition)
        for condition in condition_shapes
        if condition_structural_signature(condition)
    })
    if not condition_signatures:
        return "typedb-rule:" + signature_text(rule_id), "typedb-rule-id-fallback"

    primary_rows = primary_inference_rows(rows)
    relation_types = sorted({
        signature_text(item.get("type") or item.get("relationType"))
        for item in primary_rows
        if signature_text(item.get("type") or item.get("relationType"))
    })
    target_kinds = sorted({
        signature_text(
            item.get("targetKind")
            or item.get("target_kind")
            or str(item.get("target") or "").split(":", 1)[0]
        )
        for item in primary_rows
        if signature_text(
            item.get("targetKind")
            or item.get("target_kind")
            or str(item.get("target") or "").split(":", 1)[0]
        )
    })
    decision_stages = sorted({
        signature_text(item.get("decisionStage") or item.get("decision_stage"))
        for item in primary_rows
        if signature_text(item.get("decisionStage") or item.get("decision_stage"))
    })
    action_groups = sorted({
        signature_text(item.get("actionGroup") or item.get("action_group"))
        for item in primary_rows
        if signature_text(item.get("actionGroup") or item.get("action_group"))
    })
    target_roles = sorted({
        signature_text(item.get("targetRole") or item.get("target_role"))
        for item in primary_rows
        if signature_text(item.get("targetRole") or item.get("target_role"))
    })
    action_policies = sorted({
        signature_text(item.get("actionPolicy") or item.get("action_policy"))
        for item in primary_rows
        if signature_text(item.get("actionPolicy") or item.get("action_policy"))
    })
    any_condition_counts = sorted({
        str(int_setting(
            item.get("anyConditionMinCount") or item.get("any_condition_min_count"),
            1,
            1,
            100,
        ))
        for item in traces + matches
        if item.get("anyConditionMinCount") not in (None, "")
        or item.get("any_condition_min_count") not in (None, "")
    })
    return (
        "typedb-structural:"
        + "|".join([
            "stance=" + signature_text(stance),
            "relations=" + ",".join(relation_types),
            "targets=" + ",".join(target_kinds),
            "stages=" + ",".join(decision_stages),
            "groups=" + ",".join(action_groups),
            "roles=" + ",".join(target_roles),
            "policies=" + ",".join(action_policies),
            "anyMinimum=" + ",".join(any_condition_counts),
            "conditions=" + ",".join(condition_signatures),
        ]),
        "typedb-structural-signature",
    )


def condition_structural_signature(condition: Dict[str, object]) -> str:
    if not isinstance(condition, dict):
        return ""
    kind = signature_text(condition.get("kind"))
    role = signature_text(condition.get("role") or condition.get("conditionRole") or "required")
    field = signature_text(condition.get("field"))
    relation_type = signature_text(condition.get("relationType") or condition.get("relation_type"))
    target_kind = signature_text(condition.get("targetKind") or condition.get("target_kind"))
    direction = signature_text(condition.get("direction") or "out")
    operator = signature_text(condition.get("operator") or "==")
    value = canonical_signature_value(condition.get("value"))
    target_filters = canonical_signature_value(
        condition.get("targetPropertyFilters") or condition.get("target_property_filters")
    )
    relation_filters = canonical_signature_value(
        condition.get("relationPropertyFilters") or condition.get("relation_property_filters")
    )
    if not any([kind, field, relation_type, target_kind, value]):
        return ""
    return "~".join([
        "kind=" + kind,
        "role=" + role,
        "field=" + field,
        "relation=" + relation_type,
        "target=" + target_kind,
        "direction=" + direction,
        "operator=" + operator,
        "value=" + value,
        "targetFilters=" + target_filters,
        "relationFilters=" + relation_filters,
    ])


def signature_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()[:240]


def canonical_signature_value(value: object) -> str:
    if isinstance(value, dict):
        return json.dumps(
            {str(key): canonical_signature_value(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )[:240]
    if isinstance(value, (list, tuple, set)):
        return "[" + ",".join(sorted(canonical_signature_value(item) for item in value)) + "]"
    return signature_text(value)


def compact_hypotheses_by_causal_family(
    hypotheses: Iterable[InvestmentHypothesis],
    hypothesis_seed: str,
    name: str,
    question: InvestmentQuestion,
) -> List[InvestmentHypothesis]:
    """Merge equivalent paths and correlated evidence before candidate capping.

    The merge is a presentation and comparison optimization. It unions the
    graph-owned evidence and source rules, while keeping the original TypeDB
    action semantics untouched. Different causal signatures remain competing
    hypotheses unless their governed knowledge contracts explicitly share one
    ``evidenceIndependenceKey``. Such rows are one comparison input rather
    than independent votes, while their original graph lineage is retained.
    """
    groups: Dict[str, List[InvestmentHypothesis]] = {}
    for hypothesis in hypotheses or []:
        signature = str(hypothesis.causal_signature or "").strip()
        family_id = str(hypothesis.family_id or "").strip()
        independence_key = str(
            (hypothesis.knowledge_basis or {}).get("evidenceIndependenceKey")
            or (hypothesis.knowledge_basis or {}).get("evidence_independence_key")
            or ""
        ).strip()
        basis_origin = str(
            (hypothesis.knowledge_basis or {}).get("basisOrigin")
            or (hypothesis.knowledge_basis or {}).get("basis_origin")
            or ""
        ).strip()
        if not signature:
            signature = "legacy-template:" + str(hypothesis.template_id or "")
        if not family_id:
            family_id = stable_id(
                "hypothesis-family",
                question.subject_symbol or name,
                question.horizon,
                signature,
            )
        comparison_group = (
            "independent-evidence:" + independence_key + "|stance=" + str(hypothesis.stance or "")
            if independence_key and basis_origin != "legacy-inference"
            else family_id
        )
        groups.setdefault(comparison_group, []).append(hypothesis)

    compacted: List[InvestmentHypothesis] = []
    for comparison_group, members in groups.items():
        ordered = sorted(
            members,
            key=lambda item: (
                str(item.template_id or ""),
                ",".join(sorted(item.supporting_rule_ids or [])),
            ),
        )
        primary = ordered[0]
        family_id = primary.family_id or stable_id(
            "hypothesis-family",
            question.subject_symbol or name,
            question.horizon,
            comparison_group,
        )
        source_rule_ids = sorted({
            rule_id
            for item in ordered
            for rule_id in item.supporting_rule_ids or []
            if str(rule_id or "").strip()
        })
        count = len(source_rule_ids)
        evidence_state = merged_hypothesis_evidence_state(ordered)
        claim = primary.claim
        if count > 1:
            independence_key = str(
                (primary.knowledge_basis or {}).get("evidenceIndependenceKey")
                or (primary.knowledge_basis or {}).get("evidence_independence_key")
                or ""
            ).strip()
            claim += (
                " 같은 독립 근거 계열을 공유하는 TypeDB 규칙 "
                if independence_key
                else " 같은 인과 구조의 TypeDB 규칙 "
            ) + str(count) + "개가 이 설명을 함께 뒷받침합니다. 독립된 " + str(count) + "표로 계산하지 않습니다."
        compacted.append(InvestmentHypothesis(
            hypothesis_id=stable_id("hypothesis-instance", hypothesis_seed, family_id),
            template_id=primary.template_id,
            template_label=primary.template_label,
            claim=claim,
            stance=primary.stance,
            horizon=primary.horizon,
            evidence_state=evidence_state,
            evidence_state_label=HYPOTHESIS_EVIDENCE_STATE_LABELS[evidence_state],
            supporting_evidence_ids=unique_texts(
                [value for item in ordered for value in item.supporting_evidence_ids],
                24,
            ),
            counter_evidence_ids=unique_texts(
                [value for item in ordered for value in item.counter_evidence_ids],
                24,
            ),
            supporting_rule_ids=source_rule_ids,
            counter_rule_ids=unique_texts(
                [value for item in ordered for value in item.counter_rule_ids],
                24,
            ),
            assumptions=unique_texts(
                [value for item in ordered for value in item.assumptions],
                12,
            ),
            invalidation_conditions=unique_texts(
                [value for item in ordered for value in item.invalidation_conditions],
                12,
            ),
            causal_path_ids=unique_texts(
                [value for item in ordered for value in item.causal_path_ids],
                24,
            ),
            required_evidence_types=unique_texts(
                [value for item in ordered for value in item.required_evidence_types],
                20,
            ),
            approval_status=primary.approval_status,
            verification_status=primary.verification_status,
            status=primary.status,
            historical_calibration=dict(primary.historical_calibration or {}),
            family_id=family_id,
            causal_signature=primary.causal_signature,
            family_source=primary.family_source,
            merged_rule_count=count,
            scope_state=merged_hypothesis_scope_state(ordered),
            scope_version=primary.scope_version or HYPOTHESIS_SCOPE_VERSION,
            market_hypothesis_id=common_hypothesis_value(ordered, "market_hypothesis_id"),
            market_world_id=common_hypothesis_value(ordered, "market_world_id"),
            market_id=common_hypothesis_value(ordered, "market_id"),
            subject_symbol=common_hypothesis_value(ordered, "subject_symbol"),
            market_causal_signature=common_hypothesis_value(ordered, "market_causal_signature"),
            market_condition_ids=unique_texts(
                [value for item in ordered for value in item.market_condition_ids],
                24,
            ),
            market_relation_types=unique_texts(
                [value for item in ordered for value in item.market_relation_types],
                24,
            ),
            account_hypothesis_overlay_id=common_hypothesis_value(ordered, "account_hypothesis_overlay_id"),
            account_id=common_hypothesis_value(ordered, "account_id"),
            portfolio_world_id=common_hypothesis_value(ordered, "portfolio_world_id"),
            account_condition_ids=unique_texts(
                [value for item in ordered for value in item.account_condition_ids],
                24,
            ),
            account_fields=unique_texts(
                [value for item in ordered for value in item.account_fields],
                24,
            ),
            account_relation_types=unique_texts(
                [value for item in ordered for value in item.account_relation_types],
                24,
            ),
            account_target_kinds=unique_texts(
                [value for item in ordered for value in item.account_target_kinds],
                24,
            ),
            target_roles=unique_texts(
                [value for item in ordered for value in item.target_roles],
                12,
            ),
            action_policies=unique_texts(
                [value for item in ordered for value in item.action_policies],
                12,
            ),
            allowed_actions=unique_texts(
                [value for item in ordered for value in item.allowed_actions],
                12,
            ),
            blocked_actions=unique_texts(
                [value for item in ordered for value in item.blocked_actions],
                12,
            ),
            theory_family=primary.theory_family,
            thesis_family=primary.thesis_family,
            knowledge_basis={
                **dict(primary.knowledge_basis or {}),
                "correlatedHypothesisCount": len(ordered),
                "correlatedFamilyIds": unique_texts([
                    item.family_id for item in ordered if item.family_id
                ], 24),
                "correlatedCausalSignatures": unique_texts([
                    item.causal_signature for item in ordered if item.causal_signature
                ], 24),
                "independentVoteCount": 1,
            },
            prediction_target=primary.prediction_target,
            expected_direction=primary.expected_direction,
            expected_outcome=primary.expected_outcome,
            competing_family_ids=unique_texts(
                [value for item in ordered for value in item.competing_family_ids],
                12,
            ),
            outcome_metric=primary.outcome_metric,
            falsification_contract=primary.falsification_contract,
            inference_generation_id=common_hypothesis_value(ordered, "inference_generation_id"),
            candidate_action=common_hypothesis_value(ordered, "candidate_action"),
            claim_contract=dict(primary.claim_contract or {}),
            qualification=dict(primary.qualification or {}),
        ))
    return compacted


def common_hypothesis_value(hypotheses: Iterable[InvestmentHypothesis], field_name: str) -> str:
    values = {
        str(getattr(item, field_name, "") or "").strip()
        for item in hypotheses or []
        if str(getattr(item, field_name, "") or "").strip()
    }
    return next(iter(values)) if len(values) == 1 else ""


def merged_hypothesis_scope_state(hypotheses: Iterable[InvestmentHypothesis]) -> str:
    states = {
        str(item.scope_state or UNVERIFIED_SCOPE)
        for item in hypotheses or []
    }
    if not states:
        return UNVERIFIED_SCOPE
    if len(states) == 1:
        return next(iter(states))
    if MIXED_SCOPE in states or {MARKET_SHARED_SCOPE, ACCOUNT_ONLY_SCOPE}.issubset(states):
        return MIXED_SCOPE
    if UNVERIFIED_SCOPE in states:
        return UNVERIFIED_SCOPE
    return MIXED_SCOPE


def merged_hypothesis_evidence_state(hypotheses: Iterable[InvestmentHypothesis]) -> str:
    states = {str(item.evidence_state or "unresolved") for item in hypotheses or []}
    if "contested" in states:
        return "contested"
    if "supported" in states:
        return "supported"
    if "blocked" in states:
        return "blocked"
    return "unresolved"


def hypothesis_families_from_hypotheses(
    hypotheses: Iterable[InvestmentHypothesis],
) -> List[HypothesisFamily]:
    grouped: Dict[str, List[InvestmentHypothesis]] = {}
    for hypothesis in hypotheses or []:
        family_id = str(hypothesis.family_id or "").strip()
        if not family_id:
            family_id = stable_id(
                "hypothesis-family",
                hypothesis.template_id,
                hypothesis.horizon,
                hypothesis.causal_signature or hypothesis.template_id,
            )
        grouped.setdefault(family_id, []).append(hypothesis)
    families: List[HypothesisFamily] = []
    for family_id, members in grouped.items():
        primary = members[0]
        source_rule_ids = sorted({
            rule_id
            for item in members
            for rule_id in item.supporting_rule_ids or []
            if str(rule_id or "").strip()
        })
        families.append(HypothesisFamily(
            family_id=family_id,
            label=primary.template_label,
            causal_signature=primary.causal_signature or ("legacy-template:" + primary.template_id),
            stance=primary.stance,
            horizon=primary.horizon,
            source_rule_ids=source_rule_ids,
            candidate_hypothesis_ids=[item.hypothesis_id for item in members if item.hypothesis_id],
            source=primary.family_source or "typedb-structural-signature",
            merged_rule_count=len(source_rule_ids),
            scope_state=merged_hypothesis_scope_state(members),
            market_hypothesis_id=common_hypothesis_value(members, "market_hypothesis_id"),
            account_overlay_ids=sorted({
                item.account_hypothesis_overlay_id
                for item in members
                if item.account_hypothesis_overlay_id
            }),
            theory_family=primary.theory_family,
            thesis_family=primary.thesis_family,
        ))
    return families


def market_hypotheses_from_hypotheses(
    hypotheses: Iterable[InvestmentHypothesis],
) -> List[MarketHypothesis]:
    groups: Dict[str, List[InvestmentHypothesis]] = {}
    for hypothesis in hypotheses or []:
        market_hypothesis_id = str(hypothesis.market_hypothesis_id or "").strip()
        if not market_hypothesis_id or hypothesis.scope_state != MARKET_SHARED_SCOPE:
            continue
        groups.setdefault(market_hypothesis_id, []).append(hypothesis)
    result: List[MarketHypothesis] = []
    for market_hypothesis_id, members in sorted(groups.items()):
        primary = members[0]
        result.append(MarketHypothesis(
            market_hypothesis_id=market_hypothesis_id,
            market_world_id=primary.market_world_id,
            market_id=primary.market_id,
            subject_symbol=primary.subject_symbol,
            horizon=primary.horizon,
            causal_signature=primary.market_causal_signature,
            stance=primary.stance,
            source_rule_ids=sorted({
                rule_id
                for item in members
                for rule_id in item.supporting_rule_ids or []
                if str(rule_id or "").strip()
            }),
            market_condition_ids=unique_texts(
                [value for item in members for value in item.market_condition_ids],
                24,
            ),
            market_relation_types=unique_texts(
                [value for item in members for value in item.market_relation_types],
                24,
            ),
        ))
    return result


def account_overlays_from_hypotheses(
    hypotheses: Iterable[InvestmentHypothesis],
) -> List[AccountHypothesisOverlay]:
    groups: Dict[str, List[InvestmentHypothesis]] = {}
    for hypothesis in hypotheses or []:
        overlay_id = str(hypothesis.account_hypothesis_overlay_id or "").strip()
        if overlay_id:
            groups.setdefault(overlay_id, []).append(hypothesis)
    result: List[AccountHypothesisOverlay] = []
    for overlay_id, members in sorted(groups.items()):
        primary = members[0]
        result.append(AccountHypothesisOverlay(
            account_overlay_id=overlay_id,
            account_id=primary.account_id,
            portfolio_world_id=primary.portfolio_world_id,
            family_id=primary.family_id,
            scope_state=merged_hypothesis_scope_state(members),
            market_hypothesis_id=common_hypothesis_value(members, "market_hypothesis_id"),
            target_roles=unique_texts(
                [value for item in members for value in item.target_roles],
                12,
            ),
            action_policies=unique_texts(
                [value for item in members for value in item.action_policies],
                12,
            ),
            allowed_actions=unique_texts(
                [value for item in members for value in item.allowed_actions],
                12,
            ),
            blocked_actions=unique_texts(
                [value for item in members for value in item.blocked_actions],
                12,
            ),
            account_condition_ids=unique_texts(
                [value for item in members for value in item.account_condition_ids],
                24,
            ),
            account_fields=unique_texts(
                [value for item in members for value in item.account_fields],
                24,
            ),
            account_relation_types=unique_texts(
                [value for item in members for value in item.account_relation_types],
                24,
            ),
            account_target_kinds=unique_texts(
                [value for item in members for value in item.account_target_kinds],
                24,
            ),
            source_rule_ids=sorted({
                rule_id
                for item in members
                for rule_id in item.supporting_rule_ids or []
                if str(rule_id or "").strip()
            }),
        ))
    return result


def diverse_hypotheses(hypotheses: List[InvestmentHypothesis], maximum_count: int) -> List[InvestmentHypothesis]:
    selected: List[InvestmentHypothesis] = []
    for stance in ["risk", "support", "context"]:
        item = next((row for row in hypotheses if row.stance == stance), None)
        if item and item not in selected:
            selected.append(item)
    for item in hypotheses:
        if item not in selected:
            selected.append(item)
        if len(selected) >= maximum_count:
            break
    return selected[:maximum_count]


def rule_guardrails_for_matches(
    rule_ids: Iterable[str],
    relations: Iterable[Dict[str, object]],
    traces: Iterable[Dict[str, object]],
    matches: Iterable[Dict[str, object]],
    question: InvestmentQuestion,
) -> List[DecisionGuardrail]:
    """Project matched non-predictive rules as constraints, not theses.

    TypeDB already decided that each source rule matched. This function only
    applies the RuleBox-owned knowledge boundary so policy, execution, data
    quality and context rows cannot consume a competing-hypothesis slot.
    """

    relation_rows = [dict(item) for item in relations or [] if isinstance(item, dict)]
    trace_rows = [dict(item) for item in traces or [] if isinstance(item, dict)]
    match_rows = [dict(item) for item in matches or [] if isinstance(item, dict)]
    result: List[DecisionGuardrail] = []
    for rule_id in rule_ids or []:
        rule_relations = relations_for_rule(relation_rows, rule_id)
        rule_traces = traces_for_rule(trace_rows, rule_id)
        rule_matches = matches_for_rule(match_rows, rule_id)
        basis = rule_knowledge_basis_from_rows(rule_id, rule_relations, rule_traces, rule_matches)
        if basis.requires_hypothesis:
            continue
        rows = rule_relations + rule_traces + rule_matches
        label = causal_label("", rule_relations, rule_traces, rule_matches)
        blocked_actions = unique_texts([
            action
            for row in rows
            for action in row.get("blockedActions") or row.get("blocked_actions") or []
        ], 12)
        next_checks = unique_texts([
            check
            for row in rows
            for check in row.get("nextChecks") or row.get("next_checks") or []
        ], 12)
        result.append(DecisionGuardrail(
            guardrail_id=stable_id("decision-guardrail", question.question_id, rule_id),
            guardrail_type=basis.rule_kind,
            label=label or basis.thesis_family or rule_id,
            reason=basis.plain_language_basis,
            required_checks=next_checks,
            blocked_actions=blocked_actions,
            source="typedb-rulebox-guardrail",
            source_rule_ids=[rule_id],
            knowledge_basis=basis.to_dict(),
        ))
    return result


def decision_guardrails_for_context(
    hypotheses: List[InvestmentHypothesis],
    name: str,
    question: InvestmentQuestion,
    missing_data: Iterable[object],
    conflicts: Dict[str, object],
    minimum_count: int,
) -> List[DecisionGuardrail]:
    """Describe evidence constraints without turning them into hypotheses."""

    result: List[DecisionGuardrail] = []
    eligible_hypotheses = [
        item for item in hypotheses
        if decision_eligible_hypothesis_payload(item.to_dict())
    ]
    independent_evidence_keys = {
        str(
            (item.knowledge_basis or {}).get("evidenceIndependenceKey")
            or (item.knowledge_basis or {}).get("evidence_independence_key")
            or item.family_id
            or item.hypothesis_id
        ).strip()
        for item in eligible_hypotheses
        if str(
            (item.knowledge_basis or {}).get("evidenceIndependenceKey")
            or (item.knowledge_basis or {}).get("evidence_independence_key")
            or item.family_id
            or item.hypothesis_id
        ).strip()
    }
    missing = unique_texts(missing_data)
    has_conflict = bool((conflicts or {}).get("hasConflict"))
    if hypotheses and (len(independent_evidence_keys) < minimum_count or missing or has_conflict):
        reasons = []
        if len(independent_evidence_keys) < minimum_count:
            reasons.append(
                "판단 가능한 독립 근거 계열이 "
                + str(minimum_count)
                + "개보다 적습니다."
            )
        if missing:
            reasons.append("필수 데이터가 비어 있습니다: " + ", ".join(missing[:4]))
        if has_conflict:
            reasons.append("현재 세대의 근거 방향이 서로 충돌합니다.")
        result.append(DecisionGuardrail(
            guardrail_id=stable_id("decision-guardrail", question.question_id, "evidence-sufficiency"),
            guardrail_type="evidence-sufficiency",
            label="근거 충분성 제한",
            reason=" ".join(reasons),
            required_checks=unique_texts([
                "현재 TypeDB 규칙 가설을 모두 비교합니다.",
                *(["누락 데이터를 수집한 뒤 재추론합니다."] if missing else []),
                *(["충돌 근거의 시점과 출처를 대조합니다."] if has_conflict else []),
            ]),
            missing_data=missing,
        ))
    directional_stances = {
        item.stance for item in eligible_hypotheses
        if item.stance in {"risk", "support"}
    }
    if hypotheses and len(directional_stances) < 2:
        result.append(DecisionGuardrail(
            guardrail_id=stable_id("decision-guardrail", question.question_id, "counterfactual-coverage"),
            guardrail_type="counterfactual-coverage",
            label="반대 경로 확인 필요",
            reason=name + "의 현재 TypeDB 규칙 가설이 한 방향에 치우쳐 있어 반대 경로를 충분히 비교하지 못했습니다.",
            required_checks=["현재 경로를 반박할 독립 근거 또는 다음 세대의 경로 반복 여부를 확인합니다."],
        ))
    return result


def add_safety_hypotheses(
    hypotheses: List[InvestmentHypothesis],
    hypothesis_seed: str,
    name: str,
    question: InvestmentQuestion,
    missing_data: Iterable[object],
    conflicts: Dict[str, object],
    relation_rows: List[Dict[str, object]],
    minimum_count: int,
    maximum_count: int,
) -> List[InvestmentHypothesis]:
    """Legacy compatibility helper; new hypothesis sets use decision guardrails."""
    result = list(hypotheses)
    directional_stances = {item.stance for item in result if item.stance in {"risk", "support"}}
    evidence_safety_needed = len(result) < minimum_count or bool(list(missing_data or [])) or bool((conflicts or {}).get("hasConflict"))
    if result and evidence_safety_needed:
        missing = unique_texts(missing_data)
        evidence_state = "contested" if bool((conflicts or {}).get("hasConflict")) else "unresolved"
        family_id = system_hypothesis_family_id(name, question, SYSTEM_ABSTENTION_TEMPLATE_ID)
        safety = InvestmentHypothesis(
            hypothesis_id=stable_id("hypothesis-instance", hypothesis_seed, family_id),
            template_id=SYSTEM_ABSTENTION_TEMPLATE_ID,
            template_label="근거 충분성 검증",
            claim=name + "의 현재 근거만으로는 경쟁 인과 경로를 충분히 배제할 수 없어 행동 판단을 유보해야 한다.",
            stance="uncertain",
            horizon=question.horizon,
            evidence_state=evidence_state,
            evidence_state_label=HYPOTHESIS_EVIDENCE_STATE_LABELS[evidence_state],
            supporting_evidence_ids=relation_ids([item for item in relation_rows if relation_polarity(item) in {"context", "neutral"}]),
            counter_evidence_ids=unique_texts([evidence for item in result for evidence in item.supporting_evidence_ids]),
            supporting_rule_ids=[],
            counter_rule_ids=unique_texts([rule for item in result for rule in item.supporting_rule_ids]),
            assumptions=["누락·지연·충돌 데이터가 해소되면 가설 우선순위가 달라질 수 있습니다."],
            invalidation_conditions=["필수 근거가 채워지고 독립된 인과 경로가 같은 방향으로 반복 확인됩니다."],
            causal_path_ids=[],
            required_evidence_types=missing,
            approval_status="approved-safety-policy",
            verification_status="requires-research",
            family_id=family_id,
            causal_signature="system-safety:" + SYSTEM_ABSTENTION_TEMPLATE_ID,
            family_source="system-safety-policy",
            merged_rule_count=0,
            scope_state=SYSTEM_SAFETY_SCOPE,
        )
        result = with_reserved_safety_slot(result, safety, maximum_count)
    if result and (len(result) < minimum_count or len(directional_stances) < 2):
        leading = result[0]
        null_template = "hypothesis-template:system.null-challenge.v1"
        family_id = system_hypothesis_family_id(name, question, null_template)
        safety = InvestmentHypothesis(
            hypothesis_id=stable_id("hypothesis-instance", hypothesis_seed, family_id),
            template_id=null_template,
            template_label="추론 경로의 일시적 동행 가능성",
            claim=name + "의 현재 TypeDB 경로가 지속 가능한 인과가 아니라 일시적 동행일 수 있다.",
            stance="context",
            horizon=question.horizon,
            evidence_state="unresolved",
            evidence_state_label=HYPOTHESIS_EVIDENCE_STATE_LABELS["unresolved"],
            supporting_evidence_ids=relation_ids([item for item in relation_rows if relation_polarity(item) == "context"]),
            counter_evidence_ids=list(leading.supporting_evidence_ids),
            supporting_rule_ids=[],
            counter_rule_ids=list(leading.supporting_rule_ids),
            assumptions=["현재 경로가 다음 관측에서 반복되지 않을 수 있습니다."],
            invalidation_conditions=["동일 인과 경로가 독립된 다음 관측과 출처에서도 반복 확인됩니다."],
            causal_path_ids=[],
            required_evidence_types=list(leading.required_evidence_types),
            approval_status="approved-safety-policy",
            verification_status="counterfactual-challenge",
            family_id=family_id,
            causal_signature="system-safety:" + null_template,
            family_source="system-safety-policy",
            merged_rule_count=0,
            scope_state=SYSTEM_SAFETY_SCOPE,
        )
        result = with_reserved_safety_slot(result, safety, maximum_count)
    return result[:maximum_count]


def system_hypothesis_family_id(
    name: str,
    question: InvestmentQuestion,
    template_id: str,
) -> str:
    return stable_id(
        "hypothesis-family",
        "system-safety",
        question.subject_symbol or name,
        question.horizon,
        template_id,
    )


def with_reserved_safety_slot(
    hypotheses: List[InvestmentHypothesis],
    safety: InvestmentHypothesis,
    maximum_count: int,
) -> List[InvestmentHypothesis]:
    if any(item.template_id == safety.template_id for item in hypotheses):
        return list(hypotheses)
    result = list(hypotheses)
    if len(result) >= maximum_count:
        removable = [item for item in result if item.approval_status == "approved-active"]
        if removable:
            result.remove(removable[-1])
    result.append(safety)
    return result


def hypothesis_templates_from_hypotheses(hypotheses: Iterable[InvestmentHypothesis]) -> List[Dict[str, object]]:
    rows = []
    seen = set()
    for item in hypotheses or []:
        if item.template_id in seen:
            continue
        seen.add(item.template_id)
        rows.append(HypothesisTemplate(
            template_id=item.template_id,
            label=item.template_label,
            version="v1",
            source_rule_ids=list(item.supporting_rule_ids),
            stance=item.stance,
            required_evidence_types=list(item.required_evidence_types),
            causal_path_pattern=list(item.causal_path_ids),
            approval_status=item.approval_status,
            source="typedb-native-rule" if item.supporting_rule_ids else "system-safety-policy",
            theory_family=item.theory_family,
            thesis_family=item.thesis_family,
            knowledge_basis=dict(item.knowledge_basis or {}),
            prediction_target=item.prediction_target,
            expected_direction=item.expected_direction,
            expected_outcome=item.expected_outcome,
            default_horizon=item.horizon,
            competing_family_ids=list(item.competing_family_ids),
            outcome_metric=item.outcome_metric,
            falsification_contract=item.falsification_contract,
            claim_contract=dict(item.claim_contract or {}),
            qualification=dict(item.qualification or {}),
        ).to_dict())
    return rows


def hypothesis_templates_from_rulebox_snapshot(
    snapshot: Dict[str, object],
    performance: Dict[str, object] = None,
) -> List[Dict[str, object]]:
    rows = []
    performance_by_rule = {
        str(item.get("key") or ""): item
        for item in (performance or {}).get("byRule") or []
        if isinstance(item, dict) and str(item.get("key") or "")
    }
    for rule in (snapshot or {}).get("rules") or []:
        if not isinstance(rule, dict) or rule.get("enabled") is False:
            continue
        rule_id = row_rule_id(rule)
        if not rule_id:
            continue
        knowledge_basis = rule_knowledge_basis_from_rows(rule_id, [rule])
        if not knowledge_basis.requires_hypothesis:
            continue
        derivations = [item for item in rule.get("derivations") or [] if isinstance(item, dict)]
        polarities = {
            str(item.get("polarity") or "").strip().lower()
            for item in derivations
            if str(item.get("polarity") or "").strip().lower() in {"risk", "support"}
        }
        if "risk" in polarities and "support" not in polarities:
            stance = "risk"
        elif "support" in polarities and "risk" not in polarities:
            stance = "support"
        else:
            stance = "context"
        requirements = unique_texts([
            condition.get("relation_type") or condition.get("relationType") or condition.get("field") or condition.get("condition_id") or condition.get("conditionId")
            for condition in rule.get("conditions") or []
            if isinstance(condition, dict)
        ], 20)
        causal_path = unique_texts([
            derivation.get("relation_type") or derivation.get("relationType")
            for derivation in derivations
        ], 12)
        family_definition = hypothesis_family_definition(knowledge_basis.thesis_family)
        claim_contract = resolved_rule_claim_contract(rule, knowledge_basis)
        qualification = hypothesis_qualification(
            claim_contract,
            performance_by_rule.get(rule_id),
        )
        rows.append(HypothesisTemplate(
            template_id="hypothesis-template:" + rule_id,
            label=str(rule.get("label") or rule_id),
            version=str(rule.get("version") or "v1"),
            source_rule_ids=[rule_id],
            stance=stance,
            required_evidence_types=requirements,
            causal_path_pattern=causal_path,
            approval_status="approved-active",
            source="typedb-native-rule",
            theory_family=knowledge_basis.theory_family,
            thesis_family=knowledge_basis.thesis_family,
            knowledge_basis=knowledge_basis.to_dict(),
            prediction_target=family_definition.prediction_target if family_definition else "",
            expected_direction=family_definition.expected_direction if family_definition else stance,
            expected_outcome=family_definition.expected_outcome if family_definition else "",
            default_horizon=family_definition.default_horizon if family_definition else "multi-horizon",
            competing_family_ids=list(family_definition.competing_family_ids) if family_definition else [],
            outcome_metric=family_definition.outcome_metric if family_definition else "",
            falsification_contract=family_definition.falsification_contract if family_definition else "",
            claim_contract=claim_contract.to_dict(),
            qualification=qualification,
        ).to_dict())
    return rows


def rule_claim_coverage_from_rulebox_snapshot(snapshot: Dict[str, object]) -> Dict[str, object]:
    return rule_claim_coverage([
        item for item in (snapshot or {}).get("rules") or []
        if isinstance(item, dict) and item.get("enabled") is not False
    ])


def relation_polarity(item: Dict[str, object]) -> str:
    explicit = str(item.get("polarity") or "").strip().lower()
    if explicit in {"risk", "support", "context", "neutral"}:
        return explicit
    role = str(item.get("evidenceRole") or item.get("evidence_role") or "").strip().lower()
    if role in {"risk", "support", "counter", "context", "blocking"}:
        return "risk" if role == "blocking" else "support" if role == "counter" else role
    relation_type = str(item.get("type") or "").upper()
    if any(term in relation_type for term in ["RISK", "FAIL", "VIOLATE", "WEAKEN", "INVALIDATE", "BLOCK"]):
        return "risk"
    if any(term in relation_type for term in ["SUPPORT", "CONFIRM", "RECOVER", "OPPORTUNITY"]):
        return "support"
    return "context"


def relation_ids(rows: Iterable[Dict[str, object]]) -> List[str]:
    values = []
    for item in rows or []:
        values.append(item.get("id") or item.get("relationId") or stable_id(
            "relation-evidence",
            item.get("source"),
            item.get("type"),
            item.get("target"),
            item.get("ruleId"),
        ))
    return unique_texts(values)


def hypothesis_comparison_audit(
    candidates: Iterable[Dict[str, object]],
    ai_reviews: Iterable[Dict[str, object]] = None,
    requested_selected_hypothesis_id: object = "",
) -> HypothesisComparisonAudit:
    """Validate a bounded AI comparison against graph-owned candidates.

    An AI response may explain and choose among the current TypeDB hypotheses,
    but it may not silently add a new hypothesis, causal trace, or evidence ID.
    Incomplete comparisons abstain without selecting a hypothesis. Decision
    guardrails constrain the action separately and are never selection targets.
    """

    candidate_rows = [
        dict(item)
        for item in candidates or []
        if isinstance(item, dict) and is_selectable_hypothesis_payload(item)
    ]
    candidate_by_id = {
        str(item.get("hypothesisId") or item.get("hypothesis_id") or "").strip(): item
        for item in candidate_rows
        if str(item.get("hypothesisId") or item.get("hypothesis_id") or "").strip()
    }
    review_rows = [dict(item) for item in ai_reviews or [] if isinstance(item, dict)]
    review_by_id: Dict[str, Dict[str, object]] = {}
    invalid_hypothesis_ids: List[str] = []
    duplicate_hypothesis_ids: List[str] = []
    for row in review_rows:
        hypothesis_id = str(row.get("hypothesisId") or row.get("hypothesis_id") or row.get("id") or "").strip()
        if not hypothesis_id:
            continue
        if hypothesis_id not in candidate_by_id:
            invalid_hypothesis_ids.append(hypothesis_id)
            continue
        if hypothesis_id in review_by_id:
            duplicate_hypothesis_ids.append(hypothesis_id)
            continue
        review_by_id[hypothesis_id] = row

    invalid_evidence_ids: List[str] = []
    reviews: List[HypothesisReview] = []
    for hypothesis_id, candidate in candidate_by_id.items():
        review = review_by_id.get(hypothesis_id, {})
        supporting, invalid_supporting = bounded_hypothesis_evidence_ids(
            review.get("supportingEvidenceIds") or review.get("supporting_evidence_ids") or [],
            candidate.get("supportingEvidenceIds") or candidate.get("supporting_evidence_ids") or [],
        )
        counter, invalid_counter = bounded_hypothesis_evidence_ids(
            review.get("counterEvidenceIds") or review.get("counter_evidence_ids") or [],
            candidate.get("counterEvidenceIds") or candidate.get("counter_evidence_ids") or [],
        )
        invalid_evidence_ids.extend(invalid_supporting)
        invalid_evidence_ids.extend(invalid_counter)
        reviews.append(HypothesisReview(
            hypothesis_id=hypothesis_id,
            verdict=known_state(review.get("verdict"), HYPOTHESIS_REVIEW_VERDICTS, "unreviewed"),
            reasoning=str(review.get("reasoning") or "").strip()[:800],
            reviewed_supporting_evidence_ids=supporting,
            reviewed_counter_evidence_ids=counter,
        ))

    requested_selected = str(requested_selected_hypothesis_id or "").strip()
    valid_selected = requested_selected if requested_selected in candidate_by_id else ""
    unreviewed_hypothesis_ids = [
        item.hypothesis_id
        for item in reviews
        if (
            item.hypothesis_id not in review_by_id
            or item.verdict == "unreviewed"
            or not item.reasoning
        )
    ]
    all_reviews_present = bool(candidate_by_id) and not unreviewed_hypothesis_ids
    comparison_contract_valid = not (
        invalid_hypothesis_ids
        or invalid_evidence_ids
        or duplicate_hypothesis_ids
    )
    selected_review = next((item for item in reviews if item.hypothesis_id == valid_selected), None)
    selected_review_valid = bool(selected_review and selected_review.verdict not in {"rejected", "unreviewed"})
    if valid_selected and selected_review_valid and all_reviews_present and comparison_contract_valid:
        return HypothesisComparisonAudit(
            reviews=reviews,
            selected_hypothesis_id=valid_selected,
            comparison_state="completed",
            selection_source="ai-comparison",
            invalid_hypothesis_ids=unique_texts(invalid_hypothesis_ids),
            invalid_evidence_ids=unique_texts(invalid_evidence_ids),
            duplicate_hypothesis_ids=unique_texts(duplicate_hypothesis_ids),
            unreviewed_hypothesis_ids=[],
        )

    if not candidate_by_id:
        return HypothesisComparisonAudit(
            reviews=reviews,
            comparison_state="unavailable",
            selection_source="not-selected",
            invalid_hypothesis_ids=unique_texts(invalid_hypothesis_ids),
            invalid_evidence_ids=unique_texts(invalid_evidence_ids),
            duplicate_hypothesis_ids=unique_texts(duplicate_hypothesis_ids),
            unreviewed_hypothesis_ids=[],
            abstained=True,
            abstention_reason="현재 세대에서 선택할 TypeDB 규칙 가설이 없습니다.",
        )

    reviewed_ids = {
        item.hypothesis_id
        for item in reviews
        if item.verdict != "unreviewed"
    }
    if requested_selected and (not valid_selected or not selected_review_valid):
        state = "invalid-selection"
    elif reviewed_ids:
        state = "partial"
    else:
        state = "fallback"
    if requested_selected and not valid_selected:
        reason = "AI가 현재 TypeDB 규칙 가설 집합에 없는 가설을 선택했습니다."
    elif requested_selected and not selected_review_valid:
        reason = "AI가 기각했거나 검토하지 않은 가설을 최종 가설로 선택했습니다."
    elif duplicate_hypothesis_ids:
        reason = "AI가 같은 가설을 두 번 이상 평가해 비교 계약을 충족하지 못했습니다."
    elif invalid_evidence_ids:
        reason = "AI가 현재 가설에 연결되지 않은 근거를 사용해 비교 계약을 충족하지 못했습니다."
    elif unreviewed_hypothesis_ids:
        reason = "AI가 현재 TypeDB 규칙 가설을 모두 평가하지 못했습니다."
    else:
        reason = "AI 가설 비교 결과가 선택 조건을 충족하지 못했습니다."
    return HypothesisComparisonAudit(
        reviews=reviews,
        selected_hypothesis_id="",
        comparison_state=state,
        selection_source="abstained-" + state,
        invalid_hypothesis_ids=unique_texts(invalid_hypothesis_ids),
        invalid_evidence_ids=unique_texts(invalid_evidence_ids),
        duplicate_hypothesis_ids=unique_texts(duplicate_hypothesis_ids),
        unreviewed_hypothesis_ids=unique_texts(unreviewed_hypothesis_ids),
        abstained=True,
        abstention_reason=reason,
    )


def is_selectable_hypothesis_payload(candidate: Dict[str, object]) -> bool:
    """Return true only for a graph/rule-backed investment hypothesis."""

    item = dict(candidate or {}) if isinstance(candidate, dict) else {}
    template_id = str(item.get("templateId") or item.get("template_id") or "")
    approval_status = str(item.get("approvalStatus") or item.get("approval_status") or "")
    family_source = str(item.get("familySource") or item.get("family_source") or "")
    scope_state = str(item.get("scopeState") or item.get("scope_state") or "")
    rule_ids = item.get("supportingRuleIds") or item.get("supporting_rule_ids") or []
    return bool(rule_ids) and decision_eligible_hypothesis_payload(item) and not (
        template_id.startswith("hypothesis-template:system.")
        or approval_status == "approved-safety-policy"
        or family_source == "system-safety-policy"
        or scope_state == SYSTEM_SAFETY_SCOPE
    )


def bounded_hypothesis_evidence_ids(values: Iterable[object], allowed_values: Iterable[object]) -> tuple:
    if isinstance(values, (str, bytes)):
        values = [values]
    if isinstance(allowed_values, (str, bytes)):
        allowed_values = [allowed_values]
    allowed = set(unique_texts(allowed_values, 100))
    accepted: List[str] = []
    rejected: List[str] = []
    for value in unique_texts(values, 100):
        if value in allowed:
            accepted.append(value)
        else:
            rejected.append(value)
    return accepted, rejected


def safety_hypothesis_id(candidate_by_id: Dict[str, Dict[str, object]]) -> str:
    for hypothesis_id, candidate in candidate_by_id.items():
        template_id = str(candidate.get("templateId") or candidate.get("template_id") or "")
        approval_status = str(candidate.get("approvalStatus") or candidate.get("approval_status") or "")
        stance = str(candidate.get("stance") or "").lower()
        if (
            template_id.startswith("hypothesis-template:system.")
            or approval_status == "approved-safety-policy"
            or stance == "uncertain"
        ):
            return hypothesis_id
    return ""


def research_plan_for_hypotheses(
    question: InvestmentQuestion,
    hypotheses: List[InvestmentHypothesis],
    missing_data: Iterable[object],
    conflicts: Dict[str, object],
) -> ResearchPlan:
    missing = unique_texts(missing_data)
    unresolved: List[str] = []
    tasks: List[ResearchTask] = []
    for index, hypothesis in enumerate(hypotheses[:5]):
        question_text = "가설 '" + hypothesis.template_label + "'을 확인하거나 반박할 가장 직접적이고 최신인 근거는 무엇인가?"
        unresolved.append(question_text)
        requirements = unique_texts(hypothesis.required_evidence_types or ["provenance", "observation-time", "independent-confirmation"])
        tasks.append(ResearchTask(
            task_id=stable_id("research-task", question.question_id, hypothesis.template_id),
            question=question_text,
            purpose="TypeDB 인과 경로의 지속성, 반증 가능성, 원천 데이터 품질을 확인합니다.",
            required_evidence_types=requirements,
            related_hypothesis_ids=[hypothesis.hypothesis_id],
            source_types=source_types_for_requirements(requirements),
            max_age_minutes=research_max_age_minutes(question.horizon),
            decision_relevance="direct" if index == 0 else "important" if index < 3 else "supporting",
            priority=max(55, 100 - index * 10),
            status="blocked-by-data" if hypothesis.verification_status == "requires-research" else "ready",
        ))
    if bool((conflicts or {}).get("hasConflict")):
        conflict_question = "서로 반대인 가격·수급·이벤트 신호 중 어떤 신호가 더 신선하고 직접적인가?"
        unresolved.append(conflict_question)
        tasks.append(ResearchTask(
            task_id=stable_id("research-task", question.question_id, "resolve-conflict"),
            question=conflict_question,
            purpose="신호 충돌을 숨기지 않고 출처·시점·직접성으로 판별합니다.",
            required_evidence_types=["provenance", "observation-time", "source-reliability"],
            related_hypothesis_ids=[item.hypothesis_id for item in hypotheses],
            source_types=["official", "market-data", "news-full-text"],
            max_age_minutes=research_max_age_minutes(question.horizon),
            decision_relevance="direct",
            priority=90,
        ))
    if missing:
        missing_question = "누락 데이터 " + ", ".join(missing[:4]) + "가 들어오면 현재 결론이 바뀌는가?"
        unresolved.append(missing_question)
        tasks.append(ResearchTask(
            task_id=stable_id("research-task", question.question_id, "fill-gaps", ",".join(missing)),
            question=missing_question,
            purpose="데이터 공백이 판단 강도와 행동 후보에 미치는 영향을 확인합니다.",
            required_evidence_types=missing[:6],
            related_hypothesis_ids=[item.hypothesis_id for item in hypotheses],
            source_types=source_types_for_requirements(missing[:6]),
            max_age_minutes=research_max_age_minutes(question.horizon),
            decision_relevance="direct",
            priority=80,
            status="blocked-by-data",
        ))
    return ResearchPlan(
        plan_id=stable_id("research-plan", question.question_id),
        question_id=question.question_id,
        tasks=tasks,
        unresolved_questions=unique_texts(unresolved, 12),
    )


def source_types_for_requirements(requirements: Iterable[object]) -> List[str]:
    text = " ".join(str(item or "").lower() for item in requirements or [])
    sources = []
    if any(token in text for token in ["disclosure", "filing", "공시", "fundamental", "financial", "earning"]):
        sources.extend(["official-filing", "company-ir"])
    if any(token in text for token in ["price", "flow", "trade", "quote", "market", "volume", "수급", "가격"]):
        sources.append("market-data")
    if any(token in text for token in ["macro", "rate", "fx", "currency", "금리", "환율"]):
        sources.append("official-macro")
    if any(token in text for token in ["event", "news", "article", "사건", "뉴스"]):
        sources.append("news-full-text")
    if not sources:
        sources.extend(["official", "news-full-text"])
    return unique_texts(sources, 6)


def research_max_age_minutes(horizon: str) -> int:
    return {
        "intraday": 30,
        "short-term": 180,
        "medium-term": 1440,
        "long-term": 10080,
    }.get(str(horizon or ""), 360)


def epistemic_state(hypothesis_set: HypothesisSet, research_plan: ResearchPlan) -> Dict[str, object]:
    hypotheses = list(hypothesis_set.hypotheses)
    directional_stances = {item.stance for item in hypotheses if item.stance in {"risk", "support"}}
    if not hypotheses or all(item.evidence_state in {"unresolved", "blocked"} for item in hypotheses):
        status = "blocked"
    elif directional_stances == {"risk", "support"} or any(item.evidence_state == "contested" for item in hypotheses):
        status = "contested"
    else:
        status = "provisional"
    leading = next((item for item in hypotheses if item.evidence_state == "supported"), hypotheses[0] if hypotheses else None)
    return {
        "tboxClass": "BeliefState",
        "status": status,
        "leadingHypothesisId": leading.hypothesis_id if leading else "",
        "evidenceStates": unique_texts(item.evidence_state for item in hypotheses),
        "unresolvedQuestionCount": len(research_plan.unresolved_questions),
        "finalDecisionRequiredFromAI": True,
        "ruleDerivedPriorsAreNotFinalOpinion": True,
    }


def decision_episode_from_context(
    context: Dict[str, object],
    validated_response: Dict[str, object],
    job_id: str = "",
) -> Optional[DecisionEpisode]:
    relation_context = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
    brain = relation_context.get("investmentBrain") if isinstance(relation_context.get("investmentBrain"), dict) else {}
    hypothesis_payload = brain.get("hypothesisSet") if isinstance(brain.get("hypothesisSet"), dict) else relation_context.get("hypothesisSet")
    if not isinstance(hypothesis_payload, dict) or not hypothesis_payload.get("hypotheses"):
        return None
    question_payload = brain.get("question") if isinstance(brain.get("question"), dict) else {}
    wrapper = {
        "question": question_payload,
        "hypothesisSet": hypothesis_payload,
    }
    seed_episode = DecisionEpisode.from_dict({
        **wrapper,
        "episodeId": "seed",
        "accountId": context.get("accountId"),
        "symbol": (relation_context.get("subject") or {}).get("symbol") if isinstance(relation_context.get("subject"), dict) else "",
        "subjectName": (relation_context.get("subject") or {}).get("name") if isinstance(relation_context.get("subject"), dict) else "",
        "action": validated_response.get("action"),
        "reviewLevel": validated_response.get("reviewLevel"),
        "dataState": validated_response.get("dataState"),
        "validationState": validated_response.get("validationState"),
    })
    raw_decided_at = str(validated_response.get("referenceDate") or context.get("referenceDate") or utc_now_iso())
    decided_at = canonical_investment_timestamp(raw_decided_at) or utc_now_iso()
    reasoning_case_id = str(context.get("investmentReasoningCaseId") or "").strip()
    episode_id = (
        reasoning_case_decision_episode_id(
            reasoning_case_id,
            context.get("accountId"),
            seed_episode.symbol,
        )
        if reasoning_case_id
        else stable_id(
            "decision-episode",
            context.get("accountId"),
            seed_episode.symbol,
            relation_context.get("inferenceGenerationId"),
            job_id or context.get("jobId"),
            validated_response.get("action"),
        )
    )
    comparison = hypothesis_comparison_audit(
        [item.to_dict() for item in seed_episode.hypothesis_set.hypotheses],
        validated_response.get("hypotheses") or [],
        validated_response.get("selectedHypothesisId"),
    )
    selected_id = comparison.selected_hypothesis_id
    evidence_ids = unique_texts(
        item
        for hypothesis in seed_episode.hypothesis_set.hypotheses
        if hypothesis.hypothesis_id == selected_id
        for item in hypothesis.supporting_evidence_ids
    )
    counter_ids = unique_texts(
        item
        for hypothesis in seed_episode.hypothesis_set.hypotheses
        if hypothesis.hypothesis_id == selected_id
        for item in hypothesis.counter_evidence_ids
    )
    selected_hypothesis = next((
        item for item in seed_episode.hypothesis_set.hypotheses
        if item.hypothesis_id == selected_id
    ), None)
    outcome_contract = selected_hypothesis_outcome_contract(
        relation_context,
        selected_hypothesis,
        selected_id,
        effective_at=decided_at,
    )
    replay_manifest = decision_replay_manifest(context, relation_context)
    reasoning_snapshot = reasoning_detail_snapshot(
        relation_context,
        seed_episode.hypothesis_set.to_dict(),
        validated_response,
    )
    return DecisionEpisode(
        episode_id=episode_id,
        account_id=str(context.get("accountId") or ""),
        symbol=seed_episode.symbol,
        subject_name=seed_episode.subject_name,
        question=seed_episode.question,
        hypothesis_set=seed_episode.hypothesis_set,
        action=str(validated_response.get("action") or "HOLD"),
        review_level=known_state(validated_response.get("reviewLevel"), REVIEW_LEVELS, "check"),
        data_state=known_state(validated_response.get("dataState"), DATA_STATES, "partial"),
        validation_state=known_state(validated_response.get("validationState"), VALIDATION_STATES, "conditional"),
        decision_readiness=known_state(
            validated_response.get("decisionReadiness"),
            {"ready", "conditional", "insufficient"},
            "conditional",
        ),
        selected_hypothesis_id=selected_id,
        hypothesis_reviews=list(comparison.reviews),
        hypothesis_comparison_state=comparison.comparison_state,
        hypothesis_selection_source=comparison.selection_source,
        decision_guardrails=(
            [dict(item) for item in validated_response.get("decisionGuardrails") or [] if isinstance(item, dict)]
            or [item.to_dict() for item in seed_episode.hypothesis_set.decision_guardrails]
        ),
        decision_abstention={
            "abstained": comparison.abstained,
            "reason": comparison.abstention_reason,
            "comparisonState": comparison.comparison_state,
            "unreviewedHypothesisIds": list(comparison.unreviewed_hypothesis_ids),
            "invalidHypothesisIds": list(comparison.invalid_hypothesis_ids),
            "invalidEvidenceIds": list(comparison.invalid_evidence_ids),
            "duplicateHypothesisIds": list(comparison.duplicate_hypothesis_ids),
        } if comparison.abstained else {},
        inference_generation_id=str(relation_context.get("inferenceGenerationId") or ""),
        portfolio_id=str(
            relation_context.get("portfolioId")
            or relation_context.get("portfolioWorldId")
            or "portfolio:" + str(context.get("accountId") or "default")
        ),
        mandate_id=str(
            (relation_context.get("facts") or {}).get("mandateId")
            if isinstance(relation_context.get("facts"), dict)
            else ""
        ),
        mandate_version=str(
            (relation_context.get("facts") or {}).get("policyVersion")
            if isinstance(relation_context.get("facts"), dict)
            else ""
        ),
        source_abox_snapshot_id=str(relation_context.get("sourceAboxSnapshotId") or ""),
        evidence_ids=evidence_ids,
        counter_evidence_ids=counter_ids,
        unresolved_questions=list(validated_response.get("unresolvedQuestions") or brain.get("selfQuestions") or []),
        decision_summary=str(validated_response.get("summary") or ""),
        investment_view=str(validated_response.get("investmentView") or validated_response.get("summary") or ""),
        execution_decision=str(validated_response.get("executionDecision") or validated_response.get("currentActionPlan") or ""),
        follow_up_conditions=scoped_decision_follow_ups(
            episode_id,
            validated_response.get("followUpConditions") or [],
        ),
        unsupported_follow_ups=scoped_decision_follow_ups(
            episode_id,
            validated_response.get("unsupportedFollowUps") or [],
        ),
        decided_at=decided_at,
        status="abstained" if comparison.abstained else "active",
        source="notification-ai-hypothesis-competition",
        facts_at_decision={
            **dict(relation_context.get("facts") or {}),
            **({"investmentReasoningCaseId": context.get("investmentReasoningCaseId")} if context.get("investmentReasoningCaseId") else {}),
            **({"v2DecisionSynthesis": dict(context.get("v2DecisionSynthesis") or {})} if isinstance(context.get("v2DecisionSynthesis"), dict) and context.get("v2DecisionSynthesis") else {}),
            **({"hypothesisOutcomeContract": outcome_contract} if outcome_contract else {}),
            **({"engineManifest": replay_manifest} if replay_manifest else {}),
            "reasoningDetailSnapshot": reasoning_snapshot,
            **({"decisionReferenceDateRaw": raw_decided_at} if raw_decided_at != decided_at else {}),
        },
        research_plan=dict(brain.get("researchPlan") or relation_context.get("researchPlan") or {}),
        research_audit=dict(relation_context.get("researchCycle") or {}),
        engine_version=str(
            relation_context.get("engineVersion")
            or relation_context.get("reasoningEngineVersion")
            or INVESTMENT_BRAIN_VERSION
        ),
    )


def decision_replay_manifest(
    context: Dict[str, object],
    relation_context: Dict[str, object],
) -> Dict[str, object]:
    """Freeze the release identities required for later engine comparison."""

    frozen = (
        context.get("notificationAiReplayManifest")
        if isinstance(context.get("notificationAiReplayManifest"), dict)
        else {}
    )
    execution = (
        context.get("notificationAiExecutionAudit")
        if isinstance(context.get("notificationAiExecutionAudit"), dict)
        else {}
    )
    prompt_context = (
        relation_context.get("promptContext")
        if isinstance(relation_context.get("promptContext"), dict)
        else {}
    )
    graph_inference = (
        relation_context.get("graphStoreInference")
        if isinstance(relation_context.get("graphStoreInference"), dict)
        else {}
    )
    typedb_inference = (
        relation_context.get("typedbInference")
        if isinstance(relation_context.get("typedbInference"), dict)
        else {}
    )
    return {
        "deploymentId": str(
            relation_context.get("reasoningEngineDeploymentId")
            or relation_context.get("deploymentId")
            or frozen.get("deploymentId")
            or ""
        ),
        "releaseFingerprint": str(
            relation_context.get("reasoningEngineReleaseFingerprint")
            or relation_context.get("releaseFingerprint")
            or frozen.get("releaseFingerprint")
            or ""
        ),
        "tboxFingerprint": str(
            relation_context.get("tboxFingerprint")
            or graph_inference.get("tboxFingerprint")
            or typedb_inference.get("tboxFingerprint")
            or tbox_fingerprint()
        ),
        "ruleboxFingerprint": str(
            relation_context.get("ruleboxRulesHash")
            or relation_context.get("ruleboxFingerprint")
            or graph_inference.get("ruleboxFingerprint")
            or typedb_inference.get("ruleboxFingerprint")
            or ""
        ),
        "promptVersion": str(
            frozen.get("promptVersion")
            or execution.get("promptVersion")
            or prompt_context.get("promptVersion")
            or ""
        ),
        "modelVersion": str(
            frozen.get("modelVersion")
            or execution.get("model")
            or ""
        ),
        "decisionContractVersion": str(frozen.get("decisionContractVersion") or ""),
        "reasoningEffort": str(frozen.get("reasoningEffort") or execution.get("reasoningEffort") or ""),
        "reasoningEngineVersion": str(relation_context.get("engineVersion") or ""),
        "inferenceGenerationId": str(relation_context.get("inferenceGenerationId") or ""),
        "sourceAboxSnapshotId": str(relation_context.get("sourceAboxSnapshotId") or ""),
        "investmentReasoningCaseId": str(context.get("investmentReasoningCaseId") or ""),
        "decisionSynthesisId": str(
            (context.get("v2DecisionSynthesis") or {}).get("synthesis_id")
            or (context.get("v2DecisionSynthesis") or {}).get("synthesisId")
            or ""
        ) if isinstance(context.get("v2DecisionSynthesis"), dict) else "",
    }


def selected_hypothesis_outcome_contract(
    relation_context: Dict[str, object],
    hypothesis: Optional[InvestmentHypothesis],
    selected_hypothesis_id: str = "",
    effective_at: str = "",
) -> Dict[str, object]:
    """Freeze RuleBox outcome requirements with the exact decision episode."""

    if not hypothesis:
        return {}
    brief = relation_context.get("hypothesisDecisionBrief") if isinstance(relation_context.get("hypothesisDecisionBrief"), dict) else {}
    contracts_by_rule = brief.get("outcomeContractsByRule") if isinstance(brief.get("outcomeContractsByRule"), dict) else {}
    rule_ids = unique_texts(hypothesis.supporting_rule_ids)
    contracts = []
    for rule_id in rule_ids:
        item = contracts_by_rule.get(rule_id)
        contract = item.get("outcomeContract") if isinstance(item, dict) and isinstance(item.get("outcomeContract"), dict) else {}
        if contract:
            contracts.append(contract)
    if not contracts and isinstance(brief.get("selectedOutcomeContractCandidate"), dict):
        contracts.append(dict(brief.get("selectedOutcomeContractCandidate") or {}))
    if not contracts:
        contracts.append({})
    merged = merge_outcome_contracts(contracts)
    criteria_origin = "rulebox"
    if not list(merged.get("criteria") or []):
        merged["criteria"] = default_directional_criteria(hypothesis.stance)
        criteria_origin = "conservative-default" if merged["criteria"] else "not-applicable"
    generation_id = str(relation_context.get("inferenceGenerationId") or "")
    market_identity = hypothesis.market_hypothesis_id or hypothesis.family_id or hypothesis.template_id
    account_identity = hypothesis.account_hypothesis_overlay_id or market_identity
    payload = {
        **merged,
        "contractVersion": HYPOTHESIS_OUTCOME_CONTRACT_VERSION,
        "criteriaOrigin": criteria_origin,
        "effectiveAt": canonical_investment_timestamp(effective_at) or effective_at,
        "selectedHypothesisId": selected_hypothesis_id or hypothesis.hypothesis_id,
        "sourceRuleIds": rule_ids,
        "marketHypothesisId": hypothesis.market_hypothesis_id,
        "accountHypothesisOverlayId": hypothesis.account_hypothesis_overlay_id,
        "inferenceGenerationId": generation_id,
        "marketIndependenceKey": stable_id(
            "market-hypothesis-outcome-episode", market_identity, generation_id
        ),
        "accountIndependenceKey": stable_id(
            "account-hypothesis-outcome-episode", account_identity, generation_id
        ),
        "predictionTarget": hypothesis.prediction_target,
        "expectedDirection": hypothesis.expected_direction,
        "expectedOutcome": hypothesis.expected_outcome,
        "outcomeMetric": hypothesis.outcome_metric,
        "falsificationContract": hypothesis.falsification_contract,
    }
    payload["contractFingerprint"] = outcome_contract_fingerprint(payload)
    return payload


def float_or_zero(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def camelize(payload: Dict[str, object]) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for key, value in payload.items():
        parts = str(key).split("_")
        result[parts[0] + "".join(item[:1].upper() + item[1:] for item in parts[1:])] = value
    return result


def extract_json_object(text: str) -> Dict[str, object]:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
