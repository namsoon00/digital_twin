"""Explain existing graph decisions without selecting or authorizing a trade.

Policy restrictions, investment candidates and requests for more evidence have
different meanings. This frozen read model keeps those meanings separate; it
does not evaluate RuleBox predicates or replace hypothesis qualification.
"""

from copy import deepcopy
import math

from digital_twin.modules.decisions.domain.notification_ai_context import is_graph_backed_relation_context


VERSION = "rulebox-decision-brief-v1"
ACTIONS = {"BUY", "ADD", "HOLD", "TRIM", "SELL", "AVOID"}


def mapping(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return [v for v in value if isinstance(v, dict)] if isinstance(value, (list, tuple)) else []


def strings(value):
    return list(dict.fromkeys(v for v in value if isinstance(v, str) and v)) if isinstance(value, (list, tuple)) else []


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def resolved_rule_threshold(condition, facts):
    """Resolve a parameter only from this frozen ABox, never current settings.

An authored fallback is not proof that the inference used that fallback.
Keep unresolved parameters explicit instead of displaying a guessed default.
"""
    expected = condition.get("expectedValue", condition.get("value"))
    if isinstance(expected, dict):
        field = expected.get("field")
        value = facts.get(field) if isinstance(field, str) else None
        return {"field": field or "", "value": number(value),
                "source": "same-generation-fact" if number(value) is not None else "unresolved"}
    return {"field": "", "value": number(expected),
            "source": "authored-literal" if number(expected) is not None else "unresolved"}


def _identity(context, relation, case):
    graph = mapping(relation.get("graphStoreInference"))
    subject = mapping(relation.get("subject"))
    identity = {"accountId": case.get("accountId"), "symbol": case.get("symbol"),
                "subjectCaseId": case.get("subjectCaseId"),
                "inferenceGenerationId": case.get("inferenceGenerationId"),
                "sourceAboxSnapshotId": case.get("sourceAboxSnapshotId"),
                "worldId": relation.get("worldId") or graph.get("worldId")}
    if not all(identity.values()) or not is_graph_backed_relation_context(relation):
        return {}
    if not subject.get("symbol") or not context.get("accountId"):
        return {}
    if any(not (relation.get(key) or graph.get(key)) for key in ("inferenceGenerationId", "sourceAboxSnapshotId")):
        return {}
    for key, values in {
        "accountId": [context.get("accountId"), relation.get("accountId"), graph.get("accountId")],
        "symbol": [subject.get("symbol"), graph.get("symbol")],
        "inferenceGenerationId": [relation.get("inferenceGenerationId"), graph.get("inferenceGenerationId")],
        "sourceAboxSnapshotId": [relation.get("sourceAboxSnapshotId"), graph.get("sourceAboxSnapshotId")],
        "subjectCaseId": [context.get("investmentSubjectDecisionCaseId")],
        "worldId": [graph.get("worldId"), case.get("worldId")],
    }.items():
        if any(value and value != identity[key] for value in values):
            return {}
    if relation.get("generationAligned") is False:
        return {}
    return identity


def rulebox_decision_brief(context):
    relation = mapping(context.get("ontologyRelationContext")) or mapping(mapping(context.get("metadata")).get("ontologyRelationContext"))
    case = mapping(context.get("investmentSubjectDecisionCase"))
    identity = _identity(context, relation, case)
    if not identity:
        return {}
    envelope = mapping(relation.get("actionEnvelope"))
    eligibility = {r.get("ruleId"): r for r in rows(envelope.get("inferenceEligibilityAssessments"))}
    allowed = set(strings(case.get("allowedActions")))
    blocked = set(strings(case.get("blockedActions"))) & set(strings(envelope.get("blockedActions"))) & ACTIONS
    hypotheses = rows(mapping(relation.get("hypothesisSet")).get("hypotheses"))
    if not hypotheses:
        hypotheses = rows(mapping(mapping(relation.get("investmentBrain")).get("hypothesisSet")).get("hypotheses"))
    eligible_hypotheses = set(strings(case.get("eligibleHypothesisIds")))
    execution_hypotheses = set(strings(case.get("executionEligibleHypothesisIds")))
    candidates, restrictions, checks, unavailable = [], [], [], []
    for item in rows(relation.get("matchedRules")):
        rule_id = item.get("ruleId") or item.get("rule_id")
        eligible = eligibility.get(rule_id, {})
        if not rule_id or item.get("matched") is False:
            continue
        effect = item.get("decisionEffect") or item.get("decision_effect")
        kind = item.get("relationType") or item.get("relation_type") or item.get("type")
        action = item.get("candidateAction") or ""
        entry = {"ruleId": rule_id, "label": item.get("label") or "",
                 "relationType": kind, "decisionEffect": effect,
                 "evidenceRole": item.get("evidenceRole") or "",
                 "ruleKind": mapping(item.get("knowledgeBasis")).get("ruleKind") or "",
                 "invalidationContract": deepcopy(mapping(item.get("ruleInvalidationContract"))),
                 "nextChecks": strings(item.get("nextChecks")),
                 "invalidationConditions": strings(item.get("weakenConditions"))}
        predictive_action = (entry["ruleKind"] in {"predictive-hypothesis", "predictive"}
                             and action in ACTIONS and effect in {"support", "constrain"}
                             and kind != "REQUIRES_NEXT_CHECK" and not (action == "HOLD" and effect != "support"))
        reasons = []
        if (eligible.get("status") != "eligible" or eligible.get("evidenceUsableForJudgement") is not True
                or eligible.get("freshnessStatus") != "fresh"):
            reasons.append("unusable-rule-evidence")
        if (item.get("referenceOnly") or item.get("reference_only")
                or mapping(item.get("knowledgeBasis")).get("decisionEligibility") == "reference-only"):
            reasons.append("reference-only-rule")
        if predictive_action:
            action_reasons = [*reasons]
            if action not in allowed:
                action_reasons.append("outside-allowed-actions")
            if action in blocked:
                action_reasons.append("blocked-action")
            if action_reasons:
                unavailable.append({**entry, "action": action, "exclusionReasons": action_reasons,
                                    "eligibility": {k: eligible.get(k) for k in
                                                    ("status", "freshnessStatus", "reason", "evidenceUsableForJudgement")}})
        if reasons:
            continue
        for code in strings(item.get("blockedActions")):
            if code in blocked:
                restrictions.append({**entry, "action": code})
        # A next check is not a HOLD thesis. A constrained HOLD only limits
        # another action; it does not positively support keeping the position.
        if kind == "REQUIRES_NEXT_CHECK" or effect == "defer" or (action == "HOLD" and effect != "support"):
            checks.append(entry)
        elif (entry["ruleKind"] in {"predictive-hypothesis", "predictive"}
              and action in ACTIONS and action in allowed and action not in blocked and effect in {"support", "constrain"}):
            candidates.append({**entry, "action": action})
    admitted_candidates = []
    for entry in candidates:
        linked = [h for h in hypotheses if entry["ruleId"] in strings(h.get("supportingRuleIds"))
                  and h.get("hypothesisId") in eligible_hypotheses]
        if not linked:
            checks.append({**entry, "candidateExcludedReason": "no-eligible-linked-hypothesis"})
            unavailable.append({**entry, "exclusionReasons": ["no-eligible-linked-hypothesis"]})
            continue
        entry["hypotheses"] = [{"id": h.get("hypothesisId"),
            "qualification": mapping(h.get("qualification")).get("status") or "unknown",
            "outcomeCount": number(mapping(h.get("qualification")).get("decisiveOutcomeCount")),
            "executionEligible": h.get("hypothesisId") in execution_hypotheses} for h in linked]
        entry["executionEligible"] = bool(linked) and any(h.get("hypothesisId") in execution_hypotheses for h in linked)
        admitted_candidates.append(entry)
    candidates = admitted_candidates
    excluded = {}
    for entry in unavailable:
        # An audit of excluded graph-authored actions, never another candidate
        # list. Keep qualification separate from source freshness: a new quote
        # alone cannot qualify an unvalidated hypothesis.
        entry["hypotheses"] = [{"id": h.get("hypothesisId"),
            "qualification": mapping(h.get("qualification")).get("status") or "unknown",
            "outcomeCount": number(mapping(h.get("qualification")).get("decisiveOutcomeCount")),
            "executionEligible": h.get("hypothesisId") in execution_hypotheses}
            for h in hypotheses if entry["ruleId"] in strings(h.get("supportingRuleIds"))]
        excluded[(entry["ruleId"], entry["action"])] = entry
    if not (candidates or restrictions or checks or excluded):
        return {}
    candidate_actions = strings([r["action"] for r in candidates])
    restricted_actions = strings([r["action"] for r in restrictions])
    return {"version": VERSION, "authority": "typedb-materialized-decision-effects", **identity,
            "action": "NO_ACTION", "finalActionSelected": False,
            "status": "restricted-review" if restrictions else "candidate-review" if candidates else "evidence-review",
            "candidateActions": candidate_actions, "blockedActions": restricted_actions,
            "candidates": candidates, "restrictions": restrictions, "checks": checks,
            "unavailableCandidates": list(excluded.values()),
            "qualificationPending": any(not r["executionEligible"] for r in candidates),
            "comparisonRequired": len(candidate_actions) > 1,
            "dispositionCode": case.get("dispositionCode") or "",
            "executionDisposition": case.get("executionDisposition") or "",
            "validity": "same-account-subject-generation-abox"}
