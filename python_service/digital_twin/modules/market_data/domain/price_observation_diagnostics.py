"""Compact daily counters: source evaluations are not delivered alerts."""
from collections import Counter
from copy import deepcopy
from datetime import timezone, timedelta

from .market_time_series import parse_timestamp

STATE_KEY = "marketPriceDiagnostics"
VERSION = "price-diagnostics-v1"


def price_observation_diagnostics(states):
    """Aggregate only stored counters; keep dates and sampling coverage explicit."""
    days = {}
    for snapshot in states:
        value = (snapshot.get("metadata") or {}).get(STATE_KEY) or {}
        if value.get("version") != VERSION:
            continue
        day = days.setdefault(value["dayKst"], {"dayKst": value["dayKst"], "accountCount": 0,
            "startedAt": value["startedAt"], "observedAt": value["observedAt"],
            "evaluationCounts": Counter(), "candidateCounts": Counter(), "exclusionCounts": Counter()})
        day["accountCount"] += 1
        day["startedAt"] = min(day["startedAt"], value["startedAt"])
        day["observedAt"] = max(day["observedAt"], value["observedAt"])
        for key in ("evaluationCounts", "candidateCounts", "exclusionCounts"):
            day[key].update(value.get(key) or {})
    return {"scope": "stored-counters-since-startedAt-per-KST-day", "evaluationUnit": "symbol-per-monitor-cycle",
            "status": "available" if days else "awaiting-observations", "days": [days[key] for key in sorted(days)]}


def record_observations(current, previous, paths):
    clock = parse_timestamp(current.get("generatedAt"))
    if not clock:
        return current
    old = ((previous.get("metadata") or {}).get(STATE_KEY) or {}) if current.get("accountId") == previous.get("accountId") else {}
    if old.get("observedAt") and clock <= parse_timestamp(old["observedAt"]):
        return {**current, "metadata": {**current.get("metadata", {}), STATE_KEY: deepcopy(old)}}
    day = clock.astimezone(timezone(timedelta(hours=9))).date().isoformat()
    state = deepcopy(old) if old.get("dayKst") == day and old.get("version") == VERSION else {
        "version": VERSION, "dayKst": day, "startedAt": current["generatedAt"],
        "evaluationCounts": {}, "candidateCounts": {}, "exclusionCounts": {}}
    if not state.get("observedAt") or clock > parse_timestamp(state["observedAt"]):
        counts = Counter(state["evaluationCounts"])
        counts.update(str(path.get("status") or "unknown") for path in paths.values())
        state.update(evaluationCounts=dict(counts), observedAt=current["generatedAt"],
                     evaluationUnit="symbol-per-monitor-cycle")
    return {**current, "metadata": {**current.get("metadata", {}), STATE_KEY: state}}


def record_candidates(state, events, outboxed, exclusions):
    diagnostics = deepcopy((state.get("metadata") or {}).get(STATE_KEY) or {})
    if (not diagnostics or (diagnostics.get("candidatesObservedAt")
            and parse_timestamp(diagnostics["candidatesObservedAt"]) >= parse_timestamp(state.get("generatedAt")))):
        return state
    account = state.get("accountId")
    source = [event for event in events if event.rule == "marketObservation" and event.account_id == account]
    queued = [event for event in outboxed if event.rule == "marketObservation" and event.account_id == account]
    counts = Counter(diagnostics.get("candidateCounts") or {})
    counts.update({"candidates": len(source), "outboxed": len(queued)})
    reasons = Counter(diagnostics.get("exclusionCounts") or {})
    specific = exclusions.get(account) or {}
    reasons.update(specific)
    other = max(0, len(source) - len(queued) - sum(specific.values()))
    if other:
        reasons["other-delivery-policy-or-duplicate"] += other
    diagnostics.update(candidateCounts=dict(counts), exclusionCounts=dict(reasons), candidatesObservedAt=state.get("generatedAt"))
    return {**state, "metadata": {**state.get("metadata", {}), STATE_KEY: diagnostics}}
