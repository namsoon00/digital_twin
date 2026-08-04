import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.external_signal_quality import attach_external_signal_quality, crypto_signal_freshness, evaluate_external_signal_quality
from digital_twin.domain.events import MARKET_DATA_COLLECTED, ONTOLOGY_REASONING_REQUESTED
from digital_twin.domain.ontology_external_abox import external_quality_data_state
from digital_twin.domain.ontology_relation_facts import _external_quality_facts
from digital_twin.domain.portfolio import Position
from digital_twin.application.market_data_collection_service import MarketDataCollectionRunner
from digital_twin.infrastructure.external_signal_utils import ExternalApiGuard, ExternalRateLimited
from digital_twin.infrastructure.external_signals import ExternalSignalProvider
from digital_twin.infrastructure.news_sources import NewsSourceGateway, default_text_fetcher, provider_empty_status
from digital_twin.infrastructure.admin_preview import configured_runtime_flags, public_runtime_settings
from digital_twin.infrastructure.settings import TEXT_SETTING_KEYS
from digital_twin.infrastructure.event_bus import EventBus


class RuntimeResilienceTests(unittest.TestCase):
    def test_market_data_event_waits_for_the_verified_monitor_snapshot_before_reasoning(self):
        events = EventBus()
        runner = MarketDataCollectionRunner(None, None, None, {}, None, event_publisher=events)

        published = runner.publish_collected_event({
            "savedCount": 1,
            "changedCount": 1,
            "symbols": ["000660"],
            "investmentReasoningScheduling": "verified-monitor-snapshot-barrier",
            "reasoningDispatch": "next-verified-monitor-snapshot",
        })

        self.assertTrue(published)
        self.assertEqual([MARKET_DATA_COLLECTED], [event.name for event in events.published])
        self.assertNotIn(ONTOLOGY_REASONING_REQUESTED, [event.name for event in events.published])

    def test_static_admin_preview_masks_sec_contact_configuration(self):
        settings = {
            "externalSecContactEmail": "operations@example.com",
            "externalSecUserAgent": "OrbitAlpha operations@example.com",
        }

        public = public_runtime_settings(settings)
        configured = configured_runtime_flags(settings)

        self.assertNotIn("externalSecContactEmail", public)
        self.assertNotIn("externalSecUserAgent", public)
        self.assertTrue(configured["externalSecContactEmail"])
        self.assertTrue(configured["externalSecUserAgent"])

    def test_coingecko_collection_interval_is_a_persistable_runtime_setting(self):
        self.assertIn("externalCoinGeckoFetchIntervalMinutes", TEXT_SETTING_KEYS)

    def test_cache_only_reasoning_path_reuses_stale_external_signals_without_fetching(self):
        class MemoryCache:
            def __init__(self):
                self.payload = {}

            def load(self):
                return self.payload

            def replace(self, payload):
                self.payload = payload

        cache = MemoryCache()
        position = Position(symbol="AAPL", name="Apple", market="US", currency="USD")
        provider = ExternalSignalProvider(
            settings={"_externalSignalsCacheOnly": "1", "externalApiFetchIntervalMinutes": "10"},
            cache=cache,
        )
        cache_key = provider.cache_key_for_positions([position])
        cached_signals = {
            "fetchedAt": "2026-07-20T00:00:00Z",
            "equityQuotes": {"AAPL": {"price": 100}},
            "cryptoMarkets": {},
            "macro": {},
            "fxRates": {},
            "secFilings": {},
            "dartDisclosures": {},
            "newsHeadlines": {},
            "companyOverviews": {},
            "earningsReports": {},
            "yfinanceData": {},
            "researchEvidence": {},
            "statuses": [],
        }
        cache.replace(provider.next_cache_payload({}, cache_key, cached_signals))

        with patch.object(provider, "fetch_signals", side_effect=AssertionError("cache-only must not fetch")):
            result = provider.signals_for_positions([position])

        self.assertEqual("2026-07-20T00:00:00Z", result["fetchedAt"])
        self.assertEqual(100, result["equityQuotes"]["AAPL"]["price"])
        status = next(row for row in result["statuses"] if row["source"] == "External signal cache")
        self.assertTrue(status["cacheOnly"])
        self.assertTrue(status["deferred"])
        self.assertFalse(status["dataUsable"])

    def test_cache_only_reasoning_path_reports_missing_cache_without_fetching(self):
        class MemoryCache:
            @staticmethod
            def load():
                return {}

        position = Position(symbol="AAPL", name="Apple", market="US", currency="USD")
        provider = ExternalSignalProvider(
            settings={"_externalSignalsCacheOnly": "1"},
            cache=MemoryCache(),
        )

        with patch.object(provider, "fetch_signals", side_effect=AssertionError("cache-only must not fetch")):
            result = provider.signals_for_positions([position])

        status = next(row for row in result["statuses"] if row["source"] == "External signal cache")
        self.assertTrue(status["cacheOnly"])
        self.assertTrue(status["deferred"])
        self.assertFalse(status["dataUsable"])

    def test_cached_external_snapshot_refreshes_only_coingecko_when_due(self):
        class MemoryCache:
            def __init__(self):
                self.payload = {}

            def load(self):
                return self.payload

            def replace(self, payload):
                self.payload = payload

        class EvidenceStore:
            def latest(self, **_kwargs):
                return []

        now = datetime.now(timezone.utc)
        now_text = now.isoformat().replace("+00:00", "Z")
        stale_crypto_text = (now.replace(second=0, microsecond=0) - timedelta(minutes=11)).isoformat().replace("+00:00", "Z")
        cache = MemoryCache()
        crypto_cache = MemoryCache()
        position = Position(symbol="AAPL", name="Apple", market="US", currency="USD")
        provider = ExternalSignalProvider(
            settings={
                "externalApiFetchIntervalMinutes": "30",
                "externalSignalCacheMaxAgeMinutes": "30",
                "externalCoinGeckoEnabled": "1",
                "externalCoinGeckoFetchIntervalMinutes": "10",
                "externalAlphaEnabled": "0",
                "externalFredEnabled": "0",
                "externalDartEnabled": "0",
                "externalSecEnabled": "0",
                "externalNewsEnabled": "0",
                "externalFxRateEnabled": "0",
                "externalYFinanceEnabled": "0",
                "externalApiRateLimitSeconds": "0",
                "externalApiRetryAttempts": "1",
            },
            cache=cache,
            crypto_cache=crypto_cache,
            evidence_store=EvidenceStore(),
            fetch_json=lambda *_args, **_kwargs: [{
                "id": "bitcoin",
                "symbol": "btc",
                "name": "Bitcoin",
                "current_price": 65000,
                "price_change_percentage_24h": -3.2,
                "price_change_percentage_7d_in_currency": -4.5,
                "last_updated": now_text,
            }],
            sleep=lambda _seconds: None,
        )
        cache_key = provider.cache_key_for_positions([position])
        cached_signals = {
            "fetchedAt": now_text,
            "cryptoFetchedAt": stale_crypto_text,
            "cryptoLastAttemptAt": stale_crypto_text,
            "equityQuotes": {},
            "cryptoMarkets": {"bitcoin": {"symbol": "BTC", "price": 60000, "fetchedAt": stale_crypto_text}},
            "macro": {},
            "fxRates": {},
            "secFilings": {},
            "dartDisclosures": {},
            "newsHeadlines": {},
            "companyOverviews": {},
            "earningsReports": {},
            "yfinanceData": {},
            "researchEvidence": {},
            "statuses": [{"source": "CoinGecko", "ok": False, "message": "old failure"}],
        }
        cache.replace(provider.next_cache_payload({}, cache_key, cached_signals, cache_scope="account-snapshot", subject_count=1))

        refreshed = provider.signals_for_positions([position], cache_scope="account-snapshot")
        stored = cache.load()["entries"][cache_key]["signals"]

        self.assertEqual(now_text, refreshed["fetchedAt"])
        self.assertEqual(65000, refreshed["cryptoMarkets"]["bitcoin"]["price"])
        self.assertEqual("fresh", refreshed["cryptoFreshness"]["status"])
        self.assertTrue(stored["cryptoFetchedAt"])
        self.assertEqual(65000, crypto_cache.load()["markets"]["bitcoin"]["price"])
        self.assertFalse(any(not item.get("ok") for item in stored["statuses"] if item.get("source") == "CoinGecko"))

    def test_cache_only_reasoning_recovers_crypto_from_an_unrelated_aggregate_key(self):
        class MemoryCache:
            def __init__(self, payload=None):
                self.payload = payload or {}

            def load(self):
                return self.payload

            def replace(self, payload):
                self.payload = payload

        class EvidenceStore:
            @staticmethod
            def latest(**_kwargs):
                return []

        now_text = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        aggregate = MemoryCache({
            "schemaVersion": 1,
            "entries": {
                "different-portfolio-key": {
                    "fetchedAt": now_text,
                    "cacheScope": "account-snapshot",
                    "signals": {
                        "fetchedAt": now_text,
                        "cryptoFetchedAt": now_text,
                        "cryptoLastAttemptAt": now_text,
                        "cryptoMarkets": {
                            "bitcoin": {
                                "provider": "CoinGecko",
                                "symbol": "BTC",
                                "price": 63811,
                                "lastUpdated": now_text,
                                "fetchedAt": now_text,
                            },
                        },
                        "statuses": [{"source": "CoinGecko", "ok": True, "dataUsable": True}],
                    },
                },
            },
        })
        dedicated = MemoryCache()
        provider = ExternalSignalProvider(
            settings={
                "_externalSignalsCacheOnly": "1",
                "externalCoinGeckoEnabled": "1",
                "dataFreshnessExternalCryptoMaxAgeMinutes": "25",
            },
            cache=aggregate,
            crypto_cache=dedicated,
            evidence_store=EvidenceStore(),
        )
        position = Position(symbol="AAPL", name="Apple", market="US", currency="USD")

        with patch.object(provider, "fetch_signals", side_effect=AssertionError("cache-only must not fetch")):
            result = provider.signals_for_positions([position], cache_scope="account-snapshot")

        self.assertEqual(63811, result["cryptoMarkets"]["bitcoin"]["price"])
        self.assertEqual("fresh", result["cryptoFreshness"]["status"])
        self.assertEqual("aggregate-migrated", result["cryptoFreshness"]["cacheState"])
        self.assertEqual(63811, dedicated.load()["markets"]["bitcoin"]["price"])

    def test_fresh_dedicated_crypto_cache_prevents_duplicate_vendor_fetch(self):
        class MemoryCache:
            def __init__(self, payload=None):
                self.payload = payload or {}

            def load(self):
                return self.payload

            def replace(self, payload):
                self.payload = payload

        class EvidenceStore:
            @staticmethod
            def latest(**_kwargs):
                return []

            @staticmethod
            def upsert_many(_items):
                return None

        now_text = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        dedicated = MemoryCache({
            "schemaVersion": 1,
            "provider": "CoinGecko",
            "dataset": "coins/markets",
            "markets": {
                "bitcoin": {
                    "provider": "CoinGecko",
                    "symbol": "BTC",
                    "price": 64000,
                    "lastUpdated": now_text,
                    "fetchedAt": now_text,
                },
            },
            "fetchedAt": now_text,
            "lastAttemptAt": now_text,
            "sourceAsOf": now_text,
            "statusRows": [{"source": "CoinGecko", "ok": True, "dataUsable": True}],
        })
        aggregate = MemoryCache()
        settings = {
            "externalCoinGeckoEnabled": "1",
            "externalCoinGeckoFetchIntervalMinutes": "10",
            "externalAlphaEnabled": "0",
            "externalFredEnabled": "0",
            "externalDartEnabled": "0",
            "externalSecEnabled": "0",
            "externalNewsEnabled": "0",
            "externalFxRateEnabled": "0",
            "externalYFinanceEnabled": "0",
        }
        provider = ExternalSignalProvider(
            settings=settings,
            cache=aggregate,
            crypto_cache=dedicated,
            evidence_store=EvidenceStore(),
            fetch_json=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fresh crypto must not refetch")),
        )

        result = provider.signals_for_positions([
            Position(symbol="AAPL", name="Apple", market="US", currency="USD"),
        ])

        self.assertEqual(64000, result["cryptoMarkets"]["bitcoin"]["price"])
        self.assertEqual("fresh", result["cryptoFreshness"]["status"])
        self.assertEqual("dedicated", result["cryptoFreshness"]["cacheState"])
        self.assertEqual(1, len(aggregate.load()["entries"]))

    def test_coingecko_failure_persists_attempt_and_preserves_last_good_market(self):
        class MemoryCache:
            def __init__(self, payload=None):
                self.payload = payload or {}

            def load(self):
                return self.payload

            def replace(self, payload):
                self.payload = payload

        class EvidenceStore:
            @staticmethod
            def latest(**_kwargs):
                return []

            @staticmethod
            def upsert_many(_items):
                return None

        stale_text = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat().replace("+00:00", "Z")
        dedicated = MemoryCache({
            "schemaVersion": 1,
            "provider": "CoinGecko",
            "dataset": "coins/markets",
            "markets": {
                "bitcoin": {
                    "provider": "CoinGecko",
                    "symbol": "BTC",
                    "price": 63000,
                    "lastUpdated": stale_text,
                    "fetchedAt": stale_text,
                },
            },
            "fetchedAt": stale_text,
            "lastAttemptAt": stale_text,
            "sourceAsOf": stale_text,
            "statusRows": [{"source": "CoinGecko", "ok": True, "dataUsable": True}],
        })
        provider = ExternalSignalProvider(
            settings={
                "externalCoinGeckoEnabled": "1",
                "externalCoinGeckoFetchIntervalMinutes": "10",
                "dataFreshnessExternalCryptoMaxAgeMinutes": "25",
                "externalApiRetryAttempts": "1",
                "externalApiRateLimitSeconds": "0",
                "externalAlphaEnabled": "0",
                "externalFredEnabled": "0",
                "externalDartEnabled": "0",
                "externalSecEnabled": "0",
                "externalNewsEnabled": "0",
                "externalFxRateEnabled": "0",
                "externalYFinanceEnabled": "0",
            },
            cache=MemoryCache(),
            crypto_cache=dedicated,
            evidence_store=EvidenceStore(),
            fetch_json=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated provider outage")),
            sleep=lambda _seconds: None,
        )

        result = provider.signals_for_positions([
            Position(symbol="AAPL", name="Apple", market="US", currency="USD"),
        ])
        stored = dedicated.load()

        self.assertEqual(63000, result["cryptoMarkets"]["bitcoin"]["price"])
        self.assertEqual("partial", result["cryptoFreshness"]["status"])
        self.assertNotEqual(stale_text, stored["lastAttemptAt"])
        self.assertEqual(63000, stored["markets"]["bitcoin"]["price"])
        self.assertFalse(stored["statusRows"][-1]["ok"])

    def test_crypto_freshness_uses_its_own_twenty_five_minute_budget(self):
        signals = {
            "cryptoFetchedAt": "2026-08-01T00:00:00Z",
            "cryptoMarkets": {"bitcoin": {"symbol": "BTC", "price": 65000}},
            "statuses": [],
        }

        fresh = crypto_signal_freshness(
            signals,
            settings={"dataFreshnessExternalCryptoMaxAgeMinutes": "25"},
            now=datetime(2026, 8, 1, 0, 24, tzinfo=timezone.utc),
        )
        stale = crypto_signal_freshness(
            signals,
            settings={"dataFreshnessExternalCryptoMaxAgeMinutes": "25"},
            now=datetime(2026, 8, 1, 0, 26, tzinfo=timezone.utc),
        )

        self.assertEqual("fresh", fresh["status"])
        self.assertEqual("stale", stale["status"])

    def test_account_snapshot_cache_survives_per_symbol_cache_eviction(self):
        provider = ExternalSignalProvider(settings={"externalSignalCacheMaxEntries": "3"})
        payload = provider.next_cache_payload(
            {},
            "account-snapshot",
            {"fetchedAt": "2026-07-27T00:00:00Z"},
            cache_scope="account-snapshot",
            subject_count=4,
        )
        for index in range(4):
            payload = provider.next_cache_payload(
                payload,
                "research-" + str(index),
                {"fetchedAt": "2026-07-27T00:0" + str(index + 1) + ":00Z"},
                cache_scope="research",
                subject_count=1,
            )

        entries = payload["entries"]
        self.assertIn("account-snapshot", entries)
        self.assertEqual("account-snapshot", entries["account-snapshot"]["cacheScope"])
        self.assertEqual(3, len(entries))

    def test_aggregate_cache_keeps_only_the_two_newest_account_snapshots(self):
        class MemoryCache:
            @staticmethod
            def load():
                return {}

        provider = ExternalSignalProvider(
            settings={"externalSignalCacheMaxEntries": "3"},
            cache=MemoryCache(),
        )
        payload = {}
        for index in range(4):
            payload = provider.next_cache_payload(
                payload,
                "account-" + str(index),
                {"fetchedAt": "2026-07-27T00:0" + str(index) + ":00Z"},
                cache_scope="account-snapshot",
                subject_count=4,
            )
        payload = provider.next_cache_payload(
            payload,
            "research-latest",
            {"fetchedAt": "2026-07-27T00:05:00Z"},
            cache_scope="research",
            subject_count=1,
        )

        self.assertEqual({"account-2", "account-3", "research-latest"}, set(payload["entries"]))

    def test_fresh_legacy_cache_is_promoted_for_account_snapshot_reuse(self):
        class MemoryCache:
            def __init__(self):
                self.payload = {}

            def load(self):
                return self.payload

            def replace(self, payload):
                self.payload = payload

        cache = MemoryCache()
        position = Position(symbol="AAPL", name="Apple", market="US", currency="USD")
        provider = ExternalSignalProvider(
            settings={"externalApiFetchIntervalMinutes": "10"},
            cache=cache,
        )
        cache_key = provider.cache_key_for_positions([position])
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        signals = {
            "fetchedAt": fetched_at,
            "equityQuotes": {},
            "cryptoMarkets": {},
            "macro": {},
            "fxRates": {},
            "secFilings": {},
            "dartDisclosures": {},
            "newsHeadlines": {},
            "companyOverviews": {},
            "earningsReports": {},
            "yfinanceData": {},
            "researchEvidence": {},
            "statuses": [],
        }
        cache.replace({"schemaVersion": 1, "entries": {
            cache_key: {"fetchedAt": fetched_at, "signals": signals},
        }})

        with patch.object(provider, "fetch_signals", side_effect=AssertionError("fresh cache must not fetch")):
            provider.signals_for_positions([position], cache_scope="account-snapshot")

        entry = cache.load()["entries"][cache_key]
        self.assertEqual("account-snapshot", entry["cacheScope"])
        self.assertEqual(1, entry["subjectCount"])

    def test_market_data_external_refresh_is_summarized_without_exposing_payload(self):
        calls = []

        def refresh(positions):
            calls.extend(position.symbol for position in positions)
            return {
                "fetchedAt": "2026-07-27T00:00:00Z",
                "statuses": [
                    {"source": "provider-a", "ok": True},
                    {"source": "provider-b", "ok": False},
                    {"source": "provider-c", "ok": True, "deferred": True},
                ],
                "privatePayload": {"large": "not included"},
            }

        runner = MarketDataCollectionRunner(
            None,
            None,
            None,
            {},
            None,
            external_signal_refresher=refresh,
        )
        result = runner.refresh_external_signal_cache([
            Position(symbol="AAPL", name="Apple", market="NASDAQ", currency="USD"),
            Position(symbol="", name="", market="", currency=""),
        ])

        self.assertEqual(["AAPL"], calls)
        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["symbolCount"])
        self.assertEqual(3, result["providerStatusCount"])
        self.assertEqual(1, result["providerFailureCount"])
        self.assertEqual(1, result["providerDeferredCount"])
        self.assertNotIn("privatePayload", result)

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

    def test_recent_but_partial_external_data_is_not_marked_fresh_for_judgement(self):
        now = datetime(2026, 7, 23, 7, 0, tzinfo=timezone.utc)
        signals = {
            "fetchedAt": "2026-07-23T07:00:00Z",
            "equityQuotes": {"AAPL": {"price": 100}},
            "cryptoMarkets": {"bitcoin": {"price": 1}},
            "macro": {"series": {"DGS10": {"value": 4.5}}},
            "secFilings": {"AAPL": {"facts": {}}},
            "dartDisclosures": {"AAPL": {"items": []}},
            "newsHeadlines": {},
            "yfinanceData": {"AAPL": {"price": 100}},
            "statuses": [{
                "source": "Alpha Vantage",
                "ok": True,
                "deferred": True,
                "dataUsable": False,
                "message": "provider quota cooldown",
            }],
        }

        attached = attach_external_signal_quality(
            signals,
            positions=[Position(symbol="AAPL", name="Apple", market="US", currency="USD")],
            settings={"alphaVantageApiKey": "configured", "externalApiFetchIntervalMinutes": "30"},
            now=now,
        )

        self.assertEqual("partial", attached["quality"]["dataState"])
        self.assertEqual("fresh", attached["freshness"]["transportStatus"])
        self.assertEqual("partial", attached["freshness"]["status"])
        self.assertEqual(
            "partial",
            external_quality_data_state(
                attached["quality"], attached["freshness"], attached["provenance"]
            ),
        )
        self.assertEqual("partial", _external_quality_facts(attached)["externalSignalDataState"])

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

    def test_sec_document_fetch_is_deferred_without_contact_email(self):
        document_calls = []

        def fetch_json(url, _headers=None):
            if "submissions" in url:
                return {
                    "name": "Strategy",
                    "filings": {"recent": {
                        "form": ["10-Q"],
                        "filingDate": ["2026-07-22"],
                        "reportDate": ["2026-06-30"],
                        "accessionNumber": ["0001050446-26-000001"],
                        "primaryDocument": ["report.htm"],
                    }},
                }
            if "companyfacts" in url:
                return {"entityName": "Strategy", "facts": {"us-gaap": {}}}
            raise AssertionError("unexpected SEC endpoint: " + url)

        provider = ExternalSignalProvider(
            settings={
                "externalSecEnabled": "1",
                "externalSecDocumentTextEnabled": "1",
                "externalSecMaxSymbols": "1",
                "externalApiRetryAttempts": "1",
                "externalApiRateLimitSeconds": "0",
            },
            fetch_json=fetch_json,
            fetch_text=lambda url, _headers=None: document_calls.append(url) or "<p>filing body</p>",
        )
        signals = {"secFilings": {}, "statuses": []}

        provider.add_sec_edgar(
            signals,
            [Position(symbol="MSTR", name="Strategy", market="US", currency="USD")],
        )

        filing = signals["secFilings"]["MSTR"]["latestFiling"]
        status = next(item for item in signals["statuses"] if item.get("configurationKey") == "externalSecContactEmail")
        self.assertEqual([], document_calls)
        self.assertEqual("deferred-contact", filing["documentTextQuality"])
        self.assertTrue(status["dataUsable"])
        self.assertTrue(status["deferred"])
        self.assertFalse(status["documentTextDataUsable"])

    def test_sec_contact_email_enables_document_fetch_with_compliant_user_agent(self):
        headers = []

        def fetch_json(url, _headers=None):
            if "submissions" in url:
                return {
                    "name": "Strategy",
                    "filings": {"recent": {
                        "form": ["10-Q"],
                        "filingDate": ["2026-07-22"],
                        "reportDate": ["2026-06-30"],
                        "accessionNumber": ["0001050446-26-000001"],
                        "primaryDocument": ["report.htm"],
                    }},
                }
            if "companyfacts" in url:
                return {"entityName": "Strategy", "facts": {"us-gaap": {}}}
            raise AssertionError("unexpected SEC endpoint: " + url)

        provider = ExternalSignalProvider(
            settings={
                "externalSecEnabled": "1",
                "externalSecDocumentTextEnabled": "1",
                "externalSecContactEmail": "operations@example.com",
                "externalSecMaxSymbols": "1",
                "externalApiRetryAttempts": "1",
                "externalApiRateLimitSeconds": "0",
            },
            fetch_json=fetch_json,
            fetch_text=lambda _url, request_headers=None: headers.append(request_headers) or "<p>" + ("verified filing body " * 12) + "</p>",
        )
        signals = {"secFilings": {}, "statuses": []}

        provider.add_sec_edgar(
            signals,
            [Position(symbol="MSTR", name="Strategy", market="US", currency="USD")],
        )

        filing = signals["secFilings"]["MSTR"]["latestFiling"]
        self.assertEqual("body", filing["documentTextQuality"])
        self.assertEqual(1, len(headers))
        self.assertIn("operations@example.com", headers[0]["User-Agent"])

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
