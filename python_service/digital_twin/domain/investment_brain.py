from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Dict, Iterable, List, Optional


INVESTMENT_BRAIN_VERSION = "ontology-investment-brain-v1"
HYPOTHESIS_SET_VERSION = "competing-investment-hypotheses-v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def bounded_score(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return round(max(0.0, min(100.0, parsed)), 1)


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
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, object]:
        payload = camelize(asdict(self))
        payload["tasks"] = [item.to_dict() for item in self.tasks]
        return payload


@dataclass(frozen=True)
class InvestmentHypothesis:
    hypothesis_id: str
    claim: str
    stance: str
    horizon: str
    prior_confidence: float
    supporting_evidence_ids: List[str] = field(default_factory=list)
    counter_evidence_ids: List[str] = field(default_factory=list)
    supporting_rule_ids: List[str] = field(default_factory=list)
    counter_rule_ids: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    invalidation_conditions: List[str] = field(default_factory=list)
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
    confidence: float
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
    outcomes: List[ObservedOutcome] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        payload = camelize(asdict(self))
        payload["engineVersion"] = INVESTMENT_BRAIN_VERSION
        payload["question"] = self.question.to_dict()
        payload["hypothesisSet"] = self.hypothesis_set.to_dict()
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
                claim=str(item.get("claim") or ""),
                stance=str(item.get("stance") or "uncertain"),
                horizon=str(item.get("horizon") or "multi-horizon"),
                prior_confidence=bounded_score(item.get("priorConfidence") or item.get("prior_confidence")),
                supporting_evidence_ids=list(item.get("supportingEvidenceIds") or item.get("supporting_evidence_ids") or []),
                counter_evidence_ids=list(item.get("counterEvidenceIds") or item.get("counter_evidence_ids") or []),
                supporting_rule_ids=list(item.get("supportingRuleIds") or item.get("supporting_rule_ids") or []),
                counter_rule_ids=list(item.get("counterRuleIds") or item.get("counter_rule_ids") or []),
                assumptions=list(item.get("assumptions") or []),
                invalidation_conditions=list(item.get("invalidationConditions") or item.get("invalidation_conditions") or []),
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
            confidence=bounded_score(payload.get("confidence")),
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
        matches=context.get("activeRules") or context.get("matchedRules") or [],
        signal_conflicts=context.get("signalConflicts") or {},
        missing_data=context.get("missingData") or facts.get("missingData") or [],
        inference_generation_id=str(context.get("inferenceGenerationId") or ""),
        question=question,
    )
    return {
        "question": question.to_dict(),
        "hypothesisSet": hypothesis_set.to_dict(),
        "researchPlan": research_plan.to_dict(),
        "selfQuestions": list(research_plan.unresolved_questions),
        "epistemicState": epistemic_state(hypothesis_set, research_plan),
    }


def build_competing_hypotheses(
    subject: Dict[str, object],
    facts: Dict[str, object],
    relations: Iterable[Dict[str, object]],
    matches: Iterable[Dict[str, object]],
    signal_conflicts: Dict[str, object],
    missing_data: Iterable[object],
    inference_generation_id: str,
    question: InvestmentQuestion,
) -> tuple:
    symbol = str(subject.get("symbol") or facts.get("symbol") or question.subject_symbol or "").upper().strip()
    name = str(subject.get("name") or facts.get("name") or question.subject_name or symbol)
    relation_rows = [dict(item) for item in relations or [] if isinstance(item, dict)]
    match_rows = [dict(item) for item in matches or [] if isinstance(item, dict)]
    risk_rows = [item for item in relation_rows if relation_polarity(item) == "risk"]
    support_rows = [item for item in relation_rows if relation_polarity(item) == "support"]
    context_rows = [item for item in relation_rows if relation_polarity(item) not in {"risk", "support"}]
    risk_score = hypothesis_prior(risk_rows, match_rows, "risk", signal_conflicts)
    support_score = hypothesis_prior(support_rows, match_rows, "support", signal_conflicts)
    uncertainty_score = uncertainty_prior(missing_data, signal_conflicts, risk_score, support_score)
    risk_evidence = relation_ids(risk_rows)
    support_evidence = relation_ids(support_rows)
    context_evidence = relation_ids(context_rows)
    risk_rules = rule_ids(risk_rows, match_rows, "risk")
    support_rules = rule_ids(support_rows, match_rows, "support")
    all_rules = unique_texts(risk_rules + support_rules + rule_ids(context_rows, match_rows, ""))
    hypothesis_seed = stable_id("hypothesis-set", question.question_id, inference_generation_id, symbol)
    risk_id = stable_id("hypothesis", hypothesis_seed, "risk-continuation")
    support_id = stable_id("hypothesis", hypothesis_seed, "support-recovery")
    uncertainty_id = stable_id("hypothesis", hypothesis_seed, "uncertainty")
    hypotheses = [
        InvestmentHypothesis(
            hypothesis_id=risk_id,
            claim=name + "의 위험 신호가 이어져 현재 행동을 더 보수적으로 바꿔야 한다.",
            stance="risk",
            horizon=question.horizon,
            prior_confidence=risk_score,
            supporting_evidence_ids=risk_evidence,
            counter_evidence_ids=support_evidence,
            supporting_rule_ids=risk_rules,
            counter_rule_ids=support_rules,
            assumptions=["현재 위험 관계의 원천 데이터와 유효시각이 판단 시점에도 유효합니다."],
            invalidation_conditions=["가격·수급·이벤트 관계가 회복 방향으로 바뀌고 다음 추론 세대에서도 유지됩니다."],
        ),
        InvestmentHypothesis(
            hypothesis_id=support_id,
            claim=name + "의 지지 또는 회복 관계가 위험을 상쇄해 행동 강도를 유지하거나 완화할 수 있다.",
            stance="support",
            horizon=question.horizon,
            prior_confidence=support_score,
            supporting_evidence_ids=support_evidence,
            counter_evidence_ids=risk_evidence,
            supporting_rule_ids=support_rules,
            counter_rule_ids=risk_rules,
            assumptions=["지지 관계가 일시적 호가나 단일 관측이 아니라 다음 기간에도 확인됩니다."],
            invalidation_conditions=["지지 관계가 사라지거나 위험 관계가 더 높은 신뢰도로 재확인됩니다."],
        ),
        InvestmentHypothesis(
            hypothesis_id=uncertainty_id,
            claim="현재 근거 충돌 또는 데이터 공백 때문에 " + name + "의 행동을 단정하기보다 추가 확인이 필요하다.",
            stance="uncertain",
            horizon=question.horizon,
            prior_confidence=uncertainty_score,
            supporting_evidence_ids=context_evidence,
            counter_evidence_ids=unique_texts(risk_evidence + support_evidence),
            supporting_rule_ids=all_rules,
            counter_rule_ids=[],
            assumptions=["누락·지연·충돌 데이터가 해소되면 가설 우선순위가 달라질 수 있습니다."],
            invalidation_conditions=["핵심 데이터 공백이 채워지고 위험 또는 지지 근거가 여러 관계에서 같은 방향으로 확인됩니다."],
        ),
    ]
    research_plan = research_plan_for_hypotheses(question, hypotheses, missing_data, signal_conflicts)
    return HypothesisSet(
        hypothesis_set_id=hypothesis_seed,
        subject_symbol=symbol,
        question_id=question.question_id,
        hypotheses=hypotheses,
        inference_generation_id=inference_generation_id,
    ), research_plan


def relation_polarity(item: Dict[str, object]) -> str:
    explicit = str(item.get("polarity") or "").strip().lower()
    if explicit in {"risk", "support", "context", "neutral"}:
        return explicit
    if float_or_zero(item.get("riskImpact")) > float_or_zero(item.get("supportImpact")):
        return "risk"
    if float_or_zero(item.get("supportImpact")) > float_or_zero(item.get("riskImpact")):
        return "support"
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


def rule_ids(rows: Iterable[Dict[str, object]], matches: Iterable[Dict[str, object]], polarity: str) -> List[str]:
    values = [item.get("ruleId") for item in rows or []]
    if not values:
        for item in matches or []:
            breakdown = item.get("scoreBreakdown") if isinstance(item.get("scoreBreakdown"), dict) else {}
            if polarity == "risk" and float_or_zero(breakdown.get("riskPressure")) <= 0:
                continue
            if polarity == "support" and float_or_zero(breakdown.get("supportEvidence")) <= 0:
                continue
            values.append(item.get("ruleId") or item.get("rule_id"))
    return unique_texts(values)


def hypothesis_prior(
    rows: Iterable[Dict[str, object]],
    matches: Iterable[Dict[str, object]],
    polarity: str,
    conflicts: Dict[str, object],
) -> float:
    values = []
    for item in rows or []:
        values.extend([item.get("strength"), item.get("strengthScore"), item.get("confidence")])
    conflict_key = "riskPressure" if polarity == "risk" else "supportEvidence"
    values.append((conflicts or {}).get(conflict_key))
    for item in matches or []:
        breakdown = item.get("scoreBreakdown") if isinstance(item.get("scoreBreakdown"), dict) else {}
        directional_score = float_or_zero(breakdown.get(conflict_key))
        if directional_score > 0:
            values.append(directional_score)
            values.append(item.get("strengthScore"))
    scores = [normalize_score(item) for item in values if item not in (None, "")]
    return bounded_score(max(scores, default=25.0), 25.0)


def uncertainty_prior(
    missing_data: Iterable[object],
    conflicts: Dict[str, object],
    risk_score: float,
    support_score: float,
) -> float:
    conflict = bool((conflicts or {}).get("hasConflict"))
    missing_count = len(list(missing_data or []))
    balance = max(0.0, 100.0 - abs(risk_score - support_score))
    components = [35.0, balance * 0.55]
    if conflict:
        components.append(70.0)
    if missing_count:
        components.append(min(85.0, 45.0 + missing_count * 8.0))
    return bounded_score(max(components))


def research_plan_for_hypotheses(
    question: InvestmentQuestion,
    hypotheses: List[InvestmentHypothesis],
    missing_data: Iterable[object],
    conflicts: Dict[str, object],
) -> ResearchPlan:
    missing = unique_texts(missing_data)
    unresolved = ["위험 가설과 지지 가설 중 어느 쪽이 다음 추론 세대에서도 유지되는가?"]
    tasks = [
        ResearchTask(
            task_id=stable_id("research-task", question.question_id, "risk-support-comparison"),
            question=unresolved[0],
            purpose="단일 최고 점수가 아니라 경쟁 가설의 지속성과 반증을 비교합니다.",
            required_evidence_types=["price-path", "flow", "event", "data-freshness"],
            related_hypothesis_ids=[item.hypothesis_id for item in hypotheses],
            priority=100,
        )
    ]
    if bool((conflicts or {}).get("hasConflict")):
        conflict_question = "서로 반대인 가격·수급·이벤트 신호 중 어떤 신호가 더 신선하고 직접적인가?"
        unresolved.append(conflict_question)
        tasks.append(ResearchTask(
            task_id=stable_id("research-task", question.question_id, "resolve-conflict"),
            question=conflict_question,
            purpose="신호 충돌을 숨기지 않고 출처·시점·직접성으로 판별합니다.",
            required_evidence_types=["provenance", "observation-time", "source-reliability"],
            related_hypothesis_ids=[item.hypothesis_id for item in hypotheses],
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
            priority=80,
            status="blocked-by-data",
        ))
    return ResearchPlan(
        plan_id=stable_id("research-plan", question.question_id),
        question_id=question.question_id,
        tasks=tasks,
        unresolved_questions=unresolved,
    )


def epistemic_state(hypothesis_set: HypothesisSet, research_plan: ResearchPlan) -> Dict[str, object]:
    ranked = sorted(hypothesis_set.hypotheses, key=lambda item: item.prior_confidence, reverse=True)
    gap = ranked[0].prior_confidence - ranked[1].prior_confidence if len(ranked) > 1 else 0.0
    return {
        "tboxClass": "BeliefState",
        "status": "contested" if gap < 15 else "provisional",
        "leadingHypothesisId": ranked[0].hypothesis_id if ranked else "",
        "priorConfidenceGap": round(gap, 1),
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
        "confidence": validated_response.get("confidence"),
    })
    decided_at = str(validated_response.get("referenceDate") or context.get("referenceDate") or utc_now_iso())
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
        confidence=bounded_score(validated_response.get("confidence"), 50.0),
        selected_hypothesis_id=selected_id,
        inference_generation_id=str(relation_context.get("inferenceGenerationId") or ""),
        evidence_ids=evidence_ids,
        counter_evidence_ids=counter_ids,
        unresolved_questions=list(validated_response.get("unresolvedQuestions") or brain.get("selfQuestions") or []),
        decision_summary=str(validated_response.get("summary") or ""),
        decided_at=decided_at,
        source="notification-ai-hypothesis-competition",
        facts_at_decision=dict(relation_context.get("facts") or {}),
    )


def normalize_score(value: object) -> float:
    parsed = float_or_zero(value)
    if 0 < parsed <= 1:
        parsed *= 100
    return bounded_score(parsed)


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
