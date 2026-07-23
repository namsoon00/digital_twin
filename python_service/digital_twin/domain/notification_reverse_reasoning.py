"""Snapshot-bound reverse reasoning for an individual notification.

The notification outbox is the audit boundary for an investment alert.  This
read model deliberately reconstructs an explanation from the context captured
with that job; it never reads the current active graph generation to explain a
historical decision.  That distinction keeps an alert explainable even after a
RuleBox or ABox refresh.
"""

import math
import re
from typing import Dict, Iterable, List

from .notification_ai_context import relation_context_value
from .notification_ai_gate_sources import all_source_urls_for_context, source_detail_map


TRACE_VERSION = "notification-reverse-reasoning-v1"

ACTION_LABELS = {
    "BUY": "매수",
    "ADD": "추가매수",
    "HOLD": "보유",
    "TRIM": "분할축소",
    "SELL": "매도",
    "AVOID": "회피",
}

STANCE_LABELS = {
    "risk": "위험 경로",
    "support": "우호 경로",
    "context": "참고 경로",
    "uncertain": "판단 보류 경로",
}

EVIDENCE_STATE_LABELS = {
    "supported": "현재 근거로 확인됨",
    "contested": "반대 근거가 함께 있음",
    "unresolved": "추가 확인 필요",
    "blocked": "자료 문제로 판단 보류",
}

VERDICT_LABELS = {
    "supported": "AI가 지지",
    "weakened": "AI가 약화로 판단",
    "rejected": "AI가 기각",
    "unresolved": "AI가 결론 보류",
    "unreviewed": "AI 비교 기록 없음",
}

COMPARISON_STATE_LABELS = {
    "completed": "경쟁 가설 비교 완료",
    "partial": "일부 가설만 비교됨",
    "fallback": "안전 가설로 보수적 선택",
    "invalid-selection": "선택 가설 검증 실패",
    "unavailable": "가설 비교 기록 없음",
}

FACT_FIELDS = (
    ("currentPrice", "현재가"),
    ("averagePrice", "평균매입가"),
    ("profitLossRate", "보유 수익률(%)"),
    ("profitLossRateDeltaPct", "수익률 변화(%p)"),
    ("positionWeight", "종목 비중(%)"),
    ("quantity", "보유 수량"),
    ("ma5", "5일 평균"),
    ("ma5Distance", "5일 평균 괴리(%)"),
    ("ma20", "20일 평균"),
    ("ma20Distance", "20일 평균 괴리(%)"),
    ("ma60", "60일 평균"),
    ("ma60Distance", "60일 평균 괴리(%)"),
    ("priceChangeRate", "가격 변화율(%)"),
    ("volume", "거래량"),
    ("timeAdjustedVolumeRatio", "시간 보정 거래량 배율"),
    ("tradeStrength", "체결강도"),
    ("bidAskImbalance", "호가 불균형(%)"),
    ("foreignNetVolume", "외국인 순매수"),
    ("institutionNetVolume", "기관 순매수"),
    ("usdKrw", "원·달러 환율"),
    ("us10yYield", "미국 10년 금리(%)"),
    ("krBaseRate", "한국 기준금리(%)"),
    ("btcChange24h", "비트코인 24시간 변화(%)"),
    ("directNewsCount", "직접 관련 뉴스 수"),
)


def _text(value: object, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _rows(value: object, limit: int = 24) -> List[object]:
    if isinstance(value, (str, bytes)):
        return [value] if str(value).strip() else []
    if not isinstance(value, list):
        return []
    return list(value)[:limit]


def _dict(value: object) -> Dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _unique(values: Iterable[object], limit: int = 12, text_limit: int = 320) -> List[str]:
    result: List[str] = []
    for value in values or []:
        text = _text(value, text_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _present(value: object) -> bool:
    if value in (None, "", []):
        return False
    return not isinstance(value, float) or math.isfinite(value)


def _display_value(value: object) -> str:
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return ("%.6f" % value).rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple)):
        return ", ".join(_unique(value, limit=8, text_limit=100))
    return _text(value, 160)


def _fact_rows(facts: Dict[str, object]) -> List[Dict[str, str]]:
    rows = []
    for key, label in FACT_FIELDS:
        value = facts.get(key)
        if not _present(value):
            continue
        displayed = _display_value(value)
        if displayed:
            rows.append({"key": key, "label": label, "value": displayed})
    return rows[:18]


def _missing_data_rows(relation: Dict[str, object], facts: Dict[str, object]) -> List[str]:
    source = relation.get("missingData") or facts.get("missingData") or []
    values = []
    for item in _rows(source, 12):
        if isinstance(item, dict):
            label = _text(item.get("label") or item.get("key"), 100)
            effect = _text(item.get("effect") or item.get("reason"), 220)
            values.append(label + (": " + effect if label and effect else "") or effect)
        else:
            values.append(item)
    return _unique(values, 8)


def _source_rows(context: Dict[str, object]) -> List[Dict[str, str]]:
    details = source_detail_map(context)
    rows = []
    for url in all_source_urls_for_context(context)[:8]:
        item = _dict(details.get(url))
        payload = _dict(item.get("payload"))
        title = _text(item.get("title") or item.get("summary") or payload.get("title"), 220)
        source = _text(item.get("source") or item.get("domain") or item.get("provider") or payload.get("source"), 80)
        impact = _text(item.get("stockImpactLabel") or payload.get("stockImpactLabel") or item.get("impact"), 80)
        rows.append({
            "title": title or source or "원문",
            "source": source,
            "publishedAt": _text(item.get("publishedAt") or item.get("published_at") or payload.get("publishedAt"), 80),
            "impact": impact,
            "url": str(url),
        })
    return rows


def _rule_rows(relation: Dict[str, object], selected_rule_ids: Iterable[object]) -> List[Dict[str, object]]:
    selected = set(_unique(selected_rule_ids, 40, 180))
    decision = _dict(relation.get("decision"))
    if decision.get("selectedRuleId"):
        selected.add(str(decision.get("selectedRuleId")))
    rows = []
    for item in _rows(relation.get("activeRules") or relation.get("matchedRules"), 16):
        if not isinstance(item, dict) or item.get("referenceOnly") or item.get("reference_only"):
            continue
        rule_id = _text(item.get("ruleId") or item.get("rule_id"), 180)
        trace_id = _text(item.get("inferenceTraceId") or item.get("inference_trace_id"), 220)
        rows.append({
            "ruleId": rule_id,
            "label": _text(item.get("label") or item.get("name") or rule_id, 280),
            "inferenceTraceId": trace_id,
            "reviewLabel": _text(item.get("reviewLabel") or item.get("reviewLevel"), 100),
            "dataStateLabel": _text(item.get("dataStateLabel") or item.get("dataState"), 100),
            "evidenceRole": _text(item.get("evidenceRole") or item.get("polarity") or "context", 80),
            "evidence": _unique(item.get("evidence") or [], 4, 220),
            "selected": bool(rule_id and rule_id in selected),
        })
    return rows


def _condition_rows(trace: Dict[str, object]) -> List[Dict[str, str]]:
    rows = []
    for key in ("matchedConditions", "conditionMatches", "ruleConditionShapes"):
        value = trace.get(key)
        values = list(value.values()) if isinstance(value, dict) else _rows(value, 20)
        for item in values:
            if isinstance(item, dict):
                label = _text(
                    item.get("label")
                    or item.get("conditionLabel")
                    or item.get("field")
                    or item.get("relationType")
                    or item.get("kind")
                    or item.get("conditionId")
                    or item.get("id"),
                    180,
                )
                value_text = _text(
                    item.get("observedValue")
                    or item.get("summary")
                    or item.get("matchedValue")
                    or item.get("value")
                    or item.get("description")
                    or item.get("expectedValue"),
                    220,
                )
            else:
                label, value_text = _text(item, 220), ""
            if not label:
                continue
            row = {"label": label, "value": value_text}
            if row not in rows:
                rows.append(row)
            if len(rows) >= 10:
                return rows
    return rows


def _trace_rows(relation: Dict[str, object], rules: List[Dict[str, object]]) -> List[Dict[str, object]]:
    graph = _dict(relation.get("graphStoreInference"))
    raw = _rows(graph.get("traces"), 60)
    trace_by_id = {
        _text(item.get("id") or item.get("inferenceTraceId") or item.get("traceId"), 220): item
        for item in raw
        if isinstance(item, dict)
    }
    rows = []
    for rule in rules:
        trace = _dict(trace_by_id.get(str(rule.get("inferenceTraceId") or "")))
        if not trace:
            trace = next(
                (
                    item for item in raw
                    if isinstance(item, dict)
                    and _text(item.get("ruleId") or item.get("sourceRuleId"), 180) == str(rule.get("ruleId") or "")
                ),
                {},
            )
        trace = _dict(trace)
        rows.append({
            "ruleId": str(rule.get("ruleId") or ""),
            "label": str(rule.get("label") or ""),
            "traceId": _text(trace.get("id") or trace.get("inferenceTraceId") or rule.get("inferenceTraceId"), 220),
            "selected": bool(rule.get("selected")),
            "conditions": _condition_rows(trace),
        })
    return rows


def _hypothesis_rows(relation: Dict[str, object], ai: Dict[str, object]) -> List[Dict[str, object]]:
    brain = _dict(relation.get("investmentBrain"))
    hypothesis_set = _dict(brain.get("hypothesisSet")) or _dict(relation.get("hypothesisSet"))
    candidates = [item for item in _rows(hypothesis_set.get("hypotheses"), 12) if isinstance(item, dict)]
    reviews = [item for item in _rows(ai.get("hypotheses"), 12) if isinstance(item, dict)]
    review_by_id = {
        _text(item.get("hypothesisId") or item.get("hypothesis_id"), 220): item
        for item in reviews
        if _text(item.get("hypothesisId") or item.get("hypothesis_id"), 220)
    }
    rows = []
    for candidate in candidates:
        hypothesis_id = _text(candidate.get("hypothesisId") or candidate.get("hypothesis_id"), 220)
        review = _dict(review_by_id.get(hypothesis_id))
        evidence_state = _text(candidate.get("evidenceState") or review.get("evidenceState") or "unresolved", 80)
        verdict = _text(review.get("verdict") or "unreviewed", 80)
        rows.append({
            "hypothesisId": hypothesis_id,
            "label": _text(candidate.get("templateLabel") or review.get("templateLabel") or "가설", 220),
            "claim": _text(candidate.get("claim") or review.get("claim"), 420),
            "stance": _text(candidate.get("stance") or review.get("stance") or "uncertain", 80),
            "stanceLabel": STANCE_LABELS.get(_text(candidate.get("stance") or review.get("stance"), 80), "판단 보류 경로"),
            "evidenceState": evidence_state,
            "evidenceStateLabel": _text(candidate.get("evidenceStateLabel") or review.get("evidenceStateLabel") or EVIDENCE_STATE_LABELS.get(evidence_state, evidence_state), 120),
            "verdict": verdict,
            "verdictLabel": VERDICT_LABELS.get(verdict, verdict),
            "reasoning": _text(review.get("reasoning"), 360),
            "supportingRuleIds": _unique(candidate.get("supportingRuleIds") or review.get("supportingRuleIds") or [], 8, 180),
            "supportingEvidenceIds": _unique(candidate.get("supportingEvidenceIds") or review.get("supportingEvidenceIds") or [], 8, 180),
            "counterEvidenceIds": _unique(candidate.get("counterEvidenceIds") or review.get("counterEvidenceIds") or [], 8, 180),
            "causalPathIds": _unique(candidate.get("causalPathIds") or review.get("causalPathIds") or [], 8, 180),
            "assumptions": _unique(candidate.get("assumptions") or [], 4, 260),
            "invalidationConditions": _unique(candidate.get("invalidationConditions") or [], 4, 260),
            "horizon": _text(candidate.get("horizon"), 80),
            "scopeState": _text(candidate.get("scopeState"), 80),
            "verificationStatus": _text(candidate.get("verificationStatus"), 120),
        })
    return rows


def _traceability_rows(
    relation: Dict[str, object],
    ai: Dict[str, object],
    selected_hypothesis: Dict[str, object],
) -> List[Dict[str, str]]:
    generation = _text(relation.get("inferenceGenerationId"), 180)
    graph_used = bool(relation.get("graphStoreUsed"))
    native = bool(relation.get("nativeTypeDbReasoningUsed"))
    comparison_state = _text(ai.get("hypothesisComparisonState"), 80)
    rows = [
        {
            "label": "알림 시점 컨텍스트",
            "state": "verified",
            "detail": "현재 그래프가 아니라 이 알림 작업에 저장된 사실과 추론 결과를 사용합니다.",
        },
        {
            "label": "TypeDB InferenceBox",
            "state": "verified" if graph_used and native else "partial",
            "detail": "그래프 저장소=" + (_text(relation.get("graphStore"), 80) or "미기록") + ", 네이티브 추론=" + ("예" if native else "아니오"),
        },
        {
            "label": "추론 세대",
            "state": "verified" if generation else "partial",
            "detail": generation or "이전 알림에는 세대 ID가 저장되지 않았습니다.",
        },
        {
            "label": "경쟁 가설 비교",
            "state": "verified" if comparison_state == "completed" else "partial",
            "detail": COMPARISON_STATE_LABELS.get(comparison_state, comparison_state or "가설 비교 기록 없음"),
        },
        {
            "label": "선택 가설 연결",
            "state": "verified" if selected_hypothesis else "partial",
            "detail": _text(selected_hypothesis.get("hypothesisId"), 160) or "선택 가설 ID가 저장되지 않았습니다.",
        },
    ]
    return rows


def build_notification_reverse_reasoning_trace(
    context: Dict[str, object],
    job_id: str = "",
    job_status: str = "",
) -> Dict[str, object]:
    """Return a bounded, user-safe explanation of one saved alert decision."""

    values = _dict(context)
    relation = relation_context_value(values)
    if not relation:
        return {
            "version": TRACE_VERSION,
            "status": "unavailable",
            "reason": "이전 알림에 생성 시점의 온톨로지 추론 컨텍스트가 저장되지 않았습니다.",
            "jobId": str(job_id or ""),
            "jobStatus": str(job_status or ""),
            "snapshotBound": False,
            "steps": [],
            "inputFacts": [],
            "sources": [],
            "missingData": [],
            "traceability": [],
        }

    facts = _dict(relation.get("facts"))
    decision = _dict(relation.get("decision"))
    plan = _dict(relation.get("executionPlan"))
    subject = _dict(relation.get("subject"))
    ai = _dict(values.get("notificationAiValidatedResponse"))
    hypotheses = _hypothesis_rows(relation, ai)
    selected_hypothesis_id = _text(
        ai.get("selectedHypothesisId")
        or ai.get("selected_hypothesis_id")
        or relation.get("selectedHypothesisId"),
        220,
    )
    selected_hypothesis = next(
        (item for item in hypotheses if item.get("hypothesisId") == selected_hypothesis_id),
        {},
    )
    selected_rule_ids = list(selected_hypothesis.get("supportingRuleIds") or [])
    if decision.get("selectedRuleId"):
        selected_rule_ids.append(decision.get("selectedRuleId"))
    rules = _rule_rows(relation, selected_rule_ids)
    traces = _trace_rows(relation, rules)
    graph = _dict(relation.get("graphStoreInference"))
    comparison_state = _text(ai.get("hypothesisComparisonState"), 80)
    selected_action = _text(ai.get("action"), 80)
    selected_action_label = _text(ai.get("actionLabel"), 100) or ACTION_LABELS.get(selected_action, selected_action)
    precomputed_action = _text(ai.get("precomputedAction") or plan.get("precomputedAction"), 80)
    primary_action = _text(plan.get("primaryActionLabel") or decision.get("label"), 220)
    delivery_reasons = _unique(values.get("deliveryReasons") or [], 5, 240)
    delivery_reason = _text(values.get("deliveryGateReason"), 240)
    if delivery_reason and delivery_reason not in delivery_reasons:
        delivery_reasons.insert(0, delivery_reason)
    status = "ready" if relation.get("graphStoreUsed") and relation.get("nativeTypeDbReasoningUsed") else "partial"
    if not ai or not hypotheses:
        status = "partial"
    generation_at = _text(
        relation.get("inferenceGenerationAt") or values.get("referenceDate") or values.get("eventGeneratedAt"),
        100,
    )
    scope = {
        key: _text(relation.get(key), 180)
        for key in ("worldId", "portfolioWorldId", "marketWorldId", "tenantId")
        if _text(relation.get(key), 180)
    }
    alternatives = [item for item in hypotheses if item.get("hypothesisId") != selected_hypothesis_id][:4]
    traceability = _traceability_rows(relation, ai, selected_hypothesis)
    steps = [
        {
            "id": "delivery",
            "title": "알림 발송",
            "summary": _text(values.get("deliveryDecision") or job_status or "발송 판단 기록 없음", 180),
            "detail": delivery_reasons[0] if delivery_reasons else "발송 정책의 세부 사유가 저장되지 않았습니다.",
        },
        {
            "id": "ai-decision",
            "title": "AI 최종 판단",
            "summary": (selected_action_label + (" · " + _text(ai.get("summary"), 260) if ai.get("summary") else "")) or "AI 판단 기록 없음",
            "detail": _text(ai.get("disagreementReason"), 260) or "그래프 후보와 같은 방향인지 비교했습니다.",
        },
        {
            "id": "hypothesis",
            "title": "선택 가설",
            "summary": _text(selected_hypothesis.get("claim"), 300) or "선택 가설 기록 없음",
            "detail": _text(selected_hypothesis.get("reasoning"), 300) or _text(selected_hypothesis.get("evidenceStateLabel"), 160),
        },
        {
            "id": "typedb-rule",
            "title": "TypeDB 관계 규칙",
            "summary": _text(next((item.get("label") for item in rules if item.get("selected")), ""), 240) or _text(decision.get("label"), 240) or "성립 규칙 기록 없음",
            "detail": _text(relation.get("inferenceGenerationId"), 180) or "추론 세대 ID 미기록",
        },
        {
            "id": "abox-facts",
            "title": "ABox 사실·원천 데이터",
            "summary": str(len(_fact_rows(facts))) + "개 핵심 사실, " + str(len(_source_rows(values))) + "개 원문 출처",
            "detail": "수집 시점의 값과 출처만 사용했습니다.",
        },
    ]
    return {
        "version": TRACE_VERSION,
        "status": status,
        "reason": "알림 생성 시점에 저장된 온톨로지 컨텍스트에서 역추적했습니다.",
        "jobId": str(job_id or ""),
        "jobStatus": str(job_status or ""),
        "snapshotBound": True,
        "subject": {
            "symbol": _text(subject.get("symbol") or values.get("rawSymbol") or values.get("symbol"), 80),
            "name": _text(subject.get("name") or values.get("displayTarget") or values.get("target"), 160),
            "market": _text(subject.get("market"), 40),
        },
        "snapshot": {
            "generatedAt": generation_at,
            "inferenceGenerationId": _text(relation.get("inferenceGenerationId"), 180),
            "inferenceGenerationAt": _text(relation.get("inferenceGenerationAt"), 100),
            "ruleSetHash": _text(relation.get("ruleboxShortHash") or relation.get("ruleboxRulesHash"), 180),
            "graphStore": _text(relation.get("graphStore"), 80),
            "engineVersion": _text(relation.get("engineVersion"), 120),
            "scope": scope,
            "entityCount": graph.get("entityCount"),
            "relationCount": graph.get("relationCount"),
            "traceCount": graph.get("traceCount"),
        },
        "finalDecision": {
            "action": selected_action,
            "actionLabel": selected_action_label,
            "summary": _text(ai.get("summary") or ai.get("opinion"), 420),
            "primaryAction": primary_action,
            "validationState": _text(ai.get("validationState"), 80),
            "validationLabel": _text(ai.get("validationLabel"), 120),
            "dataState": _text(ai.get("dataState"), 80),
            "dataStateLabel": _text(ai.get("dataStateLabel"), 120),
            "reviewLevel": _text(ai.get("reviewLevel"), 80),
            "reviewLabel": _text(ai.get("reviewLabel"), 120),
            "invalidationCondition": _text(ai.get("invalidationCondition"), 320),
        },
        "aiComparison": {
            "precomputedAction": precomputed_action,
            "precomputedActionLabel": ACTION_LABELS.get(precomputed_action, precomputed_action),
            "selectedAction": selected_action,
            "selectedActionLabel": selected_action_label,
            "changed": bool(precomputed_action and selected_action and precomputed_action != selected_action),
            "disagreementReason": _text(ai.get("disagreementReason"), 360),
            "comparisonState": comparison_state,
            "comparisonStateLabel": COMPARISON_STATE_LABELS.get(comparison_state, comparison_state or "가설 비교 기록 없음"),
            "selectionSource": _text(ai.get("hypothesisSelectionSource"), 120),
            "selectedHypothesisId": selected_hypothesis_id,
            "unresolvedQuestions": _unique(ai.get("unresolvedQuestions") or [], 6, 260),
        },
        "selectedHypothesis": selected_hypothesis,
        "alternativeHypotheses": alternatives,
        "matchedRules": rules,
        "inferenceTraces": traces,
        "inputFacts": _fact_rows(facts),
        "sources": _source_rows(values),
        "missingData": _missing_data_rows(relation, facts),
        "delivery": {
            "decision": _text(values.get("deliveryDecision"), 80),
            "gateState": _text(values.get("deliveryGateState"), 80),
            "gateReason": delivery_reason,
            "reasons": delivery_reasons,
            "cooldownReason": _text(values.get("cooldownReason") or values.get("repeatBypassReason"), 240),
            "freshnessStatus": _text(values.get("dataFreshnessStatus"), 80),
            "freshnessReason": _text(values.get("dataFreshnessReason"), 220),
        },
        "traceability": traceability,
        "steps": steps,
    }
