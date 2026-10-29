"""Daily admission uses reported tokens plus explicit, conservative reservations."""
from copy import deepcopy
from datetime import timedelta, timezone

from .budget import AIControlBudgetWait, budget_state
from .planning import bounded

RESERVATION = 100_000
FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
POLICY = "paced-completion-v1"


def execution_pool(workload, prompt_version=""):
    if workload == "independent-observation":
        if prompt_version.startswith("independent-observation-retrieval-"):
            return "retrieval"
        if prompt_version.startswith(("independent-observation-v", "independent-observation-repair-",
                                      "independent-observation-review-", "research-assessment-review-")):
            return "completion"
    return "preparation"


def charged(row):
    return sum(row.get(key, 0) for key in ("input_tokens", "output_tokens", "reservedTokens", "unknownTokensAllowance"))


def allocation(settings, state, now, limit):
    """Release an hourly allowance with a small burst; carry unused capacity.

    Forty percent remains available for authored answers and their reviews.
    Retrieval may borrow unused preparation allowance, within its shared sixty
    percent ceiling. This never spends the protected completion allowance.
    Unknown historic charges
    conservatively belong to preparation, never to the protected completion pool.
    """
    now = now.astimezone(timezone.utc)
    released = min(limit, max(5 * RESERVATION,
        int(limit * (now.hour + 1) / 24) + min(limit // 20, 10 * RESERVATION)))
    total = sum(charged(row) for row in state.get("workloads", {}).values())
    pools = {key: charged(row) for key, row in state.get("pools", {}).items()}
    completion = pools.get("completion", 0)
    preparation = max(0, total - completion)
    other_preparation = max(0, preparation - pools.get("retrieval", 0))
    base_retrieval = released * 40 // 100
    # Leave two reservations for new source preparation; borrowing cannot
    # starve the collectors that supply tomorrow's evidence.
    retrieval_limit = max(base_retrieval, released * 60 // 100 - other_preparation - 2 * RESERVATION)
    return {"version": POLICY, "allocationRevision": "preparation-borrow-v2",
            "releasedTokens": released, "chargedTokens": total,
            "completionProtectedTokens": released * 40 // 100,
            "preparationLimit": released * 60 // 100, "preparationCharged": preparation,
            "retrievalBaseLimit": base_retrieval, "retrievalBorrowLimit": retrieval_limit - base_retrieval,
            "retrievalLimit": retrieval_limit, "retrievalCharged": pools.get("retrieval", 0),
            "nextReleaseAt": (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "pools": pools}


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
    failed_unknown = sum(row.get("failedUnknownTokensAllowance", 0) for row in rows.values())
    remaining = max(0, limit - measured - reserved - unknown) if enabled else None
    view = {"enabled": enabled, "dailyLimit": limit, "reportedTokens": measured,
            "reservedTokens": reserved, "unknownTokensAllowance": unknown,
            "failedUnknownTokensAllowance": failed_unknown,
            "otherUnknownTokensAllowance": max(0, unknown - failed_unknown),
            "remainingTokens": remaining, "reservationPerCall": RESERVATION,
            "exhausted": enabled and remaining < RESERVATION,
            "resetAt": budget_state(settings, 0, 0, now)["budgetResetAt"],
            "trackedSince": state.get("trackedSince"), "day": now.date().isoformat(),
            "workloads": [{"workload": name, **row} for name, row in sorted(rows.items())],
            "basis": "reported-input-plus-output; cache-and-reasoning-are-subsets",
            "scope": "project-ai-execution-gateway; from-trackedSince-only"}
    if enabled and settings.get("aiTokenBudgetPolicy") == POLICY:
        view["allocation"] = allocation(settings, state, now, limit)
    return view


def reserve(settings, state, workload, now, pool=None):
    view = usage_view(settings, state, now)
    if view["exhausted"]:
        raise AIControlBudgetWait("token", view["resetAt"])
    track_pool = pool is not None
    pool = pool or execution_pool(workload)
    if pool not in {"retrieval", "preparation", "completion"}:
        raise ValueError("invalid token allocation pool")
    limits = view.get("allocation")
    if limits:
        # Routing must leave at least two call reservations for author/review.
        runway = RESERVATION * (3 if pool == "retrieval" else 1)
        blocked = limits["chargedTokens"] + runway > limits["releasedTokens"]
        if pool != "completion":
            blocked |= limits["preparationCharged"] + RESERVATION > limits["preparationLimit"]
        if pool == "retrieval":
            blocked |= limits["retrievalCharged"] + RESERVATION > limits["retrievalLimit"]
        if blocked:
            raise AIControlBudgetWait("token-allocation", limits["nextReleaseAt"])
    state = deepcopy(state)
    state.setdefault("trackedSince", now.isoformat().replace("+00:00", "Z"))
    row = state.setdefault("workloads", {}).setdefault(workload, {})
    row["startedCalls"] = row.get("startedCalls", 0) + 1
    row["reservedTokens"] = row.get("reservedTokens", 0) + RESERVATION
    if track_pool:
        bucket = state.setdefault("pools", {}).setdefault(pool, {})
        bucket["reservedTokens"] = bucket.get("reservedTokens", 0) + RESERVATION
    return state


def settle(state, workload, usage, not_started=False, pool=None, failed=False):
    state = deepcopy(state)
    row = state["workloads"][workload]
    rows = [row]
    # Calls admitted before pool accounting retain their original reservation.
    # Do not manufacture a pool debit on a rolling deployment or late finish.
    if pool is not None:
        rows.append(state["pools"][pool])
    counts = usage_counts(usage)
    for row in rows:
        if row.get("reservedTokens", 0) < RESERVATION:
            raise ValueError("token reservation missing")
        row["reservedTokens"] -= RESERVATION
        if not_started:
            row["notStartedCalls"] = row.get("notStartedCalls", 0) + 1
        elif counts is None:
            row["unknownUsageCalls"] = row.get("unknownUsageCalls", 0) + 1
            row["unknownTokensAllowance"] = row.get("unknownTokensAllowance", 0) + RESERVATION
            if failed:
                row["failedUnknownTokensAllowance"] = row.get("failedUnknownTokensAllowance", 0) + RESERVATION
        else:
            row["reportedUsageCalls"] = row.get("reportedUsageCalls", 0) + 1
            for key, value in counts.items():
                row[key] = row.get(key, 0) + value
    return state
