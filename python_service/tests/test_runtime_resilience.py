import json
import sys
import unittest
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.external_signal_quality import evaluate_external_signal_quality
from digital_twin.domain.portfolio import Position
from digital_twin.infrastructure.external_signal_utils import ExternalApiGuard, ExternalRateLimited
from digital_twin.infrastructure.external_signals import ExternalSignalProvider
from digital_twin.infrastructure.news_sources import NewsSourceGateway, default_text_fetcher, provider_empty_status


class RuntimeResilienceTests(unittest.TestCase):
    def test_alpha_provider_quota_stops_remaining_fanout_requests(self):
        now = datetime(2026, 7, 23, 7, 0, tzinfo=timezone.utc)
        state = {}
        guard = ExternalApiGuard(state, now=lambda: now)
        calls = []

        def quota_error():
            calls.append("first")
            raise RuntimeError("standard API rate limit is 25 requests per day")

        with self.assertRaises(ExternalRateLimited):
            guard.call(
                "alpha-vantage:GLOBAL_QUOTE:AAPL",
                "Alpha Vantage GLOBAL_QUOTE:AAPL",
                quota_error,
                attempts=1,
                rate_limit_seconds=0,
                failure_threshold=2,
                cooldown_minutes=30,
                shared_rate_limit_key="alpha-vantage:provider",
                shared_daily_request_budget=20,
                shared_quota_cooldown_minutes=1440,
            )

        with self.assertRaises(ExternalRateLimited):
            guard.call(
                "alpha-vantage:GLOBAL_QUOTE:MSFT",
                "Alpha Vantage GLOBAL_QUOTE:MSFT",
                lambda: calls.append("second"),
                attempts=1,
                rate_limit_seconds=0,
                failure_threshold=2,
                cooldown_minutes=30,
                shared_rate_limit_key="alpha-vantage:provider",
                shared_daily_request_budget=20,
                shared_quota_cooldown_minutes=1440,
            )

        self.assertEqual(["first"], calls)
        self.assertEqual("provider-rate-limit", state["alpha-vantage:provider"]["quotaState"])
        self.assertTrue(state["alpha-vantage:provider"]["openedUntil"])

    def test_alpha_daily_budget_defers_without_making_an_external_request(self):
        now = datetime(2026, 7, 23, 7, 0, tzinfo=timezone.utc)
        state = {
            "alpha-vantage:provider": {
                "dailyRequestDate": "2026-07-23",
                "dailyRequestCount": 20,
            }
        }
        guard = ExternalApiGuard(state, now=lambda: now)
        called = []

        with self.assertRaises(ExternalRateLimited):
            guard.call(
                "alpha-vantage:GLOBAL_QUOTE:AAPL",
                "Alpha Vantage GLOBAL_QUOTE:AAPL",
                lambda: called.append(True),
                attempts=1,
                rate_limit_seconds=0,
                failure_threshold=2,
                cooldown_minutes=30,
                shared_rate_limit_key="alpha-vantage:provider",
                shared_daily_request_budget=20,
                shared_quota_cooldown_minutes=1440,
            )

        self.assertEqual([], called)
        self.assertEqual("daily-budget", state["alpha-vantage:provider"]["quotaState"])

    def test_rate_limited_source_is_deferred_not_reported_as_connection_failure(self):
        provider = ExternalSignalProvider(settings={})
        signals = {
            "equityQuotes": {},
            "cryptoMarkets": {"bitcoin": {"price": 1}},
            "macro": {"series": {"DGS10": {"value": 4.5}}},
            "secFilings": {},
            "dartDisclosures": {},
            "newsHeadlines": {},
            "yfinanceData": {},
            "statuses": [],
        }
        provider.status_for_error(signals, "Alpha Vantage", "AAPL ", ExternalRateLimited("provider quota cooldown"))

        status = signals["statuses"][0]
        quality = evaluate_external_signal_quality(signals, settings={"alphaVantageApiKey": "configured"})
        alpha = next(row for row in quality["sourceCoverage"] if row["source"] == "Alpha Vantage")

        self.assertTrue(status["ok"])
        self.assertTrue(status["deferred"])
        self.assertFalse(status["dataUsable"])
        self.assertFalse(alpha["ok"])
        self.assertTrue(alpha["deferred"])
        self.assertEqual(0, quality["errorCount"])

    def test_sec_known_ciks_skip_global_ticker_lookup_without_contact_agent(self):
        calls = []

        def fetch_json(url, _headers=None):
            calls.append(url)
            if "submissions" in url:
                return {
                    "name": "Strategy",
                    "filings": {"recent": {"form": ["10-Q"], "filingDate": ["2026-07-22"], "reportDate": ["2026-06-30"], "accessionNumber": ["0001050446-26-000001"], "primaryDocument": ["report.htm"]}},
                }
            if "companyfacts" in url:
                return {"entityName": "Strategy", "facts": {"us-gaap": {}}}
            raise AssertionError("unexpected SEC endpoint: " + url)

        provider = ExternalSignalProvider(
            settings={"externalSecEnabled": "1", "externalSecMaxSymbols": "3"},
            fetch_json=fetch_json,
        )
        positions = [
            Position(symbol="MSTR", name="Strategy", market="US", currency="USD"),
            Position(symbol="STRC", name="Strategy Preferred", market="US", currency="USD"),
            Position(symbol="CPNG", name="Coupang", market="US", currency="USD"),
        ]
        signals = {"secFilings": {}, "statuses": []}

        provider.add_sec_edgar(signals, positions)

        self.assertNotIn("company_tickers", " ".join(calls))
        self.assertEqual({"MSTR", "STRC", "CPNG"}, set(signals["secFilings"]))

    def test_body_fetch_failure_is_reported_before_budget_exhaustion(self):
        self.assertEqual(
            "article-body-unavailable",
            provider_empty_status({"candidateCount": 6, "bodyMissingCount": 4, "bodyBudgetRejectedCount": 2}),
        )

    def test_news_fetcher_uses_urllib_when_curl_transport_fails(self):
        class Response:
            class Headers:
                @staticmethod
                def get_content_charset():
                    return "utf-8"

            headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read():
                return b"<html><body><p>fallback body</p></body></html>"

        with patch("digital_twin.infrastructure.news_sources.curl_fetch_bytes", side_effect=URLError("resolver timeout")), patch(
            "digital_twin.infrastructure.news_sources.urllib.request.urlopen",
            return_value=Response(),
        ):
            body = default_text_fetcher("https://example.test/article", {"Accept": "text/html"}, timeout=1)

        self.assertIn("fallback body", body)

    def test_google_rss_article_url_is_resolved_before_publisher_body_fetch(self):
        request_context = ["garturlreq", ["ko", "KR"], "ko", "KR", 1, [2, 3, 4], 1, 0, "request-id", 0, 0, None, 0]
        data_payload = json.dumps(request_context).replace('["garturlreq",', "%.@.")
        interstitial = '<html><c-wiz data-p="' + escape(data_payload, quote=True) + '"></c-wiz></html>'
        posted = []

        gateway = NewsSourceGateway(
            settings={"newsCollectionGoogleOriginalUrlMaxPerTarget": "2", "newsCollectionGoogleOriginalUrlMaxPerRun": "6"},
            fetch_text=lambda _url, _headers=None: interstitial,
            fetch_post_text=lambda url, data, _headers=None: posted.append((url, data)) or ")]}'\n[[\"wrb.fr\",\"Fbv4je\",\"[\\\"garturlres\\\",\\\"https://publisher.example/article\\\",1]\",null]]",
        )
        gateway.reset_provider_diagnostics()

        resolved = gateway.resolve_google_news_article_url("https://news.google.com/rss/articles/example?oc=5")

        self.assertEqual("https://publisher.example/article", resolved)
        self.assertEqual(1, len(posted))
        self.assertIn(b"Fbv4je", posted[0][1])
        self.assertEqual(1, gateway._current_provider_diagnostics["googleOriginalUrlResolveAttemptCount"])
        self.assertEqual(1, gateway._current_provider_diagnostics["googleOriginalUrlResolvedCount"])

    def test_google_rss_resolution_budget_is_reported_without_a_false_body_failure(self):
        gateway = NewsSourceGateway(
            settings={"newsCollectionGoogleOriginalUrlMaxPerTarget": "0", "newsCollectionGoogleOriginalUrlMaxPerRun": "0"},
            fetch_text=lambda *_args, **_kwargs: "",
        )
        gateway.reset_provider_diagnostics()

        resolved = gateway.resolve_google_news_article_url("https://news.google.com/rss/articles/example?oc=5")

        self.assertEqual("", resolved)
        self.assertEqual(1, gateway._current_provider_diagnostics["googleOriginalUrlBudgetRejectedCount"])
        self.assertEqual(
            "article-original-url-budget-exhausted",
            provider_empty_status({**gateway._current_provider_diagnostics, "candidateCount": 1}),
        )


if __name__ == "__main__":
    unittest.main()
