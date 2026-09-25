import tempfile
import unittest
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from digital_twin.modules.decisions.application.ai_inference_queue_service import AIInferenceQueueRunner, NotificationAIRequestEnqueuer, ai_failure_diagnostic, ai_response_contract_error, preserve_verified_ai_narrative, typedb_inference_fallback_response
from digital_twin.modules.decisions.application.ai_inference_queue_service import ai_attempt_queue_wait_ms
from digital_twin.modules.notifications.application.ai_insight_notification_projection import AIInsightNotificationProjectionService
from digital_twin.modules.decisions.application.notification_ai_gate_audit import context_with_validated_ai_response
from digital_twin.modules.notifications.application.notification.admission import NotificationAdmissionOutcome
from digital_twin.modules.notifications.application.notification_service import NotificationQueueRunner
from digital_twin.modules.decisions.domain.ai_inference_queue import (
    AIInferenceRequest,
    AIInferenceResult,
    notification_ai_material_fingerprint,
)
from digital_twin.modules.decisions.domain.investment_reasoning.ai_insight import (
    AIInsightHandoff,
    decision_reconciliation,
)
from digital_twin.modules.decisions.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.modules.decisions.domain.notification_ai_inference_packet import build_notification_ai_inference_packet
from digital_twin.modules.decisions.domain.notification_ai_prompt_release import AI_DECISION_PROMPT_VERSION
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.infrastructure.ai_usage_metrics import ai_execution_usage
from mysql_fixtures import (
    TestAIInferenceQueueStore,
    TestNotificationJobStore,
    mysql_execute,
    mysql_fetchone,
    reset_mysql_test_database,
    test_store_seed,
)


class AttemptQueueTimingTest(unittest.TestCase):
    def test_retry_wait_excludes_prior_model_attempt_and_backoff(self):
        request = SimpleNamespace(created_at="2026-09-16T00:00:00Z", available_at="2026-09-16T00:15:00Z",
                                  started_at="2026-09-16T00:00:01Z", updated_at="2026-09-16T00:15:03Z", attempts=2)
        self.assertEqual(3000, ai_attempt_queue_wait_ms(request))

    def test_unknown_ready_time_is_not_reported_as_zero_wait(self):
        self.assertIsNone(ai_attempt_queue_wait_ms(SimpleNamespace(started_at="2026-09-16T00:15:03Z")))
        self.assertIsNone(ai_attempt_queue_wait_ms(SimpleNamespace(available_at="2026-09-16T00:15:00Z",
                                                                   started_at="2026-09-16T00:00:01Z")))

    def assert_execution_usage_sums_only_model_attempt_usage(self):
        usage = ai_execution_usage({
            "executionSpans": {"modelAttempts": [
                {"modelOutput": {"usage": {
                    "input_tokens": 100, "cached_input_tokens": 60,
                    "output_tokens": 25, "reasoning_output_tokens": 15,
                }}},
                {"modelOutput": {"usage": {
                    "input_tokens": 80, "cached_input_tokens": 20,
                    "output_tokens": 10, "reasoning_output_tokens": 7,
                }}},
            ]},
        })

        self.assertEqual(2, usage["model_call_count"])
        self.assertEqual(180, usage["input_tokens"])
        self.assertEqual(80, usage["cached_input_tokens"])
        self.assertEqual(35, usage["output_tokens"])
        self.assertEqual(22, usage["reasoning_output_tokens"])


class FakeReviewer:
    def review(self, context):
        return NotificationAIValidatedResponse(
            action="HOLD",
            action_label="보유",
            validation_state="ready",
            validation_label="검증 완료",
            data_state="sufficient",
            data_state_label="판단에 필요한 자료 있음",
            review_level="check",
            review_label="조건 확인",
            summary="현재 근거를 유지하되 다음 데이터 변화를 확인합니다.",
            opinion="현재 행동을 유지하고 반대 근거가 생기는지 확인합니다.",
            current_action_plan="지금은 보유하며 새 주문은 보류합니다.",
            change_analysis="새 판단 조건이 처음 확인됐습니다.",
            next_action_plan="다음 데이터 업데이트에서 같은 관계가 유지되는지 확인합니다.",
            evidence=["TypeDB 관계 근거가 확인됐습니다."],
            counter_evidence=["반대 근거도 계속 확인해야 합니다."],
            invalidation_condition="현재 관계가 사라지면 의견을 다시 봅니다.",
            next_checks=["다음 추론 세대 확인"],
            source="fake max AI",
            raw_response='{"action":"HOLD","narrativeClaims":[]}',
            narrative_claims=[
                {
                    "claimId": "claim:view",
                    "section": "view",
                    "text": "현재 행동을 유지하고 다음 데이터 변화를 확인합니다.",
                    "evidenceIds": ["fact:currentPrice"],
                },
                {
                    "claimId": "claim:change",
                    "section": "change",
                    "text": "새 판단 조건이 처음 확인됐습니다.",
                    "evidenceIds": ["fact:currentPrice"],
                },
                {
                    "claimId": "claim:next",
                    "section": "next-condition",
                    "text": "다음 데이터 업데이트에서 같은 관계가 유지되는지 확인합니다.",
                    "evidenceIds": ["fact:currentPrice"],
                },
            ],
        )


class RecordingDecisionStore:
    def __init__(self):
        self.saved = []
        self.observations = []

    def record_observation(self, account_id, symbol, facts, observed_at):
        self.observations.append((account_id, symbol, dict(facts or {}), observed_at))

    def save(self, episode):
        self.saved.append(episode)
        return episode


class AIInferenceQueueTests(unittest.TestCase):
    def assert_review_followups_reach_outbox_once_with_the_final_reason_preserved(self):
        self.setUp()
        from copy import deepcopy
        from test_notification_ai_delivery import reconciled_review_job
        from digital_twin.modules.notifications.domain.notification_ai_delivery import final_ai_delivery_decision
        from digital_twin.modules.notifications.domain.notification.presentation import notification_kind
        for symbol in ("035720", "028260"):
            with self.subTest(symbol=symbol):
                job = reconciled_review_job(symbol)
                handoff = AIInsightHandoff.create(job.context, job.to_dict())
                request = AIInferenceRequest.create_for_subject_decision(
                    job, job.context, handoff, model="test-model", reasoning_effort="max",
                )
                self.queue.enqueue_subject_decision(job, request)
                claimed = self.queue.claim("review-replay", 1, 60)[0]
                context = dict(claimed.context)
                context["decisionReconciliation"] = decision_reconciliation(handoff, final_ai_delivery_decision(context))
                result = AIInferenceResult.create(
                    claimed, context["notificationAiValidatedResponse"], source="test AI",
                    validation_state="ready", latency_ms=10, prompt_bytes=100,
                )
                self.assertTrue(self.complete_detached(claimed, "review-replay", result, context))
                saved = self.notifications.get(job.job_id)
                self.assertIsNotNone(saved, context["decisionReconciliation"])
                self.assertEqual("pending", saved.status)
                self.assertEqual("NO_ACTION", saved.context["notificationAiValidatedResponse"]["action"])
                self.assertEqual("ai-interpretation", notification_kind(saved.message_type, saved.context).key)
                episode = self.queue.latest_insight_episodes("test-account", symbol, 1)[0]
                self.assertEqual("verified-investment-insight-condition", episode["reconciliation"]["reasonCode"])
                self.assertEqual("send", episode["reconciliation"]["notificationDecision"])
                duplicate = deepcopy(saved)
                self.assertFalse(self.notifications.enqueue(duplicate))
                self.assertEqual("duplicate_notification_job", duplicate.context["deliverySuppressionReason"])
                self.assertEqual("pending", self.notifications.get(job.job_id).status)
        self.assertEqual(2, mysql_fetchone(self.seed, "SELECT COUNT(*) FROM notification_jobs")[0])
        delivered = []
        account = SimpleNamespace(account_id="test-account", quiet_hours_active=lambda *_args: False)
        notifier = SimpleNamespace(send=lambda message: delivered.append(message) or SimpleNamespace(
            delivered=True, label="in-memory test transport", metadata={"receiptVerified": True},
        ))
        runner = NotificationQueueRunner(
            self.notifications, SimpleNamespace(load_all=lambda: [account]), lambda _account: notifier,
        )
        self.assertEqual(2, runner.run_once(limit=2), runner.last_run_details)
        self.assertEqual(2, len(delivered), runner.last_run_details)
        self.assertEqual(0, runner.run_once(limit=2))
        for symbol in ("035720", "028260"):
            self.assertEqual(1, len(self.queue.latest_delivered_insight_episodes("test-account", symbol)))

    def test_max_reasoning_model_gets_a_long_enough_execution_watchdog(self):
        runner = AIInferenceQueueRunner(
            None,
            None,
            {"notificationAiAttemptWatchdogSeconds": "300"},
        )

        self.assertEqual(
            900,
            runner.effective_attempt_watchdog_seconds(
                SimpleNamespace(model="gpt-5.6-sol", reasoning_effort="max")
            ),
        )
        self.assertEqual(
            300,
            runner.effective_attempt_watchdog_seconds(
                SimpleNamespace(model="gpt-5.6-sol", reasoning_effort="high")
            ),
        )

    def create_detached_request(self, subject_case_id="subject:detached:1"):
        job = NotificationJob.create(
            "detached AI insight draft",
            account_id="main",
            account_label="메인",
            message_type="investmentInsight",
            source_event_id="event:inference:detached",
            source_event_name="investment.inference_episode_completed",
            context={
                "messageType": "investmentInsight",
                "accountId": "main",
                "rawSymbol": "005930",
                "investmentReasoningCaseId": "case:detached",
                "investmentSubjectDecisionCaseId": subject_case_id,
                "investmentSubjectDecisionCase": {
                    "subjectCaseId": subject_case_id,
                    "batchCaseId": "case:detached",
                    "stage": "READY",
                    "accountId": "main",
                    "symbol": "005930",
                    "sourceAboxSnapshotId": "abox:detached",
                    "inferenceGenerationId": "generation:detached",
                    "candidateSetId": "candidate-set:detached",
                    "candidateFingerprint": "a" * 64,
                },
                "decisionCandidateFingerprint": "a" * 64,
                "ontologyRelationContext": {
                    "subject": {"symbol": "005930", "name": "삼성전자"},
                    "sourceAboxSnapshotId": "abox:detached",
                    "inferenceGenerationId": "generation:detached",
                    "reviewLevel": "act",
                    "changeState": "new-condition",
                    "actionEnvelope": {
                        "executionAction": "HOLD",
                        "allowedActions": ["HOLD", "ADD"],
                    },
                },
            },
        )
        handoff = AIInsightHandoff.create(job.context, job.to_dict())
        context = {**job.context, "investmentAIInsightHandoff": handoff.to_dict()}
        job.context = context
        request = AIInferenceRequest.create_for_subject_decision(
            job,
            context,
            handoff,
            model="gpt-5.6-sol",
            reasoning_effort="max",
        )
        return job, request

    def complete_detached(self, request, worker_id, result, context):
        projector = AIInsightNotificationProjectionService()
        return self.queue.complete(
            request,
            worker_id,
            result,
            context,
            delivery_projection=lambda completed: projector.prepare(request, completed),
            after_complete=lambda _connection, outcome: projector.reconcile(context, outcome),
        )

    def test_insight_read_model_requires_transport_receipt_for_delivery_memory(self):
        job, request = self.create_detached_request("subject:delivery-memory")
        self.queue.enqueue_subject_decision(job, request)
        claimed = self.queue.claim("worker-delivery-memory", 1, 60)[0]
        result = AIInferenceResult.create(
            claimed, {"action": "NO_ACTION", "insightAssessment": {"publishable": True}},
            source="test AI", validation_state="ready", latency_ms=10, prompt_bytes=100,
        )
        context = {
            **claimed.context,
            "notificationAiValidatedResponse": result.response,
            "notificationAiExecutionAudit": {
                "status": "completed", "adoptionState": "narrative-adopted-action-not-applicable",
            },
            "notificationWriterProvenance": {"aiAuthored": True},
            "decisionReconciliation": {
                "status": "reconciled", "notificationDecision": "send", "reason": "new insight",
            },
        }
        self.assertTrue(self.complete_detached(claimed, "worker-delivery-memory", result, context))

        def latest():
            return self.queue.latest_insight_episodes("main", "005930", 1)[0]

        saved = self.notifications.get(job.job_id)
        self.assertEqual("pending", latest()["notificationDelivery"]["status"])
        self.assertEqual([], self.queue.latest_delivered_insight_episodes("main", "005930"))
        self.notifications.mark_suppressed(saved, "unchanged graph")
        self.assertEqual("suppressed", latest()["notificationDelivery"]["status"])
        self.assertEqual("send", latest()["reconciliation"]["semanticNotificationDecision"])
        self.notifications.mark_done(saved)
        self.assertEqual("unconfirmed", latest()["notificationDelivery"]["status"])
        self.assertEqual([], self.queue.latest_delivered_insight_episodes("main", "005930"))
        attempt = self.notifications.start_delivery_attempt(saved, "accountNotification", "account")
        self.notifications.complete_delivery_attempt(saved, attempt, True, provider="test transport")
        self.assertEqual("delivered", latest()["notificationDelivery"]["status"])
        self.assertEqual(1, len(self.queue.latest_delivered_insight_episodes("main", "005930")))
        self.assertEqual([], self.queue.latest_delivered_insight_episodes("other", "005930"))
        self.assertEqual([], self.queue.latest_delivered_insight_episodes("main", "MSTR"))
        saved.account_id = "other"
        self.notifications.update(saved)
        self.assertEqual([], self.queue.latest_delivered_insight_episodes("main", "005930"))
        saved.account_id = "main"
        self.notifications.update(saved)
        mysql_execute(self.seed, "DELETE FROM notification_jobs WHERE job_id = %s", (job.job_id,))
        mysql_execute(self.seed, "DELETE FROM ai_inference_results WHERE result_id = %s", (result.result_id,))
        retained = self.queue.latest_delivered_insight_episodes("main", "005930")
        self.assertEqual(1, len(retained))
        self.assertTrue(retained[0]["notificationDelivery"]["delivered"])
        self.assertTrue(retained[0]["publicationContractPassed"])
        mysql_execute(self.seed, "UPDATE investment_ai_insight_episodes SET payload_json = JSON_REMOVE(payload_json, '$.publicationContractPassed') WHERE episode_id = %s", (retained[0]["episodeId"],))
        self.assertEqual([], self.queue.latest_delivered_insight_episodes("main", "005930"))

    def assert_prompt_budget_failure_is_non_retryable_and_safe_to_persist(self):
        diagnostic = ai_failure_diagnostic(
            ValueError(
                "AI decision core cannot preserve TypeDB hypotheses within 6145 bytes "
                "(minimum contract requires 6923 bytes)"
            ),
            "ai-preparation",
        )

        self.assertEqual("prompt-contract-budget", diagnostic["category"])
        self.assertFalse(diagnostic["retryable"])
        self.assertIn("6145 bytes", diagnostic["safeDetail"])

    def test_ai_watch_registration_is_atomic_durable_and_independent_of_trade_decisions(self):
        from digital_twin.infrastructure.transactions.decision_history_parts.follow_ups import evaluate_follow_up_observation, acknowledge_follow_up_reasoning
        from digital_twin.modules.outcomes.infrastructure import transaction_writes as writes
        from digital_twin.modules.outcomes.domain.follow_up_tracking import follow_up_is_registered
        from test_ai_follow_up_tracking import condition, facts

        job, request = self.create_detached_request("subject:watch-registration")
        self.queue.enqueue_subject_decision(job, request)
        claimed = self.queue.claim("worker-watch", 1, 60)[0]
        result = AIInferenceResult.create(
            claimed, {"action": "NO_ACTION", "insightAssessment": {"publishable": True}, "followUpConditions": [condition(-1)]},
            source="test AI", validation_state="ready", latency_ms=10, prompt_bytes=100,
        )
        context = {**claimed.context, "notificationAiValidatedResponse": result.response,
                   "notificationAiExecutionAudit": {"status": "completed", "adoptionState": "narrative-adopted-action-not-applicable"},
                   "notificationWriterProvenance": {"aiAuthored": True},
                   "decisionReconciliation": {"status": "reconciled", "notificationDecision": "send", "reason": "new insight"}}
        original_writer = writes.register_ai_insight_followups
        def fail_after_registration(*args, **kwargs):
            original_writer(*args, **kwargs)
            raise RuntimeError("registration transaction rehearsal")
        with patch.object(writes, "register_ai_insight_followups", side_effect=fail_after_registration):
            with self.assertRaisesRegex(RuntimeError, "transaction rehearsal"):
                self.complete_detached(claimed, "worker-watch", result, dict(context))
        for table in ("investment_decision_follow_ups", "investment_ai_insight_episodes", "notification_jobs"):
            self.assertEqual(0, mysql_fetchone(self.seed, "SELECT COUNT(*) FROM " + table)[0])
        self.assertTrue(self.complete_detached(claimed, "worker-watch", result, context))
        self.assertEqual(0, mysql_fetchone(self.seed, "SELECT COUNT(*) FROM investment_decision_episodes")[0])
        saved = self.queue.latest_insight_episodes("main", "005930", 1)[0]
        watch = saved["insight"]["followUpConditions"][0]
        self.assertTrue(follow_up_is_registered(watch))
        self.assertEqual("ai-insight", watch["ownerKind"])
        self.assertEqual(watch, self.notifications.get(job.job_id).context["followUpRegistration"]["conditions"][0])
        registered_at = datetime.fromisoformat(watch["registration"]["registeredAt"].replace("Z", "+00:00"))
        def observe(value, minute, account="main", symbol="005930"):
            at = (registered_at + timedelta(minutes=minute)).isoformat().replace("+00:00", "Z")
            return evaluate_follow_up_observation(account, symbol, facts(value, at), at,
                                                  _connect=self.queue.connect, utc_now_iso=lambda: at)
        self.assertEqual([], observe(-1, 1))
        self.assertEqual([], observe(1, 2, account="foreign"))
        self.assertEqual([], observe(1, 2, symbol="MSTR"))
        self.assertEqual([], observe(1, 2))
        self.assertEqual([], observe(1, 2))
        transitions = observe(1, 3)
        self.assertEqual(1, len(transitions))
        self.assertEqual("pending", transitions[0]["reasoningDispatchStatus"])
        replay = observe(2, 4)
        self.assertEqual(transitions[0]["transitionId"], replay[0]["transitionId"])
        self.assertEqual(transitions[0]["sourceSnapshotObservedAt"], replay[0]["sourceSnapshotObservedAt"])
        self.assertEqual("satisfied", self.queue.latest_insight_episodes("main", "005930", 1)[0]["insight"]["followUpConditions"][0]["status"])
        acknowledge_follow_up_reasoning("main", watch["conditionId"], transitions[0]["transitionId"], _connect=self.queue.connect)
        self.assertEqual([], observe(2, 5))
        mysql_execute(self.seed, "DELETE FROM notification_jobs WHERE job_id = %s", (job.job_id,))
        mysql_execute(self.seed, "DELETE FROM ai_inference_results WHERE result_id = %s", (result.result_id,))
        self.assertEqual("satisfied", self.queue.latest_insight_episodes("main", "005930", 1)[0]["insight"]["followUpConditions"][0]["status"])

    def test_subject_decision_ai_is_independent_idempotent_and_delivery_gated(self):
        AttemptQueueTimingTest.assert_execution_usage_sums_only_model_attempt_usage(self)
        self.assert_review_followups_reach_outbox_once_with_the_final_reason_preserved()
        self.assert_prompt_budget_failure_is_non_retryable_and_safe_to_persist()
        self.assert_review_only_subject_queues_narrative_without_action_transition()
        self.assert_subject_decision_ai_exists_before_notification_and_promotes_after_completion()
        self.assert_subject_decision_ai_web_only_result_never_creates_notification()
        self.assert_subject_decision_notification_admission_is_reflected_in_episode()
        self.assert_subject_decision_ai_failure_never_creates_notification()
        self.assert_subject_decision_queue_coalesces_same_material_meaning()
        self.assert_subject_cost_control_defers_repetitive_work_but_not_action_changes()
        self.assert_material_source_and_lifecycle_changes_replace_ai_work()

    def test_duplicate_insight_keeps_watches_without_notifications_or_confirmation_resets(self):
        from copy import deepcopy
        from digital_twin.infrastructure.transactions.decision_history_parts.follow_ups import evaluate_follow_up_observation, acknowledge_follow_up_reasoning
        from digital_twin.infrastructure.event_bus import EventBus
        from digital_twin.modules.market_data.application.monitoring_service import MonitorRunner
        from mysql_fixtures import TestEventLog
        from digital_twin.modules.outcomes.infrastructure import transaction_writes as writes
        from digital_twin.modules.outcomes.domain.follow_up_tracking import follow_up_is_registered
        from digital_twin.modules.decisions.domain.investment_insight_assessment import compact_previous_investment_insight_episode
        from digital_twin.modules.notifications.domain.notification_ai_delivery import verified_follow_up_transitions
        from test_ai_follow_up_tracking import condition, facts

        def complete(index, proposals, *, valid=True, reason="unchanged_investment_insight", thesis="recovery"):
            job, _ = self.create_detached_request("subject:duplicate:" + str(index))
            job.context["reasoningDeliveryTrigger"] = {
                "material": True, "userObservable": True, "changedFields": ["marketObservationFollowup"],
                "materialRevisionKeys": ["revision:" + str(index)],
            }
            handoff = AIInsightHandoff.from_dict(job.context["investmentAIInsightHandoff"])
            request = AIInferenceRequest.create_for_subject_decision(
                job, job.context, handoff, model="gpt-5.6-sol", reasoning_effort="max")
            self.assertEqual("awaiting-ai-insight", self.queue.enqueue_subject_decision(job, request)["status"])
            claimed = self.queue.claim("worker-duplicate", 1, 60)[0]
            result = AIInferenceResult.create(
                claimed, {"action": "NO_ACTION", "insightAssessment": {"publishable": True, "thesisKey": thesis},
                          "followUpConditions": deepcopy(proposals)},
                source="test AI", validation_state="ready", latency_ms=10, prompt_bytes=100)
            context = {**claimed.context, "notificationAiValidatedResponse": result.response,
                       "notificationAiExecutionAudit": {"status": "completed", "adoptionState": "narrative-adopted-action-not-applicable" if valid else "executed-not-adopted"},
                       "notificationWriterProvenance": {"aiAuthored": valid},
                       "decisionReconciliation": {"status": "reconciled", "notificationDecision": "suppress", "reasonCode": reason}}
            if index == 1:
                original = writes.register_ai_insight_followups
                def fail(*args, **kwargs):
                    original(*args, **kwargs)
                    raise RuntimeError("web-only registration rollback")
                with patch.object(writes, "register_ai_insight_followups", side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError, "registration rollback"):
                        self.complete_detached(claimed, "worker-duplicate", result, dict(context))
                self.assertEqual(0, mysql_fetchone(self.seed, "SELECT COUNT(*) FROM investment_decision_follow_ups")[0])
                self.assertEqual(0, mysql_fetchone(self.seed, "SELECT COUNT(*) FROM investment_ai_insight_episodes")[0])
            self.assertTrue(self.complete_detached(claimed, "worker-duplicate", result, context))
            self.assertIsNone(self.notifications.get(job.job_id))
            return self.queue.latest_insight_episodes("main", "005930", 1)[0]

        first = complete(1, [condition(-1)])
        watch = first["insight"]["followUpConditions"][0]
        self.assertTrue(follow_up_is_registered(watch))
        self.assertEqual("unchanged-valid-insight", first["insight"]["followUpRegistration"]["admission"]["reason"])
        start = datetime.fromisoformat(watch["registration"]["registeredAt"].replace("Z", "+00:00"))
        def observe(value, minute):
            at = (start + timedelta(minutes=minute)).isoformat().replace("+00:00", "Z")
            return evaluate_follow_up_observation("main", "005930", facts(value, at), at,
                                                 _connect=self.queue.connect, utc_now_iso=lambda: at)
        def persisted():
            return json.loads(mysql_fetchone(self.seed, "SELECT payload_json FROM investment_decision_follow_ups WHERE condition_id=%s", (watch["conditionId"],))[0])
        self.assertEqual([], observe(-1, 1))
        self.assertEqual([], observe(1, 2))
        confirmed_once = persisted()
        self.assertEqual(1, confirmed_once["confirmationCount"])
        reworded = {**condition(1), "conditionId": "new-ai-wording", "label": "회복 여부 확인",
                    "expiresAt": (start + timedelta(days=30)).isoformat()}
        second = complete(2, [reworded])
        self.assertEqual(confirmed_once, persisted())
        self.assertEqual(confirmed_once, second["insight"]["followUpConditions"][0])
        self.assertEqual(first["episodeId"], second["insight"]["followUpConditions"][0]["episodeId"])
        complete(3, [], valid=False)
        self.assertEqual(confirmed_once, persisted())
        complete(4, [], reason="disabled")
        self.assertEqual(confirmed_once, persisted())
        omitted = complete(5, [])
        self.assertEqual(confirmed_once, omitted["insight"]["followUpConditions"][0])
        transitions = observe(1.1, 3)
        self.assertEqual(1, len(transitions))
        memory = compact_previous_investment_insight_episode(self.queue.latest_insight_episodes("main", "005930", 1)[0])
        self.assertEqual(1, len(verified_follow_up_transitions({
            "accountId": "main", "rawSymbol": "005930", "previousInvestmentAIInsightEpisode": memory})))
        event_bus = EventBus(recorder=TestEventLog(self.seed).handle)
        observer = SimpleNamespace(acknowledge_follow_up_reasoning=lambda account, condition_id, transition_id:
                                   acknowledge_follow_up_reasoning(account, condition_id, transition_id, _connect=self.queue.connect))
        runner = MonitorRunner([], None, None, None, None, event_publisher=event_bus, investment_outcome_observer=observer)
        snapshot = SimpleNamespace(account_id="main", generated_at=transitions[0]["transitionAt"])
        observation = {"followUpObservation": {"transitions": transitions}}
        runner.publish_follow_up_transition_reasoning(snapshot, observation)
        runner.publish_follow_up_transition_reasoning(snapshot, observation)
        self.assertEqual(2, mysql_fetchone(self.seed, "SELECT COUNT(*) FROM domain_events WHERE name IN (%s,%s)",
                                           ("investment.follow_up_transitioned", "ontology.reasoning_requested"))[0])
        self.assertEqual(watch["conditionId"], event_bus.published[-1].payload["sourceFacts"][0]["payload"]["conditionId"])
        self.assertEqual([], observe(2, 4))
        reached = persisted()
        complete("6-unadopted", [], valid=False)
        complete("6-disabled", [], reason="disabled")
        continued = complete(6, [reworded])
        self.assertEqual(reached, continued["insight"]["followUpConditions"][0])
        self.assertEqual(1, mysql_fetchone(self.seed, "SELECT COUNT(*) FROM investment_decision_follow_ups")[0])

        changed = complete(7, [{**condition(1, threshold=2), "conditionId": "threshold-revised"}])
        replacement = changed["insight"]["followUpConditions"][0]
        self.assertNotEqual(watch["conditionId"], replacement["conditionId"])
        self.assertEqual("pending", replacement["status"])
        self.assertFalse(replacement["trackingBaselineCaptured"])
        latest = complete(8, [{**condition(1, threshold=3), "conditionId": "threshold-revised"}])
        self.assertEqual(1, len(latest["insight"]["followUpConditions"]))
        self.assertEqual("superseded", mysql_fetchone(self.seed, "SELECT status FROM investment_decision_follow_ups WHERE condition_id=%s", (replacement["conditionId"],))[0])
        changed_thesis = complete(9, [{**condition(1, threshold=3)}], thesis="new-thesis")
        self.assertNotEqual(latest["insight"]["followUpConditions"][0]["conditionId"], changed_thesis["insight"]["followUpConditions"][0]["conditionId"])
        for table in ("notification_jobs", "investment_decision_episodes", "notification_delivery_attempts"):
            self.assertEqual(0, mysql_fetchone(self.seed, "SELECT COUNT(*) FROM " + table)[0])

    def assert_material_source_and_lifecycle_changes_replace_ai_work(self):
        job, _request = self.create_detached_request("subject:detached:fingerprint")
        baseline = json.loads(json.dumps(job.context))
        triggered = json.loads(json.dumps(baseline))
        triggered["reasoningDeliveryTrigger"] = {
            "material": True,
            "userObservable": True,
            "kinds": ["verified-market-observation-followup"],
            "reasons": ["verified-observation-followup"],
            "changedFields": ["marketObservationFollowup"],
            "materialRevisionKeys": ["revision:price:1"],
            "observedAt": "2026-09-09T00:00:00Z",
        }
        repeated = json.loads(json.dumps(triggered))
        repeated["reasoningDeliveryTrigger"]["observedAt"] = "2026-09-09T00:01:00Z"
        changed = json.loads(json.dumps(triggered))
        changed["reasoningDeliveryTrigger"]["materialRevisionKeys"] = ["revision:price:2"]

        self.assertNotEqual(
            notification_ai_material_fingerprint(baseline),
            notification_ai_material_fingerprint(triggered),
        )
        self.assertEqual(
            notification_ai_material_fingerprint(triggered),
            notification_ai_material_fingerprint(repeated),
        )
        self.assertNotEqual(
            notification_ai_material_fingerprint(triggered),
            notification_ai_material_fingerprint(changed),
        )

        lifecycle = json.loads(json.dumps(baseline))
        lifecycle["relationLifecycleTransition"] = {
            "material": True,
            "changeKind": "created",
            "lifecycleKey": "v2:account:price-recovery",
            "previousState": "",
            "currentState": "observed",
            "occurredAt": "2026-09-09T00:00:00Z",
        }
        invalidated = json.loads(json.dumps(lifecycle))
        invalidated["relationLifecycleTransition"].update({
            "changeKind": "resolved",
            "previousState": "observed",
            "currentState": "invalidated",
            "occurredAt": "2026-09-09T01:00:00Z",
        })
        self.assertNotEqual(
            notification_ai_material_fingerprint(lifecycle),
            notification_ai_material_fingerprint(invalidated),
        )

    def assert_review_only_subject_queues_narrative_without_action_transition(self):
        class Queue:
            def __init__(self):
                self.requests = []

            def enqueue_subject_decision(self, job, request):
                self.requests.append((job, request))
                return {
                    "status": "awaiting-ai-insight",
                    "requestId": request.request_id,
                    "notificationJobId": "",
                    "reservedNotificationJobId": request.notification_job_id,
                }

        class Orchestrator:
            ai_queue_transitions = 0
            capture_calls = 0

            def capture_ai_context(self, _subject_case_id, context):
                self.capture_calls += 1
                return dict(context)

            def ai_queued(self, *_args):
                self.ai_queue_transitions += 1

            def case_superseded(self, *_args):
                raise AssertionError("narrative-only subjects must keep their terminal publication")

        job, _request = self.create_detached_request("subject:detached:review-only")
        job.context.pop("investmentAIInsightHandoff", None)
        job.context["investmentSubjectDecisionCase"]["stage"] = "REVIEW_ONLY"
        job.context["investmentSubjectDecisionCase"]["publication"] = {
            "outcomeKind": "REVIEW_ONLY",
        }
        rule = {
            "ruleId": "graph.company.risk.review.v1",
            "knowledgeBasis": {
                "owner": "ontology-semantic",
                "ruleKind": "predictive-hypothesis",
                "decisionEligibility": "conditional",
                "requiresHypothesis": True,
            },
        }
        relation = job.context["ontologyRelationContext"]
        relation.update({
            "source": "typedbInferenceBox",
            "graphStoreUsed": True,
            "fallbackUsed": False,
            "activeRules": [rule],
            "matchedRules": [rule],
            "decision": {
                "selectedRuleId": rule["ruleId"],
                "basis": "typedbInferenceBox",
            },
        })
        job.context["v2DecisionSynthesis"] = {
            "selected_rule_id": rule["ruleId"],
            "eligible_hypothesis_ids": ["hypothesis:review-only"],
            "action_authority": "modify",
        }
        queue = Queue()
        orchestrator = Orchestrator()
        job.context["notificationAiReviewMode"] = "investment-judgement"

        def prepare(job):
            job.context["notificationAiReviewMode"] = "investment-judgement"

        outcome = NotificationAIRequestEnqueuer(
            queue,
            reasoning_orchestrator=orchestrator,
            context_preparer=prepare,
        ).enqueue_subject_decision(job)

        self.assertEqual("awaiting-ai-insight", outcome["status"])
        self.assertEqual(1, len(queue.requests))
        queued_request = queue.requests[0][1]
        self.assertEqual("context-narrative", queued_request.review_mode)
        self.assertTrue(queued_request.detached_from_notification)
        self.assertEqual(0, orchestrator.ai_queue_transitions)
        self.assertEqual(1, orchestrator.capture_calls)

        response = NotificationAIValidatedResponse(
            action="ADD",
            hypotheses=[{
                "hypothesisId": "hypothesis:review-only",
                "claim": "가격 회복이 이어질 수 있습니다.",
                "verdict": "supported",
                "reasoning": "가격과 수급 근거가 같은 방향입니다.",
            }],
            selected_hypothesis_id="hypothesis:review-only",
            hypothesis_comparison_state="completed",
            hypothesis_selection_source="ai",
        )
        validated = context_with_validated_ai_response(
            {**job.context, "notificationAiReviewMode": "context-narrative"},
            response,
        )["notificationAiValidatedResponse"]
        self.assertEqual("NO_ACTION", validated["action"])
        self.assertEqual("", validated["selectedHypothesisId"])
        self.assertEqual(
            "hypothesis:review-only",
            validated["researchLeadHypothesisId"],
        )
        self.assertEqual("research-reviewed", validated["hypothesisComparisonState"])
        self.assertTrue(validated["hypotheses"][0]["researchOnly"])

    def assert_subject_decision_ai_exists_before_notification_and_promotes_after_completion(self):
        self.setUp()
        job, request = self.create_detached_request()

        outcome = self.queue.enqueue_subject_decision(job, request)

        self.assertEqual("awaiting-ai-insight", outcome["status"])
        self.assertEqual("", outcome["notificationJobId"])
        self.assertEqual(job.job_id, outcome["reservedNotificationJobId"])
        fail_closed = decision_reconciliation(
            AIInsightHandoff.from_dict(job.context["investmentAIInsightHandoff"]),
            {},
        )
        self.assertEqual("suppress", fail_closed["notificationDecision"])
        self.assertEqual("", fail_closed["notificationJobId"])
        self.assertIsNone(self.notifications.get(job.job_id))
        claimed = self.queue.claim("worker-detached", 1, 60)[0]
        result = AIInferenceResult.create(
            claimed,
            {"action": "HOLD", "summary": "보유 조건이 유지됩니다."},
            source="fake max AI",
            validation_state="ready",
            latency_ms=10,
            prompt_bytes=100,
        )
        completed_context = {
            **claimed.context,
            "notificationAiValidatedResponse": result.response,
            "decisionReconciliation": {
                "version": "investment-decision-reconciliation-v1",
                "status": "reconciled",
                "subjectCaseId": request.origin_id,
                "candidateFingerprint": "a" * 64,
                "inferenceGenerationId": "generation:detached",
                "notificationDecision": "send",
                "notificationJobId": job.job_id,
                "reason": "새 최종 판단을 전달합니다.",
            },
        }

        self.assertTrue(
            self.complete_detached(
                claimed,
                "worker-detached",
                result,
                completed_context,
            )
        )
        queued_notification = self.notifications.get(job.job_id)
        self.assertIsNotNone(queued_notification)
        self.assertEqual(
            "notification-queued",
            queued_notification.context["aiInsightNotificationProjection"]["status"],
        )
        self.assertTrue(
            queued_notification.context["decisionReconciliation"]["deliveryOutcome"][
                "queued"
            ]
        )
        row = mysql_fetchone(
            self.seed,
            "SELECT model, reasoning_effort, notification_job_id, payload_json "
            "FROM investment_ai_insight_episodes WHERE request_id = %s",
            (request.request_id,),
        )
        self.assertEqual(("gpt-5.6-sol", "max", job.job_id), tuple(row[:3]))
        self.assertEqual(request.prompt_version, json.loads(row[3])["promptVersion"])
        repeat_job, repeat = self.create_detached_request("subject:detached:completed-repeat")
        repeat_outcome = self.queue.enqueue_subject_decision(repeat_job, repeat)
        self.assertEqual("coalesced-material", repeat_outcome["status"])
        self.assertIsNone(self.notifications.get(repeat_job.job_id))
        mysql_execute(
            self.seed,
            "UPDATE ai_inference_requests SET completed_at = %s WHERE request_id = %s",
            ("2000-01-01T00:00:00Z", request.request_id),
        )
        self.assertEqual(1, self.queue.prune_terminal(retention_hours=1))
        self.assertIsNone(self.queue.get(request.request_id))
        durable_job, durable = self.create_detached_request(request.origin_id)
        durable_outcome = self.queue.enqueue_subject_decision(durable_job, durable)
        self.assertEqual("completed-insight", durable_outcome["status"])
        self.assertIsNone(self.notifications.get(durable_job.job_id))
        request_count = mysql_fetchone(
            self.seed,
            "SELECT COUNT(*) FROM ai_inference_requests",
        )
        self.assertEqual(0, int(request_count[0]))

    def assert_subject_decision_ai_web_only_result_never_creates_notification(self):
        from test_ai_follow_up_tracking import condition
        self.setUp()
        job, request = self.create_detached_request("subject:detached:web-only")
        self.queue.enqueue_subject_decision(job, request)
        claimed = self.queue.claim("worker-web-only", 1, 60)[0]
        result = AIInferenceResult.create(
            claimed,
            {"action": "NO_ACTION", "summary": "판단 변화가 없습니다.", "followUpConditions": [condition(-1)]},
            source="fake max AI",
            validation_state="ready",
            latency_ms=10,
            prompt_bytes=100,
        )
        completed_context = {
            **claimed.context,
            "notificationAiExecutionAudit": {"status": "completed", "adoptionState": "narrative-adopted-action-not-applicable"},
            "notificationWriterProvenance": {"aiAuthored": True},
            "decisionReconciliation": {
                "version": "investment-decision-reconciliation-v1",
                "status": "reconciled",
                "subjectCaseId": request.origin_id,
                "candidateFingerprint": "a" * 64,
                "inferenceGenerationId": "generation:detached",
                "notificationDecision": "suppress",
                "notificationJobId": "",
                "reasonCode": "unchanged_investment_insight",
                "reason": "직전 판단과 동일합니다.",
            },
        }

        self.assertTrue(
            self.complete_detached(
                claimed,
                "worker-web-only",
                result,
                completed_context,
            )
        )
        self.assertIsNone(self.notifications.get(job.job_id))
        row = mysql_fetchone(
            self.seed,
            "SELECT notification_job_id FROM investment_ai_insight_episodes "
            "WHERE request_id = %s",
            (request.request_id,),
        )
        self.assertEqual("", row[0])
        self.assertEqual(1, mysql_fetchone(self.seed, "SELECT COUNT(*) FROM investment_decision_follow_ups")[0])

    def assert_subject_decision_notification_admission_is_reflected_in_episode(self):
        self.setUp()
        job, request = self.create_detached_request("subject:detached:admission")
        handoff = AIInsightHandoff.create(job.context, job.to_dict())
        job.context["investmentAIInsightHandoff"] = handoff.to_dict()
        request = AIInferenceRequest.create_for_subject_decision(
            job,
            job.context,
            handoff,
            model="gpt-5.6-sol",
            reasoning_effort="max",
        )
        self.queue.enqueue_subject_decision(job, request)
        claimed = self.queue.claim("worker-admission", 1, 60)[0]
        result = AIInferenceResult.create(
            claimed,
            {"action": "HOLD", "summary": "판단은 유효하지만 중복 발송은 하지 않습니다."},
            source="fake max AI",
            validation_state="ready",
            latency_ms=10,
            prompt_bytes=100,
        )
        completed_context = {
            **claimed.context,
            "decisionReconciliation": {
                "version": "investment-decision-reconciliation-v1",
                "status": "reconciled",
                "subjectCaseId": request.origin_id,
                "candidateFingerprint": "a" * 64,
                "inferenceGenerationId": "generation:detached",
                "notificationDecision": "send",
                "notificationJobId": job.job_id,
                "reason": "새 최종 판단을 전달합니다.",
            },
        }

        def reject_delivery(delivery_job, _decision, _settings=None):
            delivery_job.status = "suppressed"
            delivery_job.last_error = "final admission rejected"
            delivery_job.context["deliverySuppressionReason"] = "duplicate_notification_key"
            return NotificationAdmissionOutcome(
                accepted=False,
                persisted=True,
                status="suppressed",
                reason=delivery_job.last_error,
            )

        with patch.object(
            self.queue.notification_store.admission_policy,
            "apply_result",
            side_effect=reject_delivery,
        ):
            self.assertTrue(
                self.complete_detached(
                    claimed,
                    "worker-admission",
                    result,
                    completed_context,
                )
            )
        self.assertIsNone(self.notifications.get(job.job_id))
        self.assertEqual(
            0,
            int(mysql_fetchone(self.seed, "SELECT COUNT(*) FROM notification_jobs")[0]),
        )
        payload = json.loads(mysql_fetchone(
            self.seed,
            "SELECT payload_json FROM investment_ai_insight_episodes WHERE request_id = %s",
            (request.request_id,),
        )[0])
        reconciliation = payload["reconciliation"]
        self.assertEqual("send", reconciliation["semanticNotificationDecision"])
        self.assertEqual("suppress", reconciliation["notificationDecision"])
        self.assertEqual("", reconciliation["notificationJobId"])
        self.assertFalse(reconciliation["deliveryOutcome"]["queued"])
        self.assertEqual("final admission rejected", reconciliation["deliveryOutcome"]["reason"])
        self.assertEqual("duplicate_notification_key", reconciliation["reasonCode"])
        self.assertEqual("duplicate_notification_key", reconciliation["deliveryOutcome"]["reasonCode"])

    def assert_subject_decision_ai_failure_never_creates_notification(self):
        self.setUp()
        job, request = self.create_detached_request("subject:detached:failed")
        self.queue.enqueue_subject_decision(job, request)
        claimed = self.queue.claim("worker-failed", 1, 60)[0]

        self.assertTrue(self.queue.fail(claimed, "worker-failed", "model unavailable"))
        self.assertIsNone(self.notifications.get(job.job_id))
        self.assertEqual("failed", self.queue.get(request.request_id).status)
        row = mysql_fetchone(
            self.seed,
            "SELECT COUNT(*) FROM domain_events WHERE name = %s",
            ("investment.ai_insight_failed",),
        )
        self.assertEqual(1, int(row[0]))

    def assert_subject_decision_queue_coalesces_same_material_meaning(self):
        self.setUp()
        first_job, first = self.create_detached_request("subject:detached:first")
        second_job, second = self.create_detached_request("subject:detached:second")
        self.queue.enqueue_subject_decision(first_job, first)

        outcome = self.queue.enqueue_subject_decision(second_job, second)

        self.assertEqual("coalesced-material", outcome["status"])
        self.assertEqual("", outcome["notificationJobId"])
        self.assertEqual(second_job.job_id, outcome["reservedNotificationJobId"])
        row = mysql_fetchone(self.seed, "SELECT COUNT(*) FROM ai_inference_requests")
        self.assertEqual(1, int(row[0]))
        stale = self.queue.claim("worker-stale-material", 1, 60)[0]

        changed_job, changed = self.create_detached_request("subject:detached:changed")
        changed_job.context["ontologyRelationContext"]["actionEnvelope"] = {
            "executionAction": "TRIM",
            "allowedActions": ["HOLD", "TRIM"],
        }
        changed_handoff = AIInsightHandoff.create(changed_job.context, changed_job.to_dict())
        changed_job.context["investmentAIInsightHandoff"] = changed_handoff.to_dict()
        changed = AIInferenceRequest.create_for_subject_decision(
            changed_job,
            changed_job.context,
            changed_handoff,
            model="gpt-5.6-sol",
            reasoning_effort="max",
        )

        changed_outcome = self.queue.enqueue_subject_decision(changed_job, changed)

        self.assertEqual("coalesced-active", changed_outcome["status"])
        self.assertTrue(changed_outcome["refreshRequired"])
        self.assertEqual(first.request_id, changed_outcome["requestId"])
        self.assertEqual("processing", self.queue.get(first.request_id).status)
        self.assertTrue(
            self.queue.is_current(first.request_id, "worker-stale-material")
        )
        self.assertIsNone(self.notifications.get(first_job.job_id))
        self.assertEqual([], self.queue.claim("worker-material-change", 1, 60))
        active_result = AIInferenceResult.create(
            stale,
            {"action": "HOLD", "summary": "진행 중인 판단을 끝까지 완료했습니다."},
            source="fake max AI",
            validation_state="ready",
            latency_ms=10,
            prompt_bytes=100,
        )
        self.assertTrue(
            self.queue.complete(
                stale,
                "worker-stale-material",
                active_result,
                stale.context,
            )
        )
        self.assertEqual("completed", self.queue.get(first.request_id).status)

    def assert_subject_cost_control_defers_repetitive_work_but_not_action_changes(self):
        self.setUp()
        self.queue.runtime_settings.update({
            "notificationAiCostControlEnabled": "1",
            "notificationAiSubjectCooldownMinutes": "180",
            "notificationAiSubjectDailyLimit": "6",
            "notificationAiDailyLimit": "40",
            "notificationAiMaxEffortDailyLimit": "8",
        })
        first_job, first = self.create_detached_request("subject:cost:first")
        self.assertEqual(
            "awaiting-ai-insight",
            self.queue.enqueue_subject_decision(first_job, first)["status"],
        )

        repeated_job, repeated = self.create_detached_request("subject:cost:repeated")
        repeated.material_fingerprint = "b" * 64
        repeated_outcome = self.queue.enqueue_subject_decision(repeated_job, repeated)

        self.assertEqual("coalesced-cost-control", repeated_outcome["status"])
        self.assertEqual("subject-cooldown", repeated_outcome["reasonCode"])
        self.assertEqual(1, int(mysql_fetchone(
            self.seed, "SELECT COUNT(*) FROM ai_inference_requests"
        )[0]))

        changed_job, _ = self.create_detached_request("subject:cost:action-changed")
        changed_job.context["decisionTransition"] = {
            "kind": "action-changed",
            "material": True,
            "currentAction": "ADD",
        }
        changed_handoff = AIInsightHandoff.create(changed_job.context, changed_job.to_dict())
        changed_job.context["investmentAIInsightHandoff"] = changed_handoff.to_dict()
        changed = AIInferenceRequest.create_for_subject_decision(
            changed_job,
            changed_job.context,
            changed_handoff,
            model="gpt-5.6-sol",
            reasoning_effort="high",
        )

        changed_outcome = self.queue.enqueue_subject_decision(changed_job, changed)

        self.assertEqual("awaiting-ai-insight", changed_outcome["status"])
        self.assertEqual(2, int(mysql_fetchone(
            self.seed, "SELECT COUNT(*) FROM ai_inference_requests"
        )[0]))

    def test_verified_ai_narrative_survives_action_contract_fallback(self):
        reviewed = NotificationAIValidatedResponse(
            action="BUY",
            narrative_claims=[{
                "claimId": "claim:view",
                "section": "view",
                "text": "가격 회복은 확인됐지만 거래 확인은 아직 약합니다.",
                "evidenceIds": ["fact:price"],
            }],
            claim_validation={
                "status": "partial",
                "verifiedClaimCount": 1,
                "validations": [{"claimId": "claim:view", "status": "verified"}],
            },
        )

        fallback = preserve_verified_ai_narrative(
            typedb_inference_fallback_response({}, "action envelope violation"),
            reviewed,
        )

        self.assertEqual("HOLD", fallback.action)
        self.assertEqual("typedb", fallback.writer_provenance["decisionOwner"])
        self.assertTrue(fallback.writer_provenance["aiNarrativePartiallyAdopted"])
        self.assertEqual(1, fallback.verified_claim_count)

    def test_empty_routed_hypothesis_set_is_a_valid_abstention_contract(self):
        context = {
            "_notificationAiPreparedDecisionCore": {
                "hypothesisSet": {
                    "hypotheses": [],
                    "comparisonRequired": False,
                    "minimumComparisonCount": 0,
                },
                "decision": {
                    "actionEnvelope": {
                        "allowedActions": ["HOLD", "AVOID"],
                        "blockedActions": ["BUY", "ADD", "TRIM", "SELL"],
                    }
                },
            }
        }
        response = NotificationAIValidatedResponse(
            action="HOLD",
            selected_hypothesis_id="",
            hypotheses=[],
        )

        self.assertEqual("", ai_response_contract_error(context, response))

    def test_empty_routed_hypothesis_set_rejects_invented_hypothesis(self):
        context = {
            "_notificationAiPreparedDecisionCore": {
                "hypothesisSet": {
                    "hypotheses": [],
                    "comparisonRequired": False,
                    "minimumComparisonCount": 0,
                },
                "decision": {"actionEnvelope": {"allowedActions": ["HOLD"]}},
            }
        }
        response = NotificationAIValidatedResponse(
            action="HOLD",
            selected_hypothesis_id="hypothesis:invented",
        )

        self.assertIn("empty routed TypeDB hypothesis set", ai_response_contract_error(context, response))

    def test_superseded_lease_stops_the_active_ai_process(self):
        class Queue:
            def heartbeat(self, *_args):
                return False

        class Reviewer:
            stopped = False

            def stop(self):
                self.stopped = True

        class ImmediateEvent:
            def wait(self, _seconds):
                return False

        class LeaseState:
            lost = False

            def set(self):
                self.lost = True

        reviewer = Reviewer()
        lease_state = LeaseState()
        runner = AIInferenceQueueRunner(Queue(), reviewer, worker_id="worker-cancel")

        runner.heartbeat_loop("request:superseded", ImmediateEvent(), lease_state)

        self.assertTrue(lease_state.lost)
        self.assertTrue(reviewer.stopped)

        shutdown_order = []

        class StopQueue:
            def recover_worker_label_leases(self, *_args):
                shutdown_order.append("startup-recovery")
                return {"status": "unchanged", "recoveredCount": 0}

            def release_worker_leases(self, *_args):
                shutdown_order.append("lease-released")
                return {"status": "released", "releasedCount": 1}

        class StopReviewer:
            def stop(self):
                shutdown_order.append("model-stopped")

        stopping_runner = AIInferenceQueueRunner(
            StopQueue(),
            StopReviewer(),
            worker_id="worker-order",
        )
        stopping_runner.stop()
        self.assertEqual(
            ["startup-recovery", "lease-released", "model-stopped"],
            shutdown_order,
        )
        self.assertEqual("unchanged", stopping_runner.start_recovery["status"])
        self.assertEqual(1, stopping_runner.stop_recovery["releasedCount"])

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.seed = test_store_seed(cls.temp.name)
        reset_mysql_test_database(cls.seed)
        TestNotificationJobStore(cls.seed)
        TestAIInferenceQueueStore(cls.seed)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        for table in (
            "investment_decision_follow_ups",
            "investment_ai_insight_episodes",
            "domain_events",
            "ai_inference_execution_audits",
            "ai_inference_results",
            "ai_inference_requests",
            "notification_jobs",
            "app_store",
        ):
            mysql_execute(self.seed, "DELETE FROM " + table)
        self.notifications = TestNotificationJobStore(self.seed)
        self.queue = TestAIInferenceQueueStore(self.seed)

    def create_job(self, price=100, generation="generation-1"):
        job = NotificationJob.create(
            "AI queue test",
            account_id="main",
            account_label="메인",
            message_type="investmentInsight",
            context={
                "messageType": "investmentInsight",
                "rawSymbol": "005930",
                "displayTarget": "삼성전자 / 005930",
                "referenceDate": "2026-08-04 10:00 KST",
                "ontologyRelationContext": {
                    "subject": {"symbol": "005930", "name": "삼성전자"},
                    "inferenceGenerationId": generation,
                    "reviewLevel": "act",
                    "changeState": "new-condition",
                    "facts": {"currentPrice": price},
                },
            },
        )
        self.notifications.upsert_job(job)
        return job

    def test_same_semantic_request_joins_running_subject_without_cancelling_ai(self):
        first_job = self.create_job(100, "generation-1")
        first = AIInferenceRequest.create(first_job, first_job.context)
        self.queue.enqueue(first_job, first)
        runner = AIInferenceQueueRunner(self.queue, FakeReviewer(), worker_id="worker-1")
        self.assertEqual(first.request_id, self.queue.claim(runner.worker_id, 1, 60)[0].request_id)

        second_job = self.create_job(101, "generation-2")
        second = AIInferenceRequest.create(second_job, second_job.context)
        outcome = self.queue.enqueue(second_job, second)

        self.assertEqual("coalesced-active", outcome["status"])
        self.assertTrue(self.queue.is_current(first.request_id, runner.worker_id))
        claimed = self.queue.claim(runner.worker_id, 1, 60)
        self.assertEqual([], claimed)
        self.assertEqual([], self.queue.claim("worker-2", 1, 60))
        self.assertEqual("awaiting_ai", self.notifications.get(first_job.job_id).status)
        self.assertEqual("superseded", self.notifications.get(second_job.job_id).status)
        runner.stop()
        self.assertEqual("retry", self.queue.get(first.request_id).status)
        self.assertEqual(1, runner.stop_recovery["releasedCount"])

        stale_owner = "worker-1:stale-instance"
        reclaimed = self.queue.claim(stale_owner, 1, 60)[0]
        self.assertEqual(first.request_id, reclaimed.request_id)
        self.assertLess(reclaimed.started_at, reclaimed.available_at)
        self.assertEqual(reclaimed.heartbeat_at, reclaimed.updated_at)
        expected_wait = int((datetime.fromisoformat(reclaimed.updated_at.replace("Z", "+00:00"))
                             - datetime.fromisoformat(reclaimed.available_at.replace("Z", "+00:00"))).total_seconds() * 1000)
        self.assertEqual(expected_wait, ai_attempt_queue_wait_ms(reclaimed))
        replacement = AIInferenceQueueRunner(
            self.queue,
            FakeReviewer(),
            worker_id="worker-1",
        )
        self.assertEqual("recovered", replacement.start_recovery["status"])
        self.assertEqual(1, replacement.start_recovery["recoveredCount"])
        self.assertEqual(1, replacement.start_recovery["retryCount"])
        self.assertEqual("retry", self.queue.get(first.request_id).status)
        replacement.stop()

    def test_material_action_change_replaces_running_subject(self):
        first_job = self.create_job(100, "generation-1")
        first_job.context["ontologyRelationContext"]["actionEnvelope"] = {
            "executionAction": "HOLD",
            "allowedActions": ["HOLD", "TRIM"],
        }
        self.notifications.upsert_job(first_job)
        first = AIInferenceRequest.create(first_job, first_job.context)
        self.queue.enqueue(first_job, first)
        self.assertEqual(first.request_id, self.queue.claim("worker-1", 1, 60)[0].request_id)

        second_job = self.create_job(99, "generation-2")
        second_job.context["ontologyRelationContext"]["actionEnvelope"] = {
            "executionAction": "TRIM",
            "allowedActions": ["HOLD", "TRIM"],
        }
        second_job.context["decisionTransition"] = {
            "material": True,
            "kind": "action-changed",
            "currentAction": "TRIM",
        }
        self.notifications.upsert_job(second_job)
        second = AIInferenceRequest.create(second_job, second_job.context)
        outcome = self.queue.enqueue(second_job, second)

        self.assertEqual("awaiting-ai", outcome["status"])
        self.assertFalse(self.queue.is_current(first.request_id, "worker-1"))
        self.assertEqual(
            [second.request_id],
            [item.request_id for item in self.queue.claim("worker-1", 1, 60)],
        )

    def test_identical_context_is_coalesced_without_second_ai_request(self):
        first_job = self.create_job()
        first = AIInferenceRequest.create(first_job, first_job.context)
        self.queue.enqueue(first_job, first)

        second_job = self.create_job()
        second = AIInferenceRequest.create(second_job, second_job.context)
        outcome = self.queue.enqueue(second_job, second)

        self.assertEqual("coalesced-identical", outcome["status"])
        self.assertEqual("superseded", self.notifications.get(second_job.job_id).status)
        row = mysql_fetchone(self.seed, "SELECT COUNT(*) FROM ai_inference_requests")
        self.assertEqual(1, int(row[0]))

    def test_superseding_request_reports_the_replaced_reasoning_case(self):
        first_job = self.create_job(100, "generation-case-1")
        first_job.context["investmentReasoningCaseId"] = "reasoning-case:first"
        self.notifications.upsert_job(first_job)
        first = AIInferenceRequest.create(first_job, first_job.context)
        self.queue.enqueue(first_job, first)

        second_job = self.create_job(101, "generation-case-2")
        second_job.context["investmentReasoningCaseId"] = "reasoning-case:second"
        self.notifications.upsert_job(second_job)
        second = AIInferenceRequest.create(second_job, second_job.context)
        outcome = self.queue.enqueue(second_job, second)

        self.assertEqual(
            ["reasoning-case:first"],
            outcome["supersededReasoningCaseIds"],
        )

    def test_validated_result_releases_only_latest_notification_to_delivery(self):
        job = self.create_job()
        request = AIInferenceRequest.create(job, job.context, reasoning_effort="max")
        self.queue.enqueue(job, request)
        runner = AIInferenceQueueRunner(
            self.queue,
            FakeReviewer(),
            {
                "notificationAiQueueLeaseSeconds": "60",
                "notificationAiQueueHeartbeatSeconds": "2",
                "notificationAiQueueMaxAttempts": "2",
                "notificationAiQueueMaxPromptBytes": "49152",
            },
            worker_id="worker-1",
        )

        self.assertEqual(1, runner.run_once(limit=1))

        delivered = self.notifications.get(job.job_id)
        self.assertEqual("pending", delivered.status)
        self.assertEqual("HOLD", delivered.context["notificationAiValidatedResponse"]["action"])
        self.assertEqual("completed", delivered.context["notificationAiQueue"]["status"])
        prompt_audit = delivered.context["notificationAiExecutionAudit"]
        self.assertEqual(request.request_id, prompt_audit["requestId"])
        self.assertEqual("gpt-5.6-sol", prompt_audit["model"])
        self.assertTrue(prompt_audit["prompt"].startswith("너는 자동 주문자가 아니라 TypeDB 경쟁 가설을 비교하는"))
        self.assertEqual("investment-ai-decision-brief-v6", prompt_audit["decisionBriefVersion"])
        self.assertEqual("investment-ai-decision-core-v5", prompt_audit["decisionCore"]["schemaVersion"])
        self.assertEqual("notification-ai-context-route-v10-canonical-ledger", prompt_audit["contextRouting"]["version"])
        self.assertEqual(
            "investment-ai-judge-v30-observed-evidence",
            prompt_audit["promptRelease"]["version"],
        )
        self.assertIn(
            "시스템이 자동으로 확인한다고 표현할 조건은 반드시 followUpConditions",
            prompt_audit["prompt"],
        )
        self.assertIn(
            "구현 용어는 사용자 표시 필드에 노출하지 않고",
            prompt_audit["prompt"],
        )
        self.assertLessEqual(
            prompt_audit["inferencePacket"]["promptBudget"]["renderedPromptBytes"],
            prompt_audit["inferencePacket"]["promptBudget"]["maxPromptBytes"],
        )
        self.assertEqual(49152, prompt_audit["promptTarget"]["targetBytes"])
        self.assertEqual(49152, prompt_audit["promptTarget"]["hardLimitBytes"])
        self.assertFalse(prompt_audit["promptTarget"]["packetExpandedBeyondTarget"])
        self.assertFalse(prompt_audit["promptTarget"]["executedPromptOverTarget"])
        self.assertEqual("wait-until-complete", prompt_audit["executionSpans"]["completionPolicy"])
        self.assertIn("queueWaitMs", prompt_audit["executionSpans"])
        self.assertIn("promptPreparationMs", prompt_audit["executionSpans"])
        trace = self.queue.trace_for_notification(job.job_id)
        self.assertTrue(trace["executionAuditRetained"])
        self.assertEqual(prompt_audit["promptHash"], trace["executionAudit"]["promptHash"])
        self.assertEqual("gpt-5.6-sol", trace["executionAudit"]["model"])
        self.assertTrue(prompt_audit["contextRouting"]["fullDecisionBriefRetainedForAudit"])
        self.assertEqual("deepResearch", prompt_audit["executionProfile"]["name"])
        self.assertEqual(64, len(prompt_audit["promptHash"]))
        result_count = mysql_fetchone(self.seed, "SELECT COUNT(*) FROM ai_inference_results")
        self.assertEqual(1, int(result_count[0]))
        effective = mysql_fetchone(
            self.seed,
            "SELECT publication_mode, ai_authored, publication_contract_passed FROM ai_inference_results LIMIT 1",
        )
        self.assertEqual("ai-authored", effective[0])
        self.assertEqual(1, int(effective[1]))
        self.assertEqual(1, int(effective[2]))

    def test_completion_after_source_supersession_is_not_recorded_as_failure(self):
        job = self.create_job()
        request = AIInferenceRequest.create(job, job.context)
        self.queue.enqueue(job, request)
        claimed = self.queue.claim("worker-race", 1, 60)[0]
        mysql_execute(
            self.seed,
            "UPDATE notification_jobs SET status = 'superseded' WHERE job_id = %s",
            (job.job_id,),
        )
        result = AIInferenceResult.create(
            claimed,
            {"action": "HOLD"},
            source="test",
            validation_state="ready",
            latency_ms=1,
            prompt_bytes=16,
        )

        self.assertFalse(
            self.queue.complete(claimed, "worker-race", result, claimed.context)
        )
        status = mysql_fetchone(
            self.seed,
            "SELECT status, last_error FROM ai_inference_requests WHERE request_id = %s",
            (claimed.request_id,),
        )
        self.assertEqual("superseded", status[0])
        self.assertEqual("", status[1])
        result_count = mysql_fetchone(
            self.seed,
            "SELECT COUNT(*) FROM ai_inference_results WHERE request_id = %s",
            (claimed.request_id,),
        )
        self.assertEqual(0, int(result_count[0]))

    def test_claim_reclassifies_historical_publish_race_after_source_retention(self):
        job = self.create_job()
        request = AIInferenceRequest.create(job, job.context)
        self.queue.enqueue(job, request)
        mysql_execute(
            self.seed,
            """
            UPDATE ai_inference_requests
            SET status = 'failed', last_error = %s
            WHERE request_id = %s
            """,
            (
                "source notification is no longer awaiting AI: " + job.job_id,
                request.request_id,
            ),
        )
        mysql_execute(
            self.seed,
            "DELETE FROM notification_jobs WHERE job_id = %s",
            (job.job_id,),
        )

        self.assertEqual([], self.queue.claim("worker-history", 1, 60))
        status = mysql_fetchone(
            self.seed,
            "SELECT status, last_error FROM ai_inference_requests WHERE request_id = %s",
            (request.request_id,),
        )
        self.assertEqual("superseded", status[0])
        self.assertEqual("", status[1])

    def test_terminal_subject_is_suppressed_before_ai_queueing(self):
        class Queue:
            suppressed = []

            def suppress_source_notification(self, job_id, reason):
                self.suppressed.append((job_id, reason))
                return True

            def enqueue(self, *_args):
                raise AssertionError("terminal subject must not enter the AI queue")

        class Orchestrator:
            def capture_ai_context(self, _case_id, context):
                return {
                    **dict(context),
                    "investmentSubjectDecisionCaseId": "subject:terminal",
                    "investmentSubjectDecisionCase": {
                        "subjectCaseId": "subject:terminal",
                        "stage": "ABSTAINED",
                    },
                }

        queue = Queue()
        job = self.create_job()
        job.context["investmentSubjectDecisionCaseId"] = "subject:terminal"
        outcome = NotificationAIRequestEnqueuer(
            queue,
            reasoning_orchestrator=Orchestrator(),
        ).enqueue(job)

        self.assertEqual("suppressed-terminal-subject", outcome["status"])
        self.assertEqual(job.job_id, queue.suppressed[0][0])
        self.assert_incomplete_v2_action_contract_publishes_typedb_without_ai_request()

    def test_unchanged_graph_without_follow_up_transition_stops_before_ai_queueing(self):
        class Queue:
            suppressed = []

            def suppress_source_notification(self, job_id, reason):
                self.suppressed.append((job_id, reason))
                return True

            def enqueue(self, *_args):
                raise AssertionError("unchanged graph without decision value must not enter AI")

        class Orchestrator:
            suppression_context = {}

            def capture_ai_context(self, _case_id, context):
                return {
                    **dict(context),
                    "investmentSubjectDecisionCaseId": "subject:unchanged",
                    "investmentSubjectDecisionCase": {
                        "subjectCaseId": "subject:unchanged",
                        "stage": "AI_CONTEXT_CAPTURED",
                    },
                }

            def notification_suppressed(self, context, _reason):
                self.suppression_context = dict(context)

        queue = Queue()
        orchestrator = Orchestrator()
        job = self.create_job()
        job.context.update({
            "investmentSubjectDecisionCaseId": "subject:unchanged",
            "preDecisionDeliveryGate": {
                "reasonCode": "unchanged_graph_inference",
            },
            "decisionContinuityPacket": {
                "contractVersion": "decision-continuity-packet-v2",
                "accountId": "main",
                "symbol": "005930",
                "followUpConditions": [],
            },
            "decisionTransition": {"kind": "unchanged", "material": False},
        })

        outcome = NotificationAIRequestEnqueuer(
            queue,
            reasoning_orchestrator=orchestrator,
        ).enqueue(job)

        self.assertEqual("suppressed-no-decision-value", outcome["status"])
        self.assertEqual(job.job_id, queue.suppressed[0][0])
        self.assertEqual(job.job_id, orchestrator.suppression_context["notificationJobId"])

    def assert_incomplete_v2_action_contract_publishes_typedb_without_ai_request(self):
        class Queue:
            released = []

            def release_source_notification_without_ai(self, job_id, context, reason):
                self.released.append((job_id, dict(context), reason))
                return True

            def enqueue(self, *_args):
                raise AssertionError("an actionless TypeDB observation must not enter AI")

        class Orchestrator:
            completed = []

            def capture_ai_context(self, _case_id, context):
                return dict(context)

            def complete_without_ai(self, case_id, reason, source=""):
                self.completed.append((case_id, reason, source))

        queue = Queue()
        orchestrator = Orchestrator()
        job = self.create_job()
        job.context.update({
            "investmentReasoningCaseId": "case:no-action",
            "investmentSubjectDecisionCaseId": "subject:no-action",
            "investmentSubjectDecisionCase": {
                "subjectCaseId": "subject:no-action",
                "stage": "READY",
            },
            "v2DecisionSynthesis": {
                "graph_candidate_action": "HOLD",
                "eligible_hypothesis_ids": [],
                "allowed_actions": [],
            },
        })

        outcome = NotificationAIRequestEnqueuer(
            queue,
            reasoning_orchestrator=orchestrator,
        ).enqueue(job)

        self.assertEqual("typedb-direct-no-action-authority", outcome["status"])
        self.assertEqual("no-eligible-hypothesis", outcome["reasonCode"])
        self.assertEqual(job.job_id, queue.released[0][0])
        self.assertIn("notificationAiValidatedResponse", queue.released[0][1])
        self.assertEqual("typedb-direct", orchestrator.completed[0][2])

    def test_ai_timeout_releases_typedb_fallback_without_retry(self):
        job = self.create_job()
        job.context["ontologyRelationContext"]["sourceAboxSnapshotId"] = "abox:timeout-fallback"
        job.context["ontologyRelationContext"]["hypothesisSet"] = {
            "hypothesisSetId": "hypothesis-set:timeout-fallback",
            "comparisonRequired": False,
            "minimumComparisonCount": 0,
            "hypotheses": [{
                "hypothesisId": "hypothesis:timeout-hold",
                "templateLabel": "관계 변화 관찰",
                "claim": "현재 관계의 다음 변화를 관찰합니다.",
                "supportingRuleIds": ["rule:timeout-hold"],
                "candidateAction": "HOLD",
            }],
        }
        self.notifications.upsert_job(job)
        request = AIInferenceRequest.create(job, job.context, reasoning_effort="high")
        self.queue.enqueue(job, request)

        class TimeoutReviewer:
            calls = 0

            def review(self, _context):
                self.calls += 1
                raise TimeoutError("notification AI command exceeded 120 seconds")

        reviewer = TimeoutReviewer()
        decision_store = RecordingDecisionStore()
        runner = AIInferenceQueueRunner(
            self.queue,
            reviewer,
            {
                "notificationAiTypeDbFallbackEnabled": "1",
                "notificationAiFallbackOnFirstFailure": "1",
                "notificationAiQueueMaxAttempts": "2",
                "notificationAiDeliveryDeadlineSeconds": "120",
            },
            decision_episode_store=decision_store,
            worker_id="worker-timeout-fallback",
        )

        self.assertEqual(1, runner.run_once(limit=1))

        delivered = self.notifications.get(job.job_id)
        self.assertEqual(1, reviewer.calls)
        self.assertEqual("pending", delivered.status)
        self.assertEqual("completed", delivered.context["notificationAiQueue"]["status"])
        self.assertEqual("typedb-fallback", delivered.context["notificationAiExecutionAudit"]["status"])
        self.assertEqual("timeout", delivered.context["notificationAiFailure"]["category"])
        self.assertTrue(delivered.context["notificationAiFailure"]["retryable"])
        self.assertEqual(
            "TypeDB inference fallback",
            delivered.context["notificationAiValidatedResponse"]["source"],
        )
        self.assertIn("typedb-fallback", runner.last_run_details[0])
        self.assertEqual(1, len(decision_store.saved))
        self.assertEqual("typedb-inference-fallback", decision_store.saved[0].source)
        self.assertEqual("reference-only", decision_store.saved[0].status)
        self.assertEqual(
            "typedb-only",
            decision_store.saved[0].facts_at_decision["decisionComparisonState"],
        )
        self.assertEqual(
            decision_store.saved[0].episode_id,
            delivered.context["investmentDecisionEpisodeId"],
        )
        effective = mysql_fetchone(
            self.seed,
            "SELECT publication_mode, ai_authored, contract_failure_code FROM ai_inference_results LIMIT 1",
        )
        self.assertEqual("typedb-fallback", effective[0])
        self.assertEqual(0, int(effective[1]))
        self.assertEqual("delivery-deadline", effective[2])

    def test_invalid_ai_contract_releases_typedb_fallback(self):
        job = self.create_job()
        request = AIInferenceRequest.create(
            job,
            job.context,
            reasoning_effort="high",
            prompt_version=AI_DECISION_PROMPT_VERSION,
        )
        self.queue.enqueue(job, request)

        class RejectingOrchestrator:
            fallback_calls = 0

            def validate_ai_result(self, _context, _result):
                return False, "AI selected hypothesis is not present in the TypeDB hypothesis set."

            def ai_fallback_completed(self, *_args):
                self.fallback_calls += 1

        orchestrator = RejectingOrchestrator()
        runner = AIInferenceQueueRunner(
            self.queue,
            FakeReviewer(),
            {"notificationAiTypeDbFallbackEnabled": "1"},
            reasoning_orchestrator=orchestrator,
            worker_id="worker-contract-fallback",
        )

        self.assertEqual(1, runner.run_once(limit=1))
        delivered = self.notifications.get(job.job_id)
        self.assertEqual("pending", delivered.status)
        self.assertEqual(1, orchestrator.fallback_calls)
        self.assertEqual("typedb-fallback", delivered.context["notificationAiExecutionAudit"]["status"])
        self.assertEqual("contract-invalid", delivered.context["notificationAiFailure"]["category"])
        self.assertFalse(delivered.context["notificationAiFailure"]["retryable"])
        self.assertFalse(delivered.context["notificationWriterProvenance"]["aiAuthored"])
        self.assertEqual("typedb", delivered.context["notificationWriterProvenance"]["decisionOwner"])
        self.assertNotIn("가설·행동 계약", delivered.context["notificationAiValidatedResponse"]["summary"])

        two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        mysql_execute(
            self.seed,
            "UPDATE ai_inference_results SET created_at = %s WHERE request_id = %s",
            (two_hours_ago, request.request_id),
        )
        self.queue.runtime_settings["notificationAiHealthMinimumSamples"] = 1
        summary = self.queue.summary()
        self.assertEqual(24, summary["effectiveAiWindowHours"])
        self.assertEqual(1, summary["effectiveAiEligibleCount"])
        self.assertEqual(1, summary["effectiveAiFallbackCount"])
        self.assertEqual("critical", summary["effectiveAiStatus"])
        self.assertEqual(AI_DECISION_PROMPT_VERSION, summary["currentAiPromptVersion"])
        self.assertEqual(1, summary["historicalAiFallbackCount"])
        self.assertEqual(
            {"hypothesis-contract-mismatch": 1},
            summary["currentAiFallbackReasons"],
        )
        self.assertEqual(1, summary["currentAiPerformance"]["sampleCount"])
        self.assertEqual(0, summary["currentAiPerformance"]["overTargetCount"])
        self.assertEqual(49152, summary["currentAiPerformance"]["targetPromptBytes"])

        mysql_execute(
            self.seed,
            "UPDATE ai_inference_requests SET prompt_version = %s WHERE request_id = %s",
            ("investment-ai-judge-v18", request.request_id),
        )
        historical_only = self.queue.summary()
        self.assertEqual(0, historical_only["currentAiEligibleCount"])
        self.assertEqual(1, historical_only["historicalAiFallbackCount"])
        self.assertEqual("warming-up", historical_only["effectiveAiStatus"])

        unchanged = self.create_job(generation="generation-unchanged")
        duplicate = self.create_job(generation="generation-duplicate")
        self.notifications.mark_suppressed(
            unchanged,
            "TypeDB 관계 판단과 사용자 행동 범위가 같아 AI와 알림을 다시 실행하지 않습니다.",
        )
        self.notifications.mark_suppressed(
            duplicate,
            "같은 내용이 60분 안에 이미 발송되어 다시 보내지 않습니다.",
        )
        notification_summary = self.notifications.summary()
        self.assertEqual(2, notification_summary["intentional_suppressed"])
        self.assertEqual(1, notification_summary["suppression_categories"]["unchanged_decision"])
        self.assertEqual(1, notification_summary["suppression_categories"]["duplicate_or_cooldown"])

    def test_terminal_source_notification_removes_failed_ai_request_from_actionable_health(self):
        job = self.create_job()
        request = AIInferenceRequest.create(job, job.context, reasoning_effort="high")
        self.queue.enqueue(job, request)
        claimed = self.queue.claim("worker-failure-health", 1, 60)[0]
        self.assertTrue(self.queue.fail(claimed, "worker-failure-health", "projection failed"))

        failed = self.queue.summary()
        self.assertEqual(1, failed["failedCount"])
        self.assertEqual(1, failed["actionableFailedCount"])

        self.notifications.mark_suppressed(
            self.notifications.get(job.job_id),
            "종료된 투자 판단 건은 새 AI 판단 요청을 만들지 않습니다.",
        )

        terminal = self.queue.summary()
        self.assertEqual(1, terminal["historicalFailedCount"])
        self.assertEqual(0, terminal["actionableFailedCount"])

    def test_prompt_preparation_failure_releases_typedb_fallback(self):
        job = self.create_job()
        job.context["notificationAiExecutionProfile"] = {
            "name": "standard",
            "reasoningEffort": "high",
            "maxPromptBytes": "invalid",
        }
        self.notifications.upsert_job(job)
        request = AIInferenceRequest.create(job, job.context, reasoning_effort="high")
        self.queue.enqueue(job, request)

        class UnexpectedReviewer:
            def review(self, _context):
                raise AssertionError("AI must not run after prompt preparation fails")

        runner = AIInferenceQueueRunner(
            self.queue,
            UnexpectedReviewer(),
            {"notificationAiTypeDbFallbackEnabled": "1"},
            worker_id="worker-preparation-fallback",
        )

        self.assertEqual(1, runner.run_once(limit=1))
        delivered = self.notifications.get(job.job_id)
        audit = delivered.context["notificationAiExecutionAudit"]
        self.assertEqual("pending", delivered.status)
        self.assertEqual("typedb-fallback", audit["status"])
        self.assertEqual("ai-preparation", audit["fallback"]["stage"])
        self.assertEqual("ai-preparation", audit["failure"]["stage"])
        self.assertFalse(audit["aiAttempted"])

    def test_prompt_budget_expands_once_before_fallback(self):
        job = self.create_job()
        job.context["notificationAiExecutionProfile"] = {
            "name": "standard",
            "reasoningEffort": "high",
            "maxPromptBytes": 15 * 1024,
        }
        self.notifications.upsert_job(job)
        request = AIInferenceRequest.create(job, job.context, reasoning_effort="high")
        self.queue.enqueue(job, request)
        attempted_limits = []

        def build_with_first_budget_failure(*args, **kwargs):
            attempted_limits.append(kwargs["max_prompt_bytes"])
            if len(attempted_limits) == 1:
                raise ValueError(
                    "AI decision core cannot preserve TypeDB hypotheses within 8193 bytes "
                    "(minimum contract requires 8635 bytes)"
                )
            return build_notification_ai_inference_packet(*args, **kwargs)

        runner = AIInferenceQueueRunner(
            self.queue,
            FakeReviewer(),
            {
                "notificationAiTypeDbFallbackEnabled": "1",
                "notificationAiQueueMaxPromptBytes": str(16 * 1024),
            },
            worker_id="worker-prompt-expansion",
        )
        with patch(
            "digital_twin.modules.decisions.application.ai_inference_queue_service.build_notification_ai_inference_packet",
            side_effect=build_with_first_budget_failure,
        ):
            self.assertEqual(1, runner.run_once(limit=1))

        delivered = self.notifications.get(job.job_id)
        self.assertEqual([15 * 1024, 16 * 1024], attempted_limits)
        self.assertEqual("pending", delivered.status)
        self.assertEqual("completed", delivered.context["notificationAiQueue"]["status"])
        self.assertEqual(
            16 * 1024,
            delivered.context["notificationAiExecutionAudit"]["executionProfile"]["effectiveMaxPromptBytes"],
        )

    def test_result_publication_retries_storage_timeout_without_repeating_ai_review(self):
        job = self.create_job()
        request = AIInferenceRequest.create(job, job.context, reasoning_effort="max")
        self.queue.enqueue(job, request)
        original_complete = self.queue.complete
        complete_calls = []

        def flaky_complete(*args, **kwargs):
            complete_calls.append(args[0].request_id)
            if len(complete_calls) == 1:
                raise RuntimeError("transient result publication timeout")
            return original_complete(*args, **kwargs)

        self.queue.complete = flaky_complete

        class CountingReviewer(FakeReviewer):
            calls = 0

            def review(self, context):
                self.calls += 1
                return super().review(context)

        reviewer = CountingReviewer()
        runner = AIInferenceQueueRunner(
            self.queue,
            reviewer,
            {
                "notificationAiQueueStorageRetryAttempts": "2",
                "notificationAiQueueStorageRetryBackoffMilliseconds": "1",
            },
            worker_id="worker-publish-retry",
        )

        self.assertEqual(1, runner.run_once(limit=1))
        self.assertEqual(1, reviewer.calls)
        self.assertEqual(2, len(complete_calls))
        self.assertEqual("pending", self.notifications.get(job.job_id).status)
        self.assertIn("completed", runner.last_run_details[0])

    def test_recovery_retries_storage_timeout_and_releases_ai_lease(self):
        job = self.create_job()
        request = AIInferenceRequest.create(job, job.context, reasoning_effort="max")
        self.queue.enqueue(job, request)
        original_retry = self.queue.retry
        retry_calls = []

        def failed_complete(*_args, **_kwargs):
            raise RuntimeError("result publication unavailable")

        def flaky_retry(*args, **kwargs):
            retry_calls.append(args[0].request_id)
            if len(retry_calls) == 1:
                raise RuntimeError("transient recovery timeout")
            return original_retry(*args, **kwargs)

        self.queue.complete = failed_complete
        self.queue.retry = flaky_retry
        runner = AIInferenceQueueRunner(
            self.queue,
            FakeReviewer(),
            {
                "notificationAiQueueStorageRetryAttempts": "2",
                "notificationAiQueueStorageRetryBackoffMilliseconds": "1",
            },
            worker_id="worker-recovery-retry",
        )

        self.assertEqual(1, runner.run_once(limit=1))
        self.assertEqual(2, len(retry_calls))
        self.assertEqual("retry", self.queue.get(request.request_id).status)
        self.assertEqual("awaiting_ai", self.notifications.get(job.job_id).status)
        self.assertIn("retry", runner.last_run_details[0])

    def test_runner_loads_previous_final_decision_and_never_marks_it_initial(self):
        job = self.create_job()
        request = AIInferenceRequest.create(job, job.context, reasoning_effort="max")
        self.queue.enqueue(job, request)

        class DecisionMemoryStore:
            calls = []

            def latest_decision_memory(self, account_id, symbol, exclude_episode_id="", cutoff_at=""):
                self.calls.append((account_id, symbol, exclude_episode_id, cutoff_at))
                return {
                    "episodeId": "decision-episode:previous",
                    "accountId": account_id,
                    "symbol": symbol,
                    "action": "HOLD",
                    "decidedAt": "2026-08-04T00:30:00Z",
                }

        class HistoryAwareReviewer(FakeReviewer):
            received_context = {}

            def review(self, context):
                self.received_context = dict(context)
                response = super().review(context)
                response.change_analysis = "이번 알림은 첫 판단이라 이전 판단과 비교할 수 없습니다."
                return response

        store = DecisionMemoryStore()
        reviewer = HistoryAwareReviewer()
        runner = AIInferenceQueueRunner(
            self.queue,
            reviewer,
            {
                "notificationAiQueueLeaseSeconds": "60",
                "notificationAiQueueHeartbeatSeconds": "2",
            },
            decision_episode_store=store,
            worker_id="worker-history",
        )

        self.assertEqual(1, runner.run_once(limit=1))

        delivered = self.notifications.get(job.job_id)
        self.assertEqual([("main", "005930", "", "2026-08-04T01:00:00Z")], store.calls)
        self.assertEqual("HOLD", reviewer.received_context["previousInvestmentDecisionEpisode"]["action"])
        self.assertEqual("unchanged", delivered.context["aiDecisionTransition"]["kind"])
        self.assertFalse(delivered.context["investmentNotificationTransition"]["changed"])
        self.assertEqual("HOLD", delivered.context["investmentNotificationState"]["action"])
        self.assertNotIn("첫 판단", delivered.context["notificationAiValidatedResponse"]["changeAnalysis"])
        self.assertIn("이전 AI 최종 판단과 같은", delivered.context["notificationAiValidatedResponse"]["changeAnalysis"])
        transition_claim = next(
            item
            for item in delivered.context["notificationNarrativeBrief"]["claims"]
            if item["section"] == "change"
        )
        self.assertEqual(["transition:decision"], transition_claim["evidenceIds"])
        self.assertEqual("deterministic", transition_claim["writerKind"])
        self.assertNotIn("첫 판단", transition_claim["text"])
        self.assertIn('"previousAction":"HOLD"', delivered.context["notificationAiExecutionAudit"]["prompt"])

    def test_terminal_ai_failure_can_be_requeued_without_stranding_notification(self):
        job = self.create_job()
        first = AIInferenceRequest.create(job, job.context)
        self.queue.enqueue(job, first)
        claimed = self.queue.claim("worker-1", 1, 60)[0]
        self.assertTrue(self.queue.fail(claimed, "worker-1", "validation failed"))

        retried_job = self.notifications.claim_pending(limit=1)[0]
        second = AIInferenceRequest.create(retried_job, retried_job.context)
        outcome = self.queue.enqueue(retried_job, second)

        self.assertFalse(outcome["existing"])
        self.assertNotEqual(first.request_id, outcome["requestId"])
        self.assertEqual("awaiting_ai", self.notifications.get(job.job_id).status)
        row = mysql_fetchone(self.seed, "SELECT COUNT(*) FROM ai_inference_requests")
        self.assertEqual(1, int(row[0]))

    def test_notification_worker_defers_ai_job_without_rendering_or_delivery(self):
        job = self.create_job()

        class Queue:
            def claim_pending(self, **_kwargs):
                return [job]

            def mark_failed(self, *_args):
                raise AssertionError("the queue handoff should not fail")

        class Accounts:
            def load_all(self):
                return []

        class Enqueuer:
            called = False

            def enqueue(self, queued_job):
                self.called = queued_job is job
                return {"status": "awaiting-ai"}

        enqueuer = Enqueuer()
        rendered = []
        runner = NotificationQueueRunner(
            queue=Queue(),
            account_repository=Accounts(),
            notifier_factory=lambda _account: None,
            template_renderer=lambda queued_job: rendered.append(queued_job.job_id) or "rendered",
            ai_request_enqueuer=enqueuer,
        )

        self.assertEqual(1, runner.run_once(limit=1))
        self.assertTrue(enqueuer.called)
        self.assertEqual([], rendered)

        typedb_direct_job = self.create_job()
        typedb_direct_job.context.update({
            "inferenceDispatchDecision": {"route": "PUBLISH_TYPEDB"},
            "notificationAiBypass": {"status": "typedb-direct"},
        })
        self.assertFalse(runner.should_defer_ai_inference(typedb_direct_job))

if __name__ == "__main__":
    unittest.main()
