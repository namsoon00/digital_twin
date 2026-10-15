"""Reply with persisted outcome evaluations of a previously delivered claim."""

from copy import deepcopy
from .rule_notification_event import VERSION, decision_state, fingerprint, rows


def outcome_events(current, previous):
    result = []
    registrations = {r.get("episodeId"): r for r in rows(current.get("outcomeTracking"))}
    for key, receipt in (previous.get("notificationIntentContinuity") or {}).items():
        intent = receipt.get("intent") or {}
        if not receipt.get("rootJobId") or intent.get("purpose") != "hypothesis-evidence":
            continue
        delivered = receipt.get("outcomeReceipts") or {}
        for original in rows(receipt.get("tracking")):
            registration = registrations.get(original.get("episodeId")) or {}
            contract = registration.get("outcomeContract") or {}
            if (registration.get("claimContractId") != intent.get("claimContractId")
                    or not contract.get("contractFingerprint")
                    or contract.get("contractFingerprint") != (original.get("outcomeContract") or {}).get("contractFingerprint")):
                continue
            for observation in rows(registration.get("results")):
                payload = observation.get("payload") or {}
                if (payload.get("calibrationEligibility") != "eligible" or payload.get("mode") != "contract-criteria"
                        or not rows(payload.get("criterionAssessments"))
                        or payload.get("horizonMinutes") not in (contract.get("outcomeHorizonMinutes") or [])
                        or payload.get("contractFingerprint") != contract["contractFingerprint"]
                        or observation.get("episodeId") != original.get("episodeId")
                        or not observation.get("outcomeId") or not observation.get("observedAt")):
                    continue
                kind = {"directionally-corroborated": "outcome-confirmed",
                        "directionally-contradicted": "outcome-contradicted",
                        "inconclusive": "outcome-inconclusive"}.get(observation.get("selectedHypothesisStatus"))
                if kind not in intent.get("notifyOn", []):
                    continue
                signature = fingerprint({"outcome": observation["outcomeId"], "kind": kind,
                                         "criteria": payload.get("criterionAssessments")})
                if delivered.get(observation["outcomeId"]) == signature:
                    continue
                result.append({"version": VERSION, "eventId": "rule-result:" + signature[:24],
                    **{k: current.get(k) for k in ("accountId", "symbol", "worldId", "inferenceGenerationId", "sourceAboxSnapshotId")},
                    "intentKey": key, "intent": deepcopy(intent), "purpose": "hypothesis-evidence", "changeKind": kind,
                    "hypothesis": deepcopy(receipt.get("hypothesis") or {}), "lifecycleKey": receipt.get("lifecycleKey"),
                    "transitionId": "", "observedAt": observation["observedAt"],
                    "sourceRuleIds": [intent["ruleId"]], "outcome": deepcopy(observation),
                    "decisionImpact": {"status": "outcome-only", "previous": decision_state(previous), "current": decision_state(current)},
                    "nextCheck": {"tracking": [deepcopy(registration)], "trackingStatus": "registered"},
                    "parentJobId": receipt["rootJobId"], "fingerprint": signature, "eligible": True,
                    "reasonCode": "registered-rule-outcome-observed"})
    return result
