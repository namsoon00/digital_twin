import unittest
from datetime import datetime, timezone

from digital_twin.application.notification_service import NotificationQueueRunner
from digital_twin.application.notification_ai_gate_message import compact_current_flow_rows
from digital_twin.application.notification.admission import NotificationAdmissionPolicy
from digital_twin.domain.notification_ai_delivery import (
    final_ai_delivery_decision,
    pre_ai_deferred_delivery_decision,
)
from digital_twin.domain.notification_rules import NotificationRuleDecision
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.ontology_relation_delivery import relation_delivery_diff


class SuppressionQueue:
    def __init__(self):
        self.reason = ""

    def mark_suppressed(self, job, reason):
        self.reason = reason


def watchlist_context(ai_kind="unchanged", material_sources=None):
    return {
        "notificationAiValidatedResponse": {"action": "HOLD"},
        "aiDecisionTransition": {
            "historyAvailable": True,
            "kind": ai_kind,
            "previousAction": "BUY" if ai_kind == "action-changed" else "HOLD",
            "currentAction": "HOLD",
        },
        "decisionTransition": {
            "kind": "action-changed",
            "material": True,
            "previousAction": "BUY",
            "currentAction": "HOLD",
        },
        "ontologyRelationContext": {
            "targetRole": "watchlist",
            "actionEnvelope": {
                "targetRole": "watchlist",
                "selectedRuleId": "graph.recovery.v1",
                "dataReadiness": {"eligibleRuleIds": ["graph.recovery.v1"]},
            },
        },
        "ontologyInsight": {
            "semanticComponents": {
                "materialSourceEventKeys": list(material_sources or []),
            },
        },
    }


def graph_risk_context(material=True):
    return {
        "messageType": "investmentInsight",
        "market": "KR",
        "symbol": "005930",
        "marketHoursEnabled": True,
        "marketHoursMarkets": ["KR", "US"],
        "body": "본문에 손실과 분할축소라는 말이 포함됩니다.",
        "ontologyRelationDiff": {
            "material": material,
            "reason": "행동 범위 변경" if material else "동일 행동 범위",
            "decisionTransition": {
                "kind": "action-changed" if material else "unchanged",
                "material": material,
                "currentAction": "TRIM" if material else "HOLD",
            },
        },
        "ontologyRelationContext": {
            "source": "typedbInferenceBox",
            "graphStoreUsed": True,
            "fallbackUsed": False,
            "decision": {
                "basis": "typedbInferenceBox",
                "decisionStage": "RISK_REVIEW",
                "actionGroup": "lossControl",
                "primaryAction": "TRIM_REVIEW",
            },
            "decisionState": {
                "reviewLevel": "act",
                "dataState": "sufficient",
            },
            "actionEnvelope": {"preferredAction": "TRIM"},
        },
    }


def context_observation_context(outcome="OBSERVATION", material_sources=None):
    rule = {
        "ruleId": "graph.benchmark.beta.context.v1",
        "label": "벤치마크 베타 점검",
        "matched": True,
        "knowledgeBasis": {
            "owner": "ontology-semantic",
            "ruleKind": "context-observation",
            "decisionEligibility": "reference-only",
            "requiresHypothesis": False,
        },
    }
    return {
        "messageType": "investmentInsight",
        "symbol": "MSTR",
        "rawLines": "현재가: $132.38\n수익률: +46.0%",
        "decisionPublication": {"outcomeKind": outcome},
        "notificationAiValidatedResponse": {"action": "NO_ACTION"},
        "notificationAiExecutionAudit": {
            "status": "completed",
            "adoptionState": "narrative-adopted-action-not-applicable",
        },
        "ontologyInsight": {
            "semanticComponents": {
                "materialSourceEventKeys": list(material_sources or []),
            },
        },
        "ontologyRelationContext": {
            "source": "typedbInferenceBox",
            "graphStore": "typedb",
            "graphStoreUsed": True,
            "fallbackUsed": False,
            "sourceAboxSnapshotId": "abox:mstr:1",
            "inferenceGenerationId": "generation:mstr:1",
            "generationAligned": True,
            "subject": {"symbol": "MSTR", "market": "US"},
            "facts": {
                "symbol": "MSTR",
                "market": "US",
                "currency": "USD",
                "currentPrice": 132.38,
                "averagePrice": 90.884491,
                "profitLossRate": 45.65741394944153,
            },
            "activeRules": [rule],
            "matchedRules": [rule],
            "decision": {
                "selectedRuleId": rule["ruleId"],
                "basis": "typedbInferenceBox",
            },
            "graphStoreInference": {
                "graphStore": "typedb",
                "sourceAboxSnapshotId": "abox:mstr:1",
                "inferenceGenerationId": "generation:mstr:1",
                "relations": [rule],
                "traces": [{"id": "trace:mstr:1", **rule}],
            },
        },
    }


class FinalAIDeliveryTests(unittest.TestCase):
    def test_unchanged_graph_is_deferred_until_follow_up_conditions_are_loaded(self):
        policy = NotificationAdmissionPolicy()
        context = graph_risk_context(material=False)
        context["investmentSubjectDecisionCaseId"] = "subject-case:unchanged"
        job = NotificationJob.create(
            "test",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )
        decision = NotificationRuleDecision(
            message_type="investmentInsight",
            enabled=True,
            should_send=False,
            delivery_state="suppressed",
            gate_state="blocked",
            gate_reason="그래프 판단이 직전과 같습니다.",
            suppression_reason="unchanged_graph_inference",
            state_suppressed=True,
            state_decision="unchanged-inference",
            state_reason="그래프 판단이 직전과 같습니다.",
        )

        outcome = policy.apply_result(job, decision)

        self.assertTrue(outcome.accepted)
        self.assertEqual("pending", job.status)
        self.assertEqual(
            "unchanged_graph_inference",
            job.context["preDecisionDeliveryGate"]["reasonCode"],
        )

    def test_repeat_cooldown_is_deferred_until_after_subject_decision(self):
        policy = NotificationAdmissionPolicy()
        context = graph_risk_context(material=True)
        context["investmentSubjectDecisionCaseId"] = "subject-case:1"
        job = NotificationJob.create(
            "test",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )
        decision = NotificationRuleDecision(
            message_type="investmentInsight",
            enabled=True,
            should_send=False,
            delivery_state="suppressed",
            gate_state="blocked",
            gate_reason="같은 판단 상태",
            suppression_reason="state_cooldown",
            state_suppressed=True,
            state_decision="cooldown",
            state_reason="같은 판단 상태가 쿨다운 중입니다.",
        )

        outcome = policy.apply_result(job, decision)

        self.assertTrue(outcome.accepted)
        self.assertEqual("pending", job.status)
        self.assertEqual(
            "deferred",
            job.context["preDecisionDeliveryGate"]["status"],
        )
        self.assertEqual(
            "delivery-only",
            job.context["preDecisionDeliveryGate"]["decisionBoundary"],
        )
        self.assertNotIn("deliverySuppressionReason", job.context)

    def test_closed_market_admission_remains_deliverable(self):
        policy = NotificationAdmissionPolicy()
        job = NotificationJob.create(
            "test",
            account_id="main",
            message_type="investmentInsight",
            context=graph_risk_context(material=True),
        )
        decision = NotificationRuleDecision(
            message_type="investmentInsight",
            enabled=True,
            should_send=False,
            delivery_state="suppressed",
            gate_state="blocked",
            gate_reason="미장 닫힘",
            suppression_reason="market_closed",
            market_hours_enabled=True,
            market_hours_status="closed",
            market_hours_reason="미장 닫힘",
        )

        outcome = policy.apply_result(job, decision)

        self.assertTrue(outcome.accepted)
        self.assertEqual("pending", job.status)
        self.assertEqual("eligible", job.context["deliveryDecision"])
        self.assertEqual("advisory", job.context["marketHoursDecision"])
        self.assertNotIn("preDecisionDeliveryGate", job.context)
        self.assertNotIn("deliverySuppressionReason", job.context)

    def test_typedb_fallback_is_suppressed_when_only_readiness_label_changed(self):
        context = watchlist_context()
        context["notificationAiExecutionAudit"] = {"status": "typedb-fallback"}
        context["decisionTransition"] = {
            "kind": "readiness-context-changed",
            "material": False,
            "previousAction": "HOLD",
            "currentAction": "HOLD",
        }

        decision = final_ai_delivery_decision(context)

        self.assertEqual("suppress", decision["decision"])
        self.assertTrue(decision["typedbFallback"])

    def test_typedb_fallback_never_sends_an_investment_push(self):
        context = watchlist_context()
        context["notificationAiExecutionAudit"] = {"status": "typedb-fallback"}

        decision = final_ai_delivery_decision(context)

        self.assertEqual("suppress", decision["decision"])
        self.assertEqual("ai_failure_web_history", decision["suppressionReason"])
        self.assertTrue(decision["typedbFallback"])

        review_only = watchlist_context(ai_kind="action-changed")
        review_only.update({
            "investmentSubjectDecisionCaseId": "subject-case:review-only",
            "decisionPublication": {"outcomeKind": "REVIEW_ONLY"},
            "notificationAiExecutionAudit": {
                "status": "typedb-fallback",
                "adoptionState": "typedb-fallback",
            },
            "notificationWriterProvenance": {"aiAuthored": False},
        })
        review_decision = final_ai_delivery_decision(review_only)

        self.assertEqual("suppress", review_decision["decision"])
        self.assertEqual("review_only_web_history", review_decision["suppressionReason"])

        baseline = final_ai_delivery_decision(context_observation_context())
        self.assertEqual("suppress", baseline["decision"])
        self.assertEqual("context_observation_web_history", baseline["suppressionReason"])
        self.assertEqual([], baseline["authorizationSources"])

        material = final_ai_delivery_decision(
            context_observation_context(material_sources=["news:MSTR:material-1"])
        )
        self.assertEqual("send", material["decision"])
        self.assertEqual("material-context-observation", material["pushValueClass"])
        self.assertEqual(["material-source-event"], material["authorizationSources"])

        queue = SuppressionQueue()
        runner = NotificationQueueRunner(
            queue,
            account_repository=None,
            notifier_factory=lambda account: None,
        )
        observation_job = NotificationJob.create(
            "test",
            account_id="main",
            message_type="investmentInsight",
            context=context_observation_context(outcome="ABSTAIN"),
        )
        self.assertFalse(runner.apply_final_ai_delivery_gate(observation_job))
        self.assertEqual(
            "review_only_web_history",
            observation_job.context["deliverySuppressionReason"],
        )
        self.assertEqual(
            "suppress",
            observation_job.context["finalAiDeliveryGate"]["decision"],
        )
        self.assertTrue(queue.reason)

        rows = compact_current_flow_rows(context_observation_context())
        self.assertIn("현재가 $132.38", rows)
        self.assertIn("수익률 +45.7%", rows)
        self.assertNotIn("수익률 +46.0%", rows)

    def test_same_missing_data_does_not_make_readiness_label_churn_material(self):
        def relation(readiness):
            return {
                "decision": {
                    "selectedRuleId": "graph.cross-asset.relative-strength.v1",
                    "candidateAction": "HOLD",
                },
                "actionEnvelope": {
                    "status": "HOLDING_REVIEW",
                    "preferredAction": "HOLD",
                    "dataReadiness": {"state": readiness},
                    "judgementBlocked": False,
                },
                "decisionState": {"dataState": "partial" if readiness == "partial" else "sufficient"},
                "missingData": [{"key": "valuation", "label": "밸류에이션 입력값"}],
                "activeRules": [{"ruleId": "graph.cross-asset.relative-strength.v1"}],
            }

        diff = relation_delivery_diff(relation("ready"), relation("partial"))

        self.assertTrue(diff["changed"])
        self.assertFalse(diff["material"])
        self.assertEqual("readiness-context-changed", diff["decisionTransition"]["kind"])

    def test_candidate_only_watchlist_change_is_suppressed(self):
        decision = final_ai_delivery_decision(watchlist_context())

        self.assertEqual("suppress", decision["decision"])
        self.assertIn("최종 AI 행동", decision["reason"])

    def test_final_ai_action_change_is_sent(self):
        decision = final_ai_delivery_decision(watchlist_context(ai_kind="action-changed"))

        self.assertEqual("send", decision["decision"])

        canonical = watchlist_context(ai_kind="action-changed")
        canonical.update({
            "investmentSubjectDecisionCaseId": "subject-case:final",
            "decisionPublication": {"outcomeKind": "FINAL_DECISION"},
            "notificationAiExecutionAudit": {
                "status": "completed",
                "adoptionState": "decision-and-narrative-adopted",
            },
            "notificationWriterProvenance": {"aiAuthored": True},
        })
        incomplete = final_ai_delivery_decision(canonical)
        canonical["notificationAiValidatedResponse"].update({
            "currentActionPlan": "현재 보유를 유지합니다.",
            "changeAnalysis": "최종 행동이 이전 판단과 달라졌습니다.",
            "nextChecks": ["다음 가격·수급 갱신에서 조건 유지 여부를 확인합니다."],
        })
        complete = final_ai_delivery_decision(canonical)

        self.assertEqual("suppress", incomplete["decision"])
        self.assertEqual("incomplete_customer_action_contract", incomplete["suppressionReason"])
        self.assertEqual("send", complete["decision"])
        self.assert_explicit_profit_loss_authorization_survives_unchanged_ai_action()

    def assert_explicit_profit_loss_authorization_survives_unchanged_ai_action(self):
        context = watchlist_context()
        context["cooldownDecision"] = "typedb-profit-loss-change"

        decision = final_ai_delivery_decision(context)

        self.assertEqual("send", decision["decision"])
        self.assertEqual("profit-loss-threshold-transition", decision["pushValueClass"])
        self.assertEqual("typedb-profit-loss-change", decision["deliveryAuthorization"])

    def test_non_material_graph_rebaseline_cannot_send_action_change(self):
        context = watchlist_context(ai_kind="action-changed")
        context["notificationAiValidatedResponse"]["action"] = "BUY"
        context["aiDecisionTransition"].update({
            "previousAction": "HOLD",
            "currentAction": "BUY",
        })
        context["decisionTransition"] = {
            "kind": "initial",
            "material": False,
            "previousAction": "",
            "currentAction": "BUY",
        }
        context["investmentNotificationTransition"] = {
            "changed": True,
            "material": True,
            "kind": "action-changed",
        }

        decision = final_ai_delivery_decision(context)

        self.assertEqual("suppress", decision["decision"])
        self.assertEqual("non_material_action_rebaseline", decision["suppressionReason"])

    def test_decision_changing_source_is_sent_even_when_action_is_unchanged(self):
        decision = final_ai_delivery_decision(
            watchlist_context(material_sources=["main:news:035720:article-1"]),
        )

        self.assertEqual("send", decision["decision"])
        self.assertEqual(1, decision["materialSourceEventCount"])

        follow_up = watchlist_context()
        follow_up["decisionTransition"] = {"kind": "unchanged", "material": False}
        follow_up["decisionContinuityPacket"] = {"followUpConditions": [{
            "conditionId": "follow-up:ma20",
            "status": "satisfied",
            "previousMatched": False,
            "currentMatched": True,
            "transitionVerified": True,
            "transitionAt": "2026-08-31T06:12:00Z",
        }]}
        follow_up_decision = final_ai_delivery_decision(follow_up)

        self.assertEqual("send", follow_up_decision["decision"])
        self.assertEqual("verified-threshold-transition", follow_up_decision["pushValueClass"])

    def test_pre_ai_unchanged_gate_requires_verified_transition_or_material_evidence(self):
        unchanged = {
            "preDecisionDeliveryGate": {"reasonCode": "unchanged_graph_inference"},
            "decisionContinuityPacket": {"followUpConditions": []},
            "decisionTransition": {"kind": "unchanged", "material": False},
        }

        suppressed = pre_ai_deferred_delivery_decision(unchanged)

        self.assertEqual("suppress", suppressed["decision"])
        self.assertEqual(
            "unchanged_graph_without_decision_value",
            suppressed["suppressionReason"],
        )

        verified = dict(unchanged)
        verified["decisionContinuityPacket"] = {"followUpConditions": [{
            "conditionId": "follow-up:price",
            "status": "satisfied",
            "previousMatched": False,
            "currentMatched": True,
            "transitionVerified": True,
            "transitionAt": "2026-09-01T00:00:00Z",
        }]}

        proceeded = pre_ai_deferred_delivery_decision(verified)

        self.assertEqual("proceed", proceeded["decision"])
        self.assertEqual("verified-threshold-transition", proceeded["pushValueClass"])

    def test_holding_and_watchlist_baseline_delivery_boundaries(self):
        context = watchlist_context()
        context["ontologyRelationContext"]["targetRole"] = "holding"
        context["ontologyRelationContext"]["actionEnvelope"]["targetRole"] = "holding"

        decision = final_ai_delivery_decision(context)

        self.assertEqual("suppress", decision["decision"])
        self.assertEqual("graph_candidate_only_change", decision["suppressionReason"])

        first_holding = watchlist_context()
        first_holding["aiDecisionTransition"] = {
            "historyAvailable": False,
            "kind": "initial",
            "previousAction": "",
            "currentAction": "HOLD",
        }
        first_holding["decisionTransition"] = {
            "kind": "initial",
            "material": False,
            "previousAction": "",
            "currentAction": "HOLD",
        }
        first_holding["ontologyRelationContext"].update({
            "targetRole": "holding",
            "decisionState": {"reviewLevel": "check"},
        })
        first_holding["ontologyRelationContext"]["actionEnvelope"]["targetRole"] = "holding"
        first_holding["cooldownDecision"] = "new-condition"
        first_holding["cooldownRecentSentCount"] = 0
        self.assertEqual("send", final_ai_delivery_decision(first_holding)["decision"])

        first_holding.update({
            "cooldownDecision": "new-condition",
            "cooldownRecentSentCount": 0,
            "notificationAiExecutionAudit": {"status": "typedb-fallback"},
            "aiDecisionTransition": {
                "historyAvailable": True,
                "kind": "unchanged",
                "previousAction": "HOLD",
                "currentAction": "HOLD",
            },
        })
        fallback_decision = final_ai_delivery_decision(first_holding)
        self.assertEqual("suppress", fallback_decision["decision"])
        self.assertEqual("ai_failure_web_history", fallback_decision["suppressionReason"])
        self.assertTrue(fallback_decision["typedbFallback"])

        first_watchlist = watchlist_context()
        first_watchlist["aiDecisionTransition"] = {
            "historyAvailable": False,
            "kind": "initial",
            "previousAction": "",
            "currentAction": "HOLD",
        }
        first_watchlist["decisionTransition"] = {
            "kind": "initial",
            "material": False,
            "previousAction": "",
            "currentAction": "HOLD",
        }
        first_watchlist["ontologyRelationContext"]["decisionState"] = {"reviewLevel": "check"}
        watchlist_decision = final_ai_delivery_decision(first_watchlist)
        self.assertEqual("suppress", watchlist_decision["decision"])
        self.assertEqual("initial_graph_baseline", watchlist_decision["suppressionReason"])

    def test_nearly_expired_investment_snapshot_requests_refresh_without_blocking_ai(self):
        queue = SuppressionQueue()
        requested = []
        runner = NotificationQueueRunner(
            queue,
            account_repository=None,
            notifier_factory=lambda account: None,
            settings={
                "notificationAiGateEnabled": "1",
                "notificationAiFreshnessReserveMinutes": "4",
            },
            ai_request_enqueuer=object(),
            fresh_data_recheck_requester=lambda account, symbol, job_id: requested.append(symbol) or {"requested": True},
        )
        job = NotificationJob.create(
            "test",
            account_id="main",
            message_type="investmentInsight",
            context={
                "messageType": "investmentInsight",
                "rawSymbol": "005930",
                "dataFreshnessAgeMinutes": 7,
                "dataFreshnessMaxAgeMinutes": 10,
            },
        )

        allowed = runner.apply_ai_freshness_headroom_gate(job)

        self.assertTrue(allowed)
        self.assertEqual(["005930"], requested)
        self.assertEqual("advisory", job.context["aiFreshnessHeadroomGate"]["decision"])
        self.assertTrue(job.context["aiFreshnessHeadroomGate"]["blockingDisabled"])
        self.assertNotIn("deliverySuppressionReason", job.context)
        self.assertEqual("", queue.reason)

    def test_closed_market_is_advisory_before_ai_and_at_dispatch(self):
        queue = SuppressionQueue()
        runner = NotificationQueueRunner(
            queue,
            account_repository=None,
            notifier_factory=lambda account: None,
            now_provider=lambda: datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc),
        )
        context = graph_risk_context(material=False)
        context.pop("ontologyRelationDiff")
        context.pop("ontologyRelationContext")
        job = NotificationJob.create(
            "test",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )

        self.assertTrue(runner.apply_market_hours_gate(job, "AI 판단 전"))
        self.assertEqual("closed", job.context["marketHoursStatus"])
        self.assertFalse(job.context["preAiMarketHoursAssessment"]["blocking"])

        self.assertTrue(runner.apply_market_hours_gate(job, "발송 직전"))
        self.assertEqual("send", job.context["dispatchMarketHoursGate"]["decision"])
        self.assertTrue(job.context["dispatchMarketHoursGate"]["blockingDisabled"])
        self.assertEqual("advisory", job.context["marketHoursDecision"])
        self.assertNotIn("deliverySuppressionReason", job.context)
        self.assertEqual("", queue.reason)

    def test_material_typedb_risk_transition_records_closed_market_without_special_case(self):
        queue = SuppressionQueue()
        runner = NotificationQueueRunner(
            queue,
            account_repository=None,
            notifier_factory=lambda account: None,
            now_provider=lambda: datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc),
        )
        job = NotificationJob.create(
            "test",
            account_id="main",
            message_type="investmentInsight",
            context=graph_risk_context(material=True),
        )

        self.assertTrue(runner.apply_market_hours_gate(job, "발송 직전"))
        self.assertEqual("closed", job.context["marketHoursStatus"])
        self.assertEqual("advisory", job.context["marketHoursDecision"])
        self.assertNotIn("TypeDB 관계", job.context["marketHoursReason"])


if __name__ == "__main__":
    unittest.main()
