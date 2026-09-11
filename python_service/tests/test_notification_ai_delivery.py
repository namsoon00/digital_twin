import unittest
from datetime import datetime, timezone

from digital_twin.modules.notifications.application.notification_service import NotificationQueueRunner
from digital_twin.modules.notifications.application.notification_ai_gate_message import compact_current_flow_rows
from digital_twin.modules.notifications.application.notification.admission import NotificationAdmissionPolicy
from digital_twin.modules.notifications.domain.notification_ai_delivery import (
    final_ai_delivery_decision,
    pre_ai_deferred_delivery_decision,
)
from digital_twin.modules.notifications.domain.notification_delivery_explanation import (
    build_customer_delivery_explanation,
)
from digital_twin.modules.notifications.domain.notification_rules import NotificationRuleDecision
from digital_twin.modules.notifications.domain.notification_rules import (
    apply_state_cooldown_rule,
    default_notification_rule,
    evaluate_notification_rule,
)
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.domain.ontology_relation_delivery import relation_delivery_diff


class SuppressionQueue:
    def __init__(self):
        self.reason = ""

    def mark_suppressed(self, job, reason):
        self.reason = reason


def initial_holding_review_context(ai_status="completed"):
    context = review_observation_context()
    context["ontologyRelationContext"]["targetRole"] = "holding"
    context["ontologyRelationContext"].setdefault("actionEnvelope", {}).update({
        "targetRole": "holding",
    })
    context["ontologyRelationDiff"] = {
        "material": False,
        "decisionTransition": {
            "kind": "initial",
            "material": False,
            "currentAction": "NO_ACTION",
        },
    }
    context["v2DecisionSynthesis"].update({
        "action_authority": "originate",
        "disposition_code": "HYPOTHESIS_QUALIFICATION_PENDING",
        "execution_eligible_hypothesis_ids": [],
        "review_level": "check",
    })
    context["reasoningDeliveryTrigger"] = {
        "version": "reasoning-delivery-trigger-v1",
        "status": "verified-material-transition",
        "material": True,
        "userObservable": True,
        "kinds": ["verified-market-observation-followup"],
        "materialRevisionKeys": ["revision:nvda:price:2"],
    }
    context["preDecisionDeliveryCadence"] = {
        "eligible": True,
        "minutes": 60,
    }
    context["notificationAiValidatedResponse"] = {
        "action": "NO_ACTION",
        "nextChecks": ["다음 거래일 가격과 거래량을 다시 확인합니다."],
        "insightAssessment": {
            "publishable": True,
            "direction": "positive",
            "directionLabel": "상승 요인 우세",
            "dominantThesis": "가격 회복과 거래 흐름이 단기 상방 관점을 지지합니다.",
            "causalMechanism": "가격 회복이 수급 개선과 연결돼 단기 추세를 강화합니다.",
            "investmentImplication": "보유자는 회복 지속 여부를 기준으로 대응 강도를 판단할 수 있습니다.",
            "invalidationCondition": "가격이 회복 기준 아래로 다시 내려가면 관점을 재검토합니다.",
            "evidenceIds": ["fact:price", "fact:volume"],
        },
    }
    context["investmentInsightTransition"] = {
        "kind": "initial-insight",
        "material": True,
        "currentDirection": "positive",
    }
    context["notificationAiExecutionAudit"] = {
        "status": ai_status,
        "adoptionState": (
            "narrative-adopted-action-not-applicable"
            if ai_status == "completed"
            else "typedb-fallback"
        ),
    }
    return context


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


def review_observation_context(outcome="REVIEW_ONLY"):
    context = context_observation_context(outcome=outcome)
    rule = context["ontologyRelationContext"]["activeRules"][0]
    rule["knowledgeBasis"] = {
        "owner": "statistical-model",
        "ruleKind": "predictive-hypothesis",
        "decisionEligibility": "conditional",
        "requiresHypothesis": True,
    }
    context["ontologyRelationContext"]["matchedRules"] = [rule]
    context["ontologyRelationContext"]["graphStoreInference"]["relations"] = [rule]
    context["ontologyRelationContext"]["graphStoreInference"]["traces"] = [
        {"id": "trace:mstr:risk", **rule}
    ]
    context["v2DecisionSynthesis"] = {
        "selected_rule_id": rule["ruleId"],
        "eligible_hypothesis_ids": ["hypothesis:mstr:risk"],
        "action_authority": "modify",
    }
    context["contextObservationDecision"] = {
        "decisionMode": "typedb-review-observation",
    }
    context["notificationDecisionMode"] = "typedb-review-observation"
    context["cooldownDecision"] = "new-condition"
    context["decisionTransition"] = {
        "kind": "relation-changed",
        "material": True,
    }
    context["deliveryCadenceTier"] = "material"
    context["notificationAiExecutionAudit"] = {
        "status": "typedb-fallback",
        "adoptionState": "typedb-fallback",
    }
    return context


def lifecycle_observation_context(outcome="OBSERVATION"):
    context = context_observation_context(outcome=outcome)
    relation = context["ontologyRelationContext"]
    relation.update({
        "relationLifecycleOnly": True,
        "activeRules": [],
        "matchedRules": [],
        "actionEnvelope": {
            "status": "NO_ELIGIBLE_THESIS",
            "preferredAction": "NO_ACTION",
        },
        "hypothesisLifecycle": {
            "transitions": [{
                "transitionId": "transition:mstr:resolved",
                "lifecycleKey": "v2:account:mstr-risk",
                "lifecycleId": "hypothesis:mstr-risk",
                "scope": "account",
                "previousState": "weakened",
                "currentState": "invalidated",
                "occurredAt": "2026-08-16T00:00:00Z",
                "reason": "이전 위험 관계가 현재 TypeDB 세대에서 더 이상 성립하지 않습니다.",
                "materialChange": True,
            }],
        },
    })
    relation["decision"] = {
        "selectedRuleId": "",
        "basis": "typedbInferenceBox",
        "candidateAction": "NO_ACTION",
    }
    relation["graphStoreInference"]["relations"] = []
    relation["graphStoreInference"]["traces"] = []
    return context


class FinalAIDeliveryTests(unittest.TestCase):
    def _assert_relation_lifecycle_observation_is_web_only_without_user_evidence(self):
        decision = final_ai_delivery_decision(lifecycle_observation_context())

        self.assertEqual("suppress", decision["decision"])
        self.assertEqual("NO_ACTION", decision.get("finalAction"))
        self.assertEqual([], decision["authorizationSources"])
        self.assertEqual(
            "context_observation_web_history",
            decision["suppressionReason"],
        )
        self.assertEqual(
            "resolved",
            decision["relationLifecycleTransition"]["changeKind"],
        )

        triggered = lifecycle_observation_context()
        triggered["reasoningDeliveryTrigger"] = {
            "version": "reasoning-delivery-trigger-v1",
            "status": "verified-material-transition",
            "material": True,
            "userObservable": True,
            "kinds": ["verified-market-observation-followup"],
            "materialRevisionKeys": ["revision:mstr:price:2"],
        }
        triggered_decision = final_ai_delivery_decision(triggered)
        self.assertEqual("send", triggered_decision["decision"])
        self.assertEqual(
            ["verified-reasoning-trigger"],
            triggered_decision["authorizationSources"],
        )

        triggered["ontologyRelationDiff"] = {
            "material": False,
            "decisionTransition": {
                "kind": "initial",
                "material": False,
                "currentAction": "NO_ACTION",
            },
        }
        triggered["contextObservationDeliveryDecision"] = triggered_decision
        triggered["notificationDecisionOwner"] = "typedb"
        triggered["notificationAiBypass"] = {"status": "typedb-direct"}
        triggered["inferenceDispatchDecision"] = {
            "route": "PUBLISH_TYPEDB",
            "details": {"semanticDeliveryDecision": triggered_decision},
        }
        job = NotificationJob.create(
            "검증된 시장 전환",
            account_id="main",
            message_type="investmentInsight",
            context=triggered,
        )
        state_decision = apply_state_cooldown_rule(
            evaluate_notification_rule(
                job,
                default_notification_rule("investmentInsight"),
            ),
            default_notification_rule("investmentInsight"),
            sent_count=0,
            previous_context={},
            job=job,
        )
        self.assertTrue(state_decision.should_send)
        self.assertEqual("new-condition", state_decision.state_decision)
        self.assertNotEqual(
            "initial_graph_baseline",
            state_decision.suppression_reason,
        )

        triggered["reasoningDeliveryTrigger"]["materialRevisionKeys"] = []
        unverified_job = NotificationJob.create(
            "검증 식별자 없는 시장 전환",
            account_id="main",
            message_type="investmentInsight",
            context=triggered,
        )
        unverified_decision = apply_state_cooldown_rule(
            evaluate_notification_rule(
                unverified_job,
                default_notification_rule("investmentInsight"),
            ),
            default_notification_rule("investmentInsight"),
            sent_count=0,
            previous_context={},
            job=unverified_job,
        )
        self.assertFalse(unverified_decision.should_send)
        self.assertEqual(
            "initial_graph_baseline",
            unverified_decision.suppression_reason,
        )

    def test_unchanged_graph_is_deferred_until_follow_up_conditions_are_loaded(self):
        self._assert_relation_lifecycle_observation_is_web_only_without_user_evidence()
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

    def _assert_material_review_delivery_is_not_revoked_by_initial_baseline_rule(self):
        rule = default_notification_rule("investmentInsight")
        context = initial_holding_review_context()
        self.assertEqual("send", final_ai_delivery_decision(context)["decision"])
        job = NotificationJob.create(
            "NVDA 관계 검토",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )

        decision = apply_state_cooldown_rule(
            evaluate_notification_rule(job, rule),
            rule,
            sent_count=0,
            previous_context={},
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("new-condition", decision.state_decision)
        self.assertNotEqual("initial_graph_baseline", decision.suppression_reason)
        explanation = build_customer_delivery_explanation(
            message_type="investmentInsight",
            source_event_name="investment.inference_episode_completed",
            source_event_id="event:nvda:review-completed",
            context=context,
        )
        self.assertEqual("valid", explanation["validation"]["state"])
        self.assertEqual(
            "initial-grounded-investment-insight",
            explanation["primaryCause"]["code"],
        )
        self.assertEqual(
            "insight-transition",
            explanation["primaryCause"]["category"],
        )
        self.assertIn(
            "revision:nvda:price:2",
            explanation["primaryCause"]["sourceReferences"],
        )

        fallback_context = initial_holding_review_context(ai_status="typedb-fallback")
        self.assertEqual(
            "suppress",
            final_ai_delivery_decision(fallback_context)["decision"],
        )
        fallback_job = NotificationJob.create(
            "NVDA 관계 검토 fallback",
            account_id="main",
            message_type="investmentInsight",
            context=fallback_context,
        )
        fallback_decision = apply_state_cooldown_rule(
            evaluate_notification_rule(fallback_job, rule),
            rule,
            sent_count=0,
            previous_context={},
            job=fallback_job,
        )

        self.assertFalse(fallback_decision.should_send)
        self.assertEqual("baseline", fallback_decision.state_decision)
        self.assertEqual(
            "initial_graph_baseline",
            fallback_decision.suppression_reason,
        )

    def test_final_ai_watchlist_insight_is_not_revoked_by_initial_baseline_rule(self):
        rule = default_notification_rule("investmentInsight")
        context = watchlist_context()
        context["ontologyRelationDiff"] = {
            "material": False,
            "decisionTransition": {
                "kind": "initial",
                "material": False,
                "currentAction": "HOLD",
            },
        }
        context["notificationAiValidatedResponse"] = {
            "action": "HOLD",
            "insightAssessment": {"publishable": True, "direction": "negative"},
        }
        context["investmentInsightTransition"] = {
            "kind": "initial-insight",
            "material": True,
            "currentDirection": "negative",
        }
        context["notificationAiExecutionAudit"] = {
            "status": "completed",
            "adoptionState": "decision-and-narrative-adopted",
            "fallback": {"used": False},
        }
        context["notificationWriterProvenance"] = {"aiAuthored": True}
        context["notificationAIInsightProvenance"] = {
            "aiAuthored": True,
            "publicationContractPassed": True,
            "contractFailureCode": "",
        }
        context["decisionPublication"] = {"outcomeKind": "REVIEW_ONLY"}
        context["decisionReconciliation"] = {
            "status": "reconciled",
            "notificationDecision": "send",
            "reasonCode": "initial-grounded-investment-insight",
            "deliveryPolicy": {
                "decision": "send",
                "publicationOutcome": "REVIEW_ONLY",
                "pushValueClass": "initial-grounded-investment-insight",
            },
        }
        job = NotificationJob.create(
            "TSLA 첫 근거 기반 인사이트",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )

        decision = apply_state_cooldown_rule(
            evaluate_notification_rule(job, rule),
            rule,
            sent_count=0,
            previous_context={},
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("new-condition", decision.state_decision)
        self.assertTrue(decision.similarity_bypassed)
        self.assertNotEqual("initial_graph_baseline", decision.suppression_reason)

        context["notificationAIInsightProvenance"]["publicationContractPassed"] = False
        fallback_job = NotificationJob.create(
            "TSLA 미검증 인사이트",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )
        fallback_decision = apply_state_cooldown_rule(
            evaluate_notification_rule(fallback_job, rule),
            rule,
            sent_count=0,
            previous_context={},
            job=fallback_job,
        )

        self.assertFalse(fallback_decision.should_send)
        self.assertEqual("initial_graph_baseline", fallback_decision.suppression_reason)

        context["notificationAIInsightProvenance"]["publicationContractPassed"] = True
        context["decisionPublication"]["outcomeKind"] = "ABSTAIN"
        context["decisionReconciliation"]["deliveryPolicy"]["publicationOutcome"] = "ABSTAIN"
        abstained_job = NotificationJob.create(
            "TSLA 기권 결과",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )
        abstained_decision = apply_state_cooldown_rule(
            evaluate_notification_rule(abstained_job, rule),
            rule,
            sent_count=0,
            previous_context={},
            job=abstained_job,
        )

        self.assertFalse(abstained_decision.should_send)
        self.assertEqual("initial_graph_baseline", abstained_decision.suppression_reason)

    def test_typedb_fallback_never_sends_an_investment_push(self):
        self._assert_material_review_delivery_is_not_revoked_by_initial_baseline_rule()
        context = watchlist_context()
        context["notificationAiExecutionAudit"] = {"status": "typedb-fallback"}

        decision = final_ai_delivery_decision(context)

        self.assertEqual("suppress", decision["decision"])
        self.assertEqual("ai_failure_web_history", decision["suppressionReason"])
        self.assertTrue(decision["typedbFallback"])

        actionless_review = final_ai_delivery_decision(review_observation_context())
        self.assertEqual("suppress", actionless_review["decision"])
        self.assertEqual("ai_failure_web_history", actionless_review["suppressionReason"])
        self.assertEqual("web-only-ai-failure", actionless_review["pushValueClass"])
        self.assertEqual("NO_ACTION", actionless_review.get("finalAction"))
        self.assertTrue(actionless_review["typedbFallback"])

        nonmaterial_review = review_observation_context()
        nonmaterial_review["notificationAiExecutionAudit"] = {
            "status": "completed",
            "adoptionState": "narrative-adopted-action-not-applicable",
        }
        nonmaterial_review["notificationAiValidatedResponse"] = {
            "action": "NO_ACTION",
            "nextChecks": ["다음 거래일 가격과 거래량을 다시 확인합니다."],
            "insightAssessment": initial_holding_review_context()["notificationAiValidatedResponse"]["insightAssessment"],
        }
        nonmaterial_review["decisionTransition"] = {
            "kind": "initial",
            "material": False,
        }
        nonmaterial_review["investmentInsightTransition"] = {
            "kind": "unchanged-insight",
            "material": False,
        }
        nonmaterial_decision = final_ai_delivery_decision(nonmaterial_review)
        self.assertEqual("suppress", nonmaterial_decision["decision"])
        self.assertEqual(
            "unchanged_investment_insight",
            nonmaterial_decision["suppressionReason"],
        )

        review_in_cooldown = review_observation_context()
        review_in_cooldown.update({
            "notificationAiExecutionAudit": {
                "status": "completed",
                "adoptionState": "narrative-adopted-action-not-applicable",
            },
            "ontologyInsight": {
                "semanticComponents": {
                    "materialSourceEventKeys": ["disclosure:MSTR:new"],
                },
            },
            "notificationAiValidatedResponse": {
                "action": "NO_ACTION",
                "nextChecks": ["신규 발행 조건과 주식 수 변화를 확인합니다."],
                "insightAssessment": initial_holding_review_context()["notificationAiValidatedResponse"]["insightAssessment"],
            },
            "investmentInsightTransition": {
                "kind": "initial-insight",
                "material": True,
            },
            "cooldownDecision": "cooldown",
            "cooldownSuppressed": True,
            "cooldownReason": "중요 근거 재알림 간격 60분 전입니다.",
        })
        blocked_review = final_ai_delivery_decision(review_in_cooldown)
        self.assertEqual("suppress", blocked_review["decision"])
        self.assertEqual(
            "review_observation_delivery_cooldown",
            blocked_review["suppressionReason"],
        )

        material_review = review_observation_context()
        material_review.update({
            "notificationAiExecutionAudit": {
                "status": "completed",
                "adoptionState": "narrative-adopted-action-not-applicable",
            },
            "ontologyInsight": {
                "semanticComponents": {
                    "materialSourceEventKeys": ["disclosure:MSTR:new"],
                },
            },
            "notificationAiValidatedResponse": {
                "action": "NO_ACTION",
                "nextChecks": ["신규 발행 조건과 주식 수 변화를 확인합니다."],
                "insightAssessment": initial_holding_review_context()["notificationAiValidatedResponse"]["insightAssessment"],
            },
            "investmentInsightTransition": {
                "kind": "initial-insight",
                "material": True,
            },
        })
        material_decision = final_ai_delivery_decision(material_review)
        self.assertEqual("send", material_decision["decision"])
        self.assertEqual("initial-grounded-investment-insight", material_decision["pushValueClass"])
        self.assertEqual(["material-source-event"], material_decision["authorizationSources"])

        qualification_review = review_observation_context()
        qualification_review["v2DecisionSynthesis"].update({
            "action_authority": "originate",
            "disposition_code": "HYPOTHESIS_QUALIFICATION_PENDING",
            "execution_eligible_hypothesis_ids": [],
        })
        qualification_review.update({
            "notificationAiExecutionAudit": {
                "status": "completed",
                "adoptionState": "narrative-adopted-action-not-applicable",
            },
            "reasoningDeliveryTrigger": {
                "version": "reasoning-delivery-trigger-v1",
                "status": "verified-material-transition",
                "material": True,
                "userObservable": True,
                "kinds": ["verified-market-observation-followup"],
                "materialRevisionKeys": ["revision:mstr:price:2"],
            },
            "preDecisionDeliveryCadence": {
                "eligible": True,
                "minutes": 60,
            },
            "notificationAiValidatedResponse": {
                "action": "NO_ACTION",
                "nextChecks": ["다음 거래일 거래량과 가격 회복 여부를 확인합니다."],
                "insightAssessment": initial_holding_review_context()["notificationAiValidatedResponse"]["insightAssessment"],
            },
            "investmentInsightTransition": {
                "kind": "initial-insight",
                "material": True,
            },
        })
        qualification_decision = final_ai_delivery_decision(qualification_review)
        self.assertEqual("send", qualification_decision["decision"])
        self.assertTrue(qualification_decision["qualificationPending"])
        self.assertEqual(
            ["verified-reasoning-trigger"],
            qualification_decision["authorizationSources"],
        )

        qualification_review["preDecisionDeliveryCadence"]["eligible"] = False
        cadence_decision = final_ai_delivery_decision(qualification_review)
        self.assertEqual("suppress", cadence_decision["decision"])
        self.assertEqual(
            "review_observation_delivery_cooldown",
            cadence_decision["suppressionReason"],
        )

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

        crypto_transition = context_observation_context()
        crypto_transition.update({
            "cooldownDecision": "new-condition",
            "notificationDecisionOwner": "typedb",
            "notificationAiBypass": {"status": "typedb-direct"},
            "reasoningDeliveryTrigger": {
                "version": "reasoning-delivery-trigger-v1",
                "status": "verified-material-transition",
                "material": True,
                "userObservable": True,
                "materialRevisionKeys": ["fact-revision:eth:7d:up:watch"],
                "sourceEventIds": ["event:eth:threshold"],
                "observedAt": "2026-09-09T15:09:28Z",
                "reasons": [
                    "이더리움 7일 변동률 +4.0%가 상승 기준 +4.0%에 도달했습니다."
                ],
                "facts": {
                    "cryptoTransitions": [{"changePct": 4.0}],
                },
            },
        })
        crypto_decision = final_ai_delivery_decision(crypto_transition)
        self.assertEqual("send", crypto_decision["decision"])
        self.assertEqual(
            ["verified-reasoning-trigger"],
            crypto_decision["authorizationSources"],
        )
        crypto_explanation = build_customer_delivery_explanation(
            message_type="investmentInsight",
            source_event_name="investment.inference_episode_completed",
            source_event_id="event:eth:threshold",
            context=crypto_transition,
        )
        self.assertEqual("valid", crypto_explanation["validation"]["state"])
        self.assertEqual(
            "threshold-crossing",
            crypto_explanation["primaryCause"]["category"],
        )
        self.assertIn(
            "이더리움 7일 변동률 +4.0%",
            crypto_explanation["primaryCause"]["summary"],
        )

        material_in_cooldown = context_observation_context(
            material_sources=["news:MSTR:material-1"]
        )
        material_in_cooldown.update({
            "cooldownDecision": "cooldown",
            "cooldownSuppressed": True,
        })
        blocked_material = final_ai_delivery_decision(material_in_cooldown)
        self.assertEqual("suppress", blocked_material["decision"])
        self.assertEqual(
            "context_observation_delivery_cooldown",
            blocked_material["suppressionReason"],
        )

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

    def _assert_relation_lifecycle_resolution_is_material_without_new_action(self):
        def relation(transition_id="", state=""):
            lifecycle = {"transitions": []}
            if transition_id:
                lifecycle["transitions"] = [{
                    "transitionId": transition_id,
                    "lifecycleKey": "v2:account:trend",
                    "previousState": "weakened",
                    "currentState": state,
                    "occurredAt": "2026-08-16T00:00:00Z",
                    "reason": "이전 관계가 더 이상 성립하지 않습니다.",
                    "materialChange": True,
                }]
            return {
                "source": "typedbInferenceBox",
                "graphStoreUsed": True,
                "fallbackUsed": False,
                "decision": {
                    "basis": "typedbInferenceBox",
                    "selectedRuleId": "graph.test.v1" if not transition_id else "",
                    "candidateAction": "HOLD" if not transition_id else "NO_ACTION",
                },
                "actionEnvelope": {
                    "status": "HOLDING_REVIEW" if not transition_id else "NO_ELIGIBLE_THESIS",
                    "preferredAction": "HOLD" if not transition_id else "NO_ACTION",
                },
                "activeRules": [{"ruleId": "graph.test.v1"}] if not transition_id else [],
                "hypothesisLifecycle": lifecycle,
            }

        diff = relation_delivery_diff(
            relation("transition:resolved", "invalidated"),
            relation(),
        )

        self.assertTrue(diff["changed"])
        self.assertTrue(diff["material"])
        self.assertEqual("relation-resolved", diff["decisionTransition"]["kind"])
        self.assertIn("relationLifecycleTransition", diff["materialComponents"])

    def test_candidate_only_watchlist_change_is_suppressed(self):
        self._assert_relation_lifecycle_resolution_is_material_without_new_action()
        decision = final_ai_delivery_decision(watchlist_context())

        self.assertEqual("suppress", decision["decision"])
        self.assertIn("최종 AI 행동", decision["reason"])

        migration_churn = watchlist_context()
        migration_churn.update({
            "cooldownDecision": "meaningful-change",
            "decisionTransition": {
                "kind": "relation-strengthened",
                "material": True,
                "previousAction": "no_action",
                "currentAction": "no_action",
                "relationLifecycleTransition": {
                    "changeKind": "strengthened",
                    "material": True,
                    "evidenceDelta": {
                        "removedCounterEvidenceKeys": [
                            "counter:migration-slot:legacy:1",
                            "counter:migration-slot:legacy:2",
                        ],
                        "rotatedAddedSupportingEvidenceIds": ["relation-evidence:new"],
                        "rotatedRemovedSupportingEvidenceIds": ["relation-evidence:old"],
                    },
                },
            },
            "investmentNotificationTransition": {
                "changed": False,
                "material": False,
                "kind": "unchanged",
            },
        })

        churn_decision = final_ai_delivery_decision(migration_churn)

        self.assertEqual("suppress", churn_decision["decision"])
        self.assertEqual(
            "internal_relation_lifecycle_churn",
            churn_decision["suppressionReason"],
        )
        self.assertFalse(churn_decision["observableRelationEvidenceChanged"])
        self.assertNotIn("deliveryAuthorization", churn_decision)
        self.assertEqual(
            "decision-delta-v1",
            churn_decision["effectiveDeliveryPolicy"],
        )
        self.assertEqual(
            "match",
            churn_decision["deliveryPolicyParity"]["status"],
        )
        explanation = build_customer_delivery_explanation(
            message_type="investmentInsight",
            source_event_name="investment.reasoning.completed",
            context=migration_churn,
        )
        self.assertEqual("invalid", explanation["validation"]["state"])
        self.assertIn("primary-cause-missing", explanation["validation"]["errors"])

    def test_final_ai_action_change_is_sent(self):
        decision = final_ai_delivery_decision(watchlist_context(ai_kind="action-changed"))

        self.assertEqual("send", decision["decision"])
        self.assertEqual("decision-delta-v1", decision["effectiveDeliveryPolicy"])
        self.assertEqual("match", decision["deliveryPolicyParity"]["status"])

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
            "nextChecks": [
                "다음 가격·수급 갱신에서도 관계가 유지되는지 확인합니다."
            ],
        })
        vague = final_ai_delivery_decision(canonical)
        canonical["notificationAiValidatedResponse"].update({
            "nextChecks": [
                "현재가가 20일선 아래로 이탈하거나 외국인이 순매도로 "
                "전환되는지 확인합니다."
            ],
        })
        complete = final_ai_delivery_decision(canonical)

        self.assertEqual("suppress", incomplete["decision"])
        self.assertEqual("incomplete_customer_action_contract", incomplete["suppressionReason"])
        self.assertEqual("suppress", vague["decision"])
        self.assertEqual("incomplete_customer_action_contract", vague["suppressionReason"])
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

        market_threshold = watchlist_context()
        market_threshold.update({
            "cooldownDecision": "meaningful-change",
            "deliveryTriggerLedger": [{
                "triggerId": "repeat-transition:insight_ma60_crossed_above",
                "conditionId": "insight_ma60_crossed_above",
                "kind": "verified-market-transition",
                "status": "matched",
                "label": "60일 평균 위로 회복",
                "reason": "60일 평균 위로 회복 -0.4 -> 0.7",
            }],
        })
        threshold_decision = final_ai_delivery_decision(market_threshold)

        self.assertEqual("send", threshold_decision["decision"])
        self.assertEqual(
            "verified-market-threshold-transition",
            threshold_decision["pushValueClass"],
        )
        self.assertEqual(1, threshold_decision["verifiedMarketTransitionCount"])
        explanation = build_customer_delivery_explanation(
            message_type="investmentInsight",
            source_event_name="investment.reasoning.completed",
            context=market_threshold,
        )
        self.assertEqual("valid", explanation["validation"]["state"])
        self.assertEqual(
            "threshold-crossing",
            explanation["primaryCause"]["category"],
        )

        context = watchlist_context()
        context["cooldownDecision"] = "scheduled-summary"
        context["decisionTransition"] = {"kind": "unchanged", "material": False}
        context["investmentNotificationTransition"] = {
            "kind": "unchanged",
            "changed": False,
            "material": False,
        }

        decision = final_ai_delivery_decision(context)

        self.assertEqual("suppress", decision["decision"])
        self.assertEqual(
            "scheduled_summary_web_history",
            decision["suppressionReason"],
        )
        self.assertEqual("web-only-scheduled-summary", decision["pushValueClass"])

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

        migration_only = {
            "preDecisionDeliveryGate": {"reasonCode": "unchanged_graph_inference"},
            "decisionContinuityPacket": {"followUpConditions": []},
            "decisionTransition": {
                "kind": "relation-strengthened",
                "material": True,
                "relationLifecycleTransition": {
                    "evidenceDelta": {
                        "removedCounterEvidenceKeys": [
                            "counter:migration-slot:legacy:1",
                        ],
                    },
                },
            },
        }

        migration_decision = pre_ai_deferred_delivery_decision(migration_only)

        self.assertEqual("suppress", migration_decision["decision"])
        self.assertFalse(migration_decision["materialGraphTransition"])
        self.assertFalse(migration_decision["observableRelationEvidenceChanged"])

    def test_holding_and_watchlist_baseline_delivery_boundaries(self):
        context = watchlist_context()
        context["ontologyRelationContext"]["targetRole"] = "holding"
        context["ontologyRelationContext"]["actionEnvelope"]["targetRole"] = "holding"

        decision = final_ai_delivery_decision(context)

        self.assertEqual("suppress", decision["decision"])
        self.assertEqual("graph_candidate_only_change", decision["suppressionReason"])

        first_holding = watchlist_context()
        first_holding["aiDecisionTransition"] = {
            "historyAvailable": True,
            "kind": "unchanged",
            "previousAction": "HOLD",
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
        first_decision = final_ai_delivery_decision(first_holding)
        self.assertEqual("send", first_decision["decision"])
        self.assertEqual("first-final-holding-decision", first_decision["pushValueClass"])

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
