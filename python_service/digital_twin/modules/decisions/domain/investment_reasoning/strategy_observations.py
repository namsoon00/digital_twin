"""Read native strategy phase proofs; absence is unknown, not an exit signal."""
from copy import deepcopy


def native_strategy_observations(identity, relation):
    graph = relation.get("graphStoreInference") or {}
    if any(not identity.get(key) or graph.get(key) != identity.get(key)
           for key in ("worldId", "inferenceGenerationId", "sourceAboxSnapshotId")):
        return []
    traces = {r.get("ruleId"): r for r in graph.get("traces") or [] if isinstance(r, dict)}
    strategies = {}
    for rule in relation.get("matchedRules") or []:
        response = rule.get("strategyResponse") or {}
        if response.get("version") != "rule-strategy-contract-v1" or response.get("mode") != "research-only":
            continue
        rid = rule.get("ruleId") or rule.get("rule_id")
        trace = traces.get(rid) or {}
        required = response.get("conditionIds") or []
        proved = {c.get("conditionId") for c in trace.get("matchedConditions") or []
                  if c.get("matchedByTypeDB") is True and c.get("judgementEvidenceUsable") is True
                  and c.get("freshnessStatus") not in {"stale", "expired", "missing", "unknown"}}
        phase = response.get("phase")
        if (trace.get("matched") is not True or rule.get("matched") is False
                or "strategy-market" not in required or "strategy-model-1" not in required
                or not set(required).issubset(proved)
                or phase not in {"setup", "entry", "invalidation", "exit"}):
            continue
        key = (response.get("strategyId"), response.get("contractFingerprint"))
        if not all(isinstance(v, str) and v for v in key):
            continue
        item = strategies.setdefault(key, {"strategyId": key[0], "contractFingerprint": key[1],
            "mode": "research-only", "confirmedPhases": [], "phaseProofs": [],
            "maximumHoldingSessions": response.get("maximumHoldingSessions"),
            "signalValiditySeconds": response.get("signalValiditySeconds"),
            "selectedMarketAction": None, "orderAuthorized": False, "tradeTimingQualified": False})
        if phase not in item["confirmedPhases"]:
            item["confirmedPhases"].append(phase)
            item["phaseProofs"].append({"phase": phase, "ruleId": rid,
                "sourceModelRuleIds": deepcopy(response.get("sourceModelRuleIds") or []),
                "conditions": deepcopy(trace.get("matchedConditions") or [])})
    result = []
    for item in strategies.values():
        item["confirmedPhases"].sort()
        item["unknownPhases"] = sorted({"setup", "entry", "invalidation", "exit"} - set(item["confirmedPhases"]))
        item["status"] = ("conflicting-conditions" if "entry" in item["confirmedPhases"]
            and set(item["confirmedPhases"]) & {"invalidation", "exit"} else "research-conditions-observed")
        result.append(item)
    return sorted(result, key=lambda r: (r["strategyId"], r["contractFingerprint"]))
