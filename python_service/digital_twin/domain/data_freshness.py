from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List

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
    MONITOR_POSITION_CHANGE,
    MONITOR_TREND_CHANGE,
    MONITOR_VALUE_CHANGE,
    WATCHLIST_BUY_CANDIDATE,
    WATCHLIST_ONTOLOGY_SIGNAL,
    WATCHLIST_QUOTE,
)


DATA_FRESHNESS_MESSAGE_TYPES = {
    INVESTMENT_INSIGHT,
    MODEL_BUY,
    MODEL_SELL,
    WATCHLIST_BUY_CANDIDATE,
    WATCHLIST_ONTOLOGY_SIGNAL,
    WATCHLIST_QUOTE,
    HOLDING_TIMING,
    MONITOR_POSITION_CHANGE,
    MONITOR_PNL_CHANGE,
    MONITOR_VALUE_CHANGE,
    MONITOR_TREND_CHANGE,
    MONITOR_DECISION_CHANGE,
    EXTERNAL_EQUITY_MOVE,
    EXTERNAL_CRYPTO_MOVE,
    EXTERNAL_MACRO_SHIFT,
    EXTERNAL_DART_DISCLOSURE,
}

QUOTE_MESSAGE_TYPES = {
    INVESTMENT_INSIGHT,
    MODEL_BUY,
    MODEL_SELL,
    WATCHLIST_BUY_CANDIDATE,
    WATCHLIST_ONTOLOGY_SIGNAL,
    WATCHLIST_QUOTE,
    HOLDING_TIMING,
    MONITOR_POSITION_CHANGE,
    MONITOR_PNL_CHANGE,
    MONITOR_VALUE_CHANGE,
    MONITOR_TREND_CHANGE,
    MONITOR_DECISION_CHANGE,
}


def bool_setting(settings: Dict[str, object], key: str, fallback: bool = True) -> bool:
    value = (settings or {}).get(key)
    if value in (None, ""):
        return fallback
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def int_setting(settings: Dict[str, object], key: str, fallback: int, minimum: int = 1, maximum: int = 1440 * 30) -> int:
    try:
        parsed = int(float(str((settings or {}).get(key) or "").strip()))
    except ValueError:
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def parse_datetime(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text + "T00:00:00+00:00")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def utc_iso(value=None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def age_minutes(value: object, now=None):
    parsed = parse_datetime(value)
    if not parsed:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0, int((current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() // 60))


def max_age_minutes_for_message_type(message_type: str, settings: Dict[str, object] = None) -> int:
    settings = settings or {}
    key = str(message_type or "")
    if key == EXTERNAL_CRYPTO_MOVE:
        return int_setting(settings, "dataFreshnessExternalCryptoMaxAgeMinutes", 10)
    if key == EXTERNAL_EQUITY_MOVE:
        return int_setting(settings, "dataFreshnessExternalEquityMaxAgeMinutes", 10)
    if key == EXTERNAL_MACRO_SHIFT:
        return int_setting(settings, "dataFreshnessMacroMaxAgeMinutes", 120)
    if key == EXTERNAL_DART_DISCLOSURE:
        return int_setting(settings, "dataFreshnessDisclosureMaxAgeMinutes", 120)
    if key in QUOTE_MESSAGE_TYPES:
        return int_setting(settings, "dataFreshnessQuoteMaxAgeMinutes", 10)
    return int_setting(settings, "dataFreshnessDefaultMaxAgeMinutes", 10)


def data_freshness_required(message_type: str) -> bool:
    return str(message_type or "") in DATA_FRESHNESS_MESSAGE_TYPES


def combine_quality(*values: object) -> str:
    qualities = [str(value or "").strip() for value in values if str(value or "").strip()]
    if not qualities:
        return ""
    unique = []
    for quality in qualities:
        if quality not in unique:
            unique.append(quality)
    if any(quality in {"cached", "stale", "unavailable"} for quality in unique):
        return "cached" if len(unique) == 1 and unique[0] == "cached" else "mixed"
    return unique[0] if len(unique) == 1 else "mixed"


def freshness_record(
    source: str,
    message_type: str,
    settings: Dict[str, object] = None,
    source_fetched_at: object = "",
    source_as_of: object = "",
    data_quality: object = "",
    now=None,
    max_age_minutes: int = 0,
) -> Dict[str, object]:
    settings = settings or {}
    max_age = int(max_age_minutes or max_age_minutes_for_message_type(message_type, settings))
    timestamp = source_fetched_at or source_as_of
    age = age_minutes(timestamp, now=now)
    quality = str(data_quality or "").strip()
    if age is None:
        status = "unknown"
        reason = "기준시각 없음"
    elif age <= max_age:
        status = "fresh"
        reason = "신선도 기준 통과"
    else:
        status = "stale"
        reason = "기준 " + str(max_age) + "분 초과"
    return {
        "source": str(source or "").strip() or "unknown",
        "status": status,
        "reason": reason,
        "ageMinutes": age,
        "maxAgeMinutes": max_age,
        "sourceFetchedAt": str(source_fetched_at or ""),
        "sourceAsOf": str(source_as_of or ""),
        "dataQuality": quality,
        "checkedAt": utc_iso(now),
    }


def freshness_from_position(position: Dict[str, object], message_type: str, settings: Dict[str, object] = None, now=None) -> Dict[str, object]:
    item = position or {}
    return freshness_record(
        item.get("quote_source") or item.get("quoteSource") or item.get("source") or "position",
        message_type,
        settings=settings,
        source_fetched_at=item.get("updated_at") or item.get("updatedAt"),
        data_quality=item.get("data_quality") or item.get("dataQuality"),
        now=now,
    )


def freshness_from_external_signals(signals: Dict[str, object], message_type: str, settings: Dict[str, object] = None, now=None) -> Dict[str, object]:
    payload = signals if isinstance(signals, dict) else {}
    freshness = payload.get("freshness") if isinstance(payload.get("freshness"), dict) else {}
    source = "externalSignals"
    if message_type == EXTERNAL_EQUITY_MOVE:
        source = "Alpha Vantage"
    elif message_type == EXTERNAL_CRYPTO_MOVE:
        source = "CoinGecko"
    elif message_type == EXTERNAL_MACRO_SHIFT:
        source = "FRED"
    elif message_type == EXTERNAL_DART_DISCLOSURE:
        source = "OpenDART"
    return freshness_record(
        source,
        message_type,
        settings=settings,
        source_fetched_at=freshness.get("fetchedAt") or payload.get("fetchedAt"),
        data_quality=(payload.get("quality") or {}).get("score") if isinstance(payload.get("quality"), dict) else "",
        now=now,
    )


def aggregate_freshness(records: Iterable[Dict[str, object]], message_type: str, settings: Dict[str, object] = None, now=None) -> Dict[str, object]:
    items = [dict(item) for item in records or [] if isinstance(item, dict)]
    if not items:
        return {
            "status": "unknown",
            "reason": "신선도 입력 없음",
            "sources": [],
            "checkedAt": utc_iso(now),
            "maxAgeMinutes": max_age_minutes_for_message_type(message_type, settings),
        }
    stale = [item for item in items if str(item.get("status") or "") == "stale"]
    unknown = [item for item in items if str(item.get("status") or "") == "unknown"]
    if stale:
        status = "stale"
        reason = ", ".join(str(item.get("source") or "unknown") + " " + str(item.get("reason") or "") for item in stale[:3])
    elif unknown:
        status = "unknown"
        reason = ", ".join(str(item.get("source") or "unknown") + " " + str(item.get("reason") or "") for item in unknown[:3])
    else:
        status = "fresh"
        reason = "모든 소스 신선도 기준 통과"
    ages = [item.get("ageMinutes") for item in items if isinstance(item.get("ageMinutes"), int)]
    max_ages = [int(item.get("maxAgeMinutes") or 0) for item in items if int(item.get("maxAgeMinutes") or 0)]
    return {
        "status": status,
        "reason": reason,
        "ageMinutes": max(ages) if ages else None,
        "maxAgeMinutes": min(max_ages) if max_ages else max_age_minutes_for_message_type(message_type, settings),
        "sources": items,
        "checkedAt": utc_iso(now),
    }


@dataclass
class DataFreshnessDecision:
    enabled: bool
    evaluated: bool
    should_send: bool
    status: str = "bypass"
    reason: str = ""
    age_minutes: object = None
    max_age_minutes: object = None
    stale_sources: List[str] = field(default_factory=list)

    def to_context(self) -> Dict[str, object]:
        return {
            "dataFreshnessGateEnabled": bool(self.enabled),
            "dataFreshnessEvaluated": bool(self.evaluated),
            "dataFreshnessDecision": "send" if self.should_send else "suppressed",
            "dataFreshnessStatus": self.status,
            "dataFreshnessReason": self.reason,
            "dataFreshnessAgeMinutes": self.age_minutes,
            "dataFreshnessMaxAgeMinutes": self.max_age_minutes,
            "dataFreshnessStaleSources": list(self.stale_sources),
        }


def evaluate_notification_data_freshness(context: Dict[str, object], settings: Dict[str, object] = None, now=None) -> DataFreshnessDecision:
    settings = settings or {}
    if not bool_setting(settings, "dataFreshnessEnabled", True):
        return DataFreshnessDecision(False, False, True, status="bypass", reason="데이터 신선도 게이트 꺼짐")
    context = context or {}
    message_type = str(context.get("messageType") or context.get("rule") or "")
    if not data_freshness_required(message_type):
        return DataFreshnessDecision(True, False, True, status="bypass", reason="신선도 게이트 대상 아님")
    data = context.get("dataFreshness") if isinstance(context.get("dataFreshness"), dict) else {}
    if not data:
        return DataFreshnessDecision(
            True,
            True,
            False,
            status="missing",
            reason="신선도 메타데이터 없음",
            stale_sources=["unknown"],
        )
    record = aggregate_freshness(data.get("sources") or [data], message_type, settings=settings, now=now)
    status = str(record.get("status") or "unknown")
    stale_sources = [
        str(item.get("source") or "unknown")
        for item in record.get("sources") or []
        if str(item.get("status") or "") in {"stale", "unknown"}
    ]
    should_send = status == "fresh"
    return DataFreshnessDecision(
        True,
        True,
        should_send,
        status=status,
        reason=str(record.get("reason") or ""),
        age_minutes=record.get("ageMinutes"),
        max_age_minutes=record.get("maxAgeMinutes"),
        stale_sources=stale_sources,
    )
