import unittest

from digital_twin.domain.ontology_compiler import compile_ontology_release
from digital_twin.domain.ontology_rule_manifest import (
    ASSESSMENT_SCOPES,
    rule_dependency_reverse_index,
    rule_assessment_scope,
    validate_rule_domain_manifests,
)
from digital_twin.domain.ontology_schema_capabilities import (
    rule_schema_capability_manifest,
)
from digital_twin.domain.ontology_rulebox_contracts import GraphRuleCondition
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules


class OntologyRuleManifestTests(unittest.TestCase):
    def test_compiled_release_links_every_predictive_rule_to_hypothesis_and_data(self):
        result = compile_ontology_release(default_graph_inference_rules())

        self.assertTrue(result["valid"], result.get("failures"))
        self.assertEqual("ready", result["status"])
        self.assertGreaterEqual(result["predictiveRuleCount"], 1)
        self.assertEqual([], result["missingHypothesisRuleIds"])
        self.assertEqual([], result["missingDependencyRuleIds"])
        self.assertEqual(64, len(result["irFingerprint"]))

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

    def test_strategy_fit_policy_is_portfolio_fit_not_investment_opinion(self):
        rule = next(
            item for item in default_graph_inference_rules()
            if item.rule_id == "graph.instrument_profile.strategy_fit.support.v1"
        )
        payload = validate_rule_domain_manifests([rule])

        self.assertEqual("policy-constraint", payload["manifests"][0]["ruleKind"])
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
        self._assert_physical_schema_manifest_contains_every_active_rule_query_field()

    def _assert_physical_schema_manifest_contains_every_active_rule_query_field(self):
        rules = default_graph_inference_rules()
        manifest = rule_schema_capability_manifest(rules)

        active_rule_ids = {
            rule.rule_id for rule in rules if rule.enabled is not False
        }
        self.assertEqual(active_rule_ids, set(manifest.rule_ids))
        self.assertIn("profitLossRate", manifest.subject_fields)
        self.assertIn("ma5Distance", manifest.subject_fields)
        self.assertIn("decisionEligibility", manifest.target_fields_by_context["observation-data"])
        self.assertEqual(64, len(manifest.fingerprint))


if __name__ == "__main__":
    unittest.main()
