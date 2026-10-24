"""Bounded, point-in-time price facts for display; never alert triggers."""
from copy import deepcopy
from datetime import timedelta
from statistics import median

from .market_time_series import parse_timestamp, market_timezone
from .market_hours import source_session_observation
from .price_observation_windows import finite, positions, source_point, price_windows

STATE_KEY = "marketPriceContext"
VERSION = "price-context-v1"
MAX_SAMPLES = 96  # Per window: at most one non-overlapping sample per session/hour.
MIN_SAMPLES = 20


def change(price, anchor):
    return (price / anchor - 1) * 100


def provider_session_prices(item, point):
    stage = (item.get("market_signal_coverage") or {}).get("ccnl") or {}
    session = source_session_observation(str(item.get("market") or ""), point["at"], point["at"],
                                         provider_session=str(item.get("market_session") or ""))
    if not (stage.get("dataQuality") == "actual"
            and session.get("marketSession") == "regular"
            and str((stage.get("values") or {}).get("symbol") or "").upper() == str(item.get("symbol") or "").upper()
            and stage.get("sourceTimestampState") == item.get("source_timestamp_state")
            and parse_timestamp(stage.get("sourceAsOf")) == parse_timestamp(point["at"])
            and finite((stage.get("values") or {}).get("currentPrice")) == point["price"]
            and stage.get("marketSession") == "regular"):
        return {}
    values = {key: finite((stage.get("sessionPrices") or {}).get(key)) for key in ("open", "high", "low")}
    if (all(value is not None and value > 0 for value in values.values())
            and values["low"] <= min(point["price"], values["open"])
            and values["high"] >= max(point["price"], values["open"])):
        return values
    return {}


def day_context(item, point, old, scope):
    previous = old.get("day") or {}
    clock = parse_timestamp(point["at"])
    local = clock.astimezone(market_timezone(item.get("market"), item.get("currency")))
    key = [local.date().isoformat(), str(item.get("market_session") or "")]
    if previous.get("key") != key:
        previous = {}
    authoritative = provider_session_prices(item, point)
    price = point["price"]
    day = {"key": key, "firstAt": previous.get("firstAt", point["at"]),
           "high": max(price, previous.get("high", price)), "low": min(price, previous.get("low", price))}
    basis = "provider-session" if authoritative else "observed-session"
    measured = authoritative or day
    result = {"status": "available", "basis": basis, "date": key[0], "session": key[1],
              "startAt": day["firstAt"], "endAt": point["at"], "source": scope[1],
              "high": measured["high"], "low": measured["low"],
              "fromHighPct": change(price, measured["high"]), "fromLowPct": change(price, measured["low"])}
    prior_range = previous.get("providerPrices") if authoritative else previous
    if prior_range:
        result["newHigh"] = measured["high"] > prior_range["high"]
        result["newLow"] = measured["low"] < prior_range["low"]
    if authoritative:
        day["providerPrices"] = authoritative
        result.update(open=authoritative["open"], sinceOpenPct=change(price, authoritative["open"]))
        close = finite(item.get("previous_close"))
        trade = (item.get("market_signal_coverage") or {}).get("ccnl") or {}
        reported = finite((trade.get("values") or {}).get("changeRate"))
        if close is not None and close > 0 and reported is not None and abs(change(price, close) - reported) <= .05:
            result.update(previousClose=close, previousCloseBasis="snapshot-cross-checked-with-trade",
                          gapPct=change(authoritative["open"], close), sinceClosePct=change(price, close))
    return day, result


def window_context(path, old_samples, item):
    """Compare older disjoint intervals within 10% of the actual duration."""
    windows = price_windows(path)
    samples = deepcopy(old_samples)
    comparisons = []
    if not windows:
        return samples, comparisons
    cutoff = parse_timestamp(windows[0]["endAt"]) - timedelta(days=20)
    for key in list(samples):
        samples[key] = [row for row in samples[key] if parse_timestamp(row[0]) >= cutoff][-MAX_SAMPLES:]
    for window in windows:
        key = str(window["windowMinutes"])
        history = samples.get(key, [])
        session = str(item.get("market_session") or "")
        # Samples: end time, actual seconds, absolute change, local hour, session.
        older = [row for row in history if parse_timestamp(row[0]) <= parse_timestamp(window["startAt"])
                 and (len(row) < 5 or row[4] == session)
                 and abs(row[1] - window["elapsedSeconds"]) <= window["elapsedSeconds"] * .1]
        dates = {row[3][:10] for row in older}
        comparison = {"windowMinutes": window["windowMinutes"], "elapsedSeconds": window["elapsedSeconds"],
                      "sampleCount": len(older), "sessionDays": len(dates), "minimumSamples": MIN_SAMPLES,
                      "durationTolerancePct": 10, "basis": "retained-disjoint-comparable-duration",
                      "status": "insufficient-history"}
        if len(older) >= MIN_SAMPLES and len(dates) >= 3:
            absolute = abs(window["changePct"])
            typical = median(row[2] for row in older)
            comparison.update(status="available", medianAbsolutePct=typical,
                              historicalMinSeconds=min(row[1] for row in older),
                              historicalMaxSeconds=max(row[1] for row in older),
                              largerThanPct=sum(row[2] < absolute for row in older) / len(older) * 100,
                              oldestAt=older[0][0], newestAt=older[-1][0])
            if typical > 0:
                comparison["medianMultiple"] = absolute / typical
        comparisons.append(comparison)
        end = parse_timestamp(window["endAt"])
        local = end.astimezone(market_timezone(item.get("market"), item.get("currency")))
        slot = local.date().isoformat() + ":" + str(local.hour)
        if (not any(row[3] == slot and (len(row) < 5 or row[4] == session) for row in history)
                and (not history or parse_timestamp(history[-1][0]) <= parse_timestamp(window["startAt"]))):
            history.append([window["endAt"], window["elapsedSeconds"], abs(window["changePct"]), slot, session])
        samples[key] = history[-MAX_SAMPLES:]
    return samples, comparisons


def advance_price_context(current, previous, paths):
    prior = ((previous.get("metadata") or {}).get(STATE_KEY) or {}) if current.get("accountId") == previous.get("accountId") else {}
    states, facts = {}, {}
    for symbol, (item, _) in positions(current).items():
        old = deepcopy(prior.get(symbol) or {})
        point, status = source_point(item, current.get("generatedAt"))
        if not point:
            states[symbol] = old
            facts[symbol] = {"version": VERSION, "status": status}
            continue
        scope = [str(item.get(key) or "") for key in ("currency", "quote_source", "market_session", "price_history_adjustment")]
        prior_scope = old.get("scope") or []
        if old.get("version") != VERSION or len(prior_scope) != 4 or any(prior_scope[i] != scope[i] for i in (0, 1, 3)):
            old = {}
        elif prior_scope[2] != scope[2]:
            # A session change resets today's range, but keeps labelled history.
            old["day"] = {}
        for rows in (old.get("samples") or {}).values():
            for row in rows:
                if len(row) == 4:
                    row.append(prior_scope[2])
        if old.get("lastAt") and parse_timestamp(old["lastAt"]) >= parse_timestamp(point["at"]):
            states[symbol] = old
            facts[symbol] = {"version": VERSION, "status": "unchanged-or-older-source"}
            continue
        day, daily = day_context(item, point, old, scope)
        path = paths.get(symbol) or {}
        cutoff = parse_timestamp(point["at"]) - timedelta(days=20)
        retained = {key: [row for row in rows if cutoff <= parse_timestamp(row[0]) < parse_timestamp(point["at"])][-MAX_SAMPLES:]
                    for key, rows in (old.get("samples") or {}).items()}
        samples, comparisons = window_context(path, retained, item)
        # A previous 15-minute range is a sampled range, never a support/resistance claim.
        ranges = [window for window in price_windows(path) if window["windowMinutes"] == 15]
        interval = {}
        if ranges:
            window = ranges[0]
            values = [row["price"] for row in window["points"][:-1]]
            interval = {"startAt": window["startAt"], "endAt": window["points"][-2]["at"],
                        "elapsedSeconds": window["elapsedSeconds"], "high": max(values), "low": min(values),
                        "sampleCount": len(values), "break": "above" if point["price"] > max(values) else
                        "below" if point["price"] < min(values) else "inside"}
        facts[symbol] = {"version": VERSION, "status": "available", "symbol": symbol,
                         "sourceAsOf": point["at"], "currentPrice": point["price"],
                         "daily": daily, "priorRange": interval, "comparisons": comparisons}
        states[symbol] = {"version": VERSION, "scope": scope, "lastAt": point["at"], "day": day, "samples": samples}
    return states, facts
