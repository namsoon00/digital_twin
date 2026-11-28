"""Paired paper executions of the frozen price-recovery scenario."""

from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
from zoneinfo import ZoneInfo

from .notification_timing import clock, number


KIND = "price-recovery-paper"
VERSION = "price-recovery-paper-v1"
MARKETS = {"KR": "Asia/Seoul", "US": "America/New_York"}
HOLDING_WINDOW = timedelta(minutes=30)
QUOTE_WINDOW = timedelta(minutes=5)
ARMS = ("first-setup", "wait-for-entry", "cash")
ENDING_SIGNALS = {"exit", "invalidation", "conflict"}


def utc(stamp):
    return stamp.isoformat().replace("+00:00", "Z")


def study_window(market, start_date, end_date):
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if end == date.max or not 0 <= (end - start).days < 31:
        raise ValueError("Choose between 1 and 31 calendar days.")
    zone = ZoneInfo(MARKETS[market])
    return (
        clock(datetime.combine(start, time.min, zone).isoformat()),
        clock(datetime.combine(end + timedelta(days=1), time.min, zone).isoformat()),
    )


def study_identity(request):
    return sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def measured_signal(row, study):
    source = clock(row.get("sourceObservedAt"))
    assessed = clock(row.get("assessedAt"))
    recorded = clock(row.get("recordedAt"))
    if (row.get("status") != "accepted" or not source or not assessed or not recorded
            or not source <= assessed <= recorded
            or any(row.get(k) != study[k] for k in (
                "accountId", "symbol", "worldId", "deploymentId", "contractFingerprint", "strategyId"))
            or not all(row.get(k) for k in ("observationId", "sourceAboxSnapshotId", "inferenceGenerationId",
                                            "sourceRuleIds", "state"))
            or recorded - source > timedelta(seconds=study["signalValiditySeconds"])):
        return None
    proofs = set(row["sourceRuleIds"])
    phases = {phase for phase, rule in study["phaseRuleIds"].items() if rule in proofs}
    signal = row.get("signal")
    if (len(phases) != len(proofs) or phases != set(row.get("confirmedPhases") or [])
            or (signal == "conflict" and not ("entry" in phases and phases & {"exit", "invalidation"}))
            or (signal != "conflict" and signal not in phases)):
        return None
    session = recorded.astimezone(ZoneInfo(MARKETS[study["market"]])).date().isoformat()
    if session != row.get("session"):
        return None
    return {k: row[k] for k in ("observationId", "sourceAboxSnapshotId", "inferenceGenerationId",
                               "sourceRuleIds", "sourceObservedAt", "assessedAt", "state",
                               "session", "signal")} | {"knownAt": utc(recorded)}


def session_trials(signals):
    """One allocation per symbol, release and session, armed by its first setup."""
    sessions = {}
    for signal in sorted(signals, key=lambda s: (clock(s["knownAt"]), s["observationId"])):
        sessions.setdefault(signal["session"], []).append(signal)
    for session in sorted(sessions):
        rows = sessions[session]
        setup = next((row for row in rows if row["signal"] == "setup" and row["state"] == "observing"), None)
        if not setup:
            continue
        opened = clock(setup["knownAt"])
        deadline = opened + HOLDING_WINDOW
        later = [row for row in rows if opened <= clock(row["knownAt"]) <= deadline]
        ending = next((row for row in later if row["signal"] in ENDING_SIGNALS), None)
        exit_at = clock(ending["knownAt"]) if ending else deadline
        confirmation = next((row for row in later if row["signal"] == "entry"
                             and row["state"] == "entry-review"
                             and opened < clock(row["knownAt"]) < min(deadline - QUOTE_WINDOW, exit_at)), None)
        yield {"session": session, "setup": setup, "confirmation": confirmation,
               "ending": ending, "exitAt": utc(exit_at)}


def usable_quote(row, study, session):
    source, available = clock(row.get("sourceAsOf")), clock(row.get("generatedAt"))
    price = number(row.get("currentPrice"))
    session_fact = row.get("sourceSessionObservation") or {}
    expected_currency = "KRW" if study["market"] == "KR" else "USD"
    if (not source or not available or not source <= available <= source + QUOTE_WINDOW
            or not price or price <= 0 or not row.get("provider") or not row.get("bucketAt")
            or row.get("dataQuality") not in {"actual", "live", "fresh"}
            or row.get("observationGranularity") != "3m"
            or row.get("symbol") != study["symbol"] or row.get("currency") != expected_currency
            or row.get("marketSessionDate") != session
            or session_fact.get("version") != "source-session-observation-v1"
            or session_fact.get("market") != study["market"]
            or any(session_fact.get(key) != "regular" for key in ("marketSession", "sourceSession", "captureSession"))
            or clock(session_fact.get("sourceAsOf")) != source or clock(session_fact.get("capturedAt")) != available
            or session_fact.get("providerReference") is not False):
        return None
    return {"sourceAsOf": utc(source), "availableAt": utc(available), "price": price,
            "provider": row["provider"], "currency": expected_currency, "bucketAt": row["bucketAt"]}


def net_return(entry, exit_price, cost):
    return (exit_price * (1 - cost) / (entry * (1 + cost)) - 1) * 100


def paper_execution(signal, exit_at, quotes, cost):
    if not signal:
        return {"state": "cash", "reason": "no-confirmed-entry-in-window",
                "netReturnPct": 0, "worstObservedReturnPct": 0}
    start = clock(signal["knownAt"])
    entry = next((q for q in quotes if start < clock(q["sourceAsOf"])
                  and clock(q["availableAt"]) <= start + QUOTE_WINDOW
                  and clock(q["availableAt"]) < exit_at), None)
    result = {"entrySignalId": signal["observationId"], "entryTargetAt": utc(start), "exitTargetAt": utc(exit_at)}
    if not entry:
        return {**result, "state": "unfilled", "reason": "no-eligible-quote-before-entry-deadline"}
    buy = {**entry, "side": "buy", "effectivePrice": round(entry["price"] * (1 + cost), 8)}
    exit_quote = next((q for q in quotes if exit_at < clock(q["sourceAsOf"])
                       and clock(q["availableAt"]) <= exit_at + QUOTE_WINDOW), None)
    if not exit_quote:
        return {**result, "entry": buy, "state": "unmeasurable", "reason": "no-eligible-quote-after-exit-signal"}
    sell = {**exit_quote, "side": "sell", "effectivePrice": round(exit_quote["price"] * (1 - cost), 8)}
    path_prices = [q["price"] for q in quotes if clock(entry["availableAt"]) <= clock(q["availableAt"])
                   <= clock(exit_quote["availableAt"]) and clock(q["sourceAsOf"]) >= clock(entry["sourceAsOf"])]
    raw = (sell["price"] / buy["price"] - 1) * 100
    return {**result, "state": "closed", "entry": buy, "exit": sell,
            "grossReturnPct": round(raw, 6), "netReturnPct": round(net_return(buy["price"], sell["price"], cost), 6),
            "worstObservedReturnPct": round(min(0, net_return(buy["price"], min(path_prices), cost)), 6)}


def settle_trial(trial, study, observations):
    quotes = sorted((q for row in observations if (q := usable_quote(row, study, trial["session"]))),
                    key=lambda q: (clock(q["availableAt"]), clock(q["sourceAsOf"]), q["bucketAt"]))
    exit_at = clock(trial["exitAt"])
    cost = study["costBpsPerSide"] / 10000
    return {**trial, "trialId": study_identity([study["protocol"], study["scenarioKey"],
                                               trial["session"], study["costBpsPerSide"]]),
            "exitReason": trial["ending"]["signal"] if trial["ending"] else "time-limit",
            "eligibleQuoteCount": len(quotes),
            "arms": {
                "first-setup": paper_execution(trial["setup"], exit_at, quotes, cost),
                "wait-for-entry": paper_execution(trial["confirmation"], exit_at, quotes, cost),
                "cash": {"state": "cash", "netReturnPct": 0, "worstObservedReturnPct": 0},
            }}


def comparison(trials):
    paired = [trial["arms"] for trial in trials if all(
        arm["state"] in {"closed", "cash"} for arm in trial["arms"].values())]
    returns = [row["wait-for-entry"]["netReturnPct"] - row["first-setup"]["netReturnPct"] for row in paired]
    return {
        "trialCount": len(trials), "pairedCount": len(paired),
        "arms": {name: {
            "stateCounts": {state: sum(t["arms"][name]["state"] == state for t in trials)
                            for state in ("closed", "cash", "unfilled", "unmeasurable")},
            "pairedMeanNetReturnPct": round(sum(r[name]["netReturnPct"] for r in paired) / len(paired), 6)
            if paired else None,
            "pairedWorstObservedReturnPct": min((r[name]["worstObservedReturnPct"] for r in paired), default=None),
        } for name in ARMS},
        "waitMinusFirstPairedMeanPctPoint": round(sum(returns) / len(returns), 6) if returns else None,
        "missedProfitableFirstEntries": sum(r["wait-for-entry"]["state"] == "cash"
                                            and r["first-setup"]["netReturnPct"] > 0 for r in paired),
        "avoidedUnprofitableFirstEntries": sum(r["wait-for-entry"]["state"] == "cash"
                                               and r["first-setup"]["netReturnPct"] < 0 for r in paired),
    }
