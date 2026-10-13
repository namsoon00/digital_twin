"""Customer judgement must retain graph authority, qualification and thresholds."""

from copy import deepcopy
import unittest

from test_typedb_independent_observation import readable_fixture
from digital_twin.modules.decisions.contracts import rulebox_decision_brief, resolved_rule_threshold
from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence, relation_change_summary
from digital_twin.modules.notifications.application.typedb_observation_message import typedb_observation_telegram_message


def fixture():
    context = readable_fixture()
    relation = context["ontologyRelationContext"]
    case = context["investmentSubjectDecisionCase"]
    case.update(allowedActions=["HOLD", "TRIM"], blockedActions=["ADD"], eligibleHypothesisIds=["h1"],
                executionEligibleHypothesisIds=[], dispositionCode="HYPOTHESIS_QUALIFICATION_PENDING")
    relation["facts"].update(profitLossRate=-18, strategyLossTolerancePct=-15, strategyMaxPositionWeightPct=45,
                             positionWeight=21, currentPrice=8200, averagePrice=10000)
    relation["matchedRules"] = [
        {"rule_id": "rule:policy", "label": "손실 기준 이탈", "relation_type": "VIOLATES_RISK_TOLERANCE",
         "decision_effect": "constrain", "blockedActions": ["ADD"], "candidateAction": "",
         "knowledgeBasis": {"ruleKind": "policy-constraint"}},
        {"rule_id": "rule:linked", "label": "손실 관리 검토", "relation_type": "HAS_INFERRED_RISK",
         "decision_effect": "constrain", "candidateAction": "TRIM"},
        {"rule_id": "rule:linked", "label": "손실 관리 추가 확인", "relation_type": "REQUIRES_NEXT_CHECK",
         "decision_effect": "defer", "candidateAction": "HOLD"},
        {"rule_id": "rule:linked", "label": "위험 강도 제한", "relation_type": "WEAKENS_THESIS",
         "decision_effect": "constrain", "candidateAction": "HOLD"},
    ]
    relation["actionEnvelope"] = {"blockedActions": ["ADD"], "inferenceEligibilityAssessments": [
        {"ruleId": r, "status": "eligible", "evidenceUsableForJudgement": True, "freshnessStatus": "fresh"}
        for r in ("rule:policy", "rule:linked")
    ]}
    for row in relation["matchedRules"][1:]:
        row["knowledgeBasis"] = {"ruleKind": "predictive-hypothesis"}
    relation["graphStoreInference"]["traces"].append({"ruleId": "rule:policy", "matchedConditions": [{
        "conditionId": "loss", "field": "profitLossRate", "operator": "<=", "observedValue": -18,
        "expectedValue": {"field": "strategyLossTolerancePct", "default": -8},
        "matchedByTypeDB": True, "judgementEvidenceUsable": True, "freshnessStatus": "fresh",
    }]})
    before = relation_change_evidence(context)["current"]
    before["transitions"] = []
    next(f for f in before["facts"] if f["id"] == "profitLossRate")["value"] = -16
    next(f for f in before["facts"] if f["id"] == "currentPrice")["value"] = 8400
    context["relationChangeEvidence"] = relation_change_evidence(context, before)
    return context


def message(context):
    previous = context["relationChangeEvidence"]["previous"]
    context["relationChangeEvidence"] = relation_change_evidence(context, previous)
    return typedb_observation_telegram_message(context)


class RuleBoxDecisionBriefTests(unittest.TestCase):
    def test_restriction_candidate_and_next_check_keep_distinct_meanings(self):
        context = fixture()
        brief = rulebox_decision_brief(context)
        self.assertEqual(["ADD"], brief["blockedActions"])
        self.assertEqual(["TRIM"], brief["candidateActions"])
        self.assertEqual(2, len(brief["checks"]))
        self.assertFalse(brief["comparisonRequired"])
        self.assertTrue(brief["qualificationPending"])
        self.assertFalse(brief["finalActionSelected"])
        self.assertEqual("NO_ACTION", brief["action"])
        policy = context["ontologyRelationContext"]["matchedRules"][0]
        policy.update(candidateAction="HOLD", decision_effect="support")
        self.assertEqual(["TRIM"], rulebox_decision_brief(context)["candidateActions"], "policy support is not a competing market hypothesis")
        context["investmentSubjectDecisionCase"]["eligibleHypothesisIds"] = []
        brief = rulebox_decision_brief(context)
        self.assertEqual([], brief["candidateActions"])
        self.assertEqual(["ADD"], brief["blockedActions"], "policy restrictions do not need a market forecast")

    def test_real_hold_support_remains_a_competing_candidate(self):
        context = fixture()
        row = context["ontologyRelationContext"]["matchedRules"][-1]
        row.update(decision_effect="support", relation_type="SUPPORTS_THESIS")
        brief = rulebox_decision_brief(context)
        self.assertEqual(["TRIM", "HOLD"], brief["candidateActions"])
        self.assertTrue(brief["comparisonRequired"])

    def test_scope_or_generation_mismatch_cannot_explain_another_decision(self):
        for where, key, value in (("context", "accountId", "other"), ("relation", "accountId", "other"),
                                  ("relation", "inferenceGenerationId", "other"), ("relation", "sourceAboxSnapshotId", "other"),
                                  ("case", "symbol", "OTHER"), ("case", "subjectCaseId", "other")):
            with self.subTest(where=where, key=key):
                ctx = fixture()
                source = ctx if where == "context" else ctx["ontologyRelationContext"] if where == "relation" else ctx["investmentSubjectDecisionCase"]
                source[key] = value
                self.assertEqual({}, rulebox_decision_brief(ctx))
        ctx = fixture()
        graph = ctx["ontologyRelationContext"]["graphStoreInference"]
        graph["worldId"] = "another-world"
        self.assertEqual({}, rulebox_decision_brief(ctx))
        for field in ("inferenceGenerationId", "sourceAboxSnapshotId"):
            ctx = fixture()
            ctx["ontologyRelationContext"].pop(field, None)
            ctx["ontologyRelationContext"]["graphStoreInference"].pop(field, None)
            self.assertEqual({}, rulebox_decision_brief(ctx))

    def test_stale_reference_and_unproven_rules_cannot_supply_restrictions(self):
        for change in ("stale", "reference", "unusable", "missing-assessment", "case-block-missing"):
            with self.subTest(change=change):
                ctx = fixture()
                rel = ctx["ontologyRelationContext"]
                eligibility = rel["actionEnvelope"]["inferenceEligibilityAssessments"][0]
                if change == "stale": eligibility["freshnessStatus"] = "stale"
                if change == "reference": rel["matchedRules"][0]["reference_only"] = True
                if change == "unusable": eligibility["evidenceUsableForJudgement"] = False
                if change == "missing-assessment": rel["actionEnvelope"]["inferenceEligibilityAssessments"].pop(0)
                if change == "case-block-missing": ctx["investmentSubjectDecisionCase"]["blockedActions"] = []
                self.assertEqual([], rulebox_decision_brief(ctx)["blockedActions"])

    def test_action_outside_envelope_is_never_promoted_to_candidate(self):
        ctx = fixture()
        ctx["ontologyRelationContext"]["matchedRules"][1]["candidateAction"] = "BUY"
        self.assertEqual([], rulebox_decision_brief(ctx)["candidateActions"])
        ctx["investmentSubjectDecisionCase"]["allowedActions"] = ["BUY"]
        ctx["investmentSubjectDecisionCase"]["blockedActions"].append("BUY")
        ctx["ontologyRelationContext"]["actionEnvelope"]["blockedActions"].append("BUY")
        self.assertEqual([], rulebox_decision_brief(ctx)["candidateActions"])

    def test_parameter_uses_frozen_value_and_never_authored_default_or_current_settings(self):
        condition = {"expectedValue": {"field": "strategyLossTolerancePct", "default": -8}}
        self.assertEqual(-15, resolved_rule_threshold(condition, {"strategyLossTolerancePct": -15})["value"])
        for value in (None, True, float("nan"), float("inf"), {}, []):
            self.assertIsNone(resolved_rule_threshold(condition, {"strategyLossTolerancePct": value})["value"])
        self.assertEqual(-8, resolved_rule_threshold({"expectedValue": -8}, {})["value"])
        from digital_twin.modules.notifications.domain.relation_change_presentation import rule_conditions
        multiple = rule_conditions({"conditions": [
            {**condition, "field": "profitLossRate", "observedValue": -18, "operator": "<="},
            {"expectedValue": -5, "field": "ma20Distance", "observedValue": -10, "operator": "<="},
        ]}, {"strategyLossTolerancePct": -15}, "KRW")
        self.assertEqual(2, len(multiple))
        self.assertIn("-15% 이하", multiple[0])

    def test_rendered_message_explains_policy_delta_price_boundary_and_pending_judgement(self):
        context = fixture()
        text = message(context)
        for value in ("현재 판단", "추가매수는 현재 판단 대상에서 제외", "검토할 대응은 일부 축소", "손실 관리 기준 -15%",
                      "기준보다 손실 3%p", "이전 알림에서도", "손실이 2%p 확대", "설정 상한 45%", "상한 이내",
                      "-15% 초과", "8,500원", "검증 기준을 아직 충족하지 못", "추가 확인 요청은 보유 유지 의견으로 계산하지"):
            self.assertIn(value, text)
        for value in ("세부값 미기록 이하", "AI 가설 비교 필요", "확률 100%", "매도를 권장합니다"):
            self.assertNotIn(value, text)
        self.assertEqual("passed", context["customerInvestmentDocumentQuality"]["status"])

    def test_missing_policy_is_explicit_and_changed_policy_is_not_a_price_transition(self):
        context = fixture()
        del context["ontologyRelationContext"]["facts"]["strategyLossTolerancePct"]
        text = message(context)
        self.assertIn("실제 적용 기준값이 보존되지", text)
        self.assertNotIn("손실 관리 기준 -8%", text)
        context = fixture()
        previous = context["relationChangeEvidence"]["previous"]
        next(f for f in previous["facts"] if f["id"] == "strategyLossTolerancePct")["value"] = -20
        self.assertNotIn("이전 알림에서도 손실 기준을 벗어난", message(context))

    def test_price_translation_requires_the_same_cost_and_return_basis(self):
        context = fixture()
        context["ontologyRelationContext"]["facts"]["averagePrice"] = 11000
        text = message(context)
        self.assertNotIn("기준 가격은 약", text)
        self.assertIn("-15% 초과", text)

    def test_capture_and_render_do_not_mutate_rules_receipts_or_select_a_trade(self):
        context = fixture()
        source = deepcopy(context["ontologyRelationContext"])
        receipt = deepcopy(context["relationChangeEvidence"]["previous"])
        packet = relation_change_evidence(context, receipt)
        frozen = deepcopy(packet)
        relation_change_summary(packet)
        self.assertEqual(source, context["ontologyRelationContext"])
        self.assertEqual(receipt, context["relationChangeEvidence"]["previous"])
        self.assertEqual(frozen, packet)
        self.assertEqual([], context["investmentSubjectDecisionCase"]["executionEligibleHypothesisIds"])


if __name__ == "__main__":
    unittest.main()
