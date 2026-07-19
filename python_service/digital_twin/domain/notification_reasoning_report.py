import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List

from .notification_ai_context import relation_context_value
from .notification_ai_gate_sources import all_source_urls_for_context, source_detail_map


FACT_FIELDS = [
    ("isHolding", "보유 종목"),
    ("isWatchlist", "관심 종목"),
    ("currentPrice", "현재가"),
    ("averagePrice", "평균매입가"),
    ("profitLossRate", "손익률(%)"),
    ("previousProfitLossRate", "이전 손익률(%)"),
    ("profitLossRateDeltaPct", "손익률 변화(%p)"),
    ("positionWeight", "종목 비중(%)"),
    ("sectorWeight", "업종 비중(%)"),
    ("quantity", "보유 수량"),
    ("sellableQuantity", "매도 가능 수량"),
    ("ma5", "5일 평균"),
    ("ma5Distance", "5일 평균 괴리(%)"),
    ("ma20", "20일 평균"),
    ("ma20Distance", "20일 평균 괴리(%)"),
    ("ma60", "60일 평균"),
    ("ma60Distance", "60일 평균 괴리(%)"),
    ("priceChangeRate", "가격 변화율(%)"),
    ("volume", "거래량"),
    ("rawVolumeRatio", "원본 거래량 배율"),
    ("timeAdjustedVolumeRatio", "시간 보정 거래량 배율"),
    ("tradeStrength", "체결강도"),
    ("bidAskImbalance", "매수·매도 호가 차이(%)"),
    ("investorFlowScore", "투자자 수급 점수"),
    ("foreignNetVolume", "외국인 순매수"),
    ("institutionNetVolume", "기관 순매수"),
    ("individualNetVolume", "개인 순매수"),
    ("valuationMethod", "밸류에이션 모델"),
    ("valuationFormula", "적정가 공식"),
    ("valuationSubstitution", "밸류에이션 대입값"),
    ("valuationCurrentPrice", "밸류에이션 현재가"),
    ("valuationFairValue", "적정가"),
    ("valuationMarginOfSafetyPct", "안전마진(%)"),
    ("valuationMinimumMarginOfSafetyPct", "요구 안전마진(%)"),
    ("valuationSourceLabel", "밸류에이션 출처"),
    ("valuationReliabilityLabel", "밸류에이션 신뢰도"),
    ("valuationReliabilityScore", "밸류에이션 신뢰도 점수"),
    ("valuationApprovalStatus", "밸류에이션 승인 상태"),
    ("valuationDataStatus", "밸류에이션 데이터 상태"),
    ("usdKrw", "원·달러 환율"),
    ("us10yYield", "미국 10년 금리(%)"),
    ("us2yYield", "미국 2년 금리(%)"),
    ("fedFundsRate", "미국 기준금리(%)"),
    ("btcChange24h", "비트코인 24시간 변화(%)"),
    ("btcChange7d", "비트코인 7일 변화(%)"),
    ("directNewsCount", "직접 관련 뉴스 수"),
    ("dataQuality", "데이터 품질"),
    ("dataQualityScore", "데이터 품질 점수"),
]


@dataclass
class NotificationReasoningReport:
    report_version: str
    customer_notification_number: str
    customer_job_id: str
    account_id: str
    account_label: str
    message_type: str
    target: str
    symbol: str
    generated_at: str
    input_facts: List[Dict[str, object]] = field(default_factory=list)
    raw_observations: List[str] = field(default_factory=list)
    source_items: List[Dict[str, object]] = field(default_factory=list)
    graph_summary: Dict[str, object] = field(default_factory=dict)
    active_rules: List[Dict[str, object]] = field(default_factory=list)
    reference_rules: List[Dict[str, object]] = field(default_factory=list)
    inferred_facts: List[str] = field(default_factory=list)
    decision: Dict[str, object] = field(default_factory=dict)
    score_audit: Dict[str, object] = field(default_factory=dict)
    validation_checks: List[Dict[str, str]] = field(default_factory=list)
    ai_audit: Dict[str, object] = field(default_factory=dict)
    delivery_audit: Dict[str, object] = field(default_factory=dict)
    missing_data: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "reportVersion": payload.pop("report_version"),
            "customerNotificationNumber": payload.pop("customer_notification_number"),
            "customerJobId": payload.pop("customer_job_id"),
            "accountId": payload.pop("account_id"),
            "accountLabel": payload.pop("account_label"),
            "messageType": payload.pop("message_type"),
            "target": payload.pop("target"),
            "symbol": payload.pop("symbol"),
            "generatedAt": payload.pop("generated_at"),
            "inputFacts": payload.pop("input_facts"),
            "rawObservations": payload.pop("raw_observations"),
            "sourceItems": payload.pop("source_items"),
            "graphSummary": payload.pop("graph_summary"),
            "activeRules": payload.pop("active_rules"),
            "referenceRules": payload.pop("reference_rules"),
            "inferredFacts": payload.pop("inferred_facts"),
            "decision": payload.pop("decision"),
            "scoreAudit": payload.pop("score_audit"),
            "validationChecks": payload.pop("validation_checks"),
            "aiAudit": payload.pop("ai_audit"),
            "deliveryAudit": payload.pop("delivery_audit"),
            "missingData": payload.pop("missing_data"),
        }


def _number(value: object) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _text(value: object, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _list(value: object) -> List[object]:
    return value if isinstance(value, list) else []


def _raw_lines(context: Dict[str, object]) -> List[str]:
    value = context.get("rawLines")
    if isinstance(value, list):
        return [_text(item, 500) for item in value if _text(item, 500)]
    return [_text(item, 500) for item in str(value or "").splitlines() if _text(item, 500)]


def _fact_rows(facts: Dict[str, object]) -> List[Dict[str, object]]:
    rows = []
    for key, label in FACT_FIELDS:
        value = facts.get(key)
        if value in (None, "", []):
            continue
        rows.append({"key": key, "label": label, "value": value})
    missing_inputs = facts.get("valuationMissingInputs")
    if isinstance(missing_inputs, list) and missing_inputs:
        rows.append({"key": "valuationMissingInputs", "label": "밸류에이션 부족 입력", "value": list(missing_inputs)})
    return rows


def _rule_rows(value: object) -> List[Dict[str, object]]:
    rows = []
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        score_breakdown = item.get("scoreBreakdown") if isinstance(item.get("scoreBreakdown"), dict) else {}
        rows.append({
            "ruleId": str(item.get("ruleId") or item.get("rule_id") or ""),
            "label": _text(item.get("label") or item.get("name") or item.get("ruleId") or "", 240),
            "strengthScore": round(_number(item.get("strengthScore") or item.get("strength_score") or item.get("score") or item.get("relationScore")), 1),
            "confidence": round(_number(item.get("confidence")), 1),
            "scoreBreakdown": dict(score_breakdown),
            "evidence": [_text(row, 240) for row in _list(item.get("evidence")) if _text(row, 240)],
            "inferenceTraceId": str(item.get("inferenceTraceId") or item.get("inference_trace_id") or ""),
            "referenceOnly": bool(item.get("referenceOnly") or item.get("reference_only")),
        })
    return rows


def _missing_rows(relation: Dict[str, object], facts: Dict[str, object]) -> List[str]:
    values = relation.get("missingData") or facts.get("missingData") or []
    rows = []
    for item in _list(values):
        if isinstance(item, dict):
            label = _text(item.get("label") or item.get("key") or "", 100)
            effect = _text(item.get("effect") or item.get("reason") or "", 240)
            row = label + ((": " + effect) if label and effect else "") or effect
        else:
            row = _text(item, 260)
        if row and row not in rows:
            rows.append(row)
    return rows


def _source_rows(context: Dict[str, object]) -> List[Dict[str, object]]:
    details = source_detail_map(context)
    urls = all_source_urls_for_context(context)
    rows = []
    for url in urls:
        item = details.get(url) if isinstance(details.get(url), dict) else {}
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        rows.append({
            "url": str(url),
            "title": _text(item.get("title") or item.get("summary") or "", 240),
            "source": _text(item.get("source") or item.get("domain") or item.get("provider") or "", 100),
            "publishedAt": str(item.get("publishedAt") or item.get("published_at") or ""),
            "reliability": item.get("sourceReliability", payload.get("sourceReliability")),
            "relevanceScore": item.get("relevanceScore", payload.get("relevanceScore")),
            "materialityScore": item.get("materialityScore", payload.get("materialityScore")),
            "stockImpactLabel": _text(item.get("stockImpactLabel") or payload.get("stockImpactLabel") or "", 80),
            "stockImpactReason": _text(item.get("stockImpactReasonKo") or payload.get("stockImpactReasonKo") or "", 300),
            "readStatus": _text(item.get("readStatusLabel") or payload.get("readStatusLabel") or "", 80),
        })
    return rows


def _decision_and_score_audit(relation: Dict[str, object], active_rules: List[Dict[str, object]]):
    decision = relation.get("decision") if isinstance(relation.get("decision"), dict) else {}
    plan = relation.get("executionPlan") if isinstance(relation.get("executionPlan"), dict) else {}
    selected_id = str(decision.get("selectedRuleId") or "")
    selected_rule = next((item for item in active_rules if item.get("ruleId") == selected_id), {})
    decision_score = round(_number(decision.get("score")), 1)
    selected_score = round(_number(selected_rule.get("strengthScore")), 1)
    highest_rule = max(active_rules, key=lambda item: _number(item.get("strengthScore")), default={})
    score_audit = {
        "decisionScore": decision_score,
        "selectedRuleScore": selected_score,
        "selectedRuleScoreMatches": bool(selected_rule) and abs(decision_score - selected_score) <= 0.1,
        "highestRelationScore": round(_number(highest_rule.get("strengthScore")), 1),
        "highestRelationRuleId": str(highest_rule.get("ruleId") or ""),
        "highestRelationLabel": str(highest_rule.get("label") or ""),
        "aggregateScoreBreakdown": dict(relation.get("scoreBreakdown") or {}),
        "selectedScoreBreakdown": dict(decision.get("scoreBreakdown") or selected_rule.get("scoreBreakdown") or {}),
    }
    decision_payload = {
        "label": str(decision.get("label") or plan.get("decisionLabel") or ""),
        "score": decision_score,
        "decisionStage": str(decision.get("decisionStage") or plan.get("decisionStage") or ""),
        "actionGroup": str(decision.get("actionGroup") or plan.get("actionGroup") or ""),
        "primaryAction": str(plan.get("primaryAction") or ""),
        "primaryActionLabel": str(plan.get("primaryActionLabel") or ""),
        "selectedRuleId": selected_id,
        "selectedRuleLabel": str(selected_rule.get("label") or ""),
        "selectedInferenceTraceId": str(decision.get("selectedInferenceTraceId") or selected_rule.get("inferenceTraceId") or ""),
        "targetRole": str(decision.get("targetRole") or plan.get("targetRole") or ""),
        "actionPolicy": str(decision.get("actionPolicy") or plan.get("actionPolicy") or ""),
    }
    return decision_payload, score_audit


def _validation_checks(relation: Dict[str, object], decision: Dict[str, object], score_audit: Dict[str, object], customer_message: str) -> List[Dict[str, str]]:
    active_rules = relation.get("activeRules") if isinstance(relation.get("activeRules"), list) else []
    graph_store = str(relation.get("graphStore") or "")
    checks = [
        {
            "name": "그래프 저장소 추론 사용",
            "status": "정상" if relation.get("graphStoreUsed") else "오류",
            "detail": graph_store or "graphStore 정보 없음",
        },
        {
            "name": "Python 판단 폴백 미사용",
            "status": "정상" if relation and not relation.get("fallbackUsed") else "오류",
            "detail": "fallbackUsed=" + str(bool(relation.get("fallbackUsed"))).lower(),
        },
        {
            "name": "TypeDB InferenceBox 실행",
            "status": "정상" if relation.get("nativeTypeDbReasoningUsed") else "오류",
            "detail": "nativeTypeDbReasoningUsed=" + str(bool(relation.get("nativeTypeDbReasoningUsed"))).lower(),
        },
        {
            "name": "선택 규칙 존재",
            "status": "정상" if decision.get("selectedRuleId") and active_rules else "오류",
            "detail": str(decision.get("selectedRuleId") or "선택 규칙 없음"),
        },
        {
            "name": "선택 규칙과 판단 점수 일치",
            "status": "정상" if score_audit.get("selectedRuleScoreMatches") else "오류",
            "detail": "판단 " + str(score_audit.get("decisionScore")) + "점 / 선택 규칙 " + str(score_audit.get("selectedRuleScore")) + "점",
        },
        {
            "name": "사용자 메시지 밸류에이션 보존",
            "status": "정상" if "밸류에이션" in customer_message else "확인 필요",
            "detail": "사용자 메시지의 밸류에이션 영역 확인",
        },
        {
            "name": "사용자 메시지 기사·공시 보존",
            "status": "정상" if any(term in customer_message for term in ["원문/출처", "<b>출처</b>", "뉴스", "공시"]) else "확인 필요",
            "detail": "기사·공시가 있을 때 원문과 분석 정보 표시",
        },
        {
            "name": "TBox 전체 타입 검증",
            "status": "확인 필요",
            "detail": "알림 컨텍스트에는 전체 ontology_validator 결과가 포함되지 않아 런타임 계약만 검증",
        },
    ]
    return checks


def build_notification_reasoning_report(context: Dict[str, object], customer_job_id: str, customer_message: str = "") -> NotificationReasoningReport:
    values = dict(context or {})
    relation = relation_context_value(values)
    facts = relation.get("facts") if isinstance(relation.get("facts"), dict) else {}
    active_rules = _rule_rows(relation.get("activeRules") or relation.get("matchedRules"))
    reference_rules = _rule_rows(relation.get("referenceRules"))
    decision, score_audit = _decision_and_score_audit(relation, active_rules)
    plan = relation.get("executionPlan") if isinstance(relation.get("executionPlan"), dict) else {}
    inferred_facts = []
    for driver in _list(plan.get("decisionDrivers")):
        if not isinstance(driver, dict):
            continue
        summary = _text(driver.get("summary") or driver.get("label") or "", 360)
        if summary and summary not in inferred_facts:
            inferred_facts.append(summary)
    for rule in active_rules:
        label = _text(rule.get("label"), 260)
        if label and label not in inferred_facts:
            inferred_facts.append(label)
    graph = relation.get("graphStoreInference") if isinstance(relation.get("graphStoreInference"), dict) else {}
    subgraph = relation.get("evidenceSubgraph") if isinstance(relation.get("evidenceSubgraph"), dict) else {}
    ai = values.get("notificationAiValidatedResponse") if isinstance(values.get("notificationAiValidatedResponse"), dict) else {}
    ai_audit = values.get("notificationAiAudit") if isinstance(values.get("notificationAiAudit"), dict) else {}
    quality_gate = values.get("ontologyQualityGate") if isinstance(values.get("ontologyQualityGate"), dict) else {}
    delivery = {
        "priority": values.get("honeyScore"),
        "threshold": values.get("honeyThreshold"),
        "decision": values.get("honeyDecision"),
        "reasons": list(values.get("honeyReasons") or []),
        "stateReason": values.get("honeyStateReason"),
        "similarityReason": values.get("honeySimilarityReason"),
        "similarityBypassReason": values.get("honeySimilarityBypassReason"),
        "similarityCount": values.get("honeySimilarityCount"),
        "marketHoursReason": values.get("honeyMarketHoursReason"),
        "quietHoursReason": values.get("quietHoursReason"),
        "freshnessStatus": values.get("dataFreshnessStatus"),
        "freshnessReason": values.get("dataFreshnessReason"),
        "sentAt": values.get("sentAt"),
        "sentTime": values.get("sentTime"),
        "customerDelivery": "success",
    }
    subject = relation.get("subject") if isinstance(relation.get("subject"), dict) else {}
    input_facts = _fact_rows(facts)
    for key, label in [
        ("investmentStrategyProfileLabel", "투자 성향"),
        ("messageDeliveryLevelLabel", "투자 레벨"),
    ]:
        if values.get(key) not in (None, ""):
            input_facts.append({"key": key, "label": label, "value": values.get(key)})
    report = NotificationReasoningReport(
        report_version="notification-reasoning-report-v1",
        customer_notification_number=str(values.get("notificationNumber") or ""),
        customer_job_id=str(customer_job_id or ""),
        account_id=str(values.get("accountId") or ""),
        account_label=str(values.get("accountLabel") or ""),
        message_type=str(values.get("messageType") or values.get("rule") or ""),
        target=str(values.get("displayTarget") or values.get("target") or values.get("title") or ""),
        symbol=str(values.get("rawSymbol") or values.get("symbol") or subject.get("symbol") or ""),
        generated_at=str(values.get("sentAt") or values.get("eventGeneratedAt") or values.get("referenceDate") or ""),
        input_facts=input_facts,
        raw_observations=_raw_lines(values),
        source_items=_source_rows(values),
        graph_summary={
            "engineVersion": relation.get("engineVersion"),
            "source": relation.get("source"),
            "graphStore": relation.get("graphStore"),
            "graphStoreUsed": bool(relation.get("graphStoreUsed")),
            "fallbackUsed": bool(relation.get("fallbackUsed")),
            "nativeTypeDbReasoningUsed": bool(relation.get("nativeTypeDbReasoningUsed")),
            "inferenceGenerationId": relation.get("inferenceGenerationId"),
            "inferenceGenerationAt": relation.get("inferenceGenerationAt"),
            "entityCount": graph.get("entityCount"),
            "relationCount": graph.get("relationCount"),
            "traceCount": graph.get("traceCount"),
            "evidenceNodeCount": len(_list(subgraph.get("nodes"))),
            "evidenceEdgeCount": len(_list(subgraph.get("edges"))),
            "matchedRuleIds": list(subgraph.get("matchedRuleIds") or []),
            "ruleSetHash": relation.get("ruleboxShortHash") or relation.get("ruleboxRulesHash"),
            "ruleCount": relation.get("ruleboxRuleCount"),
            "conditionCount": relation.get("ruleboxConditionCount"),
            "derivationCount": relation.get("ruleboxDerivationCount"),
        },
        active_rules=active_rules,
        reference_rules=reference_rules,
        inferred_facts=inferred_facts,
        decision=decision,
        score_audit=score_audit,
        validation_checks=_validation_checks(relation, decision, score_audit, customer_message),
        ai_audit={
            "engineVersion": ai.get("engineVersion"),
            "source": ai.get("source"),
            "action": ai.get("action"),
            "actionLabel": ai.get("actionLabel"),
            "confidence": ai.get("confidence"),
            "precomputedAction": ai.get("precomputedAction"),
            "disagreementReason": ai.get("disagreementReason"),
            "validationWarnings": list(ai.get("validationWarnings") or []),
            "audit": dict(ai_audit),
            "ontologyQualityGate": dict(quality_gate),
        },
        delivery_audit=delivery,
        missing_data=_missing_rows(relation, facts),
    )
    return report


def customer_alert_reason_lines(context: Dict[str, object]) -> List[str]:
    relation = relation_context_value(context or {})
    decision = relation.get("decision") if isinstance(relation.get("decision"), dict) else {}
    why_now = relation.get("whyNow") if isinstance(relation.get("whyNow"), dict) else {}
    rows = []
    for item in _list(why_now.get("changeDrivers")):
        text = _text(item, 260)
        if text and "핵심 룰:" not in text and not re.search(r"[A-Z][A-Z0-9_]{3,}", text) and text not in rows:
            rows.append(text)
    score = _number(decision.get("score"))
    label = _text(decision.get("label"), 100)
    if score:
        rows.append((label + " " if label else "관계 판단 ") + str(round(score, 1)) + "점으로 설정한 투자 판단 기준을 충족했습니다.")
    state_reason = _text(context.get("honeyStateReason") or context.get("honeySimilarityBypassReason"), 260)
    if state_reason and state_reason not in rows:
        rows.append("이전 알림과 비교: " + state_reason)
    return rows[:5]


def customer_inferred_fact_lines(context: Dict[str, object]) -> List[str]:
    relation = relation_context_value(context or {})
    plan = relation.get("executionPlan") if isinstance(relation.get("executionPlan"), dict) else {}
    rows = []
    for driver in _list(plan.get("decisionDrivers")):
        if not isinstance(driver, dict):
            continue
        summary = _text(driver.get("summary"), 320)
        if summary and summary not in rows:
            rows.append(summary)
    if not rows:
        for rule in _rule_rows(relation.get("activeRules")):
            label = _text(rule.get("label"), 260)
            if label and label not in rows:
                rows.append(label)
    return rows[:5]


def customer_confidence_and_missing_lines(context: Dict[str, object]) -> List[str]:
    relation = relation_context_value(context or {})
    facts = relation.get("facts") if isinstance(relation.get("facts"), dict) else {}
    score = relation.get("scoreBreakdown") if isinstance(relation.get("scoreBreakdown"), dict) else {}
    rows = []
    confidence = _number(score.get("dataConfidence") or relation.get("confidence"))
    if confidence:
        rows.append("데이터 근거 신뢰도 " + str(round(confidence, 1)) + "점입니다. 가격 예측 성공률이 아니라 사용한 데이터의 완성도입니다.")
    rows.extend(_missing_rows(relation, facts))
    return rows[:6]


def _display(value: object) -> str:
    if value in (None, "", []):
        return "-"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, list):
        return ", ".join(_text(item, 180) for item in value if _text(item, 180)) or "-"
    if isinstance(value, dict):
        return ", ".join(str(key) + "=" + _text(item, 140) for key, item in value.items() if item not in (None, "", [])) or "-"
    return _text(value, 500)


def render_operator_reasoning_report(report: NotificationReasoningReport) -> str:
    sections = [
        "🛠 운영자 추론 보고서 · " + (report.customer_notification_number or "번호 없음"),
        report.target or report.symbol or "대상 없음",
        "",
        "연결 정보",
        "• 사용자 알림 발송: 성공",
        "• 사용자 알림 작업: " + (report.customer_job_id or "-"),
        "• 계정: " + (report.account_label or report.account_id or "-"),
        "• 생성 시각: " + (report.generated_at or "-"),
        "",
        "원본 입력 사실",
    ]
    sections.extend("• " + item["label"] + " [" + item["key"] + "]: " + _display(item["value"]) for item in report.input_facts)
    if report.raw_observations:
        sections.extend(["", "원본 관측 문장"])
        sections.extend("• " + item for item in report.raw_observations)
    sections.extend(["", "그래프·InferenceBox"])
    for key, label in [
        ("engineVersion", "엔진"),
        ("graphStore", "그래프 저장소"),
        ("graphStoreUsed", "그래프 추론 사용"),
        ("fallbackUsed", "Python 판단 폴백"),
        ("nativeTypeDbReasoningUsed", "TypeDB 네이티브 추론"),
        ("inferenceGenerationId", "추론 세대"),
        ("inferenceGenerationAt", "추론 생성 시각"),
        ("entityCount", "개체 수"),
        ("relationCount", "관계 수"),
        ("traceCount", "추론 경로 수"),
        ("ruleSetHash", "규칙 묶음 해시"),
    ]:
        sections.append("• " + label + ": " + _display(report.graph_summary.get(key)))
    sections.extend(["", "성립한 관계 규칙"])
    if report.active_rules:
        for item in report.active_rules:
            sections.append("• " + (item.get("label") or item.get("ruleId") or "규칙") + " · " + str(item.get("strengthScore")) + "점 · 신뢰도 " + str(item.get("confidence")))
            sections.append("  ID: " + (item.get("ruleId") or "-") + " · 추론 경로: " + (item.get("inferenceTraceId") or "-"))
            if item.get("scoreBreakdown"):
                sections.append("  점수 구성: " + _display(item.get("scoreBreakdown")))
    else:
        sections.append("• 성립 규칙 없음")
    if report.reference_rules:
        sections.extend(["", "참고·최종 판단 제외 규칙"])
        sections.extend("• " + (item.get("label") or item.get("ruleId") or "규칙") + " · " + str(item.get("strengthScore")) + "점" for item in report.reference_rules)
    sections.extend(["", "추론으로 새로 확인한 사실"])
    sections.extend(["• " + item for item in report.inferred_facts] or ["• 추론 사실 없음"])
    sections.extend([
        "",
        "최종 판단 연결",
        "• 판단: " + _display(report.decision.get("label")),
        "• 판단 단계: " + _display(report.decision.get("decisionStage")),
        "• 대응: " + _display(report.decision.get("primaryActionLabel") or report.decision.get("primaryAction")),
        "• 선택 규칙: " + _display(report.decision.get("selectedRuleLabel") or report.decision.get("selectedRuleId")),
        "• 판단 점수: " + _display(report.score_audit.get("decisionScore")) + "점",
        "• 선택 규칙 점수: " + _display(report.score_audit.get("selectedRuleScore")) + "점",
        "• 가장 강한 관계: " + _display(report.score_audit.get("highestRelationLabel")) + " · " + _display(report.score_audit.get("highestRelationScore")) + "점",
        "• 선택 점수 구성: " + _display(report.score_audit.get("selectedScoreBreakdown")),
        "• 전체 관계 점수 구성: " + _display(report.score_audit.get("aggregateScoreBreakdown")),
        "",
        "온톨로지 검증",
    ])
    sections.extend("• [" + item.get("status", "확인 필요") + "] " + item.get("name", "검증") + " · " + item.get("detail", "") for item in report.validation_checks)
    sections.extend([
        "",
        "AI 설명 감사",
        "• 엔진: " + _display(report.ai_audit.get("engineVersion")),
        "• 출처: " + _display(report.ai_audit.get("source")),
        "• AI 표현 대응: " + _display(report.ai_audit.get("actionLabel") or report.ai_audit.get("action")),
        "• AI 설명 확신도: " + _display(report.ai_audit.get("confidence")),
        "• 온톨로지 후보 대응: " + _display(report.ai_audit.get("precomputedAction")),
        "• 판단 차이 이유: " + _display(report.ai_audit.get("disagreementReason")),
        "• 검증 경고: " + _display(report.ai_audit.get("validationWarnings")),
        "",
        "알림 발송 감사",
        "• 발송 우선도: " + _display(report.delivery_audit.get("priority")) + "/" + _display(report.delivery_audit.get("threshold")),
        "• 발송 판정: " + _display(report.delivery_audit.get("decision")),
        "• 판정 내역: " + _display(report.delivery_audit.get("reasons")),
        "• 상태 쿨다운: " + _display(report.delivery_audit.get("stateReason")),
        "• 유사 메시지: " + _display(report.delivery_audit.get("similarityReason") or report.delivery_audit.get("similarityBypassReason")),
        "• 장 시간: " + _display(report.delivery_audit.get("marketHoursReason")),
        "• 데이터 신선도: " + _display(report.delivery_audit.get("freshnessStatus")) + " · " + _display(report.delivery_audit.get("freshnessReason")),
    ])
    if report.missing_data:
        sections.extend(["", "부족 데이터"])
        sections.extend("• " + item for item in report.missing_data)
    if report.source_items:
        sections.extend(["", "기사·공시 원문 감사"])
        for index, item in enumerate(report.source_items, start=1):
            sections.append("• " + str(index) + ". " + (item.get("title") or item.get("source") or "원문"))
            sections.append("  출처=" + _display(item.get("source")) + " · 게시=" + _display(item.get("publishedAt")) + " · 신뢰도=" + _display(item.get("reliability")) + " · 관련성=" + _display(item.get("relevanceScore")) + " · 중요도=" + _display(item.get("materialityScore")))
            if item.get("stockImpactReason"):
                sections.append("  영향=" + _display(item.get("stockImpactReason")))
            sections.append("  " + str(item.get("url") or ""))
    sections.extend(["", "보안", "• API 키, Toss Secret, Telegram Token, Chat ID는 보고서에 포함하지 않았습니다."])
    return "\n".join(sections).strip()
