import unittest
from copy import deepcopy
from types import SimpleNamespace

from digital_twin.modules.outcomes.domain.decision_follow_up import normalize_follow_up_conditions, evaluate_follow_up_conditions
from digital_twin.modules.outcomes.domain.follow_up_tracking import registered_follow_up, follow_up_is_registered, ai_follow_up_registration_admission
from digital_twin.modules.decisions.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.modules.decisions.domain.investment_insight_assessment import compact_previous_investment_insight_episode
from digital_twin.modules.notifications.application.notification_ai_gate_message import customer_follow_up_plan
from digital_twin.modules.notifications.application.notification_ai_gate_message import execution_telegram_message
from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.domain.notification_ai_delivery import verified_follow_up_transitions, pre_ai_deferred_delivery_decision, final_ai_delivery_decision


START = "2026-09-15T00:00:00Z"


def facts(value, at=START, field="ma20Distance", state="fresh"):
    return {field: value, "sourceAsOf": at, "updatedAt": at, "currency": "KRW",
            "marketEvidenceProfile": {"capabilities": {
                "pricePath": {"state": state, "sourceAsOf": at},
                "orderBook": {"state": state, "sourceAsOf": at},
            }}}


def condition(baseline=-0.1, threshold=0, field="ma20Distance"):
    rows, _ = normalize_follow_up_conditions([
        {"conditionId": "watch:recovery", "field": field, "operator": ">=", "threshold": threshold,
         "purpose": "strengthen", "label": "20일 평균 가격 회복", "onSatisfied": "가격 회복 해석을 다시 평가합니다."},
    ], facts(baseline, field=field), "005930")
    return rows[0]


class AIFollowUpTrackingTests(unittest.TestCase):
    def test_analysis_trigger_is_not_presented_as_thesis_weakening(self):
        rows, _ = normalize_follow_up_conditions([{"field": "bidAskImbalance", "operator": "<=", "threshold": 25,
            "purpose": "weaken", "onSatisfied": "이번 재분석을 촉발한 매수 우위 변화가 완화됩니다."}],
            facts(72.61, field="bidAskImbalance"), "035720")
        self.assertEqual("switch", rows[0]["purpose"])
        self.assertEqual("weaken", rows[0]["authoredPurpose"])
        self.assertEqual("analysis-trigger", rows[0]["conditionScope"])

    def test_only_valid_novelty_suppression_can_register_without_delivery(self):
        episode = SimpleNamespace(ai_authored=True, publication_contract_passed=True, contract_failure_code="",
                                  reconciliation={"status": "reconciled", "notificationDecision": "suppress",
                                                  "reasonCode": "unchanged_investment_insight",
                                                  "deliveryOutcome": {"status": "web-only", "queued": False}})
        self.assertTrue(ai_follow_up_registration_admission(episode)["eligible"])
        self.assertTrue(ai_follow_up_registration_admission(episode)["preserveExisting"])
        for key, value in (("ai_authored", False), ("publication_contract_passed", False),
                           ("contract_failure_code", "invalid-candidate")):
            bad = deepcopy(episode)
            setattr(bad, key, value)
            self.assertFalse(ai_follow_up_registration_admission(bad)["eligible"])
        for reason in ("", "stale-candidate", "disabled", "failed", "source-not-delivery-authorized"):
            bad = deepcopy(episode)
            bad.reconciliation["reasonCode"] = reason
            self.assertFalse(ai_follow_up_registration_admission(bad)["eligible"])
        episode.reconciliation["notificationDecision"] = "send"
        self.assertTrue(ai_follow_up_registration_admission(episode)["eligible"])
        self.assertFalse(ai_follow_up_registration_admission(episode)["preserveExisting"])

    def test_confirmed_observations_ignore_jitter_duplicate_clocks_and_already_true_baseline(self):
        proposal = condition()
        self.assertEqual("unregistered", proposal["trackingStatus"])
        self.assertFalse(proposal["notificationOnTransition"])
        row = registered_follow_up(proposal, episode_id="ai:1", account_id="main", symbol="005930",
                                   registered_at=START, owner_kind="ai-insight")
        self.assertTrue(follow_up_is_registered(row))
        self.assertAlmostEqual(0.3, row["observationPolicy"]["minimumBaselineChange"])
        def observe(value, minute, source_minute=None, state="fresh"):
            nonlocal row
            at = f"2026-09-15T00:{minute:02}:00Z"
            source = at if source_minute is None else f"2026-09-15T00:{source_minute:02}:00Z"
            rows, material = evaluate_follow_up_conditions([row], facts(value, source, state=state), at)
            row = rows[0]
            return material
        self.assertFalse(observe(-0.1, 1))
        self.assertFalse(observe(0.01, 2))
        self.assertEqual(0, row["confirmationCount"])
        self.assertFalse(observe(0.25, 3))
        self.assertEqual(1, row["confirmationCount"])
        self.assertFalse(observe(0.25, 4, source_minute=3))
        self.assertFalse(observe(0.25, 5, source_minute=2))
        self.assertEqual(1, row["confirmationCount"])
        self.assertTrue(observe(0.26, 6))
        self.assertEqual("satisfied", row["status"])
        self.assertEqual(2, row["confirmationCount"])
        self.assertFalse(observe(0.27, 7))

        row = registered_follow_up(condition(baseline=0.4), episode_id="ai:2", account_id="main", symbol="005930",
                                   registered_at=START, owner_kind="ai-insight")
        self.assertFalse(observe(0.5, 1))
        self.assertFalse(observe(0.6, 2))
        self.assertFalse(row["armed"])
        self.assertFalse(observe(-0.1, 3))
        self.assertFalse(observe(0.6, 4))
        self.assertTrue(observe(0.7, 5))

    def test_unregistered_stale_and_invalid_data_cannot_claim_automatic_tracking(self):
        proposal = condition()
        response = NotificationAIValidatedResponse(action="NO_ACTION", follow_up_conditions=[proposal])
        context = {"accountId": "main", "rawSymbol": "005930"}
        plan = customer_follow_up_plan(context, response)
        self.assertEqual([], plan["tracked"])
        self.assertIn("자동 관찰 미등록", " ".join(plan["additional"]))
        context["notificationAiValidatedResponse"] = response.to_dict()
        execution_telegram_message(context, response)
        registered = registered_follow_up(proposal, episode_id="ai:1", account_id="main", symbol="005930",
                                          registered_at=START, owner_kind="ai-insight")
        context["followUpRegistration"] = {"conditions": [registered]}
        plan = customer_follow_up_plan(context, response)
        self.assertIn("새 데이터 2회 연속 확인", plan["tracked"][0])
        self.assertNotIn("자동 관찰 미등록", " ".join(plan["additional"]))
        response.follow_up_conditions[0]["conditionId"] = "reworded-condition-id"
        response.follow_up_conditions[0]["label"] = "동일한 가격 회복 조건"
        plan = customer_follow_up_plan(context, response)
        self.assertEqual(1, len(plan["tracked"]))
        self.assertNotIn("자동 관찰 미등록", " ".join(plan["additional"]))
        omitted = NotificationAIValidatedResponse(action="NO_ACTION", follow_up_conditions=[])
        self.assertEqual(1, len(customer_follow_up_plan(context, omitted)["tracked"]))
        job = NotificationJob.create("draft", account_id="main", message_type="investmentInsight", context=context)
        NotificationRenderingService.apply_investment_presentation_contract(job)
        self.assertIn("자동 추적 중", job.context["telegramMessage"])
        self.assertNotIn("자동 관찰 미등록", job.context["telegramMessage"])
        context["accountId"] = "foreign"
        self.assertEqual([], customer_follow_up_plan(context, response)["tracked"])
        for value in ("nan", "inf", "bad", True, None):
            with self.subTest(value=value):
                self.assertEqual(([], []), normalize_follow_up_conditions([
                    {"field": "ma20Distance", "operator": ">=", "threshold": value}], facts(-1), "005930"))
                observed, material = evaluate_follow_up_conditions([registered], facts(value), START)
                self.assertFalse(material)
        stale, material = evaluate_follow_up_conditions([registered], facts(1, state="stale"), START)
        self.assertFalse(material)
        self.assertFalse(stale[0]["trackingBaselineCaptured"])
        expired, material = evaluate_follow_up_conditions([registered], facts(1, "2026-09-23T00:00:00Z"), "2026-09-23T00:00:00Z")
        self.assertEqual("expired", expired[0]["status"])

    def test_confirmed_ai_watch_survives_memory_compaction_and_reopens_analysis_once(self):
        row = registered_follow_up(condition(-1), episode_id="ai:1", account_id="main", symbol="005930",
                                   registered_at=START, owner_kind="ai-insight")
        for minute, value in ((1, -1), (2, 1), (3, 1)):
            at = f"2026-09-15T00:{minute:02}:00Z"
            rows, _ = evaluate_follow_up_conditions([row], facts(value, at), at)
            row = rows[0]
        previous = {"episodeId": "ai:1", "accountId": "main", "symbol": "005930", "createdAt": START,
                    "insight": {"insightAssessment": {"publishable": True}, "followUpConditions": [row]}}
        memory = compact_previous_investment_insight_episode(previous)
        self.assertEqual(memory, compact_previous_investment_insight_episode(memory))
        context = {"accountId": "main", "rawSymbol": "005930", "previousInvestmentAIInsightEpisode": memory,
                   "previousDeliveredInvestmentAIInsightEpisode": deepcopy(memory),
                   "preDecisionDeliveryGate": {"reasonCode": "unchanged_graph_inference"}}
        self.assertEqual(1, len(verified_follow_up_transitions(context)))
        self.assertEqual("proceed", pre_ai_deferred_delivery_decision(context)["decision"])
        from test_notification_ai_delivery import initial_holding_review_context
        delivery = initial_holding_review_context()
        delivery.update({"accountId": "main", "rawSymbol": "005930", "previousInvestmentAIInsightEpisode": deepcopy(memory),
                         "investmentInsightTransition": {"kind": "unchanged-insight", "material": False}})
        final = final_ai_delivery_decision(delivery)
        self.assertEqual("send", final["decision"])
        self.assertEqual("verified-investment-insight-condition", final["pushValueClass"])
        self.assertEqual(1, final["verifiedFollowUpTransitionCount"])
        self.assertEqual(1, final["decisionDelta"]["verifiedFollowUpTransitionCount"])
        from digital_twin.modules.notifications.domain.notification_delivery_explanation import build_customer_delivery_explanation
        explanation = build_customer_delivery_explanation(message_type="investmentInsight", context=delivery,
                                                          source_event_name="investment.inference_episode_completed",
                                                          source_event_id="test:follow-up")
        self.assertEqual("verified-review-follow-up-transition", explanation["primaryCause"]["code"])
        self.assertIn(row["conditionId"], explanation["primaryCause"]["sourceReferences"])
        self.assertEqual(-1, explanation["primaryCause"]["previousValue"])
        self.assertEqual(1, explanation["primaryCause"]["currentValue"])
        self.assertIn("20일 평균 가격 회복", explanation["primaryCause"]["summary"])
        delivery["previousInvestmentAIInsightEpisode"]["createdAt"] = "2026-09-15T00:04:00Z"
        self.assertEqual("suppress", final_ai_delivery_decision(delivery)["decision"])
        delivery["previousInvestmentAIInsightEpisode"]["createdAt"] = START
        delivery["accountId"] = "foreign"
        self.assertEqual("suppress", final_ai_delivery_decision(delivery)["decision"])
        context["previousInvestmentAIInsightEpisode"]["createdAt"] = "2026-09-15T00:04:00Z"
        self.assertEqual([], verified_follow_up_transitions(context))
        context["accountId"] = "foreign"
        self.assertEqual([], verified_follow_up_transitions(context))
