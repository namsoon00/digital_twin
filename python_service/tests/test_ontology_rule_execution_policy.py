import unittest

from digital_twin.domain.ontology_rule_execution_policy import rule_execution_profile
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules


class OntologyRuleExecutionPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = default_graph_inference_rules()

    def rule_with_effect(self, effect):
        return next(
            rule
            for rule in self.rules
            if {item.decision_effect for item in rule.derivations} == {effect}
        )

    def test_block_or_constrain_rules_fail_closed(self):
        profile = rule_execution_profile(self.rule_with_effect("constrain"))

        self.assertEqual("critical", profile["executionStage"])
        self.assertEqual("invalidate-generation", profile["failurePolicy"])

    def test_defer_rules_remain_core_and_fail_closed(self):
        rule = next(
            item
            for item in self.rules
            if item.rule_id == "graph.instrument_profile.strategy_fit.support.v1"
        )
        profile = rule_execution_profile(rule)

        self.assertEqual("core", profile["executionStage"])
        self.assertEqual("invalidate-generation", profile["failurePolicy"])

    def test_support_only_rules_may_preserve_completed_core_with_gap(self):
        profile = rule_execution_profile(self.rule_with_effect("support"))

        self.assertEqual("supporting", profile["executionStage"])
        self.assertEqual("preserve-core-with-gap", profile["failurePolicy"])


if __name__ == "__main__":
    unittest.main()
