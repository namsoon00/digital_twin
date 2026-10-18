"""Observe native account-policy responses without requiring a forecast."""

from collections.abc import Mapping

from digital_twin.modules.model_registry.contracts import rule_notification_intent


VERSION = "typedb-management-observation-v1"


def mapping(value):
    return dict(value) if isinstance(value, Mapping) else {}


def management_only_observation_requested(context):
    """Identify the requested contract, never authorize its delivery."""
    payload = mapping(context)
    case = mapping(payload.get("investmentSubjectDecisionCase"))
    sources = [case, mapping(case.get("candidateSet")), mapping(case.get("synthesis")),
               mapping(payload.get("v2DecisionSynthesis"))]
    return bool(any(s.get("managementDecision") or s.get("management_decision") for s in sources)
                and not any(s.get("eligibleHypothesisIds") or s.get("eligible_hypothesis_ids")
                            or (s.get("graph_candidate_action") or s.get("graphCandidateAction") or "NO_ACTION") != "NO_ACTION"
                            for s in sources))


def management_observation_contract(context):
    """Recognize policy-only candidates; successful receipts own novelty."""
    payload = mapping(context)
    # Hypothesis-backed subjects retain their existing review/publication path.
    if not management_only_observation_requested(payload):
        return {}
    # The decisions package assembles syntheses using notification contracts.
    # Resolve this port after contract loading, not during module discovery.
    from digital_twin.modules.decisions.contracts import verified_management_decision
    management = verified_management_decision(payload)
    if not management:
        return {}
    relation = mapping(payload.get("ontologyRelationContext")) or mapping(
        mapping(payload.get("metadata")).get("ontologyRelationContext"))
    graph = mapping(relation.get("graphStoreInference"))
    def rule_id(row):
        return row.get("ruleId") or row.get("rule_id") or row.get("sourceRuleId")
    native_ids = {rule_id(r) for r in [*(graph.get("traces") or []), *(graph.get("relations") or [])]
                  if isinstance(r, Mapping)}
    rules = {rule_id(r): r for r in relation.get("matchedRules") or [] if isinstance(r, Mapping)}
    ids = sorted({r["ruleId"] for r in management["responses"]})
    for identity in ids:
        row = rules.get(identity) or {}
        intent = rule_notification_intent(row.get("claimContract") or row.get("claim_contract"))
        if (identity not in native_ids or not intent or intent["ruleId"] != identity
                or intent["purpose"] != "management-policy" or "policy-changed" not in intent["notifyOn"]):
            return {}
    return {"managementContractVersion": VERSION, "messageClass": "typedb-management-policy",
            "decisionEligibility": "management-policy", "ruleKind": "policy-constraint",
            "requiresHypothesis": False, "requiresAiJudgement": False, "requiresAiNarrative": False,
            "action": "NO_ACTION", "validationState": "typedb-policy-verified",
            **{key: management[key] for key in ("accountId", "symbol", "worldId", "sourceAboxSnapshotId", "inferenceGenerationId")},
            "managementDecisionId": management["decisionId"], "relationIds": ids,
            "selectedRuleLabel": "관리 기준 점검", "graphSource": relation.get("source"),
            "graphStore": relation.get("graphStore") or graph.get("graphStore"),
            "market": (relation.get("subject") or {}).get("market") or (relation.get("facts") or {}).get("market") or ""}
