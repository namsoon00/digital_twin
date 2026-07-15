import html
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 compatibility guard.
    ZoneInfo = None

from ..domain.accounts import investment_strategy_profile, message_delivery_profile
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

BEGINNER_LABEL_REPLACEMENTS = {
    "추세": "가격 흐름(추세)",
    "수급": "거래 흐름(수급)",
    "확인할 반대 신호": "반대 신호",
    "검증 메모": "검증 결과",
}

ABSOLUTE_BEGINNER_LABEL_REPLACEMENTS = {
    "추세": "가격 흐름",
    "수급": "거래 흐름",
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


def _html_row(label: str, value: object, beginner: bool = False, level: str = "") -> str:
    text = _text(value, 500)
    if not text:
        return ""
    display_level = level or ("absoluteBeginner" if beginner else "")
    if display_level:
        text = _message_text(text, display_level)
    return "• <b>" + html.escape(_message_label(label, display_level), quote=False) + "</b>: <code>" + html.escape(text, quote=False) + "</code>"

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
    if level in {"absoluteBeginner", "beginner"}:
        return "판단 요약"
    return "AI 최종 판단"

def ai_action_row_label(level: str) -> str:
    if level == "absoluteBeginner":
        return "지금 할 일"
    return "대응 방향"

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
        _html_row(ai_action_row_label(level), action_label_for_action(response.action, context) or response.action_label, level=level),
        _html_row("판단 강도", ai_confidence_display(response, level), level=level),
    ]
    rows.extend(account_profile_rows(context or {}, level))
    summary_label = "이유" if level == "absoluteBeginner" else "AI 판단 이유"
    if response.summary:
        rows.append(_html_row(summary_label, response.summary, level=level))
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

    def add(label: object, value: object, suffix: object = "", kind: object = "", query_info: object = "") -> None:
        if len(rows) >= limit:
            return
        stamp = _format_kst_timestamp(value)
        if not stamp:
            return
        source = _collection_source_label(label, kind)
        detail = _text(str(suffix or "").strip(), 72)
        info = _text(str(query_info or kind or "API 데이터").strip(), 110)
        parts = []
        if info:
            parts.append("조회 정보 " + info)
        parts.append("조회시각 " + stamp)
        if detail:
            parts.append(detail)
        text = source + ": " + " · ".join(parts)
        key = re.sub(r"\s+", " ", source + "|" + info + "|" + stamp + "|" + detail).strip().lower()
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
        stamp = next((value.get(key) for key in DATA_COLLECTION_TIME_KEYS if value.get(key)), "")
        if stamp:
            source = next((value.get(key) for key in DATA_COLLECTION_SOURCE_KEYS if value.get(key)), "")
            detail = next((value.get(key) for key in DATA_COLLECTION_DETAIL_KEYS if value.get(key)), "")
            add(
                source or value.get("kind") or value.get("type") or "API 데이터",
                stamp,
                detail,
                _collection_text_for_kind(value, source, detail),
                _collection_query_info(value, source, detail),
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


def notification_cooldown_release_summary(context: Dict[str, object]) -> str:
    context = context or {}
    if context.get("honeyStateSuppressed"):
        return ""
    decision = str(context.get("honeyStateDecision") or "").strip()
    if not decision or decision == "cooldown":
        return ""
    cooldown_enabled = bool(context.get("honeyStateCooldownEnabled"))
    reason = _clean_reason_text(context.get("honeyStateReason") or context.get("honeySimilarityBypassReason"), 150)
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
    difference_rows = ai_difference_rows(response, level, context)
    if difference_rows:
        parts.extend(["", "<b>AI 판단 조정</b>", *difference_rows])
    if current_state_rows:
        parts.extend(["", "<b>현재 상태</b>", *current_state_rows])
    api_source_rows = external_api_source_rows(context, MESSAGE_API_SOURCE_ROW_LIMIT)
    collection_rows = data_collection_time_rows(context, MESSAGE_DATA_COLLECTION_ROW_LIMIT)
    if api_source_rows or collection_rows:
        parts.extend(["", "<b>API 조회 정보</b>"])
        parts.extend(_html_bullet(item, level) for item in api_source_rows)
        parts.extend(_html_bullet(item, level) for item in collection_rows)
    quality_rows = data_quality_warning_rows(context, MESSAGE_DATA_QUALITY_ROW_LIMIT)
    if quality_rows:
        parts.extend(["", "<b>데이터 신뢰도</b>"])
        parts.extend(_html_bullet(item, level) for item in quality_rows)
    context_rows = context_specific_insight_rows(context, response, MESSAGE_CONTEXT_ROW_LIMIT)
    if context_rows:
        parts.extend(["", "<b>이번 알림에서 봐야 할 것</b>"])
        parts.extend(_html_bullet(item, level) for item in context_rows)
    parts.extend(["", "<b>AI가 중요하게 본 근거</b>"])
    parts.extend(_html_bullet(item, level) for item in response.evidence)
    if response.counter_evidence:
        parts.extend(["", "<b>" + ("반대 신호" if level == "beginner" else "확인할 반대 신호") + "</b>"])
        parts.extend(_html_bullet(item, level) for item in response.counter_evidence)
    parts.extend(["", "<b>실행 전 확인</b>"])
    if response.opinion:
        parts.append(_html_bullet(response.opinion, level))
    if response.invalidation_condition:
        parts.append(_html_bullet(response.invalidation_condition, level, "의견이 약해지는 조건: "))
    for item in response.next_checks:
        parts.append(_html_bullet(item, level, "다음 확인: "))
    if response.missing_data_impact:
        parts.extend(["", "<b>" + html.escape(_message_label("부족 데이터", level), quote=False) + "</b>"])
        parts.extend(_html_bullet(item, level) for item in response.missing_data_impact)
    if response.validation_warnings:
        parts.extend(["", "<b>" + html.escape(_message_label("검증 메모", level), quote=False) + "</b>"])
        parts.extend(_html_bullet(item, level) for item in response.validation_warnings)
    reason = notification_reason_summary(context)
    cooldown_reason = notification_cooldown_release_summary(context)
    if reason or cooldown_reason:
        parts.extend(["", "<b>알림이 온 이유</b>"])
        if reason:
            parts.append(_html_bullet(reason, level))
        if cooldown_reason:
            parts.append(_html_bullet(cooldown_reason, level, "쿨다운 해제: "))
    if level == "advanced":
        relation_labels = relation_rule_summary(context, 4)
        if relation_labels:
            parts.extend(["", "<b>관계 규칙 요약</b>"])
            parts.extend("• " + html.escape(item, quote=False) for item in relation_labels)
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
    difference_rows = ai_difference_rows(response, "absoluteBeginner", context)
    if difference_rows:
        parts.extend(["", "<b>AI 판단 조정</b>", *difference_rows])
    if current_state_rows:
        parts.extend(["", "<b>현재 상황</b>", *current_state_rows])
    api_source_rows = external_api_source_rows(context, MESSAGE_API_SOURCE_ROW_LIMIT)
    collection_rows = data_collection_time_rows(context, MESSAGE_DATA_COLLECTION_ROW_LIMIT)
    if api_source_rows or collection_rows:
        parts.extend(["", "<b>API 조회 정보</b>"])
        parts.extend(_html_bullet(item, "absoluteBeginner") for item in api_source_rows)
        parts.extend(_html_bullet(item, "absoluteBeginner") for item in collection_rows)
    quality_rows = data_quality_warning_rows(context, MESSAGE_DATA_QUALITY_ROW_LIMIT)
    if quality_rows:
        parts.extend(["", "<b>데이터 신뢰도</b>"])
        parts.extend(_html_bullet(item, "absoluteBeginner") for item in quality_rows)
    context_rows = context_specific_insight_rows(context, response, MESSAGE_CONTEXT_ROW_LIMIT)
    if context_rows:
        parts.extend(["", "<b>이번 알림에서 봐야 할 것</b>"])
        parts.extend(_html_bullet(item, "absoluteBeginner") for item in context_rows)
    if response.evidence:
        parts.extend(["", "<b>AI가 중요하게 본 근거</b>"])
        parts.extend(_html_bullet(item, "absoluteBeginner") for item in response.evidence)
    if response.counter_evidence:
        parts.extend(["", "<b>반대 신호</b>"])
        parts.extend(_html_bullet(item, "absoluteBeginner") for item in response.counter_evidence)
    parts.extend(["", "<b>실행 전 확인</b>"])
    if response.opinion:
        parts.append(_html_bullet(response.opinion, "absoluteBeginner"))
    if response.invalidation_condition:
        parts.append(_html_bullet(response.invalidation_condition, "absoluteBeginner", "의견이 약해지는 조건: "))
    for item in response.next_checks:
        parts.append(_html_bullet(item, "absoluteBeginner"))
    if response.missing_data_impact:
        parts.extend(["", "<b>데이터 빈 곳</b>"])
        parts.extend(_html_bullet(item, "absoluteBeginner") for item in response.missing_data_impact)
    if response.validation_warnings:
        parts.extend(["", "<b>검증 결과</b>"])
        parts.extend(_html_bullet(item, "absoluteBeginner") for item in response.validation_warnings)
    reason = notification_reason_summary(context)
    cooldown_reason = notification_cooldown_release_summary(context)
    if reason or cooldown_reason:
        parts.extend(["", "<b>알림이 온 이유</b>"])
        if reason:
            parts.append(_html_bullet(reason, "absoluteBeginner"))
        if cooldown_reason:
            parts.append(_html_bullet(cooldown_reason, "absoluteBeginner", "쿨다운 해제: "))
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
        ("거래 흐름", _plain_value(context, "수급")),
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
