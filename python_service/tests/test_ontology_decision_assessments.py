import unittest

from digital_twin.domain.ontology_decision_assessments import (
    decision_assessment_bundle,
    without_portfolio_assessment,
)
from digital_twin.domain.ontology_inference_context import action_envelope_from_inference
from digital_twin.domain.ontology_relation_contracts import OntologyRuleMatch


def match(rule_id, action, effect="support", eligible=True):
    return OntologyRuleMatch(
        rule_id=rule_id,
        label=rule_id,
        version="v1",
        relation_type="INFERS_ACTION",
        signal_type="typedb",
        matched=True,
        review_level="check",
        review_label="조건 확인",
        data_state="sufficient",
        evidence_role="support",
        decision_effect=effect,
        candidate_action=action,
        reference_only=not eligible,
        evidence_state={
            "inferenceEligibilityStatus": "eligible" if eligible else "reference-only",
            "evidenceUsableForJudgement": eligible,
            "freshnessStatus": "fresh" if eligible else "stale",
            "inferenceEligibilityReason": "기준시각 만료" if not eligible else "",
        },
    )


def relation(rule_id, scope, action, effect="support"):
    return {
        "ruleId": rule_id,
        "type": "INFERS_ACTION",
        "assessmentScope": scope,
        "decisionStage": "TEST_STAGE",
        "decisionEffect": effect,
        "candidateAction": action,
        "candidateActionLabel": action,
        "actionGroup": "test",
        "actionLevel": "review",
        "decisionTone": "watch",
    }


class OntologyDecisionAssessmentTests(unittest.TestCase):
    def test_portfolio_and_execution_constraints_do_not_rewrite_opinion(self):
        matches = [
            match("rule.opinion", "BUY"),
            match("rule.portfolio", "AVOID", "constrain"),
            match("rule.execution", "HOLD", "block"),
        ]
        relations = [
            relation("rule.opinion", "investment-opinion", "BUY"),
            relation("rule.portfolio", "portfolio-fit", "AVOID", "constrain"),
            relation("rule.execution", "execution-readiness", "HOLD", "block"),
        ]

        bundle = decision_assessment_bundle(matches, relations)

        self.assertEqual("BUY", bundle["investmentOpinion"]["candidateAction"])
        self.assertEqual("BUY", bundle["recommendedPlan"]["investmentAction"])
        self.assertEqual("execution-blocked", bundle["recommendedPlan"]["status"])
        self.assertTrue(bundle["recommendedPlan"]["meaningPreserved"])

    def test_market_scope_removes_portfolio_constraint_and_recomposes_plan(self):
        bundle = decision_assessment_bundle(
            [match("rule.opinion", "BUY"), match("rule.portfolio", "HOLD", "constrain")],
            [
                relation("rule.opinion", "investment-opinion", "BUY"),
                relation("rule.portfolio", "portfolio-fit", "HOLD", "constrain"),
            ],
        )

        scoped = without_portfolio_assessment(bundle)

        self.assertEqual("not-evaluated", scoped["portfolioFit"]["status"])
        self.assertEqual("BUY", scoped["recommendedPlan"]["investmentAction"])
        self.assertEqual("ready", scoped["recommendedPlan"]["status"])
        self.assertEqual("instrument-market", scoped["policyScope"])

    def test_reference_only_rule_is_reported_but_not_used_as_opinion(self):
        bundle = decision_assessment_bundle(
            [
                match("rule.fresh", "HOLD"),
                match("rule.stale", "TRIM", "constrain", eligible=False),
            ],
            [
                relation("rule.fresh", "investment-opinion", "HOLD"),
                relation("rule.stale", "investment-opinion", "TRIM", "constrain"),
            ],
        )

        self.assertEqual("HOLD", bundle["investmentOpinion"]["candidateAction"])
        self.assertEqual(["rule.stale"], bundle["evidenceQuality"]["excludedRuleIds"])

    def test_governed_policy_role_overrides_legacy_investment_opinion_scope(self):
        policy_relation = relation("graph.strategy.fit", "investment-opinion", "HOLD", "defer")
        policy_relation["knowledgeBasis"] = {
            "ruleKind": "policy-constraint",
            "decisionEligibility": "guardrail-only",
            "requiresHypothesis": False,
        }

        bundle = decision_assessment_bundle(
            [match("graph.strategy.fit", "HOLD", "defer")],
            [policy_relation],
        )

        self.assertEqual("not-evaluated", bundle["investmentOpinion"]["status"])
        self.assertEqual("deferred", bundle["portfolioFit"]["status"])
        self.assertEqual(["graph.strategy.fit"], bundle["portfolioFit"]["ruleIds"])

    def test_policy_only_match_cannot_author_an_investment_view(self):
        policy_match = match("graph.instrument_profile.averaging_down_policy.v1", "HOLD", "defer")
        policy_relation = relation(
            "graph.instrument_profile.averaging_down_policy.v1",
            "investment-opinion",
            "HOLD",
            "defer",
        )
        policy_relation.update({
            "blockedActions": ["ADD"],
            "knowledgeBasis": {
                "ruleKind": "policy-constraint",
                "decisionEligibility": "guardrail-only",
                "requiresHypothesis": False,
            },
        })

        envelope = action_envelope_from_inference(
            {"source": "holding", "isHolding": True},
            [policy_match],
            [policy_relation],
        )

        self.assertEqual("", envelope["investmentViewAction"])
        self.assertEqual("NO_ACTION", envelope["executionAction"])
        self.assertEqual("", envelope["selectedRuleId"])
        self.assertEqual(
            ["graph.instrument_profile.averaging_down_policy.v1"],
            envelope["portfolioConstraintRuleIds"],
        )

    def test_conflicting_investment_actions_require_a_typedb_selection(self):
        matches = [
            match("rule.entry", "BUY"),
            match("rule.exit", "TRIM"),
        ]
        relations = [
            relation("rule.entry", "investment-opinion", "BUY"),
            relation("rule.exit", "investment-opinion", "TRIM"),
        ]

        bundle = decision_assessment_bundle(matches, relations)
        envelope = action_envelope_from_inference({}, matches, relations, bundle)

        self.assertEqual("conflicted", bundle["investmentOpinion"]["status"])
        self.assertEqual(["BUY", "TRIM"], bundle["investmentOpinion"]["candidateActions"])
        self.assertEqual("", bundle["investmentOpinion"]["candidateAction"])
        self.assertEqual("", bundle["investmentOpinion"]["selectedRuleId"])
        self.assertEqual("judgement-conflicted", bundle["recommendedPlan"]["status"])
        self.assertEqual("NO_ACTION", envelope["executionAction"])
        self.assertTrue(envelope["opinionActionConflict"])

    def test_same_action_from_multiple_rules_is_not_a_conflict(self):
        matches = [
            match("rule.entry.price", "BUY"),
            match("rule.entry.flow", "BUY"),
        ]
        relations = [
            relation("rule.entry.price", "investment-opinion", "BUY"),
            relation("rule.entry.flow", "investment-opinion", "BUY"),
        ]

        bundle = decision_assessment_bundle(matches, relations)

        self.assertFalse(bundle["investmentOpinion"]["actionConflict"])
        self.assertEqual("BUY", bundle["investmentOpinion"]["candidateAction"])
        self.assertEqual(
            ["rule.entry.price", "rule.entry.flow"],
            bundle["investmentOpinion"]["candidateRuleIdsByAction"]["BUY"],
        )


if __name__ == "__main__":
    unittest.main()
