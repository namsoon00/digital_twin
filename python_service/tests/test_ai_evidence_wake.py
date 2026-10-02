"""Durable committed-evidence wakeups survive late commits, retries and restarts."""
from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from digital_twin.modules.ai_orchestration.domain.evidence_wake import evidence_wake_targets
from digital_twin.modules.ai_orchestration.domain.planning import identity, stamp
from digital_twin.modules.reasoning.contracts import ONTOLOGY_REASONING_COMPLETED
from test_ai_control import SUBJECT


def payload(subject=SUBJECT, snapshot="snapshot-new"):
    return {"status": "ok", "projectionOutcomes": [{"accountId": subject["accountId"], "worldId": subject["worldId"],
        "sourceAboxSnapshotId": snapshot, "generationAligned": True, "nativeTypeDbReasoningCompleted": True,
        "alertPipeline": {"targetSymbols": [subject["symbol"]]}}]}


class EvidenceWakeContractTests(unittest.TestCase):
    def test_only_explicit_committed_subject_scope_routes_and_producer_includes_world(self):
        self.assertEqual("snapshot-new", evidence_wake_targets(payload(), [SUBJECT])[0]["sourceSnapshotId"])
        for changed in ({"worldId": "shadow:elsewhere"}, {"accountId": "other"}, {"generationAligned": False},
                        {"nativeTypeDbReasoningCompleted": False}, {"sourceAboxSnapshotId": ""},
                        {"alertPipeline": {"targetSymbols": ["OTHER"]}}):
            value = payload(); value["projectionOutcomes"][0].update(changed)
            with self.subTest(changed=changed):
                self.assertEqual([], evidence_wake_targets(value, [SUBJECT]))
        from digital_twin.modules.reasoning.public import OntologyReasoningRunner
        runner = object.__new__(OntologyReasoningRunner)
        result = runner.projection_alert_outcomes(SimpleNamespace(last_ontology_projection_results={SUBJECT["accountId"]: {
            "status": "ok", "ontologyWorld": {"worldId": SUBJECT["worldId"]},
            "inferenceBox": {"sourceAboxSnapshotId": "s1", "generationAligned": True, "nativeTypeDbReasoningCompleted": True},
            "alertPipeline": {"status": "no-signal", "targetSymbols": [SUBJECT["symbol"]]},
        }}))
        self.assertEqual(SUBJECT["worldId"], result[0]["worldId"])
        self.assertEqual("s1", evidence_wake_targets({"status": "ok", "projectionOutcomes": result}, [SUBJECT])[0]["sourceSnapshotId"])


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class EvidenceWakeStorageTests(unittest.TestCase):
    def setUp(self):
        from digital_twin.infrastructure.settings import runtime_settings, load_local_env
        from mysql_fixtures import mysql_test_settings
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
        from digital_twin.infrastructure.transactions.ai_observation_wake import AIObservationEvidenceWake
        load_local_env()  # Resolve connection settings without opening a database.
        isolated = mysql_test_settings()  # Acquire the fixture lock before any runtime DB lookup.
        self.settings = {**runtime_settings(), **isolated, "aiControlBudgetEnabled": "false"}
        self.control = MySQLAIControlStore(self.settings)
        self.waker = AIObservationEvidenceWake(self.settings)
        self.clean()

    def clean(self):
        with self.control.transaction() as connection:
            for table in ("ai_control_evidence_events", "ai_control_evidence_wakes", "ai_control_input_calls", "ai_control_inputs",
                          "ai_control_tasks", "ai_control_calls", "ai_control_budget"):
                connection.execute("DELETE FROM " + table)
            connection.execute("DELETE FROM domain_events WHERE name=%s", (ONTOLOGY_REASONING_COMPLETED,))

    def tearDown(self):
        self.clean()

    def event(self, event_id="wake-one", subject=SUBJECT, snapshot="s1", occurred_at=None):
        from digital_twin.infrastructure.mysql_operational_events import insert_domain_event_with_connection
        from digital_twin.shared_kernel.events import DomainEvent
        event = DomainEvent(ONTOLOGY_REASONING_COMPLETED, "test", event_id=event_id,
            occurred_at=occurred_at or stamp(), payload=payload(subject, snapshot))
        with self.control.transaction() as connection:
            insert_domain_event_with_connection(connection, event)

    def task(self, subject=SUBJECT, task_id="scheduled", **values):
        job = {**subject, "taskId": task_id, "capability": "observe", "availableAt": "2099-01-01T00:00:00Z", **values}
        with self.control.transaction() as connection:
            self.control.insert(connection, job)
        return job

    def row(self, task_id="scheduled"):
        with self.control.connect() as connection:
            return connection.execute("SELECT * FROM ai_control_tasks WHERE task_id=%s", (task_id,)).fetchone()

    def test_duplicate_events_coalesce_and_late_commit_is_not_skipped_by_watermark(self):
        self.assertEqual(3, self.control.retrieval_round_budget())
        self.control.runtime_settings.update(aiControlBudgetEnabled="true", aiControlDailyCallBudget="3")
        self.assertEqual(1, self.control.retrieval_round_budget())
        call_id = self.control.begin_call("research", "hash", "budget-test")
        self.control.finish_call(call_id)
        self.assertEqual(0, self.control.retrieval_round_budget())
        second = {**SUBJECT, "symbol": "OTHER"}
        self.task(); self.task(second, "second")
        self.event(occurred_at="2026-10-02T02:00:00Z")
        self.assertEqual(1, self.waker.run_once([SUBJECT, second])["tasksWoken"])
        self.assertEqual("wake-one", json.loads(self.row()["payload_json"])["evidenceWake"]["eventId"])
        self.assertEqual({"eventsConsumed": 0, "tasksWoken": 0}, self.waker.run_once([SUBJECT, second]))
        self.event("late-event", second, occurred_at="2026-10-02T01:00:00Z")
        self.assertEqual(1, self.waker.run_once([SUBJECT, second])["tasksWoken"])
        self.event("duplicate-snapshot", occurred_at="2026-10-02T03:00:00Z")
        self.assertEqual(0, self.waker.run_once([SUBJECT, second])["tasksWoken"])

    def test_arrival_during_processing_survives_restart_and_wakes_successor_with_cooldown(self):
        self.task(availableAt="2020-01-01T00:00:00Z")
        claimed = self.control.claim()
        self.event()
        self.assertEqual(0, self.waker.run_once([SUBJECT])["tasksWoken"])
        self.assertTrue(self.control.complete(claimed, {"status": "unchanged"}, [
            {**SUBJECT, "taskId": "next", "capability": "observe", "availableAt": "2099-01-01T00:00:00Z"}]))
        from digital_twin.infrastructure.transactions.ai_observation_wake import AIObservationEvidenceWake
        restarted = AIObservationEvidenceWake(self.settings)
        before = datetime.now(timezone.utc)
        self.assertEqual(1, restarted.run_once([SUBJECT])["tasksWoken"])
        due = datetime.fromisoformat(self.row("next")["available_at"].replace("Z", "+00:00"))
        self.assertGreater(due, before + timedelta(minutes=14))
        self.assertLess(due, before + timedelta(minutes=16))
        self.assertEqual(0, restarted.run_once([SUBJECT])["tasksWoken"])

    def test_retry_backoff_and_other_world_are_preserved(self):
        self.task(availableAt="2020-01-01T00:00:00Z")
        job = self.control.claim(); self.control.fail(job, "TimeoutError")
        due = self.row()["available_at"]
        self.task({**SUBJECT, "worldId": "shadow:other"}, "shadow")
        self.event()
        self.assertEqual(0, self.waker.run_once([SUBJECT])["tasksWoken"])
        self.assertEqual(due, self.row()["available_at"])
        self.assertEqual("2099-01-01T00:00:00Z", self.row("shadow")["available_at"])
        with self.control.connect() as connection:
            self.assertEqual(1, connection.execute("SELECT pending FROM ai_control_evidence_wakes").fetchone()["pending"])
            connection.execute("UPDATE ai_control_tasks SET attempts=2,available_at='2020' WHERE task_id='scheduled'")
        terminal = self.control.claim()
        self.control.fail(terminal, "TimeoutError")
        recovery_id = identity(terminal["taskId"], "recovery")
        recovery_due = self.row(recovery_id)["available_at"]
        self.assertEqual(0, self.waker.run_once([SUBJECT])["tasksWoken"])
        self.assertEqual(recovery_due, self.row(recovery_id)["available_at"])

    def test_receipt_and_queue_timing_rollback_together_then_retry_once(self):
        self.task(); self.event()
        with patch.object(self.waker.store, "wake_pending", side_effect=ValueError("injected failure")):
            with self.assertRaises(ValueError):
                self.waker.run_once([SUBJECT])
        with self.control.connect() as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) AS n FROM ai_control_evidence_events").fetchone()["n"])
            self.assertEqual(0, connection.execute("SELECT COUNT(*) AS n FROM ai_control_evidence_wakes").fetchone()["n"])
        self.assertEqual(1, self.waker.run_once([SUBJECT])["tasksWoken"])
        self.assertEqual(0, self.waker.run_once([SUBJECT])["tasksWoken"])
