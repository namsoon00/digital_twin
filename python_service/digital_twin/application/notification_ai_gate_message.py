import html
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 compatibility guard.
    ZoneInfo = None

from ..domain.accounts import investment_strategy_profile, message_delivery_profile
from ..domain.alert_formatting import compact_number, price_money, signed_pct, trade_strength_label
from ..domain.notification_ai import criterion_lines, notification_ai_prompt_context, relation_context_value
from ..domain.notification_ai_context import relation_facts
from ..domain.notification_ai_context import is_watchlist_context
from ..domain.external_api_sources import external_api_source_line
from ..domain.notification_ai_gate_contracts import ACTION_LABELS, MESSAGE_START_BADGE, NotificationAIValidatedResponse
from ..domain.notification_icon_policy import investment_notification_icon, notification_title_with_context_icon
from ..domain.investment_ubiquitous_language import user_facing_investment_language
from ..domain.ontology_decision_state import (
    ACTION_ENVELOPE_STATUS_LABELS,
    CHANGE_STATE_LABELS,
    DATA_STATE_LABELS,
    REVIEW_LEVEL_LABELS,
    VALIDATION_STATE_LABELS,
    review_level_for,
)
from ..domain.notification_ai_gate_sources import source_detail_text, source_url_rows
from ..domain.notification_reasoning_report import (
    customer_alert_reason_lines,
    customer_data_state_and_missing_lines,
    customer_inferred_fact_lines,
)
from ..domain.notification_ai_gate_text import (
    _line_after_colon,
    _number,
    _raw_lines,
    _text,
    append_unique_text,
    customer_visible_ai_text,
    reference_date,
)
from ..domain.notification_ai_gate_validation import (
    _driver_rows,
    action_label_for_target,
    delivery_level_from_context,
    watchlist_friendly_text,
)
from ..domain.notification_start_badge import labeled_message_start_badge
from ..domain.notification_text_formatting import absolute_beginner_friendly_text, beginner_friendly_text
from ..domain.notification_ontology_sections import relation_axis_summary_lines
from .notification_message_metrics import _profit_loss_change_summary


MESSAGE_CONTEXT_ROW_LIMIT = 5
MESSAGE_DATA_QUALITY_ROW_LIMIT = 3
MESSAGE_DATA_COLLECTION_ROW_LIMIT = 6
MESSAGE_API_SOURCE_ROW_LIMIT = 8
KST = ZoneInfo("Asia/Seoul") if ZoneInfo else timezone(timedelta(hours=9))

DATA_COLLECTION_TIME_KEYS = [
    "sourceFetchedAt",
    "fetchedAt",
    "collectedAt",
    "updatedAt",
    "observedAt",
    "asOf",
    "publishedAt",
    "checkedAt",
]
DATA_COLLECTION_FETCHED_TIME_KEYS = [
    "sourceFetchedAt",
    "fetchedAt",
    "collectedAt",
    "updatedAt",
    "observedAt",
    "checkedAt",
]
DATA_COLLECTION_BASIS_TIME_KEYS = ["sourceAsOf", "asOf", "publishedAt"]

DATA_COLLECTION_SOURCE_KEYS = ["provider", "source", "domain", "quoteSource", "sourceName"]
DATA_COLLECTION_DETAIL_KEYS = ["symbol", "title", "seriesId", "eventType", "field", "dataScope", "messageType"]
DATA_COLLECTION_STAGE_LABELS = {
    "price": "시세",
    "ccnl": "실시간 체결",
    "orderbook": "실시간 호가",
    "investor": "투자자 수급",
}
DATA_COLLECTION_STAGE_QUERY_INFO = {
    "price": "국내 주식 현재가·등락률·거래량",
    "ccnl": "국내 주식 실시간 체결가·거래량·체결강도",
    "orderbook": "국내 주식 실시간 매수/매도 호가잔량·호가불균형",
    "investor": "국내 주식 투자자별 매수·매도·순매수",
}
DATA_COLLECTION_SCOPE_LABELS = {
    "market-microstructure": "체결·호가",
    "investor-flow": "투자자 수급",
    "market-price": "시세",
    "market-quote": "시세",
    "quote": "시세",
    "news": "뉴스",
    "disclosure": "공시",
    "macro": "거시 지표",
    "crypto": "크립토 시세",
    "fx": "환율",
}
DATA_COLLECTION_SCOPE_QUERY_INFO = {
    "market-microstructure": "체결강도·호가잔량·거래량 같은 장중 미시구조",
    "investor-flow": "외국인·기관·개인 매수/매도/순매수",
    "market-price": "현재가·등락률·거래량",
    "market-quote": "현재가·거래량·거래대금",
    "quote": "시세·거래량",
    "news": "뉴스 제목·요약·원문 URL·발행시각",
    "disclosure": "공시 보고서명·접수일·공시 원문",
    "macro": "금리·스프레드·거시 시계열",
    "crypto": "크립토 가격·거래액·24시간/7일 변동률",
    "fx": "환율",
}
DATA_COLLECTION_FIELD_LABELS = {
    "currentPrice": "현재가",
    "changeRate": "등락률",
    "volume": "거래량",
    "volumeRatio": "거래량비율",
    "tradingValue": "거래대금",
    "tradeStrength": "체결강도",
    "buyVolume": "매수체결량",
    "sellVolume": "매도체결량",
    "orderbookBidVolume": "매수호가잔량",
    "orderbookAskVolume": "매도호가잔량",
    "bidAskImbalance": "호가불균형",
    "foreignNetVolume": "외국인 순매수",
    "institutionNetVolume": "기관 순매수",
    "individualNetVolume": "개인 순매수",
    "foreignBuyVolume": "외국인 매수",
    "foreignSellVolume": "외국인 매도",
    "institutionBuyVolume": "기관 매수",
    "institutionSellVolume": "기관 매도",
    "individualBuyVolume": "개인 매수",
    "individualSellVolume": "개인 매도",
    "valuationCurrentPrice": "밸류에이션 현재가",
    "valuationFairValue": "적정가",
    "valuationExpectedEPS": "예상 EPS",
    "valuationTargetPER": "목표 PER",
    "valuationMarginOfSafetyPct": "안전마진",
}
DATA_COLLECTION_FRESHNESS_LABELS = {
    "realtime": "실시간",
    "near-live": "준실시간",
    "last-close": "최근 마감 기준",
    "reference-only": "참고용",
    "reference-repeat": "반복 참고값",
    "stale-repeat": "반복 지연",
    "delayed-or-batched": "지연/배치 가능",
    "stale": "노후",
    "unknown": "미확인",
    "unavailable": "사용 불가",
}
DATA_COLLECTION_TRANSPORT_LABELS = {
    "websocket": "WebSocket",
    "rest": "REST",
    "http": "REST",
}
DATA_COLLECTION_SOURCE_TIMESTAMP_STATE_LABELS = {
    "exchange-tick": "거래소 틱 기준",
    "provider-timestamp": "제공 기준시각",
    "business-date-only": "영업일자 기준",
    "queried-at-fallback": "조회시각 기준",
}

ABSOLUTE_BEGINNER_TERM_REPLACEMENTS = [
    ("유동성 또는 슬리피지 위험", "거래가 적어 원하는 가격에 사고팔기 어려운 위험"),
    ("실행 가능 용량", "지금 주문해도 무리가 없는지"),
    ("실행 차단", "지금 바로 주문하기 어려운 조건"),
    ("벤치마크 베타", "시장과 같이 움직이는 정도"),
    ("관계 강도", "확인 단계"),
    ("관계 점수", "확인 단계"),
    ("관계 신호", "연결된 근거 신호"),
    ("RuleBox", "관계 분석 규칙"),
    ("InferenceBox", "관계 분석 결과"),
    ("actionGroup", "판단 묶음"),
    ("actionLevel", "판단 단계"),
    ("signalStrength", "확인 단계"),
    ("confidence", "검증 상태"),
    ("팩터 노출", "영향받는 요인"),
    ("익스포저", "쏠림 정도"),
    ("슬리피지", "원하는 가격과 실제 거래 가격 차이"),
    ("리스크", "위험"),
]

BEGINNER_TERM_HINTS = [
    ("관계 강도", "현재 자료로 어느 정도 대응 준비가 필요한지 나타내는 단계"),
    ("관계 점수", "현재 자료로 어느 정도 대응 준비가 필요한지 나타내는 단계"),
    ("관계 신호", "가격·뉴스·보유 상태를 연결해서 본 신호"),
    ("벤치마크 베타", "시장과 같이 움직이는 정도"),
    ("실행 가능 용량", "지금 주문해도 무리가 없는지"),
    ("실행 차단", "지금 바로 주문하기 어려운 조건"),
    ("슬리피지", "원하는 가격과 실제 거래 가격 차이"),
    ("RuleBox", "관계 분석 규칙"),
    ("InferenceBox", "관계 분석 결과"),
]

INTERMEDIATE_TERM_HINTS = [
    ("관계 강도", "대응 확인 단계"),
    ("벤치마크 베타", "시장 민감도"),
    ("실행 가능 용량", "주문 소화 가능성"),
    ("실행 차단", "주문 실행 제약"),
    ("슬리피지", "체결 가격 차이"),
    ("RuleBox", "규칙 저장소"),
    ("InferenceBox", "추론 결과"),
]

CUSTOMER_HIDDEN_DATA_NOTE_TERMS = [
    "AI 응답",
    "raw",
    "fallback",
    "프롬프트",
    "검증",
    "관계 분석 관계가 없어",
    "그래프 기반",
    "로컬 임계값",
    "출처 URL",
    "sourceUrl",
    "source URL",
    "TypeDB",
    "RuleBox",
    "InferenceBox",
    "ontology",
]

BEGINNER_LABEL_REPLACEMENTS = {
    "추세": "가격 흐름(추세)",
    "수급": "거래량·매수매도",
    "확인할 반대 신호": "반대 신호",
    "검증 메모": "검증 결과",
}

ABSOLUTE_BEGINNER_LABEL_REPLACEMENTS = {
    "추세": "가격 흐름",
    "수급": "거래량·매수매도",
    "AI 판단 이유": "이유",
    "확인할 반대 신호": "반대 신호",
    "부족 데이터": "데이터 빈 곳",
    "검증 메모": "검증 결과",
}

def _annotate_term_once(text: str, term: str, hint: str) -> str:
    if term not in text:
        return text
    pattern = re.compile(re.escape(term) + r"(?!\s*[\(（])")
    return pattern.sub(term + "(" + hint + ")", text, count=1)


def _message_text(value: object, level: str = "") -> str:
    text = user_facing_investment_language(value)
    normalized = str(level or "").strip()
    if normalized == "absoluteBeginner":
        for before, after in ABSOLUTE_BEGINNER_TERM_REPLACEMENTS:
            text = text.replace(before, after)
        return absolute_beginner_friendly_text(text).strip()
    if normalized == "beginner":
        text = beginner_friendly_text(text)
        for term, hint in BEGINNER_TERM_HINTS:
            text = _annotate_term_once(text, term, hint)
        return text.strip()
    if normalized == "intermediate":
        for term, hint in INTERMEDIATE_TERM_HINTS:
            text = _annotate_term_once(text, term, hint)
        return text.strip()
    return text.strip()


def _message_label(label: str, level: str = "") -> str:
    normalized = str(level or "").strip()
    if normalized == "absoluteBeginner":
        return ABSOLUTE_BEGINNER_LABEL_REPLACEMENTS.get(label, label)
    if normalized == "beginner":
        return BEGINNER_LABEL_REPLACEMENTS.get(label, label)
    return label


def _friendly_text(value: object) -> str:
    return _message_text(value, "absoluteBeginner")


def _html_bullet(value: object, level: str = "", prefix: str = "") -> str:
    text = _message_text(value, level)
    if not text:
        return ""
    if prefix:
        text = prefix + text
    return "• " + html.escape(text, quote=False)


def _html_row(label: str, value: object, beginner: bool = False, level: str = "", max_len: int = 500) -> str:
    text = _text(value, max_len)
    if not text:
        return ""
    display_level = level or ("absoluteBeginner" if beginner else "")
    if display_level:
        text = _message_text(text, display_level)
    return "• <b>" + html.escape(_message_label(label, display_level), quote=False) + "</b>: <code>" + html.escape(text, quote=False) + "</code>"

def _ai_marked_value(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("[AI]"):
        return text
    return "[AI] " + text

def customer_data_note_rows(values: List[object]) -> List[str]:
    rows: List[str] = []
    for item in values or []:
        text = customer_visible_ai_text(_text(item, 0))
        if not text:
            continue
        lowered = text.lower()
        if any(term.lower() in lowered for term in CUSTOMER_HIDDEN_DATA_NOTE_TERMS):
            continue
        if compact_reason_is_internal(text):
            continue
        append_unique_text(rows, text, 0)
    return rows


def action_envelope_status_label(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("-", "_").replace(" ", "_")
    if not normalized.isupper():
        normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", normalized)
    normalized = normalized.upper()
    return ACTION_ENVELOPE_STATUS_LABELS.get(normalized, "")


def action_envelope_status_from_transition(transition: Dict[str, object]) -> str:
    transition = transition if isinstance(transition, dict) else {}
    for key in ["currentStatus", "status", "nextStatus"]:
        value = str(transition.get(key) or "").strip()
        if action_envelope_status_label(value):
            return value
    summary = str(transition.get("summary") or "")
    for status in ACTION_ENVELOPE_STATUS_LABELS:
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(status).replace("_", "[_-]?") + r"(?![A-Za-z0-9_])"
        if re.search(pattern, summary, flags=re.IGNORECASE):
            return status
    return ""


def decision_transition_from_context(context: Dict[str, object]) -> Dict[str, object]:
    context = context if isinstance(context, dict) else {}
    transition = context.get("decisionTransition") if isinstance(context.get("decisionTransition"), dict) else {}
    if transition:
        return dict(transition)
    relation_diff = context.get("ontologyRelationDiff") if isinstance(context.get("ontologyRelationDiff"), dict) else {}
    transition = relation_diff.get("decisionTransition") if isinstance(relation_diff.get("decisionTransition"), dict) else {}
    return dict(transition or {})


def _normalized_action_envelope_status(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("-", "_").replace(" ", "_")
    if not normalized.isupper():
        normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", normalized)
    return normalized.upper()


def _transition_action(context: Dict[str, object], transition: Dict[str, object], current_action: object = "") -> str:
    current = str(transition.get("currentAction") or current_action or "").strip().upper()
    if current:
        return current
    validated = context.get("notificationAiValidatedResponse") if isinstance(context.get("notificationAiValidatedResponse"), dict) else {}
    return str(validated.get("action") or "").strip().upper()


def _watchlist_status_transition_presentation(
    current_action: str,
    current_status: str,
    previous_status: str,
    context: Dict[str, object],
) -> Dict[str, str]:
    action_label = action_label_for_action(current_action or "HOLD", context)
    current_label = action_envelope_status_label(current_status)
    previous_label = action_envelope_status_label(previous_status)
    prefix = (previous_label + "에서 " + current_label + "로 바뀌었습니다. ") if previous_label and previous_label != current_label else ""
    if current_status == "ENTRY_ELIGIBLE":
        return {
            "category": "entry-eligible",
            "label": "소액 진입 조건 성립",
            "summary": prefix + "현재 행동은 " + action_label + "입니다. 한 번에 크게 사라는 뜻이 아니라, 정한 한도 안에서만 진입을 검토할 수 있다는 뜻입니다.",
        }
    if current_status == "ENTRY_DEFERRED":
        return {
            "category": "entry-check-needed",
            "label": "진입 조건 추가 확인",
            "summary": prefix + "현재 행동은 " + action_label + "입니다. 새로 사기 전에 확인할 조건이 남아 있습니다.",
        }
    if current_status == "ENTRY_OBSERVING":
        return {
            "category": "entry-observing",
            "label": "관심 유지",
            "summary": prefix + "현재 행동은 관심 유지입니다. 매수 판단으로 바뀐 것은 아닙니다.",
        }
    if current_status in {"ENTRY_BLOCKED", "JUDGEMENT_BLOCKED"}:
        return {
            "category": "entry-blocked",
            "label": "신규 진입 판단 보류",
            "summary": prefix + "필수 자료나 반대 조건 때문에 지금은 새로 사지 않습니다.",
        }
    return {}


def decision_transition_presentation(context: Dict[str, object], current_action: object = "") -> Dict[str, str]:
    """Translate a stored decision transition into an explicit customer-facing change.

    The raw transition remains the delivery contract used by cooldown and
    dispatch. This helper only explains whether a change narrows entry, starts
    entry review, strengthens a sale decision, or merely changes data status.
    """

    context = context if isinstance(context, dict) else {}
    transition = decision_transition_from_context(context)
    if not transition:
        return {}
    kind = str(transition.get("kind") or "").strip().lower()
    previous_action = str(transition.get("previousAction") or "").strip().upper()
    next_action = _transition_action(context, transition, current_action)
    previous_status = _normalized_action_envelope_status(transition.get("previousStatus"))
    current_status = _normalized_action_envelope_status(action_envelope_status_from_transition(transition))
    watchlist = is_watchlist_context(context)

    if previous_action and next_action and previous_action != next_action:
        previous_label = action_label_for_action(previous_action, context)
        next_label = action_label_for_action(next_action, context)
        change = previous_label + "에서 " + next_label + "로 바뀌었습니다."
        if watchlist:
            if previous_action in {"BUY", "ADD"} and next_action == "HOLD":
                return {
                    "category": "entry-paused",
                    "label": "신규 매수 보류",
                    "summary": change + " 매도 신호가 아니라, 진입 조건을 더 확인하는 동안 새로 사지 않는다는 뜻입니다.",
                }
            if previous_action in {"HOLD", "AVOID", "SELL", "TRIM"} and next_action in {"BUY", "ADD"}:
                return {
                    "category": "entry-review-started",
                    "label": "소액 진입 검토 시작",
                    "summary": change + " 새로 살 조건이 생겼지만, 한 번에 크게 사라는 뜻은 아닙니다.",
                }
            if next_action in {"AVOID", "SELL", "TRIM"}:
                return {
                    "category": "entry-avoidance",
                    "label": "신규 진입 회피",
                    "summary": change + " 지금은 새로 사지 않고 위험 요인이나 자료 상태가 바뀌는지 확인합니다.",
                }
            if previous_action in {"AVOID", "SELL", "TRIM"} and next_action == "HOLD":
                return {
                    "category": "entry-avoidance-eased",
                    "label": "진입 회피 완화",
                    "summary": change + " 바로 사라는 뜻은 아니며, 관심을 유지하면서 조건을 더 확인합니다.",
                }
            return {
                "category": "entry-decision-changed",
                "label": "신규 매수 판단 변경",
                "summary": change + " 현재 행동과 다음 확인 조건을 함께 봐야 합니다.",
            }
        if next_action == "SELL":
            return {
                "category": "sale-review-started",
                "label": "매도 검토 시작",
                "summary": change + " 자동 매도가 아니라, 보유 이유와 줄일 수량을 다시 확인하라는 뜻입니다.",
            }
        if next_action == "TRIM":
            return {
                "category": "trim-review-started",
                "label": "분할축소 검토 시작",
                "summary": change + " 전량 매도보다 일부를 줄일지 먼저 검토하는 단계입니다.",
            }
        if previous_action in {"SELL", "TRIM"} and next_action == "HOLD":
            return {
                "category": "sale-review-eased",
                "label": "매도 판단 완화",
                "summary": change + " 바로 더 사라는 뜻은 아니며, 현재 보유 이유를 다시 확인하는 단계입니다.",
            }
        if next_action in {"BUY", "ADD"}:
            return {
                "category": "buy-review-started",
                "label": "매수 검토 시작",
                "summary": change + " 정한 투자 한도와 다음 확인 조건 안에서만 검토해야 합니다.",
            }
        return {
            "category": "holding-decision-changed",
            "label": "보유 판단 변경",
            "summary": change + " 자동 주문이 아니라 현재 보유 판단을 다시 확인하라는 뜻입니다.",
        }

    if kind == "action-changed":
        status_presentation = _watchlist_status_transition_presentation(next_action, current_status, previous_status, context) if watchlist else {}
        if status_presentation:
            return status_presentation
        return {
            "category": "decision-changed",
            "label": "판단 변경",
            "summary": "현재 행동이 바뀌었지만 이전 행동 정보가 완전하지 않아 자세한 차이는 확인할 수 없습니다.",
        }

    if watchlist and current_status:
        status_presentation = _watchlist_status_transition_presentation(next_action, current_status, previous_status, context)
        if status_presentation:
            return status_presentation

    if kind == "readiness-changed":
        action_label = action_label_for_action(next_action, context) if next_action else "현재 판단"
        return {
            "category": "data-readiness-changed",
            "label": "판단 자료 상태 변경",
            "summary": "현재 행동은 " + action_label + "이며, 매수·매도 판단 자체가 바뀐 것은 아닙니다. 자료 상태가 바뀌어 다음 판단의 확신도만 달라졌습니다.",
        }
    if kind in {"initial", "envelope-changed"}:
        return {
            "category": "decision-condition-changed",
            "label": "새 판단 조건",
            "summary": "새 판단 조건이 확인됐습니다. 현재 행동과 다음 확인 조건을 함께 봐야 합니다.",
        }
    return {}


def notification_topline_change_summary(context: Dict[str, object]) -> str:
    context = context or {}
    reason = str(
        context.get("cooldownReason")
        or context.get("repeatBypassReason")
        or context.get("honeyStateReason")
        or ""
    ).strip()
    source_types = " ".join(str(item or "") for item in (context.get("sourceSignalTypes") or []))
    profit_loss_summary = "" if is_watchlist_context(context) else _profit_loss_change_summary(context, reason)
    if profit_loss_summary:
        return profit_loss_summary
    transition = decision_transition_from_context(context)
    if transition:
        presentation = decision_transition_presentation(context)
        if presentation.get("label"):
            return presentation["label"]
        kind = str(transition.get("kind") or "").strip().lower()
        if kind == "action-changed":
            return "판단 변경"
        if kind in {"initial", "envelope-changed"}:
            return "새 판단 조건"
        if kind == "readiness-changed":
            return "판단 자료 상태 변경"
        if action_envelope_status_label(action_envelope_status_from_transition(transition)):
            return "새 판단 조건"
    if "손익률 추가 악화" in reason:
        return "손익률 악화"
    if "필수 발송 구간" in reason or "손실률" in reason or "수익률" in reason:
        return "손익 구간"
    if "60일 평균 아래 전환" in reason or "60일선 이탈" in reason:
        return "60일선 이탈"
    if "판단 액션 변경" in reason or "판단 변경" in reason:
        return "판단 변경"
    if "새 근거 신호 추가" in reason:
        if any(term in source_types for term in ["news", "News", "Dart", "Disclosure", "researchEvidence"]):
            return "새 뉴스·공시"
        return "새 근거"
    if "새 뉴스/공시/관계 근거" in reason or "새 관계 이벤트" in reason:
        return "새 뉴스·공시"
    if "관계 강도 변화" in reason or "확인 단계 변화" in reason or "지금 얼마나 주의" in reason:
        return "새 판단 조건"
    if "신규성 변화" in reason:
        return "신규성 변화"
    if "인사이트 유형 변경" in reason:
        return "유형 변경"
    if "신규 임계값 상태" in reason:
        return "새 기준 진입"
    reason_summary = notification_reason_summary(context)
    if reason_summary:
        if "뉴스" in reason_summary or "공시" in reason_summary:
            return "새 뉴스·공시"
        if "관계 점수" in reason_summary or "확인 단계" in reason_summary:
            return "확인 단계 변경"
        return _clean_reason_text(reason_summary, 0)
    return ""

def prepend_execution_start_badge(rendered: str, context: Dict[str, object] = None) -> str:
    text = str(rendered or "").strip()
    if not text:
        return text
    summary = notification_topline_change_summary(context or {})
    plain_badge = labeled_message_start_badge(MESSAGE_START_BADGE, context or {})
    html_badge = "<b>" + html.escape(plain_badge, quote=False) + "</b>"
    plain_summary_line = summary if summary else ""
    html_summary_line = ("<code>" + html.escape(summary, quote=False) + "</code>") if summary else ""
    if text.startswith("<b>" + MESSAGE_START_BADGE):
        first, rest = (text.split("\n", 1) + [""])[:2]
        second = text.splitlines()[1] if len(text.splitlines()) > 1 else ""
        second_plain = re.sub(r"</?(?:b|code)>", "", second)
        if first.strip() != html_badge or (summary and summary not in second_plain):
            first = html_badge
            return first + "\n" + html_summary_line + (("\n" + rest) if rest else "")
        return text
    if text.startswith(MESSAGE_START_BADGE):
        first, rest = (text.split("\n", 1) + [""])[:2]
        second = text.splitlines()[1] if len(text.splitlines()) > 1 else ""
        if first.strip() != plain_badge or (summary and summary not in second):
            first = plain_badge
            return first + "\n" + plain_summary_line + (("\n" + rest) if rest else "")
        return text
    if summary:
        return html_badge + "\n" + html_summary_line + "\n\n" + text
    return html_badge + "\n\n" + text

def action_label_for_action(action: object, context: Dict[str, object] = None) -> str:
    text = str(action or "").strip().upper()
    if context is not None:
        return action_label_for_target(context, text)
    return ACTION_LABELS.get(text, str(action or "").strip())

def relation_state_values(context: Dict[str, object]) -> Dict[str, str]:
    relation_context = relation_context_value(context or {})
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    data_state = str(decision.get("dataState") or relation_context.get("dataState") or "partial")
    review_level = str(decision.get("reviewLevel") or relation_context.get("reviewLevel") or "")
    if not review_level:
        review_level = review_level_for(decision.get("actionLevel"), data_state)
    change_state = str(decision.get("changeState") or relation_context.get("changeState") or "unchanged")
    return {
        "reviewLevel": review_level,
        "reviewLabel": str(decision.get("reviewLabel") or relation_context.get("reviewLevelLabel") or REVIEW_LEVEL_LABELS.get(review_level, REVIEW_LEVEL_LABELS["observe"])),
        "dataState": data_state,
        "dataLabel": str(decision.get("dataStateLabel") or relation_context.get("dataStateLabel") or DATA_STATE_LABELS.get(data_state, DATA_STATE_LABELS["partial"])),
        "changeState": change_state,
        "changeLabel": str(decision.get("changeStateLabel") or relation_context.get("changeStateLabel") or CHANGE_STATE_LABELS.get(change_state, CHANGE_STATE_LABELS["unchanged"])),
    }

def ai_judgment_section_title(level: str) -> str:
    return "전략 요약"

def ai_action_row_label(level: str) -> str:
    return "권장 대응"

def account_strategy_label(context: Dict[str, object]) -> str:
    context = context if isinstance(context, dict) else {}
    payload = context.get("investmentStrategy") if isinstance(context.get("investmentStrategy"), dict) else {}
    label = str(
        payload.get("label")
        or context.get("investmentStrategyProfileLabel")
        or ""
    ).strip()
    if label:
        return label
    key = payload.get("profile") or context.get("investmentStrategyProfile")
    if key:
        return str(investment_strategy_profile(key).get("label") or "").strip()
    return ""

def account_delivery_level_label(context: Dict[str, object]) -> str:
    context = context if isinstance(context, dict) else {}
    payload = context.get("messageDeliveryProfile") if isinstance(context.get("messageDeliveryProfile"), dict) else {}
    label = str(
        payload.get("label")
        or context.get("messageDeliveryLevelLabel")
        or ""
    ).strip()
    if label:
        return label
    level = payload.get("level") or context.get("messageDeliveryLevel")
    if level:
        return str(message_delivery_profile(level).get("label") or "").strip()
    return ""

def account_profile_rows(context: Dict[str, object], level: str) -> List[str]:
    rows = [
        _html_row("투자 성향", account_strategy_label(context), level=level),
        _html_row("투자 레벨", account_delivery_level_label(context), level=level),
    ]
    return [row for row in rows if row]

def ai_judgment_rows(response: NotificationAIValidatedResponse, level: str, context: Dict[str, object] = None) -> List[str]:
    relation_state = relation_state_values(context or {})
    rows = [
        _html_row(ai_action_row_label(level), _ai_marked_value(action_label_for_action(response.action, context) or response.action_label), level=level),
        _html_row("확인 단계", relation_state.get("reviewLabel") or response.review_label, level=level),
        _html_row("자료 상태", relation_state.get("dataLabel") or response.data_state_label, level=level),
        _html_row("AI 검증", response.validation_label, level=level),
        _html_row("이번 변화", relation_state.get("changeLabel"), level=level),
    ]
    rows.extend(account_profile_rows(context or {}, level))
    summary_label = "이유" if level == "absoluteBeginner" else "AI 판단 이유"
    if response.summary:
        rows.append(_html_row(summary_label, _ai_marked_value(response.summary), level=level))
    return [row for row in rows if row]

def ai_difference_rows(response: NotificationAIValidatedResponse, level: str, context: Dict[str, object] = None) -> List[str]:
    if not response.precomputed_action or response.precomputed_action == response.action:
        return []
    rows = [
        _html_row("계산 후보", action_label_for_action(response.precomputed_action, context), level=level),
        _html_row("AI 최종", action_label_for_action(response.action, context) or response.action_label, level=level),
    ]
    if response.disagreement_reason:
        rows.append(_html_row("다르게 본 이유" if level == "absoluteBeginner" else "변경 이유", response.disagreement_reason, level=level))
    return [row for row in rows if row]


def hypothesis_comparison_rows(response: NotificationAIValidatedResponse, level: str) -> List[str]:
    rows: List[str] = []
    stance_labels = {
        "risk": "위험 지속 가설",
        "support": "회복·지지 가설",
        "uncertain": "판단 보류 가설",
        "context": "외부 맥락 가설",
    }
    ordered = sorted(
        [item for item in response.hypotheses or [] if isinstance(item, dict)],
        key=lambda item: str(item.get("hypothesisId") or "") != response.selected_hypothesis_id,
    )
    for item in ordered[:3]:
        label = stance_labels.get(str(item.get("stance") or ""), "경쟁 가설")
        selected = str(item.get("hypothesisId") or "") == response.selected_hypothesis_id
        if selected:
            label = "선택 가설"
        claim = _text(item.get("claim"), 260)
        verdict = str(item.get("verdict") or "").strip()
        suffix = []
        if verdict and verdict != "unreviewed":
            suffix.append(verdict)
        if suffix:
            claim += " (" + " · ".join(suffix) + ")"
        rows.append(_html_row(label, claim, level=level, max_len=360))
    if response.unresolved_questions:
        rows.append(_html_row("남은 질문", response.unresolved_questions[0], level=level, max_len=300))
    return [row for row in rows if row]


def hypothesis_decision_brief_text_rows(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
) -> List[str]:
    """Present lifecycle audit facts without exposing TypeDB internals."""

    relation_context = relation_context_value(context or {})
    brief = relation_context.get("hypothesisDecisionBrief") if isinstance(relation_context.get("hypothesisDecisionBrief"), dict) else {}
    if not brief:
        return []
    rows: List[str] = []
    explicit_update = _strategy_guide_value(response, "hypothesisUpdate")
    if explicit_update:
        append_unique_text(rows, "AI가 본 가설 변화: " + explicit_update, 300)
    changes = brief.get("materialChanges") if isinstance(brief.get("materialChanges"), list) else []
    if not explicit_update and changes:
        item = changes[0] if isinstance(changes[0], dict) else {}
        scope = str(item.get("scopeLabel") or "가설")
        state = str(item.get("stateLabel") or "상태 변화")
        reason = _text(item.get("transitionReason"), 220)
        if reason:
            append_unique_text(rows, "가설 변화: " + scope + "이 " + state + " 상태입니다. " + reason, 300)
        else:
            append_unique_text(rows, "가설 변화: " + scope + "이 " + state + " 상태입니다.", 220)
    visible_items = [item for item in brief.get("items") or [] if isinstance(item, dict)]
    assessment = next((
        item.get("outcomeAssessment")
        for item in visible_items
        if isinstance(item.get("outcomeAssessment"), dict)
        and str(item.get("outcomeAssessment", {}).get("outcomeState") or "") not in {"", "insufficient-sample"}
    ), {})
    if isinstance(assessment, dict) and assessment:
        label = str(assessment.get("outcomeStateLabel") or "사후 검토")
        summary = _text(assessment.get("summary"), 220)
        if summary:
            append_unique_text(rows, "사후 검토: " + label + ". " + summary, 300)
    next_check = _strategy_guide_value(response, "hypothesisNextCheck")
    if not next_check:
        requirements = brief.get("nextDataRequirements") if isinstance(brief.get("nextDataRequirements"), list) else []
        next_check = _compact_text_segments(requirements, 2, 170)
    if next_check:
        append_unique_text(rows, "가설 다음 확인: " + next_check, 260)
    freshness = brief.get("freshnessWarnings") if isinstance(brief.get("freshnessWarnings"), list) else []
    if freshness:
        append_unique_text(rows, "가설 판단 제한: " + _compact_text_segments(freshness, 2, 160), 240)
    quality = brief.get("qualityReview") if isinstance(brief.get("qualityReview"), dict) else {}
    required = quality.get("reviewRequired") if isinstance(quality.get("reviewRequired"), list) else []
    if required:
        item = required[0] if isinstance(required[0], dict) else {}
        label = str(item.get("qualityStateLabel") or "가설 품질 점검")
        reason = _text(item.get("reason"), 180)
        if reason:
            append_unique_text(rows, "가설 검토 제한: " + label + ". " + reason, 260)
    return rows[:4]


def hypothesis_decision_brief_rows(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
    level: str,
) -> List[str]:
    return [_html_bullet(_ai_marked_value(row) if row.startswith("AI가") else row, level) for row in hypothesis_decision_brief_text_rows(context, response)]

def target_name_for_headline(target: object) -> str:
    text = str(target or "").strip()
    if not text:
        return ""
    for separator in ["/", "|"]:
        if separator in text:
            text = text.split(separator, 1)[0].strip()
            break
    return text[:24].rstrip()

def title_prefix_from_headline(headline: str) -> str:
    text = str(headline or "").strip()
    match = re.match(r"^(\[[^\]]+\]\s+(?:\S+\s+)?)", text)
    return match.group(1).strip() if match else ""

def action_headline(response: NotificationAIValidatedResponse, context: Dict[str, object] = None) -> str:
    action = response.action
    if is_watchlist_context(context or {}):
        if action in {"BUY", "ADD"}:
            return "소액 진입 조건 점검"
        if action == "HOLD":
            return "관심 유지·진입 조건 확인"
        if action in {"TRIM", "SELL", "AVOID"}:
            return "신규 진입 회피"
    if action == "BUY":
        return "매수 조건 점검"
    if action == "ADD":
        return "추가매수 조건 점검"
    if action == "HOLD":
        return "보유 유지·다음 조건 확인"
    if action == "TRIM":
        return "분할축소 우선 점검"
    if action == "SELL":
        return "매도 우선 점검"
    if action == "AVOID":
        return "신규 진입 회피"
    return response.action_label or "대응 기준 점검"

def execution_headline(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    presentation_context = dict(context or {})
    presentation_context["notificationAiValidatedResponse"] = response.to_dict()
    headline = notification_title_with_context_icon(
        presentation_context.get("messageType") or presentation_context.get("rule") or "",
        presentation_context.get("headline") or presentation_context.get("title") or "알림",
        presentation_context,
    )
    prefix = title_prefix_from_headline(headline)
    if not prefix:
        prefix = investment_notification_icon(
            presentation_context.get("messageType") or presentation_context.get("rule") or "",
            presentation_context,
        )
    target = target_name_for_headline(context.get("displayTarget") or context.get("target") or context.get("title") or "")
    action = action_headline(response, context)
    if target:
        return " ".join(part for part in [prefix, target + ": " + action] if part)
    return " ".join(part for part in [prefix, action] if part) or headline

def _plain_value(context: Dict[str, object], label: str) -> str:
    if label == "투자자":
        return _investor_text_from_lines(_raw_lines(context))
    return _line_after_colon(_raw_lines(context), label)

def execution_footer(context: Dict[str, object], response: NotificationAIValidatedResponse, reference: str, sent: str) -> List[str]:
    rows = [
        _html_row("데이터 기준일", reference),
        _html_row("발송시각", sent),
        _html_row("번호", context.get("notificationNumber")),
    ]
    rows = [row for row in rows if row]
    return (["", "<b>기준·추적</b>", *rows] if rows else [])

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


def _investor_text_from_relation_facts(context: Dict[str, object]) -> str:
    facts = relation_facts(context)
    coverage = facts.get("marketSignalCoverage") if isinstance(facts.get("marketSignalCoverage"), dict) else {}
    investor = coverage.get("investor") if isinstance(coverage.get("investor"), dict) else {}
    if str(investor.get("status") or "").strip() != "available" or investor.get("judgementEvidenceUsable") is False:
        return ""

    rows = []
    for label, prefix in [("외국인", "foreign"), ("기관", "institution"), ("개인", "individual")]:
        buy = _number(facts.get(prefix + "BuyVolume"))
        sell = _number(facts.get(prefix + "SellVolume"))
        reported_net = _number(facts.get(prefix + "NetVolume"))
        net = buy - sell if buy or sell else reported_net
        if not any([buy, sell, net]):
            continue
        direction = "순매수" if net > 0 else "순매도" if net < 0 else "매수·매도 균형"
        detail = label + ": " + direction
        if net:
            detail += " " + compact_number(abs(net)) + "주"
        if buy or sell:
            detail += " (매수 " + compact_number(buy) + "주 / 매도 " + compact_number(sell) + "주)"
        rows.append(detail)
    if not rows:
        return ""

    note = "KIS 당일 누적 수급"
    unchanged_count = _number(investor.get("unchangedCount"))
    if unchanged_count:
        note += " · 이전 조회와 같은 값 " + compact_number(unchanged_count) + "회"
    note += " · 보유·매매 판단에 반영 · 장중 신규 변화 확인 전 참고값"
    return note + "\n" + "\n".join(rows)


def _investor_text(context: Dict[str, object]) -> str:
    return _investor_text_from_lines(_raw_lines(context)) or _investor_text_from_relation_facts(context)


def _html_multiline_rows(title: str, value: object) -> List[str]:
    rows = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    if not rows:
        return []
    result = ["<b>" + html.escape(title, quote=False) + "</b>"]
    result.extend("• " + html.escape(row, quote=False) for row in rows)
    return result


def data_quality_warning_rows(context: Dict[str, object], limit: int = 3) -> List[str]:
    facts = relation_facts(context or {})
    warnings = facts.get("dataQualityWarnings") if isinstance(facts.get("dataQualityWarnings"), list) else []
    rows: List[str] = []
    for item in warnings:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("key") or "").strip()
        effect = str(item.get("effect") or item.get("reason") or "").strip()
        if not label and not effect:
            continue
        text = effect if label and label in effect else (label + ": " + effect if label and effect else label or effect)
        if text and text not in rows:
            rows.append(text)
        if len(rows) >= limit:
            break
    return rows


def data_collection_time_rows(context: Dict[str, object], limit: int = MESSAGE_DATA_COLLECTION_ROW_LIMIT) -> List[str]:
    rows: List[str] = []
    seen = set()

    def add(
        label: object,
        value: object,
        suffix: object = "",
        kind: object = "",
        query_info: object = "",
        source_as_of: object = "",
        transport: object = "",
        freshness_status: object = "",
        ai_usable_as_strong_evidence: object = None,
        source_timestamp_state: object = "",
    ) -> None:
        if len(rows) >= limit:
            return
        stamp = _format_kst_timestamp(value)
        if not stamp:
            return
        basis_stamp = _format_kst_timestamp(source_as_of)
        source = _collection_source_label(label, kind)
        detail = _text(str(suffix or "").strip(), 72)
        info = _text(str(query_info or kind or "API 데이터").strip(), 110)
        parts = []
        if info:
            parts.append("조회 정보 " + info)
        transport_label = DATA_COLLECTION_TRANSPORT_LABELS.get(str(transport or "").strip().lower(), str(transport or "").strip())
        if transport_label:
            parts.append("전송 " + transport_label)
        parts.append("조회시각 " + stamp)
        if basis_stamp:
            parts.append("기준시각 " + basis_stamp)
        freshness_label = DATA_COLLECTION_FRESHNESS_LABELS.get(str(freshness_status or "").strip().lower(), str(freshness_status or "").strip())
        if freshness_label:
            parts.append("품질 " + freshness_label)
        timestamp_state_label = DATA_COLLECTION_SOURCE_TIMESTAMP_STATE_LABELS.get(str(source_timestamp_state or "").strip().lower(), str(source_timestamp_state or "").strip())
        if timestamp_state_label:
            parts.append("기준 " + timestamp_state_label)
        if ai_usable_as_strong_evidence is False:
            parts.append("AI 강근거 제외")
        if detail:
            parts.append(detail)
        text = source + ": " + " · ".join(parts)
        key = re.sub(r"\s+", " ", source + "|" + info + "|" + stamp + "|" + basis_stamp + "|" + detail).strip().lower()
        if key in seen:
            return
        seen.add(key)
        rows.append(text)

    freshness = (context or {}).get("dataFreshness") if isinstance((context or {}).get("dataFreshness"), dict) else {}
    if freshness:
        source = freshness.get("source") or "데이터 신선도"
        stamp = (
            freshness.get("sourceFetchedAt")
            or freshness.get("fetchedAt")
            or freshness.get("sourceAsOf")
            or (freshness.get("checkedAt") if not freshness.get("sources") else "")
        )
        age = _number(freshness.get("ageMinutes"))
        status = str(freshness.get("status") or "").strip()
        detail_parts = []
        if status:
            detail_parts.append("상태 " + status)
        if age or age == 0:
            detail_parts.append("약 " + _minute_count_text(age) + " 전")
        add(
            source,
            stamp,
            " · ".join(part for part in detail_parts if part),
            _collection_text_for_kind(freshness, source),
            _collection_query_info(freshness, source),
            source_as_of=freshness.get("sourceAsOf"),
            transport=freshness.get("transport"),
            freshness_status=freshness.get("freshnessStatus") or freshness.get("status"),
            ai_usable_as_strong_evidence=freshness.get("aiUsableAsStrongEvidence"),
            source_timestamp_state=freshness.get("sourceTimestampState"),
        )

    roots = [
        context,
        relation_facts(context or {}),
    ]
    relation_context = relation_context_value(context or {})
    if isinstance(relation_context, dict):
        roots.append(relation_context.get("facts") if isinstance(relation_context.get("facts"), dict) else relation_context)

    def walk(value: object, depth: int = 0) -> None:
        if len(rows) >= limit or depth > 5:
            return
        if isinstance(value, list):
            for item in value[:20]:
                walk(item, depth + 1)
                if len(rows) >= limit:
                    break
            return
        if not isinstance(value, dict):
            return
        nested_sources = value.get("sources") if isinstance(value.get("sources"), list) else []
        if nested_sources:
            for item in nested_sources[:20]:
                walk(item, depth + 1)
                if len(rows) >= limit:
                    return
            source_stamp = next(
                (value.get(key) for key in DATA_COLLECTION_TIME_KEYS if key != "checkedAt" and value.get(key)),
                "",
            )
            if not source_stamp and not any(value.get(key) for key in DATA_COLLECTION_SOURCE_KEYS):
                return
        stamp = next((value.get(key) for key in DATA_COLLECTION_FETCHED_TIME_KEYS if value.get(key)), "")
        if not stamp:
            stamp = next((value.get(key) for key in DATA_COLLECTION_TIME_KEYS if value.get(key)), "")
        if stamp:
            source = next((value.get(key) for key in DATA_COLLECTION_SOURCE_KEYS if value.get(key)), "")
            detail = next((value.get(key) for key in DATA_COLLECTION_DETAIL_KEYS if value.get(key)), "")
            source_as_of = next((value.get(key) for key in DATA_COLLECTION_BASIS_TIME_KEYS if value.get(key)), "")
            add(
                source or value.get("kind") or value.get("type") or "API 데이터",
                stamp,
                detail,
                _collection_text_for_kind(value, source, detail),
                _collection_query_info(value, source, detail),
                source_as_of=source_as_of,
                transport=value.get("transport"),
                freshness_status=value.get("freshnessStatus") or value.get("latencyStatus") or value.get("status"),
                ai_usable_as_strong_evidence=value.get("aiUsableAsStrongEvidence"),
                source_timestamp_state=value.get("sourceTimestampState"),
            )
        for child in value.values():
            if isinstance(child, (dict, list)):
                walk(child, depth + 1)
                if len(rows) >= limit:
                    break

    for root in roots:
        walk(root)
        if len(rows) >= limit:
            break
    return rows[:limit]


def _criterion_value(lines: List[str], label: str) -> str:
    prefix = str(label or "").strip()
    for line in lines:
        text = str(line or "").strip()
        if text.startswith(prefix + ":"):
            return text.split(":", 1)[1].strip()
    return ""

def _clean_reason_text(value: object, limit: int = 100) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^(설정|감지|확인 데이터):\s*", "", text)
    text = text.replace(" -> ", " → ")
    return _text(text, limit)

def _top_relation_rule_reasons(relation_context: Dict[str, object], limit: int = 2) -> List[str]:
    rules = relation_context.get("activeRules") or relation_context.get("matchedRules") or []
    rows: List[str] = []
    for item in rules:
        if not isinstance(item, dict) or item.get("referenceOnly") or item.get("reference_only"):
            continue
        label = _clean_reason_text(item.get("label") or item.get("ruleId") or item.get("rule_id"), 70)
        if label:
            rows.append(label)
        if len(rows) >= limit:
            break
    return rows

def _relation_state_reason(context: Dict[str, object]) -> str:
    relation_context = relation_context_value(context)
    if not relation_context:
        return ""
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    state = relation_state_values(context)
    label = _clean_reason_text(
        decision.get("label")
        or decision.get("actionLabel")
        or decision.get("action")
        or relation_context.get("decisionLabel"),
        64,
    )
    parts = [
        state.get("changeLabel") or CHANGE_STATE_LABELS["unchanged"],
        "현재 " + (state.get("reviewLabel") or REVIEW_LEVEL_LABELS["observe"]) + " 단계입니다",
        "자료 상태는 " + (state.get("dataLabel") or DATA_STATE_LABELS["partial"]) + "입니다",
    ]
    if label:
        parts.append(label)
    reasons = _top_relation_rule_reasons(relation_context)
    if reasons:
        parts.append("주요 요인: " + ", ".join(reasons))
    return " · ".join(parts)

def _threshold_reason(context: Dict[str, object]) -> str:
    criteria = criterion_lines(context)
    if not criteria:
        return ""
    detected = _criterion_value(criteria, "감지")
    setting = _criterion_value(criteria, "설정")
    if detected and setting:
        return "감지값 " + _clean_reason_text(detected, 0) + "이 기준(" + _clean_reason_text(setting, 0) + ")을 넘었습니다."
    if detected:
        return "감지값 " + _clean_reason_text(detected, 0) + " 때문에 알림이 발생했습니다."
    if setting:
        return _clean_reason_text(setting, 0)
    return _clean_reason_text(criteria[0], 0) if criteria else ""

def notification_reason_summary(context: Dict[str, object]) -> str:
    return (
        _relation_state_reason(context)
        or _threshold_reason(context)
    )


def _minute_count_text(value: object) -> str:
    if value in (None, ""):
        return ""
    number = _number(value)
    if float(number).is_integer():
        return str(int(number)) + "분"
    return ("%.1f" % number).rstrip("0").rstrip(".") + "분"


def _format_kst_timestamp(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "KST" in text.upper():
        return text
    normalized = text
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(normalized + "T00:00:00+00:00")
        except ValueError:
            return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")


def _collection_text_for_kind(value: Dict[str, object], source: object = "", detail: object = "") -> str:
    item = value if isinstance(value, dict) else {}
    stage = str(item.get("stage") or item.get("dataStage") or "").strip()
    if stage in DATA_COLLECTION_STAGE_LABELS:
        return DATA_COLLECTION_STAGE_LABELS[stage]
    scope = str(item.get("dataScope") or item.get("scope") or "").strip()
    if scope in DATA_COLLECTION_SCOPE_LABELS:
        return DATA_COLLECTION_SCOPE_LABELS[scope]
    text = " ".join([
        str(source or ""),
        str(detail or ""),
        str(item.get("provider") or ""),
        str(item.get("source") or ""),
        str(item.get("domain") or ""),
        str(item.get("messageType") or ""),
        str(item.get("type") or ""),
        str(item.get("kind") or ""),
        " ".join(str(key or "") for key in item.keys()),
    ]).lower()
    if "opendart" in text or "dart" in text:
        return "공시"
    if "sec edgar" in text or "edgar" in text:
        return "해외 공시"
    if any(term in text for term in ["gdelt", "google news", "news", "headline", "article", "rss", "뉴스"]):
        return "뉴스"
    if "fred" in text or "macro" in text:
        return "거시 지표"
    if "coingecko" in text or "crypto" in text or "coin" in text:
        return "크립토 시세"
    if "alpha vantage" in text:
        if "fx" in text or "exchange" in text or "currency" in text:
            return "환율"
        if "fundamental" in text or "earnings" in text:
            return "펀더멘털"
        return "해외 시세"
    if "kis" in text:
        if "websocket" in text:
            return "실시간 시세·호가"
        return "시세·수급"
    if "toss" in text or "brokeraccount" in text:
        return "계좌·보유"
    if any(term in text for term in ["currentprice", "price", "quote", "volume", "tradingvalue"]):
        return "시세"
    if any(term in text for term in ["orderbook", "bid", "ask", "imbalance"]):
        return "호가"
    if any(term in text for term in ["foreign", "institution", "individual", "investor"]):
        return "투자자 수급"
    return "API 데이터"


def _collection_field_summary(value: Dict[str, object]) -> str:
    item = value if isinstance(value, dict) else {}
    fields = item.get("fields") if isinstance(item.get("fields"), list) else []
    if not fields:
        fields = item.get("nonZeroFields") if isinstance(item.get("nonZeroFields"), list) else []
    labels: List[str] = []
    for field in fields:
        label = DATA_COLLECTION_FIELD_LABELS.get(str(field or "").strip())
        if label and label not in labels:
            labels.append(label)
        if len(labels) >= 6:
            break
    if not labels:
        return ""
    extra = len(fields) - len(labels)
    return " · 실제 필드 " + "·".join(labels) + ((" 외 " + str(extra) + "개") if extra > 0 else "")


def _collection_query_info(value: Dict[str, object], source: object = "", detail: object = "") -> str:
    item = value if isinstance(value, dict) else {}
    stage = str(item.get("stage") or item.get("dataStage") or "").strip()
    if stage in DATA_COLLECTION_STAGE_QUERY_INFO:
        return DATA_COLLECTION_STAGE_QUERY_INFO[stage] + _collection_field_summary(item)
    scope = str(item.get("dataScope") or item.get("scope") or "").strip()
    if scope in DATA_COLLECTION_SCOPE_QUERY_INFO:
        return DATA_COLLECTION_SCOPE_QUERY_INFO[scope] + _collection_field_summary(item)
    text = " ".join([
        str(source or ""),
        str(detail or ""),
        str(item.get("provider") or ""),
        str(item.get("source") or ""),
        str(item.get("domain") or ""),
        str(item.get("messageType") or ""),
        str(item.get("type") or ""),
        str(item.get("kind") or ""),
        " ".join(str(key or "") for key in item.keys()),
    ]).lower()
    if "opendart" in text or "dart" in text:
        return "국내 공시 목록·접수일·보고서명"
    if "sec edgar" in text or "edgar" in text:
        return "해외 공시 제출자료·기업 facts"
    if any(term in text for term in ["gdelt", "google news", "news", "headline", "article", "rss", "뉴스"]):
        return "국내외 뉴스 제목·요약·원문 URL·발행시각"
    if "fred" in text or "macro" in text:
        return "미국 금리·스프레드·거시 시계열"
    if "coingecko" in text or "crypto" in text or "coin" in text:
        return "크립토 가격·거래액·24시간/7일 변동률"
    if "alpha vantage" in text:
        if "fx" in text or "exchange" in text or "currency" in text:
            return "환율 시계열"
        if "fundamental" in text or "earnings" in text:
            return "해외 기업 펀더멘털·실적 지표"
        return "해외 주식 시세·거래량"
    if "kis" in text:
        if "websocket" in text:
            return "국내 주식 실시간 체결·호가"
        return "국내 주식 현재가·호가·체결·투자자 수급"
    if "toss" in text:
        return "계좌 보유수량·평균매입가·평가금액"
    if "brokeraccount" in text:
        return "계좌 평가금액·기준 환율"
    if any(term in text for term in ["currentprice", "price", "quote", "volume", "tradingvalue"]):
        return "시세·거래량·거래대금"
    if any(term in text for term in ["orderbook", "bid", "ask", "imbalance"]):
        return "매수/매도 호가잔량·호가불균형"
    if any(term in text for term in ["foreign", "institution", "individual", "investor"]):
        return "외국인·기관·개인 투자자별 수급"
    return "제공 API 원천 데이터"


def _collection_source_label(source: object, kind: object) -> str:
    source_text = _text(str(source or "데이터").strip() or "데이터", 42)
    kind_text = _text(str(kind or "").strip(), 24)
    if not kind_text or kind_text in source_text:
        return source_text
    return source_text + " / " + kind_text

def _human_readable_cooldown_reason(value: object) -> str:
    text = _clean_reason_text(value, 170)
    if not text:
        return ""
    lowered = text.casefold()
    if "subject=" in lowered or "relationruleids=" in lowered or "sourceeventkeys=" in lowered:
        if "관계 경로 변경" in text or "관계 의미 경로 변경" in text:
            return "관계 경로 변경: 핵심 판단 축 조합이 달라졌습니다."
        return "관계 근거 조합이 바뀌었습니다."
    if "새 뉴스/공시 원천 근거 추가" in text:
        return "새 뉴스/공시 원천 근거가 추가됐습니다."
    if "새 근거 신호 추가" in text:
        readable = text
        replacements = {
            "holdingTiming": "보유 타이밍",
            "watchlistOntologySignal": "관심종목 신호",
            "externalDartDisclosure": "국내 공시",
            "externalSecDisclosure": "해외 공시",
            "researchEvidence": "뉴스·리서치",
        }
        for before, after in replacements.items():
            readable = readable.replace(before, after)
        return readable
    return text


def notification_cooldown_release_summary(context: Dict[str, object]) -> str:
    context = context or {}
    if context.get("cooldownSuppressed") or context.get("honeyStateSuppressed"):
        return ""
    decision = str(context.get("cooldownDecision") or context.get("honeyStateDecision") or "").strip()
    decision = {
        "material_change": "meaningful-change",
        "threshold_change": "new-condition",
        "scheduled_summary": "scheduled-summary",
    }.get(decision, decision)
    if not decision or decision == "cooldown":
        return ""
    cooldown_enabled = bool(context.get("cooldownEnabled") or context.get("honeyStateCooldownEnabled"))
    reason = _human_readable_cooldown_reason(
        context.get("cooldownReason")
        or context.get("repeatBypassReason")
        or context.get("honeyStateReason")
    )
    if not cooldown_enabled and not reason:
        return ""
    age = _number(context.get("cooldownLastSentAgeMinutes") or context.get("honeyStateLastSentAgeMinutes"))
    cooldown = _number(context.get("cooldownMinutes") or context.get("honeyStateCooldownMinutes"))
    age_text = _minute_count_text(age)
    cooldown_text = _minute_count_text(cooldown)
    before_cooldown = bool(age_text and cooldown_text and age < cooldown)
    if decision == "new-condition":
        if age_text and cooldown_text:
            return "현재 조건 조합이 처음 감지되어 기본 쿨다운 " + cooldown_text + "과 별개로 보냈습니다."
        return "현재 조건 조합이 처음 감지되어 반복 제한 없이 보냈습니다."
    if decision == "scheduled-summary":
        if age_text and cooldown_text:
            return "마지막 발송 후 " + age_text + "이 지나 기본 쿨다운 " + cooldown_text + "을 충족했습니다."
        return reason or "지속 상태 요약 기준을 충족해 다시 보냈습니다."
    if decision in {"meaningful-change", "typedb-profit-loss-change"}:
        if before_cooldown and reason:
            return "마지막 발송 후 " + age_text + "으로 기본 쿨다운 " + cooldown_text + " 전이지만, " + reason + " 때문에 다시 보냈습니다."
        if reason:
            return reason + " 때문에 반복 제한을 통과했습니다."
    if reason:
        return reason
    return ""

def _contains_any(value: object, terms: List[str]) -> bool:
    text = str(value or "").lower()
    return any(str(term or "").lower() in text for term in terms)

def _source_event_titles(context: Dict[str, object], limit: int = 3) -> List[str]:
    prompt_context = notification_ai_prompt_context(str((context or {}).get("messageType") or (context or {}).get("rule") or "notification"), context or {})
    facts = prompt_context.get("facts") if isinstance(prompt_context.get("facts"), dict) else {}
    rows: List[str] = []
    for item in (facts.get("newsHeadlines") or []) + (facts.get("researchEvidence") or []):
        if not isinstance(item, dict):
            continue
        title = source_detail_text(item, "title", "summary", "articleSummaryKo")
        source = source_detail_text(item, "domain", "provider", "source")
        impact = source_detail_text(item, "stockImpactLabel", "impactLabel")
        if not title:
            continue
        prefix = (source + " · ") if source else ""
        suffix = (" · " + impact) if impact and impact not in {"중립", "neutral", "Neutral"} else ""
        append_unique_text(rows, prefix + title + suffix, 150)
        if len(rows) >= limit:
            break
    return rows[:limit]


def _price_position_summary(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    del response
    current = _plain_value(context, "현재가")
    average = _plain_value(context, "평균매입가") or _plain_value(context, "평단가")
    pnl = _plain_value(context, "수익률") or _plain_value(context, "손익")
    trend = _plain_value(context, "추세")
    if not any([current, average, pnl, trend]):
        return ""
    details = []
    if current:
        details.append("현재가 " + current)
    if average:
        details.append("평균매입가 " + average)
    if pnl:
        details.append("수익률 " + pnl)
    if trend:
        details.append(trend)
    return _text("현재 관측값: " + " / ".join(details[:4]), 210)

def _relation_feature_summary(context: Dict[str, object]) -> str:
    facts = relation_facts(context or {})
    if not facts:
        return ""
    rows: List[str] = []
    ma5 = _number(facts.get("ma5Distance"))
    ma20 = _number(facts.get("ma20Distance"))
    ma60 = _number(facts.get("ma60Distance"))
    if facts.get("ma5"):
        rows.append("5일선 " + ("위" if ma5 >= 0 else "아래") + " " + str(abs(round(ma5, 1))) + "%")
    if facts.get("ma20"):
        rows.append("20일선 " + ("위" if ma20 >= 0 else "아래") + " " + str(abs(round(ma20, 1))) + "%")
    if facts.get("ma60"):
        rows.append("60일선 " + ("위" if ma60 >= 0 else "아래") + " " + str(abs(round(ma60, 1))) + "%")
    btc24 = _number(facts.get("btcChange24h"))
    btc7 = _number(facts.get("btcChange7d"))
    if facts.get("isBtcSensitive") and (btc24 or btc7):
        rows.append("BTC 민감 종목 · 24h " + str(round(btc24, 1)) + "% / 7d " + str(round(btc7, 1)) + "%")
    ten_year = _number(facts.get("us10yYield") or facts.get("us10y") or facts.get("tenYearYield"))
    fx = _number(facts.get("usdKrw") or facts.get("usdkrw") or facts.get("fxRate"))
    if ten_year:
        rows.append("미 10년 금리 " + str(round(ten_year, 2)) + "%")
    if fx:
        rows.append("USD/KRW " + str(round(fx, 1)))
    return " / ".join(rows[:4])

def _news_event_summary(context: Dict[str, object]) -> str:
    titles = _source_event_titles(context, 3)
    if not titles:
        return ""
    return "뉴스·공시 원문 확인 대상: " + " / ".join(titles)

def context_specific_insight_rows(context: Dict[str, object], response: NotificationAIValidatedResponse, limit: int = MESSAGE_CONTEXT_ROW_LIMIT) -> List[str]:
    rows: List[str] = []
    for item in _driver_rows(context, ["risk", "support", "counter", "neutral"], limit):
        append_unique_text(rows, item, 230)
    append_unique_text(rows, _price_position_summary(context, response), 230)
    append_unique_text(rows, _relation_feature_summary(context), 210)
    append_unique_text(rows, _news_event_summary(context), 230)
    if response.precomputed_action and response.precomputed_action != response.action:
        append_unique_text(
            rows,
            "계산 후보는 " + action_label_for_action(response.precomputed_action, context) + "였지만 최종 메시지는 " + action_label_for_action(response.action, context) + " 기준으로 완화/조정했습니다.",
            210,
        )
    return rows[:limit]


def external_api_source_rows(context: Dict[str, object], limit: int = MESSAGE_API_SOURCE_ROW_LIMIT) -> List[str]:
    context = context or {}
    rows: List[str] = []
    values = context.get("externalApiSourceLines")
    if isinstance(values, str):
        rows.extend([line.strip() for line in values.splitlines() if line.strip()])
    elif isinstance(values, list):
        for item in values:
            if isinstance(item, dict):
                line = external_api_source_line(item)
            else:
                line = str(item or "").strip()
            if line:
                rows.append(line)
    structured = context.get("externalApiSources")
    if isinstance(structured, list):
        for item in structured:
            if not isinstance(item, dict):
                continue
            line = external_api_source_line(item)
            if line:
                rows.append(line)
    unique: List[str] = []
    seen = set()
    for row in rows:
        text = re.sub(r"\s+", " ", str(row or "")).strip()
        if not text or text in seen:
            continue
        unique.append(text)
        seen.add(text)
        if len(unique) >= limit:
            break
    return unique

def _compact_text_segments(values: List[object], limit: int = 3, max_len: int = 180) -> str:
    rows: List[str] = []
    row_limit = limit if limit and limit > 0 else None
    for value in values or []:
        text = re.sub(r"\s+", " ", _text(value, max_len)).strip()
        text = re.sub(r"[\.\?!。]+$", "", text).strip()
        if not text:
            continue
        append_unique_text(rows, text, max_len)
        if row_limit and len(rows) >= row_limit:
            break
    return " / ".join(rows)

def compact_ai_opinion_sentence(context: Dict[str, object], response: NotificationAIValidatedResponse, level: str) -> str:
    action_label = action_label_for_action(response.action, context) or response.action_label or response.action
    base = "AI는 전체 내용을 종합해 " + action_label + "를 우선 보는 의견"
    details: List[str] = []
    if response.precomputed_action and response.precomputed_action != response.action:
        adjustment = (
            "계산 후보 "
            + action_label_for_action(response.precomputed_action, context)
            + "에서 최종 "
            + action_label
            + "로 조정한 점"
        )
        if response.disagreement_reason:
            adjustment += " (" + _compact_text_segments([response.disagreement_reason], 1, 0) + ")"
        details.append(adjustment)
    context_summary = _compact_text_segments(context_specific_insight_rows(context, response, 3), 0, 0)
    if context_summary:
        details.append("주요 상황 " + context_summary)
    evidence_summary = _compact_text_segments(response.evidence, 0, 0)
    if evidence_summary:
        details.append("핵심 근거 " + evidence_summary)
    counter_summary = _compact_text_segments(response.counter_evidence, 0, 0)
    if counter_summary:
        details.append("반대 신호 " + counter_summary)
    checks = []
    if response.opinion:
        checks.append(response.opinion)
    if response.invalidation_condition:
        checks.append("의견이 약해지는 조건: " + response.invalidation_condition)
    checks.extend(response.next_checks)
    check_summary = _compact_text_segments(checks, 0, 0)
    if check_summary:
        details.append("다음 확인 " + check_summary)
    data_notes = customer_data_note_rows(list(response.missing_data_impact))
    data_summary = _compact_text_segments(data_notes, 0, 0)
    if data_summary:
        details.append("추가 확인 데이터 " + data_summary)
    if details:
        return base + "입니다. " + " / ".join(details)
    return base + "입니다."

def _full_ai_opinion_rows(context: Dict[str, object], response: NotificationAIValidatedResponse, level: str) -> List[str]:
    action_label = action_label_for_action(response.action, context) or response.action_label or response.action
    rows: List[str] = []
    conclusion = _compact_text_segments([response.summary], 1, 180)
    append_unique_text(
        rows,
        "결론: " + action_label + ((". " + conclusion) if conclusion else ""),
        240,
    )
    if response.precomputed_action and response.precomputed_action != response.action:
        adjustment = (
            "판단 조정: 계산 후보 "
            + action_label_for_action(response.precomputed_action, context)
            + " → 최종 "
            + action_label
        )
        reason = _compact_text_segments([response.disagreement_reason], 1, 180)
        append_unique_text(rows, adjustment + ((" (" + reason + ")") if reason else ""), 260)
    for index, item in enumerate(response.evidence or [], 1):
        append_unique_text(rows, "근거 " + str(index) + ": " + _text(item, 260), 300)
    for index, item in enumerate(response.counter_evidence or [], 1):
        append_unique_text(rows, "반대 신호 " + str(index) + ": " + _text(item, 260), 300)
    if response.opinion:
        append_unique_text(rows, "실행 전 판단: " + _text(response.opinion, 260), 300)
    if response.invalidation_condition:
        append_unique_text(rows, "의견이 약해지는 조건: " + _text(response.invalidation_condition, 260), 300)
    for index, item in enumerate(response.next_checks or [], 1):
        append_unique_text(rows, "다음 확인 " + str(index) + ": " + _text(item, 260), 300)
    for index, item in enumerate(response.missing_data_impact or [], 1):
        append_unique_text(rows, "데이터 빈 곳 " + str(index) + ": " + _text(item, 260), 300)
    for index, item in enumerate(response.validation_warnings or [], 1):
        append_unique_text(rows, "검증 결과 " + str(index) + ": " + _text(item, 260), 300)
    return [_html_bullet(_ai_marked_value(row), level) for row in rows if row]

def _strategy_guide_value(response: NotificationAIValidatedResponse, key: str) -> str:
    guide = response.strategy_guide if isinstance(response.strategy_guide, dict) else {}
    value = guide.get(key)
    if isinstance(value, list):
        return " / ".join(str(item).strip() for item in value if str(item or "").strip())
    return str(value or "").strip()

def _strategy_guide_list(response: NotificationAIValidatedResponse, key: str) -> List[str]:
    guide = response.strategy_guide if isinstance(response.strategy_guide, dict) else {}
    value = guide.get(key)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if str(value or "").strip():
        return [str(value).strip()]
    return []

def _execution_plan(context: Dict[str, object]) -> Dict[str, object]:
    relation_context = relation_context_value(context or {})
    plan = relation_context.get("executionPlan") if isinstance(relation_context.get("executionPlan"), dict) else {}
    if plan:
        return plan
    opinion = context.get("activeInvestmentOpinion") if isinstance(context.get("activeInvestmentOpinion"), dict) else {}
    return opinion.get("executionPlan") if isinstance(opinion.get("executionPlan"), dict) else {}


def _execution_plan_value(context: Dict[str, object], *keys: str) -> str:
    plan = _execution_plan(context)
    for key in keys:
        value = plan.get(key)
        if isinstance(value, list):
            text = " / ".join(str(item).strip() for item in value if str(item or "").strip())
        else:
            text = str(value or "").strip()
        if text:
            return text
    return ""


def _execution_plan_list(context: Dict[str, object], *keys: str) -> List[str]:
    plan = _execution_plan(context)
    rows: List[str] = []
    for key in keys:
        value = plan.get(key)
        if isinstance(value, list):
            for item in value:
                append_unique_text(rows, str(item or "").strip(), 0)
        elif value not in (None, ""):
            append_unique_text(rows, str(value).strip(), 0)
    return rows


def _derived_action_mode(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    return (
        _strategy_guide_value(response, "actionMode")
        or _execution_plan_value(context, "actionMode", "executionMode", "primaryActionLabel")
        or "TypeDB 실행 계획의 조건 확인"
    )


def _derived_position_sizing(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    explicit = _strategy_guide_value(response, "positionSizing") or _execution_plan_value(
        context,
        "positionSizing",
        "sizing",
        "positionSizeGuidance",
    )
    if explicit:
        return explicit
    if is_watchlist_context(context):
        return "진입 금액·비중은 계정 한도 안에서 정하세요."
    return "수량·비중은 손실 허용선과 매도 가능 수량을 함께 확인하세요."


def _derived_interpretation(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    explicit = _strategy_guide_value(response, "interpretation")
    if explicit:
        return explicit
    return " ".join(part for part in [str(response.summary or "").strip(), str(response.opinion or "").strip()] if part)


def _derived_execution_criteria(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    explicit = _strategy_guide_value(response, "executionCriteria") or _execution_plan_value(
        context,
        "executionCriteria",
        "actionCriteria",
    )
    if explicit:
        return explicit
    rows: List[str] = []
    weaken = _execution_plan_list(context, "weakenConditions")
    strengthen = _execution_plan_list(context, "strengthenConditions")
    next_checks = _execution_plan_list(context, "nextChecks")
    if weaken:
        rows.append("의견 완화 조건: " + " / ".join(weaken[:2]))
    if strengthen:
        rows.append("의견 보강 조건: " + " / ".join(strengthen[:2]))
    if next_checks:
        rows.append("다음 확인: " + " / ".join(next_checks[:2]))
    if rows:
        return ". ".join(rows)
    return "다음 데이터 업데이트에서 현재 근거와 반대 근거를 다시 확인합니다."


def _derived_invalidation_condition(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    explicit = _strategy_guide_value(response, "invalidationCondition") or response.invalidation_condition
    if explicit:
        return watchlist_friendly_text(context, explicit)
    weaken = _execution_plan_list(context, "weakenConditions")
    if weaken:
        return watchlist_friendly_text(context, " / ".join(weaken[:2]))
    return "현재 근거가 사라지거나 반대 근거가 새로 확인되면 의견을 다시 봅니다."


def _strategy_validation_limiters(context: Dict[str, object], response: NotificationAIValidatedResponse) -> List[str]:
    rows = list(_strategy_guide_list(response, "dataLimitations"))
    rows.extend(_execution_plan_list(context, "dataLimitations", "missingDataImpact"))
    for item in customer_data_note_rows(list(response.missing_data_impact)):
        append_unique_text(rows, item, 0)
    return rows


def _derived_ai_hypothesis(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    explicit = _strategy_guide_value(response, "aiHypothesis")
    if explicit:
        return explicit
    selected_id = str(response.selected_hypothesis_id or "")
    for item in response.hypotheses or []:
        if not isinstance(item, dict) or str(item.get("hypothesisId") or "") != selected_id:
            continue
        return str(item.get("claim") or item.get("templateLabel") or "").strip()
    return ""


def _ai_hypothesis_boundary(response: NotificationAIValidatedResponse) -> str:
    return _strategy_guide_value(response, "hypothesisBoundary") or "현재 TypeDB 가설과 검증된 근거를 벗어난 내용은 다음 확인 가설로만 다룹니다."

def strategy_guide_quality(context: Dict[str, object], response: NotificationAIValidatedResponse) -> Dict[str, object]:
    plan = _execution_plan(context)
    explicit_execution = _strategy_guide_value(response, "executionCriteria") or _execution_plan_value(
        context,
        "executionCriteria",
        "actionCriteria",
    )
    inferred_execution = bool(
        _execution_plan_list(context, "weakenConditions", "strengthenConditions", "nextChecks")
    )
    explicit_invalidation = bool(
        _strategy_guide_value(response, "invalidationCondition")
        or response.invalidation_condition
        or _execution_plan_list(context, "weakenConditions")
    )
    checks = [
        ("typedbExecutionPlan", bool(plan)),
        ("actionMode", bool(_strategy_guide_value(response, "actionMode") or _execution_plan_value(context, "actionMode", "executionMode", "primaryActionLabel"))),
        ("positionSizing", bool(_strategy_guide_value(response, "positionSizing") or _execution_plan_value(context, "positionSizing", "sizing", "positionSizeGuidance"))),
        ("ruleboxExecutionCriteria", bool(explicit_execution or inferred_execution)),
        ("dataLimitations", bool(_strategy_validation_limiters(context, response))),
        ("aiHypothesisSeparated", bool(_derived_ai_hypothesis(context, response))),
        ("invalidationCondition", explicit_invalidation),
    ]
    passed = [key for key, ok in checks if ok]
    missing = [key for key, ok in checks if not ok]
    state = "complete" if not missing else "partial" if passed else "missing"
    return {
        "state": state,
        "passed": passed,
        "missing": missing,
        "score": round((len(passed) / len(checks)) * 100) if checks else 0,
        "source": "typedb-execution-plan-and-ai-response",
    }

def strategy_guide_rows(context: Dict[str, object], response: NotificationAIValidatedResponse, level: str) -> List[str]:
    rows: List[str] = []
    action_label = action_label_for_action(response.action, context) or response.action_label or response.action
    interpretation = watchlist_friendly_text(context, _derived_interpretation(context, response))
    if interpretation:
        append_unique_text(rows, "결론: " + action_label + ". AI 해석: " + interpretation, 0)
    action_mode = _derived_action_mode(context, response)
    if action_mode:
        append_unique_text(rows, "대응 모드: " + action_mode, 0)
    execution = watchlist_friendly_text(context, _derived_execution_criteria(context, response))
    if execution:
        append_unique_text(rows, "실행 기준: " + execution, 0)
    evidence_summary = watchlist_friendly_text(context, _compact_text_segments(response.evidence or context_specific_insight_rows(context, response, MESSAGE_CONTEXT_ROW_LIMIT), 3, 150))
    if evidence_summary:
        append_unique_text(rows, "핵심 근거: " + evidence_summary, 0)
    counter_summary = watchlist_friendly_text(context, _compact_text_segments(response.counter_evidence, 2, 140))
    if counter_summary:
        append_unique_text(rows, "반대 신호: " + counter_summary, 0)
    check_items = list(_strategy_guide_list(response, "confirmationData"))
    check_items.extend(response.next_checks or [])
    check_summary = watchlist_friendly_text(context, _compact_text_segments(check_items, 3, 140))
    if check_summary:
        append_unique_text(rows, "확인할 데이터/다음 확인: " + check_summary, 0)
    limiters = _strategy_validation_limiters(context, response)
    if limiters:
        append_unique_text(rows, "추가 확인 데이터: " + _compact_text_segments(limiters, 3, 140), 0)
    hypothesis = _derived_ai_hypothesis(context, response)
    if hypothesis:
        append_unique_text(rows, "AI 가설: " + hypothesis + " " + _ai_hypothesis_boundary(response), 0)
    invalidation = _derived_invalidation_condition(context, response)
    if invalidation:
        append_unique_text(rows, "의견이 약해지는 조건: " + invalidation, 0)
    return [_html_bullet(_ai_marked_value(row), level) for row in rows if row]

def compact_ai_opinion_rows(context: Dict[str, object], response: NotificationAIValidatedResponse, level: str) -> List[str]:
    action_label = action_label_for_action(response.action, context) or response.action_label or response.action
    rows: List[str] = []
    conclusion = _compact_text_segments([response.summary], 1, 0)
    append_unique_text(
        rows,
        "결론: " + action_label + ((". " + conclusion) if conclusion else ""),
        0,
    )
    if response.precomputed_action and response.precomputed_action != response.action:
        adjustment = (
            "판단 조정: 계산 후보 "
            + action_label_for_action(response.precomputed_action, context)
            + " → 최종 "
            + action_label
        )
        reason = _compact_text_segments([response.disagreement_reason], 1, 0)
        append_unique_text(rows, adjustment + ((" (" + reason + ")") if reason else ""), 0)
    evidence_summary = _compact_text_segments(response.evidence, 0, 0)
    if not evidence_summary:
        evidence_summary = _compact_text_segments(context_specific_insight_rows(context, response, MESSAGE_CONTEXT_ROW_LIMIT), 0, 0)
    if evidence_summary:
        append_unique_text(rows, "핵심 근거: " + evidence_summary, 0)
    counter_summary = _compact_text_segments(response.counter_evidence, 0, 0)
    if counter_summary:
        append_unique_text(rows, "반대 신호: " + counter_summary, 0)
    checks = []
    if response.opinion:
        checks.append(response.opinion)
    if response.invalidation_condition:
        checks.append("의견이 약해지는 조건: " + response.invalidation_condition)
    checks.extend(response.next_checks)
    check_summary = _compact_text_segments(checks, 0, 0)
    if check_summary:
        append_unique_text(rows, "다음 확인: " + check_summary, 0)
    data_summary = _compact_text_segments(customer_data_note_rows(list(response.missing_data_impact)), 0, 0)
    if data_summary:
        append_unique_text(rows, "추가 확인 데이터: " + data_summary, 0)
    return [_html_bullet(_ai_marked_value(row), level) for row in rows if row]

def relation_axis_summary_rows(context: Dict[str, object], level: str, limit: int = 5) -> List[str]:
    rows: List[str] = []
    for item in relation_axis_summary_lines(context, limit):
        append_unique_text(rows, item, 230)
        if len(rows) >= limit:
            break
    return [_html_bullet(item, level) for item in rows if str(item or "").strip()]


def customer_reason_rows(context: Dict[str, object], level: str) -> List[str]:
    return [_html_bullet(item, level) for item in customer_alert_reason_lines(context) if str(item or "").strip()]


def customer_inference_rows(context: Dict[str, object], level: str) -> List[str]:
    return [_html_bullet(item, level) for item in customer_inferred_fact_lines(context) if str(item or "").strip()]


def customer_data_state_rows(context: Dict[str, object], level: str) -> List[str]:
    return [_html_bullet(item, level) for item in customer_data_state_and_missing_lines(context) if str(item or "").strip()]


def _valuation_value_present(value: object) -> bool:
    return value not in (None, "") and str(value).strip() not in {"", "-"}


def _valuation_price_display(value: object, currency: object) -> str:
    amount = _number(value)
    if not amount and not _valuation_value_present(value):
        return ""
    return price_money(amount, str(currency or "KRW"))


def _valuation_pct_display(value: object) -> str:
    if not _valuation_value_present(value):
        return ""
    return signed_pct(_number(value))


def _valuation_per_inputs(facts: Dict[str, object], currency: object) -> str:
    current_per = _number(facts.get("valuationCurrentPER"))
    expected_eps = _number(facts.get("valuationExpectedEPS"))
    target_per = _number(facts.get("valuationTargetPER"))
    return " · ".join(
        part
        for part in [
            ("현재 PER " + str(round(current_per, 2)).rstrip("0").rstrip(".") + "배") if current_per else "",
            ("사용 EPS " + _valuation_price_display(expected_eps, currency)) if expected_eps else "",
            ("기준 PER " + str(round(target_per, 2)).rstrip("0").rstrip(".") + "배") if target_per else "",
        ]
        if part
    )


def valuation_detail_rows(context: Dict[str, object], level: str) -> List[str]:
    facts = relation_facts(context or {})
    if not facts:
        return []
    rows_data = facts.get("valuationRows") if isinstance(facts.get("valuationRows"), list) else []
    currency = facts.get("currency") or "KRW"
    formula = str(facts.get("valuationFormula") or "").strip() or "적정가 공식 미설정"
    substitution = str(facts.get("valuationSubstitution") or "").strip()
    missing_inputs = facts.get("valuationMissingInputs") if isinstance(facts.get("valuationMissingInputs"), list) else []
    has_valuation_fact = any(
        key in facts and facts.get(key) not in (None, "", [])
        for key in [
            "valuationFormula",
            "valuationSubstitution",
            "valuationCurrentPrice",
            "valuationFairValue",
            "valuationFairValuePrice",
            "valuationMarginOfSafetyPct",
            "valuationMinimumMarginOfSafetyPct",
            "valuationSourceLabel",
            "valuationSourceReason",
            "valuationPerStatus",
            "valuationPerReason",
            "valuationPreferredMetric",
            "valuationFundamentalDataSourcePriority",
            "valuationDataStateLabel",
            "valuationExplanation",
            "valuationDataStatus",
        ]
    )
    if not rows_data and not missing_inputs and not has_valuation_fact:
        return []
    if not rows_data and not missing_inputs and not facts.get("valuationFormula"):
        missing_inputs = ["적정가", "예상 EPS", "목표 PER"]
    if not substitution and missing_inputs:
        substitution = "대입값 부족: " + ", ".join(str(item) for item in missing_inputs[:5])
    current = _valuation_price_display(facts.get("valuationCurrentPrice") or facts.get("currentPrice"), currency)
    fair_value = _valuation_price_display(facts.get("valuationFairValue") or facts.get("valuationFairValuePrice"), currency)
    fair_value_low = _valuation_price_display(facts.get("valuationFairValueLow"), currency)
    fair_value_high = _valuation_price_display(facts.get("valuationFairValueHigh"), currency)
    fair_value_range = ""
    if fair_value_low and fair_value_high:
        fair_value_range = fair_value_low + " ~ " + fair_value_high
    margin = _valuation_pct_display(facts.get("valuationMarginOfSafetyPct"))
    conservative_margin = _valuation_pct_display(facts.get("valuationConservativeMarginOfSafetyPct"))
    optimistic_margin = _valuation_pct_display(facts.get("valuationOptimisticMarginOfSafetyPct"))
    minimum_margin = _valuation_pct_display(facts.get("valuationMinimumMarginOfSafetyPct"))
    margin_text = margin
    if margin and minimum_margin:
        margin_text += " / 요구 " + minimum_margin
    source = str(facts.get("valuationSourceLabel") or "").strip()
    if facts.get("valuationHasUserInput") and facts.get("valuationHasExternalInput") and source:
        source += " · 외부 데이터도 참고"
    if not source:
        source = "사용자 입력 없음 · 외부 밸류에이션 데이터 없음"
    per_status = str(facts.get("valuationPerStatus") or "").strip()
    per_reason = str(facts.get("valuationPerReason") or "").strip()
    per_inputs = _valuation_per_inputs(facts, currency)
    preferred_metric = str(facts.get("valuationPreferredMetric") or "").strip()
    source_priority = str(facts.get("valuationFundamentalDataSourcePriority") or "").strip()
    per_status_labels = {
        "available": "PER/EPS 사용",
        "missing": "PER/EPS 부족",
        "not_applicable": "PER보다 다른 기준 우선",
        "conversion_missing": "PER 확인 · 환산값 부족",
        "partial_conversion_missing": "PER 확인 · 환산값 부족",
    }
    per_line = per_status_labels.get(per_status, per_status)
    if per_reason:
        per_line = (per_line + " · " if per_line else "") + per_reason
    approval = ""
    if facts.get("valuationRequiresUserApproval") or facts.get("valuationIsAiGenerated"):
        status = str(facts.get("valuationReviewStatus") or facts.get("valuationApprovalStatus") or "ai_applied_pending_review").strip()
        status_labels = {
            "suggested": "AI 제안 · 사용자 검토 전",
            "ai_applied_pending_review": "AI 제안 자동 적용 · 사용자 검토 전",
            "user_approved": "사용자 승인",
            "user_modified": "사용자 수정 승인",
            "user_rejected": "사용자 거절",
            "approved": "사용자 승인",
            "modified": "사용자 수정 승인",
            "rejected": "사용자 거절",
        }
        status_label = status_labels.get(status, status)
        approval = status_label
    valuation_data_state = str(facts.get("valuationDataStateLabel") or "").strip() or "판단 보류"
    freshness_labels = {"fresh": "최신", "aging": "업데이트 필요", "stale": "오래됨", "unknown": "기준일 미확인"}
    freshness = freshness_labels.get(str(facts.get("valuationFreshnessStatus") or "unknown"), str(facts.get("valuationFreshnessStatus") or "기준일 미확인"))
    valuation_as_of = str(facts.get("valuationAsOf") or "").strip()
    decision_eligible = bool(facts.get("valuationDecisionEligible"))
    decision_status = "투자 판단 근거로 사용 가능" if decision_eligible else "참고만 사용 · 매수·매도 추론에서 제외"
    model_count = int(_number(facts.get("valuationModelCount"))) if _valuation_value_present(facts.get("valuationModelCount")) else 0
    disagreement = _valuation_pct_display(facts.get("valuationDisagreementPct"))
    consensus_labels = {"agreement": "모델 결과가 비슷함", "conflict": "모델 차이가 커 판단 보류", "single-model": "검증 가능한 모델 1개 이하"}
    consensus = consensus_labels.get(str(facts.get("valuationConsensusStatus") or ""), "")
    if model_count:
        consensus += ((" · " if consensus else "") + str(model_count) + "개 모델" + ((" · 차이 " + disagreement) if disagreement else ""))
    explanation = str(facts.get("valuationExplanation") or "").strip()
    if not explanation:
        explanation = "적정가 공식이나 적정가 입력값이 없어 현재가가 싼지 비싼지 계산하지 않았습니다. 설정 탭에서 적정가, 예상 EPS, 목표 PER 중 하나를 입력해야 합니다."
    data_status = str(facts.get("valuationDataStatus") or "").strip() or ("available" if fair_value and margin else "missing")
    status_labels = {
        "available": "계산 가능",
        "partial": "일부 부족",
        "missing": "부족",
    }
    method = str(facts.get("valuationMethod") or facts.get("valuationFormula") or "").strip()
    if facts.get("valuationIsAiGenerated"):
        if str(method).casefold() == "ai-current-price-anchor":
            status_text = "입력 부족 · 임시 기준"
        elif missing_inputs:
            status_text = "AI 초안 자동 적용 · 검토 필요"
        else:
            status_text = "AI 초안 자동 적용"
    else:
        status_text = status_labels.get(data_status, data_status)
    rows = [
        _html_row("사용 모델", method, level=level, max_len=260),
        _html_row("공식", formula, level=level, max_len=260),
        _html_row("대입값", substitution, level=level, max_len=260),
        _html_row("승인 상태", approval, level=level, max_len=180),
        _html_row("현재가", current or "현재가 없음", level=level),
        _html_row("기준 적정가", fair_value or "미설정", level=level),
        _html_row("적정가 범위", fair_value_range, level=level),
        _html_row("안전마진", margin_text or "계산 불가", level=level),
        _html_row("시나리오 안전마진", ("보수 " + conservative_margin + " / 낙관 " + optimistic_margin) if conservative_margin and optimistic_margin else "", level=level),
        _html_row("데이터 출처", source, level=level),
        _html_row("PER/EPS 입력", per_inputs, level=level, max_len=220),
        _html_row("PER 기준", per_line, level=level, max_len=300),
        _html_row("대체 기준", preferred_metric, level=level, max_len=180),
        _html_row("데이터 우선순위", source_priority, level=level, max_len=220),
        _html_row("계산 근거", str(facts.get("valuationSourceReason") or "").strip(), level=level, max_len=260),
        _html_row("자료 상태", valuation_data_state, level=level, max_len=260),
        _html_row("데이터 기준", freshness + ((" · " + valuation_as_of) if valuation_as_of else ""), level=level, max_len=220),
        _html_row("판단 사용", decision_status, level=level, max_len=220),
        _html_row("모델 비교", consensus, level=level, max_len=220),
        _html_row("계산 상태", status_text, level=level),
        _html_row("계산 뜻", explanation, level=level, max_len=700),
        _html_row("부족 데이터", ", ".join(str(item) for item in missing_inputs[:5]), level=level, max_len=260),
    ]
    return [row for row in rows if row]


def compact_valuation_detail_rows(context: Dict[str, object], level: str) -> List[str]:
    facts = relation_facts(context or {})
    if not facts:
        return []
    rows_data = facts.get("valuationRows") if isinstance(facts.get("valuationRows"), list) else []
    missing_inputs = facts.get("valuationMissingInputs") if isinstance(facts.get("valuationMissingInputs"), list) else []
    if not rows_data and not missing_inputs and not any(str(key).startswith("valuation") for key in facts):
        return []
    currency = facts.get("currency") or "KRW"
    is_unreviewed_ai_proposal = (
        (bool(facts.get("valuationIsAiGenerated")) or str(facts.get("valuationSourceType") or "").strip().lower() == "ai")
        and (bool(facts.get("valuationRequiresUserApproval")) or not bool(facts.get("valuationDecisionEligible")))
    )
    if is_unreviewed_ai_proposal:
        missing_text = " · ".join(str(item) for item in missing_inputs[:4] if str(item or "").strip())
        per_inputs = _valuation_per_inputs(facts, currency)
        valuation_basis = str(
            facts.get("valuationFormula")
            or facts.get("valuationPreferredMetric")
            or facts.get("valuationPerReason")
            or ""
        ).strip()
        return [
            _html_row("평가 상태", "사용자 검토 전 AI 초안 · 투자 판단에서 제외", level=level, max_len=240),
            _html_row("알림 처리", "적정가·안전마진 숫자를 표시하지 않습니다.", level=level, max_len=240),
            _html_row("평가 기준", valuation_basis, level=level, max_len=240),
            _html_row("검증 입력", per_inputs, level=level, max_len=220),
            _html_row("확인할 데이터", missing_text or "공식 실적, 성장률 전망, 피어 또는 과거 PER 범위", level=level, max_len=260),
        ]
    method = str(facts.get("valuationMethod") or facts.get("valuationFormula") or "").strip()
    fair_value = _valuation_price_display(facts.get("valuationFairValue") or facts.get("valuationFairValuePrice"), currency)
    fair_value_low = _valuation_price_display(facts.get("valuationFairValueLow"), currency)
    fair_value_high = _valuation_price_display(facts.get("valuationFairValueHigh"), currency)
    fair_value_text = fair_value or "계산 불가"
    if fair_value_low and fair_value_high:
        fair_value_text += " · 예상 범위 " + fair_value_low + " ~ " + fair_value_high
    margin_value = facts.get("valuationMarginOfSafetyPct")
    minimum_value = facts.get("valuationMinimumMarginOfSafetyPct")
    margin = _valuation_pct_display(margin_value)
    minimum_margin = _valuation_pct_display(minimum_value)
    margin_text = margin or "계산 불가"
    if margin and minimum_margin:
        meets_requirement = _number(margin_value) >= _number(minimum_value)
        margin_text += " · 계정 기준 " + minimum_margin + (" 충족" if meets_requirement else " 미달")
    source = str(facts.get("valuationSourceLabel") or "").strip()
    valuation_data_state = str(facts.get("valuationDataStateLabel") or "").strip()
    review_status = str(facts.get("valuationReviewStatus") or facts.get("valuationApprovalStatus") or "").strip()
    review_labels = {
        "suggested": "사용자 검토 전",
        "ai_applied_pending_review": "AI 초안 · 사용자 검토 전",
        "user_approved": "사용자 승인",
        "user_modified": "사용자 수정 승인",
        "user_rejected": "사용자 거절",
        "approved": "사용자 승인",
        "modified": "사용자 수정 승인",
        "rejected": "사용자 거절",
    }
    basis = " · ".join(part for part in [source, valuation_data_state, review_labels.get(review_status, review_status)] if part)
    if "valuationDecisionEligible" in facts:
        basis += (" · " if basis else "") + ("투자 판단에 사용" if facts.get("valuationDecisionEligible") else "참고만 사용")
    per_status = str(facts.get("valuationPerStatus") or "").strip()
    per_reason = str(facts.get("valuationPerReason") or "").strip()
    per_inputs = _valuation_per_inputs(facts, currency)
    missing_parts = [str(item) for item in missing_inputs[:4] if str(item or "").strip()]
    if per_status in {"missing", "conversion_missing", "partial_conversion_missing"} and per_reason:
        missing_parts.append(per_reason)
    rows = [
        _html_row("평가 방법", method, level=level, max_len=180),
        _html_row("기준 적정가", fair_value_text, level=level, max_len=240),
        _html_row("현재가와 적정가 차이", margin_text, level=level, max_len=180),
        _html_row("자료 상태", basis, level=level, max_len=240),
        _html_row("PER/EPS 기준", per_inputs, level=level, max_len=220),
        _html_row("확인할 데이터", " · ".join(missing_parts), level=level, max_len=260),
    ]
    return [row for row in rows if row]


def compact_beginner_judgment_rows(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
    level: str,
) -> List[str]:
    relation_state = relation_state_values(context or {})
    profile = " · ".join(part for part in [account_strategy_label(context), account_delivery_level_label(context)] if part)
    rows = [
        _html_row("대응", action_label_for_action(response.action, context) or response.action_label, level=level),
        _html_row("이유", response.summary, level=level, max_len=420),
        _html_row("확인 단계", relation_state.get("reviewLabel") or response.review_label, level=level),
        _html_row("자료 상태", relation_state.get("dataLabel") or response.data_state_label, level=level),
        _html_row("이번 변화", relation_state.get("changeLabel"), level=level),
        _html_row("AI 검증", response.validation_label, level=level),
        _html_row("계정 기준", profile, level=level),
        _html_row("안내", "실행 전 참고용이며 자동 주문되지 않습니다.", level=level),
    ]
    return [row for row in rows if row]


def compact_beginner_reason_rows(context: Dict[str, object], level: str) -> List[str]:
    values = customer_alert_reason_lines(context)
    return [_html_bullet(item, level) for item in values[:3] if str(item or "").strip()]


def compact_beginner_evidence_rows(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
    level: str,
) -> List[str]:
    values = list(relation_axis_summary_lines(context, 8))
    if not values:
        values = list(response.evidence or [])
    rows: List[str] = []
    for item in values:
        text = _message_text(item, level)
        if not text:
            continue
        normalized = re.sub(r"[^0-9a-z가-힣]+", "", text.casefold())
        if any(normalized and (normalized in key or key in normalized) for key in [re.sub(r"[^0-9a-z가-힣]+", "", row.casefold()) for row in rows]):
            continue
        rows.append(text)
        if len(rows) >= 3:
            break
    if response.counter_evidence:
        counter = _message_text(response.counter_evidence[0], level)
        if counter and all(counter not in row and row not in counter for row in rows):
            rows.append("반대 신호: " + counter)
    return [_html_bullet(item, level) for item in rows[:5]]


def compact_beginner_next_rows(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
    level: str,
) -> List[str]:
    rows: List[str] = []
    execution = compact_sentence_count(
        watchlist_friendly_text(context, _derived_execution_criteria(context, response)),
        2,
    )
    if execution:
        rows.append("실행 조건: " + execution)
    invalidation = _derived_invalidation_condition(context, response)
    if invalidation and invalidation not in execution:
        rows.append("다시 판단할 조건: " + invalidation)
    for item in response.next_checks[:1]:
        text = watchlist_friendly_text(context, str(item or "").strip())
        if text and all(text not in row and row not in text for row in rows):
            rows.append("다음 확인: " + text)
    for item in hypothesis_decision_brief_text_rows(context, response)[:1]:
        text = watchlist_friendly_text(context, str(item or "").strip())
        if text and all(text not in row and row not in text for row in rows):
            rows.append(text)
    missing = customer_data_note_rows(list(response.missing_data_impact))
    if missing:
        rows.append("부족한 데이터: " + " / ".join(missing[:2]))
    return [_html_bullet(item, level) for item in rows]


def compact_sentence_count(value: object, limit: int = 2) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    parts = [part.strip() for part in re.split(r"(?<=[가-힣])\.\s+(?=[가-힣$0-9])", text) if part.strip()]
    if len(parts) <= limit:
        return text
    return ". ".join(parts[:limit]).rstrip(".") + "."


def execution_telegram_message(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    level = delivery_level_from_context(context)
    if level in {"absoluteBeginner", "beginner"}:
        return execution_telegram_message_compact_beginner(context, response, level)
    headline = execution_headline(context, response)
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
    investor = _investor_text(context)
    sent = str(context.get("sentTime") or "").strip()
    reference = response.reference_date or reference_date(context)
    current_state_rows = [
        _html_row("현재가", current, level=level),
        _html_row("평균매입가", average, level=level),
        _html_row("수익률", pnl, level=level),
        _html_row("보유 수량", quantity, level=level),
        _html_row("매도가능 수량", sellable, level=level),
        _html_row("종목 평가금액", position_value, level=level),
        _html_row("계좌 평가금액", account_value, level=level),
        _html_row("보유", legacy_balance, level=level),
        _html_row("추세", trend, level=level),
        _html_row("수급", flow, level=level),
        *_html_multiline_rows("투자자", investor),
    ]
    current_state_rows = [row for row in current_state_rows if str(row or "").strip()]
    parts = [
        "<b>" + html.escape(headline, quote=False) + "</b>",
        ("<code>" + html.escape(target, quote=False) + "</code>") if target else "",
        "",
        "<b>" + ai_judgment_section_title(level) + "</b>",
        *ai_judgment_rows(response, level, context),
    ]
    hypothesis_rows = hypothesis_comparison_rows(response, level)
    if hypothesis_rows:
        parts.extend(["", "<b>AI 경쟁 가설</b>", *hypothesis_rows])
    lifecycle_rows = hypothesis_decision_brief_rows(context, response, level)
    if lifecycle_rows:
        parts.extend(["", "<b>가설 변화와 검증</b>", *lifecycle_rows])
    reason_rows = customer_reason_rows(context, level)
    if reason_rows:
        parts.extend(["", "<b>왜 알림이 왔나요?</b>", *reason_rows])
    if current_state_rows:
        parts.extend(["", "<b>현재 상황</b>", *current_state_rows])
    valuation_rows = valuation_detail_rows(context, level)
    if valuation_rows:
        parts.extend(["", "<b>밸류에이션</b>", *valuation_rows])
    inference_rows = customer_inference_rows(context, level)
    if inference_rows:
        parts.extend(["", "<b>관계 분석으로 새로 확인한 사실</b>", *inference_rows])
    axis_rows = relation_axis_summary_rows(context, level, 4)
    if axis_rows:
        parts.extend(["", "<b>투자 판단 근거</b>", *axis_rows])
    opinion_rows = strategy_guide_rows(context, response, level)
    if opinion_rows:
        parts.extend(["", "<b>전략 가이드</b>", *opinion_rows])
    data_state_rows = customer_data_state_rows(context, level)
    if data_state_rows:
        parts.extend(["", "<b>자료 상태와 부족 데이터</b>", *data_state_rows])
    if response.source_urls:
        parts.extend(["", "<b>출처</b>"])
        parts.extend(source_url_rows(response.source_urls, context))
    parts.extend(execution_footer(context, response, reference, sent))
    return "\n".join(part for part in parts if str(part).strip() or part == "").strip()

def execution_telegram_message_absolute_beginner(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    return execution_telegram_message_compact_beginner(context, response, "absoluteBeginner")


def execution_telegram_message_compact_beginner(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
    level: str,
) -> str:
    headline = execution_headline(context, response)
    target = str(context.get("displayTarget") or context.get("target") or "").strip()
    sent = str(context.get("sentTime") or "").strip()
    reference = response.reference_date or reference_date(context)
    parts = [
        "<b>" + html.escape(headline, quote=False) + "</b>",
        ("<code>" + html.escape(target, quote=False) + "</code>") if target else "",
        "",
        "<b>지금 행동</b>",
        _html_bullet(
            "[AI] "
            + (action_label_for_action(response.action, context) or response.action_label)
            + (". " + compact_sentence_count(response.summary, 1) if str(response.summary or "").strip() else ""),
            level,
        ),
    ]
    transition = compact_decision_transition(context, response)
    if transition:
        parts.extend(["", "<b>이번 변화</b>", _html_bullet(transition, level)])
    flow_rows = compact_current_flow_rows(context)
    if flow_rows:
        parts.extend(["", "<b>현재 흐름</b>", *[_html_bullet(row, level) for row in flow_rows]])
    reasons = compact_action_reason_rows(context, response)
    if reasons:
        parts.extend(["", "<b>바뀐 이유</b>", *[_html_bullet(item, level) for item in reasons]])
    next_action = compact_next_action_line(context, response)
    if next_action:
        parts.extend(["", "<b>다음 행동</b>", _html_bullet(next_action, level)])
    invalidation = compact_invalidation_line(context, response)
    if invalidation:
        parts.extend(["", "<b>판단 변경 조건</b>", _html_bullet(invalidation, level)])
    data_line = compact_data_status_line(context, response)
    if data_line:
        parts.extend(["", "<b>자료 상태</b>", _html_bullet(data_line, level)])
    news_line = compact_news_impact_line(context)
    if news_line:
        parts.extend(["", "<b>뉴스 영향</b>", _html_bullet(news_line, level)])
    footer = " · ".join(part for part in ["기준 " + str(reference) if reference else "", "발송 " + sent if sent else ""] if part)
    if footer:
        parts.extend(["", "<i>" + html.escape(footer, quote=False) + "</i>"])
    return "\n".join(part for part in parts if str(part).strip() or part == "").strip()


def compact_decision_transition(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    presentation = decision_transition_presentation(context, response.action)
    if presentation.get("summary"):
        return "[" + presentation.get("label", "판단 변경") + "] " + presentation["summary"]
    transition = decision_transition_from_context(context)
    previous = str(transition.get("previousAction") or "").strip().upper()
    current = str(transition.get("currentAction") or response.action or "").strip().upper()
    if previous and current and previous != current:
        return action_label_for_action(previous, context) + "에서 " + action_label_for_action(current, context) + "으로 바뀌었습니다."
    previous_status = str(transition.get("previousStatus") or "").strip()
    current_status = action_envelope_status_from_transition(transition)
    if current_status:
        current_label = action_envelope_status_label(current_status)
        previous_label = action_envelope_status_label(previous_status)
        if previous_label and previous_label != current_label:
            return previous_label + "에서 " + current_label + "로 바뀌었습니다."
        if current_label:
            return "새로 확인된 조건: " + current_label + "."
    summary = compact_sentence_count(customer_visible_ai_text(transition.get("summary") or ""), 1)
    if summary and not compact_reason_is_internal(summary):
        return summary
    envelope = relation_context_value(context).get("actionEnvelope") if isinstance(relation_context_value(context), dict) else {}
    label = str(envelope.get("statusLabel") or "").strip() if isinstance(envelope, dict) else ""
    return label or "새 판단 조건을 확인했습니다."


def _market_signal_stage(context: Dict[str, object], stage: str) -> Dict[str, object]:
    facts = relation_facts(context or {})
    coverage = facts.get("marketSignalCoverage") if isinstance(facts.get("marketSignalCoverage"), dict) else {}
    payload = coverage.get(stage) if isinstance(coverage.get(stage), dict) else {}
    return dict(payload or {})


def _market_signal_stage_visible(context: Dict[str, object], stage: str) -> bool:
    payload = _market_signal_stage(context, stage)
    if not payload:
        # Legacy contexts do not retain per-stage freshness metadata. Their
        # raw lines were already sanitized by the dispatch freshness gate.
        return True
    status = str(payload.get("status") or "").strip().lower()
    if status and status not in {"available", "partial", "proxy"}:
        return False
    return payload.get("judgementEvidenceUsable") is not False


def _market_signal_basis_label(context: Dict[str, object], stage: str) -> str:
    payload = _market_signal_stage(context, stage)
    if not payload:
        return ""
    freshness = str(payload.get("freshnessStatus") or "").strip().lower()
    latency = str(payload.get("latencyStatus") or "").strip().lower()
    if stage == "investor" and (
        payload.get("realTime") is False
        or freshness in {"reference-only", "reference-repeat"}
        or latency in {"delayed-or-batched", "unchanged-repeat"}
        or _number(payload.get("unchangedCount")) > 0
    ):
        return "당일 누적 참고"
    if freshness == "last-close" or latency == "market-closed-reference":
        return "최근 마감 기준"
    return ""


def _market_signal_exclusion_note(context: Dict[str, object], stage: str) -> str:
    payload = _market_signal_stage(context, stage)
    if not payload or _market_signal_stage_visible(context, stage):
        return ""
    status = str(payload.get("status") or "").strip().lower()
    if status in {"stale", "stale-at-dispatch"}:
        return "최신값이 아니어서 이번 판단에서는 제외"
    if status in {"unavailable", "missing", "empty"}:
        session = str(payload.get("marketSession") or "").strip().lower()
        if session in {"pre_open", "post_close", "closed"}:
            return "정규장 체결값이 없어 이번 판단에서는 제외"
        return "확인 가능한 값이 없어 이번 판단에서는 제외"
    return ""


def _compact_trend_from_facts(context: Dict[str, object]) -> str:
    facts = relation_facts(context or {})
    current = _number(facts.get("currentPrice"))
    currency = str(facts.get("currency") or "KRW")
    rows = []
    for period in [5, 20, 60]:
        average = _number(facts.get("ma" + str(period)))
        if average <= 0:
            continue
        distance_key = "ma" + str(period) + "Distance"
        distance = _number(facts.get(distance_key))
        if distance == 0 and current:
            distance = ((current / average) - 1) * 100
        direction = "높음" if distance >= 0 else "낮음"
        rows.append(
            str(period)
            + "일선 "
            + price_money(average, currency)
            + "보다 "
            + str(abs(round(distance, 1)))
            + "% "
            + direction
        )
    return ", ".join(rows)


def _compact_investor_fact(label: str, prefix: str, facts: Dict[str, object]) -> str:
    buy = _number(facts.get(prefix + "BuyVolume"))
    sell = _number(facts.get(prefix + "SellVolume"))
    reported_net = _number(facts.get(prefix + "NetVolume"))
    net = buy - sell if buy or sell else reported_net
    if not any([buy, sell, net]):
        return ""
    direction = "순매수" if net > 0 else "순매도" if net < 0 else "매수·매도 균형"
    if net:
        return label + " " + direction + " " + compact_number(abs(net)) + "주"
    return label + " " + direction


def _compact_investor_rows_from_raw_lines(context: Dict[str, object]) -> List[str]:
    rows = []
    for label in ["외국인", "기관"]:
        value = ""
        for line in _raw_lines(context):
            text = str(line or "").strip()
            if not (text.startswith(label) or text.startswith("투자자")):
                continue
            direction_match = re.search(r"(?:^|[,:\s])" + re.escape(label) + r"\s*:?\s*(순매수|순매도)\s*([+\-]?\s*[0-9][0-9,\.]*)\s*주?", text)
            if direction_match:
                value = label + " " + direction_match.group(1) + " " + re.sub(r"\s+", "", direction_match.group(2)) + "주"
                break
            signed_match = re.search(r"(?:^|[,:\s])" + re.escape(label) + r"\s*:?\s*([+\-])\s*([0-9][0-9,\.]*)", text)
            if signed_match:
                direction = "순매수" if signed_match.group(1) == "+" else "순매도"
                value = label + " " + direction + " " + signed_match.group(2) + "주"
                break
        if value:
            rows.append(value)
    return rows


def compact_investor_flow_line(context: Dict[str, object]) -> str:
    if not _market_signal_stage_visible(context, "investor"):
        return ""
    facts = relation_facts(context or {})
    rows = [
        _compact_investor_fact("외국인", "foreign", facts),
        _compact_investor_fact("기관", "institution", facts),
    ]
    rows = [row for row in rows if row]
    if not rows:
        rows = _compact_investor_rows_from_raw_lines(context)
    if not rows:
        return ""
    basis = _market_signal_basis_label(context, "investor")
    return " · ".join(rows) + ((" (" + basis + ")") if basis else "")


def _compact_execution_from_raw_flow(flow: str) -> List[str]:
    parts = []
    strength = re.search(r"체결강도\s*([0-9][0-9,\.]*)", str(flow or ""))
    if strength:
        value = _number(strength.group(1).replace(",", ""))
        label = trade_strength_label(value)
        parts.append("체결강도 " + compact_number(value) + (("(" + label + ")") if label else ""))
    buy = re.search(r"매수\s*체결(?:량)?\s*([0-9][0-9,\.]*)\s*주?", str(flow or ""))
    sell = re.search(r"매도\s*체결(?:량)?\s*([0-9][0-9,\.]*)\s*주?", str(flow or ""))
    if buy and sell:
        parts.append("매수 체결 " + buy.group(1) + "주 / 매도 체결 " + sell.group(1) + "주")
    return parts


def compact_execution_flow_line(context: Dict[str, object]) -> str:
    if not _market_signal_stage_visible(context, "ccnl"):
        return ""
    facts = relation_facts(context or {})
    strength = _number(facts.get("tradeStrength"))
    buy = _number(facts.get("buyVolume"))
    sell = _number(facts.get("sellVolume"))
    parts = []
    if strength > 0:
        label = trade_strength_label(strength)
        parts.append("체결강도 " + compact_number(strength) + (("(" + label + ")") if label else ""))
    if buy > 0 and sell > 0:
        parts.append("매수 체결 " + compact_number(buy) + "주 / 매도 체결 " + compact_number(sell) + "주")
    if not parts:
        parts = _compact_execution_from_raw_flow(_plain_value(context, "수급"))
    if not parts:
        return ""
    basis = _market_signal_basis_label(context, "ccnl")
    return " · ".join(parts) + ((" (" + basis + ")") if basis else "")


def compact_current_flow_rows(context: Dict[str, object]) -> List[str]:
    current = _plain_value(context, "현재가")
    pnl = _plain_value(context, "수익률") or _plain_value(context, "손익")
    trend = _plain_value(context, "추세") or _compact_trend_from_facts(context)
    rows = []
    if current:
        rows.append("현재가 " + current)
    if pnl:
        rows.append("수익률 " + pnl)
    if trend:
        rows.append("가격 흐름: " + compact_sentence_count(trend, 1))
    investor = compact_investor_flow_line(context)
    if investor:
        rows.append("투자자 수급: " + investor)
    else:
        investor_exclusion = _market_signal_exclusion_note(context, "investor")
        if investor_exclusion:
            rows.append("투자자 수급: " + investor_exclusion)
    execution = compact_execution_flow_line(context)
    if execution:
        rows.append("체결 흐름: " + execution)
    else:
        execution_exclusion = _market_signal_exclusion_note(context, "ccnl")
        if execution_exclusion:
            rows.append("체결 흐름: " + execution_exclusion)
    return rows


def compact_current_flow_line(context: Dict[str, object]) -> str:
    """Compatibility summary for callers that still expect one compact row."""

    return " · ".join(compact_current_flow_rows(context)[:2])


def compact_action_reason_rows(
    context: Dict[str, object],
    response: NotificationAIValidatedResponse,
) -> List[str]:
    rows: List[str] = []
    summary = compact_sentence_count(_message_text(response.summary, "beginner"), 1)
    summary_key = re.sub(r"[^0-9a-z가-힣]+", "", summary.casefold())
    envelope_rows = compact_envelope_reason_rows(context)
    relation_context = relation_context_value(context)
    envelope = relation_context.get("actionEnvelope") if isinstance(relation_context.get("actionEnvelope"), dict) else {}
    status = str(envelope.get("status") or "").strip().upper()
    if status == "ENTRY_OBSERVING" and "진입 근거" in summary and "아직" in summary:
        envelope_rows = [row for row in envelope_rows if "진입 근거" not in row]
    elif status == "ENTRY_DEFERRED" and "진입" in summary and "조건" in summary:
        envelope_rows = [row for row in envelope_rows if "진입" not in row]
    elif status in {"ENTRY_BLOCKED", "JUDGEMENT_BLOCKED"} and ("보류" in summary or "필수 자료" in summary):
        envelope_rows = [row for row in envelope_rows if "판단을 보류" not in row]
    # Keep one sentence that explains the allowed action range, then prefer a
    # concrete market fact over a second generic envelope sentence. This makes
    # the compact alert explain *why now* without exposing the rule payload.
    values = (
        envelope_rows[:1]
        + compact_effect_driver_rows(context)
        + list(response.evidence or [])
        + list(relation_axis_summary_lines(context, 5))
        + envelope_rows[1:]
    )
    for item in values:
        text = compact_sentence_count(_message_text(customer_visible_ai_text(item), "beginner"), 1)
        if compact_reason_is_internal(text):
            continue
        normalized = re.sub(r"[^0-9a-z가-힣]+", "", text.casefold())
        if summary_key and normalized and (normalized in summary_key or summary_key in normalized):
            continue
        if text and not any(normalized and (normalized in existing or existing in normalized) for existing in [re.sub(r"[^0-9a-z가-힣]+", "", row.casefold()) for row in rows]):
            rows.append(text)
        if len(rows) >= 2:
            break
    return rows


def compact_envelope_reason_rows(context: Dict[str, object]) -> List[str]:
    relation_context = relation_context_value(context)
    envelope = relation_context.get("actionEnvelope") if isinstance(relation_context.get("actionEnvelope"), dict) else {}
    effect = str(envelope.get("selectedDecisionEffect") or "").strip().lower()
    status = str(envelope.get("status") or "").strip().upper()
    if status == "ENTRY_OBSERVING":
        rows = ["매수로 바꿀 진입 근거는 아직 충분하지 않습니다."]
        if effect == "constrain":
            rows.append("현재 제약 조건은 진입 시점과 금액을 보수적으로 보게 합니다.")
        return rows
    if status == "ENTRY_DEFERRED":
        return ["진입을 뒷받침하는 근거는 있지만, 추가 확인 조건이 남아 있습니다."]
    if status in {"ENTRY_BLOCKED", "JUDGEMENT_BLOCKED"}:
        return ["필수 자료나 반대 조건 때문에 지금은 판단을 보류합니다."]
    if effect == "support":
        return ["현재 행동을 뒷받침하는 근거가 확인됐습니다."]
    if effect == "defer":
        return ["추가 확인 조건이 남아 있어 지금 바로 행동을 바꾸지 않습니다."]
    if effect == "constrain":
        return ["현재 제약 조건은 행동의 시점과 금액을 보수적으로 보게 합니다."]
    if effect == "block":
        return ["필수 자료나 반대 조건 때문에 지금은 판단을 보류합니다."]
    return []


def compact_effect_driver_rows(context: Dict[str, object]) -> List[str]:
    relation_context = relation_context_value(context)
    envelope = relation_context.get("actionEnvelope") if isinstance(relation_context.get("actionEnvelope"), dict) else {}
    effect = str(envelope.get("selectedDecisionEffect") or "").strip().lower()
    categories = {
        "support": ["trend", "news", "valuation", "flow"],
        "defer": ["news", "disclosure", "trend", "flow"],
        "constrain": ["macro", "fx", "rate", "market", "flow"],
        "block": ["dataQuality"],
    }.get(effect, ["trend", "macro", "news", "flow"])
    execution_plan = relation_context.get("executionPlan") if isinstance(relation_context.get("executionPlan"), dict) else {}
    drivers = execution_plan.get("decisionDrivers") if isinstance(execution_plan.get("decisionDrivers"), list) else []
    for category in categories:
        for driver in drivers:
            if not isinstance(driver, dict) or str(driver.get("category") or "") != category:
                continue
            text = customer_visible_ai_text(driver.get("summary") or driver.get("text") or driver.get("label") or "")
            if text and not compact_reason_is_internal(text):
                return [text]
    return []


def compact_reason_is_internal(value: object) -> bool:
    text = str(value or "").lower()
    return any(token in text for token in [
        "typedb", "rulebox", "inferencebox", "관계 분석 규칙", "관계 분석 실행 계획",
        "관계가 새로 감지", "관계 신호 관계",
        "supportingevidenceid", "counterevidenceid", "reviewedsupportingevidenceid",
        "reviewedcounterevidenceid", "causalpathid", "relation-evidence", "relation_근거",
        "relation-근거", "changedevidencecount", "reasoningrefreshed",
    ])


def compact_next_action_line(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    value = compact_sentence_count(
        watchlist_friendly_text(context, _derived_execution_criteria(context, response)),
        1,
    )
    if value:
        return value
    if response.next_checks:
        return compact_sentence_count(watchlist_friendly_text(context, response.next_checks[0]), 1)
    return "다음 데이터 업데이트에서 현재 조건이 유지되는지 확인합니다."


def compact_invalidation_line(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    value = compact_sentence_count(_derived_invalidation_condition(context, response), 1)
    return value or "현재 근거가 사라지거나 반대 근거가 새로 확인되면 이 판단을 다시 봅니다."


def compact_data_status_line(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    state = relation_state_values(context)
    label = customer_visible_ai_text(state.get("dataLabel") or response.data_state_label or "")
    missing = customer_data_note_rows(list(response.missing_data_impact))
    if missing:
        return label + ". " + " / ".join(missing[:1])
    return label


def compact_news_impact_line(context: Dict[str, object]) -> str:
    impact = context.get("newsImpact") if isinstance(context.get("newsImpact"), dict) else {}
    if not impact:
        relation = relation_context_value(context)
        impact = relation.get("newsImpact") if isinstance(relation.get("newsImpact"), dict) else {}
    if (
        not impact
        or not impact.get("decisionChanging")
        or impact.get("decisionInlineEligible") is not True
        or impact.get("decisionDriverConfirmed") is not True
    ):
        return ""
    headline = customer_visible_ai_text(impact.get("headline") or impact.get("summary") or "")
    source = customer_visible_ai_text(impact.get("source") or "")
    prefix = (source + ": ") if source else ""
    return prefix + compact_sentence_count(headline, 1)

def beginner_current_state_rows(context: Dict[str, object]) -> List[str]:
    values = [
        ("현재가", _plain_value(context, "현재가")),
        ("평균매입가", _plain_value(context, "평균매입가") or _plain_value(context, "평단가")),
        ("수익률", _plain_value(context, "수익률") or _plain_value(context, "손익")),
        ("보유 수량", _plain_value(context, "보유 수량")),
        ("종목 평가금액", _plain_value(context, "종목 평가금액") or _plain_value(context, "평가금액")),
        ("계좌 평가금액", _plain_value(context, "계좌 평가금액")),
        ("가격 흐름", _plain_value(context, "추세")),
        ("거래량·매수매도", _plain_value(context, "수급")),
    ]
    rows = [row for row in [_html_row(label, value, True) for label, value in values] if row]
    rows.extend(_html_multiline_rows("투자자", _investor_text(context)))
    return rows

def relation_rule_summary(context: Dict[str, object], limit: int = 4) -> List[str]:
    relation_context = relation_context_value(context)
    if not isinstance(relation_context, dict):
        return []
    matches = relation_context.get("matchedRules") or relation_context.get("activeRules") or relation_context.get("rules") or []
    rows = []
    if isinstance(matches, list):
        for item in matches:
            if isinstance(item, dict):
                label = str(item.get("label") or item.get("name") or item.get("rule") or item.get("ruleId") or "").strip()
                if label:
                    rows.append(label)
            elif str(item or "").strip():
                rows.append(str(item).strip())
            if len(rows) >= limit:
                break
    return rows
