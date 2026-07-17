import html
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 compatibility guard.
    ZoneInfo = None

from ..domain.accounts import investment_strategy_profile, message_delivery_profile
from ..domain.alert_formatting import price_money, signed_pct
from ..domain.notification_ai import criterion_lines, notification_ai_prompt_context, relation_context_value
from ..domain.notification_ai_context import relation_facts
from ..domain.notification_ai_context import is_watchlist_context
from ..domain.external_api_sources import external_api_source_line
from ..domain.notification_ai_gate_contracts import ACTION_LABELS, MESSAGE_START_BADGE, NotificationAIValidatedResponse
from ..domain.notification_ai_gate_sources import source_detail_text, source_url_rows
from ..domain.notification_ai_gate_text import (
    _clamp,
    _line_after_colon,
    _number,
    _raw_lines,
    _text,
    append_unique_text,
    reference_date,
)
from ..domain.notification_ai_gate_validation import (
    _driver_rows,
    action_label_for_target,
    delivery_level_from_context,
    signed_percent_from_text,
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
DATA_COLLECTION_SOURCE_AS_OF_CONFIDENCE_LABELS = {
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
    ("관계 강도", "확인 필요 강도"),
    ("관계 점수", "확인 필요 점수"),
    ("관계 신호", "연결된 근거 신호"),
    ("RuleBox", "관계 분석 규칙"),
    ("InferenceBox", "관계 분석 결과"),
    ("actionGroup", "판단 묶음"),
    ("actionLevel", "판단 단계"),
    ("signalStrength", "관계 점수"),
    ("confidence", "신뢰도"),
    ("팩터 노출", "영향받는 요인"),
    ("익스포저", "쏠림 정도"),
    ("슬리피지", "원하는 가격과 실제 거래 가격 차이"),
    ("리스크", "위험"),
]

BEGINNER_TERM_HINTS = [
    ("관계 강도", "여러 근거가 같은 방향인지 보는 확인 필요 점수"),
    ("관계 점수", "여러 근거가 같은 방향인지 보는 확인 필요 점수"),
    ("관계 신호", "가격·뉴스·보유 상태를 연결해서 본 신호"),
    ("벤치마크 베타", "시장과 같이 움직이는 정도"),
    ("실행 가능 용량", "지금 주문해도 무리가 없는지"),
    ("실행 차단", "지금 바로 주문하기 어려운 조건"),
    ("슬리피지", "원하는 가격과 실제 거래 가격 차이"),
    ("RuleBox", "관계 분석 규칙"),
    ("InferenceBox", "관계 분석 결과"),
]

INTERMEDIATE_TERM_HINTS = [
    ("관계 강도", "대응 필요 강도"),
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
    text = str(value or "")
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
        text = _text(item, 0)
        if not text:
            continue
        lowered = text.lower()
        if any(term.lower() in lowered for term in CUSTOMER_HIDDEN_DATA_NOTE_TERMS):
            continue
        append_unique_text(rows, text, 0)
    return rows

def notification_topline_change_summary(context: Dict[str, object]) -> str:
    context = context or {}
    reason = str(context.get("honeyStateReason") or context.get("honeySimilarityBypassReason") or "").strip()
    source_types = " ".join(str(item or "") for item in (context.get("sourceSignalTypes") or []))
    profit_loss_summary = "" if is_watchlist_context(context) else _profit_loss_change_summary(context, reason)
    if profit_loss_summary:
        return profit_loss_summary
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
    if "관계 강도 변화" in reason:
        return "관계 강도 변화"
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
        if "관계 점수" in reason_summary:
            return "관계 점수 상승"
        return _clean_reason_text(reason_summary, 18)
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

def confidence_text(value: object) -> str:
    score = _clamp(_number(value), 0, 100)
    if score >= 85:
        label = "높음"
    elif score >= 65:
        label = "보통"
    else:
        label = "낮음"
    return label + " (" + str(round(score, 1)) + "%)"

def action_label_for_action(action: object, context: Dict[str, object] = None) -> str:
    text = str(action or "").strip().upper()
    if context is not None:
        return action_label_for_target(context, text)
    return ACTION_LABELS.get(text, str(action or "").strip())

def ai_confidence_display(response: NotificationAIValidatedResponse, level: str) -> str:
    if level in {"absoluteBeginner", "beginner"}:
        return confidence_text(response.confidence)
    return str(round(response.confidence, 1)) + "%"

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
    rows = [
        _html_row(ai_action_row_label(level), _ai_marked_value(action_label_for_action(response.action, context) or response.action_label), level=level),
        _html_row("판단 강도", _ai_marked_value(ai_confidence_display(response, level)), level=level),
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
    headline = str(context.get("headline") or context.get("title") or "알림").strip()
    prefix = title_prefix_from_headline(headline)
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
    return []

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
        source_as_of_confidence: object = "",
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
        confidence_label = DATA_COLLECTION_SOURCE_AS_OF_CONFIDENCE_LABELS.get(str(source_as_of_confidence or "").strip().lower(), str(source_as_of_confidence or "").strip())
        if confidence_label:
            parts.append("기준 " + confidence_label)
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
            source_as_of_confidence=freshness.get("sourceAsOfConfidence"),
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
                source_as_of_confidence=value.get("sourceAsOfConfidence"),
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


def _point_text(value: object) -> str:
    number = _number(value)
    if not number:
        return ""
    if float(number).is_integer():
        return str(int(number))
    return ("%.1f" % number).rstrip("0").rstrip(".")

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

def _rule_score(item: Dict[str, object]) -> float:
    if not isinstance(item, dict):
        return 0.0
    return _number(item.get("strengthScore") or item.get("strength_score") or item.get("score") or item.get("relationScore"))

def _top_relation_rule_reasons(relation_context: Dict[str, object], limit: int = 2) -> List[str]:
    rules = relation_context.get("activeRules") or relation_context.get("matchedRules") or []
    rows: List[str] = []
    ranked = sorted(
        [item for item in rules if isinstance(item, dict) and not item.get("referenceOnly") and not item.get("reference_only")],
        key=_rule_score,
        reverse=True,
    )
    for item in ranked:
        label = _clean_reason_text(item.get("label") or item.get("ruleId") or item.get("rule_id"), 70)
        score = _point_text(_rule_score(item))
        if label:
            rows.append(label + ((" " + score + "점") if score else ""))
        if len(rows) >= limit:
            break
    return rows

def _relation_score_reason(context: Dict[str, object]) -> str:
    relation_context = relation_context_value(context)
    if not relation_context:
        return ""
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    score = _number(
        decision.get("score")
        or relation_context.get("signalStrength")
        or relation_context.get("score")
    )
    if not score:
        rules = relation_context.get("activeRules") or relation_context.get("matchedRules") or []
        score = max([_rule_score(item) for item in rules if isinstance(item, dict)], default=0.0)
    if not score:
        return ""
    previous = _number(
        decision.get("previousScore")
        or decision.get("previousRelationScore")
        or relation_context.get("previousScore")
        or relation_context.get("previousRelationScore")
    )
    label = _clean_reason_text(
        decision.get("label")
        or decision.get("actionLabel")
        or decision.get("action")
        or relation_context.get("decisionLabel"),
        64,
    )
    if previous and score > previous:
        score_text = "관계 점수 " + _point_text(previous) + "점→" + _point_text(score) + "점"
    else:
        score_text = "관계 점수 " + _point_text(score) + "점까지 상승"
    stage = "주의 기준" if score >= 85 else "실행 기준" if score >= 70 else "관찰 기준"
    parts = [score_text + "해 " + stage + "을 넘었습니다"]
    if label:
        parts.append(label)
    reasons = _top_relation_rule_reasons(relation_context)
    if reasons:
        parts.append("주요 요인: " + ", ".join(reasons))
    return " · ".join(parts)

def _score_text_reason(context: Dict[str, object]) -> str:
    lines = [*criterion_lines(context), *_raw_lines(context)]
    for line in lines:
        text = _clean_reason_text(line, 130)
        if "점" not in text:
            continue
        if "설정" in str(line or "") and "감지" not in str(line or ""):
            continue
        score_match = re.search(r"(\d+(?:\.\d+)?)점", text)
        if not score_match:
            continue
        if any(token in text for token in ["상태", "판단", "점수", "관계", "강도", "감지"]):
            return text + "까지 올라 알림 기준을 넘었습니다."
    return ""

def _threshold_reason(context: Dict[str, object]) -> str:
    criteria = criterion_lines(context)
    if not criteria:
        return ""
    detected = _criterion_value(criteria, "감지")
    setting = _criterion_value(criteria, "설정")
    if detected and setting:
        return "감지값 " + _clean_reason_text(detected, 90) + "이 기준(" + _clean_reason_text(setting, 70) + ")을 넘었습니다."
    if detected:
        return "감지값 " + _clean_reason_text(detected, 120) + " 때문에 알림이 발생했습니다."
    if setting:
        return _clean_reason_text(setting, 120)
    return _clean_reason_text(criteria[0], 120) if criteria else ""

def notification_reason_summary(context: Dict[str, object]) -> str:
    return (
        _relation_score_reason(context)
        or _score_text_reason(context)
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
    if context.get("honeyStateSuppressed"):
        return ""
    decision = str(context.get("honeyStateDecision") or "").strip()
    if not decision or decision == "cooldown":
        return ""
    cooldown_enabled = bool(context.get("honeyStateCooldownEnabled"))
    reason = _human_readable_cooldown_reason(context.get("honeyStateReason") or context.get("honeySimilarityBypassReason"))
    if not cooldown_enabled and not reason:
        return ""
    age = _number(context.get("honeyStateLastSentAgeMinutes"))
    cooldown = _number(context.get("honeyStateCooldownMinutes"))
    age_text = _minute_count_text(age)
    cooldown_text = _minute_count_text(cooldown)
    before_cooldown = bool(age_text and cooldown_text and age < cooldown)
    if decision == "new_threshold":
        if age_text and cooldown_text:
            return "현재 조건 조합이 처음 감지되어 기본 쿨다운 " + cooldown_text + "과 별개로 보냈습니다."
        return "현재 조건 조합이 처음 감지되어 반복 제한 없이 보냈습니다."
    if decision == "sustained_summary":
        if age_text and cooldown_text:
            return "마지막 발송 후 " + age_text + "이 지나 기본 쿨다운 " + cooldown_text + "을 충족했습니다."
        return reason or "지속 상태 요약 기준을 충족해 다시 보냈습니다."
    if decision in {"material_change", "mandatory_profit_loss_band"}:
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


def _source_alert_events(context: Dict[str, object]) -> List[Dict[str, object]]:
    context = context or {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    raw = context.get("sourceAlertEvents") or metadata.get("sourceAlertEvents") or []
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _first_crypto_source_event(context: Dict[str, object]) -> Dict[str, object]:
    for item in _source_alert_events(context):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        text = " ".join([
            str(item.get("rule") or ""),
            str(item.get("key") or ""),
            str(item.get("title") or ""),
            str(item.get("symbol") or ""),
            str(metadata.get("cryptoId") or ""),
            str(metadata.get("market") or ""),
        ]).casefold()
        if "externalcryptomove" in text or "crypto" in text or metadata.get("cryptoMoveModel"):
            return item
    if str((context or {}).get("messageType") or (context or {}).get("rule") or "") == "externalCryptoMove":
        context_metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        merged_metadata = {**context_metadata, **context}
        if isinstance(context_metadata.get("cryptoMoveModel"), dict) and not isinstance(merged_metadata.get("cryptoMoveModel"), dict):
            merged_metadata["cryptoMoveModel"] = context_metadata.get("cryptoMoveModel")
        return {
            "rule": "externalCryptoMove",
            "symbol": context.get("symbol") or context.get("rawSymbol"),
            "lines": _raw_lines(context),
            "criteria": criterion_lines(context),
            "metadata": merged_metadata,
        }
    return {}


def _crypto_line_value(lines: List[str], label: str) -> str:
    prefix = str(label or "").strip()
    for line in lines or []:
        text = str(line or "").strip().lstrip("-• ").strip()
        if text.startswith(prefix):
            return text[len(prefix):].strip(" :")
    return ""


def _crypto_signed_pct(value: object) -> str:
    if value in (None, ""):
        return ""
    return signed_pct(_number(value))


def _crypto_trigger_summary_lines(context: Dict[str, object], limit: int = 3) -> List[str]:
    source = _first_crypto_source_event(context)
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    model = metadata.get("cryptoMoveModel") if isinstance(metadata.get("cryptoMoveModel"), dict) else {}
    lines = [str(item or "").strip() for item in (source.get("lines") if isinstance(source.get("lines"), list) else []) if str(item or "").strip()]
    criteria = [str(item or "").strip() for item in (source.get("criteria") if isinstance(source.get("criteria"), list) else []) if str(item or "").strip()]
    if not source and not model and not lines:
        return []

    symbol = str(source.get("symbol") or metadata.get("symbol") or context.get("symbol") or context.get("rawSymbol") or "").upper().strip()
    asset = str(model.get("assetLabel") or metadata.get("cryptoMoveAssetLabel") or "").strip()
    for line in lines:
        match = re.search(r"^(.+?)\s+변동\s+24h", line)
        if match:
            asset = asset or match.group(1).strip()
            break
    if not asset:
        target = str(context.get("displayTarget") or context.get("target") or "").strip()
        parts = [part.strip() for part in target.split("/") if part.strip()]
        asset = parts[-2] if len(parts) >= 2 and parts[-1].upper() in {"BTC", "ETH"} else (symbol or "크립토")

    change24h = metadata.get("change24h")
    change7d = metadata.get("change7d")
    for line in lines:
        match = re.search(r"24h\s*([+-]?\d+(?:\.\d+)?)%\s*[·,]\s*7d\s*([+-]?\d+(?:\.\d+)?)%", line)
        if match:
            change24h = change24h if change24h not in (None, "") else match.group(1)
            change7d = change7d if change7d not in (None, "") else match.group(2)
            break

    dominant_period = str(model.get("dominantPeriodLabel") or metadata.get("cryptoMoveDominantPeriod") or "").strip()
    dominant_change = model.get("dominantChange", metadata.get("cryptoMoveDominantChange"))
    if dominant_change in (None, ""):
        dominant_change = change7d if dominant_period == "7일" else change24h if dominant_period in {"24시간", "24h"} else None
    def clean_criterion_value(value: str) -> str:
        text = str(value or "").strip()
        text = re.sub(r"^(발송\s*기준\s*)?(설정|감지)\s*:\s*", "", text).strip()
        return text

    setting = ""
    detected = ""
    for line in criteria:
        text = str(line or "").strip()
        if not setting and ("설정:" in text or "설정：" in text):
            setting = clean_criterion_value(text.split(":", 1)[1] if ":" in text else text.split("：", 1)[1])
        if not detected and ("감지:" in text or "감지：" in text):
            detected = clean_criterion_value(text.split(":", 1)[1] if ":" in text else text.split("：", 1)[1])

    rows: List[str] = []
    if dominant_period or dominant_change not in (None, ""):
        dominant_text = " ".join(part for part in [dominant_period, _crypto_signed_pct(dominant_change)] if part)
        threshold_text = (" 기준(" + setting + ")" if setting else "")
        rows.append("알림 발생 이유: " + asset + " " + dominant_text + " 변동이" + threshold_text + "을 넘었습니다.")
    elif detected:
        rows.append("알림 발생 이유: " + detected + ("이 기준(" + setting + ")을 넘었습니다." if setting else " 때문에 감지됐습니다."))
    if change24h not in (None, "") or change7d not in (None, ""):
        rows.append("크립토 변동: 24시간 " + (_crypto_signed_pct(change24h) or "-") + ", 7일 " + (_crypto_signed_pct(change7d) or "-"))

    price = metadata.get("price")
    volume = metadata.get("volume24h")
    price_text = _crypto_line_value(lines, "크립토 가격") or (price_money(_number(price), "USD") if price not in (None, "") else "")
    volume_text = _crypto_line_value(lines, "크립토 거래액") or (price_money(_number(volume), "USD") if volume not in (None, "") else "")
    provider = metadata.get("provider") or _crypto_line_value(lines, "출처")
    detail_parts = []
    if price_text:
        detail_parts.append("가격 " + price_text)
    if volume_text:
        detail_parts.append("24시간 거래액 " + volume_text)
    if provider:
        detail_parts.append("출처 " + str(provider))
    if detail_parts:
        rows.append("확인 데이터: " + ", ".join(detail_parts))
    return rows[:limit]


def _price_position_summary(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    current = _plain_value(context, "현재가")
    average = _plain_value(context, "평균매입가") or _plain_value(context, "평단가")
    pnl = _plain_value(context, "수익률") or _plain_value(context, "손익")
    trend = _plain_value(context, "추세")
    if not any([current, average, pnl, trend]):
        return ""
    if is_watchlist_context(context):
        if response.action in {"BUY", "ADD"}:
            base = "관심종목의 진입 조건이 일부 잡혔지만 작게 확인하는 단계입니다."
        elif response.action in {"TRIM", "SELL", "AVOID"}:
            base = "관심종목은 보유 물량이 아니므로 팔기보다 새로 들어갈지 말지를 판단합니다."
        else:
            base = "관심종목은 보유 판단이 아니라 진입 조건을 계속 지켜보는 단계입니다."
    elif response.action in {"TRIM", "SELL"} and signed_percent_from_text(pnl) > 0:
        base = "아직 수익 구간이지만 가격 흐름이 약해져 수익 보호 쪽으로 봅니다."
    elif response.action in {"TRIM", "SELL"}:
        base = "손실 또는 가격 약화가 커져 비중 관리 쪽으로 봅니다."
    elif response.action in {"BUY", "ADD"}:
        base = "가격 회복 조건이 일부 잡혀 진입 조건을 봅니다."
    else:
        base = "바로 실행보다 다음 조건 확인이 먼저입니다."
    details = []
    if pnl:
        details.append("수익률 " + pnl)
    if trend:
        details.append(trend)
    return _text(base + (" " + " / ".join(details[:2]) if details else ""), 210)

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
    joined = " / ".join(titles)
    if _contains_any(joined, ["sell", "sells", "sold", "매도", "처분", "disposal"]):
        return "뉴스·공시에서 보유자산 매각/처분 성격의 이벤트가 보여 원문 확인 우선입니다: " + joined
    if _contains_any(joined, ["rise", "rises", "상승", "defend", "호재", "beat"]):
        return "뉴스에는 반대 방향 신호도 있어 가격 반응 확인이 필요합니다: " + joined
    return "뉴스·공시 확인 대상: " + joined

def context_specific_insight_rows(context: Dict[str, object], response: NotificationAIValidatedResponse, limit: int = MESSAGE_CONTEXT_ROW_LIMIT) -> List[str]:
    rows: List[str] = []
    for item in _crypto_trigger_summary_lines(context, 2):
        append_unique_text(rows, item, 230)
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

def _context_blob(context: Dict[str, object]) -> str:
    values = [
        str(context.get("displayTarget") or context.get("target") or context.get("title") or ""),
        "\n".join(_raw_lines(context)),
        str(relation_facts(context or {})),
    ]
    return "\n".join(values)

def _is_outside_regular_session(context: Dict[str, object]) -> bool:
    blob = _context_blob(context).lower()
    return any(term in blob for term in ["장외", "프리장", "프리마켓", "애프터", "after-hours", "premarket", "pre-market", "정규 거래시간 밖"])

def _volume_ratio_from_context(context: Dict[str, object]) -> float:
    candidates = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*x", _context_blob(context), flags=re.IGNORECASE):
        candidates.append(_number(match.group(1)))
    return min(candidates) if candidates else 0.0

def _has_low_volume_context(context: Dict[str, object]) -> bool:
    ratio = _volume_ratio_from_context(context)
    if ratio and ratio < 0.3:
        return True
    return bool(re.search(r"\b0x\b|0\.0+\s*x", _context_blob(context), flags=re.IGNORECASE))

def _quantity_number(text: object) -> float:
    cleaned = str(text or "").replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    return _number(match.group(1)) if match else 0.0

def _quantity_display(value: float) -> str:
    if not value:
        return ""
    if float(value).is_integer():
        return str(int(value))
    return ("%.2f" % value).rstrip("0").rstrip(".")

def _quantity_range_text(quantity: float, low_ratio: float, high_ratio: float) -> str:
    if quantity <= 0:
        return ""
    if quantity <= 1:
        return _quantity_display(quantity) + "주라 분할 여지가 작아 정규장 확인 후 보유 또는 정리 중 하나를 다시 판단"
    low = max(1, int(round(quantity * low_ratio)))
    high = max(low, int(round(quantity * high_ratio)))
    high = min(int(round(quantity)), high)
    return _quantity_display(quantity) + "주 중 " + str(low) + "~" + str(high) + "주"

def _price_text_from_current(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"([$₩]?\s*\d[\d,]*(?:\.\d+)?)", text)
    return re.sub(r"\s+", "", match.group(1)) if match else text

def _profit_loss_rate_from_context(context: Dict[str, object]) -> float:
    for label in ["수익률", "손익률", "손익"]:
        value = _plain_value(context, label)
        if value:
            rate = signed_percent_from_text(value)
            if rate:
                return rate
    for key in ["profitLossRate", "profit_loss_rate", "pnlRate", "pnlPct", "profitLossPct"]:
        if key in (context or {}):
            return _number(context.get(key))
    return 0.0

def _moving_average_price(context: Dict[str, object], days: int) -> str:
    blob = _plain_value(context, "추세") or _context_blob(context)
    patterns = [
        r"" + str(days) + r"일선\s*([$₩]?\s*\d[\d,]*(?:\.\d+)?)",
        r"" + str(days) + r"일\s*평균(?:\s*가격|가)?\s*([$₩]?\s*\d[\d,]*(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, blob)
        if match:
            return re.sub(r"\s+", "", match.group(1))
    return ""

def _strategy_price_levels(context: Dict[str, object], response: NotificationAIValidatedResponse) -> Dict[str, str]:
    current = _price_text_from_current(_plain_value(context, "현재가"))
    ma5 = _moving_average_price(context, 5)
    ma20 = _moving_average_price(context, 20)
    ma60 = _moving_average_price(context, 60)
    risk = _strategy_guide_value(response, "riskPrice") or current
    recovery = _strategy_guide_value(response, "recoveryPrice") or ma20 or ma5 or ma60
    return {"current": current, "ma5": ma5, "ma20": ma20, "ma60": ma60, "risk": risk, "recovery": recovery}

def _loss_zone(context: Dict[str, object]) -> str:
    rate = _profit_loss_rate_from_context(context)
    if rate <= -20:
        return "large_loss"
    if rate <= -8:
        return "loss_management"
    if rate < 0:
        return "small_loss"
    if rate >= 8:
        return "profit_protection"
    if rate > 0:
        return "small_profit"
    return "flat"

def _holding_action_mode(context: Dict[str, object]) -> str:
    if _is_outside_regular_session(context):
        return "정규장 재확인"
    zone = _loss_zone(context)
    if zone in {"large_loss", "loss_management"}:
        return "보유 유지·손실 방어선 확인"
    if zone == "small_loss":
        return "보유 유지·추가매수 보류"
    if zone == "profit_protection":
        return "보유 유지·이익 보호선 확인"
    return "보유 유지·조건 확인"

def _derived_action_mode(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    explicit = _strategy_guide_value(response, "actionMode")
    if explicit:
        return explicit
    if is_watchlist_context(context):
        if response.action in {"BUY", "ADD"}:
            return "소액 진입 검토"
        if response.action == "AVOID":
            return "신규 진입 회피"
        return "대기"
    if response.action == "HOLD":
        return _holding_action_mode(context)
    if _is_outside_regular_session(context):
        return "정규장 확인"
    if response.action in {"SELL", "TRIM"}:
        return "분할 준비"
    if response.action in {"BUY", "ADD"}:
        return "소액 진입 검토"
    return "대기"

def _holding_position_sizing(context: Dict[str, object]) -> str:
    quantity = _quantity_number(_plain_value(context, "매도가능 수량") or _plain_value(context, "보유 수량"))
    keep = (_quantity_display(quantity) + "주 유지") if quantity else "현재 수량 유지"
    zone = _loss_zone(context)
    if zone in {"large_loss", "loss_management"}:
        sized = _quantity_range_text(quantity, 0.20, 0.30)
        if sized:
            return keep + ". 추가매수는 보류하고, 약한 조건이 다음 조회에서도 이어지면 " + sized + " 축소 기준을 준비"
        return keep + ". 추가매수는 보류하고, 약한 조건이 이어지면 일부 축소 기준을 먼저 준비"
    if zone == "small_loss":
        return keep + ". 손실이 더 커지기 전까지 새로 늘리지 않고 회복 조건부터 확인"
    if zone == "profit_protection":
        sized = _quantity_range_text(quantity, 0.20, 0.30)
        if sized:
            return keep + ". 평균선 아래로 밀리면 수익 보호용 " + sized + " 축소 기준을 준비"
        return keep + ". 평균선 아래로 밀리면 수익 보호용 일부 축소 기준을 준비"
    return keep + ". 새로 늘리기보다 다음 조회에서도 가격과 거래가 버티는지 확인"

def _derived_position_sizing(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    explicit = _strategy_guide_value(response, "positionSizing")
    if explicit:
        return explicit
    if is_watchlist_context(context):
        if response.action in {"BUY", "ADD"}:
            return "처음 진입이면 계획 금액의 일부만 사용하고, 다음 조회에서도 조건이 유지될 때 나머지를 검토"
        return "보유 수량이 없으므로 수량 변경이 아니라 신규 진입 여부만 판단"
    quantity = _quantity_number(_plain_value(context, "매도가능 수량") or _plain_value(context, "보유 수량"))
    if response.action == "SELL":
        sized = _quantity_range_text(quantity, 0.30, 0.50)
        return (sized + " 축소 검토") if sized else "전량 판단보다 일부 축소 기준부터 확인"
    if response.action == "TRIM":
        sized = _quantity_range_text(quantity, 0.20, 0.30)
        return (sized + " 축소 검토") if sized else "줄일 수량 정보가 없어 비중 기준부터 확인"
    if response.action in {"BUY", "ADD"}:
        return "추가 진입은 한 번에 늘리기보다 계획 수량의 일부만 검토"
    if response.action == "HOLD":
        return _holding_position_sizing(context)
    return "수량 변경보다 보유 이유와 회복 조건 확인"

def _derived_interpretation(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    explicit = _strategy_guide_value(response, "interpretation")
    if explicit:
        return explicit
    opinion = str(response.opinion or "").strip()
    action_label = action_label_for_action(response.action, context) or response.action_label or response.action
    if _is_outside_regular_session(context) and response.action in {"SELL", "TRIM"}:
        return "지금은 바로 전량 " + action_label + "보다 정규장 확인 후 분할 대응을 준비하는 쪽이 맞습니다. " + " ".join(part for part in [response.summary, opinion] if part)
    if response.action in {"SELL", "TRIM"}:
        return "바로 결론을 고정하기보다 손실 관리 기준과 줄일 수량을 같이 확인하는 " + action_label + " 검토 단계입니다. " + " ".join(part for part in [response.summary, opinion] if part)
    if is_watchlist_context(context):
        return "보유 종목 판단이 아니라 새로 들어갈 조건을 확인하는 단계입니다. " + " ".join(part for part in [response.summary, opinion] if part)
    if response.action == "HOLD":
        zone = _loss_zone(context)
        base = " ".join(part for part in [response.summary, opinion] if part)
        if zone in {"large_loss", "loss_management"}:
            return "보유 의견은 낙관이 아니라, 지금 바로 전량 정리할 만큼의 추가 근거가 부족하다는 뜻입니다. 추가매수는 막고 손실 방어 기준을 확인합니다. " + base
        if zone == "profit_protection":
            return "보유 의견은 수익을 계속 방치하라는 뜻이 아니라, 수익을 지키는 기준을 정해 둔 채 더 이어지는지 확인하는 단계입니다. " + base
        if _is_outside_regular_session(context):
            return "장외나 거래가 적은 시간대라 지금 가격만으로 수량을 바꾸기보다 정규장 거래를 다시 확인하는 단계입니다. " + base
        return "보유 의견은 새로 늘리라는 뜻이 아니라, 조건이 유지되는지 보면서 다음 행동 기준을 정하는 단계입니다. " + base
    return " ".join(part for part in [response.summary, opinion] if part) or "현재 조건을 다음 조회에서도 확인하는 단계입니다."

def _price_clause(label: str, price: str) -> str:
    return (price + "(" + label + ")") if price else ""

def _derived_holding_execution_criteria(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    prices = _strategy_price_levels(context, response)
    sizing = _derived_position_sizing(context, response)
    current = prices.get("current") or prices.get("risk") or ""
    ma5 = prices.get("ma5") or ""
    ma20 = prices.get("ma20") or prices.get("recovery") or ""
    recovery = ma20 or ma5 or prices.get("ma60") or ""
    zone = _loss_zone(context)
    session = "정규장 기준으로 " if _is_outside_regular_session(context) else "다음 조회에서도 "
    if zone in {"large_loss", "loss_management"}:
        weak_parts = []
        if current:
            weak_parts.append(current + " 아래로 더 밀림")
        if ma5:
            weak_parts.append(_price_clause("5일 평균", ma5) + " 회복 실패")
        if not weak_parts and ma20:
            weak_parts.append(_price_clause("20일 평균", ma20) + " 회복 실패")
        first = session + " / ".join(weak_parts or ["약한 가격 흐름"]) + "이면 분할축소 판단으로 바꿉니다"
        if recovery:
            first += ". " + _price_clause("20일 평균", recovery) + " 위로 회복하고 거래량이 붙으면 보유 유지 근거가 강해집니다"
        first += ". " + sizing
        return first
    if zone == "profit_protection":
        guard = ma20 or ma5 or current
        first = "수익은 유지하되 " + (_price_clause("20일 평균", guard) + " 아래로 내려가면 이익 보호용 일부 축소를 검토합니다" if guard else "주요 평균선 아래로 내려가면 이익 보호용 일부 축소를 검토합니다")
        if recovery:
            first += ". " + _price_clause("회복 기준", recovery) + " 위에서 거래가 붙으면 보유를 유지합니다"
        first += ". " + sizing
        return first
    first = "새로 늘리지는 않습니다"
    if recovery:
        first += ". " + _price_clause("확인 기준", recovery) + " 위에서 거래가 붙으면 보유를 유지하고, 아래로 내려가면 추가매수는 계속 보류합니다"
    elif current:
        first += ". 다음 조회에서도 " + current + " 근처를 지키는지 먼저 봅니다"
    first += ". " + sizing
    return first

def _derived_execution_criteria(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    explicit = _strategy_guide_value(response, "executionCriteria")
    if explicit:
        return explicit
    prices = _strategy_price_levels(context, response)
    sizing = _derived_position_sizing(context, response)
    action_label = action_label_for_action(response.action, context) or response.action_label or response.action
    if _is_outside_regular_session(context) and response.action in {"SELL", "TRIM"}:
        first = "정규장 시작 후에도 " + (prices["risk"] + " 아래이고 " if prices["risk"] else "") + "거래량이 붙으면 " + sizing + "합니다"
    elif response.action in {"SELL", "TRIM"}:
        first = "다음 조회에서도 약한 조건이 유지되면 " + sizing + "합니다"
    elif response.action in {"BUY", "ADD"}:
        first = "다음 조회에서도 가격과 거래가 같이 버티면 " + sizing + "합니다"
    elif response.action == "AVOID":
        first = "위험 조건이 해소되기 전까지 신규 진입을 피합니다"
    elif response.action == "HOLD":
        return _derived_holding_execution_criteria(context, response)
    else:
        first = "지금은 수량을 바꾸기보다 확인 조건을 기다립니다"
    if prices["recovery"] and response.action in {"SELL", "TRIM", "AVOID"}:
        first += ". " + prices["recovery"] + " 위로 회복하면 " + action_label + " 강도를 낮춥니다"
    elif response.invalidation_condition:
        first += ". " + response.invalidation_condition
    return first

def _derived_invalidation_condition(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    explicit = _strategy_guide_value(response, "invalidationCondition") or response.invalidation_condition
    if explicit and not (response.action == "HOLD" and "관계 점수 급변" in explicit):
        return explicit
    if response.action != "HOLD":
        return ""
    prices = _strategy_price_levels(context, response)
    current = prices.get("current") or ""
    ma5 = prices.get("ma5") or ""
    ma20 = prices.get("ma20") or prices.get("recovery") or ""
    zone = _loss_zone(context)
    if zone in {"large_loss", "loss_management"}:
        parts = []
        if current:
            parts.append(current + " 아래로 더 내려감")
        if ma5:
            parts.append(_price_clause("5일 평균", ma5) + " 회복 실패")
        if ma20:
            parts.append(_price_clause("20일 평균", ma20) + " 회복 실패")
        return "다음 조회에서도 " + " + ".join(parts or ["약한 조건"]) + "가 이어지면 보유 의견이 약해지고 분할축소를 다시 봅니다."
    if zone == "profit_protection":
        guard = ma20 or ma5 or current
        return (_price_clause("20일 평균", guard) + " 아래로 내려가거나 직접 악재가 추가되면 보유 의견이 약해집니다.") if guard else "가격 흐름이 약해지거나 직접 악재가 추가되면 보유 의견이 약해집니다."
    guard = ma20 or ma5 or current
    return (_price_clause("확인 기준", guard) + " 아래로 내려가고 거래가 늘지 않으면 보유 의견이 약해집니다.") if guard else "가격과 거래가 같이 약해지면 보유 의견이 약해집니다."

def _strategy_confidence_limiters(context: Dict[str, object], response: NotificationAIValidatedResponse) -> List[str]:
    rows = list(_strategy_guide_list(response, "dataLimitations"))
    if _is_outside_regular_session(context):
        append_unique_text(rows, "정규 거래시간 밖이라 거래가 적고 현재가 신뢰도가 낮을 수 있습니다.", 0)
    if _has_low_volume_context(context):
        append_unique_text(rows, "거래량이 평균보다 크게 낮아 강한 매수세나 투매로 단정하지 않습니다.", 0)
    for item in customer_data_note_rows(list(response.missing_data_impact)):
        append_unique_text(rows, item, 0)
    return rows

def _derived_ai_hypothesis(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    explicit = _strategy_guide_value(response, "aiHypothesis")
    if explicit:
        return explicit
    blob = _context_blob(context).lower()
    target = str(context.get("displayTarget") or context.get("target") or context.get("title") or "")
    if "adr" in blob or "crosslisted" in blob or "skhy" in blob:
        return target_name_for_headline(target) + " 같은 ADR은 한국 본주, 환율, 미국 반도체 투자심리에 같이 흔들릴 수 있습니다."
    if "mstr" in blob or "strc" in blob or "bitcoin" in blob or "비트코인" in blob:
        return target_name_for_headline(target) + "은 비트코인 가격과 시장 심리에 민감하게 반응할 수 있습니다."
    if "semiconductor" in blob or "반도체" in blob or "hynix" in blob or "하이닉스" in blob or "삼성전자" in blob:
        return "반도체 종목은 AI 수요, 메모리 가격, 미국 반도체 업종 심리에 같이 영향을 받을 수 있습니다."
    if "금리" in blob or "10년" in blob or "growth" in blob or "성장" in blob:
        return "성장주는 금리가 높을수록 미래 이익의 현재 가치가 낮게 평가될 수 있어 금리 변화에 민감할 수 있습니다."
    return ""

def _ai_hypothesis_boundary(response: NotificationAIValidatedResponse) -> str:
    return _strategy_guide_value(response, "hypothesisBoundary") or "이 내용은 수집 데이터 밖의 일반 배경지식이므로 매매 근거가 아니라 다음에 확인할 가설입니다."

def strategy_guide_quality(context: Dict[str, object], response: NotificationAIValidatedResponse) -> Dict[str, object]:
    prices = _strategy_price_levels(context, response)
    checks = [
        ("actionMode", bool(_derived_action_mode(context, response))),
        ("positionSizing", bool(_derived_position_sizing(context, response))),
        ("priceCriteria", bool(prices.get("risk") or prices.get("recovery"))),
        ("dataLimitations", bool(_strategy_confidence_limiters(context, response))),
        ("aiHypothesisSeparated", bool(_derived_ai_hypothesis(context, response))),
        ("invalidationCondition", bool(_derived_invalidation_condition(context, response))),
    ]
    passed = [key for key, ok in checks if ok]
    missing = [key for key, ok in checks if not ok]
    score = round(100 * len(passed) / max(1, len(checks)), 1)
    return {"score": score, "passed": passed, "missing": missing}

def strategy_guide_rows(context: Dict[str, object], response: NotificationAIValidatedResponse, level: str) -> List[str]:
    rows: List[str] = []
    action_label = action_label_for_action(response.action, context) or response.action_label or response.action
    interpretation = _derived_interpretation(context, response)
    if interpretation:
        append_unique_text(rows, "결론: " + action_label + ". AI 해석: " + interpretation, 0)
    action_mode = _derived_action_mode(context, response)
    if action_mode:
        append_unique_text(rows, "대응 모드: " + action_mode, 0)
    execution = _derived_execution_criteria(context, response)
    if execution:
        append_unique_text(rows, "실행 기준: " + execution, 0)
    evidence_summary = _compact_text_segments(response.evidence or context_specific_insight_rows(context, response, MESSAGE_CONTEXT_ROW_LIMIT), 0, 0)
    if evidence_summary:
        append_unique_text(rows, "핵심 근거: " + evidence_summary, 0)
    counter_summary = _compact_text_segments(response.counter_evidence, 0, 0)
    if counter_summary:
        append_unique_text(rows, "반대 신호: " + counter_summary, 0)
    check_items = list(_strategy_guide_list(response, "confirmationData"))
    check_items.extend(response.next_checks or [])
    check_summary = _compact_text_segments(check_items, 0, 0)
    if check_summary:
        append_unique_text(rows, "확인할 데이터/다음 확인: " + check_summary, 0)
    limiters = _strategy_confidence_limiters(context, response)
    if limiters:
        append_unique_text(rows, "추가 확인 데이터: " + _compact_text_segments(limiters, 0, 0), 0)
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
    for item in _crypto_trigger_summary_lines(context, 3):
        append_unique_text(rows, item, 230)
    for item in relation_axis_summary_lines(context, limit):
        append_unique_text(rows, item, 230)
        if len(rows) >= limit:
            break
    return [_html_bullet(item, level) for item in rows if str(item or "").strip()]


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
            "valuationReliabilityLabel",
            "valuationReliabilityScore",
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
    margin = _valuation_pct_display(facts.get("valuationMarginOfSafetyPct"))
    minimum_margin = _valuation_pct_display(facts.get("valuationMinimumMarginOfSafetyPct"))
    margin_text = margin
    if margin and minimum_margin:
        margin_text += " / 요구 " + minimum_margin
    source = str(facts.get("valuationSourceLabel") or "").strip()
    if facts.get("valuationHasUserInput") and facts.get("valuationHasExternalInput") and source:
        source += " · 외부 데이터도 참고"
    if not source:
        source = "사용자 입력 없음 · 외부 밸류에이션 데이터 없음"
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
    reliability = str(facts.get("valuationReliabilityLabel") or "").strip()
    reliability_score = facts.get("valuationReliabilityScore")
    if reliability and _valuation_value_present(reliability_score):
        reliability += " (" + str(round(_number(reliability_score), 1)).rstrip("0").rstrip(".") + "%)"
    elif _valuation_value_present(reliability_score):
        reliability = str(round(_number(reliability_score), 1)).rstrip("0").rstrip(".") + "%"
    if not reliability:
        reliability = "판단 보류"
    if reliability:
        reliability += " · 예측 성공률이 아니라 출처와 공식 완성도 기준"
    explanation = str(facts.get("valuationExplanation") or "").strip()
    if not explanation:
        explanation = "적정가 공식이나 적정가 입력값이 없어 현재가가 싼지 비싼지 계산하지 않았습니다. 설정 탭에서 적정가, 예상 EPS, 목표 PER 중 하나를 입력해야 합니다."
    data_status = str(facts.get("valuationDataStatus") or "").strip() or ("available" if fair_value and margin else "missing")
    status_labels = {
        "available": "계산 가능",
        "partial": "일부 부족",
        "missing": "부족",
    }
    method = str(facts.get("valuationMethod") or "").strip()
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
        _html_row("적정가", fair_value or "미설정", level=level),
        _html_row("안전마진", margin_text or "계산 불가", level=level),
        _html_row("데이터 출처", source, level=level),
        _html_row("계산 근거", str(facts.get("valuationSourceReason") or "").strip(), level=level, max_len=260),
        _html_row("근거 신뢰도", reliability, level=level, max_len=260),
        _html_row("계산 상태", status_text, level=level),
        _html_row("계산 뜻", explanation, level=level, max_len=700),
        _html_row("부족 데이터", ", ".join(str(item) for item in missing_inputs[:5]), level=level, max_len=260),
    ]
    return [row for row in rows if row]


def execution_telegram_message(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    level = delivery_level_from_context(context)
    if level == "absoluteBeginner":
        return execution_telegram_message_absolute_beginner(context, response)
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
    investor = _plain_value(context, "투자자")
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
    if current_state_rows:
        parts.extend(["", "<b>현재 상황</b>", *current_state_rows])
    valuation_rows = valuation_detail_rows(context, level)
    if valuation_rows:
        parts.extend(["", "<b>밸류에이션</b>", *valuation_rows])
    axis_rows = relation_axis_summary_rows(context, level)
    if axis_rows:
        parts.extend(["", "<b>투자 판단 근거</b>", *axis_rows])
    opinion_rows = strategy_guide_rows(context, response, level)
    if opinion_rows:
        parts.extend(["", "<b>전략 가이드</b>", *opinion_rows])
    if response.source_urls:
        parts.extend(["", "<b>출처</b>"])
        parts.extend(source_url_rows(response.source_urls, context))
    parts.extend(execution_footer(context, response, reference, sent))
    return "\n".join(part for part in parts if str(part).strip() or part == "").strip()

def execution_telegram_message_absolute_beginner(context: Dict[str, object], response: NotificationAIValidatedResponse) -> str:
    headline = execution_headline(context, response)
    target = str(context.get("displayTarget") or context.get("target") or "").strip()
    sent = str(context.get("sentTime") or "").strip()
    reference = response.reference_date or reference_date(context)
    current_state_rows = beginner_current_state_rows(context)
    parts = [
        "<b>" + html.escape(headline, quote=False) + "</b>",
        ("<code>" + html.escape(target, quote=False) + "</code>") if target else "",
        "",
        "<b>" + ai_judgment_section_title("absoluteBeginner") + "</b>",
        *ai_judgment_rows(response, "absoluteBeginner", context),
        _html_row("안내", "자동 주문이 아니라 실행 전 점검 알림입니다.", True),
    ]
    if current_state_rows:
        parts.extend(["", "<b>현재 상황</b>", *current_state_rows])
    valuation_rows = valuation_detail_rows(context, "absoluteBeginner")
    if valuation_rows:
        parts.extend(["", "<b>밸류에이션</b>", *valuation_rows])
    axis_rows = relation_axis_summary_rows(context, "absoluteBeginner")
    if axis_rows:
        parts.extend(["", "<b>투자 판단 근거</b>", *axis_rows])
    opinion_rows = strategy_guide_rows(context, response, "absoluteBeginner")
    if opinion_rows:
        parts.extend(["", "<b>전략 가이드</b>", *opinion_rows])
    if response.source_urls:
        parts.extend(["", "<b>원문/출처</b>"])
        parts.extend(source_url_rows(response.source_urls, context))
    parts.extend(execution_footer(context, response, reference, sent))
    return "\n".join(part for part in parts if str(part).strip() or part == "").strip()

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
    rows.extend(_html_multiline_rows("투자자", _plain_value(context, "투자자")))
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
                score = item.get("score") or item.get("strength") or item.get("relationScore")
                if label:
                    rows.append(label + ((" (" + str(score) + "점)") if score not in (None, "") else ""))
            elif str(item or "").strip():
                rows.append(str(item).strip())
            if len(rows) >= limit:
                break
    return rows
