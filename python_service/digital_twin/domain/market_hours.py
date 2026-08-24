from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .notification_ai_context import is_graph_backed_relation_context


OFF_HOURS_IMPORTANT_ONLY = "important_only"
OFF_HOURS_SEND_ALL = "send_all"
OFF_HOURS_DEFER_UNTIL_OPEN = "defer_until_open"
OFF_HOURS_DELIVERY_MODES = {
    OFF_HOURS_IMPORTANT_ONLY,
    OFF_HOURS_SEND_ALL,
    OFF_HOURS_DEFER_UNTIL_OPEN,
}


DEFAULT_MARKET_HOUR_SESSIONS: Dict[str, Dict[str, object]] = {
    "KR": {
        "market": "KR",
        "label": "국장",
        "timezone": "Asia/Seoul",
        "openTime": "08:00",
        "closeTime": "20:00",
        "sessions": [
            {"key": "pre", "label": "프리마켓", "openTime": "08:00", "closeTime": "08:50"},
            {"key": "regular", "label": "정규장", "openTime": "09:00", "closeTime": "15:30"},
            {"key": "after", "label": "애프터마켓", "openTime": "15:30", "closeTime": "20:00"},
        ],
        "weekdays": [0, 1, 2, 3, 4],
    },
    "US": {
        "market": "US",
        "label": "미장",
        "timezone": "America/New_York",
        "openTime": "04:00",
        "closeTime": "20:00",
        "sessions": [
            {"key": "pre", "label": "프리마켓", "openTime": "04:00", "closeTime": "09:30"},
            {"key": "regular", "label": "정규장", "openTime": "09:30", "closeTime": "16:00"},
            {"key": "after", "label": "애프터마켓", "openTime": "16:00", "closeTime": "20:00"},
        ],
        "weekdays": [0, 1, 2, 3, 4],
    },
}


@dataclass
class MarketHoursDecision:
    enabled: bool
    market: str = ""
    label: str = ""
    status: str = "bypass"
    should_send: bool = True
    reason: str = ""
    local_time: str = ""
    open_time: str = ""
    close_time: str = ""
    timezone: str = ""
    off_hours_mode: str = OFF_HOURS_IMPORTANT_ONLY

    def to_context(self) -> Dict[str, object]:
        return {
            "marketHoursEnabled": bool(self.enabled),
            "marketHoursMarket": self.market,
            "marketHoursLabel": self.label,
            "marketHoursStatus": self.status,
            "marketHoursDecision": "send" if self.should_send else "suppressed",
            "marketHoursReason": self.reason,
            "marketHoursLocalTime": self.local_time,
            "marketHoursOpenTime": self.open_time,
            "marketHoursCloseTime": self.close_time,
            "marketHoursTimezone": self.timezone,
            "offHoursDeliveryMode": self.off_hours_mode,
        }


def normalize_market_key(value: object) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in {"KR", "KOR", "KOREA", "KOSPI", "KOSDAQ", "KONEX", "KRX", "XKRX"}:
        return "KR"
    if normalized in {"US", "USA", "NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "XNYS", "XNAS"}:
        return "US"
    return ""


def default_market_hours_enabled(message_type: str) -> bool:
    return str(message_type or "") in {
        "investmentInsight",
        "modelBuy",
        "modelSell",
        "watchlistBuyCandidate",
        "watchlistQuote",
        "watchlistQuotePending",
        "holdingTiming",
        "monitorPositionChange",
        "monitorPnlChange",
        "monitorValueChange",
        "monitorTrendChange",
        "monitorDecisionChange",
        "externalEquityMove",
        "externalDartDisclosure",
    }


def default_market_hours_markets(message_type: str) -> List[str]:
    key = str(message_type or "")
    if key == "externalEquityMove":
        return ["US"]
    if key == "externalDartDisclosure":
        return ["KR"]
    if default_market_hours_enabled(key):
        return ["KR", "US"]
    return []


def default_off_hours_delivery_mode(message_type: str) -> str:
    key = str(message_type or "").strip()
    if key in {"investmentInsight", "externalDartDisclosure"}:
        return OFF_HOURS_IMPORTANT_ONLY
    return OFF_HOURS_DEFER_UNTIL_OPEN


def normalize_off_hours_delivery_mode(value: object, message_type: str = "") -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "important": OFF_HOURS_IMPORTANT_ONLY,
        "importantonly": OFF_HOURS_IMPORTANT_ONLY,
        "all": OFF_HOURS_SEND_ALL,
        "always": OFF_HOURS_SEND_ALL,
        "sendall": OFF_HOURS_SEND_ALL,
        "defer": OFF_HOURS_DEFER_UNTIL_OPEN,
        "closed": OFF_HOURS_DEFER_UNTIL_OPEN,
        "market_only": OFF_HOURS_DEFER_UNTIL_OPEN,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in OFF_HOURS_DELIVERY_MODES:
        return normalized
    return default_off_hours_delivery_mode(message_type)


def infer_market_from_context(message_type: str, context: Dict[str, object]) -> str:
    context = context or {}
    explicit = normalize_market_key(context.get("market") or context.get("exchange") or context.get("marketCode"))
    if explicit:
        return explicit
    currency = str(context.get("currency") or "").strip().upper()
    if currency == "KRW":
        return "KR"
    if currency == "USD":
        return "US"
    key = str(message_type or context.get("messageType") or "").strip()
    if key == "externalEquityMove":
        return "US"
    if key == "externalDartDisclosure":
        return "KR"
    symbol = str(context.get("symbol") or context.get("target") or "").strip().upper()
    compact_symbol = symbol.replace(".", "").replace("-", "")
    if compact_symbol.isdigit() and 4 <= len(compact_symbol) <= 8:
        return "KR"
    if symbol.endswith((".KS", ".KQ")):
        return "KR"
    if symbol and any(char.isalpha() for char in symbol):
        return "US"
    return ""


def _mapping(value: object) -> Dict[str, object]:
    return value if isinstance(value, dict) else {}


def _normalized_values(*values: object) -> set:
    return {
        str(value or "").strip().replace("-", "_").replace(" ", "_").upper()
        for value in values
        if str(value or "").strip()
    }


def _structured_disclosure_changed_decision(context: Dict[str, object]) -> bool:
    source_types = context.get("sourceSignalTypes")
    source_types = source_types if isinstance(source_types, list) else []
    if "externalDartDisclosure" not in {str(item or "").strip() for item in source_types}:
        return False
    insight = _mapping(context.get("ontologyInsight"))
    semantic = _mapping(insight.get("semanticComponents"))
    event_keys = semantic.get("materialSourceEventKeys") or insight.get("materialSourceEventKeys") or []
    relation = _mapping(context.get("ontologyRelationContext"))
    return bool(
        event_keys
        and _mapping(context.get("ontologyRelationDiff")).get("material")
        and is_graph_backed_relation_context(relation)
    )


def _structured_urgent_investment_transition(context: Dict[str, object]) -> bool:
    relation_diff = _mapping(context.get("ontologyRelationDiff"))
    relation = _mapping(context.get("ontologyRelationContext"))
    if not relation_diff.get("material") or not is_graph_backed_relation_context(relation):
        return False

    decision = _mapping(relation.get("decision"))
    state = _mapping(relation.get("decisionState"))
    envelope = _mapping(relation.get("actionEnvelope"))
    transition = _mapping(relation_diff.get("decisionTransition"))
    review_level = str(
        state.get("reviewLevel")
        or relation.get("reviewLevel")
        or decision.get("reviewLevel")
        or ""
    ).strip().lower()
    if review_level not in {"act", "immediate"}:
        return False

    action_values = _normalized_values(
        envelope.get("preferredAction"),
        decision.get("primaryAction"),
        relation.get("primaryAction"),
        transition.get("currentAction"),
    )
    urgent_actions = {
        "TRIM",
        "SELL",
        "REDUCE",
        "EXIT",
        "STOP_LOSS",
        "TRIM_REVIEW",
        "SELL_REVIEW",
        "LOSS_CONTROL",
        "LOSS_CONTROL_WATCH",
    }
    action_groups = _normalized_values(
        decision.get("actionGroup"),
        relation.get("actionGroup"),
    )
    decision_stages = _normalized_values(
        decision.get("decisionStage"),
        relation.get("decisionStage"),
    )
    return bool(
        action_values & urgent_actions
        or action_groups & {"LOSSCONTROL", "RISKMANAGEMENT", "EVENTRISK"}
        or decision_stages & {"RISK_REVIEW", "LOSS_REDUCE", "LOSS_CONTROL", "EXIT_REVIEW"}
    )


def _structured_material_external_event_reason(context: Dict[str, object]) -> str:
    relation_diff = _mapping(context.get("ontologyRelationDiff"))
    relation = _mapping(context.get("ontologyRelationContext"))
    if not relation_diff.get("material") or not is_graph_backed_relation_context(relation):
        return ""
    source_types = {
        str(item or "").strip()
        for item in context.get("sourceSignalTypes") or []
        if str(item or "").strip()
    }
    insight = _mapping(context.get("ontologyInsight"))
    semantic = _mapping(insight.get("semanticComponents"))
    event_keys = [
        str(item or "").strip().lower()
        for item in semantic.get("materialSourceEventKeys") or insight.get("materialSourceEventKeys") or []
        if str(item or "").strip()
    ]
    if "externalCryptoMove" in source_types:
        return "크립토 급변이 TypeDB 투자 판단을 바꾼 중요 사건이라 장 시간 외에도 발송"
    if "externalMacroShift" in source_types:
        return "금리·환율 등 거시 변화가 TypeDB 투자 판단을 바꾼 중요 사건이라 장 시간 외에도 발송"
    if "newsDigest" in source_types or any(
        marker in key
        for key in event_keys
        for marker in (":news:", ":article:", ":rss:", ":filing:", ":sec:")
    ):
        return "새 뉴스·공시가 TypeDB 투자 판단을 바꾼 중요 사건이라 장 시간 외에도 발송"
    return ""


def market_hours_important_exception_reason(message_type: str, context: Dict[str, object]) -> str:
    key = str(message_type or (context or {}).get("messageType") or "").strip()
    context = context or {}
    if key == "externalDartDisclosure":
        return "공시는 장 시간 외에도 확인이 필요한 이벤트라 발송"
    if key != "investmentInsight":
        return ""
    if _structured_disclosure_changed_decision(context):
        return "새 공시가 TypeDB 판단을 실제로 바꾼 중요 이벤트라 장 시간 외에도 발송"
    if _structured_urgent_investment_transition(context):
        return "TypeDB 관계가 즉시 손실·위험 대응 단계로 바뀌어 장 시간 외에도 발송"
    material_event_reason = _structured_material_external_event_reason(context)
    if material_event_reason:
        return material_event_reason
    return ""


def parse_hhmm(value: object):
    parts = str(value or "").strip().split(":")
    if len(parts) < 2:
        return 0, 0
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return 0, 0
    return max(0, min(23, hour)), max(0, min(59, minute))


def market_time(now: datetime, timezone_name: str) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    return now.astimezone(zone)


def session_items(market_session: Dict[str, object]) -> List[Dict[str, object]]:
    raw_sessions = market_session.get("sessions") if isinstance(market_session.get("sessions"), list) else []
    sessions = [item for item in raw_sessions if isinstance(item, dict) and item.get("openTime") and item.get("closeTime")]
    if sessions:
        return sessions
    return [{
        "key": "regular",
        "label": market_session.get("label") or "정규장",
        "openTime": market_session.get("openTime") or "",
        "closeTime": market_session.get("closeTime") or "",
    }]


def session_summary(sessions: List[Dict[str, object]]) -> str:
    parts = []
    for item in sessions:
        label = str(item.get("label") or "").strip()
        open_time = str(item.get("openTime") or "")
        close_time = str(item.get("closeTime") or "")
        if open_time and close_time:
            parts.append((label + " " if label else "") + open_time + "-" + close_time)
    return " · ".join(parts)


def evaluate_market_hours(
    message_type: str,
    context: Dict[str, object],
    enabled: bool,
    markets: List[str],
    now: datetime = None,
    off_hours_mode: str = "",
) -> MarketHoursDecision:
    normalized_off_hours_mode = normalize_off_hours_delivery_mode(off_hours_mode, message_type)
    selected_markets = [normalize_market_key(item) for item in markets or []]
    selected_markets = [item for item in selected_markets if item]
    if not enabled:
        return MarketHoursDecision(
            False,
            status="bypass",
            reason="장 시간 필터 꺼짐",
            off_hours_mode=normalized_off_hours_mode,
        )
    market = infer_market_from_context(message_type, context)
    if not market:
        return MarketHoursDecision(
            True,
            status="unknown",
            reason="시장 식별 불가로 통과",
            off_hours_mode=normalized_off_hours_mode,
        )
    if selected_markets and market not in selected_markets:
        return MarketHoursDecision(
            True,
            market=market,
            status="bypass",
            reason="선택한 장 시간 대상이 아니라 통과",
            off_hours_mode=normalized_off_hours_mode,
        )
    session = DEFAULT_MARKET_HOUR_SESSIONS.get(market)
    if not session:
        return MarketHoursDecision(
            True,
            market=market,
            status="unknown",
            reason="장 시간 세션 없음",
            off_hours_mode=normalized_off_hours_mode,
        )

    sessions = session_items(session)
    current = market_time(now or datetime.now(timezone.utc), str(session.get("timezone") or "UTC"))
    weekdays = session.get("weekdays") if isinstance(session.get("weekdays"), list) else [0, 1, 2, 3, 4]
    is_weekday = current.weekday() in weekdays
    matched_session = None
    for item in sessions:
        open_hour, open_minute = parse_hhmm(item.get("openTime"))
        close_hour, close_minute = parse_hhmm(item.get("closeTime"))
        open_at = current.replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
        close_at = current.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
        if is_weekday and open_at <= current < close_at:
            matched_session = item
            break
    market_label = str(session.get("label") or market)
    session_label = str((matched_session or {}).get("label") or "").strip()
    label = " ".join(part for part in [market_label, session_label] if part).strip()
    open_time = str((matched_session or session).get("openTime") or "")
    close_time = str((matched_session or session).get("closeTime") or "")
    local_time = current.isoformat()
    if matched_session:
        reason = label + " 열림 (" + open_time + "-" + close_time + ")"
        status = "open"
    else:
        reason = market_label + " 닫힘 (" + session_summary(sessions) + ")"
        status = "closed"
        if normalized_off_hours_mode == OFF_HOURS_SEND_ALL:
            exception_reason = "모든 장외 투자 알림을 받도록 설정되어 발송"
        elif normalized_off_hours_mode == OFF_HOURS_IMPORTANT_ONLY:
            exception_reason = market_hours_important_exception_reason(message_type, context)
        else:
            exception_reason = ""
        if exception_reason:
            reason = reason + " · " + exception_reason
            status = "closed_exception"
    return MarketHoursDecision(
        True,
        market=market,
        label=label,
        status=status,
        should_send=bool(matched_session) or status == "closed_exception",
        reason=reason,
        local_time=local_time,
        open_time=open_time,
        close_time=close_time,
        timezone=str(session.get("timezone") or ""),
        off_hours_mode=normalized_off_hours_mode,
    )
