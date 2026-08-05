import unittest

from digital_twin.domain.notification_rule_evaluator import (
    notification_subject_group_key,
    notification_state_group_key,
    ontology_relation_delivery_diff,
    ontology_relation_delivery_metadata,
)
from digital_twin.domain.notification_rules import default_notification_rule, evaluate_notification_rule
from digital_twin.domain.notification_templates import alert_context
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.ontology_insights import build_investment_insight_events
from digital_twin.domain.portfolio import AccountSnapshot, AlertEvent, PortfolioSummary


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

    def test_combined_insight_keeps_one_graph_context_for_notification_delivery(self):
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            "2026-08-05T03:00:00Z",
            PortfolioSummary(0, 0, 0, [], [], 0),
        )
        lower_priority = self.context()["ontologyRelationContext"]
        lower_priority["decision"].update({"reviewLevel": "observe", "changeState": "unchanged"})
        lower_priority.update({"reviewLevel": "observe", "changeState": "unchanged", "dataState": "sufficient"})
        lower_priority["decisionState"] = {
            "reviewLevel": "observe",
            "dataState": "sufficient",
            "changeState": "unchanged",
            "conflictState": "context-only",
        }
        higher_priority = self.context()["ontologyRelationContext"]
        higher_priority["decision"].update({
            "selectedRuleId": "graph.watchlist.entry.confirmed.v1",
            "reviewLevel": "check",
            "changeState": "new-condition",
        })
        higher_priority["activeRules"][0]["ruleId"] = "graph.watchlist.entry.confirmed.v1"
        higher_priority.update({"reviewLevel": "check", "changeState": "new-condition", "dataState": "sufficient"})
        higher_priority["decisionState"] = {
            "reviewLevel": "check",
            "dataState": "sufficient",
            "changeState": "new-condition",
            "conflictState": "context-only",
        }
        events = [
            AlertEvent(
                "main",
                "메인",
                "WATCH",
                "watchlistOntologySignal",
                "main:watch:005930:trend",
                "삼성전자",
                ["추세 확인"],
                "005930",
                metadata={"ontologyRelationContext": lower_priority, "watchlistOntologySignalType": "trendReview"},
            ),
            AlertEvent(
                "main",
                "메인",
                "WATCH",
                "watchlistOntologySignal",
                "main:watch:005930:entry",
                "삼성전자",
                ["진입 조건 확인"],
                "005930",
                metadata={"ontologyRelationContext": higher_priority, "watchlistOntologySignalType": "entryCandidate"},
            ),
        ]

        insight = build_investment_insight_events(snapshot, events)[0]
        metadata = insight.metadata
        relation_context = metadata["ontologyRelationContext"]

        self.assertIsInstance(relation_context, dict)
        self.assertEqual("graph.watchlist.entry.confirmed.v1", relation_context["decision"]["selectedRuleId"])
        self.assertEqual(2, len(metadata["ontologyRelationContexts"]))

        context = alert_context(insight)
        decision = evaluate_notification_rule(
            self.job(context),
            default_notification_rule("investmentInsight"),
        )
        self.assertTrue(decision.should_send)
        self.assertNotEqual("missing_graph_inference", decision.suppression_reason)

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
        self.assertIn("main:news:005930:article-2", diff["addedEvidenceKeys"])
        self.assertIn("main:news:005930:article-1", diff["removedEvidenceKeys"])

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

    def test_relation_row_churn_keeps_one_cooldown_group_when_action_envelope_is_static(self):
        before = self.context()
        before["ontologyRelationContext"]["actionEnvelope"] = {
            "status": "HOLDING_REVIEW",
            "preferredAction": "TRIM",
            "selectedRuleId": "graph.holding.trend.risk.v1",
            "dataReadiness": {"state": "ready", "dataState": "sufficient"},
        }
        after = self.context()
        after["ontologyRelationContext"]["actionEnvelope"] = dict(before["ontologyRelationContext"]["actionEnvelope"])
        after["ontologyRelationContext"]["decision"]["selectedRuleId"] = "graph.holding.liquidity.risk.v1"
        after["ontologyRelationContext"]["activeRules"][0]["ruleId"] = "graph.holding.liquidity.risk.v1"
        after["ontologyRelationContext"]["graphStoreInference"]["relations"][0]["ruleId"] = "graph.holding.liquidity.risk.v1"

        diff = ontology_relation_delivery_diff(after, before)

        self.assertNotEqual(
            ontology_relation_delivery_metadata(before)["fingerprint"],
            ontology_relation_delivery_metadata(after)["fingerprint"],
        )
        self.assertEqual(notification_state_group_key(self.job(before)), notification_state_group_key(self.job(after)))
        self.assertFalse(diff["material"])
        self.assertEqual("context-drift", diff["changeClass"])
        self.assertEqual("unchanged", diff["decisionTransition"]["kind"])

    def test_action_envelope_transition_is_material(self):
        before = self.context()
        before["ontologyRelationContext"]["actionEnvelope"] = {
            "status": "ENTRY_DEFERRED",
            "preferredAction": "HOLD",
            "dataReadiness": {"state": "partial", "dataState": "partial"},
        }
        after = self.context()
        after["ontologyRelationContext"]["actionEnvelope"] = {
            "status": "ENTRY_ELIGIBLE",
            "preferredAction": "BUY",
            "dataReadiness": {"state": "ready", "dataState": "sufficient"},
        }

        diff = ontology_relation_delivery_diff(after, before)

        self.assertTrue(diff["material"])
        self.assertEqual("action-changed", diff["decisionTransition"]["kind"])
        self.assertIn("actionEnvelope", diff["materialComponents"])

    def test_initial_hold_without_material_event_becomes_a_delivery_baseline(self):
        context = self.context(event_key="")
        context["ontologyRelationContext"]["decisionState"]["reviewLevel"] = "observe"
        context["ontologyRelationContext"]["actionEnvelope"] = {
            "status": "ENTRY_DEFERRED",
            "preferredAction": "HOLD",
            "selectedDecisionEffect": "defer",
            "dataReadiness": {"state": "partial", "dataState": "partial"},
        }
        context["ontologyInsight"]["semanticComponents"] = {"materialSourceEventKeys": []}

        diff = ontology_relation_delivery_diff(context, {})

        self.assertTrue(diff["changed"])
        self.assertFalse(diff["material"])
        self.assertEqual("baseline", diff["changeClass"])
        self.assertEqual("initial", diff["decisionTransition"]["kind"])
        self.assertFalse(diff["decisionTransition"]["material"])

    def test_initial_trim_review_is_material_when_envelope_remains_hold(self):
        context = self.context(event_key="")
        context["ontologyRelationContext"]["decision"].update({
            "candidateAction": "HOLD",
            "primaryAction": "TRIM_REVIEW",
        })
        context["ontologyRelationContext"]["decisionState"]["reviewLevel"] = "check"
        context["ontologyRelationContext"]["actionEnvelope"] = {
            "status": "ENTRY_OBSERVING",
            "preferredAction": "HOLD",
            "selectedDecisionEffect": "defer",
            "dataReadiness": {"state": "partial", "dataState": "partial"},
        }
        context["ontologyInsight"]["semanticComponents"] = {"materialSourceEventKeys": []}

        diff = ontology_relation_delivery_diff(context, {})

        self.assertTrue(diff["material"])
        self.assertEqual("material", diff["changeClass"])
        self.assertTrue(diff["decisionTransition"]["material"])

        prior_baseline = dict(context)
        prior_baseline["deliverySuppressionReason"] = "initial_graph_baseline"
        replay = ontology_relation_delivery_diff(context, prior_baseline)

        self.assertTrue(replay["material"])
        self.assertEqual("policy-reclassified", replay["decisionTransition"]["kind"])

    def test_initial_uppercase_direct_action_is_material(self):
        context = self.context(event_key="")
        context["ontologyRelationContext"]["decision"].update({
            "candidateAction": "TRIM",
            "primaryAction": "TRIM_REVIEW",
        })
        context["ontologyRelationContext"]["decisionState"]["reviewLevel"] = "observe"
        context["ontologyRelationContext"]["actionEnvelope"] = {
            "status": "ENTRY_DEFERRED",
            "preferredAction": "HOLD",
            "selectedDecisionEffect": "defer",
            "dataReadiness": {"state": "partial", "dataState": "partial"},
        }
        context["ontologyInsight"]["semanticComponents"] = {"materialSourceEventKeys": []}

        diff = ontology_relation_delivery_diff(context, {})

        self.assertTrue(diff["material"])
        self.assertTrue(diff["decisionTransition"]["material"])

    def test_initial_hold_with_a_material_source_event_remains_deliverable(self):
        context = self.context(event_key="main:news:005930:article-9")
        context["ontologyRelationContext"]["decisionState"]["reviewLevel"] = "observe"
        context["ontologyRelationContext"]["actionEnvelope"] = {
            "status": "HOLDING_REVIEW",
            "preferredAction": "HOLD",
            "selectedDecisionEffect": "defer",
            "dataReadiness": {"state": "ready", "dataState": "sufficient"},
        }
        context["ontologyInsight"]["semanticComponents"] = {
            "materialSourceEventKeys": ["main:news:005930:article-9"],
        }

        diff = ontology_relation_delivery_diff(context, {})

        self.assertTrue(diff["material"])
        self.assertTrue(diff["decisionTransition"]["material"])


if __name__ == "__main__":
    unittest.main()
