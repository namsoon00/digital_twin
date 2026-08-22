import tempfile
import unittest
import json
from datetime import datetime, timedelta, timezone

from digital_twin.application.ai_inference_queue_service import (
    AIInferenceQueueRunner,
    ai_response_contract_error,
)
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


class AIInferenceQueueTests(unittest.TestCase):
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

    def test_ai_must_explain_action_disagreement_with_selected_typedb_hypothesis(self):
        context = {
            "_notificationAiPreparedDecisionCore": {
                "hypothesisSet": {
                    "hypotheses": [{
                        "hypothesisId": "hypothesis:entry",
                        "candidateAction": "BUY",
                    }],
                    "comparisonRequired": True,
                },
                "decision": {
                    "actionEnvelope": {
                        "allowedActions": ["BUY", "HOLD"],
                    },
                },
            },
        }
        unexplained = NotificationAIValidatedResponse(
            action="HOLD",
            selected_hypothesis_id="hypothesis:entry",
        )
        explained = NotificationAIValidatedResponse(
            action="HOLD",
            selected_hypothesis_id="hypothesis:entry",
            disagreement_reason="거래 확인이 없어 진입 실행을 보류합니다.",
        )

        self.assertIn("without an explicit disagreement reason", ai_response_contract_error(context, unexplained))
        self.assertEqual("", ai_response_contract_error(context, explained))

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

    def test_ai_request_row_stores_compact_identity_and_claim_rehydrates_full_context(self):
        job = self.create_job()
        job.context["largeCanonicalEvidence"] = [
            {"evidenceId": "evidence:" + str(index), "text": "x" * 4000}
            for index in range(80)
        ]
        job.context["investmentReasoningCaseId"] = "reasoning-case:compact"
        job.context["investmentReasoningCase"] = {
            "caseId": "reasoning-case:compact",
            "state": "AI_QUEUED",
            "hypotheses": [{"hypothesisId": "hypothesis:" + str(index)} for index in range(40)],
        }
        self.notifications.upsert_job(job)
        request = AIInferenceRequest.create(job, job.context)

        self.queue.enqueue(job, request)

        stored = mysql_fetchone(
            self.seed,
            "SELECT context_json FROM ai_inference_requests WHERE request_id = %s",
            (request.request_id,),
        )
        stored_context = json.loads(stored[0])
        self.assertLess(len(stored[0].encode("utf-8")), 4096)
        self.assertNotIn("largeCanonicalEvidence", stored_context)
        self.assertEqual("reasoning-case:compact", stored_context["investmentReasoningCaseId"])

        claimed = self.queue.claim("worker-compact", 1, 60)[0]
        self.assertEqual(80, len(claimed.context["largeCanonicalEvidence"]))
        self.assertEqual(
            "reasoning-case:compact",
            claimed.context["investmentReasoningCase"]["caseId"],
        )

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
        self.assertEqual("investment-ai-judge-v9", prompt_audit["promptRelease"]["version"])
        self.assertTrue(prompt_audit["contextRouting"]["fullDecisionBriefRetainedForAudit"])
        self.assertEqual("deepResearch", prompt_audit["executionProfile"]["name"])
        self.assertEqual(64, len(prompt_audit["promptHash"]))
        result_count = mysql_fetchone(self.seed, "SELECT COUNT(*) FROM ai_inference_results")
        self.assertEqual(1, int(result_count[0]))

    def test_ai_timeout_releases_typedb_fallback_without_retry(self):
        job = self.create_job()
        request = AIInferenceRequest.create(job, job.context, reasoning_effort="high")
        self.queue.enqueue(job, request)

        class TimeoutReviewer:
            calls = 0

            def review(self, _context):
                self.calls += 1
                raise TimeoutError("notification AI command exceeded 120 seconds")

        reviewer = TimeoutReviewer()
        runner = AIInferenceQueueRunner(
            self.queue,
            reviewer,
            {
                "notificationAiTypeDbFallbackEnabled": "1",
                "notificationAiFallbackOnFirstFailure": "1",
                "notificationAiQueueMaxAttempts": "2",
                "notificationAiDeliveryDeadlineSeconds": "120",
            },
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

    def test_expired_queue_deadline_skips_ai_and_releases_typedb_fallback(self):
        job = self.create_job()
        request = AIInferenceRequest.create(job, job.context, reasoning_effort="high")
        request.created_at = (
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).isoformat().replace("+00:00", "Z")
        request.updated_at = request.created_at
        request.available_at = request.created_at
        self.queue.enqueue(job, request)

        class UnexpectedReviewer:
            def review(self, _context):
                raise AssertionError("expired work must not start an AI process")

        runner = AIInferenceQueueRunner(
            self.queue,
            UnexpectedReviewer(),
            {
                "notificationAiTypeDbFallbackEnabled": "1",
                "notificationAiDeliveryDeadlineSeconds": "60",
            },
            worker_id="worker-expired-fallback",
        )

        self.assertEqual(1, runner.run_once(limit=1))
        delivered = self.notifications.get(job.job_id)
        self.assertEqual("pending", delivered.status)
        self.assertFalse(delivered.context["notificationAiExecutionAudit"]["aiAttempted"])
        self.assertIn("deadline", delivered.context["notificationAiExecutionAudit"]["fallback"]["reason"])

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

    def test_incomplete_hypothesis_comparison_is_repaired_once_before_publish(self):
        job = self.create_job()
        hypotheses = [
            {
                "hypothesisId": "hypothesis:risk",
                "supportingRuleIds": ["rule:risk"],
                "supportingEvidenceIds": ["evidence:risk"],
            },
            {
                "hypothesisId": "hypothesis:support",
                "supportingRuleIds": ["rule:support"],
                "supportingEvidenceIds": ["evidence:support"],
            },
        ]
        job.context["ontologyRelationContext"]["hypothesisSet"] = {
            "hypothesisSetId": "set:1",
            "hypotheses": hypotheses,
        }
        job.context["ontologyRelationContext"]["activeRules"] = [
            {
                "ruleId": rule_id,
                "evidenceState": {
                    "evidenceUsableForJudgement": True,
                    "inferenceEligibilityStatus": "eligible",
                },
            }
            for rule_id in ["rule:risk", "rule:support"]
        ]
        self.notifications.upsert_job(job)
        request = AIInferenceRequest.create(job, job.context, reasoning_effort="max")
        self.queue.enqueue(job, request)

        class RepairReviewer:
            def __init__(self):
                self.calls = []
                self.profiles = []
                self.timeouts = []

            def review(self, context):
                self.calls.append(str(context.get("_notificationAiPreparedPrompt") or ""))
                self.profiles.append(dict(context.get("notificationAiExecutionProfile") or {}))
                self.timeouts.append(context.get("_notificationAiTimeoutSecondsOverride"))
                if len(self.calls) == 1:
                    return NotificationAIValidatedResponse(
                        action="HOLD",
                        hypotheses=[hypotheses[0]],
                        hypothesis_comparison_state="partial",
                        hypothesis_selection_source="abstained-partial",
                        decision_abstention={
                            "abstained": True,
                            "reason": "AI가 현재 TypeDB 규칙 가설을 모두 평가하지 못했습니다.",
                            "unreviewedHypothesisIds": ["hypothesis:support"],
                        },
                    )
                return NotificationAIValidatedResponse(
                    action="HOLD",
                    hypotheses=hypotheses,
                    selected_hypothesis_id="hypothesis:risk",
                    hypothesis_comparison_state="completed",
                    hypothesis_selection_source="ai-comparison",
                )

        reviewer = RepairReviewer()
        runner = AIInferenceQueueRunner(
            self.queue,
            reviewer,
            {
                "notificationAiComparisonRepairReasoningEffort": "low",
                "notificationAiComparisonRepairTimeoutSeconds": "45",
            },
            worker_id="worker-repair",
        )

        self.assertEqual(1, runner.run_once(limit=1))

        delivered = self.notifications.get(job.job_id)
        repair = delivered.context["notificationAiExecutionAudit"]["hypothesisComparisonRepair"]
        self.assertEqual(2, len(reviewer.calls))
        self.assertTrue(repair["attempted"])
        self.assertTrue(repair["succeeded"])
        self.assertEqual("low", repair["reasoningEffort"])
        self.assertEqual(45, repair["timeoutSeconds"])
        self.assertEqual("low", reviewer.profiles[1]["reasoningEffort"])
        self.assertEqual(45, reviewer.timeouts[1])
        self.assertIn("unreviewedHypothesisIds", reviewer.calls[1])
        self.assertEqual("hypothesis:risk", delivered.context["notificationAiValidatedResponse"]["selectedHypothesisId"])

    def test_completed_comparison_with_invalid_action_envelope_is_repaired(self):
        job = self.create_job()
        hypotheses = [
            {"hypothesisId": "hypothesis:risk", "supportingRuleIds": ["rule:risk"]},
            {"hypothesisId": "hypothesis:support", "supportingRuleIds": ["rule:support"]},
        ]
        job.context["ontologyRelationContext"]["hypothesisSet"] = {
            "hypotheses": hypotheses,
        }
        job.context["ontologyRelationContext"]["activeRules"] = [
            {
                "ruleId": rule_id,
                "evidenceState": {
                    "evidenceUsableForJudgement": True,
                    "inferenceEligibilityStatus": "eligible",
                },
            }
            for rule_id in ["rule:risk", "rule:support"]
        ]
        job.context["ontologyRelationContext"]["actionEnvelope"] = {
            "allowedActions": ["HOLD"],
            "blockedActions": ["BUY"],
            "drivingRuleIds": ["rule:risk", "rule:support"],
        }
        job.context["investmentReasoningCase"] = {
            "caseId": "case:envelope",
            "hypothesisIds": ["hypothesis:risk", "hypothesis:support"],
            "decisionSyntheses": [{
                "eligibleHypothesisIds": ["hypothesis:risk", "hypothesis:support"],
                "allowedActions": ["HOLD"],
                "blockedActions": ["BUY"],
            }],
        }
        self.notifications.upsert_job(job)
        request = AIInferenceRequest.create(job, job.context, reasoning_effort="max")
        self.queue.enqueue(job, request)

        class EnvelopeReviewer:
            def __init__(self):
                self.calls = 0

            def review(self, context):
                self.calls += 1
                if self.calls == 1:
                    return NotificationAIValidatedResponse(
                        action="BUY",
                        hypotheses=hypotheses,
                        selected_hypothesis_id="hypothesis:risk",
                        hypothesis_comparison_state="completed",
                    )
                self.assert_contract_prompt = "decisionContractError" in str(
                    context.get("_notificationAiPreparedPrompt") or ""
                )
                return NotificationAIValidatedResponse(
                    action="HOLD",
                    hypotheses=hypotheses,
                    selected_hypothesis_id="hypothesis:risk",
                    hypothesis_comparison_state="completed",
                )

        reviewer = EnvelopeReviewer()
        runner = AIInferenceQueueRunner(
            self.queue,
            reviewer,
            {"notificationAiComparisonRepairReasoningEffort": "max"},
            worker_id="worker-envelope-repair",
        )

        self.assertEqual(1, runner.run_once(limit=1))
        delivered = self.notifications.get(job.job_id)
        repair = delivered.context["notificationAiExecutionAudit"]["hypothesisComparisonRepair"]
        self.assertEqual(2, reviewer.calls)
        self.assertTrue(reviewer.assert_contract_prompt)
        self.assertTrue(repair["attempted"])
        self.assertTrue(repair["succeeded"])
        self.assertIn("blocked", repair["initialContractError"])
        self.assertEqual("HOLD", delivered.context["notificationAiValidatedResponse"]["action"])

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

    def test_notification_worker_suppresses_watchlist_candidate_churn_after_ai(self):
        job = NotificationJob.create(
            "candidate churn",
            account_id="main",
            message_type="investmentInsight",
            context={
                "notificationAiValidatedResponse": {"action": "HOLD"},
                "aiDecisionTransition": {
                    "historyAvailable": True,
                    "kind": "unchanged",
                    "previousAction": "HOLD",
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
                    "actionEnvelope": {"targetRole": "watchlist"},
                },
                "ontologyInsight": {"semanticComponents": {"materialSourceEventKeys": []}},
            },
        )

        class Queue:
            def claim_pending(self, **_kwargs):
                return [job]

            def mark_suppressed(self, target, reason):
                target.status = "suppressed"
                target.last_error = reason

            def mark_failed(self, *_args):
                raise AssertionError("candidate-only churn should be suppressed, not failed")

        class Accounts:
            def load_all(self):
                return []

        sent = []
        rendered = []
        runner = NotificationQueueRunner(
            queue=Queue(),
            account_repository=Accounts(),
            notifier_factory=lambda _account: type("Notifier", (), {"send": lambda _self, message: sent.append(message)})(),
            template_renderer=lambda queued_job: rendered.append(queued_job.job_id) or "rendered",
        )

        self.assertEqual(1, runner.run_once(limit=1))
        self.assertEqual("suppressed", job.status)
        self.assertEqual([], rendered)
        self.assertEqual([], sent)
        self.assertEqual("suppress", job.context["finalAiDeliveryGate"]["decision"])
        self.assertEqual("graph_candidate_only_change", job.context["deliverySuppressionReason"])


if __name__ == "__main__":
    unittest.main()
