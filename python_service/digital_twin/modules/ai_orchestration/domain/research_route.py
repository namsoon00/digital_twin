"""Consume a source-owner route opportunity once within existing research limits."""


def opportunity(case, action):
    row = (case.get("researchSourceState") or {}).get("sourceRouteOpportunity") or {}
    if (row.get("version") != "question-source-route-v1" or not row.get("key")
            or row.get("authority") != "collection-retry-only" or row.get("remaining") != 1):
        return ""
    used = ((case.get("researchRepeat") or {}).get("sourceRoutes") or {}).get(row["key"], {})
    if used.get(action):
        return ""
    if action == "scheduled":
        plan = case.get("researchPlan") or {}
        if plan.get("sourceRouteOpportunityKey") != row["key"] or plan.get("action") != "research":
            return ""
    return row["key"]
