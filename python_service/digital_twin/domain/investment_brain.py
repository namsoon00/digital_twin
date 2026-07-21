from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Dict, Iterable, List, Optional

from .ontology_decision_state import DATA_STATES, REVIEW_LEVELS, VALIDATION_STATES


INVESTMENT_BRAIN_VERSION = "ontology-investment-brain-v2"
HYPOTHESIS_SET_VERSION = "typedb-causal-hypotheses-v2"
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

    def to_dict(self) -> Dict[str, object]:
        return camelize(asdict(self))


@dataclass(frozen=True)
class HypothesisSet:
    hypothesis_set_id: str
    subject_symbol: str
    question_id: str
    hypotheses: List[InvestmentHypothesis]
    comparison_required: bool = True
    minimum_comparison_count: int = 3
    inference_generation_id: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    version: str = HYPOTHESIS_SET_VERSION

    def to_dict(self) -> Dict[str, object]:
        payload = camelize(asdict(self))
        payload["hypotheses"] = [item.to_dict() for item in self.hypotheses]
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
    selected_hypothesis_id: str = ""
    inference_generation_id: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    counter_evidence_ids: List[str] = field(default_factory=list)
    unresolved_questions: List[str] = field(default_factory=list)
    decision_summary: str = ""
    decided_at: str = field(default_factory=utc_now_iso)
    status: str = "active"
    source: str = "notification-ai"
    facts_at_decision: Dict[str, object] = field(default_factory=dict)
    research_plan: Dict[str, object] = field(default_factory=dict)
    research_audit: Dict[str, object] = field(default_factory=dict)
    outcomes: List[ObservedOutcome] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        payload = camelize(asdict(self))
        payload["engineVersion"] = INVESTMENT_BRAIN_VERSION
        payload["question"] = self.question.to_dict()
        payload["hypothesisSet"] = self.hypothesis_set.to_dict()
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
            ))
        hypothesis_set = HypothesisSet(
            hypothesis_set_id=str(hypothesis_payload.get("hypothesisSetId") or hypothesis_payload.get("hypothesis_set_id") or ""),
            subject_symbol=str(hypothesis_payload.get("subjectSymbol") or hypothesis_payload.get("subject_symbol") or ""),
            question_id=str(hypothesis_payload.get("questionId") or hypothesis_payload.get("question_id") or question.question_id),
            hypotheses=hypotheses,
            comparison_required=bool(hypothesis_payload.get("comparisonRequired", True)),
            minimum_comparison_count=int(hypothesis_payload.get("minimumComparisonCount") or 3),
            inference_generation_id=str(hypothesis_payload.get("inferenceGenerationId") or ""),
            created_at=str(hypothesis_payload.get("createdAt") or utc_now_iso()),
            version=str(hypothesis_payload.get("version") or HYPOTHESIS_SET_VERSION),
        )
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
            selected_hypothesis_id=str(payload.get("selectedHypothesisId") or ""),
            inference_generation_id=str(payload.get("inferenceGenerationId") or ""),
            evidence_ids=list(payload.get("evidenceIds") or []),
            counter_evidence_ids=list(payload.get("counterEvidenceIds") or []),
            unresolved_questions=list(payload.get("unresolvedQuestions") or []),
            decision_summary=str(payload.get("decisionSummary") or ""),
            decided_at=str(payload.get("decidedAt") or utc_now_iso()),
            status=str(payload.get("status") or "active"),
            source=str(payload.get("source") or "notification-ai"),
            facts_at_decision=dict(payload.get("factsAtDecision") or {}),
            research_plan=dict(payload.get("researchPlan") or {}),
            research_audit=dict(payload.get("researchAudit") or {}),
            outcomes=outcomes,
        )


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


def default_question(subject: Dict[str, object], facts: Dict[str, object], inference_generation_id: str = "") -> InvestmentQuestion:
    symbol = str(subject.get("symbol") or facts.get("symbol") or "").upper().strip()
    name = str(subject.get("name") or facts.get("name") or symbol)
    source = "관심종목" if facts.get("isWatchlist") else "보유종목"
    return InvestmentQuestion.create(
        name + " " + source + "에서 현재 행동을 바꿀 만큼 중요한 변화가 있는가?",
        subject_symbol=symbol,
        subject_name=name,
        asked_at=str(facts.get("observedAt") or inference_generation_id or utc_now_iso()),
        source="system-self-question",
    )


def hypothesis_set_from_relation_context(
    relation_context: Dict[str, object],
    question: Optional[InvestmentQuestion] = None,
) -> Dict[str, object]:
    context = relation_context if isinstance(relation_context, dict) else {}
    subject = context.get("subject") if isinstance(context.get("subject"), dict) else {}
    facts = context.get("facts") if isinstance(context.get("facts"), dict) else {}
    question = question or default_question(subject, facts, str(context.get("inferenceGenerationId") or ""))
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
) -> tuple:
    symbol = str(subject.get("symbol") or facts.get("symbol") or question.subject_symbol or "").upper().strip()
    name = str(subject.get("name") or facts.get("name") or question.subject_name or symbol)
    relation_rows = [dict(item) for item in relations or [] if isinstance(item, dict)]
    trace_rows = [dict(item) for item in traces or [] if isinstance(item, dict)]
    match_rows = [dict(item) for item in matches or [] if isinstance(item, dict)]
    policy = policy if isinstance(policy, dict) else {}
    minimum_count = int_setting(policy.get("minimumComparisonCount"), 3, 2, 6)
    maximum_count = int_setting(policy.get("maximumComparisonCount"), 8, minimum_count, 12)
    hypothesis_seed = stable_id("hypothesis-set", question.question_id, inference_generation_id, symbol)
    rule_keys = ordered_rule_ids(match_rows, trace_rows, relation_rows)
    hypotheses = [
        hypothesis_from_inference_rule(
            hypothesis_seed,
            name,
            question,
            rule_id,
            relations_for_rule(relation_rows, rule_id),
            traces_for_rule(trace_rows, rule_id),
            matches_for_rule(match_rows, rule_id),
            relation_rows,
        )
        for rule_id in rule_keys
    ]
    hypotheses = [item for item in hypotheses if item is not None]
    hypotheses = diverse_hypotheses(hypotheses, maximum_count)
    hypotheses = add_safety_hypotheses(
        hypotheses,
        hypothesis_seed,
        name,
        question,
        missing_data,
        signal_conflicts,
        relation_rows,
        minimum_count,
        maximum_count,
    )
    research_plan = research_plan_for_hypotheses(question, hypotheses, missing_data, signal_conflicts)
    return HypothesisSet(
        hypothesis_set_id=hypothesis_seed,
        subject_symbol=symbol,
        question_id=question.question_id,
        hypotheses=hypotheses,
        minimum_comparison_count=minimum_count,
        inference_generation_id=inference_generation_id,
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
    name: str,
    question: InvestmentQuestion,
    rule_id: str,
    rows: List[Dict[str, object]],
    traces: List[Dict[str, object]],
    matches: List[Dict[str, object]],
    all_rows: List[Dict[str, object]],
) -> Optional[InvestmentHypothesis]:
    if not rule_id or not (rows or traces or matches):
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
    return InvestmentHypothesis(
        hypothesis_id=stable_id("hypothesis", hypothesis_seed, template_id),
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
    )


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
    result = list(hypotheses)
    directional_stances = {item.stance for item in result if item.stance in {"risk", "support"}}
    evidence_safety_needed = len(result) < minimum_count or bool(list(missing_data or [])) or bool((conflicts or {}).get("hasConflict"))
    if result and evidence_safety_needed:
        missing = unique_texts(missing_data)
        evidence_state = "contested" if bool((conflicts or {}).get("hasConflict")) else "unresolved"
        safety = InvestmentHypothesis(
            hypothesis_id=stable_id("hypothesis", hypothesis_seed, SYSTEM_ABSTENTION_TEMPLATE_ID),
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
        )
        result = with_reserved_safety_slot(result, safety, maximum_count)
    if result and (len(result) < minimum_count or len(directional_stances) < 2):
        leading = result[0]
        null_template = "hypothesis-template:system.null-challenge.v1"
        safety = InvestmentHypothesis(
            hypothesis_id=stable_id("hypothesis", hypothesis_seed, null_template),
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
        )
        result = with_reserved_safety_slot(result, safety, maximum_count)
    return result[:maximum_count]


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
        ).to_dict())
    return rows


def hypothesis_templates_from_rulebox_snapshot(snapshot: Dict[str, object]) -> List[Dict[str, object]]:
    rows = []
    for rule in (snapshot or {}).get("rules") or []:
        if not isinstance(rule, dict) or rule.get("enabled") is False:
            continue
        rule_id = row_rule_id(rule)
        if not rule_id:
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
        ).to_dict())
    return rows


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
    episode_id = stable_id(
        "decision-episode",
        context.get("accountId"),
        seed_episode.symbol,
        relation_context.get("inferenceGenerationId"),
        job_id or context.get("jobId"),
        validated_response.get("action"),
    )
    selected_id = str(validated_response.get("selectedHypothesisId") or "")
    valid_ids = {item.hypothesis_id for item in seed_episode.hypothesis_set.hypotheses}
    if selected_id not in valid_ids:
        selected_id = str((brain.get("epistemicState") or {}).get("leadingHypothesisId") or "")
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
        selected_hypothesis_id=selected_id,
        inference_generation_id=str(relation_context.get("inferenceGenerationId") or ""),
        evidence_ids=evidence_ids,
        counter_evidence_ids=counter_ids,
        unresolved_questions=list(validated_response.get("unresolvedQuestions") or brain.get("selfQuestions") or []),
        decision_summary=str(validated_response.get("summary") or ""),
        decided_at=decided_at,
        source="notification-ai-hypothesis-competition",
        facts_at_decision={
            **dict(relation_context.get("facts") or {}),
            **({"decisionReferenceDateRaw": raw_decided_at} if raw_decided_at != decided_at else {}),
        },
        research_plan=dict(brain.get("researchPlan") or relation_context.get("researchPlan") or {}),
        research_audit=dict(relation_context.get("researchCycle") or {}),
    )


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
