"""Customer notification events bound to frozen RuleBox claims and receipts.

The native graph/lifecycle authorizes facts. This projection accounts for their
meaning and delivery novelty; it never selects an investment action.
"""

from copy import deepcopy
from hashlib import sha256
import json

from digital_twin.modules.model_registry.contracts import rule_notification_intent


VERSION = "rule-notification-event-v1"


def rows(value):
    return [v for v in value or [] if isinstance(v, dict)] if isinstance(value, (tuple, list)) else []


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()


def decision_state(snapshot):
    brief = snapshot.get("decisionBrief") or {}
    if not brief:
        return {}
    return {"candidateActions": sorted(set(brief.get("candidateActions") or [])),
            "blockedActions": sorted(set(brief.get("blockedActions") or [])),
            "qualificationPending": brief.get("qualificationPending") is True,
            "finalActionSelected": False}


def _hypotheses(current, previous, transition):
    continuity = (previous.get("hypothesisContinuity") or {}).get(transition.get("lifecycleKey")) or {}
    candidates = rows(current.get("hypotheses")) + rows(previous.get("hypotheses")) + rows(continuity.get("hypotheses"))
    ids, rule_ids = set(transition.get("hypothesisIds") or []), set(transition.get("sourceRuleIds") or [])
    exact = [h for h in candidates if h.get("id") in ids]
    return exact or [h for h in candidates if rule_ids & set(h.get("ruleIds") or [])]


def _changes(transition):
    delta = transition.get("evidenceDelta") or {}
    changes = []
    for role, action, key in (
        ("support", "added", "addedSupportingEvidenceKeys"),
        ("support", "removed", "removedSupportingEvidenceKeys"),
        ("counter", "added", "addedCounterEvidenceKeys"),
        ("counter", "removed", "removedCounterEvidenceKeys"),
    ):
        proofs = [p for p in delta.get(key) or [] if not any(x in str(p) for x in (":unresolved-slot:", ":migration-slot:"))]
        details = [r for r in rows(transition.get("evidenceChanges")) if r.get("role") == role and r.get("change") == action]
        if proofs or details:
            changes.append({"kind": role + "-" + action, "proofKeys": proofs, "evidence": deepcopy(details)})
    return changes


def _kind(transition, changes):
    state = transition.get("currentState")
    if state == "invalidated":
        return "invalidated"
    if changes:
        return changes[0]["kind"] if len(changes) == 1 else "mixed-evidence"
    if state == "observed":
        return "reactivated" if transition.get("previousState") in {"expired", "invalidated"} else "observed"
    return ""


def _next_checks(hypothesis, current, previous, transition):
    claim = hypothesis.get("claimContract") or {}
    contract = deepcopy(claim.get("outcomeContract") or {})
    # Registration receipts originate in the outcomes writer, not this renderer.
    delivered_ids = {r.get("episodeId") for entry in (previous.get("notificationIntentContinuity") or {}).values()
                     for r in rows(entry.get("tracking"))}
    tracking = [r for r in rows(current.get("outcomeTracking"))
                if r.get("claimContractId") == claim.get("claimContractId")
                and (r.get("episodeId") in delivered_ids or
                     (r.get("inferenceGenerationId") == current.get("inferenceGenerationId")
                      and r.get("sourceAboxSnapshotId") == current.get("sourceAboxSnapshotId")))]
    return {"condition": claim.get("falsificationContract") or "",
            "outcomeContract": contract, "sourceRuleIds": list(transition.get("sourceRuleIds") or []),
            "tracking": tracking, "trackingStatus": "registered" if tracking else "registration-unconfirmed",
            "lifecycleKey": transition.get("lifecycleKey") or ""}


def _event(current, previous, transition, hypothesis, intent):
    changes = _changes(transition)
    kind = _kind(transition, changes)
    if not kind or kind not in intent["notifyOn"] or intent["purpose"] == "internal-only":
        return None
    scope = {k: current.get(k) or "" for k in ("accountId", "symbol", "worldId")}
    if not all(scope.values()):
        return None
    key = fingerprint({**scope, "lifecycleKey": transition.get("lifecycleKey"),
                       "claimId": intent["claimContractId"], "claimFingerprint": intent["claimFingerprint"],
                       "purpose": intent["purpose"]})
    before, after = decision_state(previous), decision_state(current)
    basis = transition.get("changeBasis") or {}
    comparisons = deepcopy(rows(basis.get("ruleComparisons")))
    # Fingerprints exclude generation, polling clocks, wording and current
    # account valuations. Native semantic changes carry their own proof keys.
    semantic = {"kind": kind, "claim": intent["claimContractId"],
                "state": transition.get("currentState"),
                "roles": [{"kind": c["kind"], "proofKeys": sorted(c["proofKeys"]),
                           "rules": sorted({str((r.get("evidence") or {}).get("ruleId") or "") for r in c["evidence"]})} for c in changes],
                "rules": sorted(hypothesis.get("ruleIds") or []), "decision": after}
    # Repeated price/window samples do not create a new evidence role. The
    # lifecycle's semantic proof keys, not polling timestamps, own materiality.
    old_event = (previous.get("notificationIntentContinuity") or {}).get(key) or {}
    signature = fingerprint(semantic)
    conclusion = fingerprint({"rules": sorted(hypothesis.get("ruleIds") or []),
                              "counterRules": sorted(hypothesis.get("counterRuleIds") or []),
                              "evidence": sorted(hypothesis.get("evidenceIds") or []),
                              "counterEvidence": sorted(hypothesis.get("counterEvidenceIds") or []), "decision": after})
    # A repeated unaltered reactivation is not a new customer conclusion.
    duplicate = old_event.get("fingerprint") == signature or (kind == "reactivated" and not changes
        and old_event.get("conclusionFingerprint") == conclusion and old_event.get("active") is True)
    return {"version": VERSION, "eventId": "rule-notice:" + fingerprint([key, transition.get("transitionId"), signature])[:24],
            "intentKey": key, "intent": intent, "purpose": intent["purpose"], "changeKind": kind,
            **scope, "inferenceGenerationId": current.get("inferenceGenerationId"),
            "sourceAboxSnapshotId": current.get("sourceAboxSnapshotId"),
            "hypothesis": deepcopy(hypothesis), "transitionId": transition.get("transitionId"),
            "lifecycleKey": transition.get("lifecycleKey"), "observedAt": transition.get("occurredAt"),
            "previousState": transition.get("previousState"), "currentState": transition.get("currentState"),
            "evidenceChanges": changes, "ruleComparisons": comparisons,
            "sourceRuleIds": list(transition.get("sourceRuleIds") or []),
            "decisionImpact": {"status": "unchanged" if before and before == after else "changed" if before and after else "first-observation" if after else "unavailable",
                               "previous": before, "current": after},
            "nextCheck": _next_checks(hypothesis, current, previous, transition),
            "parentJobId": old_event.get("rootJobId") or transition.get("parentJobId") or "",
            "fingerprint": signature, "conclusionFingerprint": conclusion,
            "active": transition.get("currentState") != "invalidated", "eligible": not duplicate,
            "reasonCode": "same-customer-meaning" if duplicate else "verified-customer-evidence-change"}


def customer_rule_events(current, previous, transitions):
    events, rejected = [], []
    for transition in transitions:
        if transition.get("changeCategory") == "data-availability":
            continue  # The receipt-backed source interruption contract owns this.
        hypotheses = _hypotheses(current, previous, transition)
        seen = set()
        purpose_linked = False
        for hypothesis in hypotheses:
            intent = rule_notification_intent(hypothesis.get("claimContract"))
            if (not intent or intent["claimContractId"] in seen
                    or intent["ruleId"] not in (hypothesis.get("ruleIds") or [])):
                continue
            purpose_linked = True
            if intent["purpose"] not in {"hypothesis-evidence", "context-evidence"}:
                continue
            seen.add(intent["claimContractId"])
            event = _event(current, previous, transition, hypothesis, intent)
            if event:
                events.append(event)
        if not any(e.get("transitionId") == transition.get("transitionId") for e in events):
            rejected.append({"transitionId": transition.get("transitionId"),
                             "reasonCode": "rule-notification-change-not-selected" if purpose_linked else "missing-rule-notification-purpose",
                             "reason": "규칙에서 이 변화를 고객 알림 대상으로 지정하지 않아 상세 이력에 남깁니다." if purpose_linked else
                                       "변화의 목적을 저장된 가설 계약에 연결할 수 없어 상세 이력에 남깁니다."})
    return events, rejected


def carry_intent_receipts(current, previous, events, job_id):
    history = deepcopy(previous.get("notificationIntentContinuity") or {})
    for event in events:
        if not event.get("eligible"):
            continue
        key = event["intentKey"]
        prior = history.pop(key, {})
        if event.get("outcome"):
            outcomes = {**prior.get("outcomeReceipts", {}), event["outcome"]["outcomeId"]: event["fingerprint"]}
            history[key] = {**prior, "outcomeReceipts": dict(list(outcomes.items())[-32:]), "lastJobId": job_id}
            continue  # Result replies must not reset the evidence-change baseline.
        history[key] = {**prior, "fingerprint": event["fingerprint"], "eventId": event["eventId"],
                        "purpose": event["purpose"], "rootJobId": prior.get("rootJobId") or event.get("parentJobId") or job_id,
                        "lastJobId": job_id, "changeKind": event["changeKind"],
                        "conclusionFingerprint": event.get("conclusionFingerprint"), "active": event.get("active"),
                        "intent": deepcopy(event.get("intent") or {}), "hypothesis": deepcopy(event.get("hypothesis") or {}),
                        "lifecycleKey": event.get("lifecycleKey") or "",
                        "tracking": deepcopy((event.get("nextCheck") or {}).get("tracking") or prior.get("tracking") or [])}
    current["notificationIntentContinuity"] = dict(list(history.items())[-64:])
