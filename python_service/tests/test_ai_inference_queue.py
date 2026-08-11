import tempfile
import unittest

from digital_twin.application.ai_inference_queue_service import AIInferenceQueueRunner
from digital_twin.application.notification_service import NotificationQueueRunner
from digital_twin.domain.ai_inference_queue import AIInferenceRequest
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notifications import NotificationJob
from mysql_fixtures import (
    TestAIInferenceQueueStore,
    TestNotificationJobStore,
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
        )


class AIInferenceQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.seed = test_store_seed(self.temp.name)
        reset_mysql_test_database(self.seed)
        self.notifications = TestNotificationJobStore(self.seed)
        self.queue = TestAIInferenceQueueStore(self.seed)

    def tearDown(self):
        self.temp.cleanup()

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
        self.assertTrue(prompt_audit["prompt"].startswith("너는 자동 주문자가 아니라 검증된 근거를 비교하는"))
        self.assertEqual("investment-ai-decision-brief-v1", prompt_audit["decisionBriefVersion"])
        self.assertEqual("deepResearch", prompt_audit["executionProfile"]["name"])
        self.assertEqual(64, len(prompt_audit["promptHash"]))
        result_count = mysql_fetchone(self.seed, "SELECT COUNT(*) FROM ai_inference_results")
        self.assertEqual(1, int(result_count[0]))

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
        self.assertNotIn("첫 판단", delivered.context["notificationAiValidatedResponse"]["changeAnalysis"])
        self.assertIn("이전 AI 최종 판단과 같은", delivered.context["notificationAiValidatedResponse"]["changeAnalysis"])
        self.assertIn('"previousFinalDecision"', delivered.context["notificationAiExecutionAudit"]["prompt"])

    def test_heartbeat_requires_matching_owner_and_latest_head(self):
        job = self.create_job()
        request = AIInferenceRequest.create(job, job.context)
        self.queue.enqueue(job, request)
        self.queue.claim("worker-1", 1, 60)

        self.assertFalse(self.queue.heartbeat(request.request_id, "worker-2", 60))
        self.assertTrue(self.queue.heartbeat(request.request_id, "worker-1", 60))

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
