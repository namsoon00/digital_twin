"""Credential-free tests for the passive verifier; no application imports or DB setup."""

from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import runtime_continuity_reads as reads
import verify_runtime_continuity as verify


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
START = NOW - timedelta(minutes=15)


def linked_row():
    row = {key: "private-" + key for key in (
        "job_id", "stored_event_id", "source_event_id", "case_id", "subject_case_id", "stored_ai_request_id",
        "result_id", "insight_id", "inference_generation_id", "candidate_fingerprint", "source_abox_snapshot_id")}
    row.update(stored_snapshot_id="private-snapshot", executed_snapshot_id="private-snapshot",
               source_at=verify.iso(NOW - timedelta(minutes=5)), source_mode="live",
               reasoning_at=verify.iso(NOW - timedelta(minutes=4)), ai_at=verify.iso(NOW - timedelta(minutes=3)),
               ai_status="completed", ai_authored=1, publication_contract_passed=1,
               publication_mode="ai-authored", trace_complete=1)
    row.update({key: 1 for key in ("account_symbol_match", "candidate_match", "generation_in_result",
                                  "abox_in_result", "insight_match", "batch_request_match", "source_scope_match",
                                  "source_boundary_match", "executed_source_match")})
    return row


def arguments(output="/tmp/fixture-runtime-evidence.json"):
    return verify.parse_args(["--allow-live-read-only", "--duration-seconds", "60", "--output", output])


def healthy_observation():
    queues = [dict(verify.queue_evidence({"rows": [], "truncated": False}, state, NOW, 300), kind=kind)
              for kind, spec in reads.QUEUES.items() for state in spec[3]]
    return {"http": [{"path": verify.ENDPOINTS[0], "statusCode": 200, "durationMs": 2, "identity": "web"},
                     {"path": verify.ENDPOINTS[1], "statusCode": 200, "durationMs": 2, "generatedAgeSeconds": 0}],
            "supervisor": {"state": "running", "ageSeconds": 2},
            "database": {"controlIdentity": "control", "expectedDeploymentCount": 1, "expectedQueueReads": 14,
                         "deployments": [{"id": "dep", "release": "release", "health": "ready", "hasError": False, "roles": ["active", "delivery"]}],
                         "sources": {"latestAt": verify.iso(NOW), "latestAgeSeconds": 0},
                         "queues": queues, "lineage": []}}


class RuntimeContinuityTests(unittest.TestCase):
    def test_opt_in_and_bounds_fail_before_io(self):
        with redirect_stdout(io.StringIO()), patch("sys.stderr", new_callable=io.StringIO):
            for argv in (["--output", "/tmp/test.json"], ["--allow-live-read-only", "--output", "relative.json"],
                         ["--allow-live-read-only", "--output", "/tmp/test.json", "--interval-seconds", "1"],
                         ["--allow-live-read-only", "--output", "/tmp/test.json", "--duration-seconds", "86401"],
                         ["--allow-live-read-only", "--output", str(verify.ROOT / "private-report.json")]):
                with self.subTest(argv=argv), self.assertRaises(SystemExit):
                    verify.parse_args(argv)

    def test_remote_credentials_redirect_targets_and_dns_names_rejected(self):
        for value in ("https://127.0.0.1:3000", "http://secret@127.0.0.1", "http://example.org", "http://127.0.0.1/api/chat",
                      "http://127.0.0.1?token=secret", "http://192.0.2.1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                verify.local_endpoint(value)
        self.assertEqual(verify.local_endpoint("http://[::1]:3000"), ("::1", 3000))
        with self.assertRaises(ValueError):
            reads.database_options({}, 3)
        with self.assertRaises(ValueError):
            reads.database_options({"MYSQL_HOST": "192.0.2.1", "MYSQL_USER": "fixture", "MYSQL_DATABASE": "fixture"}, 3)

    def test_session_is_read_only_with_limits_and_rollback_without_commit(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchmany.return_value = [{"n": 1}, {"n": 2}, {"n": 3}]
        db = reads.ReadOnlyDatabase({}, 2, 500, connect=lambda **_kw: connection)
        self.assertEqual(db.read("SELECT n FROM fixture"), {"rows": [{"n": 1}, {"n": 2}], "truncated": True})
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(statements[:4], ["SET SESSION TRANSACTION READ ONLY", "SET SESSION MAX_EXECUTION_TIME = %s",
                                         "SET SESSION lock_wait_timeout = 1", "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"])
        self.assertEqual(cursor.execute.call_args.args, ("SELECT n FROM fixture LIMIT %s", (3,)))
        for sql in ("UPDATE fixture SET n = 0", "SELECT n FROM fixture FOR UPDATE", "SELECT 1; DELETE FROM fixture"):
            with self.assertRaises(ValueError):
                db.read(sql)
        db.close()
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()

    def test_failed_read_only_setup_never_selects_or_falls_back(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError("secret")
        with self.assertRaises(RuntimeError):
            reads.ReadOnlyDatabase({}, 2, 500, connect=lambda **_kw: connection)
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()

    def test_healthy_infrastructure_without_ai_is_inconclusive_e2e(self):
        result = verify.verdicts([healthy_observation()], arguments(), [], True)
        self.assertEqual(result["sampledInfrastructure"], "pass")
        self.assertEqual(result["liveAiLineage"]["status"], "inconclusive")
        self.assertFalse(result["sourceProgress"]["timestampAdvanced"])

    def test_http_200_alone_cannot_pass_missing_evidence(self):
        result = verify.verdicts([{"http": healthy_observation()["http"]}], arguments(), [], True)
        self.assertEqual(result["sampledInfrastructure"], "inconclusive")
        self.assertEqual(result["liveAiLineage"]["status"], "inconclusive")

    def test_linked_ai_is_separate_from_publication_and_notification(self):
        row = linked_row()
        value = verify.lineage_evidence(row, NOW, START, verify.Redactor())
        self.assertTrue(value["storedLinkedAi"])
        self.assertTrue(value["sourceToAiEntirelyInWindow"])
        self.assertFalse(value["recordedTelegramDelivery"])
        self.assertFalse(value["storedPublication"])
        row.update(publication_id="pub", publication_outcome="FINAL_DECISION", decision_id="decision", decision_match=1, attempt_id="attempt",
                   notification_id="notification", notification_match=1, is_mock=0, data_quality="actual",
                   delivery_status="delivered", channel="telegram", delivery_at=verify.iso(NOW - timedelta(seconds=30)))
        self.assertTrue(verify.lineage_evidence(row, NOW, START, verify.Redactor())["recordedTelegramDelivery"])
        for key, replacement in (("delivery_status", "done"), ("channel", "console"), ("is_mock", 1), ("decision_match", 0), ("attempt_id", None)):
            changed = dict(row, **{key: replacement})
            self.assertFalse(verify.lineage_evidence(changed, NOW, START, verify.Redactor())["recordedTelegramDelivery"])

    def test_historical_ai_fallback_missing_links_mock_and_conflicts_never_pass(self):
        redactor = verify.Redactor()
        for change in ({"source_mode": "mock"}, {"publication_mode": "typedb-fallback"}, {"ai_authored": 0},
                       {"publication_contract_passed": 0}, {"insight_id": None}, {"insight_match": 0},
                       {"candidate_match": None}, {"source_scope_match": 0}, {"trace_complete": 0},
                       {"executed_snapshot_id": "other"}, {"ai_at": verify.iso(NOW + timedelta(seconds=1))}):
            with self.subTest(change=change):
                value = verify.lineage_evidence(dict(linked_row(), **change), NOW, START, redactor)
                self.assertFalse(value["storedLinkedAi"])
        value = verify.lineage_evidence(linked_row(), NOW, NOW, redactor)
        self.assertTrue(value["storedLinkedAi"])
        self.assertFalse(value["aiCompletedInWindow"])

    def test_downstream_conflicts_preserve_ai_evidence_but_block_publication_and_delivery(self):
        for key in ("decision_match", "notification_match"):
            row = dict(linked_row(), publication_id="pub", publication_outcome="FINAL_DECISION", decision_id="decision",
                       decision_match=1, notification_id="notification", notification_match=1, is_mock=0, attempt_id="attempt",
                       data_quality="actual", delivery_status="delivered", channel="telegram", delivery_at=verify.iso(NOW))
            row[key] = 0
            evidence = verify.lineage_evidence(row, NOW, START, verify.Redactor())
            self.assertTrue(evidence["storedLinkedAi"])
            self.assertFalse(evidence["storedPublication"])
            self.assertFalse(evidence["recordedTelegramDelivery"])
            observation = healthy_observation()
            observation["database"]["lineage"] = [evidence]
            result = verify.verdicts([observation], arguments(), [], True)
            self.assertEqual(result["liveAiLineage"]["status"], "degraded")
            self.assertEqual(result["liveAiLineage"]["publicationsWithInWindowAi"], 0)

    def test_global_source_requires_exact_persisted_subject_boundary_not_blank_wildcard(self):
        row = linked_row()
        self.assertTrue(verify.lineage_evidence(row, NOW, START, verify.Redactor())["storedLinkedAi"])
        for field in ("source_scope_match", "source_boundary_match"):
            row[field] = None
            evidence = verify.lineage_evidence(row, NOW, START, verify.Redactor())
            self.assertFalse(evidence["storedLinkedAi"])
            self.assertIn(field, evidence["missing"])
            self.assertNotIn(field, evidence["conflicts"])
            row[field] = 1
        row["source_scope_match"] = 0
        self.assertIn("source_scope_match", verify.lineage_evidence(row, NOW, START, verify.Redactor())["conflicts"])
        row = dict(linked_row(), stored_ai_request_id=None, result_id=None, insight_id=None,
                   ai_status=None, ai_authored=0, publication_id="observation", publication_outcome="OBSERVATION", outcome_kind="OBSERVATION")
        evidence = verify.lineage_evidence(row, NOW, START, verify.Redactor())
        self.assertEqual(evidence["subjectOutcomeKind"], "OBSERVATION")
        self.assertTrue(evidence["hasStoredPublicationRow"])
        self.assertFalse(evidence["storedLinkedAi"])
        self.assertFalse(evidence["conflicts"])

    def test_candidate_only_backlog_remains_visible_without_primary_delivery_severity(self):
        observation = healthy_observation()
        db = observation["database"]
        db["expectedDeploymentCount"] = 2
        db["deployments"].append({"id": "candidate", "release": "candidate-release", "health": "ready", "roles": ["candidate"]})
        candidate_queues = [deepcopy(row) for row in db["queues"] if row["kind"] == "reasoning"]
        for row in candidate_queues:
            row["roles"] = ["candidate"]
        candidate_queues[1].update(sampledCount=2, oldestAgeSeconds=62000)
        db["queues"].extend(candidate_queues)
        db["expectedQueueReads"] += len(candidate_queues)
        result = verify.verdicts([observation], arguments(), [], True)
        self.assertEqual(result["sampledInfrastructure"], "pass")
        self.assertEqual(result["candidateOnlyHealth"]["status"], "degraded")
        self.assertIn("aged-backlog", result["candidateOnlyHealth"]["issues"])
        candidate_queues[1]["roles"] = ["active"]
        result = verify.verdicts([observation], arguments(), [], True)
        self.assertEqual(result["sampledInfrastructure"], "degraded")

    def test_source_progress_regression_runtime_release_drift_and_skipped_polls(self):
        first, second = healthy_observation(), healthy_observation()
        first["database"]["sources"]["latestAt"] = verify.iso(NOW - timedelta(seconds=60))
        result = verify.verdicts([first, second], arguments(), [], True)
        self.assertTrue(result["sourceProgress"]["timestampAdvanced"])
        second["database"]["deployments"][0]["release"] = "other"
        second["http"][0]["identity"] = "new-process"
        result = verify.verdicts([second, first], arguments(), [60], False)
        self.assertIn("source-time-regressed", result["issues"])
        self.assertIn("deployment-release-drift", result["issues"])
        self.assertIn("runtime-or-control-drift", result["issues"])
        self.assertIn("missing-scheduled-observations", result["gaps"])

    def test_backlog_retries_expired_leases_stale_source_and_truncation(self):
        observation = healthy_observation()
        row = {"created_at": verify.iso(START), "updated_at": verify.iso(NOW), "has_error": 1, "lease": True,
               "lease_owner": "private-worker", "lease_expires_at": verify.iso(NOW - timedelta(seconds=1)),
               "heartbeat_at": verify.iso(START)}
        evidence = verify.queue_evidence({"rows": [row], "truncated": True}, "running", NOW, 300)
        self.assertEqual(evidence["expiredLeases"], 1)
        self.assertEqual(evidence["staleHeartbeats"], 1)
        observation["database"]["queues"][0] = dict(evidence, kind="reasoning")
        observation["database"]["sources"]["latestAgeSeconds"] = 600
        result = verify.verdicts([observation], arguments(), [], True)
        self.assertEqual(result["sampledInfrastructure"], "degraded")
        self.assertIn("queue-sample-incomplete", result["gaps"])
        self.assertIn("stale-source-or-clock-skew", result["issues"])

    def test_redaction_whitelist_and_per_run_hashes(self):
        redactor = verify.Redactor()
        row = linked_row()
        row.update(error="password=secret", account="private-account", symbol="SECRET_SYMBOL", text="private message")
        value = verify.lineage_evidence(row, NOW, START, redactor)
        output = json.dumps(value)
        for secret in ("private-", "password", "SECRET_SYMBOL", "private message"):
            self.assertNotIn(secret, output)
        self.assertEqual(redactor.identity("abc"), redactor.identity("abc"))
        self.assertNotEqual(redactor.identity("abc"), verify.Redactor().identity("abc"))
        self.assertNotIn("secret", json.dumps(verify.error_code(RuntimeError(1213, "secret"))))

    def test_http_is_allowlisted_bounded_and_does_not_follow_redirects(self):
        response = MagicMock(status=302)
        response.read.return_value = b"redirect body secret"
        with patch.object(verify.http.client, "HTTPConnection") as factory:
            connection = factory.return_value
            connection.getresponse.return_value = response
            self.assertEqual(verify.http_read(("127.0.0.1", 3000), verify.ENDPOINTS[0], 2), (302, None))
            connection.request.assert_called_once()
            response.read.assert_called_once_with(verify.MAX_BODY + 1)
            with self.assertRaises(ValueError):
                verify.http_read(("127.0.0.1", 3000), "/api/realtime/status", 2)
            response.read.return_value = b"x" * (verify.MAX_BODY + 1)
            with self.assertRaises(ValueError):
                verify.http_read(("127.0.0.1", 3000), verify.ENDPOINTS[0], 2)

    def test_heartbeat_only_reads_regular_bounded_file_and_whitelists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "heartbeat"
            path.write_text(json.dumps({"state": "running", "observedAt": verify.iso(NOW), "pid": 1, "token": "secret"}))
            value = verify.supervisor_evidence(path, NOW, verify.Redactor())
            self.assertEqual(value["ageSeconds"], 0)
            self.assertNotIn("secret", json.dumps(value))
            path.write_text("x" * 32769)
            with self.assertRaises(ValueError):
                verify.supervisor_evidence(path, NOW, verify.Redactor())

    def test_deadline_escapes_normal_read_handlers(self):
        with self.assertRaises(verify.ObservationDeadline):
            with verify.deadline(0.01):
                try:
                    time.sleep(0.1)
                except Exception:
                    self.fail("Deadline was swallowed by a read handler")

    def test_single_smoke_writes_restricted_report_but_never_claims_duration_or_e2e(self):
        with tempfile.TemporaryDirectory() as directory:
            args = arguments(str(Path(directory) / "report.json"))
            args.duration_seconds = 0
            with patch.object(verify, "observe", return_value=healthy_observation()), redirect_stdout(io.StringIO()):
                self.assertEqual(verify.run(args, {}), 2)
            report = json.loads(args.output.read_text())
            self.assertEqual(report["sampledSpanSeconds"], 0)
            self.assertEqual(report["observedCount"], 1)
            self.assertEqual(report["verdicts"]["liveAiLineage"]["status"], "inconclusive")
            self.assertEqual(os.stat(args.output).st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                verify.new_report(args.output)

    def test_degraded_ai_verdict_exits_one_even_when_infrastructure_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            args = arguments(str(Path(directory) / "report.json"))
            args.duration_seconds = 0
            verdict = verify.verdicts([healthy_observation()], args, [], True)
            verdict["liveAiLineage"]["status"] = "degraded"
            with patch.object(verify, "observe", return_value=healthy_observation()), \
                    patch.object(verify, "verdicts", return_value=verdict), redirect_stdout(io.StringIO()):
                self.assertEqual(verify.run(args, {}), 1)

    def test_clock_jump_skips_observation_without_catch_up_burst(self):
        clock = [100.0]
        def sleep(seconds):
            clock[0] += seconds + 5
        with tempfile.TemporaryDirectory() as directory:
            args = arguments(str(Path(directory) / "report.json"))
            with patch.object(verify.time, "monotonic", side_effect=lambda: clock[0]), \
                    patch.object(verify.time, "sleep", side_effect=sleep), \
                    patch.object(verify, "observe", return_value=healthy_observation()) as observer, redirect_stdout(io.StringIO()):
                self.assertEqual(verify.run(args, {}), 2)
            self.assertEqual(observer.call_count, 1)
            report = json.loads(args.output.read_text())
            self.assertEqual(report["missedScheduledSeconds"], [60])
            self.assertFalse(report["durationCompleted"])

    def test_interruption_finalizes_incomplete_report_and_no_more_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            args = arguments(str(Path(directory) / "report.json"))
            with patch.object(verify.time, "sleep", side_effect=KeyboardInterrupt), \
                    patch.object(verify, "observe", return_value=healthy_observation()) as observer, redirect_stdout(io.StringIO()):
                self.assertEqual(verify.run(args, {}), 2)
            self.assertEqual(observer.call_count, 1)
            report = json.loads(args.output.read_text())
            self.assertFalse(report["durationCompleted"])
            self.assertEqual(report["missedScheduledSeconds"], [60])

    def test_lineage_query_uses_survivor_sources_and_exact_subject_anchors(self):
        self.assertIn("reasoning_engine_job_sources", reads.LINEAGE)
        self.assertIn("$.reasoning_case_id", reads.LINEAGE)
        self.assertIn("i.candidate_fingerprint = s.candidate_fingerprint", reads.LINEAGE)
        self.assertNotIn("SELECT *", reads.LINEAGE)
        self.assertNotIn("FOR UPDATE", reads.LINEAGE)
        self.assertIn("JSON_TABLE(j.source_boundary_json", reads.LINEAGE)
        self.assertIn("v.generated_at = CAST(b.generated_at AS BINARY)", reads.LINEAGE)
        self.assertIn("ON s.account_id = CAST(b.account_id AS BINARY)", reads.LINEAGE)
        self.assertIn("ON v.snapshot_id = CAST(b.snapshot_id AS BINARY)", reads.LINEAGE)
        for field, comparisons in (("account_id", 4), ("snapshot_id", 3), ("generated_at", 3)):
            self.assertEqual(reads.LINEAGE.count("CAST(b." + field + " AS BINARY)"), comparisons)
        self.assertNotIn("ON CAST(v.snapshot_id", reads.LINEAGE)
        self.assertNotIn("ON CAST(s.account_id", reads.LINEAGE)
        self.assertIn("JSON_CONTAINS(e.payload_json, JSON_QUOTE(s.account_id), '$.accountIds')", reads.LINEAGE)
        self.assertEqual(reads.LINEAGE.count(reads.SOURCE_SCOPE_MATCH_SQL), 1)
        self.assertIn("COALESCE(l.account_id, '') = '' OR CAST(s.account_id AS BINARY) = CAST(l.account_id AS BINARY)", reads.SOURCE_SCOPE_MATCH_SQL)
        self.assertIn("WHEN e.event_id IS NULL OR e.payload_json IS NULL", reads.SOURCE_SCOPE_MATCH_SQL)
        self.assertEqual(reads.QUEUES["reasoning"][3], ("queued", "retry", "processing", "awaiting_source", "awaiting_world_projection", "failed"))


if __name__ == "__main__":
    unittest.main()
