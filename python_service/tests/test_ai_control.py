import os
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

from digital_twin.modules.ai_orchestration.domain.planning import validate_plan, enabled, identity, stamp
from digital_twin.modules.ai_orchestration.domain.budget import AIControlBudgetWait, budget_state
from digital_twin.modules.ai_orchestration.application.control import AIControlService
from digital_twin.modules.ai_orchestration.infrastructure.execution import ai_execution
from digital_twin.modules.reasoning.domain.observation_evidence import EVIDENCE_PROTOCOL, EVIDENCE_PROFILE, select_evidence


SUBJECT = {"accountId": "control-test", "symbol": "TEST", "name": "Test", "worldId": "portfolio:local:control-test"}
_FACTS, _COVERAGE = select_evidence([{"id": "quote-1", "kind": "stock", "currentPrice": 100,
    "sourceEntityId": "quote", "sourceWorldId": SUBJECT["worldId"], "sourceSnapshotId": "snapshot-1"}])
PACKET = {**SUBJECT, "protocolVersion": EVIDENCE_PROTOCOL, "profile": EVIDENCE_PROFILE,
          "sourceSnapshotId": "snapshot-1", "sourceSnapshots": {SUBJECT["worldId"]: "snapshot-1"},
          "capturedAt": stamp(), "facts": _FACTS, "coverage": _COVERAGE}
PLAN = {"summary": "이전 관찰과 비교할 첫 근거입니다.", "hypothesis": "실적 변화가 가격 흐름과 연관될 수 있습니다.",
        "counterEvidence": "기간별 실적이 없어 확인이 필요합니다.", "comparison": "첫 관찰입니다.",
        "notification": {"send": False, "reason": "첫 관찰이라 이전 흐름을 더 확인합니다."},
        "evidenceIds": ["quote-1"], "questions": [{"question": "공식 발표에서 최근 분기 실적이 개선되었는가?", "capability": "research"}], "nextCheckMinutes": 1}


class AIControlTests(unittest.TestCase):
    def test_execution_metrics_survive_timeout_without_private_error_output(self):
        from digital_twin.modules.ai_orchestration.infrastructure.execution import current_execution_metrics
        store = Mock(); store.begin_call.return_value = 'call-timing'
        with self.assertRaises(TimeoutError):
            with ai_execution('independent-observation', store=store):
                current_execution_metrics().update(stage='model-process', capacityWaitMs=5, modelProcessMs=240000,
                                                   terminationReason='timeout')
                raise TimeoutError('private model output')
        metrics = store.finish_call.call_args.kwargs['metrics']
        self.assertEqual(5, metrics['capacityWaitMs'])
        self.assertEqual(240000, metrics['modelProcessMs'])
        self.assertNotIn('private', repr(store.finish_call.call_args))
        self.assertIsNone(current_execution_metrics())

    def runner(self, **kwargs):
        store = Mock()
        store.claim.return_value = {**SUBJECT, "taskId": "task-1", "capability": "observe", "attempts": 1, "leaseToken": "lease"}
        store.keep_alive.return_value = nullcontext()
        store.memory.return_value = [{"summary": "이전 관찰", "observedAt": "2026-01-01T00:00:00Z"}]
        store.complete.return_value = True
        store.save_execution_input.return_value = "frozen-input"
        planner = Mock(return_value=PLAN)
        service = AIControlService(store, lambda: [SUBJECT], Mock(return_value=dict(PACKET)), planner,
                                   Mock(return_value={"status": "completed"}), Mock(return_value=[]),
                                   {"notificationAiQueueWorkerCount": 1})
        for key, value in kwargs.items():
            setattr(service, key, value)
        return service, store, planner

    def test_rule_independent_plan_keeps_hypothesis_and_bounded_schedule(self):
        result = validate_plan(PLAN, PACKET)
        self.assertEqual("research-only", result["authority"])
        self.assertEqual(60, result["nextCheckMinutes"])
        self.assertEqual(PLAN["hypothesis"], result["hypothesis"])
        self.assertNotIn("candidate", PACKET)
        observation = validate_plan({**PLAN, "questions": [{"question": "다음 관측에서 매도 수급이 지속되는가?", "capability": "observe"}]}, PACKET)
        self.assertEqual([], observation["researchQuestions"])

    def test_unknown_fact_and_self_authorized_action_are_rejected(self):
        for patch in ({"evidenceIds": ["invented"]}, {"action": "BUY"}, {"accountId": "another"}, {"questions": ["x"]}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_plan({**PLAN, **patch}, PACKET)

    def test_observation_persists_research_and_next_check_with_same_subject(self):
        service, store, planner = self.runner()
        self.assertEqual("completed", service.run_once()["status"])
        _, result, children = store.complete.call_args.args
        self.assertEqual("snapshot-1", result["input"]["sourceSnapshotId"])
        self.assertEqual(["research", "observe"], [child["capability"] for child in children])
        self.assertTrue(all(child["accountId"] == SUBJECT["accountId"] for child in children))
        self.assertEqual("이전 관찰", planner.call_args.args[0]["previousAnalyses"][0]["summary"])
        self.assertEqual("frozen-input", result["executionInputId"])

    def test_missing_facts_do_not_call_ai_or_schedule_followups(self):
        service, store, planner = self.runner(evidence=Mock(return_value={}))
        self.assertEqual("deferred", service.run_once()["status"])
        planner.assert_not_called()
        store.complete.assert_not_called()
        store.fail.assert_called_once()

    def test_removed_account_subject_is_retired_before_read_or_ai(self):
        service, store, planner = self.runner(subjects=lambda: [])
        self.assertEqual("retired", service.run_once()["status"])
        service.evidence.assert_not_called()
        planner.assert_not_called()
        self.assertEqual([], store.complete.call_args.args[2])

    def test_pause_and_lost_lease_do_not_claim_success(self):
        service, store, planner = self.runner(settings={"aiControlEnabled": "false", "notificationAiQueueWorkerCount": 0})
        self.assertEqual("paused", service.run_once()["status"])
        store.claim.assert_not_called()
        self.assertTrue(enabled({"notificationAiQueueWorkerCount": 0}))
        service.settings["aiControlEnabled"] = "true"
        store.complete.return_value = False
        self.assertEqual("lease-lost", service.run_once()["status"])
        store.claim.side_effect = AIControlBudgetWait("call", "2026-10-02T00:00:00Z")
        planner.reset_mock(); store.fail.reset_mock()
        self.assertEqual("budget-wait", service.run_once()["status"])
        planner.assert_not_called(); store.fail.assert_not_called()

    def test_execution_audit_records_failure_without_prompt_or_error_contents(self):
        store = Mock()
        store.begin_call.return_value = "call-1"
        with self.assertRaises(ValueError):
            with ai_execution("research", "private input", store=store):
                raise ValueError("private provider details")
        store.finish_call.assert_called_once_with("call-1", "ValueError")
        self.assertNotIn("private", repr(store.begin_call.call_args))

    def test_unchanged_inputs_skip_call_but_schedule_durable_recheck(self):
        from digital_twin.modules.ai_orchestration.domain.planning import observation_fingerprint
        from digital_twin.modules.ai_orchestration.domain.execution_input import PROMPT_VERSION
        service, store, planner = self.runner()
        store.memory.return_value = [{"inputFingerprint": observation_fingerprint(PACKET, []), "observedAt": stamp(),
                                      "executionPromptVersion": PROMPT_VERSION, "quality": {"status": "observation-only"}}]
        self.assertEqual("unchanged", service.run_once()["status"])
        planner.assert_not_called()
        self.assertEqual("observe", store.complete.call_args.args[2][0]["capability"])
        self.assertIn("followUpEvaluations", store.complete.call_args.args[1])
        for patch in ({"quality": {"status": "rejected"}}, {"executionPromptVersion": "older-prompt"}):
            with self.subTest(patch=patch):
                store.memory.return_value[0].update(patch)
                planner.reset_mock()
                self.assertEqual("completed", service.run_once()["status"])
                planner.assert_called_once()

    def test_graph_reader_rejects_scope_mismatch_and_changing_generation(self):
        from digital_twin.modules.reasoning.public import ObservationEvidenceReader
        repository = Mock()
        repository.metadata.return_value = {"status": "ok", "aboxSnapshotId": "snap-1", "accountId": SUBJECT["accountId"]}
        repository.snapshot_id.return_value = "snap-1"
        repository.candidates.return_value = [{"id": "quote", "kind": "stock", "symbol": "OTHER", "currentPrice": 100}]
        with self.assertRaises(ValueError):
            ObservationEvidenceReader(repository)(SUBJECT)
        repository.candidates.return_value = [{"id": "quote", "kind": "stock", "symbol": "TEST", "currentPrice": 100}]
        repository.snapshot_id.side_effect = ["snap-1", "snap-2"]
        with self.assertRaises(ValueError):
            ObservationEvidenceReader(repository)(SUBJECT)
        repository.snapshot_id.side_effect = None
        packet = ObservationEvidenceReader(repository)(SUBJECT)
        self.assertEqual(100, packet["facts"][0]["currentPrice"])
        self.assertEqual("snap-1", packet["facts"][0]["sourceSnapshotId"])
        self.assertFalse(packet["requiresMatchedRule"])

    def test_private_status_and_invalid_settings_have_no_write_or_read(self):
        from digital_twin.infrastructure.web.routes.operations import OperationsRoutes
        from digital_twin.infrastructure.composition.ai_orchestration import save_ai_control_settings
        request = Mock(command="GET")
        request.share_access.return_value.role = "viewer"
        OperationsRoutes().route_operations_health(request, "/api/ai-control/status", {})
        self.assertEqual(403, request.send_payload.call_args.args[0])
        with patch("digital_twin.infrastructure.settings.save_runtime_settings") as save:
            for payload in ({"aiControlDailyTaskBudget": -1}, {"command": "anything"}, {"aiControlEnabled": "maybe"}, {"aiControlBudgetEnabled": "maybe"}, {"aiObservationPromptMaxBytes": 1}, {"aiObservationPromptMaxBytes": 524289}):
                with self.assertRaises(ValueError):
                    save_ai_control_settings(payload)
            save.assert_not_called()
            save_ai_control_settings({"aiControlEnabled": "true", "aiControlBudgetEnabled": "false", "aiControlDailyCallBudget": "0", "aiObservationPromptMaxBytes": "262144"})
            save.assert_called_once_with({"aiControlEnabled": "true", "aiControlBudgetEnabled": "false", "aiControlDailyCallBudget": "0", "aiObservationPromptMaxBytes": "262144"})


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class AIControlStorageTests(unittest.TestCase):
    def setUp(self):
        from digital_twin.infrastructure.settings import runtime_settings
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
        self.store = MySQLAIControlStore({**runtime_settings(), "aiControlDailyTaskBudget": 10, "aiControlDailyCallBudget": 2})
        self.clean()

    def clean(self):
        with self.store.transaction() as c:
            for table in ("ai_control_call_metrics", "ai_control_input_calls", "ai_control_inputs", "ai_control_tasks", "ai_control_budget", "ai_control_calls"):
                c.execute("DELETE FROM " + table)

    def tearDown(self):
        self.clean()

    def test_idempotent_seed_lease_recovery_and_atomic_successors(self):
        self.store.seed(SUBJECT)
        self.store.seed(SUBJECT)
        old = self.store.claim()
        self.assertIsNone(self.store.claim())
        with self.store.transaction() as c:
            c.execute("UPDATE ai_control_tasks SET lease_until='2000' WHERE task_id=%s", (old["taskId"],))
        new = self.store.claim()
        child = {**SUBJECT, "capability": "observe", "taskId": identity("child"), "availableAt": "2099"}
        self.assertFalse(self.store.complete(old, {"summary": "stale"}, [child]))
        self.assertTrue(self.store.complete(new, {"summary": "saved"}, [child]))
        self.assertFalse(self.store.complete(new, {}, [child]))
        self.assertEqual(2, len(self.store.status()["tasks"]))
        self.assertEqual("saved", self.store.memory(SUBJECT["accountId"], "TEST")[0]["summary"])
        self.assertEqual([], self.store.memory("other-account", "TEST"))
        # A skipped model call still records condition state, without replacing
        # the last real analysis or resetting its six-hour reuse window.
        with self.store.transaction() as c:
            c.execute("UPDATE ai_control_tasks SET available_at='2000' WHERE task_id=%s", (child["taskId"],))
        skipped = self.store.claim()
        state = [{"conditionId": "check", "status": "expired", "transitionVerified": False}]
        self.assertTrue(self.store.complete(skipped, {"status": "unchanged", "followUpEvaluations": state}, []))
        remembered = self.store.memory(SUBJECT["accountId"], "TEST")[0]
        self.assertEqual("saved", remembered["summary"])
        self.assertEqual(state, remembered["followUpEvaluations"])
        other = {**SUBJECT, "symbol": "RETURNING"}
        self.store.seed(other)
        retired = self.store.claim()
        self.assertTrue(self.store.complete(retired, {"status": "retired"}, []))
        self.store.seed(other)
        rejoined = self.store.claim()
        self.assertNotEqual(retired["taskId"], rejoined["taskId"])
        self.assertEqual("RETURNING", rejoined["symbol"])

    def test_failed_completion_rolls_back_both_result_and_successors(self):
        self.store.seed(SUBJECT)
        job = self.store.claim()
        with self.assertRaises(KeyError):
            self.store.complete(job, {"summary": "must not persist"}, [{"taskId": "incomplete"}])
        self.assertEqual("processing", self.store.status()["tasks"][0]["status"])
        self.assertEqual([], self.store.memory(SUBJECT["accountId"], "TEST"))

    def test_budgets_survive_new_store_and_failed_calls_consume_admission(self):
        from datetime import datetime, timezone
        reset = budget_state({}, 0, 24, datetime(2026, 10, 1, 23, 59, tzinfo=timezone.utc))["budgetResetAt"]
        self.assertEqual("2026-10-02T00:00:00Z", reset)
        self.store.seed(SUBJECT)
        call = self.store.begin_call("research", "hash", "task")
        self.store.finish_call(call, "TimeoutError", metrics={"modelProcessMs": 240001, "terminationReason": "timeout", "stderr": "private"})
        with self.store.connect() as connection:
            import json
            metrics = json.loads(connection.execute("SELECT metrics_json FROM ai_control_call_metrics WHERE call_id=%s", (call,)).fetchone()["metrics_json"])
        self.assertEqual({"modelProcessMs": 240001, "terminationReason": "timeout"}, metrics)
        # One call remains, but a draft must leave room for its critique. The
        # wait must not claim work, use task budget or exhaust failure retries.
        for _ in range(3):
            with self.assertRaises(AIControlBudgetWait):
                self.store.claim()
        state = self.store.status()
        self.assertEqual(0, state["tasksStartedToday"])
        self.assertEqual(0, state["tasks"][0]["attempts"])
        self.assertEqual(1, state["modelCallsUsedToday"])
        self.assertEqual("budget-wait", state["observationScheduling"]["status"])
        self.store.runtime_settings["aiControlDailyCallBudget"] = 3
        job = self.store.claim()
        self.assertEqual(1, job["attempts"])
        # Another admitted worker can consume the last call after preflight.
        for _ in range(2):
            self.store.begin_call("research", "hash", "other-task")
        with self.assertRaises(AIControlBudgetWait) as blocked:
            self.store.begin_call("research", "hash", job["taskId"])
        self.assertTrue(self.store.defer_budget(job, blocked.exception))
        self.assertFalse(self.store.defer_budget(job, blocked.exception))
        self.store.runtime_settings["aiControlDailyCallBudget"] = 5
        resumed = self.store.claim()  # A raised limit resumes before tomorrow.
        self.assertEqual(job["taskId"], resumed["taskId"])
        self.assertEqual(1, resumed["budgetDeferrals"])
        self.store.fail(resumed, "TimeoutError")
        self.assertEqual("pending", self.store.status()["tasks"][0]["status"])
        # Legacy domain workloads retain their own budgets but share the execution ledger.
        self.store.begin_call("model-review", "hash")
        self.assertEqual(3, self.store.status()["modelCallsUsedToday"])
        self.store.runtime_settings["aiControlDailyTaskBudget"] = 0
        with self.store.transaction() as c:
            c.execute("UPDATE ai_control_tasks SET available_at='2000'")
        with self.assertRaises(AIControlBudgetWait) as blocked:
            self.store.claim()
        self.assertEqual("ai-task-budget-exhausted", blocked.exception.code)
        # Removing limits admits work even beyond both numeric settings while
        # preserving all usage counters; re-enabling resumes enforcement.
        self.store.runtime_settings.update(aiControlBudgetEnabled="false", aiControlDailyCallBudget=0)
        unlimited = self.store.claim()
        self.store.begin_call("research", "hash", unlimited["taskId"])
        status = self.store.status()
        self.assertFalse(status["budgetEnabled"])
        self.assertEqual(4, status["modelCallsUsedToday"])
        self.assertIsNone(status["modelCallsRemainingToday"])
        self.assertEqual("ready", status["observationScheduling"]["status"])
        self.store.fail(unlimited, "TimeoutError")
        self.store.runtime_settings.update(aiControlBudgetEnabled="true", aiControlDailyCallBudget=5)
        # A new UTC day admits due work without a counter reset or lost task.
        self.store.runtime_settings["aiControlDailyTaskBudget"] = 10
        with patch("digital_twin.modules.ai_orchestration.infrastructure.mysql_control.stamp", return_value=reset):
            self.assertEqual(job["taskId"], self.store.claim()["taskId"])

    def test_central_model_requires_frozen_input_and_keeps_failed_attempt_replay(self):
        import gzip
        import json
        from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input, validate_execution_input
        self.store.seed(SUBJECT)
        job = self.store.claim()
        envelope = freeze_execution_input({**PACKET, "taskId": job["taskId"]}, [{"summary": "old analysis"}], [{"runId": "research-1"}])
        with self.assertRaises(ValueError):
            self.store.begin_call("independent-observation", envelope["promptHash"], job["taskId"])
        input_id = self.store.save_execution_input(job, envelope)
        with self.assertRaises(ValueError):
            self.store.begin_call("independent-observation", "wrong-hash", job["taskId"], input_id)
        call_id = self.store.begin_call("independent-observation", envelope["promptHash"], job["taskId"], input_id)
        self.store.finish_call(call_id, "TimeoutError")
        with self.store.connect() as connection:
            row = connection.execute("SELECT i.input_hash,i.artifact_gzip FROM ai_control_inputs i JOIN ai_control_input_calls c ON c.input_id=i.input_id WHERE c.call_id=%s", (call_id,)).fetchone()
        saved = json.loads(gzip.decompress(row["artifact_gzip"]))
        self.assertEqual(envelope, saved)
        self.assertEqual(row["input_hash"], validate_execution_input(saved))
        self.store.complete(job, {"summary": "completed"}, [])
        self.assertEqual("", self.store.save_execution_input(job, envelope))
        with self.assertRaises(ValueError):
            self.store.begin_call("independent-observation", envelope["promptHash"], job["taskId"], input_id)
