from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
) -> MarketHoursDecision:
    selected_markets = [normalize_market_key(item) for item in markets or []]
    selected_markets = [item for item in selected_markets if item]
    if not enabled:
        return MarketHoursDecision(False, status="bypass", reason="장 시간 필터 꺼짐")
    market = infer_market_from_context(message_type, context)
    if not market:
        return MarketHoursDecision(True, status="unknown", reason="시장 식별 불가로 통과")
    if selected_markets and market not in selected_markets:
        return MarketHoursDecision(True, market=market, status="bypass", reason="선택한 장 시간 대상이 아니라 통과")
    session = DEFAULT_MARKET_HOUR_SESSIONS.get(market)
    if not session:
        return MarketHoursDecision(True, market=market, status="unknown", reason="장 시간 세션 없음")

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
    return MarketHoursDecision(
        True,
        market=market,
        label=label,
        status=status,
        should_send=bool(matched_session),
        reason=reason,
        local_time=local_time,
        open_time=open_time,
        close_time=close_time,
        timezone=str(session.get("timezone") or ""),
    )
