import os
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

from digital_twin.modules.ai_orchestration.domain.planning import validate_plan, enabled, identity, stamp
from digital_twin.modules.ai_orchestration.application.control import AIControlService
from digital_twin.modules.ai_orchestration.infrastructure.execution import ai_execution


SUBJECT = {"accountId": "control-test", "symbol": "TEST", "name": "Test", "worldId": "portfolio:local:control-test"}
PACKET = {**SUBJECT, "sourceSnapshotId": "snapshot-1", "facts": [{"id": "quote-1", "price": 100}]}
PLAN = {"summary": "이전 관찰과 비교할 첫 근거입니다.", "hypothesis": "실적 변화가 가격 흐름과 연관될 수 있습니다.",
        "counterEvidence": "기간별 실적이 없어 확인이 필요합니다.", "comparison": "첫 관찰입니다.",
        "notification": {"send": False, "reason": "첫 관찰이라 이전 흐름을 더 확인합니다."},
        "evidenceIds": ["quote-1"], "questions": [{"question": "공식 발표에서 최근 분기 실적이 개선되었는가?", "capability": "research"}], "nextCheckMinutes": 1}


class AIControlTests(unittest.TestCase):
    def runner(self, **kwargs):
        store = Mock()
        store.claim.return_value = {**SUBJECT, "taskId": "task-1", "capability": "observe", "attempts": 1, "leaseToken": "lease"}
        store.keep_alive.return_value = nullcontext()
        store.memory.return_value = [{"summary": "이전 관찰", "observedAt": "2026-01-01T00:00:00Z"}]
        store.complete.return_value = True
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
        self.assertEqual("이전 관찰", planner.call_args.args[1][0]["summary"])

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
        service, store, planner = self.runner()
        store.memory.return_value = [{"inputFingerprint": observation_fingerprint(PACKET, []), "observedAt": stamp()}]
        self.assertEqual("unchanged", service.run_once()["status"])
        planner.assert_not_called()
        self.assertEqual("observe", store.complete.call_args.args[2][0]["capability"])

    def test_graph_reader_rejects_scope_mismatch_and_changing_generation(self):
        from digital_twin.modules.ai_orchestration.infrastructure.observation_reader import GraphObservationReader
        repository = Mock()
        repository.active_abox_metadata.return_value = {"status": "ok", "aboxSnapshotId": "snap-1", "accountId": SUBJECT["accountId"]}
        repository.active_abox_snapshot_id.return_value = "snap-1"
        repository.active_abox_rule_context.return_value = {"status": "ok", "sourceIdsBySymbol": {"TEST": ["quote"]}}
        repository.read_entity_rows_by_ids.return_value = [{"id": "quote", "symbol": "OTHER", "currentPrice": 100}]
        with self.assertRaises(ValueError):
            GraphObservationReader(repository)(SUBJECT)
        repository.read_entity_rows_by_ids.return_value = [{"id": "quote", "symbol": "TEST", "currentPrice": 100}]
        repository.active_abox_snapshot_id.side_effect = ["snap-1", "snap-2"]
        with self.assertRaises(ValueError):
            GraphObservationReader(repository)(SUBJECT)
        repository.active_abox_snapshot_id.side_effect = None
        packet = GraphObservationReader(repository)(SUBJECT)
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
            for payload in ({"aiControlDailyTaskBudget": -1}, {"command": "anything"}, {"aiControlEnabled": "maybe"}):
                with self.assertRaises(ValueError):
                    save_ai_control_settings(payload)
            save.assert_not_called()
            save_ai_control_settings({"aiControlEnabled": "false", "aiControlDailyCallBudget": "0"})
            save.assert_called_once()


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class AIControlStorageTests(unittest.TestCase):
    def setUp(self):
        from digital_twin.infrastructure.settings import runtime_settings
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
        self.store = MySQLAIControlStore({**runtime_settings(), "aiControlDailyTaskBudget": 10, "aiControlDailyCallBudget": 1})
        self.clean()

    def clean(self):
        with self.store.transaction() as c:
            for table in ("ai_control_tasks", "ai_control_budget", "ai_control_calls"):
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
        call = self.store.begin_call("research", "hash", "task")
        self.store.finish_call(call, "TimeoutError")
        with self.assertRaises(RuntimeError):
            self.store.begin_call("research", "hash", "next-task")
        # Legacy domain workloads retain their own budgets but share the execution ledger.
        self.store.begin_call("model-review", "hash")
        self.store.runtime_settings["aiControlDailyTaskBudget"] = 0
        self.store.seed(SUBJECT)
        self.assertIsNone(self.store.claim())
