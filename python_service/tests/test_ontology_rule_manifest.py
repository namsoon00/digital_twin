import unittest

from digital_twin.domain.ontology_rule_manifest import (
    ASSESSMENT_SCOPES,
    rule_dependency_reverse_index,
    rule_assessment_scope,
    validate_rule_domain_manifests,
)
from digital_twin.domain.ontology_rulebox_contracts import GraphRuleCondition
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules


class OntologyRuleManifestTests(unittest.TestCase):
    def test_default_rulebox_has_complete_v2_manifests(self):
        payload = validate_rule_domain_manifests(default_graph_inference_rules())

        self.assertTrue(payload["valid"])
        self.assertGreater(payload["ruleCount"], 100)
        self.assertTrue(all(item["assessmentScope"] in ASSESSMENT_SCOPES for item in payload["manifests"]))
        self.assertTrue(all(item["outputContract"] for item in payload["manifests"]))
        self.assertTrue(all(item["triggerDependencies"] for item in payload["manifests"]))
        self.assertTrue(all(item["requiredContext"] for item in payload["manifests"]))
        self.assertTrue(all(item["invalidationContract"] for item in payload["manifests"]))
        self.assertTrue(all(item["derivedOutputs"] for item in payload["manifests"]))
        self.assertTrue(all(
            item["contextCompletenessPolicy"]["retainUnchangedFacts"]
            for item in payload["manifests"]
        ))

    def test_assessment_scope_separates_policy_and_execution_from_opinion(self):
        self.assertEqual("investment-opinion", rule_assessment_scope({}, ["trend", "flow"]))
        self.assertEqual("portfolio-fit", rule_assessment_scope({}, ["portfolio", "exposure"]))
        self.assertEqual("execution-readiness", rule_assessment_scope({}, ["execution", "liquidity"]))
        self.assertEqual("evidence-quality", rule_assessment_scope({}, ["quality", "freshness"]))

    def test_repeated_account_action_guard_is_portfolio_fit_not_investment_opinion(self):
        rule = next(
            item for item in default_graph_inference_rules()
            if item.rule_id == "graph.portfolio.repeated_loss_add.guard.v1"
        )
        payload = validate_rule_domain_manifests([rule])

        self.assertEqual("portfolio-fit", payload["manifests"][0]["assessmentScope"])

    def test_dependency_reverse_index_separates_trigger_context_and_invalidation(self):
        rule = {
            "ruleId": "graph.test.routed.v1",
            "actionGroup": "watchlist",
            "enabled": True,
            "conditions": [
                {
                    "conditionId": "profile-gate",
                    "kind": "subject_property",
                    "field": "targetPositionRole",
                    "role": "required",
                    "changeTrigger": False,
                    "invalidationTrigger": True,
                },
                {
                    "conditionId": "price-trigger",
                    "kind": "subject_property",
                    "field": "currentPrice",
                    "role": "required",
                },
            ],
            "derivations": [{
                "relationType": "HAS_TEST_RESULT",
                "targetKind": "reasoning-insight",
                "tboxClass": "InvestmentInsight",
                "decisionStage": "observe",
                "decisionEffect": "support",
            }],
        }

        index = rule_dependency_reverse_index([rule])

        self.assertEqual([], index["triggerByDependencyKey"].get("kind:stock:field:targetpositionrole", []))
        self.assertEqual(
            ["graph.test.routed.v1"],
            index["invalidationByDependencyKey"]["kind:stock:field:targetpositionrole"],
        )
        self.assertEqual(
            ["graph.test.routed.v1"],
            index["contextByDependencyKey"]["kind:stock:field:targetpositionrole"],
        )
        self.assertEqual(
            ["graph.test.routed.v1"],
            index["triggerByDependencyKey"]["kind:stock:field:currentprice"],
        )
        self.assertEqual(64, len(index["fingerprint"]))

    def test_condition_change_routing_metadata_round_trips_without_changing_typeql_semantics(self):
        condition = GraphRuleCondition.from_dict({
            "conditionId": "context",
            "kind": "subject_property",
            "field": "sector",
            "operator": "==",
            "value": "technology",
            "changeTrigger": False,
            "invalidationTrigger": True,
        })

        payload = condition.to_dict()

        self.assertFalse(payload["change_trigger"])
        self.assertTrue(payload["invalidation_trigger"])
        self.assertEqual("sector", payload["field"])
        self.assertEqual("==", payload["operator"])
        self.assertEqual("technology", payload["value"])


if __name__ == "__main__":
    unittest.main()
