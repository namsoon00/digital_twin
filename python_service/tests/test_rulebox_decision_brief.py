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

    def test_excluding_one_rule_does_not_remove_another_rules_valid_candidate(self):
        context = fixture()
        relation = context["ontologyRelationContext"]
        relation["matchedRules"].append({**deepcopy(relation["matchedRules"][1]), "rule_id": "rule:stale"})
        relation["actionEnvelope"]["inferenceEligibilityAssessments"].append({
            "ruleId": "rule:stale", "status": "reference-only", "freshnessStatus": "stale",
            "evidenceUsableForJudgement": False})
        relation["hypothesisSet"]["hypotheses"][0]["supportingRuleIds"].append("rule:stale")
        brief = rulebox_decision_brief(context)
        self.assertEqual(["TRIM"], brief["candidateActions"])
        self.assertEqual(["rule:stale"], [r["ruleId"] for r in brief["unavailableCandidates"]])
        text = message(context)
        self.assertIn("일부 축소 후보의 일부 근거 제외", text)
        self.assertIn("일부 축소 검토 보류", text)
        self.assertNotIn("일부 축소 판단에 사용하지 못한 이유", text)

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
        for value in ("현재 판단", "관리 원칙: 추가매수 제한", "일부 축소 검토 보류", "손실 관리 기준 -15%",
                      "기준보다 손실 3%p", "이전 알림에서도", "손실이 2%p 확대",
                      "-15% 초과", "8,500원", "운영 검증 미충족", "반등 확인이나 추가매수 허용을 뜻하지"):
            self.assertIn(value, text)
        for value in ("세부값 미기록 이하", "AI 가설 비교 필요", "확률 100%", "매도를 권장합니다"):
            self.assertNotIn(value, text)
        self.assertEqual("passed", context["customerInvestmentDocumentQuality"]["status"])
        self.assertNotIn("설정 상한 45%", text, "a settings value alone is not proof of which denominator a rule compared")

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

    def test_previous_loss_requires_the_original_literal_operator_and_boundary(self):
        from digital_twin.modules.notifications.domain.rulebox_decision_presentation import decision_brief_presentation
        for old_limit, old_operator, old_pnl, continued in ((-20, "<=", -16, False), (-15, "<", -15, False),
                                                          (-15, "<", -16, True), (-15, "<=", -15, True)):
            with self.subTest(limit=old_limit, operator=old_operator, pnl=old_pnl):
                packet = fixture()["relationChangeEvidence"]
                for snapshot, limit in ((packet["current"], -15), (packet["previous"], old_limit)):
                    condition = next(r for r in snapshot["rules"] if r["id"] == "rule:policy")["conditions"][0]
                    condition.update(expectedValue=limit, operator=old_operator,
                                     resolvedThreshold={"field": "", "value": limit, "source": "authored-literal"})
                next(f for f in packet["previous"]["facts"] if f["id"] == "profitLossRate")["value"] = old_pnl
                text = str(decision_brief_presentation(packet))
                self.assertEqual(continued, "이전 알림에서도 손실 기준을 벗어난" in text)
        packet["previous"]["rules"] = []
        self.assertNotIn("이전 알림에서도 손실 기준을 벗어난", str(decision_brief_presentation(packet)))

    def test_pending_candidates_name_each_hypothesis_and_its_frozen_qualification(self):
        context = fixture()
        first = context["ontologyRelationContext"]["hypothesisSet"]["hypotheses"][0]
        first.update(expectedOutcome="현재 추세가 약화되거나 반대 방향으로 전환", qualification={
            "status": "observed", "decisiveOutcomeCount": 6, "directionalHitRate": .5,
            "directionalHitRateConfidence95": {"lower": .18},
            "policy": {"activeFloor": 12, "activeMinHitRate": .6, "activeMinLowerConfidence": .4}})
        second = deepcopy(first)
        second.update(hypothesisId="h2", expectedOutcome="반등이 유지되지 못하고 이전 위험 경로로 복귀")
        context["ontologyRelationContext"]["hypothesisSet"]["hypotheses"].append(second)
        context["investmentSubjectDecisionCase"]["eligibleHypothesisIds"].append("h2")
        text = message(context)
        for value in ("가격 흐름 변화 가능성: 운영 검증 미충족", "반등 실패 가능성: 운영 검증 미충족",
                      "6건 / 운영 기준 12건 이상", "과거 적중률 50% / 기준 60% 이상", "적중률 하한 18% / 기준 40% 이상"):
            self.assertIn(value, text)
        self.assertEqual(2, text.count("6건 / 운영 기준 12건 이상"))
        self.assertNotIn("0건·6건", text)
        first["qualification"].pop("policy")
        second["qualification"].pop("policy")
        first["claimContract"].pop("qualificationPolicy", None)
        second["claimContract"].pop("qualificationPolicy", None)
        self.assertNotIn("운영 기준 12건", message(context))

    def test_new_rule_is_the_delivery_cause_not_an_existing_loss_breach(self):
        context = fixture()
        t = context["ontologyRelationContext"]["hypothesisLifecycle"]["transitions"][0]
        t.update(currentState="strengthened", previousState="maintained", materialChange=True,
                 sourceRuleIds=["rule:linked", "rule:policy"],
                 evidenceDelta={"addedRuleIds": ["rule:linked"], "addedSupportingEvidenceKeys": ["new-support"]},
                 changeBasis={"category": "market-change", "ruleComparisons": [{
                     "ruleId": "rule:linked", "label": "예시종목 · 보유 종목 + 장중 하락 가속 -> 재점검 · 모델 신호",
                     "comparison": "verified", "previous": {}, "current": {"score": 1, "minimumScore": .7,
                     "modelInputWindows": {"1D": {"sampleCount": 4, "priceReturn": -.02, "recentReturn": -.01,
                                                 "latestObservedAt": "2026-10-02T01:10:00Z"}}}}]})
        text = message(context)
        doc = context["customerInvestmentDocument"]
        self.assertIn("뒷받침하는 근거가 추가", doc["lead"])
        cause = next(s for s in doc["sections"] if s["key"] == "delivery-cause")
        self.assertIn("‘장중 하락 속도가 빨라짐’ 규칙이 가설의 지지 근거에 추가", str(cause))
        self.assertNotIn("평가 수익률", str(cause))
        self.assertIn("1일 -2% (4건)", str(cause))
        self.assertNotIn("직전 분석 미보존 → 이번 1", text)
        self.assertIn("손실이 2%p 확대", text)
        self.assertEqual("passed", context["customerInvestmentDocumentQuality"]["status"])

    def test_policy_notice_explains_excluded_actions_and_uses_the_native_weight_denominator(self):
        from dataclasses import replace
        from test_investment_insight_dispatch_service import frozen_notification_claim
        from digital_twin.modules.read_models.contracts import customer_investment_document_from_dict, customer_investment_document_quality
        context = fixture()
        relation = context["ontologyRelationContext"]
        original_case = deepcopy(context["investmentSubjectDecisionCase"])
        relation["hypothesisLifecycle"]["transitions"] = []
        relation["facts"].update(positionAccountWeight=78, positionWeight=96, accountNumber="private-account-number")
        policy = relation["matchedRules"][0]
        policy.update(claimContract=frozen_notification_claim("rule:policy", "risk-invariant"),
                      nextChecks=["회복 가격대와 보유 비중 한도를 함께 확인"])
        relation["graphStoreInference"]["traces"][-1]["matchedConditions"][0].update(expectedValue=0, operator="<")
        concentration = {**deepcopy(policy), "rule_id": "rule:concentration", "label": "비중 한도 초과",
                         "claimContract": frozen_notification_claim("rule:concentration", "risk-invariant"),
                         "nextChecks": ["한도 초과 비중, 줄일 경우의 수량과 현금 비중, 세금·거래비용을 함께 확인"]}
        relation["matchedRules"].append(concentration)
        relation["graphStoreInference"]["traces"].append({"ruleId": "rule:concentration", "matchedConditions": [{
            "conditionId": "position-over-policy", "field": "positionAccountWeight", "operator": ">", "observedValue": 78,
            "expectedValue": {"field": "strategyMaxPositionWeightPct", "default": 30},
            "matchedByTypeDB": True, "judgementEvidenceUsable": True, "freshnessStatus": "fresh"}]})
        relation["actionEnvelope"]["inferenceEligibilityAssessments"].append({
            "ruleId": "rule:concentration", "status": "eligible", "evidenceUsableForJudgement": True, "freshnessStatus": "fresh"})
        relation["actionEnvelope"]["inferenceEligibilityAssessments"][1].update(
            status="reference-only", evidenceUsableForJudgement=False, freshnessStatus="stale")
        context["investmentSubjectDecisionCase"].update(allowedActions=[], eligibleHypothesisIds=[],
                                                      dispositionCode="HYPOTHESIS_RESEARCH_ONLY")
        first = relation["hypothesisSet"]["hypotheses"][0]
        first.update(expectedOutcome="반등이 유지되지 못하고 이전 위험 경로로 복귀", evidenceState="blocked",
                     qualification={"status": "observed", "decisiveOutcomeCount": 6,
                                    "policy": {"activeFloor": 12}})
        for identity, outcome in (("h2", "현재 추세가 약화되거나 반대 방향으로 전환"),
                                  ("h3", "거시·연관자산 충격이 종목 위험으로 전이")):
            relation["hypothesisSet"]["hypotheses"].append({**deepcopy(first), "hypothesisId": identity, "expectedOutcome": outcome})
        source = deepcopy(relation)
        text = message(context)
        packet = context["relationChangeEvidence"]
        brief = packet["current"]["decisionBrief"]
        self.assertEqual(["TRIM"], [r["action"] for r in brief["unavailableCandidates"]])
        self.assertEqual([], brief["candidateActions"])
        self.assertEqual(["ADD"], brief["blockedActions"])
        self.assertFalse(brief["finalActionSelected"])
        for value in ("추가매수 제한은 계속 적용", "이전 ‘일부 축소’ 후보는 이번 판단에 사용할 수 없습니다",
                      "원천 자료가 허용 시간을 넘었습니다", "운영 검증 미충족", "6건 / 운영 기준 12건 이상",
                      "계좌 전체 대비 비중 78% / 설정 상한 45%", "상한보다 33%p 높음", "보유 종목 평가액 내 비중 96%",
                      "줄일 경우의 수량과 현금 비중", "평가 수익률 0% 이상", "비중 45% 이하"):
            self.assertIn(value, text)
        for value in ("검토 후보: 일부 축소 → 없음", "살펴볼 가설", "구간 고점", "계좌 비중 96%", "private-account-number"):
            self.assertNotIn(value, text)
        self.assertEqual(3, len(packet["current"]["hypotheses"]), "full audit claims remain frozen")
        self.assertEqual(source, relation)
        self.assertEqual(original_case["executionEligibleHypothesisIds"], context["investmentSubjectDecisionCase"]["executionEligibleHypothesisIds"])
        self.assertEqual("passed", context["customerInvestmentDocumentQuality"]["status"])
        doc = customer_investment_document_from_dict(context["customerInvestmentDocument"])
        self.assertLess(text.index("지금 적용할 대응"), text.index("현재 전체 데이터"))
        for key in ("notification-purpose", "decision-impact", "delivery-cause", "next-check-contract"):
            broken = replace(doc, sections=tuple(s for s in doc.sections if s.key != key))
            self.assertIn("missing-observation-" + key, customer_investment_document_quality(broken)["issues"])
        from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
        from digital_twin.modules.notifications.domain.notifications import NotificationJob
        from digital_twin.modules.notifications.domain.notification_templates import NotificationTemplate, render_notification
        from digital_twin.modules.notifications.domain.notification.presentation import presentation_metadata
        job = NotificationJob.create("preview", message_type="investmentInsight", context=deepcopy(context))
        renderer = NotificationRenderingService(template_renderer=lambda j: render_notification(NotificationTemplate.default("investmentInsight"), j.context))
        delivered = renderer.render(job)
        self.assertTrue(delivered.startswith("<b>🔗 관리 기준 점검"), "the final template must keep the admitted policy purpose")
        self.assertEqual("relation-change", presentation_metadata(job.message_type, job.context)["kind"], "label changes cannot reroute transport")
        self.assertNotIn("살펴볼 가설", delivered)
        self.assertIn("계좌 전체 대비 비중은 78%", delivered)
        self.assertEqual("passed", job.context["customerInvestmentDocumentQuality"]["status"])
        job.context["transportDelivery"] = {"message": delivered, "relationChangeEvidence": deepcopy(packet)}
        self.assertEqual(delivered, renderer.render(job), "a partial retry must preserve the exact frozen body")
        # Missing total-account data must never borrow the holdings-only weight.
        relation["facts"].pop("positionAccountWeight")
        relation["graphStoreInference"]["traces"][-1]["matchedConditions"][0]["observedValue"] = None
        missing = message(context)
        self.assertNotIn("계좌 전체 대비 비중 96%", missing)
        self.assertIn("실제 비교값 또는 기준이 보존되지", missing)


if __name__ == "__main__":
    unittest.main()
