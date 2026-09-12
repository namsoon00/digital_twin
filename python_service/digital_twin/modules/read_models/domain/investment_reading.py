"""Question-oriented presentation of saved opinions, never an action evaluator."""

import math
import re
from typing import Mapping

from digital_twin.modules.read_models.domain.customer_investment_document import customer_investment_text


VERSION = "investment-reading-v1"
ACTION_LABELS = {
    "BUY": "매수 검토", "ADD": "추가매수 검토", "HOLD": "보유 유지",
    "TRIM": "비중 축소 검토", "SELL": "매도 검토", "AVOID": "진입 회피",
}
DISPOSITIONS = {
    "HYPOTHESIS_QUALIFICATION_PENDING": "검토 중인 설명이 실제 결과와 맞는지 확인하고 있어, 아직 투자 의견으로 사용할 수 없습니다.",
    "HYPOTHESIS_RESEARCH_ONLY": "현재 설명은 연구 단계입니다. 투자에 사용할 만큼 검증되지 않았습니다.",
    "RULE_COVERAGE_GAP_CANDIDATE": "현재 변화에 대한 설명을 검증하는 중입니다. 매매 의견은 아직 없습니다.",
    "WAITING_FOR_SCHEDULED_SOURCE": "판단에 필요한 자료가 아직 발표되지 않았습니다.",
    "DATA_SOURCE_FAILURE": "필요한 자료를 가져오지 못해 현재 투자 의견을 제공할 수 없습니다.",
    "JUDGEMENT_BLOCKED": "투자 의견을 확정하는 데 필요한 확인이 끝나지 않았습니다.",
    "NO_MATERIAL_PREDICTIVE_RULE_MATCH": "이번 분석에서는 투자 행동을 바꿀 근거가 확인되지 않았습니다. 보유 유지 의견을 뜻하지는 않습니다.",
}
FACTS = {
    "currentPrice": ("가격", ""), "lastPrice": ("가격", ""), "price": ("가격", ""),
    "profitLossRate": ("보유 수익률", "%"), "positionAccountWeight": ("계좌 내 비중", "%"),
    "quantity": ("보유 수량", ""), "revenueGrowthRate": ("매출 성장률", "%"),
}


def _map(value):
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value):
    return list(value) if isinstance(value, (list, tuple)) else []


def _prose(value):
    if isinstance(value, Mapping):
        value = value.get("summary") or value.get("detail") or value.get("reason") or value.get("text") or value.get("label") or ""
    if not isinstance(value, str):
        return ""
    # Identifier-only diagnostics remain in the full model/trace views.
    if re.search(r"validated-model-signal:|graph\.[a-z]|subject-decision-case:|HAS_[A-Z]|HAS [A-Z]", value):
        return ""
    # This catalog sentence describes engine wiring, not the investment thesis.
    value = value.replace("원시 숫자 조건 대신 시점 고정 모델 신호를 TypeDB가 해석합니다.", "")
    value = value.replace("TypeDB가", "분석 시스템이").replace("인과 경로가", "원인과 결과의 연결이")
    return customer_investment_text(value).strip()


def _texts(values):
    return list(dict.fromkeys(text for value in values if (text := _prose(value))))


def investment_reading(value: Mapping[str, object], compact: bool = False):
    """Keep meaning, evidence, unavailable information and operating state distinct.

    Every statement is a projection of this case. No market call, source fetch,
    notification or future monitoring promise is created here.
    """
    case = _map(value)
    decision = _map(case.get("decision"))
    explanation = _map(case.get("explanation"))
    subject = _map(case.get("subjectDecisionCase"))
    lineage = _map(case.get("reasoningLineage"))
    ai = _map(lineage.get("ai")) or _map(subject.get("aiInsight"))
    authored = (
        ai.get("status") in {"ai-authored", "completed"}
        and ai.get("currentGeneration") is True
        and ai.get("aiAuthored") is True
        and ai.get("publicationContractPassed") is True
    )
    assessment = _map(ai.get("insightAssessment")) if authored else {}
    if assessment.get("publishable") is not True:
        assessment = {}
    action = str(decision.get("action") or "").upper()
    blocked = case.get("readinessState") in {"blocked", "error"} or decision.get("state") in {"blocked", "error"}
    final = action in ACTION_LABELS and not blocked
    kind = "opinion" if final else "interpretation" if assessment else "unavailable" if blocked else "awaiting"
    status = ACTION_LABELS[action] if final else "참고 해석 · 매매 의견 없음" if assessment else "투자 의견 미확정"
    disposition = str(decision.get("dispositionCode") or subject.get("dispositionCode") or "")
    unavailable_reason = DISPOSITIONS.get(disposition) or (
        "현재 자료의 분석이 완료되지 않아 매수·보유·매도 의견이 확정되지 않았습니다."
    )
    headline = _prose(assessment.get("dominantThesis")) or (_prose(case.get("headline")) if final else "") or unavailable_reason
    meaning = _prose(assessment.get("investmentImplication"))
    primary = _map(explanation.get("primaryCause"))
    reasons = _texts([assessment.get("causalMechanism")] + _rows(explanation.get("supportingCauses")))
    if not reasons and final:
        reasons = _texts([primary.get("summary")])
    reasons = [text for text in reasons if text != headline]
    counters = _texts(_rows(explanation.get("counterCauses")) + _rows(assessment.get("risks")))
    limits = _texts(_rows(explanation.get("constraints")))
    evidence = _map(case.get("evidence"))
    gaps = _texts(
        _rows(evidence.get("missingDataItems")) + _rows(evidence.get("missingData"))
        + _rows(explanation.get("dataGaps")) + _rows(subject.get("dataGaps"))
        + [row.get("reason") for row in _rows(case.get("statusDimensions"))
           if isinstance(row, Mapping) and row.get("id") == "data" and row.get("state") in {"blocked", "error", "warning"}]
    )
    checks = _texts(
        [assessment.get("invalidationCondition")]
        + (_rows(ai.get("nextChecks")) if authored else [])
        + _rows(decision.get("requiredChecks")) + _rows(subject.get("nextChecks"))
        + _rows(explanation.get("changeConditions"))
    )
    if not checks and not disposition:
        checks = _texts([case.get("nextAction")])
    transition = _map(ai.get("insightTransition")) if authored else {}
    changes = _texts([transition.get("summary") or transition.get("reason")]) if assessment and transition.get("kind") in {
        "initial-insight", "material-insight-change", "unchanged-insight"
    } else []
    facts = []
    for group in _rows(_map(case.get("currentState")).get("groups")):
        for fact in _rows(_map(group).get("items")):
            fact = _map(fact)
            field = str(fact.get("field") or "")
            number = fact.get("value")
            if field not in FACTS or isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
                continue
            label, unit = FACTS[field]
            facts.append({
                "label": label, "value": number, "unit": unit, "field": field,
                "source": str(fact.get("source") or ""),
                "asOf": str(fact.get("asOf") or fact.get("sourceAsOf") or ""),
                "evidenceId": str(fact.get("id") or ""),
            })
    freshness = _map(case.get("freshness"))
    explanations = []
    for row in _rows(case.get("scenarios")):
        if not isinstance(row, Mapping):
            continue
        contract = _map(row.get("claimContract"))
        qualification = _map(row.get("qualification"))
        basis = row.get("plainLanguageBasis") or _map(row.get("knowledgeBasis")).get("plainLanguageBasis")
        explanations.append({
            "id": str(row.get("id") or row.get("hypothesisId") or ""),
            "title": _prose(contract.get("expectedOutcome") or row.get("title")),
            "claim": _prose(basis or row.get("claim")),
            "selected": bool(row.get("selected")) and final,
            "qualification": str(qualification.get("status") or "unknown"),
            "reason": _prose(qualification.get("reason")),
            "conditions": _texts([contract.get("falsificationContract")] + _rows(row.get("invalidationConditions"))),
        })
    result = {
        "version": VERSION, "kind": kind, "status": status, "headline": headline,
        "meaning": meaning,
        "meaningEmpty": "투자에 미칠 영향이 별도로 설명되지 않았습니다." if final else unavailable_reason,
        "changes": changes, "changeEmpty": "이전 분석과 비교해 확인된 변화가 기록되지 않았습니다.",
        "reasons": reasons, "reasonLabel": "이 해석을 뒷받침하는 이유" if final or assessment else "검토 중인 설명",
        "counters": counters, "limits": limits, "gaps": gaps, "nextChecks": checks,
        "facts": facts,
        "explanations": explanations,
        "sourceAt": str(freshness.get("sourceAsOf") or ""),
        "opinionAt": str(freshness.get("decisionAsOf") or case.get("decidedAt") or case.get("updatedAt") or ""),
        "evidenceTarget": "evidence", "modelTarget": "reasoning",
    }
    if compact:
        return {key: result[key] for key in ("version", "kind", "status", "headline", "meaning", "meaningEmpty", "opinionAt", "sourceAt")}
    return result
