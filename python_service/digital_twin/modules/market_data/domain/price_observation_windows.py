"""Bounded source-clock price paths for factual alerts, never trade signals."""
from copy import deepcopy
from datetime import timedelta
from math import isfinite

from .market_time_series import parse_timestamp, iso_utc, market_session_date

VERSION = "price-observation-windows-v1"
STATE_KEY = "marketPriceObservations"
MAX_GAP_SECONDS = 180
WINDOWS = (3, 15, 60)


def finite(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (ValueError, TypeError):
        return None


def positions(state):
    result = {}
    for group, role in (("positions", "holding"), ("watchlist", "watchlist")):
        values = state.get(group) or {}
        for item in values.values() if isinstance(values, dict) else values:
            symbol = str(item.get("symbol") or "").upper()
            if symbol and symbol != "CASH" and symbol not in result:
                result[symbol] = (item, role)
    return result


def source_point(item, observed_at):
    now, clock = parse_timestamp(observed_at), parse_timestamp(item.get("source_as_of"))
    price = finite(item.get("current_price"))
    state = str(item.get("source_timestamp_state") or "")
    quality = str(item.get("data_quality") or "").lower()
    freshness = str(item.get("freshness_status") or "").lower()
    # A poll time or last-close reference is not a new trade observation.
    if not clock or not now or price is None or price <= 0:
        return None, "missing-source-clock-or-price"
    if state not in {"provider", "exchange", "provider-timestamp"}:
        return None, "unverified-source-clock"
    if quality not in {"actual", "live", "real"} or freshness not in {"fresh", "live", "real-time"}:
        return None, "reference-or-unusable-quote"
    age = (now - clock).total_seconds()
    if age < 0 or age > MAX_GAP_SECONDS:
        return None, "source-clock-outside-window"
    return {"at": iso_utc(clock.isoformat()), "price": price}, "available"


def scope_for(item, point):
    return [str(item.get(k) or "") for k in ("currency", "quote_source", "market_session", "price_history_adjustment")] + [
        market_session_date(point["at"], item.get("market"), item.get("currency")), finite(item.get("previous_close"))]


def advance_paths(current, previous):
    """Save at most 90 source minute observations per current subject (75m)."""
    if current.get("accountId") != previous.get("accountId"):
        previous = {}
    before = (previous.get("metadata") or {}).get(STATE_KEY) or {}
    result = {}
    for symbol, (item, _role) in positions(current).items():
        old = deepcopy(before.get(symbol) or {})
        point, status = source_point(item, current.get("generatedAt"))
        if not point:
            result[symbol] = {**old, "version": VERSION, "status": status}
            continue
        scope = scope_for(item, point)
        rows = old.get("points", []) if old.get("version") == VERSION and old.get("scope") == scope else []
        # Bootstrap from one captured previous quote; never from the poll clock.
        if not rows:
            old_item = positions(previous).get(symbol, ({}, ""))[0]
            seed, _ = source_point(old_item, previous.get("generatedAt"))
            if seed and scope_for(old_item, seed) == scope and seed["at"] < point["at"]:
                rows = [seed]
        if rows and point["at"] < rows[-1]["at"]:
            result[symbol] = {**old, "status": "out-of-order-source"}
            continue
        if rows and point["at"] == rows[-1]["at"]:
            result[symbol] = {**old, "status": "unchanged-source" if point == rows[-1] else "source-revision-without-new-clock"}
            continue
        if rows and (parse_timestamp(point["at"]) - parse_timestamp(rows[-1]["at"])).total_seconds() > MAX_GAP_SECONDS:
            rows = []  # No short-window claim across missing observations or reopening.
        cutoff = parse_timestamp(point["at"]) - timedelta(minutes=75)
        rows = [r for r in rows if parse_timestamp(r["at"]) >= cutoff]
        if rows and rows[-1]["at"][:16] == point["at"][:16]:
            rows[-1] = point
        else:
            rows.append(point)
        result[symbol] = {"version": VERSION, "status": "available", "scope": scope, "points": rows[-90:]}
    return result


def price_windows(path):
    if path.get("status") != "available":
        return []
    rows = path.get("points") or []
    if len(rows) < 2:
        return []
    last, result = rows[-1], []
    now = parse_timestamp(last["at"])
    for minutes in WINDOWS:
        # Never extrapolate an incomplete interval. Keep the real elapsed span.
        eligible = [r for r in rows[:-1]
                    if minutes * 60 <= (now - parse_timestamp(r["at"])).total_seconds() <= minutes * 60 + min(90, minutes * 30)]
        if not eligible:
            continue
        start = eligible[-1]
        points = [r for r in rows if r["at"] >= start["at"]]
        if any((parse_timestamp(b["at"]) - parse_timestamp(a["at"])).total_seconds() > MAX_GAP_SECONDS
               for a, b in zip(points, points[1:])):
            continue
        elapsed = (now - parse_timestamp(start["at"])).total_seconds()
        result.append({"windowMinutes": minutes, "elapsedSeconds": elapsed, "startAt": start["at"], "endAt": last["at"],
            "startPrice": start["price"], "endPrice": last["price"],
            "changePct": (last["price"] / start["price"] - 1) * 100,
            "sampleCount": len(points), "points": deepcopy(points), "maxGapSeconds": MAX_GAP_SECONDS})
    return result
