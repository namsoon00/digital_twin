import unittest

from digital_twin.modules.decisions.domain.decision_evidence_assertion import (
    inference_evidence_assertions,
    rebind_hypothesis_evidence_ids,
)
from digital_twin.modules.decisions.domain.decision_evidence_contract import hypothesis_decision_eligibility
from digital_twin.modules.notifications.domain.notification_narrative import (
    build_decision_core_evidence_ledger,
)


def inference_trace(rule_id, evidence_id, signal_type, direction, label):
    observed_value = {
        "signalType": signal_type,
        "strengthBand": "strong",
        "hypothesisContractId": rule_id,
        "decisionEligibility": "conditional",
    }
    return {
        "traceId": "trace:" + rule_id,
        "ruleId": rule_id,
        "label": label,
        "evidenceUsableForJudgement": True,
        "evidenceRelationIds": [evidence_id],
        "claimContract": {
            "expectedDirection": direction,
            "evidenceIndependenceKey": "model:" + signal_type,
        },
        "matchedConditions": [{
            "conditionId": "validated-model-signal:" + rule_id,
            "relationId": evidence_id,
            "relationType": "HAS_MODEL_SIGNAL",
            "observedValue": observed_value,
            "matchedTargetProperties": {
                **observed_value,
                "releaseId": "event-response-statistics-production-v2",
                "sourceFeatureSnapshotId": "feature-snapshot:000660",
                "modelEvidenceIds": ["model-evidence:000660:event"],
                "currentPrice": 1775000,
                "ma20Distance": 8.6,
            },
            "sourceFactIds": ["source-fact:000660:price"],
            "source": "statistical-signal-pipeline",
            "sourceAsOf": "2026-09-08T08:09:00Z",
            "sourceFetchedAt": "2026-09-08T08:09:05Z",
            "freshnessStatus": "fresh",
            "judgementEvidenceUsable": True,
        }],
    }


class DecisionEvidenceAssertionTests(unittest.TestCase):
    def test_exact_assertions_survive_hypothesis_rebinding_and_prompt_ledger(self):
        support_rule = "graph.price.recovery.v1"
        risk_rule = "graph.price.failed_recovery.v1"
        support_id = "ontology-assertion:support"
        risk_id = "ontology-assertion:risk"
        traces = [
            inference_trace(
                support_rule,
                support_id,
                "price-recovery-support",
                "support",
                "가격 회복",
            ),
            inference_trace(
                risk_rule,
                risk_id,
                "price-failed-recovery-risk",
                "risk",
                "회복 실패 위험",
            ),
        ]
        hypothesis_set = {
            "hypotheses": [{
                "hypothesisId": "hypothesis:recovery",
                "claim": "가격 회복이 이어질 수 있다.",
                "supportingRuleIds": [support_rule],
                "counterRuleIds": [risk_rule],
                "supportingEvidenceIds": ["relation-evidence:legacy-support"],
                "counterEvidenceIds": ["relation-evidence:legacy-risk"],
            }],
        }

        rebound = rebind_hypothesis_evidence_ids(hypothesis_set, traces)
        hypothesis = rebound["hypotheses"][0]
        assertions = inference_evidence_assertions(
            traces,
            facts={"currentPrice": 1775000},
            referenced_evidence_ids=[support_id, risk_id],
        )
        ledger = build_decision_core_evidence_ledger(
            facts={"currentPrice": 1775000},
            rules=[],
            hypotheses=rebound["hypotheses"],
            evidence_assertions=assertions,
            reference_date="2026-09-08T08:10:00Z",
        )
        by_id = {item["evidenceId"]: item for item in ledger}

        self.assertEqual([support_id], hypothesis["supportingEvidenceIds"])
        self.assertEqual([risk_id], hypothesis["counterEvidenceIds"])
        self.assertEqual("support", by_id[support_id]["role"])
        self.assertEqual("counter", by_id[risk_id]["role"])
        self.assertIn("price-failed-recovery-risk", by_id[risk_id]["label"])
        self.assertNotEqual(hypothesis["claim"], by_id[risk_id]["label"])
        self.assertEqual(
            "price-failed-recovery-risk",
            by_id[risk_id]["value"]["signalType"],
        )
        self.assertEqual("statistical-signal-pipeline", by_id[risk_id]["source"])
        self.assertEqual("2026-09-08T08:09:00Z", by_id[risk_id]["sourceAsOf"])
        self.assertEqual("2026-09-08T08:09:05Z", by_id[risk_id]["fetchedAt"])
        self.assertEqual("fresh", by_id[risk_id]["freshness"])
        self.assertEqual(
            ["source-fact:000660:price"],
            by_id[risk_id]["sourceFactIds"],
        )
        self.assertEqual(
            ["model-evidence:000660:event"],
            by_id[risk_id]["modelEvidenceIds"],
        )
        self.assertEqual(
            "feature-snapshot:000660",
            by_id[risk_id]["sourceFeatureSnapshotId"],
        )
        self.assertEqual(
            "event-response-statistics-production-v2",
            by_id[risk_id]["modelReleaseId"],
        )
        self.assertEqual(8.6, by_id[risk_id]["featureSummary"]["ma20Distance"])

    def test_unusable_or_unresolved_derived_evidence_fails_closed(self):
        trace = inference_trace(
            "graph.price.recovery.v1",
            "ontology-assertion:stale",
            "price-recovery-support",
            "support",
            "가격 회복",
        )
        trace["matchedConditions"][0]["judgementEvidenceUsable"] = False
        hypothesis_set = {
            "hypotheses": [{
                "hypothesisId": "hypothesis:recovery",
                "supportingRuleIds": ["graph.price.recovery.v1"],
                "supportingEvidenceIds": [
                    "relation-evidence:legacy",
                    "ontology-assertion:unproven",
                ],
                "counterRuleIds": [],
                "counterEvidenceIds": ["inference-trace:legacy"],
            }],
        }

        rebound = rebind_hypothesis_evidence_ids(hypothesis_set, [trace])

        self.assertEqual([], rebound["hypotheses"][0]["supportingEvidenceIds"])
        self.assertEqual([], rebound["hypotheses"][0]["counterEvidenceIds"])
        self.assertIn(
            "missing-supporting-evidence",
            hypothesis_decision_eligibility(rebound["hypotheses"][0])["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
