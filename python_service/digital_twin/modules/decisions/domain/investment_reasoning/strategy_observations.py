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
        if (response.get("version"), response.get("mode")) not in {
                ("rule-strategy-contract-v1", "research-only"), ("rule-strategy-contract-v2", "live-observation"),
                ("rule-strategy-contract-v3", "live-observation")}:
            continue
        rid = rule.get("ruleId") or rule.get("rule_id")
        trace = traces.get(rid) or {}
        required = response.get("conditionIds") or []
        conditions = list(trace.get("matchedConditions") or [])
        if response.get("version") == "rule-strategy-contract-v3":
            # The portfolio trace may refer to one exact public-market proof.
            # Never substitute an unrelated rule with similar measurements.
            for condition in list(conditions):
                lineage = condition.get("premiseLineage") or {}
                shared = traces.get("shared.premise." + str(rid)) or {}
                if (condition.get("conditionId") == "shared-market-premise:" + str(rid)
                        and condition.get("matchedByTypeDB") is True and condition.get("judgementEvidenceUsable") is True
                        and condition.get("freshnessStatus") not in {"stale", "expired", "missing", "unknown"}
                        and lineage.get("originalRuleId") == rid and lineage.get("status") == "available"
                        and lineage.get("premiseProofId")
                        and lineage["premiseProofId"] == (shared.get("inferenceTraceId") or shared.get("traceId") or shared.get("id"))
                        and shared.get("matched") is not False):
                    conditions.extend(shared.get("matchedConditions") or [])
        proved = {c.get("conditionId") for c in conditions
                  if c.get("matchedByTypeDB") is True and c.get("judgementEvidenceUsable") is True
                  and c.get("freshnessStatus") not in {"stale", "expired", "missing", "unknown"}}
        phase = response.get("phase")
        # Materialized positive traces omit 'matched'; only policy resolution
        # traces carry matched=false. Require the native trace identity and all
        # positive condition proofs, rather than a synthetic matched=true flag.
        trace_proven = trace.get("matched") is True or (
            response.get("version") == "rule-strategy-contract-v3" and trace.get("matched") is not False
            and bool(trace.get("id") or trace.get("traceId") or trace.get("inferenceTraceId"))
            and rule.get("matched") is True)
        if (not trace_proven or rule.get("matched") is False
                or "strategy-market" not in required or "strategy-model-1" not in required
                or not set(required).issubset(proved)
                or phase not in {"setup", "entry", "invalidation", "exit"}):
            continue
        key = (response.get("strategyId"), response.get("contractFingerprint"))
        if not all(isinstance(v, str) and v for v in key):
            continue
        item = strategies.setdefault(key, {"strategyId": key[0], "contractFingerprint": key[1],
            "mode": response["mode"], "confirmedPhases": [], "phaseProofs": [],
            "maximumHoldingSessions": response.get("maximumHoldingSessions"),
            "signalValiditySeconds": response.get("signalValiditySeconds"),
            "selectedMarketAction": None, "orderAuthorized": False, "tradeTimingQualified": False})
        if phase not in item["confirmedPhases"]:
            item["confirmedPhases"].append(phase)
            item["phaseProofs"].append({"phase": phase, "ruleId": rid,
                "sourceModelRuleIds": deepcopy(response.get("sourceModelRuleIds") or []),
                "conditions": deepcopy(conditions)})
    result = []
    for item in strategies.values():
        item["confirmedPhases"].sort()
        item["unknownPhases"] = sorted({"setup", "entry", "invalidation", "exit"} - set(item["confirmedPhases"]))
        item["status"] = ("conflicting-conditions" if "entry" in item["confirmedPhases"]
            and set(item["confirmedPhases"]) & {"invalidation", "exit"} else
            "live-conditions-observed" if item["mode"] == "live-observation" else "research-conditions-observed")
        result.append(item)
    return sorted(result, key=lambda r: (r["strategyId"], r["contractFingerprint"]))
