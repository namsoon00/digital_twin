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


if __name__ == "__main__":
    unittest.main()
