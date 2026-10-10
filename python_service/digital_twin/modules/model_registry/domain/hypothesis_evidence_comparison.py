"""Compare authored evidence and its availability as separate observations."""

from typing import Mapping


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
    reasons = []
    for trace in traces:
        if trace.get("evidenceUsableForJudgement") is False or trace.get("freshnessStatus") in {"stale", "unavailable", "expired"}:
            reasons.append(str(trace.get("freshnessGateReason") or "규칙에 사용된 자료를 다시 확인해야 합니다."))
    for domain in required_domains:
        profile = profiles.get(domain) or {}
        # A currently matched trace has its own source eligibility assessment.
        # This may cover a model domain absent from the position-only profiles.
        if not profile and not any(trace.get("freshnessStatus") == "fresh"
                                   and trace.get("evidenceUsableForJudgement") is not False for trace in traces):
            reasons.append(domain + " 자료의 현재 사용 가능 상태를 확인하지 못했습니다.")
        if profile.get("judgementEvidenceUsable") is False or profile.get("freshnessStatus") in {"stale", "unavailable", "expired"}:
            reasons.append(str(profile.get("freshnessGateReason") or "필요한 자료의 유효기간이 지났습니다."))
    return {
        "state": "unavailable" if reasons else "usable",
        "reasons": list(dict.fromkeys(reasons)),
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
