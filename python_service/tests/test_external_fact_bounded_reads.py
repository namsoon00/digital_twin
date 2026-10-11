"""Regression coverage for realtime external-fact reads under accumulated history."""

from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from digital_twin.infrastructure.external_api.mysql_stores import MySQLExternalDataStore
from digital_twin.infrastructure.external_api.revision_projection_cache import RevisionProjectionCache
from digital_twin.infrastructure.mysql_operational_connection import attach_mysql_error_context, mysql_statement_label
from digital_twin.modules.portfolio.application.valuation_evidence_service import HistoricalMultipleEvidenceService
from digital_twin.modules.portfolio.domain.valuation.historical_multiples import (
    build_historical_forward_multiple_observations,
    historical_multiple_fact_projection,
)
from test_historical_multiple_evidence import fact


def database_row(value):
    return {"revision_id": value["revisionId"], "dataset_id": value["datasetId"],
            "subject_key": value["subjectKey"], "fetched_at": value["fetchedAt"],
            "provider_id": value["providerId"], "source_revision": value["sourceRevision"],
            "payload_hash": value["payloadHash"], "source_schema_version": value["sourceSchemaVersion"],
            "source_as_of": value["sourceAsOf"], "availability": value["availability"],
            "payload_json": json.dumps(value["payload"]), "quality_json": "{}"}


class MemoryReadStore(MySQLExternalDataStore):
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.mysql_config = {"database": "fixture", "user": "fixture"}
        self._revision_projection_cache = RevisionProjectionCache()
        self.calls = []
        self.fail_operation = ""
        self.after_keys = None
        self.omit_page_row = False
        self.fail_revision = ""

    @contextmanager
    def transaction(self):
        snapshot = deepcopy(self.rows)

        def execute(sql, params=()):
            self.calls.append((sql, params))
            if self.fail_operation and self.fail_operation in sql:
                raise TimeoutError("isolated database read timeout")
            rows = []
            if "operation:external-revision-ids" in sql:
                rows = [{"revision_id": row["revision_id"], "fetched_at": row["fetched_at"]}
                        for row in snapshot[:params[-1]]]
            elif "operation:external-revision-page" in sql:
                if self.fail_revision in params:
                    raise TimeoutError("isolated later page timeout")
                rows = [row for row in reversed(snapshot) if row["revision_id"] in params]
            elif "operation:external-current-keys" in sql:
                rows = [{"dataset_id": row["dataset_id"], "subject_key": row["subject_key"]} for row in snapshot]
                if self.after_keys:
                    self.after_keys()
            elif "operation:external-current-page" in sql:
                selected = set(zip(params[::2], params[1::2]))
                rows = [row for row in snapshot if (row["dataset_id"], row["subject_key"]) in selected]
            if self.omit_page_row and "-page */" in sql:
                rows = rows[:-1]
            return SimpleNamespace(fetchall=lambda: deepcopy(rows))

        yield SimpleNamespace(execute=execute)

    def read_projections(self, **kwargs):
        return self.list_revision_projections(dataset_ids=["yfinance.fundamental"], subject_keys=["TEST"],
            limit=1000, projector=historical_multiple_fact_projection, **kwargs)


class ExternalFactBoundedReadsTests(unittest.TestCase):
    def revisions(self, count=35):
        return [database_row(fact("yfinance.fundamental", "p" + str(i),
                    "2026-09-15T10:00:00Z", price=100 + i)) for i in range(count)]

    def test_warm_read_rechecks_membership_but_does_not_reload_provider_bodies(self):
        store = MemoryReadStore(self.revisions())
        first = store.read_projections()
        pages = [params for sql, params in store.calls if "external-revision-page" in sql]
        self.assertEqual([16, 16, 3], [len(page) for page in pages])
        self.assertEqual([row["revision_id"] for row in store.rows], [row["revisionId"] for row in first])
        first[0]["payload"]["companyOverviews"]["TEST"]["currentPrice"] = -1
        store.calls.clear()
        second = store.read_projections()
        self.assertEqual(100, second[0]["payload"]["companyOverviews"]["TEST"]["currentPrice"])
        self.assertTrue(any("external-revision-ids" in sql for sql, _ in store.calls))
        self.assertFalse(any("external-revision-page" in sql for sql, _ in store.calls))
        # Retention and newly inserted immutable revisions are visible next time.
        store.rows = store.rows[1:] + [database_row(fact("yfinance.fundamental", "new", "2026-09-16", price=200))]
        store.calls.clear()
        third = store.read_projections()
        self.assertNotIn("p0", [row["revisionId"] for row in third])
        self.assertEqual("new", third[-1]["revisionId"])
        self.assertEqual([("new",)], [params for sql, params in store.calls if "external-revision-page" in sql])

    def test_cached_evidence_cannot_hide_database_failure_or_a_missing_page(self):
        store = MemoryReadStore(self.revisions(2))
        store.read_projections()
        store.fail_operation = "external-revision-ids"
        with self.assertRaises(TimeoutError):
            store.read_projections()
        cold = MemoryReadStore(self.revisions(2))
        cold.fail_operation = "external-revision-page"
        with self.assertRaises(TimeoutError):
            cold.read_projections()
        cold.fail_operation = ""
        cold.omit_page_row = True
        with self.assertRaisesRegex(RuntimeError, "read snapshot"):
            cold.read_projections()

    def test_current_pages_share_one_snapshot_and_preserve_retention_and_freshness_metadata(self):
        rows = [{**row, "subject_key": str(i).zfill(6), "revision_available": i % 2,
                 "expires_at": "2099-01-01T00:00:00Z", "updated_at": "2026-09-15T10:00:00Z"}
                for i, row in enumerate(self.revisions())]
        store = MemoryReadStore(rows)
        store.after_keys = lambda: store.rows.clear()
        actual = store.list_current()
        self.assertEqual([store._fact_row(row) for row in rows], actual)
        pages = [params for sql, params in store.calls if "external-current-page" in sql]
        self.assertEqual([32, 32, 6], [len(page) for page in pages])
        self.assertIn(("START TRANSACTION READ ONLY", ()), store.calls)
        cold = MemoryReadStore(rows)
        cold.omit_page_row = True
        with self.assertRaisesRegex(RuntimeError, "read snapshot"):
            cold.list_current()
        cold.omit_page_row = False
        cold.fail_operation = "external-current-page"
        with self.assertRaises(TimeoutError):
            cold.list_current()

    def test_later_page_failure_never_returns_partial_history_and_next_cycle_can_finish(self):
        store = MemoryReadStore(self.revisions())
        store.fail_revision = "p20"
        with self.assertRaises(TimeoutError):
            store.read_projections()
        store.fail_revision = ""
        store.calls.clear()
        rows = store.read_projections()
        self.assertEqual(35, len(rows))
        self.assertEqual([16, 3], [len(params) for sql, params in store.calls if "external-revision-page" in sql])

    def test_distinct_projection_contract_cannot_reuse_another_consumers_cached_value(self):
        store = MemoryReadStore(self.revisions(1))
        store.read_projections()
        other = store.list_revision_projections(dataset_ids=["yfinance.fundamental"], subject_keys=["TEST"],
            limit=100, projector=lambda row: {"differentContract": row["revisionId"]})
        self.assertEqual([{"differentContract": "p0"}], other)

    def test_reconstructed_stores_reuse_only_the_same_database_principal_and_revision_clock(self):
        shared = RevisionProjectionCache()
        with patch("digital_twin.infrastructure.external_api.mysql_stores.process_revision_projection_cache", return_value=shared):
            first = MemoryReadStore(self.revisions(1))
            del first._revision_projection_cache
            expected = first.read_projections()
            for changes, hit in (({}, True), ({"database": "other"}, False), ({"user": "other"}, False)):
                recreated = MemoryReadStore(self.revisions(1))
                del recreated._revision_projection_cache
                recreated.mysql_config.update(changes)
                self.assertEqual(expected, recreated.read_projections())
                self.assertEqual(hit, not any("external-revision-page" in sql for sql, _ in recreated.calls))
            recreated = MemoryReadStore(self.revisions(1))
            del recreated._revision_projection_cache
            recreated.rows[0]["fetched_at"] = "2026-09-16T10:00:00Z"
            changed = recreated.read_projections()
            self.assertEqual("2026-09-16T10:00:00Z", changed[0]["fetchedAt"])
            self.assertTrue(any("external-revision-page" in sql for sql, _ in recreated.calls))

    def test_projection_preserves_point_in_time_join_and_all_source_references(self):
        analysts = [fact("yfinance.analyst", "a1", "2026-09-01T09:00:00Z", eps=10),
                    fact("yfinance.analyst", "a2", "2026-09-08T09:00:00Z", eps=12),
                    fact("yfinance.analyst", "future", "2026-09-16T09:00:00Z", eps=50)]
        prices = [fact("yfinance.fundamental", "p1", "2026-09-01T10:00:00Z", price=100),
                  fact("yfinance.fundamental", "p2", "2026-09-08T10:00:00Z", price=120),
                  fact("yfinance.fundamental", "p3", "2026-09-15T10:00:00Z", price=140)]
        for row in analysts + prices:
            row["payload"]["yfinanceData"]["TEST"]["unusedFinancialStatements"] = "x" * 200000
        before = deepcopy(analysts + prices)
        expected = build_historical_forward_multiple_observations("TEST", prices, analysts)
        projected_prices = [historical_multiple_fact_projection(row) for row in prices]
        projected_analysts = [historical_multiple_fact_projection(row) for row in analysts]
        self.assertEqual(expected, build_historical_forward_multiple_observations("TEST", projected_prices, projected_analysts))
        self.assertEqual(3, len(expected))
        self.assertNotIn("future", repr(expected))
        self.assertLess(len(json.dumps(projected_prices + projected_analysts)), 10000)
        self.assertEqual(before, analysts + prices)

    def test_application_uses_projected_reader_and_does_not_fallback_on_failure(self):
        class Store:
            def list_revisions(self, **kwargs):
                raise AssertionError("full provider history must not be read")

            def list_revision_projections(self, **kwargs):
                raise TimeoutError("read failed")

        with self.assertRaises(TimeoutError):
            HistoricalMultipleEvidenceService(Store()).enrich({}, ["TEST"])
        projected_only = SimpleNamespace(list_revision_projections=lambda **kwargs: [])
        result = HistoricalMultipleEvidenceService(projected_only).enrich({}, ["TEST"])
        self.assertEqual("insufficient-history", result["valuationEvidenceFeeds"]["TEST"]["status"])

    def test_cache_budget_evicts_without_truncating_oversized_results(self):
        cache = RevisionProjectionCache(max_bytes=50, max_entries=2)
        value = {"value": "한글"}
        cache.put("a", value)
        cache.put("b", value)
        self.assertEqual(value, cache.get("a"))
        cache.put("c", value)
        self.assertIsNone(cache.get("b"))
        cache.put("huge", {"body": "x" * 1000})
        self.assertIsNone(cache.get("huge"))
        self.assertLessEqual(cache.byte_count, 50)
        self.assertEqual(value, cache.get("a"))

    def test_projection_exceeding_cache_budget_is_returned_intact(self):
        store = MemoryReadStore(self.revisions(1))
        store._revision_projection_cache = RevisionProjectionCache(max_bytes=50)
        value = {"evidence": "x" * 1000}
        rows = store.list_revision_projections(dataset_ids=["yfinance.fundamental"], subject_keys=["TEST"],
            limit=100, projector=lambda row: value)
        self.assertEqual([value], rows)
        self.assertEqual(0, store._revision_projection_cache.byte_count)

    def test_failure_labels_distinguish_current_and_revision_pages_without_parameters(self):
        sql = "SELECT /* operation:external-current-page */ fact.* FROM external_fact_current fact WHERE subject_key=%s"
        label = mysql_statement_label(sql)
        self.assertEqual("SELECT external_fact_current [external-current-page]", label)
        error = TimeoutError("read timed out")
        attach_mysql_error_context(error, sql, 0)
        self.assertEqual(label, error.orbit_mysql_statement)
        self.assertNotIn("subject_key", error.orbit_mysql_statement)
        self.assertEqual("SELECT external_fact_revision", mysql_statement_label("SELECT * FROM external_fact_revision"))


class ExternalCollectorResilienceTests(unittest.TestCase):
    def service(self, store=None):
        from datetime import datetime, timezone
        from digital_twin.modules.market_data.application.external_data.collection_service import ExternalDataCollectionService
        from digital_twin.modules.market_data.application.external_data.registry import ExternalDatasetRegistry
        from test_external_data_platform import StaticAdapter, MemoryCollectionStore
        self.clock = datetime(2026, 10, 11, tzinfo=timezone.utc)
        self.store = store or MemoryCollectionStore()
        return ExternalDataCollectionService({}, ExternalDatasetRegistry([StaticAdapter()]), self.store,
                                             now_provider=lambda: self.clock)

    def test_worker_omits_heavy_summary_on_active_and_idle_cycles_but_explicit_reads_remain(self):
        from unittest.mock import Mock
        service = self.service()
        self.store.summary = Mock(return_value={"facts": {"count": 1}})
        active = service.run_once(include_summary=False)
        idle = service.run_once(include_summary=False)
        self.assertEqual(1, active["successCount"])
        self.assertEqual("idle", idle["status"])
        self.assertNotIn("summary", active)
        self.assertNotIn("summary", idle)
        self.store.summary.assert_not_called()
        self.assertEqual({"facts": {"count": 1}}, service.run_once()["summary"])

    def test_retention_failure_preserves_collection_and_retries_after_cooldown_not_six_hours(self):
        from datetime import timedelta
        from unittest.mock import Mock
        service = self.service()
        failure = RuntimeError(2013, "read timeout")
        failure.orbit_mysql_statement = "DELETE external_collection_runs [external-retention-runs]"
        failure.orbit_mysql_elapsed_ms = 30000
        self.store.cleanup_history = Mock(side_effect=[failure, {"status": "ok", "deletedRuns": 100},
                                                      {"status": "ok", "deletedRuns": 0}])
        self.assertEqual("deferred", service.run_once(include_summary=False)["historyCleanup"]["status"])
        self.store.cleanup_history.assert_not_called()
        self.clock += timedelta(seconds=60)
        service._last_partition_sync_at = None
        result = service.run_once(include_summary=False)
        self.assertEqual(1, result["successCount"])
        self.assertEqual("partial", result["status"])
        self.assertIsNone(service._last_cleanup_at)
        self.assertEqual("history-cleanup", result["historyCleanup"]["diagnostics"]["stage"])
        self.assertEqual(30000, result["historyCleanup"]["diagnostics"]["mysqlElapsedMs"])
        self.assertEqual("deferred", service.cleanup_history_if_due()["status"])
        self.clock += timedelta(seconds=60)
        self.assertTrue(service.cleanup_history_if_due()["backlogPossible"])
        self.clock += timedelta(seconds=60)
        self.assertFalse(service.cleanup_history_if_due()["backlogPossible"])
        self.assertEqual(3, self.store.cleanup_history.call_count)
        self.clock += timedelta(seconds=60)
        self.assertEqual("deferred", service.cleanup_history_if_due()["status"])
        self.assertEqual(3, self.store.cleanup_history.call_count)

    def test_uncertain_saved_observation_is_not_replayed_or_marked_as_provider_failure(self):
        from unittest.mock import Mock
        service = self.service()
        original = self.store.complete_observation
        failure = RuntimeError(2013, "commit acknowledgement lost")
        def commit_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise failure
        self.store.complete_observation = Mock(side_effect=commit_then_fail)
        self.store.fail_job = Mock(side_effect=AssertionError("must not undo durable completion"))
        result = service.run_once(include_summary=False)
        self.assertEqual(1, result["failureCount"])
        self.assertEqual(1, len(self.store.completed))
        self.assertEqual("store.complete-observation", result["results"][0]["diagnostics"]["stage"])
        self.store.fail_job.assert_not_called()
        self.assertEqual("idle", service.run_once(include_summary=False)["status"])
        self.store.complete_observation.assert_called_once()

    def test_claim_failure_retains_original_error_and_safe_stage(self):
        from unittest.mock import Mock
        service = self.service()
        failure = RuntimeError(2013, "read timeout")
        self.store.claim_due = Mock(side_effect=failure)
        with self.assertRaises(RuntimeError) as caught:
            service.run_once(include_summary=False)
        self.assertIs(failure, caught.exception)
        self.assertEqual("claim-due", failure.orbit_collection_stage)

    def test_legacy_check_retries_after_failure_without_loading_full_summary(self):
        from unittest.mock import Mock
        from digital_twin.infrastructure.external_api.legacy_import import LegacyExternalSignalImporter
        store = SimpleNamespace(current_fact_count=Mock(side_effect=[RuntimeError(2013, "timeout"), 5]),
                                summary=Mock(side_effect=AssertionError("heavy query forbidden")))
        importer = LegacyExternalSignalImporter(None, store, None, {})
        with self.assertRaises(RuntimeError):
            importer.import_if_empty()
        self.assertFalse(importer._checked)
        self.assertEqual("current facts already exist", importer.import_if_empty()["reason"])
        self.assertEqual("migration already checked", importer.import_if_empty()["reason"])
        self.assertEqual(2, store.current_fact_count.call_count)
        store.summary.assert_not_called()

    def test_only_safe_count_read_retries_once_with_a_new_connection(self):
        from unittest.mock import Mock
        store = object.__new__(MySQLExternalDataStore)
        connections = []
        failures = [RuntimeError(2013, "lost response"), None]
        @contextmanager
        def connect():
            connections.append(object())
            failure = failures.pop(0)
            def execute(sql):
                self.assertIn("COUNT(*)", sql)
                self.assertNotIn("payload_json", sql)
                if failure:
                    raise failure
                return SimpleNamespace(fetchone=lambda: {"fact_count": 12})
            yield SimpleNamespace(execute=execute)
        store.connect = connect
        with patch("digital_twin.infrastructure.external_api.mysql_stores.time.sleep") as sleep:
            self.assertEqual(12, store.current_fact_count())
            self.assertEqual(2, len(connections))
            sleep.assert_called_once_with(0.25)
            failures[:] = [RuntimeError(2013, "first"), RuntimeError(2013, "second")]
            with self.assertRaisesRegex(RuntimeError, "second"):
                store.current_fact_count()
            self.assertEqual(4, len(connections))
            failures[:] = [RuntimeError(1064, "bad SQL")]
            with self.assertRaisesRegex(RuntimeError, "bad SQL"):
                store.current_fact_count()
            self.assertEqual(5, len(connections))

    def test_cleanup_caps_batch_and_preserves_retention_cutoffs(self):
        from datetime import datetime, timezone
        store = object.__new__(MySQLExternalDataStore)
        calls = []
        @contextmanager
        def transaction():
            def execute(sql, args):
                calls.append((sql, args))
                return SimpleNamespace(rowcount=3)
            yield SimpleNamespace(execute=execute)
        store.transaction = transaction
        result = store.cleanup_history(batch_size=10000, now=datetime(2026, 10, 11, tzinfo=timezone.utc))
        self.assertEqual([250, 250], [args[1] for sql, args in calls])
        self.assertEqual("2026-09-11T00:00:00Z", result["runCutoff"])
        self.assertEqual("2025-10-11T00:00:00Z", result["revisionCutoff"])
        self.assertEqual(3, result["deletedRevisions"])

    def test_scheduler_skips_summary_and_reports_stage_query_and_maintenance_failure(self):
        from unittest.mock import Mock
        from digital_twin.infrastructure.schedulers import ExternalDataCollectionScheduler
        runner = SimpleNamespace()
        scheduler = ExternalDataCollectionScheduler(runner, 15, error_reporter=object())
        def run_once(**kwargs):
            self.assertEqual({"include_summary": False}, kwargs)
            scheduler.stop()
            return {"status": "partial", "processedCount": 1, "historyCleanup": {
                "status": "error", "error": "read timeout", "diagnostics": {
                    "stage": "history-cleanup", "mysqlStatement": "DELETE external_fact_revision",
                    "mysqlElapsedMs": 30000, "errorType": "OperationalError"}}}
        runner.run_once = run_once
        with patch("digital_twin.infrastructure.schedulers.install_stop_handlers"), \
             patch("digital_twin.infrastructure.schedulers.wait_until_running"), \
             patch("digital_twin.infrastructure.schedulers.report_runtime_error") as report, \
             patch("builtins.print"):
            scheduler.run_forever()
        stage = report.call_args[0][3]
        self.assertIn("history-cleanup", stage)
        self.assertIn("mysql=DELETE external_fact_revision", stage)
        self.assertIn("query=30.0s", stage)
        self.assertIn("cause=OperationalError", stage)
        failure = RuntimeError(2013, "timeout")
        failure.orbit_collection_stage = "claim-due"
        failure.orbit_mysql_statement = "SELECT external_dataset_state"
        self.assertIn("claim-due", scheduler.error_stage(failure))


if __name__ == "__main__":
    unittest.main()
