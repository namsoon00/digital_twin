"""Bounded execution opportunities; this policy never judges market evidence."""

VERSION = "central-task-fairness-v3-work-attention"
SCOPE = "central-task-scheduler"
CYCLE = ("recovery", "market-observe", "observe", "research", "market-observe", "observe", "research-plan")
CAPABILITIES = ("market-observe", "observe", "research", "research-plan")
AGING_MINUTES = 20


def dispatch_order(state):
    cursor = int(state.get("nextSlot", 0)) % len(CYCLE) if state.get("version") == VERSION else 0
    return [((cursor + offset) % len(CYCLE), CYCLE[(cursor + offset) % len(CYCLE)])
            for offset in range(len(CYCLE))]


def dispatched(state, slot, lane, task_id, capability, now, expired_lease=""):
    receipt = {"version": VERSION, "lane": lane, "claimedAt": now,
               "recoveredLeaseUntil": expired_lease}
    counts = dict(state.get("laneDispatchCounts") or {})
    counts[lane] = counts.get(lane, 0) + 1
    return {"laneDispatchCounts": counts, "version": VERSION, "nextSlot": (slot + 1) % len(CYCLE),
            "dispatchCount": int(state.get("dispatchCount", 0)) + 1,
            "recoveredLeaseCount": int(state.get("recoveredLeaseCount", 0)) + bool(expired_lease),
            "lastDispatch": {**receipt, "taskId": task_id, "capability": capability}}, receipt
