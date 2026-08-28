import tempfile
import unittest
import json
from datetime import datetime, timedelta, timezone

from digital_twin.application.ai_inference_queue_service import (
    AIInferenceQueueRunner,
    ai_response_contract_error,
    preserve_verified_ai_narrative,
    typedb_inference_fallback_response,
)
from digital_twin.application.notification_service import NotificationQueueRunner
from digital_twin.domain.ai_inference_queue import AIInferenceRequest, AIInferenceResult
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notifications import NotificationJob
from mysql_fixtures import (
    TestAIInferenceQueueStore,
    TestNotificationJobStore,
    mysql_execute,
    mysql_fetchone,
    reset_mysql_test_database,
    test_store_seed,
)


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

    def test_latest_request_supersedes_running_subject_and_claim_is_exclusive(self):
        first_job = self.create_job(100, "generation-1")
        first = AIInferenceRequest.create(first_job, first_job.context)
        self.queue.enqueue(first_job, first)
        self.assertEqual(first.request_id, self.queue.claim("worker-1", 1, 60)[0].request_id)

        second_job = self.create_job(101, "generation-2")
        second = AIInferenceRequest.create(second_job, second_job.context)
        self.queue.enqueue(second_job, second)

        self.assertFalse(self.queue.is_current(first.request_id, "worker-1"))
        claimed = self.queue.claim("worker-1", 1, 60)
        self.assertEqual([second.request_id], [item.request_id for item in claimed])
        self.assertEqual([], self.queue.claim("worker-2", 1, 60))
        self.assertEqual("superseded", self.notifications.get(first_job.job_id).status)
        self.assertEqual("awaiting_ai", self.notifications.get(second_job.job_id).status)

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
        self.assertEqual("investment-ai-decision-brief-v4", prompt_audit["decisionBriefVersion"])
        self.assertEqual("investment-ai-decision-core-v1", prompt_audit["decisionCore"]["schemaVersion"])
        self.assertEqual("notification-ai-context-route-v2", prompt_audit["contextRouting"]["version"])
        self.assertEqual("investment-ai-judge-v10", prompt_audit["promptRelease"]["version"])
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
        request = AIInferenceRequest.create(job, job.context, reasoning_effort="high")
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
        self.assertFalse(delivered.context["notificationWriterProvenance"]["aiAuthored"])
        self.assertEqual("typedb", delivered.context["notificationWriterProvenance"]["decisionOwner"])
        self.assertNotIn("가설·행동 계약", delivered.context["notificationAiValidatedResponse"]["summary"])

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
        self.assertFalse(audit["aiAttempted"])

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

            def latest_decision_memory(self, account_id, symbol, exclude_episode_id=""):
                self.calls.append((account_id, symbol, exclude_episode_id))
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
        self.assertEqual([("main", "005930", "")], store.calls)
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

if __name__ == "__main__":
    unittest.main()
