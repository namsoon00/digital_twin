"""Graph outage isolation, immutable evidence, leased preparation and scoped wakeups."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
import unittest
from unittest.mock import Mock, patch

from digital_twin.modules.reasoning.domain.observation_binding import VERSION as BINDING_VERSION
from digital_twin.modules.reasoning.domain.observation_checkpoint import CheckpointNotReady, freeze, reopen, scope, VERSION
from digital_twin.modules.reasoning.domain.observation_evidence import EvidenceContractError, EvidenceReadError, evidence_change_identity, select_evidence
from digital_twin.modules.reasoning.domain.observation_session import ObservationEvidenceSession
from digital_twin.modules.reasoning.application.observation_checkpoints import CheckpointEvidenceReader, ObservationCheckpointService
from test_ai_control import SUBJECT, PACKET
import test_ai_control

NOW = datetime(2026, 11, 1, 1, tzinfo=timezone.utc)
BINDING = {"version": BINDING_VERSION, "deploymentId": "live", "engineVersion": "v2",
    "graphStoreBinding": "graph-live", "sharedGraphStoreBinding": "graph-market", "controlVersion": 1}


def session():
    quote = {**PACKET["facts"][0], "symbol": SUBJECT["symbol"], "sourceAsOf": NOW.isoformat(), "maxAgeMinutes": 2}
    report = {**quote, "id": "report", "kind": "company-financial-state", "payload": "original-document " * 3000}
    facts, coverage = select_evidence([quote, report])
    packet = {**deepcopy(PACKET), "capturedAt": NOW.isoformat().replace("+00:00", "Z"),
        "reasoningBinding": deepcopy(BINDING), "facts": facts, "coverage": coverage}
    return ObservationEvidenceSession(packet, [quote, report])


class CheckpointContractTests(unittest.TestCase):
    def test_newer_known_evidence_requires_refresh_but_an_old_event_does_not(self):
        store = Mock()
        artifact = freeze(session())
        store.read.return_value = (artifact, {"status": "ready"})
        reader = CheckpointEvidenceReader(store, lambda: BINDING, lambda: NOW + timedelta(seconds=30))
        request = {**SUBJECT, "evidenceWake": {"sourceSnapshotId": "new-snapshot", "occurredAt": (NOW + timedelta(seconds=1)).isoformat()}}
        with self.assertRaises(CheckpointNotReady) as raised:
            reader.capture_session(request)
        self.assertEqual(["newer-verified-evidence-pending"], raised.exception.conditions)
        self.assertTrue(store.request.call_args.kwargs["force"])
        request["evidenceWake"]["occurredAt"] = (NOW - timedelta(seconds=1)).isoformat()
        self.assertEqual("snapshot-1", reader.capture_session(request).packet()["sourceSnapshotId"])
        # A commit during a native READ snapshot must not be hidden by its later
        # completion clock. Its exact source identity still has to be observed.
        artifact["captureStartedAt"] = (NOW - timedelta(seconds=30)).isoformat()
        with self.assertRaises(CheckpointNotReady):
            reader.capture_session(request)

    def test_wait_diagnostic_names_evidence_preparation_separately_from_model_state(self):
        from digital_twin.modules.ai_orchestration.domain.question_progress import task_wait
        row = {"status": "pending", "lastError": "evidence-contract:observation-checkpoint-not-ready",
            "result": {"processingOutcome": {"producer": {"status": "pending", "lastError": "TypeDBDriverException"}}}}
        value = task_wait(row, {}, NOW)
        self.assertEqual("verified-evidence", value["scope"])
        self.assertEqual("TypeDBDriverException", value["producer"]["lastError"])
        self.assertEqual({}, value["modelWait"])
        value = task_wait(row, {"lastFailureCategory": "quota", "blockedUntil": "2026-11-02T00:00:00Z"}, NOW)
        self.assertEqual("verified-evidence", value["scope"])
        self.assertTrue(value["modelWait"])

    def test_copy_preserves_omitted_sources_and_recomputes_quote_age(self):
        original = session()
        artifact = freeze(original)
        restored = reopen(artifact, {**SUBJECT, "capability": "market-observe"}, BINDING, NOW + timedelta(minutes=3))
        self.assertEqual(original.checkpoint_inventory(), restored.checkpoint_inventory())
        self.assertEqual(original.packet()["facts"], restored.packet()["facts"])
        self.assertEqual("stale", restored.packet()["quoteAssessment"]["quotes"][0]["status"])
        self.assertEqual(180, restored.packet()["evidenceCheckpoint"]["ageSeconds"])
        self.assertEqual(original.packet()["capturedAt"], artifact["packet"]["capturedAt"])
        self.assertNotEqual(evidence_change_identity(original.packet(), []), evidence_change_identity(restored.packet(), []))
        self.assertEqual(original.read("company", fact_id="report"), restored.read("company", fact_id="report"))
        self.assertEqual("report", restored.report_index()[0]["factId"])

    def test_cache_cannot_cross_subject_world_deployment_or_future_clock(self):
        artifact = freeze(session())
        for request, binding in (({**SUBJECT, "accountId": "other"}, BINDING),
                ({**SUBJECT, "worldId": "portfolio:other"}, BINDING),
                (SUBJECT, {**BINDING, "controlVersion": 2}),
                ({**SUBJECT, "marketWorldId": "market:other"}, BINDING)):
            with self.subTest(request=request), self.assertRaises(EvidenceContractError):
                reopen(artifact, request, binding, NOW)
        with self.assertRaises(CheckpointNotReady):
            reopen(artifact, SUBJECT, BINDING, NOW - timedelta(seconds=1))

    def test_document_research_can_continue_when_market_copy_is_too_old(self):
        artifact = freeze(session())
        with self.assertRaises(CheckpointNotReady):
            reopen(artifact, {**SUBJECT, "capability": "market-observe"}, BINDING, NOW + timedelta(hours=2))
        reopened = reopen(artifact, {**SUBJECT, "capability": "observe"}, BINDING, NOW + timedelta(hours=2))
        self.assertEqual("stale", reopened.packet()["quoteAssessment"]["quotes"][0]["status"])
        with self.assertRaises(CheckpointNotReady):
            reopen(artifact, SUBJECT, BINDING, NOW + timedelta(days=2))

    def test_missing_inventory_and_modified_selected_fact_are_rejected(self):
        artifact = freeze(session())
        artifact["inventory"] = artifact["inventory"][:1]
        with self.assertRaises(EvidenceContractError):
            reopen(artifact, SUBJECT, BINDING, NOW)
        artifact = freeze(session())
        artifact["packet"]["facts"][0]["currentPrice"] = 999
        with self.assertRaises(EvidenceContractError):
            reopen(artifact, SUBJECT, BINDING, NOW)

    def test_reader_does_not_touch_graph_and_wait_does_not_call_ai(self):
        store = Mock()
        store.read.return_value = (freeze(session()), {"status": "pending", "lastError": "TypeDBDriverException"})
        reader = CheckpointEvidenceReader(store, lambda: BINDING, lambda: NOW)
        self.assertEqual("snapshot-1", reader.capture_session(SUBJECT).packet()["sourceSnapshotId"])
        store.read.return_value = (None, {"status": "pending"})
        service, ledger, model = test_ai_control.AIControlTests().runner(evidence=reader.capture_session)
        ledger.defer_evidence.return_value = True
        self.assertEqual("awaiting-evidence", service.run_once()["status"])
        model.assert_not_called()
        ledger.fail.assert_not_called()
        ledger.complete.assert_not_called()

    def test_cached_graph_evidence_reaches_authored_judgment_during_refresh_failure(self):
        store = Mock()
        store.read.return_value = (freeze(session()), {"status": "pending", "lastError": "evidence-read:snapshot:TimeoutError"})
        reader = CheckpointEvidenceReader(store, lambda: BINDING, lambda: NOW)
        service, ledger, model = test_ai_control.AIControlTests().runner(evidence=reader.capture_session)
        self.assertEqual("completed", service.run_once()["status"])
        result = ledger.complete.call_args.args[1]
        self.assertEqual("verified-graph-copy", result["input"]["evidenceCheckpoint"]["mode"])
        self.assertEqual("snapshot-1", result["input"]["sourceSnapshotId"])
        self.assertTrue(model.called)

    def test_producer_failure_keeps_copy_and_rejects_stale_route_without_capture(self):
        store, reader = Mock(), Mock()
        job = {"scopeId": scope(SUBJECT, BINDING), "request": SUBJECT, "binding": BINDING, "leaseToken": "one"}
        store.claim.return_value = job
        reader.capture_session.side_effect = EvidenceReadError("snapshot", TimeoutError("private data"))
        service = ObservationCheckpointService(store, reader, lambda: BINDING, lambda: [SUBJECT], clock=lambda: NOW)
        self.assertEqual("deferred", service.run_once()["status"])
        store.complete.assert_not_called()
        self.assertNotIn("private", repr(store.fail.call_args))
        service.binding_reader = lambda: {**BINDING, "controlVersion": 2}
        reader.reset_mock()
        self.assertEqual("retired", service.run_once()["status"])
        reader.capture_session.assert_not_called()

    def test_producer_never_accepts_incomplete_capture_or_calls_model(self):
        store, reader = Mock(), Mock()
        store.claim.return_value = {"scopeId": scope(SUBJECT, BINDING), "request": SUBJECT, "binding": BINDING}
        reader.capture_session.return_value = session()
        store.complete.return_value = True
        result = ObservationCheckpointService(store, reader, lambda: BINDING, lambda: [SUBJECT], clock=lambda: NOW).run_once()
        self.assertEqual("prepared", result["status"])
        artifact = store.complete.call_args.args[1]
        self.assertEqual(VERSION, artifact["version"])
        self.assertEqual(2, len(artifact["inventory"]))

    def test_checkpoint_event_is_subject_scoped_without_inventing_inference(self):
        from digital_twin.modules.ai_orchestration.domain.evidence_wake import evidence_wake_targets
        payload = {**SUBJECT, "status": "ok", "checkpointVersion": VERSION, "sourceSnapshotId": "s1",
            "materialHash": "h1", "reasoningBinding": BINDING, "authority": "verified-facts-only"}
        self.assertEqual(1, len(evidence_wake_targets(payload, [SUBJECT])))
        self.assertEqual([], evidence_wake_targets({**payload, "authority": "investment-action"}, [SUBJECT]))
        self.assertEqual([], evidence_wake_targets({**payload, "accountId": "other"}, [SUBJECT]))

    def test_evidence_pool_cannot_occupy_model_pool(self):
        import tempfile
        from pathlib import Path
        from digital_twin.infrastructure import background_capacity as capacity
        with tempfile.TemporaryDirectory() as directory:
            occupied = capacity.BackgroundCapacity(Path(directory) / "background-capacity", pressure_reader=lambda: "normal", emit=Mock())
            with occupied.acquire() as admitted:
                self.assertTrue(admitted)
                with patch.dict(os.environ, {"ORBIT_BACKGROUND_ADMISSION": "1", "ORBIT_BACKGROUND_WORKER": "ai-evidence"}), \
                     patch.object(capacity, "_managed_capacity", None), \
                     patch("digital_twin.infrastructure.settings.data_dir", return_value=Path(directory)), \
                     patch.object(capacity, "host_memory_pressure", return_value="normal"):
                    self.assertEqual("prepared", capacity.managed_background_call(lambda: "prepared"))


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class CheckpointStorageTests(unittest.TestCase):
    def setUp(self):
        from digital_twin.infrastructure.settings import load_local_env
        from mysql_fixtures import mysql_test_settings
        from digital_twin.modules.reasoning.infrastructure.mysql_observation_checkpoints import MySQLObservationCheckpointStore
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
        load_local_env()
        self.settings = mysql_test_settings()
        self.store = MySQLObservationCheckpointStore(self.settings)
        self.control = MySQLAIControlStore(self.settings)
        self.addCleanup(self.clean)
        self.clean()
        self.key = scope(SUBJECT, BINDING)

    def clean(self):
        from digital_twin.modules.reasoning.domain.observation_checkpoint import READY_EVENT
        with self.store.transaction() as connection:
            connection.execute("DELETE FROM observation_evidence_checkpoints")
            connection.execute("DELETE FROM ai_control_tasks WHERE account_id=%s", (SUBJECT["accountId"],))
            connection.execute("DELETE FROM domain_events WHERE name=%s AND "
                "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.accountId'))=%s", (READY_EVENT, SUBJECT["accountId"]))

    def prepare(self):
        now = datetime.now(timezone.utc)
        self.store.request(self.key, SUBJECT, BINDING, now)
        job = self.store.claim()
        self.assertIsNotNone(job)
        artifact = freeze(session())
        artifact["packet"]["capturedAt"] = now.isoformat().replace("+00:00", "Z")
        self.assertTrue(self.store.complete(job, artifact))
        return job, artifact

    def test_integrity_and_failed_refresh_preserve_original_artifact(self):
        job, artifact = self.prepare()
        with self.store.transaction() as connection:
            connection.execute("UPDATE observation_evidence_checkpoints SET status='pending',available_at='2000' WHERE scope_id=%s", (self.key,))
        second = self.store.claim()
        self.assertIsNone(self.store.claim())
        self.assertTrue(self.store.fail(second, "evidence-read:snapshot:TimeoutError"))
        self.assertFalse(self.store.complete(job, artifact))
        read, state = self.store.read(self.key)
        self.assertEqual(artifact, read)
        self.assertEqual("pending", state["status"])
        self.store.request(self.key, SUBJECT, BINDING, datetime.now(timezone.utc))
        self.assertIsNone(self.store.claim())
        with self.store.transaction() as connection:
            connection.execute("UPDATE observation_evidence_checkpoints SET artifact_hash='wrong' WHERE scope_id=%s", (self.key,))
        with self.assertRaises(EvidenceContractError):
            self.store.read(self.key)

    def test_publication_failure_rolls_back_copy_and_lease_loss_cannot_publish(self):
        self.store.request(self.key, SUBJECT, BINDING, datetime.now(timezone.utc))
        job = self.store.claim()
        self.store.publisher = Mock(side_effect=RuntimeError("transaction failed"))
        with self.assertRaises(RuntimeError):
            self.store.complete(job, freeze(session()))
        self.assertIsNone(self.store.read(self.key)[0])
        self.store.publisher = Mock()
        with self.store.transaction() as connection:
            connection.execute("UPDATE observation_evidence_checkpoints SET lease_token='another' WHERE scope_id=%s", (self.key,))
        self.assertFalse(self.store.complete(job, freeze(session())))
        self.store.publisher.assert_not_called()

    def test_resume_only_matching_checkpoint_wait_without_resetting_other_backoffs(self):
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_evidence_wake import wake_checkpoint_waiters
        with self.control.transaction() as connection:
            for key, error in (("checkpoint", "evidence-contract:observation-checkpoint-not-ready"), ("budget", "ai-call-budget-exhausted")):
                self.control.insert(connection, {**SUBJECT, "taskId": key, "capability": "observe", "availableAt": "2099"})
                connection.execute("UPDATE ai_control_tasks SET last_error=%s WHERE task_id=%s", (error, key))
            self.assertEqual(1, wake_checkpoint_waiters(connection, PACKET))
            rows = connection.execute("SELECT task_id,available_at FROM ai_control_tasks WHERE account_id=%s", (SUBJECT["accountId"],)).fetchall()
        dates = {r["task_id"]: r["available_at"] for r in rows}
        self.assertNotEqual("2099", dates["checkpoint"])
        self.assertEqual("2099", dates["budget"])

    def test_committed_copy_wakes_ai_once_without_a_matched_rule(self):
        from digital_twin.infrastructure.transactions.observation_checkpoint_publication import publish_checkpoint
        from digital_twin.infrastructure.transactions.ai_observation_wake import AIObservationEvidenceWake
        from digital_twin.modules.reasoning.domain.observation_checkpoint import READY_EVENT
        waker = AIObservationEvidenceWake(self.settings)
        with self.control.transaction() as connection:
            connection.execute("DELETE FROM domain_events WHERE name=%s", (READY_EVENT,))
            connection.execute("DELETE FROM ai_control_evidence_wakes")
            self.control.insert(connection, {**SUBJECT, "taskId": "ready-wake", "capability": "observe", "availableAt": "2099"})
        self.store.publisher = publish_checkpoint
        with patch("digital_twin.infrastructure.transactions.observation_checkpoint_publication.active_observation_binding", return_value=BINDING):
            self.prepare()
            self.assertEqual(1, waker.run_once([SUBJECT])["tasksWoken"])
            with self.store.transaction() as connection:
                connection.execute("UPDATE observation_evidence_checkpoints SET status='pending',available_at='2000' WHERE scope_id=%s", (self.key,))
            job = self.store.claim()
            self.assertTrue(self.store.complete(job, freeze(session())))
        with self.store.connect() as connection:
            count = connection.execute("SELECT COUNT(*) AS count FROM domain_events WHERE name=%s", (READY_EVENT,)).fetchone()["count"]
            task = connection.execute("SELECT available_at FROM ai_control_tasks WHERE task_id='ready-wake'").fetchone()
        self.assertEqual(1, count)
        self.assertNotEqual("2099", task["available_at"])
        self.assertEqual(0, waker.run_once([SUBJECT])["tasksWoken"])


if __name__ == "__main__":
    unittest.main()
