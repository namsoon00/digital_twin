"""Read one owned past run; never infer changed evidence from a code deployment."""
from copy import deepcopy
from time import monotonic

from digital_twin.modules.news_intelligence.contracts import source_route_opportunity
from ..domain.planning import identity


class ResearchRouteReader:
    def __init__(self, run_reader, enabled_routes=()):
        self.run_reader, self.enabled_routes, self.cache = run_reader, tuple(enabled_routes), {}

    def __call__(self, case):
        if (not self.enabled_routes or case.get("capability") != "research"
                or "official-filing" not in (case.get("researchRequest") or {}).get("sourceTypes", [])
                or case.get("researchAttempts", 0) >= 6):
            return {}
        previous = case.get("lastResearch") or {}
        run_id = (previous.get("result") or {}).get("runId")
        if not run_id or not previous.get("taskId"):
            return {}
        key = (case["accountId"], case["symbol"], case["caseId"], run_id, previous["taskId"],
               identity(case.get("questionContract", {})))
        cached = self.cache.get(key)
        if cached and monotonic() - cached[0] < 300:
            return deepcopy(cached[1])
        run = self.run_reader(run_id) or {}
        reference = {**{k: case[k] for k in ("caseId", "accountId", "symbol")},
                     "runId": run_id, "taskId": identity(previous["taskId"], "sources"),
                     "questionContract": case.get("questionContract", {})}
        result = source_route_opportunity(reference, run, self.enabled_routes)
        if len(self.cache) >= 128:
            self.cache.clear()
        self.cache[key] = (monotonic(), result)
        return deepcopy(result)
