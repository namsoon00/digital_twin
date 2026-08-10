import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.notification_ai_gate_message import execution_telegram_message
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.external_api_sources import external_api_source_metadata
from digital_twin.domain.investment_research import research_evidence_from_external_signals
from digital_twin.domain.investor_flow_psychology import investor_flow_psychology
from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notification_templates import NotificationTemplate, alert_context, render_notification
from digital_twin.domain.ontology_observation_quality import position_observation_profiles
from digital_twin.domain.portfolio import AccountSnapshot, AlertEvent, Position
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.monitoring import RealtimeMonitor
from digital_twin.infrastructure.external_signals import ExternalSignalProvider
from digital_twin.infrastructure.external_signal_utils import sanitize_sensitive_text
from digital_twin.infrastructure.kis_market_signals import KISMarketSignalProvider, stage_coverage
from digital_twin.infrastructure.toss_snapshots import TossProvider, normalize_price_payload


class MemoryQuoteCache:
    def __init__(self, payloads=None):
        self.payloads = dict(payloads or {})

    def load(self, provider, account_id, symbol):
        return dict(self.payloads.get(str(symbol), {}))

    def save(self, provider, account_id, symbol, payload):
        self.payloads[str(symbol)] = dict(payload or {})


class ExternalApiSourceTests(unittest.TestCase):
    def test_fred_uses_dedicated_timeout_on_default_transport(self):
        settings = {
            "externalAlphaEnabled": "0",
            "externalCoinGeckoEnabled": "0",
            "externalFredEnabled": "1",
            "externalFredSeries": "DGS10",
            "externalFredMaxSeries": "1",
            "externalFredTimeoutSeconds": "7",
            "fredApiKey": "fred-key",
            "externalYFinanceEnabled": "0",
            "externalSecEnabled": "0",
            "externalDartEnabled": "0",
            "externalNewsEnabled": "0",
            "externalFxRateEnabled": "0",
            "externalApiRetryAttempts": "1",
            "externalApiRateLimitSeconds": "0",
        }
        provider = ExternalSignalProvider(settings=settings)

        with patch(
            "digital_twin.infrastructure.external_signal_provider_core.default_json_fetcher",
            return_value={"observations": [{"date": "2026-07-01", "value": "4.35"}]},
        ) as fetch:
            signals = provider.fetch_signals([])

        self.assertEqual(4.35, signals["macro"]["series"]["DGS10"]["value"])
        self.assertEqual(7.0, fetch.call_args.kwargs["timeout"])

    def test_fred_uses_published_history_for_rate_change_windows(self):
        settings = {
            "externalAlphaEnabled": "0",
            "externalCoinGeckoEnabled": "0",
            "externalFredEnabled": "1",
            "externalFredSeries": "DGS10",
            "externalFredMaxSeries": "1",
            "fredApiKey": "fred-key",
            "externalYFinanceEnabled": "0",
            "externalSecEnabled": "0",
            "externalDartEnabled": "0",
            "externalNewsEnabled": "0",
            "externalFxRateEnabled": "0",
            "externalApiRetryAttempts": "1",
            "externalApiRateLimitSeconds": "0",
        }
        observations = [
            {"date": "2026-08-03", "value": "4.75"},
            {"date": "2026-08-02", "value": "4.70"},
            {"date": "2026-08-01", "value": "4.68"},
            {"date": "2026-07-31", "value": "."},
            {"date": "2026-07-30", "value": "4.66"},
            {"date": "2026-07-29", "value": "4.63"},
            {"date": "2026-07-28", "value": "4.55"},
        ]
        provider = ExternalSignalProvider(settings=settings)

        with patch(
            "digital_twin.infrastructure.external_signal_provider_core.default_json_fetcher",
            return_value={"observations": observations},
        ) as fetch:
            signals = provider.fetch_signals([])

        rate = signals["macro"]["series"]["DGS10"]
        self.assertEqual("2026-08-03", rate["observationDate"])
        self.assertEqual("2026-08-02", rate["previousDate"])
        self.assertAlmostEqual(5, rate["deltaBp"])
        self.assertAlmostEqual(20, rate["delta5dBp"])
        self.assertIn("limit=25", fetch.call_args.args[0])

    def test_toss_price_outside_market_session_is_labeled_last_close_reference(self):
        quote = normalize_price_payload(
            {
                "symbol": "005930",
                "name": "삼성전자",
                "market": "KR",
                "currency": "KRW",
                "lastPrice": "70000",
                "timestamp": "2026-07-24T06:30:00Z",
            },
            now=datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc),
        )

        self.assertEqual("reference", quote["dataQuality"])
        self.assertEqual("last-close", quote["freshnessStatus"])
        self.assertEqual("provider-last-close", quote["sourceTimestampState"])
        self.assertEqual("closed", quote["marketSession"])
        self.assertFalse(quote["realTime"])

    def test_toss_merge_keeps_last_close_as_reference_when_candles_are_available(self):
        quote = normalize_price_payload(
            {
                "symbol": "005930",
                "name": "삼성전자",
                "market": "KR",
                "currency": "KRW",
                "lastPrice": "70000",
                "timestamp": "2026-07-24T06:30:00Z",
            },
            now=datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc),
        )
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "quantity": 1,
            "currentPrice": 70000,
            "dataQuality": "actual",
        })
        provider = TossProvider(
            AccountConfig("test", "test", "toss", "https://example.test", "id", "secret", "1", []),
            quote_cache=SimpleNamespace(),
            settings={},
        )

        merged = provider.merge_market_data(
            position,
            quote,
            {"ma20": 69000, "ma60": 68000},
            {},
            quote_live=True,
            indicators_live=True,
        )
        profiles = position_observation_profiles(
            merged,
            {"asOf": "2026-07-26T08:00:00Z", "settings": {}},
        )

        self.assertEqual("reference", merged.data_quality)
        self.assertEqual("last-close", merged.freshness_status)
        self.assertIn("장 마감 기준값", merged.quote_message)
        self.assertEqual("last-close", profiles["quote"]["freshnessStatus"])
        self.assertEqual("limited", profiles["quote"]["sourceTrustState"])

    def test_kis_rest_price_outside_regular_session_is_labeled_last_close(self):
        coverage = stage_coverage(
            "price",
            {"stck_prpr": "70000"},
            {"currentPrice": 70000},
            ["currentPrice"],
            fetched_at="2026-07-26T08:00:00Z",
            session={"key": "post_close", "regular": False},
            transport="rest",
        )

        self.assertEqual("available", coverage["status"])
        self.assertEqual("last-close", coverage["freshnessStatus"])
        self.assertEqual("market-close-reference", coverage["cadence"])
        self.assertEqual("market-closed-reference", coverage["latencyStatus"])

    def test_kis_last_close_does_not_replace_fresh_toss_premarket_quote(self):
        provider = KISMarketSignalProvider(settings={"kisMarketSignalsEnabled": "0"})
        position = normalize_position({
            "symbol": "035420",
            "name": "NAVER",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 231500,
            "changeRate": 11.57,
            "quoteSource": "Toss /api/v1/prices",
            "quoteStatus": "토스 prices 반영",
            "quoteMessage": "현재가는 토스 prices 기준입니다.",
            "updatedAt": "2026-07-26T23:49:15Z",
            "freshnessStatus": "fresh",
            "marketSession": "open",
            "ma20": 197595,
            "ma60": 213982,
            "volume": 1234,
        })
        signal = {
            "currentPrice": 207500,
            "changeRate": 0,
            "volume": 70,
            "quoteSource": "KIS Open API",
            "quoteStatus": "KIS 현재가 반영",
            "quoteMessage": "KIS 장 마감 기준값입니다.",
            "updatedAt": "2026-07-26T23:50:13Z",
            "marketSignalCoverage": {
                "price": {
                    "status": "available",
                    "freshnessStatus": "last-close",
                    "cadence": "market-close-reference",
                    "latencyStatus": "market-closed-reference",
                },
                "orderbook": {"status": "available", "fields": ["orderbookBidVolume"]},
            },
        }

        merged = provider.merge_position(position, signal)

        self.assertEqual(231500, merged.current_price)
        self.assertEqual(11.57, merged.change_rate)
        self.assertEqual(1234, merged.volume)
        self.assertEqual("Toss /api/v1/prices", merged.quote_source)
        self.assertEqual("토스 prices 반영", merged.quote_status)
        self.assertEqual("2026-07-26T23:49:15Z", merged.updated_at)
        self.assertIn("신선한 현재가", merged.quote_message)
        self.assertAlmostEqual(8.19, merged.ma60_distance, places=2)

    def test_kis_last_close_fills_nonfresh_position_quote(self):
        provider = KISMarketSignalProvider(settings={"kisMarketSignalsEnabled": "0"})
        position = normalize_position({
            "symbol": "035420",
            "name": "NAVER",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 205000,
            "freshnessStatus": "last-close",
        })
        signal = {
            "currentPrice": 207500,
            "quoteSource": "KIS Open API",
            "updatedAt": "2026-07-26T23:50:13Z",
            "marketSignalCoverage": {
                "price": {
                    "status": "available",
                    "freshnessStatus": "last-close",
                    "cadence": "market-close-reference",
                },
            },
        }

        merged = provider.merge_position(position, signal)

        self.assertEqual(207500, merged.current_price)
        self.assertEqual("KIS Open API", merged.quote_source)

    def test_kis_code_only_name_does_not_replace_resolved_company_name(self):
        provider = KISMarketSignalProvider(settings={"kisMarketSignalsEnabled": "0"})
        position = normalize_position({
            "symbol": "028260",
            "name": "삼성물산",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 336500,
        })

        merged = provider.merge_position(position, {
            "name": "028260",
            "currentPrice": 336500,
            "quoteSource": "KIS Open API",
        })

        self.assertEqual("삼성물산", merged.name)

    def test_kis_rest_price_does_not_replace_fresh_toss_quote_without_websocket_tick(self):
        provider = KISMarketSignalProvider(settings={"kisMarketSignalsEnabled": "0"})
        position = normalize_position({
            "symbol": "035420",
            "name": "NAVER",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 223500,
            "freshnessStatus": "fresh",
            "quoteSource": "Toss /api/v1/prices",
        })
        signal = {
            "currentPrice": 207500,
            "quoteSource": "KIS Open API",
            "marketSignalCoverage": {
                "price": {
                    "status": "available",
                    "freshnessStatus": "near-live",
                    "transport": "rest",
                },
                "ccnl": {"status": "unavailable", "fields": []},
            },
        }

        merged = provider.merge_position(position, signal)

        self.assertEqual(223500, merged.current_price)
        self.assertEqual("Toss /api/v1/prices", merged.quote_source)

    def test_kis_websocket_price_replaces_fresh_toss_quote(self):
        provider = KISMarketSignalProvider(settings={"kisMarketSignalsEnabled": "0"})
        position = normalize_position({
            "symbol": "035420",
            "name": "NAVER",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 223500,
            "freshnessStatus": "fresh",
            "quoteSource": "Toss /api/v1/prices",
        })
        signal = {
            "currentPrice": 220000,
            "quoteSource": "KIS Open API + KIS WebSocket",
            "marketSignalCoverage": {
                "price": {"status": "available", "freshnessStatus": "near-live", "transport": "rest"},
                "ccnl": {
                    "status": "available",
                    "fields": ["currentPrice", "tradeStrength"],
                    "cadence": "websocket",
                    "realTime": True,
                    "freshnessStatus": "realtime",
                },
            },
        }

        merged = provider.merge_position(position, signal)

        self.assertEqual(220000, merged.current_price)
        self.assertIn("KIS Open API", merged.quote_source)

    def test_kis_regular_session_uses_scheduled_investor_estimate_not_close_only_totals(self):
        calls = []

        def fake_fetch_json(method, url, headers=None, body=None, query=None, timeout=12):
            path = url.split("?", 1)[0]
            calls.append((path, dict(query or {})))
            if path.endswith("/oauth2/tokenP"):
                return {"access_token": "token"}
            if path.endswith("/inquire-price"):
                return {"rt_cd": "0", "output": {"stck_prpr": "39000", "acml_vol": "1000"}}
            if path.endswith("/inquire-ccnl"):
                return {"rt_cd": "0", "output": [{"stck_prpr": "39000", "tday_rltv": "88"}]}
            if path.endswith("/investor-trend-estimate"):
                return {"rt_cd": "0", "output1": None, "output2": [
                    {"bsop_hour_gb": "5", "frgn_fake_ntby_qty": "-349000", "orgn_fake_ntby_qty": "19000"},
                    {"bsop_hour_gb": "2", "frgn_fake_ntby_qty": "-226000", "orgn_fake_ntby_qty": "-4000"},
                    {"bsop_hour_gb": "1", "frgn_fake_ntby_qty": "-121000", "orgn_fake_ntby_qty": "0"},
                ]}
            if path.endswith("/inquire-asking-price-exp-ccn"):
                return {"rt_cd": "0", "output1": {"total_bidp_rsqn": "1200", "total_askp_rsqn": "900"}}
            raise AssertionError("unexpected KIS path: " + path)

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "key",
                "kisAppSecret": "secret",
                "kisMarketSignalGapSeconds": "0",
                "kisInvestorIntradayEstimateEnabled": "1",
            },
            quote_cache=MemoryQuoteCache(),
            fetch_json=fake_fetch_json,
            now_provider=lambda: datetime(2026, 8, 10, 1, 5, tzinfo=timezone.utc),
        )
        prior = normalize_position({
            "symbol": "035720",
            "name": "카카오",
            "market": "KR",
            "currency": "KRW",
            "foreignBuyVolume": 999,
            "foreignSellVolume": 1,
            "foreignNetVolume": 998,
            "institutionNetVolume": 777,
            "individualNetVolume": -1775,
        })

        positions, _watchlist = provider.enrich_collections([prior], [])
        position = positions[0]
        coverage = position.market_signal_coverage["investor"]

        self.assertTrue(any(path.endswith("/investor-trend-estimate") for path, _query in calls))
        self.assertFalse(any(path.endswith("/inquire-investor") for path, _query in calls))
        self.assertEqual({"MKSC_SHRN_ISCD": "035720"}, next(query for path, query in calls if path.endswith("/investor-trend-estimate")))
        self.assertEqual(-226000, position.foreign_net_volume)
        self.assertEqual(-4000, position.institution_net_volume)
        self.assertEqual(0, position.foreign_buy_volume)
        self.assertEqual(0, position.foreign_sell_volume)
        self.assertEqual(0, position.individual_net_volume)
        self.assertEqual("intraday-estimate", coverage["measurementType"])
        self.assertEqual("10:00", coverage["providerUpdateSlot"])
        self.assertEqual("2026-08-10T10:00:00+09:00", coverage["sourceAsOf"])
        self.assertEqual("scheduled-estimate", coverage["cadence"])
        self.assertFalse(coverage["realTime"])
        self.assertTrue(coverage["judgementEvidenceUsable"])
        psychology = investor_flow_psychology(position)
        self.assertEqual("estimated", psychology["dataState"])
        self.assertEqual("intraday-estimate", psychology["investorFlowMeasurementType"])
        self.assertTrue(psychology["investorFlowIsEstimate"])
        self.assertIn("10:00 KST 기준", RealtimeMonitor().investor_context_line(position.to_dict()))

    def test_kis_regular_session_waits_for_first_official_investor_estimate_slot(self):
        calls = []

        def fake_fetch_json(method, url, headers=None, body=None, query=None, timeout=12):
            path = url.split("?", 1)[0]
            calls.append(path)
            if path.endswith("/oauth2/tokenP"):
                return {"access_token": "token"}
            if path.endswith("/inquire-price"):
                return {"rt_cd": "0", "output": {"stck_prpr": "39000", "acml_vol": "1000"}}
            if path.endswith("/inquire-ccnl"):
                return {"rt_cd": "0", "output": [{"stck_prpr": "39000", "tday_rltv": "88"}]}
            if path.endswith("/inquire-asking-price-exp-ccn"):
                return {"rt_cd": "0", "output1": {"total_bidp_rsqn": "1200", "total_askp_rsqn": "900"}}
            raise AssertionError("investor estimate must not be requested before 09:30 KST")

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "key",
                "kisAppSecret": "secret",
                "kisMarketSignalGapSeconds": "0",
                "kisInvestorIntradayEstimateEnabled": "1",
            },
            quote_cache=MemoryQuoteCache(),
            fetch_json=fake_fetch_json,
            now_provider=lambda: datetime(2026, 8, 10, 0, 15, tzinfo=timezone.utc),
        )

        signal = provider.fetch_symbol_signal("035720")
        coverage = signal["marketSignalCoverage"]["investor"]

        self.assertFalse(any(path.endswith("/investor-trend-estimate") for path in calls))
        self.assertEqual("missing", coverage["status"])
        self.assertEqual("intraday-estimate", coverage["measurementType"])
        self.assertFalse(coverage["judgementEvidenceUsable"])
        self.assertEqual("2026-08-10T09:30:00+09:00", coverage["nextProviderUpdateAt"])
        self.assertNotIn("foreignNetVolume", signal)

    def test_kis_first_estimate_slot_does_not_treat_institution_placeholder_as_flow(self):
        def fake_fetch_json(method, url, headers=None, body=None, query=None, timeout=12):
            path = url.split("?", 1)[0]
            if path.endswith("/oauth2/tokenP"):
                return {"access_token": "token"}
            if path.endswith("/inquire-price"):
                return {"rt_cd": "0", "output": {"stck_prpr": "39000", "acml_vol": "1000"}}
            if path.endswith("/inquire-ccnl"):
                return {"rt_cd": "0", "output": [{"stck_prpr": "39000", "tday_rltv": "88"}]}
            if path.endswith("/investor-trend-estimate"):
                return {"rt_cd": "0", "output2": [{
                    "bsop_hour_gb": "1",
                    "frgn_fake_ntby_qty": "-121000",
                    "orgn_fake_ntby_qty": "0",
                }]}
            if path.endswith("/inquire-asking-price-exp-ccn"):
                return {"rt_cd": "0", "output1": {"total_bidp_rsqn": "1200", "total_askp_rsqn": "900"}}
            raise AssertionError("unexpected KIS path: " + path)

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "key",
                "kisAppSecret": "secret",
                "kisMarketSignalGapSeconds": "0",
            },
            quote_cache=MemoryQuoteCache(),
            fetch_json=fake_fetch_json,
            now_provider=lambda: datetime(2026, 8, 10, 0, 35, tzinfo=timezone.utc),
        )

        signal = provider.fetch_symbol_signal("035720")
        coverage = signal["marketSignalCoverage"]["investor"]

        self.assertEqual(-121000, signal["foreignNetVolume"])
        self.assertNotIn("institutionNetVolume", signal)
        self.assertEqual(["foreignNetVolume"], coverage["fields"])
        self.assertEqual("09:30", coverage["providerUpdateSlot"])

    def test_kis_post_close_uses_current_business_day_final_investor_totals(self):
        calls = []

        def fake_fetch_json(method, url, headers=None, body=None, query=None, timeout=12):
            path = url.split("?", 1)[0]
            calls.append(path)
            if path.endswith("/oauth2/tokenP"):
                return {"access_token": "token"}
            if path.endswith("/inquire-price"):
                return {"rt_cd": "0", "output": {"stck_prpr": "39550", "acml_vol": "1949998"}}
            if path.endswith("/inquire-ccnl"):
                return {"rt_cd": "0", "output": [{"stck_prpr": "39550", "tday_rltv": "70"}]}
            if path.endswith("/inquire-investor"):
                return {"rt_cd": "0", "output": [{
                    "stck_bsop_date": "20260810",
                    "frgn_ntby_qty": "-324494",
                    "orgn_ntby_qty": "74557",
                    "prsn_ntby_qty": "270797",
                }]}
            if path.endswith("/inquire-asking-price-exp-ccn"):
                return {"rt_cd": "0", "output1": {"total_bidp_rsqn": "1200", "total_askp_rsqn": "900"}}
            raise AssertionError("unexpected KIS path: " + path)

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "key",
                "kisAppSecret": "secret",
                "kisMarketSignalGapSeconds": "0",
            },
            quote_cache=MemoryQuoteCache(),
            fetch_json=fake_fetch_json,
            now_provider=lambda: datetime(2026, 8, 10, 7, 0, tzinfo=timezone.utc),
        )

        signal = provider.fetch_symbol_signal("035720")
        coverage = signal["marketSignalCoverage"]["investor"]

        self.assertIn("https://kis.example.test/uapi/domestic-stock/v1/quotations/inquire-investor", calls)
        self.assertFalse(any(path.endswith("/investor-trend-estimate") for path in calls))
        self.assertEqual(-324494, signal["foreignNetVolume"])
        self.assertEqual(74557, signal["institutionNetVolume"])
        self.assertEqual(270797, signal["individualNetVolume"])
        self.assertEqual("daily-final", coverage["measurementType"])
        self.assertEqual("market-close-final", coverage["freshnessStatus"])
        self.assertEqual("2026-08-10T00:00:00+09:00", coverage["sourceAsOf"])
        self.assertTrue(coverage["aiUsableAsStrongEvidence"])

    def test_kis_reuses_estimate_until_next_official_update_slot(self):
        cached = {
            "035720": {
                "foreignNetVolume": -226000,
                "institutionNetVolume": -4000,
                "updatedAt": "2026-08-10T01:01:00Z",
                "marketSignalCoverage": {
                    "investor": {
                        "status": "available",
                        "fields": ["foreignNetVolume", "institutionNetVolume"],
                        "measurementType": "intraday-estimate",
                        "providerUpdateCode": "2",
                        "providerUpdateSlot": "10:00",
                        "providerUpdateCurrent": True,
                        "sourceAsOf": "2026-08-10T10:00:00+09:00",
                        "validUntil": "2026-08-10T11:30:00+09:00",
                        "judgementEvidenceUsable": True,
                    }
                },
            }
        }
        calls = []

        def fake_fetch_json(method, url, headers=None, body=None, query=None, timeout=12):
            path = url.split("?", 1)[0]
            calls.append(path)
            if path.endswith("/oauth2/tokenP"):
                return {"access_token": "token"}
            if path.endswith("/inquire-price"):
                return {"rt_cd": "0", "output": {"stck_prpr": "39000", "acml_vol": "1000"}}
            if path.endswith("/inquire-ccnl"):
                return {"rt_cd": "0", "output": [{"stck_prpr": "39000", "tday_rltv": "88"}]}
            if path.endswith("/inquire-asking-price-exp-ccn"):
                return {"rt_cd": "0", "output1": {"total_bidp_rsqn": "1200", "total_askp_rsqn": "900"}}
            raise AssertionError("estimate should remain cached until the next official slot")

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "key",
                "kisAppSecret": "secret",
                "kisMarketSignalGapSeconds": "0",
            },
            quote_cache=MemoryQuoteCache(cached),
            fetch_json=fake_fetch_json,
            now_provider=lambda: datetime(2026, 8, 10, 1, 30, tzinfo=timezone.utc),
        )

        signal = provider.fetch_symbol_signal("035720")

        self.assertFalse(any(path.endswith("/investor-trend-estimate") for path in calls))
        self.assertEqual(-226000, signal["foreignNetVolume"])
        self.assertEqual(-4000, signal["institutionNetVolume"])

    def test_repeated_kis_investor_totals_remain_important_reference_evidence(self):
        provider = KISMarketSignalProvider(
            settings={"kisMarketSignalUnchangedStaleCount": "3"},
            quote_cache=SimpleNamespace(),
            now_provider=lambda: datetime(2026, 7, 20, 4, 40, tzinfo=timezone.utc),
        )
        investor_values = {
            "foreignBuyVolume": 1550648,
            "foreignSellVolume": 1354573,
            "foreignNetVolume": 196075,
            "institutionBuyVolume": 400313,
            "institutionSellVolume": 197899,
            "institutionNetVolume": 202414,
            "individualBuyVolume": 721925,
            "individualSellVolume": 1102553,
            "individualNetVolume": -380628,
        }
        fields = sorted(investor_values)
        previous = {
            **investor_values,
            "marketSignalCoverage": {
                "investor": {"status": "available", "fields": fields, "unchangedCount": 57}
            },
        }
        signal = {
            **investor_values,
            "currentPrice": 34550,
            "marketSignalCoverage": {
                "investor": {
                    "status": "available",
                    "fields": fields,
                    "realTime": False,
                    "cadence": "rest-reference",
                }
            },
            "quoteStatus": "KIS 투자자별 수급 반영",
            "quoteSource": "KIS Open API",
            "dataQuality": "actual",
        }

        marked = provider.mark_unchanged_stage_health(signal, previous)
        position = provider.merge_position(
            normalize_position({"symbol": "035720", "name": "카카오", "market": "KR", "currency": "KRW"}),
            marked,
        )
        coverage = position.market_signal_coverage["investor"]
        psychology = investor_flow_psychology(position)

        self.assertEqual("available", coverage["status"])
        self.assertEqual(58, coverage["unchangedCount"])
        self.assertEqual("reference-repeat", coverage["freshnessStatus"])
        self.assertIs(True, coverage["judgementEvidenceUsable"])
        self.assertIs(False, coverage["aiUsableAsStrongEvidence"])
        self.assertEqual(196075, position.foreign_net_volume)
        self.assertEqual(202414, position.institution_net_volume)
        self.assertEqual(-380628, position.individual_net_volume)
        self.assertTrue(psychology["available"])
        investor_line = RealtimeMonitor().investor_context_line(position.to_dict())
        self.assertIn("투자자:", investor_line)
        self.assertIn("보유·매매 판단에 반영", investor_line)
        self.assertIn("외국인: 상태 순매수", investor_line)

    def test_current_day_repeated_investor_cache_remains_usable(self):
        provider = KISMarketSignalProvider(
            settings={},
            quote_cache=SimpleNamespace(),
            now_provider=lambda: datetime(2026, 7, 20, 5, 20, tzinfo=timezone.utc),
        )
        signal = {
            "foreignNetVolume": 196075,
            "institutionNetVolume": 202414,
            "individualNetVolume": -380628,
            "marketSignalCoverage": {
                "investor": {
                    "status": "available",
                    "fields": ["foreignNetVolume", "institutionNetVolume", "individualNetVolume"],
                    "freshnessStatus": "reference-repeat",
                    "cadence": "rest-reference",
                    "unchangedCount": 60,
                    "sourceAsOf": "2026-07-20T00:00:00+09:00",
                    "judgementEvidenceUsable": True,
                }
            },
        }

        retained = provider.signal_for_current_session(signal)
        coverage = retained["marketSignalCoverage"]["investor"]

        self.assertEqual("available", coverage["status"])
        self.assertEqual("reference-repeat", coverage["freshnessStatus"])
        self.assertIs(True, coverage["judgementEvidenceUsable"])
        self.assertEqual(196075, retained["foreignNetVolume"])
        self.assertEqual(202414, retained["institutionNetVolume"])

    def test_external_api_error_redacts_key_disclosed_by_provider(self):
        fake_key = "TESTKEY1234567890"
        message = "We have detected your API key as " + fake_key + " and our standard rate limit applies"

        sanitized = sanitize_sensitive_text(message)

        self.assertNotIn(fake_key, sanitized)
        self.assertIn("API key as ***", sanitized)

    def snapshot_with_sources(self) -> AccountSnapshot:
        samsung = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            market_value=1000000,
            current_price=70000,
            quote_source="Toss /api/v1/prices + KIS Open API",
            market_signal_coverage={
                "ccnl": {"status": "available"},
                "orderbook": {"status": "available"},
                "investor": {"status": "available"},
            },
        )
        apple = Position(
            symbol="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            market_value=315,
            current_price=315,
            quote_source="Toss /api/v1/prices",
        )
        external_signals = {
            "fetchedAt": "2026-07-15T00:00:00Z",
            "fxRates": {
                "USDKRW": {
                    "provider": "RuntimeSettings",
                    "sourceType": "fallback_setting",
                    "rate": 1400,
                    "fallbackRate": 1400,
                    "marketProvider": "Alpha Vantage",
                    "marketSourceType": "market_daily",
                }
            },
            "equityQuotes": {"AAPL": {"provider": "Alpha Vantage", "price": 315}},
            "companyOverviews": {"AAPL": {"provider": "Alpha Vantage", "sector": "Technology"}},
            "earningsReports": {"AAPL": {"provider": "Alpha Vantage", "latestQuarter": {}}},
            "yfinanceData": {
                "AAPL": {
                    "provider": "yfinance",
                    "modulesCollected": ["history", "incomeStatement", "optionChains"],
                }
            },
            "cryptoMarkets": {"bitcoin": {"provider": "CoinGecko", "price": 100000}},
            "macro": {"series": {"DGS10": {"provider": "FRED", "value": 4.5}, "DGS2": {"provider": "FRED", "value": 4.1}}},
            "secFilings": {"AAPL": {"provider": "SEC EDGAR", "latestFiling": {"form": "10-Q"}}},
            "dartDisclosures": {"005930": {"provider": "OpenDART", "reportName": "주요사항보고서"}},
            "newsHeadlines": {
                "AAPL": {"provider": "Alpha Vantage", "items": [{"title": "Apple downgrade"}]},
                "005930": {"provider": "GDELT", "items": [{"title": "Samsung chip news"}]},
            },
            "statuses": [
                {"source": "Alpha Vantage", "ok": False, "message": "fx:USDKRW rate limit"},
                {"source": "GDELT News", "ok": True, "message": "doc:005930"},
            ],
        }
        return AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            "2026-07-15T00:00:00Z",
            portfolio_summary([samsung, apple], fx_rates={"USD": 1400, "KRW": 1}),
            [samsung, apple],
            [],
            external_signals=external_signals,
        )

    def test_external_api_source_metadata_lists_all_used_sources(self):
        metadata = external_api_source_metadata(self.snapshot_with_sources())
        text = "\n".join(metadata["externalApiSourceLines"])

        for provider in ["Toss", "KIS", "Alpha Vantage", "yfinance", "CoinGecko", "FRED", "SEC EDGAR", "OpenDART", "GDELT"]:
            self.assertIn(provider, text)
        self.assertIn("RuntimeSettings", text)
        self.assertIn("환율", text)
        self.assertIn("실패", text)

    def test_monitor_stamps_external_api_metadata_but_renderer_hides_block(self):
        snapshot = self.snapshot_with_sources()
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "investmentInsight",
            "main:insight:AAPL",
            "Apple",
            ["상태: 보유 점검", "현재가: $315"],
            "AAPL",
        )
        stamped = RealtimeMonitor().stamp_events(snapshot, [event])[0]
        self.assertIn("externalApiSourceLines", stamped.metadata)

        message = render_notification(
            NotificationTemplate("investmentInsight", "{telegramMessage}"),
            alert_context(stamped),
        )

        self.assertNotIn("사용한 데이터 API", message)
        self.assertNotIn("API 조회 정보", message)
        self.assertNotIn("Alpha Vantage", message)
        self.assertNotIn("CoinGecko", message)
        self.assertNotIn("KIS", message)

    def test_yfinance_provider_collects_data_without_real_network_in_unit_test(self):
        class FakeRecordsFrame:
            def __init__(self, rows):
                self.rows = list(rows)

            @property
            def empty(self):
                return not self.rows

            def reset_index(self):
                return self

            def tail(self, limit):
                return FakeRecordsFrame(self.rows[-int(limit):])

            def to_dict(self, orient="records"):
                return list(self.rows)

        class FakeStatementFrame:
            def __init__(self, rows):
                self.rows = dict(rows)
                first = next(iter(self.rows.values()), {})
                self.columns = list(first.keys())

            @property
            def empty(self):
                return not self.rows

            def iterrows(self):
                for metric, values in self.rows.items():
                    yield metric, values

        class FakeSeries:
            def __init__(self, values):
                self.values = list(values)

            @property
            def empty(self):
                return not self.values

            def tail(self, limit):
                return FakeSeries(self.values[-int(limit):])

            def items(self):
                return list(self.values)

        class FakeTicker:
            history_metadata = {"currency": "USD"}
            fast_info = {"lastPrice": 110.0}
            calendar = {"Earnings Date": "2026-07-30"}
            income_stmt = FakeStatementFrame({"Total Revenue": {"2025-12-31": 1000}})
            quarterly_income_stmt = FakeStatementFrame({"Total Revenue": {"2026-03-31": 260}})
            balance_sheet = FakeStatementFrame({"Total Assets": {"2025-12-31": 3000}})
            quarterly_balance_sheet = FakeStatementFrame({"Total Assets": {"2026-03-31": 3100}})
            cashflow = FakeStatementFrame({"Operating Cash Flow": {"2025-12-31": 400}})
            quarterly_cashflow = FakeStatementFrame({"Operating Cash Flow": {"2026-03-31": 105}})
            earnings_estimate = FakeRecordsFrame([{"period": "0q", "avg": 1.3}])
            revenue_estimate = FakeRecordsFrame([{"period": "0q", "avg": 1000}])
            eps_trend = FakeRecordsFrame([{"period": "0q", "current": 1.3}])
            eps_revisions = FakeRecordsFrame([{"period": "0q", "upLast7days": 2}])
            recommendations_summary = FakeRecordsFrame([{"period": "0m", "buy": 12, "hold": 6}])
            analyst_price_targets = {"mean": 125.0}
            institutional_holders = FakeRecordsFrame([{"Holder": "Fund A", "Shares": 1000}])
            options = ["2026-08-21"]
            actions = FakeRecordsFrame([])
            dividends = FakeSeries([])
            splits = FakeSeries([])
            capital_gains = FakeSeries([])
            funds_data = None
            news = []

            def __init__(self, _symbol):
                pass

            def history(self, **_kwargs):
                return FakeRecordsFrame([
                    {"Date": "2026-07-01", "Close": 100.0, "Volume": 1000},
                    {"Date": "2026-07-02", "Close": 110.0, "Volume": 1300},
                ])

            def get_info(self):
                return {
                    "longName": "Apple Inc.",
                    "quoteType": "EQUITY",
                    "currency": "USD",
                    "sector": "Technology",
                    "marketCap": 3200000000000,
                    "totalRevenue": 391000000000,
                    "currentPrice": 110.0,
                    "targetMeanPrice": 125.0,
                }

            def get_earnings_dates(self, limit=16):
                return FakeRecordsFrame([{
                    "Earnings Date": "2026-07-30",
                    "Reported EPS": 1.65,
                    "EPS Estimate": 1.58,
                }])

            def get_shares_full(self):
                return FakeSeries([("2026-07-01", 15000000000)])

            def option_chain(self, _expiration):
                return SimpleNamespace(
                    calls=FakeRecordsFrame([{"contractSymbol": "AAPL260821C00110000", "openInterest": 100, "volume": 20}]),
                    puts=FakeRecordsFrame([{"contractSymbol": "AAPL260821P00110000", "openInterest": 50, "volume": 10}]),
                )

        previous_module = sys.modules.get("yfinance")
        sys.modules["yfinance"] = SimpleNamespace(Ticker=FakeTicker)
        try:
            provider = ExternalSignalProvider(
                settings={
                    "externalAlphaEnabled": "0",
                    "externalCoinGeckoEnabled": "0",
                    "externalFredEnabled": "0",
                    "externalDartEnabled": "0",
                    "externalSecEnabled": "0",
                    "externalNewsEnabled": "0",
                    "externalFxRateEnabled": "0",
                    "externalYFinanceEnabled": "1",
                    "externalYFinanceMaxSymbols": "1",
                    "externalYFinanceHistoryRows": "2",
                    "externalYFinanceOptionExpirations": "1",
                    "externalApiRateLimitSeconds": "0",
                    "externalApiRetryAttempts": "1",
                },
                cache=object(),
                evidence_store=object(),
                fetch_json=lambda *_args, **_kwargs: {},
                sleep=lambda _: None,
            )
            signals = provider.fetch_signals([
                normalize_position({"symbol": "AAPL", "name": "Apple", "market": "US", "currency": "USD"})
            ])
        finally:
            if previous_module is None:
                sys.modules.pop("yfinance", None)
            else:
                sys.modules["yfinance"] = previous_module

        payload = signals["yfinanceData"]["AAPL"]
        self.assertEqual(110.0, signals["equityQuotes"]["AAPL"]["price"])
        self.assertEqual(125.0, signals["companyOverviews"]["AAPL"]["analystTargetPrice"])
        self.assertEqual(1.65, signals["earningsReports"]["AAPL"]["latestQuarter"]["reportedEPS"])
        self.assertEqual(0.5, payload["optionChains"][0]["summary"]["putCallOpenInterestRatio"])
        self.assertIn("incomeStatement", payload["modulesCollected"])
        self.assertEqual("fresh", payload["freshness"]["status"])
        self.assertEqual("fresh", payload["moduleFreshness"]["optionChains"]["status"])
        self.assertEqual(30, payload["moduleFreshness"]["optionChains"]["maxAgeMinutes"])
        evidence = research_evidence_from_external_signals("AAPL", signals)
        self.assertTrue(any(item.kind == "financial-fact" and item.raw_payload.get("provider") == "yfinance" for item in evidence))

    def test_yfinance_stale_modules_mark_financial_fact_as_partial(self):
        signals = {
            "yfinanceData": {
                "AAPL": {
                    "provider": "yfinance",
                    "querySymbol": "AAPL",
                    "collectedAt": "2000-01-01T00:00:00Z",
                    "modulesCollected": ["quote", "optionChains", "incomeStatement"],
                    "quote": {"price": 110.0},
                    "options": ["2026-08-21"],
                    "optionChains": [{"summary": {"putCallOpenInterestRatio": 0.5}}],
                    "incomeStatement": [{"metric": "Total Revenue", "values": {"2025-12-31": 1000}}],
                    "freshness": {
                        "status": "stale",
                        "reason": "quote 기준 30분 초과",
                        "staleModules": ["quote", "optionChains"],
                    },
                    "moduleFreshness": {
                        "quote": {"status": "stale", "maxAgeMinutes": 30},
                        "optionChains": {"status": "stale", "maxAgeMinutes": 30},
                        "incomeStatement": {"status": "fresh", "maxAgeMinutes": 129600},
                    },
                }
            }
        }

        evidence = research_evidence_from_external_signals("AAPL", signals)
        item = next(row for row in evidence if row.kind == "financial-fact")

        self.assertEqual("limited", item.source_trust_state)
        self.assertEqual("partial", item.data_state)
        self.assertEqual("stale", item.raw_payload["freshness"]["status"])
        self.assertIn("stale-yfinance-modules", item.raw_payload["dataQualityRisk"])

    def test_yfinance_missing_fundamentals_keeps_quote_without_error_status(self):
        class FakeRecordsFrame:
            def __init__(self, rows):
                self.rows = list(rows)

            @property
            def empty(self):
                return not self.rows

            def reset_index(self):
                return self

            def tail(self, limit):
                return FakeRecordsFrame(self.rows[-int(limit):])

            def to_dict(self, orient="records"):
                return list(self.rows)

        class FakeTicker:
            options = []

            def __init__(self, _symbol):
                pass

            def history(self, **_kwargs):
                return FakeRecordsFrame([
                    {"Date": "2026-07-01", "Close": 94.0, "Volume": 1000},
                    {"Date": "2026-07-02", "Close": 98.0, "Volume": 1300},
                ])

            def get_info(self):
                raise RuntimeError(
                    'HTTP Error 404: {"quoteSummary":{"result":null,"error":{"code":"Not Found",'
                    '"description":"No fundamentals data found for symbol: MSTR"}}}'
                )

        previous_module = sys.modules.get("yfinance")
        sys.modules["yfinance"] = SimpleNamespace(Ticker=FakeTicker)
        try:
            provider = ExternalSignalProvider(
                settings={
                    "externalAlphaEnabled": "0",
                    "externalCoinGeckoEnabled": "0",
                    "externalFredEnabled": "0",
                    "externalDartEnabled": "0",
                    "externalSecEnabled": "0",
                    "externalNewsEnabled": "0",
                    "externalFxRateEnabled": "0",
                    "externalYFinanceEnabled": "1",
                    "externalYFinanceMaxSymbols": "1",
                    "externalYFinanceHistoryRows": "2",
                    "externalYFinanceOptionExpirations": "0",
                    "externalYFinanceNewsLimit": "0",
                    "externalApiRateLimitSeconds": "0",
                    "externalApiRetryAttempts": "1",
                },
                cache=object(),
                evidence_store=object(),
                fetch_json=lambda *_args, **_kwargs: {},
                sleep=lambda _: None,
            )
            signals = provider.fetch_signals([
                normalize_position({"symbol": "MSTR", "name": "Strategy", "market": "US", "currency": "USD"})
            ])
        finally:
            if previous_module is None:
                sys.modules.pop("yfinance", None)
            else:
                sys.modules["yfinance"] = previous_module

        payload = signals["yfinanceData"]["MSTR"]
        self.assertEqual(98.0, signals["equityQuotes"]["MSTR"]["price"])
        self.assertNotIn("info", payload)
        self.assertNotIn("errors", payload)
        self.assertEqual("expected-missing", payload["dataQualityNotes"][0]["status"])
        self.assertEqual("fundamentals-not-available", payload["dataQualityNotes"][0]["reason"])
        self.assertFalse([
            item for item in signals["statuses"]
            if item.get("source") == "yfinance" and not item.get("ok")
        ])

    def test_ai_rewritten_message_hides_api_sources_from_alert_body(self):
        snapshot = self.snapshot_with_sources()
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "investmentInsight",
            "main:insight:AAPL",
            "Apple",
            ["현재가: $315", "수익률: +0.5%"],
            "AAPL",
        )
        stamped = RealtimeMonitor().stamp_events(snapshot, [event])[0]
        context = alert_context(stamped)
        context["telegramMessage"] = execution_telegram_message(
            context,
            NotificationAIValidatedResponse(
                action="HOLD",
                action_label="보유",
                validation_state="conditional",
                data_state="partial",
                review_level="check",
                summary="보유 판단입니다.",
                evidence=["가격 흐름을 확인했습니다."],
                opinion="바로 매매보다 확인이 우선입니다.",
            ),
        )

        message = render_notification(NotificationTemplate("investmentInsight", "{telegramMessage}"), context)

        self.assertNotIn("API 조회 정보", message)
        self.assertNotIn("사용한 데이터 API", message)
        self.assertNotIn("Alpha Vantage", message)
        self.assertNotIn("CoinGecko", message)
        self.assertNotIn("KIS", message)


if __name__ == "__main__":
    unittest.main()
