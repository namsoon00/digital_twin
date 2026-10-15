"""Notify changes to materialized personal constraints, never select trades."""

from copy import deepcopy
from digital_twin.modules.model_registry.contracts import rule_notification_intent
from .rule_notification_event import VERSION, decision_state, fingerprint, rows


def _signature(rule, actions, conditions):
    return fingerprint({"ruleId": rule.get("id"), "actions": actions,
        "claim": (rule.get("claimContract") or {}).get("ruleFingerprint"),
        "criteria": [{k: c.get(k) for k in ("conditionId", "field", "operator", "expectedValue", "resolvedThreshold")}
                     for c in conditions]})


def policy_events(current, previous):
    brief, prior = current.get("decisionBrief") or {}, previous.get("decisionBrief") or {}
    if brief and (brief.get("authority") != "typedb-materialized-decision-effects" or any(
        brief.get(k) != current.get(k) for k in ("accountId", "symbol", "worldId", "inferenceGenerationId", "sourceAboxSnapshotId"))):
        return []
    scope = {k: current.get(k) or "" for k in ("accountId", "symbol", "worldId")}
    if not all(scope.values()) or not current.get("proofIdentityVerified"):
        return []
    current_rules = {r["id"]: r for r in rows(current.get("rules"))}
    prior_rules = {r["id"]: r for r in rows(previous.get("rules"))}
    result = []
    rule_ids = dict.fromkeys(r.get("ruleId") for r in rows(brief.get("restrictions")) + rows(prior.get("restrictions")))
    for rule_id in rule_ids:
        rule, old_rule = current_rules.get(rule_id) or {}, prior_rules.get(rule_id) or {}
        intent = rule_notification_intent(rule.get("claimContract"))
        if (not rule.get("nativeProof") or not intent or intent["purpose"] not in {"management-policy", "execution-constraint"}
                or "policy-changed" not in intent["notifyOn"] or intent["ruleId"] != rule_id):
            continue
        actions = sorted({r.get("action") for r in rows(brief.get("restrictions")) if r.get("ruleId") == rule_id})
        old_actions = sorted({r.get("action") for r in rows(prior.get("restrictions")) if r.get("ruleId") == rule_id})
        conditions = rows(rule.get("conditions"))
        release = not actions and bool(old_actions)
        if release:
            # Absence, stale data or a missing rule is never a policy release.
            if not (rule.get("nativeProof") and rule.get("matched") is False and any(
                c.get("matched") is False and c.get("judgementEvidenceUsable") is True
                and c.get("freshnessStatus") == "fresh" for c in conditions)):
                continue
        elif not actions or not any((c.get("matchedByTypeDB") is True or c.get("matched") is True)
                                   and c.get("judgementEvidenceUsable") is not False for c in conditions):
            continue
        signature = _signature(rule, actions, conditions)
        if old_actions == actions and signature == _signature(old_rule, old_actions, rows(old_rule.get("conditions"))):
            continue
        key = fingerprint({**scope, "claimId": intent["claimContractId"], "purpose": intent["purpose"]})
        old_event = (previous.get("notificationIntentContinuity") or {}).get(key) or {}
        if old_event.get("fingerprint") == signature:
            continue
        result.append({"version": VERSION, **scope, "eventId": "rule-policy:" + fingerprint([key, signature])[:24],
            "intentKey": key, "intent": intent, "purpose": intent["purpose"], "changeKind": "policy-changed",
            "policyState": "released" if release else "updated" if actions == old_actions else "applied",
            "inferenceGenerationId": current.get("inferenceGenerationId"), "sourceAboxSnapshotId": current.get("sourceAboxSnapshotId"),
            "transitionId": "", "lifecycleKey": "", "observedAt": current.get("observedAt"),
            "hypothesis": {}, "sourceRuleIds": [rule_id], "conditions": deepcopy(conditions),
            "ruleLabel": rule.get("label") or intent["expectedOutcome"],
            "blockedActions": actions, "previousBlockedActions": old_actions,
            "decisionImpact": {"status": "changed" if prior else "first-observation",
                               "previous": decision_state(previous), "current": decision_state(current)},
            "nextCheck": {"condition": intent["falsificationContract"], "conditions": deepcopy(conditions),
                          "trackingStatus": "rule-reevaluation", "tracking": []},
            "parentJobId": old_event.get("rootJobId") or "", "fingerprint": signature,
            "eligible": True, "reasonCode": "verified-management-policy-change"})
    return result
