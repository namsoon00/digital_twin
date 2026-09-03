import unittest

from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_rulebox_contracts import GraphInferenceRule
from digital_twin.domain.rule_claim_contract import (
    hypothesis_qualification,
    rule_claim_contract_violations,
    rule_claim_coverage,
)
from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules


class RuleClaimContractTests(unittest.TestCase):
    def test_every_rule_has_one_typed_claim_and_predictive_outcome_contract(self):
        rules = default_graph_inference_rules()

        coverage = rule_claim_coverage(rules)

        self.assertEqual(122, coverage["ruleCount"])
        self.assertEqual(122, coverage["claimCount"])
        self.assertEqual(0, coverage["orphanRuleCount"])
        self.assertEqual(0, coverage["duplicateClaimCount"])
        self.assertEqual(0, coverage["violationCount"])
        self.assertEqual(74, coverage["predictiveClaimCount"])
        self.assertEqual(74, coverage["structuredOutcomeContractCount"])
        self.assertTrue(coverage["complete"])

    def test_claim_contract_round_trips_with_rulebox_payload(self):
        original = next(
            rule for rule in default_graph_inference_rules()
            if rule.enabled and rule.resolved_claim_contract.is_predictive
        )

        restored = GraphInferenceRule.from_dict(original.to_dict())

        self.assertEqual(original.resolved_claim_contract, restored.resolved_claim_contract)
        self.assertFalse(rule_claim_contract_violations(restored.resolved_claim_contract, restored.rule_id))
        self.assertEqual(2, len(restored.resolved_hypothesis_lifecycle().outcome_contract.criteria))

    def test_non_predictive_rule_is_claimed_without_becoming_market_hypothesis(self):
        rule = next(
            rule for rule in default_graph_inference_rules()
            if rule.resolved_knowledge_basis.rule_kind == "data-quality-gate"
        )

        claim = rule.resolved_claim_contract
        qualification = hypothesis_qualification(claim)

        self.assertEqual("data-reliability", claim.claim_type)
        self.assertEqual("guardrail-only", claim.decision_authority)
        self.assertFalse(claim.outcome_contract.criteria)
        self.assertEqual("active-guardrail", qualification["status"])

    def test_predictive_qualification_is_reproducible_from_outcomes(self):
        claim = next(
            rule.resolved_claim_contract for rule in default_graph_inference_rules()
            if rule.enabled and rule.resolved_claim_contract.is_predictive
        )

        shadow = hypothesis_qualification(claim)
        limited = hypothesis_qualification(claim, {
            "decisiveOutcomeCount": 6,
            "directionalHitRate": 0.67,
            "directionalHitRateConfidence95": {"lower": 0.30, "upper": 0.90},
            "averageActionAdjustedReturnPct": 0.4,
        })
        active = hypothesis_qualification(claim, {
            "decisiveOutcomeCount": 20,
            "directionalHitRate": 0.70,
            "directionalHitRateConfidence95": {"lower": 0.48, "upper": 0.85},
            "averageActionAdjustedReturnPct": 0.8,
        })
        quarantined = hypothesis_qualification(claim, {
            "decisiveOutcomeCount": 12,
            "directionalHitRate": 0.10,
            "directionalHitRateConfidence95": {"lower": 0.02, "upper": 0.35},
            "averageActionAdjustedReturnPct": -1.2,
        })

        self.assertEqual("shadow", shadow["status"])
        self.assertEqual("limited-active", limited["status"])
        self.assertEqual("active", active["status"])
        self.assertEqual("quarantined", quarantined["status"])

    def test_rulebox_projects_claims_and_outcome_criteria(self):
        rules = default_graph_inference_rules()

        graph = rulebox_graph_from_rules(rules, include_tbox=False)

        claims = [item for item in graph.entities if item.kind == "rule-claim"]
        outcomes = [item for item in graph.entities if item.kind == "hypothesis-outcome-contract"]
        criteria = [item for item in graph.entities if item.kind == "hypothesis-outcome-criterion"]
        relation_types = [item.relation_type for item in graph.relations]
        self.assertEqual(122, len(claims))
        self.assertEqual(74, len(outcomes))
        self.assertEqual(148, len(criteria))
        self.assertEqual(122, relation_types.count("GOVERNED_BY_CLAIM"))
        self.assertEqual(74, relation_types.count("USES_HYPOTHESIS_OUTCOME_CONTRACT"))
        self.assertEqual(148, relation_types.count("HAS_OUTCOME_CRITERION"))


if __name__ == "__main__":
    unittest.main()
