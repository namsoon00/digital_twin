"""Raw crypto-market scheduling facts.

This module deliberately does not choose an investment action.  It turns a
CoinGecko observation into bounded, replayable transition provenance so the
RuleBox can evaluate the current ABox only when a BTC/ETH market state has
meaningfully changed.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import timezone
from typing import Dict, Iterable, List, Mapping

from .external_signal_quality import parse_iso_datetime
from .instrument_profiles import BTC_SENSITIVE_SYMBOLS
from .market_data import known_stock, number
from .portfolio import Position


CRYPTO_ALERT_SYMBOLS = ("BTC", "ETH")
COIN_ID_BY_SYMBOL = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
}
DEFAULT_CHANGE_THRESHOLDS = {
    "24h": 3.0,
    "7d": 4.0,
}
ESCALATION_MULTIPLIER = 2.0
CRYPTO_MARKET_SNAPSHOT_SCHEMA_VERSION = 1


def _clean_symbol(value: object) -> str:
    return str(value or "").upper().strip()


def _timestamp_key(value: object) -> float:
    parsed = parse_iso_datetime(str(value or ""))
    if not parsed:
        return -1.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def _latest_timestamp(values: Iterable[object]) -> str:
    rows = [str(value or "").strip() for value in values if str(value or "").strip()]
    return max(rows, key=_timestamp_key) if rows else ""


def crypto_market_snapshot(external_signals: Mapping[str, object] = None) -> Dict[str, object]:
    """Build the compact, portfolio-independent CoinGecko cache contract."""

    payload = external_signals if isinstance(external_signals, Mapping) else {}
    markets_value = payload.get("cryptoMarkets")
    markets = {
        str(coin_id): deepcopy(dict(item))
        for coin_id, item in markets_value.items()
        if isinstance(markets_value, Mapping) and isinstance(item, Mapping)
    } if isinstance(markets_value, Mapping) else {}
    fetched_at = str(payload.get("cryptoFetchedAt") or "").strip()
    if not fetched_at:
        fetched_at = _latest_timestamp(
            item.get("fetchedAt") or item.get("lastUpdated")
            for item in markets.values()
        )
    last_attempt_at = str(payload.get("cryptoLastAttemptAt") or fetched_at or "").strip()
    source_as_of = str(payload.get("cryptoSourceAsOf") or "").strip()
    if not source_as_of:
        source_as_of = _latest_timestamp(
            item.get("lastUpdated") or item.get("fetchedAt")
            for item in markets.values()
        )
    statuses_value = payload.get("statuses")
    status_rows = [
        deepcopy(dict(item))
        for item in statuses_value
        if isinstance(statuses_value, list)
        and isinstance(item, Mapping)
        and str(item.get("source") or "") == "CoinGecko"
    ][-5:] if isinstance(statuses_value, list) else []
    if not markets and not fetched_at and not last_attempt_at and not status_rows:
        return {}
    return {
        "schemaVersion": CRYPTO_MARKET_SNAPSHOT_SCHEMA_VERSION,
        "provider": "CoinGecko",
        "dataset": "coins/markets",
        "markets": markets,
        "fetchedAt": fetched_at,
        "lastAttemptAt": last_attempt_at,
        "sourceAsOf": source_as_of,
        "statusRows": status_rows,
    }


def _normalized_crypto_market_snapshot(payload: Mapping[str, object] = None) -> Dict[str, object]:
    value = payload if isinstance(payload, Mapping) else {}
    if not isinstance(value.get("markets"), Mapping):
        return crypto_market_snapshot(value)
    signals = {
        "cryptoMarkets": value.get("markets"),
        "cryptoFetchedAt": value.get("fetchedAt"),
        "cryptoLastAttemptAt": value.get("lastAttemptAt"),
        "cryptoSourceAsOf": value.get("sourceAsOf"),
        "statuses": value.get("statusRows") if isinstance(value.get("statusRows"), list) else [],
    }
    return crypto_market_snapshot(signals)


def combine_crypto_market_snapshots(*snapshots: Mapping[str, object]) -> Dict[str, object]:
    """Combine latest market facts with the latest independent request state."""

    rows = [_normalized_crypto_market_snapshot(item) for item in snapshots]
    rows = [item for item in rows if item]
    if not rows:
        return {}
    market_rows = [item for item in rows if item.get("markets")]
    market_source = max(
        market_rows,
        key=lambda item: _timestamp_key(item.get("fetchedAt") or item.get("sourceAsOf")),
    ) if market_rows else {}
    attempt_source = max(
        rows,
        key=lambda item: _timestamp_key(item.get("lastAttemptAt") or item.get("fetchedAt")),
    )
    status_rows = attempt_source.get("statusRows") if isinstance(attempt_source.get("statusRows"), list) else []
    if not status_rows and market_source:
        status_rows = market_source.get("statusRows") if isinstance(market_source.get("statusRows"), list) else []
    return {
        "schemaVersion": CRYPTO_MARKET_SNAPSHOT_SCHEMA_VERSION,
        "provider": "CoinGecko",
        "dataset": "coins/markets",
        "markets": deepcopy(market_source.get("markets") or {}),
        "fetchedAt": str(market_source.get("fetchedAt") or ""),
        "lastAttemptAt": str(attempt_source.get("lastAttemptAt") or attempt_source.get("fetchedAt") or ""),
        "sourceAsOf": str(market_source.get("sourceAsOf") or ""),
        "statusRows": deepcopy(status_rows),
    }


def merge_crypto_market_snapshot(
    external_signals: Dict[str, object],
    snapshot: Mapping[str, object],
    cache_state: str = "dedicated",
) -> bool:
    """Merge a global CoinGecko snapshot without disturbing other providers."""

    if not isinstance(external_signals, dict):
        return False
    current = crypto_market_snapshot(external_signals)
    combined = combine_crypto_market_snapshots(current, snapshot)
    if not combined:
        return False
    before = {
        "markets": deepcopy(external_signals.get("cryptoMarkets") or {}),
        "fetchedAt": str(external_signals.get("cryptoFetchedAt") or ""),
        "lastAttemptAt": str(external_signals.get("cryptoLastAttemptAt") or ""),
        "sourceAsOf": str(external_signals.get("cryptoSourceAsOf") or ""),
    }
    external_signals["cryptoMarkets"] = deepcopy(combined.get("markets") or {})
    external_signals["cryptoFetchedAt"] = str(combined.get("fetchedAt") or "")
    external_signals["cryptoLastAttemptAt"] = str(combined.get("lastAttemptAt") or "")
    external_signals["cryptoSourceAsOf"] = str(combined.get("sourceAsOf") or "")
    statuses = external_signals.get("statuses") if isinstance(external_signals.get("statuses"), list) else []
    external_signals["statuses"] = [
        item for item in statuses
        if not isinstance(item, Mapping) or str(item.get("source") or "") != "CoinGecko"
    ] + deepcopy(combined.get("statusRows") or [])
    external_signals["cryptoCache"] = {
        "schemaVersion": CRYPTO_MARKET_SNAPSHOT_SCHEMA_VERSION,
        "provider": "CoinGecko",
        "dataset": "coins/markets",
        "state": str(cache_state or "dedicated"),
    }
    after = {
        "markets": external_signals["cryptoMarkets"],
        "fetchedAt": external_signals["cryptoFetchedAt"],
        "lastAttemptAt": external_signals["cryptoLastAttemptAt"],
        "sourceAsOf": external_signals["cryptoSourceAsOf"],
    }
    return before != after


def _number_setting(settings: Mapping[str, object], key: str, fallback: float) -> float:
    try:
        value = float(str((settings or {}).get(key) or "").strip())
    except (TypeError, ValueError):
        value = fallback
    return max(0.1, min(100.0, abs(value)))


def crypto_thresholds(settings: Mapping[str, object] = None) -> Dict[str, float]:
    return {
        "24h": _number_setting(settings or {}, "externalBitcoinChange24hPct", DEFAULT_CHANGE_THRESHOLDS["24h"]),
        "7d": _number_setting(settings or {}, "externalBitcoinChange7dPct", DEFAULT_CHANGE_THRESHOLDS["7d"]),
    }


def crypto_markets_by_symbol(external_signals: Mapping[str, object] = None) -> Dict[str, Dict[str, object]]:
    markets = (external_signals or {}).get("cryptoMarkets") if isinstance(external_signals, Mapping) else {}
    if not isinstance(markets, Mapping):
        return {}
    rows: Dict[str, Dict[str, object]] = {}
    for coin_id, payload in markets.items():
        if not isinstance(payload, Mapping):
            continue
        symbol = _clean_symbol(payload.get("symbol"))
        if not symbol:
            normalized_id = str(coin_id or "").lower().strip()
            symbol = next((key for key, value in COIN_ID_BY_SYMBOL.items() if value == normalized_id), "")
        if symbol not in CRYPTO_ALERT_SYMBOLS:
            continue
        item = dict(payload)
        item.setdefault("coinId", str(coin_id or COIN_ID_BY_SYMBOL[symbol]).lower())
        item["symbol"] = symbol
        rows[symbol] = item
    return rows


def crypto_freshness(external_signals: Mapping[str, object] = None) -> Dict[str, object]:
    value = (external_signals or {}).get("cryptoFreshness") if isinstance(external_signals, Mapping) else {}
    return dict(value) if isinstance(value, Mapping) else {}


def crypto_freshness_is_usable(external_signals: Mapping[str, object] = None) -> bool:
    freshness = crypto_freshness(external_signals)
    # Backward-compatible snapshots have no dedicated crypto freshness yet.
    # Their source row is still a usable input for the migration transition.
    if not freshness:
        return bool(crypto_markets_by_symbol(external_signals))
    return str(freshness.get("status") or "").strip().lower() == "fresh"


def crypto_market_positions(external_signals: Mapping[str, object] = None) -> List[Position]:
    """Expose BTC/ETH as virtual watchlist subjects for graph read models.

    These are not portfolio holdings and are never persisted into the account
    snapshot.  They only let an already materialized crypto-asset ABox source
    be rendered through the same graph-backed alert contract as a watchlist
    subject.
    """

    freshness = crypto_freshness(external_signals)
    result: List[Position] = []
    for symbol, item in crypto_markets_by_symbol(external_signals).items():
        info = known_stock(symbol)
        price = number(item.get("price"))
        volume = number(item.get("volume24h"))
        result.append(Position(
            symbol=symbol,
            name=str(item.get("name") or info.get("name") or symbol),
            market="CRYPTO",
            currency="USD",
            current_price=price,
            change_rate=number(item.get("change24h")),
            quote_source=str(item.get("provider") or "CoinGecko"),
            quote_status="CoinGecko market observation",
            data_quality="actual" if crypto_freshness_is_usable(external_signals) else "partial",
            updated_at=str(item.get("lastUpdated") or item.get("fetchedAt") or freshness.get("fetchedAt") or ""),
            source_as_of=str(item.get("lastUpdated") or item.get("fetchedAt") or freshness.get("fetchedAt") or ""),
            source_fetched_at=str(item.get("fetchedAt") or freshness.get("fetchedAt") or ""),
            freshness_status=str(freshness.get("status") or "unknown"),
            freshness_age_minutes=number(freshness.get("ageMinutes")),
            freshness_max_age_minutes=number(freshness.get("maxAgeMinutes")),
            volume=volume,
            trading_value=price * volume if price and volume else 0.0,
            sector=str(info.get("sector") or "디지털자산"),
            source="watchlist",
        ))
    return result


def _band(value: float, threshold: float) -> str:
    magnitude = abs(number(value))
    if magnitude < threshold:
        return "neutral"
    direction = "up" if value > 0 else "down"
    severity = "major" if magnitude >= threshold * ESCALATION_MULTIPLIER else "watch"
    return direction + ":" + severity


def _transition_reason(previous: str, current: str) -> str:
    if previous == "neutral":
        return "threshold-crossed"
    previous_direction = previous.split(":", 1)[0]
    current_direction = current.split(":", 1)[0]
    if previous_direction != current_direction:
        return "direction-changed"
    if previous.endswith(":watch") and current.endswith(":major"):
        return "severity-escalated"
    return "state-changed"


def crypto_market_transitions(
    previous_signals: Mapping[str, object] = None,
    current_signals: Mapping[str, object] = None,
    settings: Mapping[str, object] = None,
) -> List[Dict[str, object]]:
    """Return only threshold entry, reversal, or escalation transitions.

    Leaving a threshold band is intentionally not a delivery trigger.  The
    next native TypeDB generation clears the old relation, while this policy
    avoids sending a second notification merely because a move normalized.
    """

    if not crypto_freshness_is_usable(current_signals):
        return []
    thresholds = crypto_thresholds(settings)
    previous = crypto_markets_by_symbol(previous_signals)
    current = crypto_markets_by_symbol(current_signals)
    transitions: List[Dict[str, object]] = []
    for symbol in CRYPTO_ALERT_SYMBOLS:
        current_item = current.get(symbol)
        if not current_item:
            continue
        previous_item = previous.get(symbol) or {}
        for horizon, field in (("24h", "change24h"), ("7d", "change7d")):
            threshold = thresholds[horizon]
            before = _band(number(previous_item.get(field)), threshold)
            after_value = number(current_item.get(field))
            after = _band(after_value, threshold)
            if after == "neutral" or after == before:
                continue
            direction, severity = after.split(":", 1)
            transitions.append({
                "symbol": symbol,
                "coinId": str(current_item.get("coinId") or COIN_ID_BY_SYMBOL[symbol]),
                "name": str(current_item.get("name") or known_stock(symbol).get("name") or symbol),
                "horizon": horizon,
                "field": field,
                "direction": direction,
                "severity": severity,
                "changePct": round(after_value, 4),
                "thresholdPct": threshold,
                "previousBand": before,
                "currentBand": after,
                "transition": _transition_reason(before, after),
                "observedAt": str(
                    current_item.get("lastUpdated")
                    or current_item.get("fetchedAt")
                    or crypto_freshness(current_signals).get("fetchedAt")
                    or ""
                ),
                "signature": ":".join([symbol, horizon, after]),
            })
    return transitions


def crypto_sensitive_symbols(positions: Iterable[Position]) -> List[str]:
    symbols: List[str] = []
    for position in positions or []:
        if position.is_cash():
            continue
        symbol = _clean_symbol(position.symbol)
        sector = str(position.sector or "").lower()
        name = str(position.name or "").lower()
        if not symbol:
            continue
        if (
            symbol in BTC_SENSITIVE_SYMBOLS
            or any(token in sector for token in ("디지털자산", "crypto", "bitcoin", "비트코인"))
            or any(token in name for token in ("strategy", "bitcoin", "crypto", "스트래티지", "비트코인"))
        ) and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def crypto_transition_targets(transitions: Iterable[Mapping[str, object]], positions: Iterable[Position]) -> List[str]:
    direct = []
    for transition in transitions or []:
        symbol = _clean_symbol((transition or {}).get("symbol"))
        if symbol in CRYPTO_ALERT_SYMBOLS and symbol not in direct:
            direct.append(symbol)
    return direct + [symbol for symbol in crypto_sensitive_symbols(positions) if symbol not in direct]
