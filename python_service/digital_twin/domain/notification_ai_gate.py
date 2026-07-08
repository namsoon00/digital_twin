import html
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from .message_types import (
    EXTERNAL_CRYPTO_MOVE,
    EXTERNAL_DART_DISCLOSURE,
    EXTERNAL_EQUITY_MOVE,
    EXTERNAL_MACRO_SHIFT,
    HOLDING_TIMING,
    INVESTMENT_INSIGHT,
    MODEL_BUY,
    MODEL_SELL,
    MONITOR_DECISION_CHANGE,
    MONITOR_PNL_CHANGE,
    MONITOR_TREND_CHANGE,
    MONITOR_VALUE_CHANGE,
    WATCHLIST_BUY_CANDIDATE,
)
from .notification_ai import (
    active_investment_opinion_value,
    build_notification_ai_opinion,
    criterion_lines,
    line_value,
    missing_data_labels,
    notification_ai_prompt_context,
    relation_context_value,
)


NOTIFICATION_AI_GATE_VERSION = "notification-ai-gate-v1"
VALID_ACTIONS = {"BUY", "ADD", "HOLD", "TRIM", "SELL", "AVOID"}
ACTION_LABELS = {
    "BUY": "매수",
    "ADD": "추가매수",
    "HOLD": "보유",
    "TRIM": "분할축소",
    "SELL": "매도",
    "AVOID": "회피",
}
DEFAULT_AI_GATE_MESSAGE_TYPES = {
    INVESTMENT_INSIGHT,
    HOLDING_TIMING,
    MONITOR_DECISION_CHANGE,
    MONITOR_TREND_CHANGE,
    MONITOR_PNL_CHANGE,
    MONITOR_VALUE_CHANGE,
    MODEL_BUY,
    MODEL_SELL,
    WATCHLIST_BUY_CANDIDATE,
    EXTERNAL_EQUITY_MOVE,
    EXTERNAL_CRYPTO_MOVE,
    EXTERNAL_MACRO_SHIFT,
    EXTERNAL_DART_DISCLOSURE,
}


@dataclass
class NotificationAIValidatedResponse:
    action: str = "HOLD"
    action_label: str = "보유"
    confidence: float = 50.0
    summary: str = ""
    opinion: str = ""
    evidence: List[str] = field(default_factory=list)
    counter_evidence: List[str] = field(default_factory=list)
    invalidation_condition: str = ""
    next_checks: List[str] = field(default_factory=list)
    missing_data_impact: List[str] = field(default_factory=list)
    reference_date: str = ""
    validation_warnings: List[str] = field(default_factory=list)
    source: str = "local"
    raw_response: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["engineVersion"] = NOTIFICATION_AI_GATE_VERSION
        payload["actionLabel"] = payload.pop("action_label")
        payload["counterEvidence"] = payload.pop("counter_evidence")
        payload["invalidationCondition"] = payload.pop("invalidation_condition")
        payload["nextChecks"] = payload.pop("next_checks")
        payload["missingDataImpact"] = payload.pop("missing_data_impact")
        payload["referenceDate"] = payload.pop("reference_date")
        payload["validationWarnings"] = payload.pop("validation_warnings")
        payload["rawResponse"] = payload.pop("raw_response")
        return payload


def ai_gate_message_type_set(raw: object = "") -> set:
    text = str(raw or "").strip()
    if not text:
        return set(DEFAULT_AI_GATE_MESSAGE_TYPES)
    return {part.strip() for part in text.replace("\n", ",").split(",") if part.strip()}


def ai_gate_enabled_for_message_type(message_type: str, settings: Optional[Dict[str, object]] = None) -> bool:
    settings = settings or {}
    enabled = str(settings.get("notificationAiGateEnabled") or "1").strip() != "0"
    if not enabled:
        return False
    return str(message_type or "").strip() in ai_gate_message_type_set(settings.get("notificationAiGateMessageTypes"))


def _raw_lines(context: Dict[str, object]) -> List[str]:
    raw = context.get("rawLines") if isinstance(context, dict) else ""
    if isinstance(raw, list):
        return [str(item or "").strip() for item in raw if str(item or "").strip()]
    return [line.strip() for line in str(raw or "").splitlines() if line.strip()]


def _text(value: object, limit: int = 220) -> str:
    cleaned = " ".join(str(value or "").split())
    if limit > 3 and len(cleaned) > limit:
        return cleaned[: limit - 3].rstrip() + "..."
    return cleaned


def _list(value: object, limit: int = 5) -> List[str]:
    if isinstance(value, list):
        result = [_text(item, 180) for item in value if _text(item, 180)]
    elif value:
        result = [_text(value, 180)]
    else:
        result = []
    seen = set()
    unique: List[str] = []
    for item in result:
        if item not in seen:
            seen.add(item)
            unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def _number(value: object, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value or 0)))


def _strip_code_fence(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    return cleaned


def parse_ai_response_json(text: str) -> Dict[str, object]:
    cleaned = _strip_code_fence(text)
    if not cleaned:
        return {}
    try:
        payload = json.loads(cleaned)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(cleaned[start : end + 1])
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _line_after_colon(lines: List[str], label: str) -> str:
    return line_value(lines, label)


def reference_date(context: Dict[str, object]) -> str:
    lines = _raw_lines(context)
    return (
        str(context.get("referenceDate") or "").strip()
        or _line_after_colon(lines, "기준일")
        or str(context.get("sentTime") or "").strip()
        or str(context.get("eventGeneratedAt") or "").strip()
    )


def fallback_action_from_label(value: object) -> str:
    text = str(value or "").upper()
    if "ADD" in text or "추가매수" in text:
        return "ADD"
    if "BUY" in text or ("매수" in text and "보류" not in text):
        return "BUY"
    if "TRIM" in text or "분할" in text or "축소" in text:
        return "TRIM"
    if "SELL" in text or "매도" in text or "손절" in text:
        return "SELL"
    if "AVOID" in text or "회피" in text or "보류" in text:
        return "AVOID"
    return "HOLD"


def local_validated_ai_response(context: Dict[str, object], source: str = "local") -> NotificationAIValidatedResponse:
    context = dict(context or {})
    relation_context = relation_context_value(context)
    execution_plan = relation_context.get("executionPlan") if isinstance(relation_context.get("executionPlan"), dict) else {}
    opinion = active_investment_opinion_value(context)
    if not execution_plan and isinstance(opinion, dict):
        execution_plan = opinion.get("executionPlan") if isinstance(opinion.get("executionPlan"), dict) else {}
    lines = build_notification_ai_opinion(context).get("lines") or []
    raw_lines = _raw_lines(context)
    action = str(opinion.get("action") or "").strip().upper() if isinstance(opinion, dict) else ""
    if action not in VALID_ACTIONS:
        action = fallback_action_from_label(
            (opinion or {}).get("actionLabel") if isinstance(opinion, dict) else ""
            or _line_after_colon(lines, "판단")
            or _line_after_colon(raw_lines, "권장 액션")
        )
    confidence = _clamp((opinion or {}).get("conviction") if isinstance(opinion, dict) else 0, 0, 100)
    if not confidence:
        confidence = 60.0
    evidence = []
    for item in execution_plan.get("riskSignals") or []:
        evidence.append(_text(item))
    for item in execution_plan.get("supportSignals") or []:
        evidence.append(_text(item))
    for label in ["투자 의견 근거", "근거", "가격 위치", "뉴스·공시", "공시 의미", "공시 영향"]:
        value = _line_after_colon(lines, label)
        if value:
            evidence.append(value)
    counter = []
    for item in execution_plan.get("counterSignals") or []:
        counter.append(_text(item))
    if isinstance(opinion, dict):
        for item in opinion.get("counterEvidence") or []:
            if isinstance(item, dict):
                counter.append(_text(item.get("title") or item.get("summary") or item.get("source")))
            else:
                counter.append(_text(item))
    counter_value = _line_after_colon(lines, "반대 근거")
    if counter_value:
        counter.append(counter_value)
    next_check = (opinion or {}).get("nextCheck") if isinstance(opinion, dict) else ""
    if not next_check:
        next_checks = execution_plan.get("nextChecks") if isinstance(execution_plan.get("nextChecks"), list) else []
        next_check = " / ".join(str(item) for item in next_checks[:2]) or _line_after_colon(lines, "다음 확인") or _line_after_colon(raw_lines, "다음 확인")
    invalidation = (opinion or {}).get("invalidationCondition") if isinstance(opinion, dict) else ""
    if not invalidation:
        weaken = execution_plan.get("weakenConditions") if isinstance(execution_plan.get("weakenConditions"), list) else []
        invalidation = " / ".join(str(item) for item in weaken[:2])
    if not invalidation:
        opinion_line = _line_after_colon(lines, "의견")
        marker = "무효화 조건:"
        if marker in opinion_line:
            invalidation = opinion_line.split(marker, 1)[1].strip()
    missing = missing_data_labels(context)
    missing_impact = list(execution_plan.get("missingDataImpact") or []) if isinstance(execution_plan.get("missingDataImpact"), list) else []
    if not missing_impact:
        missing_impact = [item + "는 결론 강도를 낮추는 요소입니다." for item in missing[:4]]
    return NotificationAIValidatedResponse(
        action=action,
        action_label=ACTION_LABELS.get(action, action),
        confidence=confidence,
        summary=_line_after_colon(lines, "해석") or _line_after_colon(raw_lines, "핵심 결론") or "온톨로지 실행 계획이 생성됐습니다.",
        opinion=str(execution_plan.get("primaryActionLabel") or "").strip() or _line_after_colon(lines, "의견") or _line_after_colon(raw_lines, "권장 액션") or "다음 데이터에서도 같은 신호가 유지되는지 확인하세요.",
        evidence=_list(evidence, 5),
        counter_evidence=_list(counter, 4),
        invalidation_condition=_text(invalidation, 220),
        next_checks=_list([next_check], 3),
        missing_data_impact=_list(missing_impact, 4),
        reference_date=reference_date(context),
        source=source,
    )


def build_notification_ai_gate_prompt(context: Dict[str, object]) -> str:
    context = dict(context or {})
    message_type = str(context.get("messageType") or context.get("rule") or "notification")
    prompt_context = notification_ai_prompt_context(message_type, context)
    payload = {
        "messageType": message_type,
        "target": context.get("displayTarget") or context.get("target") or context.get("title") or "",
        "referenceDate": reference_date(context),
        "rawLines": _raw_lines(context),
        "criteria": criterion_lines(context),
        "ontologyRelationContext": relation_context_value(context),
        "executionPlan": relation_context_value(context).get("executionPlan") if isinstance(relation_context_value(context), dict) else {},
        "activeInvestmentOpinion": active_investment_opinion_value(context),
        "promptContext": prompt_context,
    }
    return "\n".join([
        "너는 자동 주문자가 아니라 투자 실행 알림을 검증하는 분석가다.",
        "제공된 데이터와 온톨로지 관계 규칙만 사용한다. 없는 데이터는 절대 추정하지 않는다.",
        "BUY, ADD, HOLD, TRIM, SELL, AVOID 중 하나를 고르되 자동 주문 지시처럼 쓰지 않는다.",
        "반대 근거, 부족 데이터 영향, 무효화 조건, 다음 확인 조건을 반드시 포함한다.",
        "응답은 설명 문장 없이 JSON 객체 하나만 출력한다.",
        "스키마:",
        json.dumps({
            "action": "BUY|ADD|HOLD|TRIM|SELL|AVOID",
            "confidence": "number 0-100",
            "summary": "string",
            "opinion": "string",
            "evidence": ["string"],
            "counterEvidence": ["string"],
            "invalidationCondition": "string",
            "nextChecks": ["string"],
            "missingDataImpact": ["string"],
            "referenceDate": "string",
        }, ensure_ascii=False),
        "입력:",
        json.dumps(payload, ensure_ascii=False, default=str),
    ])


def validated_response_from_payload(
    context: Dict[str, object],
    payload: Dict[str, object],
    raw_response: str = "",
    source: str = "ai",
) -> NotificationAIValidatedResponse:
    fallback = local_validated_ai_response(context, source="local fallback")
    warnings: List[str] = []
    if not isinstance(payload, dict) or not payload:
        fallback.validation_warnings.append("AI 응답 JSON을 파싱하지 못해 로컬 검증 의견을 사용했습니다.")
        fallback.raw_response = raw_response
        return fallback

    action = str(payload.get("action") or "").strip().upper()
    if action not in VALID_ACTIONS:
        warnings.append("지원하지 않는 action 값이라 로컬 판단으로 대체했습니다.")
        action = fallback.action
    confidence = _clamp(_number(payload.get("confidence"), fallback.confidence), 0, 100)
    summary = _text(payload.get("summary") or fallback.summary)
    opinion = soften_order_language(_text(payload.get("opinion") or fallback.opinion))
    evidence = _list(payload.get("evidence") or fallback.evidence, 5)
    counter = _list(payload.get("counterEvidence") or payload.get("counter_evidence") or fallback.counter_evidence, 4)
    invalidation = soften_order_language(_text(payload.get("invalidationCondition") or payload.get("invalidation_condition") or fallback.invalidation_condition))
    next_checks = _list(payload.get("nextChecks") or payload.get("next_checks") or fallback.next_checks, 4)
    missing_impact = _list(payload.get("missingDataImpact") or payload.get("missing_data_impact") or fallback.missing_data_impact, 5)
    expected_reference = reference_date(context)
    response_reference = _text(payload.get("referenceDate") or payload.get("reference_date") or expected_reference, 80)
    if expected_reference and response_reference and expected_reference not in response_reference and response_reference not in expected_reference:
        warnings.append("AI 기준일이 알림 기준일과 달라 알림 기준일로 보정했습니다.")
        response_reference = expected_reference
    missing_labels = missing_data_labels(context)
    if not missing_labels:
        missing_impact = [
            item
            for item in missing_impact
            if not any(token in item for token in ["missingData", "빈 배열", "빈 객체", "명시적 부족 데이터", "없음"])
        ]
    for item in missing_labels:
        if not any(item in row for row in missing_impact):
            missing_impact.append(item + "는 결론 강도를 낮추는 요소입니다.")
    if not counter:
        warnings.append("반대 근거가 비어 있어 반대 신호는 웹 상세에서 추가 확인이 필요합니다.")
    return NotificationAIValidatedResponse(
        action=action,
        action_label=ACTION_LABELS.get(action, action),
        confidence=confidence,
        summary=summary,
        opinion=opinion,
        evidence=evidence,
        counter_evidence=counter,
        invalidation_condition=invalidation,
        next_checks=next_checks,
        missing_data_impact=missing_impact[:5],
        reference_date=response_reference,
        validation_warnings=warnings,
        source=source,
        raw_response=raw_response,
    )


def soften_order_language(text: str) -> str:
    replacements = {
        "무조건 매수": "매수 조건 검토",
        "무조건 추가매수": "추가매수 조건 검토",
        "무조건 매도": "매도 조건 검토",
        "반드시 매수": "매수 조건 검토",
        "반드시 매도": "매도 조건 검토",
        "즉시 매수": "매수 전 최종 확인",
        "즉시 매도": "매도 전 최종 확인",
    }
    result = str(text or "")
    for before, after in replacements.items():
        result = result.replace(before, after)
    return result


def validated_response_from_text(context: Dict[str, object], text: str, source: str = "ai") -> NotificationAIValidatedResponse:
    return validated_response_from_payload(context, parse_ai_response_json(text), raw_response=str(text or ""), source=source)


def _html_row(label: str, value: object) -> str:
    text = _text(value, 500)
    if not text:
        return ""
    return "• <b>" + html.escape(label, quote=False) + "</b>: <code>" + html.escape(text, quote=False) + "</code>"


def _plain_value(context: Dict[str, object], label: str) -> str:
    return _line_after_colon(_raw_lines(context), label)


def _criteria_summary(context: Dict[str, object]) -> str:
    criteria = criterion_lines(context)
    if not criteria:
        return ""
    return " / ".join(_text(item, 120) for item in criteria[:2])


def execution_telegram_message(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    headline = str(context.get("headline") or context.get("title") or "알림").strip()
    target = str(context.get("displayTarget") or context.get("target") or "").strip()
    current = _plain_value(context, "현재가")
    average = _plain_value(context, "평단가")
    pnl = _plain_value(context, "수익률") or _plain_value(context, "손익")
    trend = _plain_value(context, "추세")
    flow = _plain_value(context, "수급")
    investor = _plain_value(context, "투자자")
    sent = str(context.get("sentTime") or "").strip()
    reference = response.reference_date or reference_date(context)
    parts = [
        "<b>" + html.escape(headline, quote=False) + "</b>",
        ("<code>" + html.escape(target, quote=False) + "</code>") if target else "",
        "",
        "<b>판단</b>",
        _html_row("우선 행동", response.action_label),
        _html_row("확신", str(round(response.confidence, 1)) + "%"),
        _html_row("기준시각", reference),
        _html_row("발송시각", sent),
        "",
        "<b>현재 상태</b>",
        _html_row("현재가", current),
        _html_row("평단가", average),
        _html_row("수익률", pnl),
        _html_row("추세", trend),
        _html_row("수급", flow),
        _html_row("투자자", investor),
        "",
        "<b>핵심 근거</b>",
    ]
    parts.extend("• " + html.escape(item, quote=False) for item in response.evidence[:4])
    if response.counter_evidence:
        parts.extend(["", "<b>반대 신호</b>"])
        parts.extend("• " + html.escape(item, quote=False) for item in response.counter_evidence[:4])
    parts.extend(["", "<b>다음 행동 기준</b>"])
    if response.opinion:
        parts.append("• " + html.escape(response.opinion, quote=False))
    if response.invalidation_condition:
        parts.append("• 무효화 조건: " + html.escape(response.invalidation_condition, quote=False))
    for item in response.next_checks[:3]:
        parts.append("• 다음 확인: " + html.escape(item, quote=False))
    if response.missing_data_impact:
        parts.extend(["", "<b>부족 데이터</b>"])
        parts.extend("• " + html.escape(item, quote=False) for item in response.missing_data_impact[:4])
    if response.validation_warnings:
        parts.extend(["", "<b>검증 메모</b>"])
        parts.extend("• " + html.escape(item, quote=False) for item in response.validation_warnings[:3])
    criteria = _criteria_summary(context)
    if criteria:
        parts.extend(["", "<b>발송 기준</b>", "• " + html.escape(criteria, quote=False)])
    parts.append("• 분석출처: AI 검증 알림 / " + html.escape(response.source, quote=False))
    return "\n".join(part for part in parts if str(part).strip() or part == "").strip()


def _ontology_id(kind: str, value: object) -> str:
    normalized = re.sub(r"[^A-Za-z0-9가-힣_.:-]+", "-", str(value or "").strip())
    return kind + ":" + (normalized or "notification")


def notification_ai_validation_assertions(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
    payload: Dict[str, object],
) -> Dict[str, object]:
    message_type = str(context.get("messageType") or context.get("rule") or "notification")
    target = str(context.get("displayTarget") or context.get("target") or context.get("title") or message_type)
    reference = response.reference_date or reference_date(context)
    assertion_key = message_type + ":" + target + ":" + reference
    validation_id = _ontology_id("ai-validation", assertion_key)
    opinion_id = _ontology_id("validated-opinion", assertion_key + ":" + response.action)
    dispatch_id = _ontology_id("notification-dispatch", assertion_key)
    relation_context = relation_context_value(context)
    active_opinion = active_investment_opinion_value(context)
    execution_plan = relation_context.get("executionPlan") if isinstance(relation_context.get("executionPlan"), dict) else {}
    if not execution_plan and isinstance(active_opinion.get("executionPlan"), dict):
        execution_plan = active_opinion.get("executionPlan")
    entities = [
        {
            "id": validation_id,
            "ontologyBox": "ABox",
            "tboxClass": "AIValidation",
            "engineVersion": NOTIFICATION_AI_GATE_VERSION,
            "messageType": message_type,
            "target": target,
            "referenceDate": reference,
            "validationWarnings": list(response.validation_warnings or []),
        },
        {
            "id": opinion_id,
            "ontologyBox": "ABox",
            "tboxClass": "ValidatedOpinion",
            "action": response.action,
            "actionLabel": response.action_label,
            "confidence": round(_number(response.confidence), 1),
            "validatedOpinion": dict(payload or {}),
        },
        {
            "id": dispatch_id,
            "ontologyBox": "ABox",
            "tboxClass": "NotificationDispatch",
            "messageType": message_type,
            "producesValidatedMessage": True,
        },
    ]
    relations = [
        {"source": validation_id, "target": opinion_id, "relationType": "VALIDATES_OPINION"},
        {"source": validation_id, "target": dispatch_id, "relationType": "PRODUCES_VALIDATED_MESSAGE"},
    ]
    if active_opinion:
        active_id = _ontology_id("active-opinion", target)
        entities.append({
            "id": active_id,
            "ontologyBox": "ABox",
            "tboxClass": "ActiveInvestmentOpinion",
            "action": active_opinion.get("action"),
            "source": "notification-context",
        })
        relations.append({"source": validation_id, "target": active_id, "relationType": "VALIDATES_OPINION"})
    if execution_plan:
        plan_id = _ontology_id("execution-plan", target)
        entities.append({
            "id": plan_id,
            "ontologyBox": "ABox",
            "tboxClass": "ExecutionPlan",
            "primaryAction": execution_plan.get("primaryAction"),
            "primaryActionLabel": execution_plan.get("primaryActionLabel"),
            "executionPlan": dict(execution_plan),
        })
        relations.append({"source": active_id if active_opinion else opinion_id, "target": plan_id, "relationType": "HAS_EXECUTION_PLAN"})
        relations.append({"source": validation_id, "target": plan_id, "relationType": "VALIDATES_DATA"})
    return {
        "box": "ABox",
        "engineVersion": NOTIFICATION_AI_GATE_VERSION,
        "entities": entities,
        "relations": relations,
    }


def context_with_validated_ai_response(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
) -> Dict[str, object]:
    enriched = dict(context or {})
    payload = response.to_dict()
    assertions = notification_ai_validation_assertions(enriched, response, payload)
    enriched["notificationAiValidatedResponse"] = payload
    enriched["notificationAiGate"] = {
        "enabled": True,
        "engineVersion": NOTIFICATION_AI_GATE_VERSION,
        "source": response.source,
        "validationWarnings": list(response.validation_warnings or []),
    }
    enriched["ontologyAiValidation"] = {
        "ontologyBox": "ABox",
        "tboxClass": "AIValidation",
        "engineVersion": NOTIFICATION_AI_GATE_VERSION,
        "validates": ["activeInvestmentOpinion", "executionPlan", "missingData"],
        "validatedOpinion": payload,
        "validationWarnings": list(response.validation_warnings or []),
        "producesValidatedMessage": True,
        "assertionIds": [item.get("id") for item in assertions.get("entities", [])],
    }
    enriched["ontologyAssertions"] = assertions
    lines = [
        "판단: " + response.action_label + " · 확신 " + str(round(response.confidence, 1)) + "%",
        "해석: " + response.summary,
    ]
    if response.evidence:
        lines.append("근거: " + " / ".join(response.evidence[:3]))
    if response.counter_evidence:
        lines.append("반대 근거: " + " / ".join(response.counter_evidence[:3]))
    if response.invalidation_condition:
        lines.append("무효화 조건: " + response.invalidation_condition)
    if response.next_checks:
        lines.append("다음 확인: " + " / ".join(response.next_checks[:3]))
    if response.missing_data_impact:
        lines.append("부족 데이터: " + " / ".join(response.missing_data_impact[:3]))
    lines.append("분석출처: AI 검증 알림 / " + response.source)
    enriched["notificationAiOpinion"] = {
        "engineVersion": NOTIFICATION_AI_GATE_VERSION,
        "source": "AI 검증 알림",
        "messageType": enriched.get("messageType") or enriched.get("rule") or "",
        "lines": lines,
        "validatedResponse": payload,
    }
    enriched["telegramMessage"] = execution_telegram_message(enriched, response)
    enriched["readableMessage"] = re.sub(r"</?(?:b|code)>", "", enriched["telegramMessage"])
    return enriched
