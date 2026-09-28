import unittest
from dataclasses import replace

from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.modules.model_registry.domain.hypothesis_semantic_audit import audit_active_predictive_rules
from digital_twin.modules.model_registry.domain.ontology_rulebox_contracts import GraphInferenceRule
from digital_twin.modules.model_registry.domain.rule_claim_contract import (
    hypothesis_qualification,
    rule_claim_contract_violations,
    rule_claim_coverage,
)
from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules
from digital_twin.modules.outcomes.domain.hypothesis_outcome_evaluation import evaluate_hypothesis_outcome
from digital_twin.modules.outcomes.domain.hypothesis_outcome_facts import freeze_outcome_baseline, premise_observation_facts
from digital_twin.modules.model_registry.domain.rule_claim_contract import predictive_outcome_contract
from digital_twin.modules.outcomes.domain.hypothesis_calibration_identity import claim_validation_fingerprint


class RuleClaimContractTests(unittest.TestCase):
    def test_every_active_predictive_rule_has_aligned_signal_claim_and_outcome_semantics(self):
        audit = audit_active_predictive_rules(default_graph_inference_rules())

        self.assertEqual(0, audit["issueRuleCount"], audit["issues"])
        self.assertEqual(audit["activePredictiveRuleCount"], audit["alignedRuleCount"])
        self.assertEqual(58, audit["activePredictiveRuleCount"])

    def test_high_beta_is_market_sensitivity_and_high_pe_is_review_context(self):
        rules = {item.rule_id: item for item in default_graph_inference_rules()}
        beta = rules["graph.benchmark.beta.context.v1"]
        expensive = rules["graph.valuation.expensive.review.v2"]

        self.assertEqual("context-observation", beta.resolved_knowledge_basis.rule_kind)
        self.assertEqual("context-observation", expensive.resolved_knowledge_basis.rule_kind)
        self.assertFalse(expensive.resolved_claim_contract.is_predictive)
        self.assertNotIn("high-beta", [item.condition_id for item in expensive.conditions])
        self.assertTrue(any("베타" in item.description for item in beta.conditions))

    def test_negative_event_absorption_has_its_own_claim_and_model_input(self):
        rule = next(
            item for item in default_graph_inference_rules()
            if item.rule_id == "graph.temporal.risk_event_absorption.support.v2"
        )
        claim = rule.resolved_claim_contract
        dependencies = set(rule.model_input_contract.get("dependencyKeys") or [])

        self.assertEqual("event-absorption", rule.resolved_knowledge_basis.thesis_family)
        self.assertEqual("event-abnormal-return", claim.outcome_metric)
        self.assertIn("kind:temporal-window:field:riskeventcount", dependencies)
        self.assertIn("kind:temporal-window:field:pricechangepct", dependencies)
        self.assertNotIn("kind:temporal-window:field:supporteventcount", dependencies)

    def test_every_rule_has_one_typed_claim_and_predictive_outcome_contract(self):
        rules = default_graph_inference_rules()

        coverage = rule_claim_coverage(rules)

        self.assertEqual(122, coverage["ruleCount"])
        self.assertEqual(122, coverage["claimCount"])
        self.assertEqual(0, coverage["orphanRuleCount"])
        self.assertEqual(0, coverage["duplicateClaimCount"])
        self.assertEqual(0, coverage["violationCount"])
        self.assertEqual(71, coverage["predictiveClaimCount"])
        self.assertEqual(71, coverage["structuredOutcomeContractCount"])
        self.assertTrue(coverage["complete"])

    def test_claim_contract_round_trips_with_rulebox_payload(self):
        original = next(
            rule for rule in default_graph_inference_rules()
            if rule.enabled and rule.resolved_claim_contract.is_predictive
        )

        restored = GraphInferenceRule.from_dict(original.to_dict())

        self.assertEqual(original.resolved_claim_contract, restored.resolved_claim_contract)
        self.assertFalse(rule_claim_contract_violations(restored.resolved_claim_contract, restored.rule_id))
        self.assertGreaterEqual(len(restored.resolved_hypothesis_lifecycle().outcome_contract.criteria), 2)
        self.assertEqual(
            {0},
            {
                item.horizon_minutes
                for item in restored.resolved_hypothesis_lifecycle().outcome_contract.criteria
            },
        )

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
        self.assertEqual(71, len(outcomes))
        self.assertGreater(len(criteria), 144)
        self.assertEqual(122, relation_types.count("GOVERNED_BY_CLAIM"))
        self.assertEqual(71, relation_types.count("USES_HYPOTHESIS_OUTCOME_CONTRACT"))
        self.assertEqual(len(criteria), relation_types.count("HAS_OUTCOME_CRITERION"))

    def test_market_rise_does_not_validate_event_outperformance(self):
        contract = predictive_outcome_contract("event-support", "support").to_dict()
        for criterion in contract["criteria"]:
            criterion["benchmarkSymbol"] = "SPY"
        result = evaluate_hypothesis_outcome(contract, "support", {
            "currentPrice": 103, "benchmarkSymbol": "SPY", "benchmarkReturnPct": 5,
            "observationSourcePolicy": "point-in-time-market-observation",
        }, 3, 1440)
        self.assertEqual("contradicted", result["predictionStatus"])
        self.assertEqual("not-measured", result["thesisValidationStatus"])

    def test_price_gain_does_not_validate_missing_or_worsening_financial_premise(self):
        contract = predictive_outcome_contract("fundamental-rerating", "support", "graph.company.market.fundamental_confirmation.support.v1").to_dict()
        facts = {"currentPrice": 103, "observationSourcePolicy": "point-in-time-market-observation"}
        missing = evaluate_hypothesis_outcome(contract, "support", facts, 3, 90 * 1440)
        self.assertEqual("corroborated", missing["predictionStatus"])
        self.assertEqual("unavailable", missing["thesisValidationStatus"])
        self.assertEqual("inconclusive", missing["selectedHypothesisStatus"])
        facts.update({"newFinancialPeriod": True, "revenueGrowthPct": -20, "freeCashFlowMarginPct": -10})
        contradicted = evaluate_hypothesis_outcome(contract, "support", facts, 3, 90 * 1440)
        self.assertEqual("inconclusive", contradicted["selectedHypothesisStatus"])
        facts.update({"revenueGrowthPct": 20, "freeCashFlowMarginPct": 10,
                      "valuationGapReductionPp": 5, "valuationMeasurementState": "measured"})
        supported = evaluate_hypothesis_outcome(contract, "support", facts, 3, 90 * 1440)
        self.assertEqual("corroborated", supported["thesisValidationStatus"])
        self.assertEqual("not-established", supported["causalAttribution"])

    def test_changed_rule_predicate_cannot_inherit_claim_validation(self):
        rule = next(rule for rule in default_graph_inference_rules() if rule.resolved_claim_contract.is_predictive)
        changed = replace(rule, conditions=[replace(rule.conditions[0], value=123), *rule.conditions[1:]])
        self.assertNotEqual(
            claim_validation_fingerprint(rule.resolved_claim_contract.to_dict()),
            claim_validation_fingerprint(changed.resolved_claim_contract.to_dict()),
        )

    def test_valuation_rule_measures_comparable_value_gap_without_pretending_to_validate_earnings(self):
        contract = predictive_outcome_contract("fundamental-rerating", "support", "graph.valuation.margin_of_safety.opportunity.v1")
        causes = [row for row in contract.criteria if row.role == "cause"]
        self.assertEqual(["valuationGapReductionPp"], [row.metric for row in causes])
        self.assertIn("valuation", contract.required_observation_domains)

    def test_missing_flow_is_not_zero_net_buying(self):
        contract = predictive_outcome_contract("flow-accumulation", "support").to_dict()
        facts = {"currentPrice": 103, "foreignNetVolume": 0, "institutionNetVolume": 0, "observationSourcePolicy": "point-in-time-market-observation"}
        missing = evaluate_hypothesis_outcome(contract, "support", facts, 3, 60)
        self.assertEqual("unavailable", missing["thesisValidationStatus"])
        facts["marketSignalCoverage"] = {"investor": {"usableForJudgement": True}}
        observed = evaluate_hypothesis_outcome(contract, "support", facts, 3, 60)
        self.assertEqual("contradicted", observed["thesisValidationStatus"])

    def test_same_report_refetch_is_not_new_financial_evidence(self):
        company = {"latestFinancials": {"quarterly": [{"period": "2026-06-30"}]}}
        baseline = freeze_outcome_baseline({"companyContext": company, "ma20Distance": -3})
        unchanged = premise_observation_facts(baseline, {"companyContext": company, "ma20Distance": 2})
        self.assertNotIn("newFinancialPeriod", unchanged)
        self.assertEqual(5, unchanged["ma20DistanceChangePp"])
        current = {"financials": {"quarterly": [{"period": "2026-09-30", "netIncomeGrowthPct": 10}]}}
        updated = premise_observation_facts(baseline, {"companyContext": current, "evidenceSnapshotAt": "2026-10-15T00:00:00Z"})
        self.assertTrue(updated["newFinancialPeriod"])
        self.assertEqual(10, updated["netIncomeGrowthPct"])


if __name__ == "__main__":
    unittest.main()
