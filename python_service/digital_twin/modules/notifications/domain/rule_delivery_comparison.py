"""One customer-meaning comparison for publication, admission and final send."""

from .relation_change import relation_change_evidence


VERSION = "rule-notice-receipt-comparison-v1"


def rule_delivery_comparison(context, baseline=None, *, stage="admission", require_recovery_receipt=True):
    packet = relation_change_evidence(context, baseline, require_recovery_receipt=require_recovery_receipt)
    previous = packet["previous"]
    events = packet["customerEvents"]
    components = sorted({event["purpose"] + ":" + event["changeKind"] for event in events})
    components.extend(sorted({"data-availability:" + str(t.get("currentState") or "")
                              for t in packet["transitions"] if t.get("changeCategory") == "data-availability"}))
    diff = {"version": VERSION, "material": packet["eligible"],
            "changeClass": "material" if packet["eligible"] else "unchanged",
            "reason": packet["reason"], "changedComponents": components,
            "materialComponents": components, "contextComponents": [],
            "decisionTransition": {"version": VERSION, "material": packet["eligible"],
                'policyLifecycle': packet['policyLifecycle'],
                "kind": "rule-notice-change" if packet["eligible"] else "unchanged",
                "summary": packet["reason"], "impacts": [e.get("decisionImpact") or {} for e in events]}}
    audit = {"version": VERSION, "stage": stage,
             "baselineSource": "successful-delivery-receipt" if previous.get("receiptJobId") else "provisional" if previous else "none",
             "baselineJobId": previous.get("receiptJobId") or "", "baselineDeliveredAt": packet["baselineDeliveredAt"],
             "eligible": packet["eligible"], "reasonCode": packet["nonDeliveryReasonCode"],
             "customerEventIds": [e["eventId"] for e in events],
             'policyTransitions': [{k: t[k] for k in ('eventId', 'sourceRuleIds', 'previous', 'current', 'cause')}
                                   for t in packet['policyLifecycle']['transitions']],
             'remainingPolicyRuleIds': sorted({r['ruleId'] for r in packet['policyLifecycle']['remainingRestrictions']}),
             'unverifiedPreviousPolicyRuleIds': packet['policyLifecycle']['unverifiedPreviousRuleIds'],
             "sourceRuleIds": sorted({rid for e in events for rid in e.get("sourceRuleIds", [])})}
    return {"relationChangeEvidence": packet, "ontologyRelationDiff": diff,
            "decisionTransition": diff["decisionTransition"], "ruleNotificationComparison": audit}
