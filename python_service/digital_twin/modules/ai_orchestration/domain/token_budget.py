"""Daily admission uses reported tokens plus explicit, conservative reservations."""
from copy import deepcopy

from .budget import AIControlBudgetWait, budget_state
from .planning import bounded

RESERVATION = 100_000
FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


def usage_counts(value):
    if not isinstance(value, dict):
        return None
    valid = {key: value[key] for key in FIELDS
             if type(value.get(key)) is int and 0 <= value[key] <= 10**12}
    if not all(key in valid for key in ("input_tokens", "output_tokens")):
        return None
    for detail, total in (("cached_input_tokens", "input_tokens"), ("reasoning_output_tokens", "output_tokens")):
        if valid.get(detail, 0) > valid[total]:
            return None
    return valid


def usage_view(settings, state, now):
    limit = bounded(settings.get("aiDailyTokenBudget"), 1_000_000, 0, 100_000_000)
    enabled = str(settings.get("aiTokenBudgetEnabled", "true")).lower() not in {"false", "0", "off"}
    rows = state.get("workloads", {})
    measured = sum(row.get("input_tokens", 0) + row.get("output_tokens", 0) for row in rows.values())
    reserved = sum(row.get("reservedTokens", 0) for row in rows.values())
    unknown = sum(row.get("unknownTokensAllowance", 0) for row in rows.values())
    remaining = max(0, limit - measured - reserved - unknown) if enabled else None
    return {"enabled": enabled, "dailyLimit": limit, "reportedTokens": measured,
            "reservedTokens": reserved, "unknownTokensAllowance": unknown,
            "remainingTokens": remaining, "reservationPerCall": RESERVATION,
            "exhausted": enabled and remaining < RESERVATION,
            "resetAt": budget_state(settings, 0, 0, now)["budgetResetAt"],
            "trackedSince": state.get("trackedSince"), "day": now.date().isoformat(),
            "workloads": [{"workload": name, **row} for name, row in sorted(rows.items())],
            "basis": "reported-input-plus-output; cache-and-reasoning-are-subsets",
            "scope": "project-ai-execution-gateway; from-trackedSince-only"}


def reserve(settings, state, workload, now):
    view = usage_view(settings, state, now)
    if view["exhausted"]:
        raise AIControlBudgetWait("token", view["resetAt"])
    state = deepcopy(state)
    state.setdefault("trackedSince", now.isoformat().replace("+00:00", "Z"))
    row = state.setdefault("workloads", {}).setdefault(workload, {})
    row["startedCalls"] = row.get("startedCalls", 0) + 1
    row["reservedTokens"] = row.get("reservedTokens", 0) + RESERVATION
    return state


def settle(state, workload, usage, not_started=False):
    state = deepcopy(state)
    row = state["workloads"][workload]
    row["reservedTokens"] -= RESERVATION
    counts = usage_counts(usage)
    if not_started:
        row["notStartedCalls"] = row.get("notStartedCalls", 0) + 1
    elif counts is None:
        row["unknownUsageCalls"] = row.get("unknownUsageCalls", 0) + 1
        row["unknownTokensAllowance"] = row.get("unknownTokensAllowance", 0) + RESERVATION
    else:
        row["reportedUsageCalls"] = row.get("reportedUsageCalls", 0) + 1
        for key, value in counts.items():
            row[key] = row.get(key, 0) + value
    return state
