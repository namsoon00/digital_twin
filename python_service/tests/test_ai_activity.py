"""Durable operational notices: no extra model calls, stale writes or silent loss."""
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from digital_twin.modules.ai_orchestration.domain.activity import event
from digital_twin.modules.ai_orchestration.domain.execution_resilience import AIExecutionDeferred
from digital_twin.modules.notifications.application.ai_activity_message import render_ai_activity
from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
from digital_twin.modules.notifications.domain.notifications import NotificationJob


class AIActivityContractTests(unittest.TestCase):
    def test_bounded_safe_rendering_does_not_invoke_enrichment(self):
        value = event("model-failed", "call-1", {"name": "기업 <테스트>", "symbol": "TEST"}, {
            "prompt": "private prompt", "response": "private answer", "token": "private credential",
            "reasonCode": "provider error: private credential", "inputTokens": 1200, "outputTokens": 30,
            "promptVersion": "independent-observation-review-v1", "elapsedMs": 1200})
        self.assertNotIn("private", json.dumps(value))
        body = render_ai_activity(value)
        self.assertIn("기업 &lt;테스트&gt;", body)
        self.assertIn("결과 검토", body)
        self.assertIn("입력 1,200 / 출력 30", body)
        job = NotificationJob.create("ignored", message_type="aiActivity", context={"aiActivity": value})
        renderer = NotificationRenderingService(context_enricher=Mock(side_effect=AssertionError("no enrichment")),
                                               template_renderer=Mock(side_effect=AssertionError("no template")))
        self.assertIn("KST", renderer.render(job))
        self.assertNotIn("ignored", job.text)
        self.assertIn("사용 토큰: 미측정", render_ai_activity(event("model-completed", "call-2")))
        self.assertEqual(value["eventId"], event("model-failed", "call-1")["eventId"])

    def test_operator_route_uses_research_bot_without_idle_hours_or_similarity_gate(self):
        from digital_twin.modules.notifications.domain.notification_rule_models import default_notification_rule
        from digital_twin.modules.notifications.domain.message_types import notification_message_types
        from digital_twin.modules.notifications.domain.account_preferences import QUIET_HOURS_BYPASS_MESSAGE_TYPES
        from digital_twin.modules.notifications.infrastructure.notification import transport
        rule = default_notification_rule("aiActivity")
        self.assertIn("aiActivity", notification_message_types())
        self.assertTrue(rule.enabled)
        self.assertFalse(rule.similarity_enabled)
        self.assertFalse(rule.market_hours_enabled)
        self.assertIn("aiActivity", QUIET_HOURS_BYPASS_MESSAGE_TYPES)
        settings = {"aiResearchTelegramBotToken": "999:fixture", "aiResearchTelegramMessageTypes": "aiActivity",
                    "operationsTelegramChatId": "333"}
        with patch.object(transport, "runtime_settings", return_value=settings):
            self.assertEqual("333", transport.notifier_for_ai_research("aiActivity").chat_id)
        fallback = Mock(side_effect=AssertionError("wrong route"))
        dispatcher = NotificationDispatchService(SimpleNamespace(), fallback, operations_notifier_factory=fallback)
        job = NotificationJob.create("operation", account_id="subject", message_type="aiActivity")
        with self.assertRaisesRegex(RuntimeError, "전용 봇"):
            dispatcher.deliver(job, {}, job.text)
        notifier = SimpleNamespace(send=Mock(return_value=SimpleNamespace(delivered=True, label="test", reason="", metadata={})))
        factory = Mock(return_value=notifier)
        dispatcher.ai_research_notifier_factory = factory
        dispatcher.deliver(job, {"subject": object()}, job.text)
        factory.assert_called_once_with("aiActivity", None)
        self.assertEqual("ai-research", job.context["deliveryRoute"])
        self.assertEqual("operations", job.context["deliveryAudience"])


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class AIActivityStorageTests(unittest.TestCase):
    def test_lost_admission_replies_execute_the_model_body_and_reserve_budget_once(self):
        from test_ai_control import PACKET
        from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input
        from digital_twin.modules.ai_orchestration.infrastructure.execution import ai_execution, CURRENT_TASK, CURRENT_INPUT, current_execution_metrics
        from digital_twin.infrastructure.mysql_operational_connection import MySQLConnectionProxy
        from pymysql.err import OperationalError
        job = self.claim()
        envelope = freeze_execution_input({**PACKET, 'taskId': job['taskId']}, [], [])
        key = self.store.save_execution_input(job, envelope)
        original, commits, invoke = MySQLConnectionProxy.commit, [], Mock()
        def lost_reply(connection):
            original(connection); commits.append(True)
            error = OperationalError(2013, 'synthetic lost commit reply'); error.orbit_mysql_statement = 'COMMIT'
            raise error
        task_token, input_token = CURRENT_TASK.set(job['taskId']), CURRENT_INPUT.set(key)
        try:
            with patch.object(MySQLConnectionProxy, 'commit', lost_reply):
                with ai_execution('independent-observation', envelope['prompt'], store=self.store) as call_id:
                    invoke()
                    current_execution_metrics().update(usage={'input_tokens': 100, 'output_tokens': 20})
        finally:
            CURRENT_TASK.reset(task_token); CURRENT_INPUT.reset(input_token)
        invoke.assert_called_once()
        self.assertEqual(4, len(commits))  # Admission, reservation, settlement and provider success.
        with self.store.connect() as c:
            row = c.execute('SELECT status,started_at FROM ai_control_calls WHERE call_id=%s', (call_id,)).fetchone()
            budget = c.execute('SELECT used_count FROM ai_control_budget WHERE day_key=%s', ('calls:'+row['started_at'][:10],)).fetchone()
            self.assertEqual(1, c.execute('SELECT COUNT(*) AS n FROM ai_control_calls').fetchone()['n'])
        self.assertEqual('completed', row['status']); self.assertEqual(1, budget['used_count'])
        self.assertEqual(1, sum(e['stage']=='model-started' for e in self.events()))
        self.assertEqual(1, sum(e['stage']=='model-completed' for e in self.events()))

    def test_committed_call_cannot_start_after_lease_expiry_during_readback(self):
        from test_ai_control import PACKET
        from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input
        from digital_twin.modules.ai_orchestration.infrastructure.execution import ai_execution, CURRENT_TASK, CURRENT_INPUT
        from digital_twin.infrastructure.mysql_operational_connection import MySQLConnectionProxy
        from pymysql.err import OperationalError
        job = self.claim(); envelope = freeze_execution_input({**PACKET, 'taskId': job['taskId']}, [], [])
        key = self.store.save_execution_input(job, envelope)
        original, commits, invoke = MySQLConnectionProxy.commit, [], Mock()
        error = OperationalError(2013, 'synthetic lost commit reply'); error.orbit_mysql_statement = 'COMMIT'
        def expire_after_admission(connection):
            original(connection); commits.append(True)
            if len(commits)==2:
                with self.store.connect() as c:
                    c.execute("UPDATE ai_control_tasks SET lease_until='2000' WHERE task_id=%s", (job['taskId'],))
                raise error
        task_token, input_token = CURRENT_TASK.set(job['taskId']), CURRENT_INPUT.set(key)
        try:
            with patch.object(MySQLConnectionProxy, 'commit', expire_after_admission), self.assertRaises(OperationalError) as caught:
                with ai_execution('independent-observation', envelope['prompt'], store=self.store): invoke()
        finally:
            CURRENT_TASK.reset(task_token); CURRENT_INPUT.reset(input_token)
        invoke.assert_not_called(); self.assertIs(error, caught.exception)
        self.assertEqual('not-confirmed', error.orbit_mysql_commit_verification)

    def setUp(self):
        from mysql_fixtures import mysql_test_settings
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
        from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore
        from digital_twin.infrastructure.transactions.ai_activity_publication import AIActivityPublication
        self.settings = {**mysql_test_settings(), "aiActivityNotificationsEnabled": "true", "aiControlEnabled": "true",
                         "aiControlBudgetEnabled": "false", "aiTokenBudgetEnabled": "false"}
        self.store = MySQLAIControlStore(self.settings)
        self.queue = MySQLNotificationJobStore(self.settings)
        self.publisher = AIActivityPublication(self.store, self.queue, self.settings)
        self.clean()

    def clean(self):
        with self.store.transaction() as c:
            c.execute("DELETE FROM notification_delivery_attempts WHERE job_id IN "
                      "(SELECT job_id FROM notification_jobs WHERE message_type='aiActivity')")
            c.execute("DELETE FROM notification_jobs WHERE message_type='aiActivity'")
            for table in ("ai_activity_events", "ai_control_input_calls", "ai_control_retrieval_rounds", "ai_control_inputs",
                          "ai_control_tasks", "ai_control_budget", "ai_control_calls", "ai_control_call_metrics",
                          "ai_control_execution_state"):
                c.execute("DELETE FROM " + table)

    def tearDown(self):
        self.clean()

    def events(self):
        with self.store.connect() as c:
            return [json.loads(r["payload_json"]) for r in c.execute("SELECT payload_json FROM ai_activity_events ORDER BY created_at,event_id").fetchall()]

    def claim(self):
        from test_ai_control import SUBJECT
        with self.store.transaction() as c:
            self.store.insert(c, {**SUBJECT, "taskId": "activity-task", "capability": "observe"})
        return self.store.claim()

    def test_task_input_calls_and_children_record_once_with_lease_fencing(self):
        from test_ai_control import PACKET
        from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input
        job = self.claim()
        envelope = freeze_execution_input({**PACKET, "taskId": job["taskId"]}, [], [])
        input_id = self.store.save_execution_input(job, envelope)
        self.store.save_execution_input(job, envelope)
        self.store.record_activity(job, "evidence-ready", factCount=1)
        call_id = self.store.begin_call("independent-observation", envelope["promptHash"], job["taskId"], input_id)
        self.store.finish_call(call_id, metrics={"usage": {"input_tokens": 100, "output_tokens": 20}, "totalMs": 500})
        self.store.finish_call(call_id, "stale failure")
        child = {**{k: job[k] for k in ("accountId", "name", "symbol", "worldId")},
                 "taskId": "activity-child", "capability": "research", "availableAt": "2099-01-01T00:00:00Z"}
        self.assertTrue(self.store.complete(job, {"status": "unchanged"}, [child]))
        self.assertFalse(self.store.complete(job, {"status": "unchanged"}, [child]))
        self.store.record_activity(job, "memory-read", memoryCount=9)
        rows = self.events()
        stages = [r["stage"] for r in rows]
        for stage in ("input-prepared", "task-completed", "task-scheduled"):
            self.assertEqual(1, stages.count(stage))
        self.assertNotIn("memory-read", stages)
        self.assertNotIn("model-failed", stages)
        completed = next(r for r in rows if r["stage"] == "model-completed")
        self.assertEqual((100, 20), (completed["details"]["inputTokens"], completed["details"]["outputTokens"]))
        self.assertEqual(job["symbol"], completed["symbol"])

    def test_call_settlement_and_provider_success_survive_a_lost_commit_reply_once(self):
        from test_ai_control import PACKET
        from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input
        from digital_twin.infrastructure.mysql_operational_connection import MySQLConnectionProxy
        from pymysql.err import OperationalError
        job = self.claim()
        envelope = freeze_execution_input({**PACKET, "taskId": job["taskId"]}, [], [])
        key = self.store.save_execution_input(job, envelope)
        ticket = self.store.acquire_execution()
        call_id = self.store.begin_call("independent-observation", envelope["promptHash"], job["taskId"], key)
        original, commits = MySQLConnectionProxy.commit, []
        def committed_without_reply(connection):
            original(connection)
            commits.append(True)
            error = OperationalError(2013, "synthetic lost commit reply")
            error.orbit_mysql_statement = "COMMIT"
            raise error
        with patch.object(MySQLConnectionProxy, "commit", committed_without_reply):
            self.store.finish_call(call_id, metrics={"usage": {"input_tokens": 100, "output_tokens": 20}, "totalMs": 500})
            self.store.finish_execution(ticket, "success")
        self.assertEqual(2, len(commits))
        self.store.finish_call(call_id, "late-failure")
        with self.store.connect() as c:
            call = c.execute("SELECT status,error_kind FROM ai_control_calls WHERE call_id=%s", (call_id,)).fetchone()
            budget = c.execute("SELECT used_count FROM ai_control_budget WHERE day_key=%s", ("calls:" + envelope["current"]["capturedAt"][:10],)).fetchone()
            measured = json.loads(c.execute("SELECT metrics_json FROM ai_control_call_metrics WHERE call_id=%s", (call_id,)).fetchone()["metrics_json"])
        self.assertEqual({"status": "completed", "error_kind": ""}, call)
        self.assertEqual(1, budget["used_count"])
        self.assertEqual({"input_tokens": 100, "output_tokens": 20}, measured["usage"])
        self.assertEqual(1, sum(e["stage"] == "model-completed" for e in self.events()))
        self.store.execution_wait()

    def test_outbox_rolls_back_then_retries_without_lost_or_duplicate_receipt(self):
        self.claim()
        enqueue = self.queue.enqueue_with_connection
        def fail_after_insert(connection, job):
            enqueue(connection, job)
            raise RuntimeError("outbox interrupted")
        with patch.object(self.queue, "enqueue_with_connection", side_effect=fail_after_insert), self.assertRaises(RuntimeError):
            self.publisher.run_once()
        with self.store.connect() as c:
            self.assertEqual("", c.execute("SELECT published_at FROM ai_activity_events").fetchone()["published_at"])
            self.assertEqual(0, c.execute("SELECT COUNT(*) AS n FROM notification_jobs WHERE message_type='aiActivity'").fetchone()["n"])
        self.assertEqual(1, self.publisher.run_once()["queued"])
        self.assertEqual(0, self.publisher.run_once()["queued"])
        with self.queue.connect() as c:
            row = c.execute("SELECT text,payload_json FROM notification_jobs WHERE message_type='aiActivity'").fetchone()
        job = self.queue.job_from_row(row)
        notifier = SimpleNamespace(send=Mock(return_value=SimpleNamespace(delivered=True, label="test", reason="", metadata={})))
        dispatcher = NotificationDispatchService(self.queue, Mock(), ai_research_notifier_factory=lambda *_: notifier)
        dispatcher.deliver(job, {}, NotificationRenderingService().render(job))
        with self.queue.connect() as c:
            receipt = c.execute("SELECT status,metadata_json FROM notification_delivery_attempts WHERE job_id=%s", (job.job_id,)).fetchone()
        self.assertEqual("delivered", receipt["status"])
        metadata = json.loads(receipt["metadata_json"])
        self.assertEqual("ai-research", metadata["deliveryRoute"])
        self.assertEqual("task-started", metadata["aiActivity"]["stage"])

    def test_wait_heartbeat_dedup_is_persistent_and_optional(self):
        start = datetime.now(timezone.utc)
        for activity in ("checking", "recovery-wait", "checking", "recovery-wait"):
            self.store.record_runtime_pulse(now=start, worker_id="worker", activity=activity)
            start += timedelta(seconds=1)
        self.assertEqual(1, len(self.events()))
        self.store.runtime_settings["aiActivityNotificationsEnabled"] = "false"
        self.store.record_runtime_pulse(now=start, worker_id="worker", activity="idle")
        self.assertEqual(1, len(self.events()))
        self.store.runtime_settings["aiActivityNotificationsEnabled"] = "true"
        self.store.record_runtime_pulse(now=start + timedelta(seconds=1), worker_id="worker", activity="idle")
        self.assertEqual(2, len(self.events()))

    def test_recheck_preserves_failure_history_and_allows_exactly_one_probe(self):
        ticket = self.store.acquire_execution()
        self.store.finish_execution(ticket, "failure", {"category": "quota"})
        with self.assertRaises(AIExecutionDeferred):
            self.store.acquire_execution()
        self.assertEqual("probe-requested", self.store.request_execution_recheck()["status"])
        with self.store.connect() as c:
            state = json.loads(c.execute("SELECT state_json FROM ai_control_execution_state WHERE scope_key='shared-local-model'").fetchone()["state_json"])
        self.assertEqual(1, state["quotaFailures"])
        def probe(_):
            try:
                return self.store.acquire_execution()
            except AIExecutionDeferred:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = [t for t in pool.map(probe, range(2)) if t]
        self.assertEqual(1, len(winners))
        self.assertEqual("probe-running", self.store.request_execution_recheck()["status"])
        self.store.finish_execution(winners[0], "failure", {"category": "quota"})
        with self.assertRaises(AIExecutionDeferred):
            self.store.acquire_execution()
