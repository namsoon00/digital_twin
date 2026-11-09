import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError
from types import SimpleNamespace

from digital_twin.infrastructure.stale_read_model import StaleReadModelCache
from digital_twin.infrastructure.web.adapters import calendar, console
from digital_twin.infrastructure.external_api.adapters.official_release import OfficialReleaseAdapter
from digital_twin.infrastructure.external_api.adapters.krx import KrxMarketIndexAdapter
from digital_twin.modules.market_data.contracts import ExternalCallDeferred


class OperationalReadLatencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = StaleReadModelCache("calendar", root=Path(self.temporary.name))

    def test_calendar_cold_read_and_refresh_never_wait_for_slow_database(self):
        entered, release = threading.Event(), threading.Event()
        def load(_query):
            entered.set(); release.wait(2)
            return {"events": [{"eventId": "one"}], "summary": {"total": 1}}
        service = MagicMock()
        service.list_events.side_effect = load
        with patch.object(calendar, "CALENDAR_READ_MODEL", self.cache), \
             patch.object(calendar, "investment_calendar_read_service", return_value=service):
            try:
                started = time.monotonic()
                first = calendar.investment_calendar_payload({})
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertEqual(first["status"], "warming")
                self.assertEqual(first["events"], [])
                self.assertIsNone(first["summary"]["total"])
                self.assertTrue(entered.wait(1))
                for _ in range(4):
                    calendar.investment_calendar_payload({"refresh": ["true"]})
                self.assertEqual(service.list_events.call_count, 1)
            finally:
                release.set()
                self.wait_finished()
            result = calendar.investment_calendar_payload({})
            self.assertEqual(result["events"][0]["eventId"], "one")
            self.assertFalse(result["readCache"]["refreshing"])

    def wait_finished(self):
        end = time.monotonic() + 2
        while self.cache._inflight and time.monotonic() < end:
            time.sleep(0.005)
        self.assertFalse(self.cache._inflight)

    def test_calendar_keys_keep_accounts_date_ranges_and_filters_separate(self):
        cache = MagicMock()
        cache.get_or_refresh.return_value = {"refreshing": True}
        with patch.object(calendar, "CALENDAR_READ_MODEL", cache):
            queries = [{"accountId": ["one"], "symbol": ["EXAMPLE"]},
                       {"accountId": ["two"], "symbol": ["EXAMPLE"]},
                       {"accountId": ["one"], "symbol": ["OTHER"]},
                       {"accountId": ["one"], "symbol": ["EXAMPLE"], "from": ["2026-01-01"]}]
            for query in queries:
                calendar.investment_calendar_payload(query)
        keys = [call.args[0] for call in cache.get_or_refresh.call_args_list]
        self.assertEqual(len(set(keys)), 4)
        self.assertEqual(json.loads(keys[0])["accountId"], "one")

    def test_edit_discards_old_inflight_response_before_a_new_read(self):
        entered, release = threading.Event(), threading.Event()
        def stale_load():
            entered.set(); release.wait(2)
            return {"events": [{"eventId": "deleted"}]}
        self.cache.refresh_async("one", stale_load)
        self.assertTrue(entered.wait(1))
        try:
            service = MagicMock()
            service.delete_event.return_value = {"deleted": True}
            with patch.object(calendar, "CALENDAR_READ_MODEL", self.cache), \
                 patch.object(calendar, "investment_calendar_service", return_value=service):
                self.assertEqual(calendar.delete_investment_calendar_event_payload("deleted"), {"deleted": True})
        finally:
            release.set(); self.wait_finished()
        self.assertFalse(self.cache.snapshot("one")["hasData"])
        self.cache.refresh("one", lambda: {"events": [{"eventId": "replacement"}]})
        self.assertEqual(self.cache.snapshot("one")["payload"]["events"][0]["eventId"], "replacement")

    def test_failed_refresh_keeps_old_rows_explicitly_stale(self):
        self.cache.store_success("one", {"events": [{"eventId": "old"}]})
        def failed():
            raise TimeoutError("read unavailable")
        self.cache.refresh("one", failed)
        result = self.cache.snapshot("one")
        self.assertTrue(result["hasData"])
        self.assertIn("read unavailable", result["lastError"])

    def test_dashboard_cold_read_uses_nonblocking_cache(self):
        cache = MagicMock()
        cache.get_or_refresh.return_value = {"refreshing": True}
        with patch.object(console, "DASHBOARD_READ_MODEL", cache), \
             patch.object(console, "_console_dashboard_source_payload") as source:
            result = console.console_dashboard_api_payload({})
        self.assertEqual(result["status"], "warming")
        self.assertEqual(result["version"], "console-read-model-v2")
        self.assertEqual(result["tasks"], [])
        self.assertIsNone(result["taskSummary"]["decisionCount"])
        self.assertFalse(cache.get_or_refresh.call_args.kwargs["blocking_first_load"])
        source.assert_not_called()

    def test_background_dashboard_reads_calendar_rows_despite_a_moving_from_clock(self):
        service = MagicMock()
        service.list_events.return_value = {"events": [{"eventId": "upcoming"}], "summary": {"total": 1}}
        model = MagicMock()
        model.dashboard_summary.side_effect = lambda _s, _l, _c, events: {"upcomingEvents": events["events"]}
        with patch.object(console, "operational_read_settings", return_value={}), \
                patch.object(console.stores, "investment_domain_store"), \
                patch.object(console, "flow_lens_read_payload", return_value={}), \
                patch.object(console, "investment_case_api_payload", return_value={}), \
                patch.object(console, "console_read_model_service", return_value=model), \
                patch.object(calendar, "investment_calendar_read_service", return_value=service), \
                patch.object(calendar, "CALENDAR_READ_MODEL") as nested_cache:
            first = console._console_dashboard_source_payload({})
            second = console._console_dashboard_source_payload({})
        self.assertEqual(first["upcomingEvents"], [{"eventId": "upcoming"}])
        self.assertEqual(second["upcomingEvents"], first["upcomingEvents"])
        self.assertEqual(first["componentFreshness"]["calendar"]["status"], "ok")
        self.assertEqual(service.list_events.call_count, 2)
        queries = [call.args[0] for call in service.list_events.call_args_list]
        self.assertNotEqual(queries[0]["from"], queries[1]["from"])
        nested_cache.get_or_refresh.assert_not_called()


class CollectionTurnBudgetTests(unittest.TestCase):
    def test_bls_access_denial_is_shared_by_partitions_and_expires(self):
        clock = [datetime(2026, 11, 9, tzinfo=timezone.utc)]
        fetcher = MagicMock(side_effect=HTTPError("https://www.bls.gov/", 403, "denied", {}, None))
        adapter = OfficialReleaseAdapter("bls", fetch_text=fetcher, now=lambda: clock[0])
        for partition in ("cpi", "employment"):
            with self.assertRaises(ExternalCallDeferred) as failure:
                adapter.fetch(SimpleNamespace(partition_key=partition), {})
            self.assertEqual(failure.exception.reason, "access-denied")
        self.assertEqual(fetcher.call_count, 1)
        clock[0] += timedelta(hours=6, seconds=1)
        with self.assertRaises(ExternalCallDeferred):
            adapter.fetch(SimpleNamespace(partition_key="employment"), {})
        self.assertEqual(fetcher.call_count, 2)

    def test_krx_slow_lookback_defers_without_publishing_an_empty_observation(self):
        adapter = KrxMarketIndexAdapter(json_fetcher=MagicMock(return_value={}))
        with patch("digital_twin.infrastructure.external_api.adapters.krx.time.monotonic",
                   side_effect=[0, 0, 31]):
            with self.assertRaises(ExternalCallDeferred) as failure:
                adapter.fetch(None, {"krxOpenApiKey": "test"})
        self.assertEqual(failure.exception.reason, "source-query-budget")
        self.assertEqual(adapter.json_fetcher.call_count, 1)
