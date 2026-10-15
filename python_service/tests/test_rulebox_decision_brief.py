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
    """Full evidence view; compact transport has separate end-to-end checks."""
    previous = context["relationChangeEvidence"]["previous"]
    context["relationChangeEvidence"] = relation_change_evidence(context, previous)
    rendered = typedb_observation_telegram_message(context)
    return context.get("customerInvestmentDetailText") or rendered


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
        doc = context["customerInvestmentDetailDocument"]
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
        doc = customer_investment_document_from_dict(context["customerInvestmentDetailDocument"])
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
        self.assertIn("계좌 전체 대비 비중 현재 78%", delivered)
        self.assertEqual("passed", job.context["customerInvestmentDocumentQuality"]["status"])
        job.context["transportDelivery"] = {"message": delivered, "relationChangeEvidence": deepcopy(packet)}
        self.assertEqual(delivered, renderer.render(job), "a partial retry must preserve the exact frozen body")
        # Missing total-account data must never borrow the holdings-only weight.
        relation["facts"].pop("positionAccountWeight")
        relation["graphStoreInference"]["traces"][-1]["matchedConditions"][0]["observedValue"] = None
        missing = message(context)
        self.assertNotIn("계좌 전체 대비 비중 96%", missing)
        self.assertIn("실제 비교값 또는 기준이 보존되지", missing)


class NativeManagementResponseTests(unittest.TestCase):
    def response_context(self):
        from digital_twin.modules.model_registry.contracts import default_graph_inference_rules
        ctx = fixture()
        rel = ctx['ontologyRelationContext']
        facts = rel['facts']
        facts.update(positionAccountWeight=78.45, positionWeight=96.03,
                     strategyMaxPositionWeightPct=45, quantity=203,
                     currentPrice=139.26, averagePrice=143.86, currency='USD')
        rule = next(r for r in default_graph_inference_rules() if r.rule_id == 'graph.position.concentration.guard.v1')
        response = deepcopy(rule.derivations[0].management_response)
        response['boundFacts'] = {k: facts[k] for k in ('positionAccountWeight', 'strategyMaxPositionWeightPct', 'quantity', 'currentPrice', 'currency')}
        rel['matchedRules'][0]['managementResponse'] = response
        return ctx

    def test_policy_decision_survives_unqualified_forecasts_and_persists_with_synthesis(self):
        from digital_twin.modules.decisions.contracts import decision_synthesis_from_relation_context
        from digital_twin.modules.decisions.domain.investment_reasoning.contracts import DecisionSynthesis
        ctx = self.response_context()
        synthesis = decision_synthesis_from_relation_context(ctx['accountId'], ctx['ontologyRelationContext'])
        restored = DecisionSynthesis.from_dict(synthesis.to_dict())
        decision = restored.management_decision
        self.assertEqual('POLICY_RESPONSE_SELECTED', decision['status'])
        self.assertFalse(decision['forecastActionSelected'])
        self.assertFalse(decision['orderAuthorized'])
        scenario = decision['responses'][0]['adjustmentScenario']
        self.assertAlmostEqual(33.45, scenario['excessWeightPercentagePoints'])
        self.assertAlmostEqual(42.638623326959846, scenario['positionReductionPct'])
        self.assertAlmostEqual(86.55640535372848, scenario['equivalentQuantity'])
        self.assertAlmostEqual(74.33333333333333, scenario['externalDepositPctOfAccount'])
        self.assertIsNone(scenario['orderQuantity'])
        ctx['investmentSubjectDecisionCase']['managementDecision'] = decision
        rendered = message(ctx)
        for phrase in ('관리 대응 결정', '계좌 비중을 설정 상한 이내로 조정', '78.45%', '45%', '42.64%', '86.56주', '74.33%', '확정 주문 수량은 아닙니다'):
            self.assertIn(phrase, rendered)
        self.assertNotIn('유지·축소를 결정하지 못한 이유', rendered)
        self.assertIn('가격 전망에 따른 별도 검토 후보: 일부 축소', rendered)
        self.assertIn('운영 검증 미충족으로 선택 보류', rendered)
        self.assertEqual('passed', ctx['customerInvestmentDocumentQuality']['status'])
        from digital_twin.modules.notifications.domain.rule_policy_event import _signature
        response = decision['responses'][0]
        signature = _signature({'id':'rule:policy'}, ['ADD'], [], [response])
        later = deepcopy(response)
        later['observedValue'] = 80
        later['adjustmentScenario']['equivalentQuantity'] = 90
        later['boundFacts']['currentPrice'] = 150
        self.assertEqual(signature, _signature({'id':'rule:policy'}, ['ADD'], [], [later]))
        later['targetValue'] = 40
        self.assertNotEqual(signature, _signature({'id':'rule:policy'}, ['ADD'], [], [later]))
        self.assertNotEqual(signature, _signature({'id':'rule:policy'}, ['ADD'], []))
        from digital_twin.modules.notifications.domain.rule_event_presentation import event_impact
        impact = event_impact({'managementResponseChanged': True, 'decisionImpact': {
            'status': 'changed', 'previous': {'blockedActions': ['ADD']}, 'current': {'blockedActions': ['ADD']}}})
        self.assertIn('관리 대응 또는 목표가 새로 정해졌습니다', impact)
        self.assertNotIn('규칙 조건이 바뀌었습니다', impact)

    def test_raw_weights_or_stale_mismatched_unbound_evidence_cannot_create_policy_decision(self):
        from digital_twin.modules.decisions.domain.investment_reasoning.management import management_decision_from_relation
        for variant in ('no-native-response', 'stale', 'unusable', 'unmatched', 'reference', 'wrong-account', 'wrong-generation', 'missing-target', 'mixed-facts'):
            with self.subTest(variant=variant):
                ctx = self.response_context()
                rel = ctx['ontologyRelationContext']
                rule = rel['matchedRules'][0]
                assessment = rel['actionEnvelope']['inferenceEligibilityAssessments'][0]
                if variant == 'no-native-response': rule.pop('managementResponse')
                if variant == 'stale': assessment['freshnessStatus'] = 'stale'
                if variant == 'unusable': assessment['evidenceUsableForJudgement'] = False
                if variant == 'unmatched': rule['matched'] = False
                if variant == 'reference': rule['reference_only'] = True
                if variant == 'wrong-account': rel['accountId'] = 'other'
                if variant == 'wrong-generation': rel['graphStoreInference']['inferenceGenerationId'] = 'other'
                if variant == 'missing-target': rule['managementResponse']['boundFacts'].pop('strategyMaxPositionWeightPct')
                if variant == 'mixed-facts': rule['managementResponse']['boundFacts']['positionAccountWeight'] = 96.03
                self.assertEqual({}, management_decision_from_relation(ctx['accountId'], rel))

    def test_policy_scenario_does_not_fabricate_quantity_or_permit_invalid_account_weights(self):
        from digital_twin.modules.decisions.domain.investment_reasoning.management import management_decision_from_relation
        ctx = self.response_context()
        rel = ctx['ontologyRelationContext']
        bound = rel['matchedRules'][0]['managementResponse']['boundFacts']
        bound.pop('quantity')
        result = management_decision_from_relation(ctx['accountId'], rel)
        self.assertNotIn('equivalentQuantity', result['responses'][0]['adjustmentScenario'])
        for value in (0, -1, 78.45, 110, True, float('nan')):
            rel['facts']['strategyMaxPositionWeightPct'] = bound['strategyMaxPositionWeightPct'] = value
            self.assertEqual({}, management_decision_from_relation(ctx['accountId'], rel))

    def test_policy_contract_rejects_prediction_or_unrelated_target_and_freezes_old_payloads(self):
        from dataclasses import replace
        from digital_twin.modules.model_registry.contracts import default_graph_inference_rules, GraphInferenceRule
        from digital_twin.modules.model_registry.domain.ontology_rulebox_governance import rulebox_semantic_violations, rulebox_rules_hash
        rules = default_graph_inference_rules()
        rule = next(r for r in rules if r.rule_id == 'graph.position.concentration.guard.v1')
        self.assertEqual([], rulebox_semantic_violations([rule]))
        invalid = deepcopy(rule.derivations[0].management_response)
        invalid['objective']['field'] = 'positionWeight'
        broken = replace(rule, derivations=[replace(rule.derivations[0], management_response=invalid)])
        self.assertTrue(rulebox_semantic_violations([broken]))
        predictive = next(r for r in rules if r.resolved_knowledge_basis.rule_kind == 'predictive-hypothesis')
        broken = replace(predictive, derivations=[replace(predictive.derivations[0], management_response=rule.derivations[0].management_response)])
        self.assertTrue(any('risk-invariant' in v for v in rulebox_semantic_violations([broken])))
        legacy = rule.to_dict()
        legacy['derivations'][0].pop('management_response')
        frozen_hash = rulebox_rules_hash([legacy])
        # Authored and normalized runtime hashes are deliberately distinct.
        # Loading a legacy release must never synthesize the new response.
        restored = GraphInferenceRule.from_dict(legacy).to_dict()
        self.assertTrue(all('management_response' not in d for d in restored['derivations']))
        self.assertNotEqual(frozen_hash, rulebox_rules_hash([rule.to_dict()]))

    def test_rulebox_storage_round_trip_and_materialized_response_bind_the_native_fact_basis(self):
        import json
        from digital_twin.modules.model_registry.contracts import default_graph_inference_rules
        from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules, build_rulebox_rules_from_rows
        from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, PortfolioOntology
        from digital_twin.modules.reasoning.domain.ontology_inference_materializer import materialize_rule_inference
        rule = next(r for r in default_graph_inference_rules() if r.rule_id == 'graph.position.concentration.guard.v1')
        graph = rulebox_graph_from_rules([rule], include_tbox=False)
        from unittest.mock import patch
        from digital_twin.infrastructure import typedb_ontology as api
        with patch.object(api, 'runtime_settings', return_value={}):
            repository = api.TypeDBOntologyGraphRepository('fixture.invalid', retry_count=0)
        template = next(e for e in graph.entities if e.kind == 'relation-template' and e.properties.get('derivation', {}).get('management_response'))
        native_row = repository.entity_row_from_typeql({
            'id': template.entity_id, 'kind': template.kind, 'ruleId': rule.rule_id,
            'json': json.dumps(template.properties),
        }, 'RuleBox')
        self.assertEqual(rule.derivations[0].management_response, native_row['derivationManagementResponse'])
        rows = lambda kind: [{'ruleId': rule.rule_id, 'propertiesJson': json.dumps(e.properties), **{k:e.properties.get(k) for k in ('conditionId', 'conditionIndex', 'derivationIndex')}} for e in graph.entities if e.kind == kind]
        restored = build_rulebox_rules_from_rows(rows('rule'), rows('rule-condition'), rows('relation-template'))[0]
        self.assertEqual(rule.derivations[0].management_response, restored.derivations[0].management_response)
        properties = {'symbol':'MSTR', 'source':'holding', 'positionAccountWeight':78.45, 'positionWeight':96.03,
                      'strategyMaxPositionWeightPct':45, 'quantity':203, 'currentPrice':139.26, 'currency':'USD'}
        stock = OntologyEntity('stock:MSTR', 'Strategy', 'stock', properties)
        output = PortfolioOntology('test:policy', entities=[stock])
        materialize_rule_inference(output, restored, stock, {'matchedConditions':[{'conditionId':c.condition_id} for c in restored.conditions]})
        response = next(r.properties['managementResponse'] for r in output.relations if r.properties.get('managementResponse'))
        self.assertEqual(78.45, response['boundFacts']['positionAccountWeight'])
        self.assertNotIn('positionWeight', response['boundFacts'])
        self.assertEqual(45, response['boundFacts']['strategyMaxPositionWeightPct'])
        # The production native path reads both in-memory and persisted rows
        # through this boundary before matches_from_inference. A formatter-only
        # fixture would miss a response silently dropped by that projection.
        from digital_twin.infrastructure.graph_store_inferencebox import inferencebox_relation_payload, inferencebox_entity_payload
        for project in (inferencebox_relation_payload, inferencebox_entity_payload):
            projected = project({'propertiesJson': json.dumps({'managementResponse': response})})
            self.assertEqual(response, projected['managementResponse'])
            projected['managementResponse']['boundFacts']['positionAccountWeight'] = 1
            self.assertEqual(78.45, response['boundFacts']['positionAccountWeight'])
            self.assertNotIn('managementResponse', project({'propertiesJson': '{}'}))
            self.assertNotIn('managementResponse', project({'managementResponse': 'malformed'}))
        from digital_twin.modules.reasoning.domain.ontology_inference_context import matches_from_inference
        projected_relations = [inferencebox_relation_payload(row) for row in repository.rows_for_relations(output)]
        matches = matches_from_inference(projected_relations, [])
        self.assertEqual(['RESTORE_POSITION_LIMIT'], [m.management_response['code'] for m in matches if m.management_response])
        self.assertTrue(any(m.relation_type == 'REQUIRES_NEXT_CHECK' for m in matches),
                        'the follow-up derivation must not hide the separate native management response')


class CompactRuleBoxJudgmentTests(unittest.TestCase):
    def test_persisted_judgment_keeps_native_policy_independent_of_forecast_qualification(self):
        from digital_twin.modules.decisions.contracts import decision_synthesis_from_relation_context
        from digital_twin.modules.decisions.domain.investment_reasoning.contracts import DecisionSynthesis
        ctx = NativeManagementResponseTests().response_context()
        synthesis = decision_synthesis_from_relation_context(ctx['accountId'], ctx['ontologyRelationContext'])
        restored = DecisionSynthesis.from_dict(synthesis.to_dict())
        judgment = restored.rulebox_judgment
        self.assertEqual('decision-synthesis', judgment['source'])
        self.assertEqual('management-response', judgment['status'])
        self.assertEqual(['ADD'], judgment['blockedActions'])
        self.assertEqual(45, judgment['managementDecision']['responses'][0]['targetValue'])
        self.assertFalse(judgment['finalMarketActionSelected'])
        self.assertFalse(judgment['orderAuthorized'])
        self.assertEqual('world:fixture', judgment['worldId'])
        self.assertEqual(-15, judgment['conditions'][0]['resolvedThreshold']['value'])
        self.assertEqual(synthesis.rulebox_judgment, judgment)
        legacy = synthesis.to_dict()
        legacy.pop('rulebox_judgment')
        legacy['version'] = 'investment-decision-synthesis-v9'
        self.assertEqual(legacy, DecisionSynthesis.from_dict(legacy).to_dict())
        ctx['investmentSubjectDecisionCase']['ruleboxJudgment'] = judgment
        self.assertEqual(judgment, rulebox_decision_brief(ctx)['judgment'])

    def test_different_native_candidates_are_compared_without_inventing_a_winner(self):
        ctx = fixture()
        row = ctx['ontologyRelationContext']['matchedRules'][-1]
        row.update(decision_effect='support', relation_type='SUPPORTS_THESIS')
        previous = ctx['relationChangeEvidence']['previous']
        ctx['relationChangeEvidence'] = relation_change_evidence(ctx, previous)
        body = typedb_observation_telegram_message(ctx)
        judgment = ctx['ruleboxNotificationSummary']['judgment']
        self.assertEqual(['HOLD', 'TRIM'], [g['action'] for g in judgment['candidateGroups']])
        self.assertTrue(judgment['comparisonRequired'])
        self.assertFalse(judgment['finalMarketActionSelected'])
        self.assertIn('근거가 엇갈려 최종 판단을 확정하지 않았습니다', body)
        self.assertNotIn('살펴볼 가설', body)

    def test_compact_measurements_use_same_rule_comparison_and_keep_full_data_in_detail(self):
        ctx = fixture()
        packet = ctx['relationChangeEvidence']
        condition = deepcopy(ctx['ontologyRelationContext']['graphStoreInference']['traces'][-1]['matchedConditions'][0])
        ctx['ontologyRelationContext']['graphStoreInference']['traces'][0]['matchedConditions'].append(condition)
        old_rule = next(r for r in packet['previous']['rules'] if r['id'] == 'rule:linked')
        old_rule['conditions'].append({**deepcopy(condition), 'observedValue': -16})
        next(f for f in packet['previous']['facts'] if f['id'] == 'profitLossRate')['value'] = -2
        ctx['relationChangeEvidence'] = relation_change_evidence(ctx, packet['previous'])
        body = typedb_observation_telegram_message(ctx)
        self.assertIn('평가 수익률 -16% → -18% / 기준 -15% 이하', body)
        self.assertNotIn('-2% → -18%', body)
        self.assertIn('가격 8,200원', body)
        self.assertIn('보유 수익률 -18%', body)
        self.assertLessEqual(len([line for line in body.splitlines() if line]), 7)
        self.assertLess(len(body), 850)
        self.assertEqual('passed', ctx['customerInvestmentDocumentQuality']['status'])
        for value in ('외국인: 순매도 500주', '체결강도 120', '매수 대기 500주', '살펴볼 가설'):
            self.assertIn(value, ctx['customerInvestmentDetailText'])
            self.assertNotIn(value, body)

    def test_transport_replaces_old_cached_long_document_but_preserves_partial_retry_bytes(self):
        from unittest.mock import Mock
        from digital_twin.modules.notifications.public import NotificationRenderingService
        from digital_twin.modules.notifications.domain.notifications import NotificationJob
        ctx = fixture()
        typedb_observation_telegram_message(ctx)
        ctx['customerInvestmentDocument'] = deepcopy(ctx['customerInvestmentDetailDocument'])
        ctx['customerInvestmentDocumentRelationRevision'] = 'readable-relation-change-v13'
        ctx['notifyLinkUrl'] = 'https://example.test/'
        template = Mock(return_value='legacy template with long debug sections')
        job = NotificationJob.create('old queued long body', message_type='investmentInsight', context=ctx)
        renderer = NotificationRenderingService(template_renderer=template)
        body = renderer.render(job)
        template.assert_not_called()
        self.assertNotIn('old queued long body', body)
        self.assertNotIn('관계 판단', body)
        self.assertLessEqual(len([line for line in body.splitlines() if line]), 9)
        self.assertIn('전체 판단 근거·현재 데이터 보기', body)
        job.context['transportDelivery'] = {'message': body, 'relationChangeEvidence': deepcopy(job.context['relationChangeEvidence'])}
        job.context['ontologyRelationContext']['facts']['currentPrice'] = 999
        self.assertEqual(body, renderer.render(job))

    def test_web_detail_preserves_full_evidence_and_receipt_without_mutating_the_job(self):
        from digital_twin.infrastructure.web.adapters.notification_presentation import notification_job_public_payload
        from digital_twin.modules.notifications.public import NotificationRenderingService
        from digital_twin.modules.notifications.domain.notifications import NotificationJob
        job = NotificationJob.create('original', message_type='investmentInsight', context=fixture())
        body = NotificationRenderingService().render(job)
        saved = deepcopy(job.to_dict())
        payload = notification_job_public_payload(job, detail=True, settings={'notificationProcessingStaleMinutes': 2})
        self.assertEqual(saved, job.to_dict())
        self.assertEqual(body, job.text)
        self.assertEqual('compact-rulebox-v1', payload['customerInvestmentNotificationDocument']['layout'])
        self.assertNotIn('layout', payload['customerInvestmentDocument'])
        self.assertIn('체결강도 120', payload['fullText'])
        self.assertIn('외국인: 순매도 500주', payload['fullText'])
        self.assertIn('살펴볼 가설', str(payload['customerInvestmentDocument']))

    def test_judgment_scope_and_semantic_repeat_identity_do_not_borrow_unrelated_decisions(self):
        from digital_twin.modules.decisions.contracts import decision_synthesis_from_relation_context
        ctx = NativeManagementResponseTests().response_context()
        relation = ctx['ontologyRelationContext']
        first = decision_synthesis_from_relation_context(ctx['accountId'], relation).rulebox_judgment
        relation['matchedRules'][0]['managementResponse']['boundFacts']['positionAccountWeight'] = 80
        relation['facts']['positionAccountWeight'] = 80
        later = decision_synthesis_from_relation_context(ctx['accountId'], relation).rulebox_judgment
        self.assertEqual(first['conclusionFingerprint'], later['conclusionFingerprint'])
        self.assertNotEqual(first['managementDecision']['decisionId'], later['managementDecision']['decisionId'])
        bad = deepcopy(first)
        bad['worldId'] = 'other-private-world'
        ctx['investmentSubjectDecisionCase']['ruleboxJudgment'] = bad
        judgment = rulebox_decision_brief(ctx)['judgment']
        self.assertNotEqual('other-private-world', judgment['worldId'])
        self.assertEqual({}, judgment['managementDecision'], 'a foreign persisted response cannot be used by the legacy adapter')
        relation['graphStoreInference']['inferenceGenerationId'] = 'other-generation'
        self.assertEqual({}, decision_synthesis_from_relation_context(ctx['accountId'], relation).rulebox_judgment)


if __name__ == "__main__":
    unittest.main()
