import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.investment_alert_coverage import (
    CANDIDATE,
    DELIVERED,
    FAILED,
    NO_MATCH,
    REFERENCE_ONLY,
    REVIEW_ONLY,
    SUPPRESSED,
    SUPERSEDED,
    derive_delivery_eligibility,
    derive_coverage_outcome,
    evaluate_alert_coverage_health,
    material_event_assessment,
    reasoning_delivery_trigger,
)
from digital_twin.domain.events import compact_materiality_assessment_event_payload


class InvestmentAlertCoverageTests(unittest.TestCase):
    def test_materiality_uses_passed_assessment_or_verified_followup(self):
        material, reason = material_event_assessment({
            "materialityAssessments": [{
                "subject": "MSTR",
                "passed": True,
                "matchedConditions": ["price-move", "volume-confirmation"],
            }],
        }, "MSTR")
        self.assertTrue(material)
        self.assertEqual("price-move+volume-confirmation", reason)

        followup, followup_reason = material_event_assessment({
            "observationFollowupSymbols": ["028260"],
        }, "028260")
        self.assertTrue(followup)
        self.assertEqual("verified-observation-followup", followup_reason)

        quiet, quiet_reason = material_event_assessment({
            "materialityAssessments": [{
                "subject": "MSTR",
                "passed": False,
                "reason": "상태 유지",
            }],
        }, "MSTR")
        self.assertFalse(quiet)
        self.assertEqual("상태 유지", quiet_reason)

        trigger = reasoning_delivery_trigger([{
            "eventId": "reasoning:event:1",
            "name": "ontology.reasoning_requested",
            "occurredAt": "2026-09-09T00:00:00Z",
            "payload": {
                "observationFollowupSymbols": ["028260"],
                "changedFieldsBySymbol": {
                    "028260": ["marketObservationFollowup"],
                },
                "factRevisionsBySymbol": {"028260": "revision:price:2"},
                "sourceObservedAt": "2026-09-08T23:59:00Z",
                "materialityAssessments": [{
                    "subject": "028260",
                    "passed": True,
                    "changedFields": ["bidAskImbalance"],
                    "matchedConditions": ["orderbook-imbalance"],
                    "facts": {
                        "previousBidAskImbalance": 8.5,
                        "bidAskImbalance": 24.2,
                        "bidAskImbalanceThreshold": 20,
                        "orderbookBidVolume": 1500,
                        "orderbookAskVolume": 900,
                        "confirmedSignalTransitions": [{
                            "signalId": "orderbook",
                            "condition": "orderbook-imbalance",
                            "fromState": "neutral",
                            "toState": "positive",
                            "observedValue": 24.2,
                            "confirmationCount": 2,
                            "requiredConfirmations": 2,
                        }],
                    },
                }],
            },
        }], "028260")
        self.assertEqual("verified-material-transition", trigger["status"])
        self.assertTrue(trigger["observationFollowup"])
        self.assertEqual(["revision:price:2"], trigger["materialRevisionKeys"])
        self.assertEqual(
            ["bidAskImbalance", "marketObservationFollowup"],
            trigger["changedFields"],
        )
        self.assertEqual("24.2", str(trigger["facts"]["bidAskImbalance"]))
        self.assertEqual("20", str(trigger["facts"]["bidAskImbalanceThreshold"]))
        compact = compact_materiality_assessment_event_payload({
            "subject": "028260",
            "passed": True,
            "facts": trigger["facts"],
        })
        self.assertEqual("1500", str(compact["facts"]["orderbookBidVolume"]))
        self.assertEqual("900", str(compact["facts"]["orderbookAskVolume"]))
        self.assertEqual(
            2,
            compact["facts"]["confirmedSignalTransitions"][0]["confirmationCount"],
        )
        self.assertEqual({}, reasoning_delivery_trigger([{
            "eventId": "reasoning:event:quiet",
            "payload": {
                "symbols": ["028260"],
                "materialityAssessments": [{
                    "subject": "028260",
                    "passed": False,
                    "reason": "상태 유지",
                }],
            },
        }], "028260"))

    def test_terminal_outcomes_are_explicit(self):
        self.assertEqual(DELIVERED, derive_coverage_outcome({"notificationStatus": "done"})["state"])
        self.assertEqual(SUPPRESSED, derive_coverage_outcome({
            "notificationStatus": "suppressed",
            "suppressionReason": "unchanged_graph_inference",
        })["state"])
        self.assertEqual(REFERENCE_ONLY, derive_coverage_outcome({"subjectStage": "OBSERVATION"})["state"])
        self.assertEqual(REVIEW_ONLY, derive_coverage_outcome({"subjectStage": "ABSTAINED"})["state"])
        self.assertEqual(NO_MATCH, derive_coverage_outcome({
            "reasoningJobStatus": "completed",
            "candidatePresent": False,
        })["state"])
        self.assertEqual(CANDIDATE, derive_coverage_outcome({
            "reasoningJobStatus": "completed",
            "candidatePresent": True,
        })["state"])
        self.assertEqual(FAILED, derive_coverage_outcome({"reasoningJobStatus": "failed"})["state"])
        superseded = derive_coverage_outcome({"reasoningJobStatus": "superseded"})
        self.assertEqual(SUPERSEDED, superseded["state"])
        self.assertTrue(superseded["terminal"])

    def test_durable_subject_and_ai_states_survive_notification_job_retention(self):
        suppressed = derive_coverage_outcome({
            "subjectStage": "VALIDATED",
            "subjectDeliveryState": "suppressed",
            "subjectDeliveryReason": "final action unchanged",
        })
        superseded = derive_coverage_outcome({
            "subjectStage": "AI_PENDING",
            "aiRequestStatus": "superseded",
        })

        self.assertEqual(SUPPRESSED, suppressed["state"])
        self.assertTrue(suppressed["terminal"])
        self.assertEqual("SUPERSEDED", superseded["state"])
        self.assertTrue(superseded["terminal"])

        recovered_failure = derive_coverage_outcome({
            "subjectStage": "ABSTAINED",
            "aiRequestStatus": "failed",
        })
        self.assertEqual(REVIEW_ONLY, recovered_failure["state"])

    def test_health_detects_overdue_material_event_without_alert_quota(self):
        now = datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc)
        health = evaluate_alert_coverage_health([{
            "coverageId": "coverage:1",
            "material": True,
            "terminal": False,
            "state": CANDIDATE,
            "candidatePresent": True,
            "eventAt": (now - timedelta(minutes=10)).isoformat(),
        }], now=now, deadline_seconds=300)

        self.assertEqual("warning", health["state"])
        self.assertEqual(1, health["overdueEventCount"])
        self.assertEqual(0.0, health["terminalCoveragePct"])

        quiet = evaluate_alert_coverage_health([], now=now)
        self.assertEqual("healthy", quiet["state"])
        self.assertEqual(100.0, quiet["terminalCoveragePct"])

    def test_health_detects_candidate_starvation_only_after_material_candidates(self):
        now = datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc)
        rows = [{
            "coverageId": "coverage:" + str(index),
            "material": True,
            "terminal": True,
            "state": SUPPRESSED,
            "candidatePresent": True,
            "pushEligible": True,
            "eventAt": now.isoformat(),
        } for index in range(8)]

        health = evaluate_alert_coverage_health(
            rows,
            now=now,
            starvation_min_candidates=8,
        )
        self.assertEqual("warning", health["state"])
        self.assertTrue(health["policyStarvation"])

        rows[0]["state"] = DELIVERED
        delivered = evaluate_alert_coverage_health(
            rows,
            now=now,
            starvation_min_candidates=8,
        )
        self.assertEqual("healthy", delivered["state"])
        self.assertFalse(delivered["policyStarvation"])

        for row in rows:
            row["state"] = SUPPRESSED
            row["pushEligible"] = False
        web_only = evaluate_alert_coverage_health(
            rows,
            now=now,
            starvation_min_candidates=8,
        )
        self.assertEqual("healthy", web_only["state"])
        self.assertFalse(web_only["policyStarvation"])
        self.assertEqual(0, web_only["deliveryEligibleCandidateCount"])

        delivered = derive_delivery_eligibility({
            "candidatePresent": True,
            "notificationStatus": "done",
        })
        web_only = derive_delivery_eligibility({
            "candidatePresent": True,
            "finalAiDeliveryGate": {
                "decision": "suppress",
                "suppressionReason": "review_only_web_history",
                "pushValueClass": "web-only-review",
            },
        })
        authorized = derive_delivery_eligibility({
            "candidatePresent": True,
            "finalAiDeliveryGate": {
                "decision": "send",
                "pushValueClass": "material-source-evidence",
            },
        })
        retained_delivery = derive_delivery_eligibility({
            "candidatePresent": True,
            "subjectDeliveryState": "delivered",
        })
        explicit_transition = derive_delivery_eligibility({
            "candidatePresent": True,
            "notificationStatus": "suppressed",
            "subjectOutcomeKind": "FINAL_DECISION",
            "cooldownDecision": "meaningful-change",
        })

        self.assertTrue(delivered["eligible"])
        self.assertFalse(web_only["eligible"])
        self.assertTrue(authorized["eligible"])
        self.assertTrue(retained_delivery["eligible"])
        self.assertTrue(explicit_transition["eligible"])
        self.assertEqual("material-source-evidence", authorized["pushValueClass"])

        internal_only = derive_delivery_eligibility({
            "candidatePresent": True,
            "v2DecisionSynthesis": {
                "dispositionCode": "RULE_COVERAGE_GAP_CANDIDATE",
            },
        })
        self.assertFalse(internal_only["eligible"])
        self.assertTrue(internal_only["determined"])
        self.assertEqual("rule_hypothesis_coverage_gap", internal_only["reasonCode"])


if __name__ == "__main__":
    unittest.main()
