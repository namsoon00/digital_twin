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
ACTION_TEXT_REPLACEMENTS = {
    "BUY": "매수",
    "ADD": "추가매수",
    "HOLD": "보유",
    "TRIM": "분할축소",
    "SELL": "매도",
    "AVOID": "회피",
}
INTERNAL_VARIABLE_REPLACEMENTS = [
    (
        re.compile(r"entryAllocationRoom.*entrySupportCount.*entryExternalRiskBlocked.*?(?:다|\.|$)"),
        "추가매수 여력과 일부 지지 신호는 있지만, 공시·뉴스 같은 외부 위험 때문에 추가매수 근거로 보기는 어렵다.",
    ),
    (re.compile(r"\bentryAllocationRoom\b\s*(?:=|:|이|가)?\s*true", re.IGNORECASE), "추가매수 여력 있음"),
    (re.compile(r"\bentryAllocationRoom\b\s*(?:=|:|이|가)?\s*false", re.IGNORECASE), "추가매수 여력 부족"),
    (re.compile(r"\bentrySupportCount\b\s*(?:=|:|이|가)?\s*(\d+)", re.IGNORECASE), r"추가매수 지지 신호 \1개"),
    (re.compile(r"\bentryExternalRiskBlocked\b\s*(?:=|:|이|가)?\s*true", re.IGNORECASE), "공시·뉴스 같은 외부 위험으로 추가매수 보류"),
    (re.compile(r"\bentryExternalRiskBlocked\b\s*(?:=|:|이|가)?\s*false", re.IGNORECASE), "외부 위험 차단 조건 없음"),
    (re.compile(r"\bentryAllocationRoom\b", re.IGNORECASE), "추가매수 여력"),
    (re.compile(r"\bentrySupportCount\b", re.IGNORECASE), "추가매수 지지 신호 수"),
    (re.compile(r"\bentryExternalRiskBlocked\b", re.IGNORECASE), "외부 위험 차단 조건"),
    (re.compile(r"\bmissingData\b", re.IGNORECASE), "부족 데이터"),
    (re.compile(r"\brawLines\b", re.IGNORECASE), "알림 원문 데이터"),
    (re.compile(r"\bsourceFacts\b", re.IGNORECASE), "판단에 사용한 데이터"),
    (re.compile(r"\bontologyRelationContext\b", re.IGNORECASE), "관계 분석 데이터"),
    (re.compile(r"\bactiveInvestmentOpinion\b", re.IGNORECASE), "현재 투자 의견"),
    (re.compile(r"\bexecutionPlan\b", re.IGNORECASE), "실행 점검 계획"),
    (re.compile(r"\bcounterEvidence\b", re.IGNORECASE), "반대 근거"),
    (re.compile(r"\bnextChecks\b", re.IGNORECASE), "다음 확인"),
    (re.compile(r"\breferenceDate\b", re.IGNORECASE), "기준시각"),
    (re.compile(r"\bprimaryActionLabel\b", re.IGNORECASE), "우선 행동"),
    (re.compile(r"\bprimaryAction\b", re.IGNORECASE), "우선 행동"),
    (re.compile(r"\briskSignals\b", re.IGNORECASE), "위험 신호"),
    (re.compile(r"\bsupportSignals\b", re.IGNORECASE), "지지 신호"),
    (re.compile(r"\bweakenConditions\b", re.IGNORECASE), "의견이 약해지는 조건"),
]
INTERNAL_VARIABLE_TEXT_REPLACEMENTS = [
    ("entryAllocationRoom", "추가매수 여력"),
    ("entrySupportCount", "추가매수 지지 신호 수"),
    ("entryExternalRiskBlocked", "외부 위험 차단 조건"),
    ("missingData", "부족 데이터"),
    ("rawLines", "알림 원문 데이터"),
    ("sourceFacts", "판단에 사용한 데이터"),
    ("ontologyRelationContext", "관계 분석 데이터"),
    ("activeInvestmentOpinion", "현재 투자 의견"),
    ("executionPlan", "실행 점검 계획"),
    ("counterEvidence", "반대 근거"),
    ("nextChecks", "다음 확인"),
    ("referenceDate", "기준시각"),
    ("primaryActionLabel", "우선 행동"),
    ("primaryAction", "우선 행동"),
    ("riskSignals", "위험 신호"),
    ("supportSignals", "지지 신호"),
    ("weakenConditions", "의견이 약해지는 조건"),
]
USER_FRIENDLY_REPLACEMENTS = [
    ("손실 보유 + 기준선 이탈 -> 손실 관리", "손실이 커지고 주요 평균선 아래에 있어 손실 관리"),
    ("추세 훼손 + 하락 가속 -> 리스크 강화", "주요 평균선 아래에서 하락 속도가 빨라져 위험 증가"),
    ("보유 종목 + 추세 훼손 -> 추가매수 보류", "보유 종목의 가격 흐름이 약해져 추가매수 보류"),
    ("단기선 이탈 + 60일선 지지 -> 지지선 재확인", "20일선 아래지만 60일선 근처라 지지 여부 재확인"),
    ("수익 보유 + 추세 약화 -> 익절 점검", "수익 중이지만 가격 흐름이 약해져 분할매도 점검"),
    ("업종 집중 + 보유 비중 과대 -> 리밸런싱 점검", "한 업종이나 종목 비중이 커서 비중 조정 점검"),
    ("비트코인 급변 + 민감 종목 -> 연동 점검", "비트코인 변동에 민감한 종목이라 함께 점검"),
    ("기준선 이탈이 해소", "주요 평균선 아래 상태가 해소"),
    ("하락 가속이 멈추", "하락 속도가 더 빨라지는 흐름이 멈추"),
    ("기준선 이탈", "주요 평균선 아래로 내려감"),
    ("추세 훼손", "가격 흐름 약화"),
    ("하락 가속", "하락 속도 증가"),
    ("리스크 강화", "위험 증가"),
    ("리스크", "위험"),
    ("괴리", "차이"),
    ("feature 기여도", "판단에 영향을 준 항목"),
    ("feature", "판단 항목"),
    ("thesis", "보유 이유"),
    ("무효화 조건", "의견이 약해지는 조건"),
]
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


def user_friendly_ai_text(value: object, limit: int = 220) -> str:
    result = _text(value, limit)
    if not result:
        return ""
    for pattern, replacement in INTERNAL_VARIABLE_REPLACEMENTS:
        result = pattern.sub(replacement, result)
    for before, after in INTERNAL_VARIABLE_TEXT_REPLACEMENTS:
        result = result.replace(before, after)
    for before, after in USER_FRIENDLY_REPLACEMENTS:
        result = result.replace(before, after)
    for action, label in ACTION_TEXT_REPLACEMENTS.items():
        result = re.sub(r"\b" + action + r"\s*의견", label + " 의견", result)
        result = re.sub(r"\b" + action + r"\s*을\s*선택", label + " 의견을 선택", result)
        result = re.sub(r"\b" + action + r"\s*를\s*선택", label + " 의견을 선택", result)
        result = re.sub(r"\b" + action + r"\b", label, result)
    result = result.replace("->", "→")
    result = re.sub(r"\btrue\b", "예", result, flags=re.IGNORECASE)
    result = re.sub(r"\bfalse\b", "아니오", result, flags=re.IGNORECASE)
    result = result.replace("주요 평균선 아래로 내려감이", "주요 평균선 아래 상태가")
    result = result.replace("하락 속도 증가이", "하락 속도 증가가")
    result = result.replace("조건를", "조건을")
    result = result.replace("..", ".")
    result = re.sub(r"\s+", " ", result).strip()
    return result


def user_friendly_ai_list(value: object, limit: int = 5) -> List[str]:
    return _list([user_friendly_ai_text(item, 180) for item in _list(value, limit * 2)], limit)


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
        summary=user_friendly_ai_text(_line_after_colon(lines, "해석") or _line_after_colon(raw_lines, "핵심 결론") or "관계 분석 실행 계획이 생성됐습니다."),
        opinion=user_friendly_ai_text(str(execution_plan.get("primaryActionLabel") or "").strip() or _line_after_colon(lines, "의견") or _line_after_colon(raw_lines, "권장 액션") or "다음 데이터에서도 같은 신호가 유지되는지 확인하세요."),
        evidence=user_friendly_ai_list(evidence, 5),
        counter_evidence=user_friendly_ai_list(counter, 4),
        invalidation_condition=user_friendly_ai_text(invalidation, 220),
        next_checks=user_friendly_ai_list([next_check], 3),
        missing_data_impact=user_friendly_ai_list(missing_impact, 4),
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
        "action 필드에만 BUY/ADD/HOLD/TRIM/SELL/AVOID 코드를 쓰고, summary/opinion/evidence/counterEvidence/nextChecks에는 매수/추가매수/보유/분할축소/매도/회피처럼 한국어 행동명만 쓴다.",
        "사용자에게 보이는 문장에는 snake_case, camelCase, true/false, entryAllocationRoom, entrySupportCount, entryExternalRiskBlocked 같은 내부 변수명을 쓰지 않는다. 반드시 쉬운 한국어 문장으로 풀어쓴다.",
        "어려운 표현은 피한다. '기준선 이탈'은 '주요 평균선 아래로 내려감', '추세 훼손'은 '가격 흐름 약화', '하락 가속'은 '하락 속도 증가', '괴리'는 '차이'처럼 바꿔 쓴다.",
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
    summary = user_friendly_ai_text(payload.get("summary") or fallback.summary)
    opinion = soften_order_language(user_friendly_ai_text(payload.get("opinion") or fallback.opinion))
    evidence = user_friendly_ai_list(payload.get("evidence") or fallback.evidence, 5)
    counter = user_friendly_ai_list(payload.get("counterEvidence") or payload.get("counter_evidence") or fallback.counter_evidence, 4)
    invalidation = soften_order_language(user_friendly_ai_text(payload.get("invalidationCondition") or payload.get("invalidation_condition") or fallback.invalidation_condition))
    next_checks = user_friendly_ai_list(payload.get("nextChecks") or payload.get("next_checks") or fallback.next_checks, 4)
    missing_impact = user_friendly_ai_list(payload.get("missingDataImpact") or payload.get("missing_data_impact") or fallback.missing_data_impact, 5)
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
            missing_impact.append(user_friendly_ai_text(item + "는 결론 강도를 낮추는 요소입니다."))
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
    if label == "투자자":
        return _investor_text_from_lines(_raw_lines(context))
    return _line_after_colon(_raw_lines(context), label)


def _split_legacy_investor_rows(text: str) -> List[str]:
    rows = []
    for part in re.split(r",\s*(?=(?:기관|개인)(?:\s|:))", str(text or "")):
        cleaned = part.strip()
        if cleaned:
            rows.append(cleaned)
    return rows


def _investor_text_from_lines(lines: List[str]) -> str:
    for index, line in enumerate(lines):
        if not str(line or "").startswith("투자자"):
            continue
        first = _line_after_colon([line], "투자자")
        rows = _split_legacy_investor_rows(first)
        for next_line in lines[index + 1 :]:
            stripped = str(next_line or "").strip()
            if stripped.startswith(("외국인:", "기관:", "개인:")):
                rows.append(stripped)
                continue
            break
        return "\n".join(rows)
    return ""


def _html_multiline_rows(title: str, value: object) -> List[str]:
    rows = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    if not rows:
        return []
    result = ["<b>" + html.escape(title, quote=False) + "</b>"]
    result.extend("• " + html.escape(row, quote=False) for row in rows)
    return result


def _criteria_summary(context: Dict[str, object]) -> str:
    criteria = criterion_lines(context)
    if not criteria:
        return ""
    return " / ".join(_text(item, 120) for item in criteria[:2])


def execution_telegram_message(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    headline = str(context.get("headline") or context.get("title") or "알림").strip()
    target = str(context.get("displayTarget") or context.get("target") or "").strip()
    current = _plain_value(context, "현재가")
    average = _plain_value(context, "평균매입가") or _plain_value(context, "평단가")
    pnl = _plain_value(context, "수익률") or _plain_value(context, "손익")
    quantity = _plain_value(context, "보유 수량")
    sellable = _plain_value(context, "매도가능 수량")
    position_value = _plain_value(context, "종목 평가금액") or _plain_value(context, "평가금액")
    account_value = _plain_value(context, "계좌 평가금액")
    legacy_balance = _plain_value(context, "보유") if not any([quantity, sellable, position_value]) else ""
    trend = _plain_value(context, "추세")
    flow = _plain_value(context, "수급")
    investor = _plain_value(context, "투자자")
    sent = str(context.get("sentTime") or "").strip()
    reference = response.reference_date or reference_date(context)
    current_state_rows = [
        _html_row("현재가", current),
        _html_row("평균매입가", average),
        _html_row("수익률", pnl),
        _html_row("보유 수량", quantity),
        _html_row("매도가능 수량", sellable),
        _html_row("종목 평가금액", position_value),
        _html_row("계좌 평가금액", account_value),
        _html_row("보유", legacy_balance),
        _html_row("추세", trend),
        _html_row("수급", flow),
        *_html_multiline_rows("투자자", investor),
    ]
    current_state_rows = [row for row in current_state_rows if str(row or "").strip()]
    parts = [
        "<b>" + html.escape(headline, quote=False) + "</b>",
        ("<code>" + html.escape(target, quote=False) + "</code>") if target else "",
        "",
        "<b>판단</b>",
        _html_row("우선 행동", response.action_label),
        _html_row("확신", str(round(response.confidence, 1)) + "%"),
        _html_row("기준시각", reference),
        _html_row("발송시각", sent),
    ]
    if current_state_rows:
        parts.extend(["", "<b>현재 상태</b>", *current_state_rows])
    parts.extend(["", "<b>핵심 근거</b>"])
    parts.extend("• " + html.escape(item, quote=False) for item in response.evidence[:4])
    if response.counter_evidence:
        parts.extend(["", "<b>반대 신호</b>"])
        parts.extend("• " + html.escape(item, quote=False) for item in response.counter_evidence[:4])
    parts.extend(["", "<b>다음 행동 기준</b>"])
    if response.opinion:
        parts.append("• " + html.escape(response.opinion, quote=False))
    if response.invalidation_condition:
        parts.append("• 의견이 약해지는 조건: " + html.escape(response.invalidation_condition, quote=False))
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
        lines.append("의견이 약해지는 조건: " + response.invalidation_condition)
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
