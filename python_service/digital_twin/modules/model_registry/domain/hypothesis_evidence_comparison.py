"""Compare authored evidence and its availability as separate observations."""

from typing import Mapping
from digital_twin.modules.model_registry.domain.hypothesis_data_readiness import trace_checks, summary, VERSION


def condition_evidence_rows(traces):
    """Resolve source assertion IDs using the exact conditions proven by TypeDB.

    Derived relation hashes and generation-local IDs are not evidence meaning.
    Numeric measurements are retained for explanation, not used as a new market
    threshold: the RuleBox condition owns whether a measurement is significant.
    """
    result = {}
    for trace in traces:
        rule_id = trace.get("ruleId")
        for condition in trace.get("matchedConditions") or []:
            if not isinstance(condition, Mapping):
                continue
            identity = condition.get("relationId")
            condition_id = condition.get("conditionId")
            if not identity or not condition_id or not rule_id:
                continue
            shape = condition.get("ruleConditionShape")
            shape = dict(shape) if isinstance(shape, Mapping) else {}
            result[str(identity)] = {
                "ruleId": rule_id,
                "conditionId": condition_id,
                "matchedConditionIds": [condition_id],
                "relationType": condition.get("relationType") or shape.get("relationType"),
                "evidenceRole": condition.get("role") or shape.get("role"),
                "field": condition.get("field") or shape.get("field"),
                "operator": condition.get("operator") or shape.get("operator"),
                "expectedValue": condition.get("expectedValue", condition.get("value", shape.get("value"))),
                "observedValue": condition.get("observedValue"),
                "label": condition.get("label") or trace.get("label") or "확인된 조건",
                "source": condition.get("source"),
                "observedAt": condition.get("observedAt"),
                "judgementEvidenceUsable": condition.get("judgementEvidenceUsable"),
                "semanticKey": condition.get("semanticKey") or condition.get("semantic_key"),
            }
    return result


def availability_for_traces(traces, profiles, required_domains):
    """A missing/stale input limits use; it is not opposing market evidence."""
    checks = trace_checks(traces, profiles, required_domains)
    assessment = summary(checks)
    issues = [issue for row in checks for issue in row["issues"]]
    return {
        "version": VERSION, "state": "unavailable" if issues else "usable",
        "reasons": list(dict.fromkeys((issue["ruleLabel"] + ": " if issue["ruleLabel"] else "") + issue["reason"] for issue in issues)),
        "issues": issues, "ruleChecks": checks,
        "readyRuleIds": assessment["readyRuleIds"], "blockedRuleIds": assessment["blockedRuleIds"],
    }


def availability_change(previous, current):
    before = previous.get("state")
    after = current.get("state")
    if before not in {"usable", "unavailable"} or after not in {"usable", "unavailable"} or before == after:
        return {}
    return {
        "previousState": before,
        "currentState": after,
        "kind": "data-restored" if after == "usable" else "data-unavailable",
        "summary": "판단에 필요한 자료를 다시 사용할 수 있습니다." if after == "usable" else "판단에 필요한 자료를 다시 확인해야 합니다.",
        "reasons": list(current.get("reasons") or []),
    }
