import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from digital_twin.infrastructure import web_server


class SymbolUniverseRefreshJobTests(unittest.TestCase):
    def setUp(self):
        with web_server.SYMBOL_UNIVERSE_REFRESH_LOCK:
            web_server.SYMBOL_UNIVERSE_REFRESH_STATE.update({
                "jobId": "",
                "running": False,
                "status": "idle",
                "markets": set(),
                "pendingMarkets": set(),
                "completedMarkets": set(),
                "results": [],
                "summary": {},
                "requestedAt": "",
                "startedAt": "",
                "finishedAt": "",
                "lastError": "",
                "stage": "idle",
                "currentMarket": "",
                "stageItemCount": 0,
                "updatedAt": "",
            })

    def wait_for_terminal_status(self, job_id, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = web_server.symbol_universe_refresh_status(job_id)
            if status["status"] in {"completed", "partial", "failed"}:
                return status
            time.sleep(0.01)
        self.fail("종목 유니버스 갱신 작업이 제한 시간 안에 끝나지 않았습니다.")

    @mock.patch.object(web_server, "new_domain_event")
    @mock.patch.object(web_server, "symbol_universe_service")
    def test_refresh_request_returns_immediately_and_coalesces_markets(self, service_factory, publish):
        release = threading.Event()
        started = threading.Event()
        calls = []

        def refresh(markets, on_progress=None):
            market = markets[0]
            calls.append(market)
            if on_progress:
                on_progress({"market": market, "stage": "fetching", "count": 0})
            started.set()
            release.wait(1.0)
            if on_progress:
                on_progress({"market": market, "stage": "saving", "count": 10})
            return {
                "results": [{"market": market, "status": "ok", "count": 10}],
                "summary": {"total": len(calls) * 10},
            }

        service_factory.return_value = SimpleNamespace(refresh=refresh)
        self.addCleanup(release.set)

        before = time.monotonic()
        first = web_server.request_symbol_universe_refresh({"markets": ["KOSPI"]})
        elapsed = time.monotonic() - before

        self.assertLess(elapsed, 0.5)
        self.assertTrue(first["accepted"])
        self.assertFalse(first["coalesced"])
        self.assertTrue(first["running"])
        self.assertTrue(started.wait(0.5))
        running = web_server.symbol_universe_refresh_status(first["jobId"])
        self.assertEqual("fetching", running["stage"])
        self.assertEqual("KOSPI", running["currentMarket"])
        self.assertGreater(running["progressPercent"], 0)

        second = web_server.request_symbol_universe_refresh({"markets": ["NASDAQ"]})
        self.assertTrue(second["coalesced"])
        self.assertEqual(first["jobId"], second["jobId"])
        release.set()

        status = self.wait_for_terminal_status(first["jobId"])
        self.assertEqual("completed", status["status"])
        self.assertEqual(["KOSPI", "NASDAQ"], status["completedMarkets"])
        self.assertEqual(2, status["completedCount"])
        self.assertEqual(2, status["totalCount"])
        self.assertEqual(100, status["progressPercent"])
        self.assertEqual(["KOSPI", "NASDAQ"], calls)
        self.assertIn(web_server.SYMBOL_UNIVERSE_REFRESH_REQUESTED, [call.args[0] for call in publish.call_args_list])
        self.assertIn(web_server.SYMBOL_UNIVERSE_REFRESHED, [call.args[0] for call in publish.call_args_list])

    @mock.patch.object(web_server, "new_domain_event")
    @mock.patch.object(web_server, "symbol_universe_service")
    def test_refresh_failure_is_terminal_and_keeps_error_for_status_polling(self, service_factory, publish):
        service_factory.return_value = SimpleNamespace(refresh=lambda markets, on_progress=None: {
            "results": [{"market": markets[0], "status": "error", "count": 0, "error": "source timeout"}],
            "summary": {"total": 0},
        })

        accepted = web_server.request_symbol_universe_refresh({"markets": ["KOSDAQ"]})
        status = self.wait_for_terminal_status(accepted["jobId"])

        self.assertEqual("failed", status["status"])
        self.assertFalse(status["running"])
        self.assertEqual("source timeout", status["lastError"])
        self.assertEqual(100, status["progressPercent"])
        self.assertIn(web_server.SYMBOL_UNIVERSE_REFRESH_FAILED, [call.args[0] for call in publish.call_args_list])

    def test_status_poll_moves_an_older_client_to_the_latest_job(self):
        with web_server.SYMBOL_UNIVERSE_REFRESH_LOCK:
            web_server.SYMBOL_UNIVERSE_REFRESH_STATE.update({
                "jobId": "symbol-refresh-latest",
                "running": True,
                "status": "running",
                "markets": {"NASDAQ"},
                "pendingMarkets": set(),
                "completedMarkets": set(),
            })

        status = web_server.symbol_universe_refresh_status("symbol-refresh-older")

        self.assertEqual("symbol-refresh-latest", status["jobId"])
        self.assertEqual("symbol-refresh-older", status["requestedJobId"])
        self.assertTrue(status["superseded"])
        self.assertTrue(status["running"])


if __name__ == "__main__":
    unittest.main()
