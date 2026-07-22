import unittest

from digital_twin.domain.notification_rule_evaluator import (
    notification_subject_group_key,
    notification_state_group_key,
    ontology_relation_delivery_diff,
    ontology_relation_delivery_metadata,
)
from digital_twin.domain.notifications import NotificationJob


class OntologyRelationDeliveryTests(unittest.TestCase):
    def context(self, current_price=70000, event_key="main:news:005930:article-1"):
        return {
            "ontologyInsight": {
                "subject": "005930",
                "dispatchInsightType": "holdingPositionCommon",
                "sourceEventKeys": [event_key],
            },
            "ontologyRelationContext": {
                "source": "typedbInferenceBox",
                "graphStore": "typedb",
                "graphStoreUsed": True,
                "fallbackUsed": False,
                "facts": {"currentPrice": current_price},
                "decision": {
                    "basis": "typedbInferenceBox",
                    "selectedRuleId": "graph.holding.trend.risk.v1",
                    "decisionStage": "RISK_REVIEW",
                    "actionGroup": "lossControl",
                    "actionPolicy": "holding",
                },
                "decisionState": {
                    "reviewLevel": "act",
                    "dataState": "sufficient",
                    "changeState": "worsening",
                    "conflictState": "risk-dominant",
                    "validationState": "ready",
                },
                "activeRules": [{
                    "ruleId": "graph.holding.trend.risk.v1",
                    "decisionStage": "RISK_REVIEW",
                    "actionGroup": "lossControl",
                }],
                "graphStoreInference": {
                    "relations": [{
                        "type": "HAS_INFERRED_RISK",
                        "ruleId": "graph.holding.trend.risk.v1",
                    }],
                    "traces": [{
                        "id": "inference:volatile:one",
                        "ruleId": "graph.holding.trend.risk.v1",
                        "decisionStage": "RISK_REVIEW",
                    }],
                },
            },
        }

    def job(self, context):
        return NotificationJob.create(
            "graph backed insight",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )

    def test_price_only_change_keeps_relation_delivery_fingerprint_and_cooldown_group(self):
        before = self.context(current_price=70000)
        after = self.context(current_price=70100)

        self.assertEqual(
            ontology_relation_delivery_metadata(before)["fingerprint"],
            ontology_relation_delivery_metadata(after)["fingerprint"],
        )
        self.assertEqual(notification_state_group_key(self.job(before)), notification_state_group_key(self.job(after)))
        self.assertFalse(ontology_relation_delivery_diff(after, before)["changed"])

    def test_new_material_evidence_changes_graph_delivery_identity(self):
        before = self.context(event_key="main:news:005930:article-1")
        after = self.context(event_key="main:news:005930:article-2")

        diff = ontology_relation_delivery_diff(after, before)

        self.assertNotEqual(
            ontology_relation_delivery_metadata(before)["fingerprint"],
            ontology_relation_delivery_metadata(after)["fingerprint"],
        )
        self.assertNotEqual(notification_state_group_key(self.job(before)), notification_state_group_key(self.job(after)))
        self.assertTrue(diff["changed"])
        self.assertIn("evidenceKeys", diff["changedComponents"])

    def test_inference_generation_id_does_not_change_delivery_identity(self):
        before = self.context()
        after = self.context()
        after["ontologyRelationContext"]["inferenceGenerationId"] = "inference:new-generation"
        after["ontologyRelationContext"]["graphStoreInference"]["traces"][0]["id"] = "inference:volatile:two"

        self.assertEqual(
            ontology_relation_delivery_metadata(before)["fingerprint"],
            ontology_relation_delivery_metadata(after)["fingerprint"],
        )

    def test_trace_provenance_drift_is_not_a_material_delivery_change(self):
        before = self.context()
        after = self.context()
        before["ontologyRelationContext"]["graphStoreInference"]["traces"][0]["evidenceRelationIds"] = ["relation:old"]
        after["ontologyRelationContext"]["graphStoreInference"]["traces"][0]["evidenceRelationIds"] = ["relation:new"]

        self.assertEqual(
            ontology_relation_delivery_metadata(before)["fingerprint"],
            ontology_relation_delivery_metadata(after)["fingerprint"],
        )
        diff = ontology_relation_delivery_diff(after, before)
        self.assertFalse(diff["material"])
        self.assertEqual("unchanged", diff["changeClass"])

    def test_legacy_relation_shaped_context_does_not_change_delivery_group(self):
        context = self.context()
        context["ontologyRelationContext"]["decision"].pop("basis")

        self.assertEqual({}, ontology_relation_delivery_metadata(context))
        self.assertNotIn("graph=", notification_state_group_key(self.job(context)))

    def test_tracking_query_parameters_do_not_create_new_evidence(self):
        before = self.context(event_key="")
        after = self.context(event_key="")
        before["ontologyRelationContext"]["evidenceSubgraph"] = [{
            "kind": "article",
            "url": "https://news.example.com/story/42?utm_source=rss&id=42",
        }]
        after["ontologyRelationContext"]["evidenceSubgraph"] = [{
            "kind": "article",
            "url": "https://news.example.com/story/42?id=42&fbclid=campaign",
        }]

        self.assertEqual(
            ontology_relation_delivery_metadata(before)["fingerprint"],
            ontology_relation_delivery_metadata(after)["fingerprint"],
        )

    def test_subject_comparison_survives_a_new_graph_rule(self):
        before = self.context()
        after = self.context()
        after["ontologyRelationContext"]["decision"]["selectedRuleId"] = "graph.holding.liquidity.risk.v1"
        after["ontologyRelationContext"]["activeRules"][0]["ruleId"] = "graph.holding.liquidity.risk.v1"
        after["ontologyRelationContext"]["graphStoreInference"]["relations"][0]["ruleId"] = "graph.holding.liquidity.risk.v1"
        after["ontologyRelationContext"]["graphStoreInference"]["traces"][0]["ruleId"] = "graph.holding.liquidity.risk.v1"

        self.assertEqual(notification_subject_group_key(self.job(before)), notification_subject_group_key(self.job(after)))
        self.assertNotEqual(notification_state_group_key(self.job(before)), notification_state_group_key(self.job(after)))
        self.assertIn("activeRules", ontology_relation_delivery_diff(after, before)["changedComponents"])


if __name__ == "__main__":
    unittest.main()
