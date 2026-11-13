"""Exact snapshot evidence, failure recovery and cooperative storage admission."""
import json
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from digital_twin.infrastructure.snapshot_payload_codec import (
    MARKER, snapshot_json_dumps, snapshot_json_loads,
)
from digital_twin.infrastructure.mysql_realtime_workload_guard import MySQLRealtimeWorkloadGuard
from digital_twin.infrastructure.transactions.monitoring import MySQLMonitorStore
from digital_twin.modules.reasoning.domain.reasoning_source_snapshot import build_reasoning_source_snapshot
from digital_twin.modules.model_registry.infrastructure.experiment_inputs import source_packet
from digital_twin.modules.outcomes.infrastructure.mysql_outcome_evidence import MySQLOutcomeEvidenceSource
from stabilization_database import StabilizationDatabaseCase
from test_reasoning_snapshot_replay import monitor_state


def evidence_state(stamp="2026-07-29T00:02:00Z"):
    state = monitor_state(stamp)
    state["externalSignals"] = {
        "companyKnowledge": {"AAPL": {"symbol": "AAPL", "description": "수치·원문 -1.2 / 관측 " * 5000}},
        "researchEvidence": {"AAPL": [{"evidenceId": "immutable", "summary": "원문 그대로 " * 10000,
                                         "observedAt": "2026-07-28T01:00:00Z"}]},
    }
    return state


class SnapshotPayloadCodecTests(TestCase):
    def test_old_new_nested_evidence_roundtrip_and_sql_leaf_preserve_all_facts(self):
        state = evidence_state()
        state["metadata"]["previousMonitorState"] = evidence_state("2026-07-29T00:01:00Z")
        original = deepcopy(state)
        packed = snapshot_json_dumps(state)
        self.assertLess(len(packed), len(json.dumps(state)) // 3)
        self.assertEqual(original, state)
        for payload in (packed, json.dumps(state)):
            self.assertEqual(state, snapshot_json_loads(payload))
        stored = json.loads(packed)
        self.assertEqual(state["positions"], stored["positions"])
        leaf = stored["externalSignals"]["companyKnowledge"]["AAPL"]
        self.assertIn(MARKER, leaf)
        self.assertEqual(state["externalSignals"]["companyKnowledge"]["AAPL"], snapshot_json_loads(json.dumps(leaf)))
        self.assertEqual(packed, snapshot_json_dumps(snapshot_json_loads(packed)))

    def test_corruption_truncation_and_size_mismatch_fail_without_empty_evidence(self):
        stored = json.loads(snapshot_json_dumps(evidence_state()))
        leaf = stored["externalSignals"]["companyKnowledge"]["AAPL"]
        for field, value in (("sha256", "0" * 64), ("bytes", 1), (MARKER, leaf[MARKER][:-8])):
            broken = deepcopy(stored)
            broken["externalSignals"]["companyKnowledge"]["AAPL"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "integrity"):
                snapshot_json_loads(json.dumps(broken), {})

    def test_maintenance_yields_to_waiting_monitor_and_reacquires_before_next_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = MySQLRealtimeWorkloadGuard(Path(directory) / "work.lock")
            demand, ran = threading.Event(), threading.Event()
            failures = []
            original = guard._acquire
            from contextlib import contextmanager
            @contextmanager
            def traced(**kwargs):
                with original(**kwargs) as lease:
                    if kwargs["role"] == "monitor-demand":
                        demand.set()
                    yield lease
            def monitor():
                try:
                    with guard.monitor_cycle():
                        ran.set()
                        with MySQLRealtimeWorkloadGuard(guard.path).maintenance_turn() as other:
                            self.assertFalse(other.acquired)
                except BaseException as error:
                    failures.append(error)
            with patch.object(guard, "_acquire", traced):
                with guard.maintenance_turn() as lease:
                    worker = threading.Thread(target=monitor, daemon=True)
                    worker.start()
                    self.assertTrue(demand.wait(2))
                    self.assertFalse(ran.is_set())
                    lease.checkpoint()  # Admit one batch so cleanup cannot starve.
                    self.assertFalse(ran.is_set())
                    lease.checkpoint()
                    self.assertTrue(ran.is_set())
                    with MySQLRealtimeWorkloadGuard(guard.path).maintenance_turn() as other:
                        self.assertFalse(other.acquired)
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual([], failures)
            with guard.maintenance_turn() as after:
                self.assertTrue(after.acquired)


class SnapshotPayloadStorageTests(StabilizationDatabaseCase):
    def test_selected_revision_fields_match_full_history_and_survive_restart_without_archive_reads(self):
        from digital_twin.infrastructure.external_api.mysql_stores import MySQLExternalDataStore
        from digital_twin.infrastructure.external_api.revision_field_cache import RevisionFieldCache
        from digital_twin.infrastructure.external_api.revision_projection_cache import RevisionProjectionCache
        from digital_twin.infrastructure.external_api.revision_field_projection import payload_field_paths
        from digital_twin.modules.portfolio.domain.valuation.historical_multiples import (
            historical_multiple_fact_projection as project, HISTORICAL_MULTIPLE_PAYLOAD_FIELDS as fields,
        )
        from test_external_fact_bounded_reads import database_row
        from test_historical_multiple_evidence import fact
        import hashlib
        subject = 'TE"ST\\X-' + self.deployment.upper()
        ids = []
        for index in range(18):
            value = fact("yfinance.fundamental", self.deployment + str(index),
                         "2026-09-15T10:00:%02dZ" % index, price=100 + index)
            value["subjectKey"] = subject
            for group, entries in value["payload"].items():
                entries[subject] = entries.pop("TEST")
            value["payload"]["companyOverviews"][subject].update({"currency": None, "earningsEstimates": []})
            value["payload"]["yfinanceData"][subject]["unusedReport"] = "原文 " * 30000
            row = database_row(value)
            row["created_at"] = row["fetched_at"]
            row["payload_hash"] = hashlib.sha256(row["payload_json"].encode()).hexdigest()
            self.sql("INSERT INTO external_fact_revision (" + ",".join(row) + ") VALUES ("
                     + ",".join(["%s"] * len(row)) + ")", tuple(row.values()))
            ids.append(row["revision_id"])
        with tempfile.TemporaryDirectory() as root:
            def store():
                result = MySQLExternalDataStore(self.settings)
                result._revision_projection_cache = RevisionProjectionCache()
                result._revision_field_cache = RevisionFieldCache(root, [self.deployment], payload_field_paths(fields))
                return result
            args = {"dataset_ids": ["yfinance.fundamental"], "subject_keys": [subject], "limit": 100,
                    "projector": project, "payload_fields": fields}
            first = store()
            raw = first.list_revisions(args["dataset_ids"], args["subject_keys"], 100)
            expected = [project(row) for row in raw]
            self.assertEqual(expected, first.list_revision_projections(**args))
            self.assertTrue(first._revision_field_cache.path.exists())
            self.assertLess(first._revision_field_cache.path.stat().st_size, 50000)
            with patch("digital_twin.infrastructure.external_api.mysql_stores.revision_page_query",
                       side_effect=AssertionError("full archive reread after restart")):
                self.assertEqual(expected, store().list_revision_projections(**args))
                self.sql("DELETE FROM external_fact_revision WHERE revision_id=%s", (ids[0],))
                self.assertEqual([row for row in expected if row["revisionId"] != ids[0]],
                                 store().list_revision_projections(**args))
            # Same ID with corrected contents must invalidate process and disk caches.
            corrected = raw[1]
            corrected["payload"]["companyOverviews"][subject]["currentPrice"] = 777
            payload = json.dumps(corrected["payload"])
            self.sql("UPDATE external_fact_revision SET payload_json=%s,payload_hash=%s WHERE revision_id=%s",
                     (payload, hashlib.sha256(payload.encode()).hexdigest(), corrected["revisionId"]))
            actual = store().list_revision_projections(**args)
            self.assertEqual(777, next(row for row in actual if row["revisionId"] == corrected["revisionId"])
                             ["payload"]["companyOverviews"][subject]["currentPrice"])
            first._revision_field_cache.path.write_text("corrupt cache")
            self.assertEqual(actual, store().list_revision_projections(**args))
            unavailable = store()
            with patch.object(unavailable, "transaction", side_effect=TimeoutError("DB unavailable")), self.assertRaises(TimeoutError):
                unavailable.list_revision_projections(**args)

    def test_revision_field_cache_is_bounded_namespaced_and_never_becomes_authority(self):
        from digital_twin.infrastructure.external_api.revision_field_cache import RevisionFieldCache
        with tempfile.TemporaryDirectory() as root:
            cache = RevisionFieldCache(root, ["database", "principal"], [("group", "field")])
            cache.MAX_BYTES = 1024
            cache.save({str(i): {"small": "value " * 10} for i in range(100)})
            self.assertLessEqual(cache.path.stat().st_size, 1024)
            self.assertTrue(cache.load())
            for namespace, paths in [(["other", "principal"], [("group", "field")]),
                                     (["database", "other"], [("group", "field")]),
                                     (["database", "principal"], [("group", "other")])]:
                self.assertEqual({}, RevisionFieldCache(root, namespace, paths).load())
            cache.path.write_text(cache.path.read_text().replace("value", "WRONG", 1))
            self.assertEqual({}, cache.load())
            cache.path.write_text("x" * 2048)
            self.assertEqual({}, cache.load())

    def test_restart_history_fingerprint_and_asof_evidence_are_exact_and_account_scoped(self):
        state = evidence_state()
        state["accountId"] = self.deployment
        store = MySQLMonitorStore(self.settings)
        store.upsert_snapshot_state(self.deployment, state)
        restarted = MySQLMonitorStore(self.settings)
        self.assertEqual(state, restarted.previous[self.deployment])
        expected = build_reasoning_source_snapshot(self.deployment, state, settings=self.settings)
        self.assertEqual(expected["payload"], restarted.reasoning_source_snapshot_state(expected["snapshotId"]))
        self.assertEqual({}, restarted.reasoning_snapshot_state_at("other", state["generatedAt"]))
        row = self.sql("SELECT * FROM verified_reasoning_source_snapshots WHERE snapshot_id=%s", (expected["snapshotId"],))
        self.assertEqual(expected["fingerprint"], source_packet(row)["fingerprint"])
        # Make this historical fixture known at its source time, not today's test time.
        self.sql("UPDATE monitor_snapshot_history SET created_at=generated_at WHERE account_id=%s", (self.deployment,))
        request = {"requestId": "asof", "symbol": "AAPL", "observedAt": state["generatedAt"]}
        evidence = MySQLOutcomeEvidenceSource(self.settings).load_outcome_evidence(self.deployment, [request])
        historical = restarted.reasoning_snapshot_state_at(self.deployment, state["generatedAt"])
        self.assertEqual(historical["externalSignals"]["companyKnowledge"]["AAPL"], evidence["asof"]["companyContext"])
        self.assertEqual({}, MySQLOutcomeEvidenceSource(self.settings).load_outcome_evidence("other", [request]))
        # Row eviction must not orphan encoded evidence or change replay hashes.
        from digital_twin.infrastructure.mysql_history_archive import archive_delete_row
        from digital_twin.infrastructure.history_archive import LocalHistoryArchive
        with tempfile.TemporaryDirectory() as root:
            archive = LocalHistoryArchive(Path(root), minimum_free_bytes=0)
            with store.connect() as connection:
                self.assertEqual(1, archive_delete_row(connection, "verified_reasoning_source_snapshots",
                    {"snapshot_id": expected["snapshotId"]}, "1=1", archive=archive))
            with patch("digital_twin.infrastructure.mysql_history_archive.archive_for_connection", return_value=archive):
                self.assertEqual(expected["payload"], restarted.reasoning_source_snapshot_state(expected["snapshotId"]))

    def test_v2_removes_only_own_legacy_copy_and_rollback_keeps_previous_source(self):
        state = evidence_state()
        state["accountId"] = self.deployment
        store = MySQLMonitorStore(self.settings)
        with store.transaction() as connection:
            for account in (self.deployment, "other-" + self.deployment):
                store.upsert_reasoning_snapshot_inputs_with_connection(connection, account, state)
        def count(account):
            return self.sql("SELECT COUNT(*) AS n FROM monitor_snapshot_reasoning_inputs WHERE account_id=%s", (account,))["n"]
        before = count(self.deployment)
        self.assertGreater(before, 0)
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with store.transaction() as connection:
                store.upsert_snapshot_state_with_connection(connection, self.deployment, state)
                raise RuntimeError("rollback")
        self.assertEqual(before, count(self.deployment))
        self.assertEqual({}, store.reasoning_source_snapshot_state(build_reasoning_source_snapshot(self.deployment, state)["snapshotId"]))
        store.upsert_snapshot_state(self.deployment, state)
        self.assertEqual(0, count(self.deployment))
        self.assertEqual(before, count("other-" + self.deployment))
        original = store.reasoning_source_snapshot_state(build_reasoning_source_snapshot(self.deployment, state)["snapshotId"])
        changed = deepcopy(state)
        changed["generatedAt"] = "2026-07-29T00:03:00Z"
        changed["positions"]["AAPL"]["current_price"] = 91
        store.upsert_snapshot_state(self.deployment, changed, previous_state=state)
        self.assertEqual(original, store.reasoning_source_snapshot_state(build_reasoning_source_snapshot(self.deployment, state)["snapshotId"]))
