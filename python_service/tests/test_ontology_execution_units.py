import unittest

from digital_twin.domain.fact_changes import fact_change_contract
from digital_twin.domain.ontology_execution_units import (
    ACCOUNT_GRAIN,
    INSTRUMENT_GRAIN,
    event_change_classes,
    revision_vector_for_change,
    rule_evaluation_grain,
    rules_allow_subject_fanout,
)
from digital_twin.domain.ontology_rule_manifest import (
    rule_dependency_reverse_index,
    validate_rule_domain_manifests,
)
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.infrastructure.typedb_ontology import (
    typedb_native_rule_execution_plan,
    typedb_native_rule_target_work_plan,
)


class OntologyExecutionUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = default_graph_inference_rules()

    def test_portfolio_sources_run_once_per_account(self):
        rule = next(
            item for item in self.rules
            if item.rule_id == "graph.portfolio.risk_policy.review.v1"
        )

        self.assertEqual(ACCOUNT_GRAIN, rule_evaluation_grain(rule))
        self.assertFalse(rules_allow_subject_fanout([rule]))
        self.assertEqual("accountId", rule.resolved_domain_manifest["executionUnit"]["singleEvaluationKey"])

    def test_instrument_rules_remain_subject_fanout_eligible(self):
        rules = [
            item for item in self.rules
            if item.rule_id in {
                "graph.price.reclaim.thesis_support.v1",
                "graph.news.direct_material_risk.v1",
            }
        ]

        self.assertTrue(rules)
        self.assertTrue(all(rule_evaluation_grain(rule) == INSTRUMENT_GRAIN for rule in rules))
        self.assertTrue(rules_allow_subject_fanout(rules))

    def test_portfolio_valuation_and_structure_changes_are_distinct(self):
        self.assertIn(
            "portfolio-valuation",
            event_change_classes(["position", "portfolio"], ["marketValue", "profitLossRate"]),
        )
        self.assertNotIn(
            "portfolio-structure",
            event_change_classes(["position", "portfolio"], ["marketValue", "profitLossRate"]),
        )
        self.assertIn(
            "portfolio-structure",
            event_change_classes(["position", "portfolio"], ["quantity", "averagePrice"]),
        )

    def test_fact_change_contract_exposes_event_classes(self):
        contract = fact_change_contract(
            ["PortfolioSnapshot"],
            fact_types_by_symbol={"AAPL": ["PortfolioSnapshot"]},
            changed_fields_by_symbol={"AAPL": ["marketValue"]},
        )

        self.assertEqual(["portfolio-state", "portfolio-valuation"], contract["eventClassesBySymbol"]["AAPL"])
        self.assertEqual(
            {"portfolio-state": "revision-1", "portfolio-valuation": "revision-1"},
            revision_vector_for_change("revision-1", ["position", "portfolio"], ["marketValue"]),
        )

    def test_every_rule_has_governed_execution_unit_and_reverse_indexes(self):
        validation = validate_rule_domain_manifests(self.rules)
        reverse_index = rule_dependency_reverse_index(self.rules)

        self.assertTrue(validation["valid"], validation["invalidRuleIds"])
        self.assertEqual(len(self.rules), validation["ruleCount"])
        self.assertIn("account", reverse_index["rulesByEvaluationGrain"])
        self.assertIn("instrument", reverse_index["rulesByEvaluationGrain"])
        self.assertIn("market-observation", reverse_index["triggerByEventClass"])

    def test_account_rule_is_one_work_item_for_a_multi_symbol_request(self):
        rule = next(
            item for item in self.rules
            if item.rule_id == "graph.portfolio.risk_policy.review.v1"
        )
        plan = typedb_native_rule_execution_plan(
            [rule],
            ["AAPL", "MSTR"],
            relation_types_by_symbol={"AAPL": [], "MSTR": []},
        )
        entry = plan["selectedEntries"][0]
        work = typedb_native_rule_target_work_plan(
            plan["selectedEntries"], target_parallelism=4,
        )

        self.assertEqual(ACCOUNT_GRAIN, entry["evaluationGrain"])
        self.assertEqual([], entry["candidateSymbols"])
        self.assertEqual(1, work["targetWorkItemCount"])


if __name__ == "__main__":
    unittest.main()
