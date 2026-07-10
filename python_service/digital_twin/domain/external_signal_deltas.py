from copy import deepcopy
from typing import Dict, Iterable, Tuple

from .market_data import number


def _dict(value) -> Dict[str, object]:
    return value if isinstance(value, dict) else {}


def _series_value(item: Dict[str, object]):
    if not isinstance(item, dict):
        return None
    raw = item.get("value") if item.get("value") not in (None, "") else item.get("rate")
    if raw in (None, ""):
        return None
    return number(raw)


def _attach_macro_deltas(current: Dict[str, object], previous: Dict[str, object]) -> None:
    macro = _dict(current.get("macro"))
    previous_macro = _dict(previous.get("macro"))
    if not macro or not previous_macro:
        return
    current_series = _dict(macro.get("series"))
    previous_series = _dict(previous_macro.get("series"))
    for series_id, item in current_series.items():
        if not isinstance(item, dict):
            continue
        previous_item = _dict(previous_series.get(series_id))
        current_value = _series_value(item)
        previous_value = _series_value(previous_item)
        if current_value is None or previous_value is None:
            continue
        item["previousValue"] = previous_value
        item["deltaBp"] = (current_value - previous_value) * 100
        item["deltaPctPoint"] = current_value - previous_value
        if previous_item.get("date"):
            item["previousDate"] = str(previous_item.get("date") or "")
    if macro.get("yieldSpread10y2y") not in (None, "") and previous_macro.get("yieldSpread10y2y") not in (None, ""):
        spread = number(macro.get("yieldSpread10y2y"))
        previous_spread = number(previous_macro.get("yieldSpread10y2y"))
        macro["previousYieldSpread10y2y"] = previous_spread
        macro["yieldSpread10y2yDeltaBp"] = (spread - previous_spread) * 100
    if current_series:
        macro["series"] = current_series
    current["macro"] = macro


def _rate_from_entry(entry, base: str, quote: str):
    if isinstance(entry, dict):
        item_base = str(entry.get("base") or entry.get("baseCurrency") or base).upper().strip()
        item_quote = str(entry.get("quote") or entry.get("quoteCurrency") or quote).upper().strip()
        raw = entry.get("rate") if entry.get("rate") not in (None, "") else entry.get("value")
        rate = number(raw)
        if item_base == base and item_quote == quote and rate:
            return rate
        if item_base == quote and item_quote == base and rate:
            return 1 / rate
        return None
    if entry not in (None, ""):
        rate = number(entry)
        return rate if rate else None
    return None


def _fx_pair_from_entry(key: str, item) -> Tuple[str, str, str]:
    normalized_key = str(key or "").upper().replace("/", "").strip()
    if isinstance(item, dict):
        base = str(item.get("base") or item.get("baseCurrency") or "").upper().strip()
        quote = str(item.get("quote") or item.get("quoteCurrency") or "").upper().strip()
    else:
        base = ""
        quote = ""
    if not base and len(normalized_key) >= 6:
        base = normalized_key[:3]
    if not quote and len(normalized_key) >= 6:
        quote = normalized_key[3:6]
    if not base and len(normalized_key) == 3:
        base = normalized_key
    if not quote:
        quote = "KRW"
    return base, quote, base + quote if base and quote else normalized_key


def _previous_fx_rate(previous_rates: Dict[str, object], key: str, base: str, quote: str):
    candidates: Iterable[str] = [
        str(key or "").upper().replace("/", "").strip(),
        base + quote,
        base,
        quote + base,
    ]
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        rate = _rate_from_entry(previous_rates.get(candidate), base, quote)
        if rate is not None:
            return rate
    return None


def _attach_fx_deltas(current: Dict[str, object], previous: Dict[str, object]) -> None:
    rates = _dict(current.get("fxRates"))
    previous_rates = _dict(previous.get("fxRates"))
    if not rates or not previous_rates:
        return
    for key, item in rates.items():
        if not isinstance(item, dict):
            continue
        base, quote, pair = _fx_pair_from_entry(key, item)
        current_rate = _rate_from_entry(item, base, quote)
        previous_rate = _previous_fx_rate(previous_rates, key, base, quote)
        if current_rate is None or previous_rate is None:
            continue
        delta_krw = current_rate - previous_rate
        item["previousRate"] = previous_rate
        item["deltaKrw"] = delta_krw
        item["deltaPct"] = (delta_krw / previous_rate) * 100 if previous_rate else 0.0
        item["pair"] = str(item.get("pair") or pair)
    current["fxRates"] = rates


def external_signals_with_deltas(
    current_signals: Dict[str, object],
    previous_signals: Dict[str, object],
) -> Dict[str, object]:
    current = deepcopy(current_signals) if isinstance(current_signals, dict) else {}
    previous = previous_signals if isinstance(previous_signals, dict) else {}
    if not current or not previous:
        return current
    _attach_macro_deltas(current, previous)
    _attach_fx_deltas(current, previous)
    return current
