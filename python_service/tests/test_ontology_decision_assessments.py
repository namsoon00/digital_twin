import unittest

from digital_twin.domain.ontology_decision_assessments import (
    decision_assessment_bundle,
    without_portfolio_assessment,
)
from digital_twin.domain.ontology_relation_contracts import OntologyRuleMatch


def match(rule_id, action, effect="support"):
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


if __name__ == "__main__":
    unittest.main()
