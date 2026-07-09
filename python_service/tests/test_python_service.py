import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.admin_preview import admin_preview_config, write_admin_preview
from digital_twin.application.account_service import AccountApplicationService
from digital_twin.application.flow_lens_service import FlowLensService
from digital_twin.application.market_data_collection_service import MARKET_DATA_ACCOUNT_ID, MarketDataCollectionRunner
from digital_twin.application.model_review_service import ModelReviewRunner
from digital_twin.application.news_collection_service import NewsCollectionRunner
from digital_twin.application.monitoring_service import MonitorRunner as ApplicationMonitorRunner
from digital_twin.application.notification_service import CompositeNotificationContextEnricher, DisclosureAnalysisNotificationEnricher, NotificationAIValidatedGateEnricher, NotificationAIOpinionEnricher, NotificationHoldingSnapshotEnricher, NotificationQueueRunner
from digital_twin.application.symbol_universe_service import SymbolUniverseService, seed_symbol
from digital_twin.cli import build_handoff_message
from digital_twin.cli import preserve_existing_secrets
from digital_twin.cli import build_parser
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.external_signal_quality import attach_external_signal_quality
from digital_twin.domain.investment_research import NewsCollectionTarget, build_active_investment_opinion, research_evidence_from_facts
from digital_twin.domain.market_data import normalize_position, technical_indicators_from_candles
from digital_twin.domain.message_types import DEFAULT_ALERT_RULES, DEFAULT_CADENCE, MESSAGE_TYPE_EMOJIS, MESSAGE_TYPE_LABELS, public_message_catalog
from digital_twin.domain.ontology import OntologyEntity, OntologyRelation, abox_properties, apply_relation_driven_opinions, build_portfolio_ontology, entity_id
from digital_twin.domain.ontology_rules import decision_action_group_for_label, evaluate_position_relation_rules, prompt_template_for_message_type
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.strategy import SafeFormula, StrategyModel, decisions_for_positions
from digital_twin.domain.events import ACCOUNT_SAVED, MARKET_DATA_COLLECTED, MONITORING_ALERTS_DETECTED, MONITORING_CYCLE_COMPLETED, MONITORING_SNAPSHOT_COLLECTED, alerts_detected_event, monitoring_cycle_completed_event, snapshot_collected_event
from digital_twin.domain.monitoring import RealtimeMonitor
from digital_twin.domain.model_review import ModelReviewJob, build_model_review_prompt, local_model_review
from digital_twin.domain.disclosure_analysis import DisclosureAnalysisResult, local_disclosure_analysis
from digital_twin.domain.notification_templates import NotificationTemplate, alert_context, render_notification
from digital_twin.domain.notification_rules import apply_market_hours_rule, apply_state_cooldown_rule, default_notification_rule, evaluate_notification_rule
from digital_twin.domain.ontology_insights import build_investment_insight_events
from digital_twin.domain.notification_ai import build_notification_ai_opinion
from digital_twin.domain.notification_ai_gate import build_notification_ai_gate_prompt, context_with_validated_ai_response, validated_response_from_payload
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.parsing import parse_assignments
from digital_twin.domain.portfolio import AccountSnapshot, AlertEvent, Position, utc_now_iso
from digital_twin.infrastructure.event_bus import EventBus, JsonEventLog
from digital_twin.infrastructure.external_signals import ExternalSignalProvider
from digital_twin.infrastructure.json_monitor_state import MonitorStore
from digital_twin.infrastructure.kis_market_signals import KIS_CACHE_ACCOUNT_ID, KIS_CACHE_PROVIDER, KISMarketSignalProvider
from digital_twin.infrastructure.model_review_queue import ModelReviewEnqueuer, ModelReviewJobStore
from digital_twin.infrastructure.mock_market import mock_market_payload
from digital_twin.infrastructure.neo4j_ontology import Neo4jOntologyGraphRepository, NullOntologyGraphRepository, safe_relation_type
from digital_twin.infrastructure.news_sources import NewsSourceGateway
from digital_twin.infrastructure.notifications import TelegramNotifier, send_events
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
from digital_twin.infrastructure.service_factory import flow_lens_snapshot
from digital_twin.infrastructure.settings import runtime_settings, save_runtime_settings
from digital_twin.infrastructure.sqlite_model_review import SQLiteModelReviewJobStore
from digital_twin.infrastructure.sqlite_monitoring import SQLiteEventLog, SQLiteExternalSignalCache, SQLiteMarketQuoteCache, SQLiteMonitoringCycleRecorder, SQLiteMonitorStore, SQLiteOntologyQualitySampleStore, SQLiteResearchEvidenceStore
from digital_twin.infrastructure.sqlite_notifications import SQLiteNotificationJobStore, SQLiteNotificationRuleStore, SQLiteNotificationTemplateStore
from digital_twin.infrastructure.sqlite_runtime import SQLiteAppStore, SQLiteRuntimeSettingsStore
from digital_twin.infrastructure.sqlite_symbols import SQLiteSymbolUniverseStore
from digital_twin.infrastructure.symbol_sources import RemoteSymbolSourceGateway, parse_krx_kind_table, parse_nasdaq_listed
from digital_twin.infrastructure.sqlite_accounts import AccountRegistry
from digital_twin.infrastructure.toss_snapshots import TossProvider, account_cash_amount, normalize_price_items, select_account
from digital_twin.infrastructure.web_server import list_notification_rules_payload, notification_jobs_payload, notification_schedules_payload, notification_template_test_payload, realtime_status_payload, save_notification_rule_payload, settings_status_payload
from digital_twin.scheduler import MonitorRunner


class PythonServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env_patch = mock.patch.dict(os.environ, {
            "DIGITAL_TWIN_DATA_DIR": self.temp.name,
            "SETTINGS_PATH": str(Path(self.temp.name) / "settings.json"),
        }, clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def fresh_data_freshness(self, source: str = "unit-test", max_age_minutes: int = 10):
        return {
            "source": source,
            "status": "fresh",
            "reason": "신선도 기준 통과",
            "ageMinutes": 0,
            "maxAgeMinutes": max_age_minutes,
            "sourceFetchedAt": utc_now_iso(),
            "checkedAt": utc_now_iso(),
        }

    def mark_event_fresh(self, event: AlertEvent, source: str = "unit-test") -> AlertEvent:
        event.metadata = dict(event.metadata or {})
        event.metadata.setdefault("dataFreshness", self.fresh_data_freshness(source))
        return event

    def insight_event(self, events, symbol: str = ""):
        for event in events:
            if event.rule != "investmentInsight":
                continue
            if not symbol or str(event.symbol or "").upper() == symbol.upper():
                return event
        raise AssertionError("investmentInsight event not found")

    def insight_source_rules(self, event):
        return [
            str(item.get("rule") or "")
            for item in event.metadata.get("sourceAlertEvents", [])
            if isinstance(item, dict)
        ]

    def insight_source_message(self, event, rule: str) -> str:
        lines = []
        for item in event.metadata.get("sourceAlertEvents", []):
            if not isinstance(item, dict) or item.get("rule") != rule:
                continue
            lines.extend(str(line or "") for line in item.get("lines", []))
            lines.extend(str(line or "") for line in item.get("criteria", []))
        return "\n".join(lines)

    def insight_active_rule_ids(self, event):
        contexts = event.metadata.get("ontologyRelationContext")
        if isinstance(contexts, dict):
            contexts = [contexts]
        ids = []
        for context in contexts or []:
            if not isinstance(context, dict):
                continue
            for item in context.get("activeRules") or []:
                if not isinstance(item, dict):
                    continue
                ids.append(item.get("ruleId") or item.get("rule_id"))
        return ids

    def test_account_registry_supports_multiple_accounts(self):
        registry = AccountRegistry()
        first = AccountConfig("main", "메인", "toss", "https://example.test", "id1", "secret1", "1", ["AAPL"])
        second = AccountConfig("ira", "장기", "toss", "https://example.test", "id2", "secret2", "2", ["NVDA"])
        registry.upsert(first)
        registry.upsert(second)

        accounts = registry.load_all()

        self.assertEqual(["main", "ira"], [item.account_id for item in accounts])
        self.assertTrue((Path(self.temp.name) / "service.db").exists())
        self.assertTrue(accounts[0].client_id)
        self.assertTrue(accounts[0].quiet_hours_enabled)
        self.assertEqual("22:00", accounts[0].quiet_hours_start)
        self.assertEqual("05:00", accounts[0].quiet_hours_end)

    def test_account_registry_persists_quiet_hours_for_many_accounts(self):
        registry = AccountRegistry()
        for index in range(60):
            registry.upsert(AccountConfig(
                "acct" + str(index).zfill(2),
                "계정" + str(index),
                "toss",
                "https://example.test",
                "id" + str(index),
                "secret" + str(index),
                str(index),
                ["AAPL"],
                quiet_hours_enabled=index % 2 == 0,
                quiet_hours_start="21:30",
                quiet_hours_end="06:15",
                quiet_hours_timezone="Asia/Seoul",
            ))

        accounts = registry.load_all()

        self.assertEqual(60, len(accounts))
        self.assertEqual(["acct00", "acct01", "acct02"], [item.account_id for item in accounts[:3]])
        self.assertFalse(accounts[1].quiet_hours_enabled)
        self.assertEqual("21:30", accounts[0].quiet_hours_start)
        self.assertEqual("06:15", accounts[0].quiet_hours_end)

    def test_toss_account_selection_uses_configured_account_seq(self):
        accounts = [
            {"accountSeq": "1", "orderableAmount": "0"},
            {"accountSeq": "2", "withdrawableAmount": {"amount": {"krw": "350000"}}},
        ]

        selected = select_account(accounts, "2")

        self.assertEqual("2", selected["accountSeq"])
        self.assertEqual(350000.0, account_cash_amount(selected))

    def test_toss_account_cash_accepts_alternate_nested_fields(self):
        account = {
            "accountNo": "123",
            "cashBalances": [
                {"currency": "KRW", "availableOrderAmount": {"krw": "125000"}},
            ],
        }

        self.assertEqual(125000.0, account_cash_amount(account))

    def test_toss_prices_are_primary_quote_source_and_cached(self):
        calls = []
        db_path = Path(self.temp.name) / "service.db"
        account = AccountConfig("main", "메인", "toss", "https://example.test", "id", "secret", "1", ["TSLA"])

        def candles(price):
            return {
                "result": {
                    "candles": [
                        {
                            "timestamp": "2026-01-" + str(index + 1).zfill(2) + "T09:00:00+09:00",
                            "closePrice": str(price + index),
                            "volume": str(1000 + index),
                            "currency": "USD",
                        }
                        for index in range(28)
                    ]
                }
            }

        def fake_http_json(method, url, headers, body=None, timeout=12):
            calls.append(url)
            path = urllib.parse.urlparse(url).path
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            if path == "/oauth2/token":
                return {"access_token": "token"}
            if path == "/api/v1/accounts":
                return {"result": [{"accountSeq": "1", "currency": "KRW", "orderableAmount": "1000"}]}
            if path == "/api/v1/buying-power":
                return {"result": {"cashBuyingPower": "0"}}
            if path == "/api/v1/holdings":
                return {"result": {"holdings": [{
                    "symbol": "AAPL",
                    "name": "Apple",
                    "market": "US",
                    "currency": "USD",
                    "quantity": "2",
                    "averagePrice": "90",
                }]}}
            if path == "/api/v1/prices":
                symbols = set(",".join(query.get("symbols", [])).split(","))
                result = []
                if "AAPL" in symbols:
                    result.append({"symbol": "AAPL", "lastPrice": "101", "currency": "USD", "timestamp": "2026-07-03T09:30:00+09:00"})
                if "TSLA" in symbols:
                    result.append({"symbol": "TSLA", "lastPrice": "202", "currency": "USD", "timestamp": "2026-07-03T09:30:00+09:00"})
                return {"result": result}
            if path == "/api/v1/candles":
                symbol = (query.get("symbol") or [""])[0]
                return candles(50 if symbol == "AAPL" else 150)
            return {}

        provider = TossProvider(account, quote_cache=SQLiteMarketQuoteCache(db_path))
        with mock.patch("digital_twin.infrastructure.toss_snapshots.http_json", side_effect=fake_http_json):
            mode, status, positions, cash, currency, watchlist = provider.fetch_positions()

        aapl = next(item for item in positions if item.symbol == "AAPL")
        tsla = next(item for item in watchlist if item.symbol == "TSLA")
        cached = SQLiteMarketQuoteCache(db_path).load("toss", "main", "TSLA")

        self.assertEqual("live", mode)
        self.assertEqual("토스 계좌 동기화", status)
        self.assertEqual(101, aapl.current_price)
        self.assertEqual(202, tsla.current_price)
        self.assertEqual("watchlist", tsla.source)
        self.assertEqual("Toss /api/v1/prices", tsla.quote_source)
        self.assertEqual("actual", tsla.data_quality)
        self.assertGreater(tsla.ma20, 0)
        self.assertEqual(202, cached["currentPrice"])
        self.assertTrue(any("/api/v1/prices" in url for url in calls))

    def test_toss_quote_cache_fills_watchlist_when_market_data_is_rate_limited(self):
        db_path = Path(self.temp.name) / "service.db"
        cache = SQLiteMarketQuoteCache(db_path)
        cache.save("toss", "main", "TSLA", {
            "symbol": "TSLA",
            "name": "Tesla",
            "market": "US",
            "currency": "USD",
            "currentPrice": 250,
            "quoteSource": "Toss /api/v1/prices",
            "quoteStatus": "토스 prices 반영",
            "dataQuality": "actual",
            "ma20": 240,
            "ma60": 220,
            "updatedAt": "2026-07-03T00:00:00Z",
        })
        account = AccountConfig("main", "메인", "toss", "https://example.test", "id", "secret", "1", ["TSLA"])

        def fake_http_json(method, url, headers, body=None, timeout=12):
            path = urllib.parse.urlparse(url).path
            if path == "/oauth2/token":
                return {"access_token": "token"}
            if path == "/api/v1/accounts":
                return {"result": [{"accountSeq": "1", "currency": "KRW", "orderableAmount": "1000"}]}
            if path == "/api/v1/buying-power":
                return {"result": {"cashBuyingPower": "0"}}
            if path == "/api/v1/holdings":
                return {"result": {"holdings": []}}
            if path in {"/api/v1/prices", "/api/v1/candles"}:
                raise urllib.error.URLError("rate limit")
            return {}

        provider = TossProvider(account, quote_cache=cache)
        with mock.patch("digital_twin.infrastructure.toss_snapshots.http_json", side_effect=fake_http_json):
            mode, _, _, _, _, watchlist = provider.fetch_positions()

        tsla = next(item for item in watchlist if item.symbol == "TSLA")

        self.assertEqual("live", mode)
        self.assertEqual(250, tsla.current_price)
        self.assertEqual(240, tsla.ma20)
        self.assertEqual("watchlist", tsla.source)
        self.assertEqual("cached", tsla.data_quality)
        self.assertEqual("마지막 저장 시세", tsla.quote_status)

    def test_kis_market_signal_provider_enriches_kr_positions(self):
        db_path = Path(self.temp.name) / "service.db"
        calls = []

        def fake_fetch_json(method, url, headers, body=None, query=None, timeout=12):
            path = urllib.parse.urlparse(url).path
            calls.append((method, path, headers.get("tr_id"), query))
            if path == "/oauth2/tokenP":
                self.assertEqual("POST", method)
                return {"access_token": "kis-token", "expires_in": 86400}
            self.assertEqual("Bearer kis-token", headers.get("authorization"))
            self.assertEqual({"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}, query)
            if path.endswith("/inquire-price"):
                return {"rt_cd": "0", "output": {
                    "stck_prpr": "72000",
                    "prdy_ctrt": "1.25",
                    "acml_vol": "1000000",
                    "acml_tr_pbmn": "72000000000",
                    "prdy_vrss_vol_rate": "185.0",
                    "frgn_ntby_qty": "900",
                }}
            if path.endswith("/inquire-ccnl"):
                return {"rt_cd": "0", "output": [
                    {"stck_prpr": "72000", "tday_rltv": "118.5", "cntg_vol": "120", "prdy_ctrt": "1.25"},
                    {"stck_prpr": "71900", "tday_rltv": "117.0", "cntg_vol": "-80", "prdy_ctrt": "1.11"},
                ]}
            if path.endswith("/inquire-investor"):
                return {"rt_cd": "0", "output": [
                    {
                        "prsn_ntby_qty": "",
                        "frgn_ntby_qty": "",
                        "orgn_ntby_qty": "",
                        "prsn_shnu_vol": "",
                        "frgn_shnu_vol": "",
                        "orgn_shnu_vol": "",
                        "prsn_seln_vol": "",
                        "frgn_seln_vol": "",
                        "orgn_seln_vol": "",
                    },
                    {
                        "prsn_ntby_qty": "-400",
                        "frgn_ntby_qty": "700",
                        "orgn_ntby_qty": "300",
                        "prsn_shnu_vol": "2000",
                        "frgn_shnu_vol": "1300",
                        "orgn_shnu_vol": "900",
                        "prsn_seln_vol": "2400",
                        "frgn_seln_vol": "600",
                        "orgn_seln_vol": "600",
                    },
                ]}
            if path.endswith("/inquire-asking-price-exp-ccn"):
                return {"rt_cd": "0", "output1": {
                    "total_bidp_rsqn": "9000",
                    "total_askp_rsqn": "3000",
                }}
            return {"rt_cd": "1", "msg1": "unexpected path"}

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "app-key",
                "kisAppSecret": "app-secret",
                "kisMarketSignalsEnabled": "1",
                "kisMarketSignalGapSeconds": "0",
                "kisMarketSignalCacheMinutes": "10",
                "kisMarketSignalMaxSymbols": "5",
                "externalApiRetryAttempts": "1",
            },
            quote_cache=SQLiteMarketQuoteCache(db_path),
            fetch_json=fake_fetch_json,
            sleep=lambda _seconds: None,
        )
        samsung = normalize_position({"symbol": "005930", "name": "삼성전자", "market": "KR", "currency": "KRW", "quantity": "2"})
        apple = normalize_position({"symbol": "AAPL", "name": "Apple", "market": "US", "currency": "USD"})

        positions, watchlist = provider.enrich_collections([samsung, apple], [])
        enriched = next(item for item in positions if item.symbol == "005930")
        untouched = next(item for item in positions if item.symbol == "AAPL")
        cached = SQLiteMarketQuoteCache(db_path).load(KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID, "005930")

        self.assertEqual([], watchlist)
        self.assertEqual(72000, enriched.current_price)
        self.assertEqual(144000, enriched.market_value)
        self.assertEqual(1.25, enriched.change_rate)
        self.assertEqual(118.5, enriched.trade_strength)
        self.assertEqual(120, enriched.buy_volume)
        self.assertEqual(80, enriched.sell_volume)
        self.assertEqual(9000, enriched.orderbook_bid_volume)
        self.assertEqual(3000, enriched.orderbook_ask_volume)
        self.assertEqual(50, enriched.bid_ask_imbalance)
        self.assertEqual(1.85, enriched.volume_ratio)
        self.assertEqual(700, enriched.foreign_net_volume)
        self.assertEqual(1300, enriched.foreign_buy_volume)
        self.assertEqual(600, enriched.foreign_sell_volume)
        self.assertEqual(300, enriched.institution_net_volume)
        self.assertEqual(-400, enriched.individual_net_volume)
        self.assertIn("KIS Open API", enriched.quote_source)
        self.assertEqual("actual", enriched.data_quality)
        self.assertEqual(0, untouched.current_price)
        self.assertEqual(700, cached["foreignNetVolume"])
        self.assertEqual(50, cached["bidAskImbalance"])
        self.assertEqual("available", enriched.market_signal_coverage["investor"]["status"])
        self.assertEqual("available", cached["marketSignalCoverage"]["investor"]["status"])
        self.assertEqual(["/oauth2/tokenP", "/uapi/domestic-stock/v1/quotations/inquire-price", "/uapi/domestic-stock/v1/quotations/inquire-ccnl", "/uapi/domestic-stock/v1/quotations/inquire-investor", "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"], [item[1] for item in calls])

    def test_kis_market_signal_provider_does_not_treat_price_foreign_zero_as_investor_flow(self):
        calls = []

        def fake_fetch_json(method, url, headers=None, body=None, query=None, timeout=12):
            path = urllib.parse.urlparse(url).path
            calls.append(path)
            if path.endswith("/oauth2/tokenP"):
                return {"access_token": "kis-token"}
            if path.endswith("/inquire-price"):
                return {"rt_cd": "0", "output": {"stck_prpr": "197200", "acml_vol": "0", "frgn_ntby_qty": "0"}}
            if path.endswith("/inquire-ccnl"):
                return {"rt_cd": "0", "output": []}
            if path.endswith("/inquire-investor"):
                return {"rt_cd": "0", "output": []}
            if path.endswith("/inquire-asking-price-exp-ccn"):
                return {"rt_cd": "0", "output1": {}}
            return {"rt_cd": "0", "output": {}}

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "app-key",
                "kisAppSecret": "app-secret",
                "kisMarketSignalsEnabled": "1",
                "kisMarketSignalGapSeconds": "0",
                "externalApiRetryAttempts": "1",
            },
            quote_cache=SQLiteMarketQuoteCache(Path(self.temp.name) / "service.db"),
            fetch_json=fake_fetch_json,
            sleep=lambda _seconds: None,
        )

        signal = provider.fetch_symbol_signal("035420")

        self.assertIn("현재가", signal["quoteStatus"])
        self.assertNotIn("투자자별 수급", signal["quoteStatus"])
        self.assertEqual("empty", signal["marketSignalCoverage"]["investor"]["status"])
        self.assertFalse(provider.is_signal_complete(signal))
        self.assertIn("/uapi/domestic-stock/v1/quotations/inquire-investor", calls)

    def test_kis_market_signal_provider_uses_fresh_cache_without_token(self):
        db_path = Path(self.temp.name) / "service.db"
        cache = SQLiteMarketQuoteCache(db_path)
        cache.save(KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID, "005930", {
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 71000,
            "tradeStrength": 109,
            "foreignNetVolume": 500,
            "institutionNetVolume": -120,
            "individualNetVolume": -380,
            "quoteSource": "KIS Open API",
            "quoteStatus": "KIS 현재가/수급 반영",
            "quoteMessage": "cached",
            "dataQuality": "actual",
            "updatedAt": utc_now_iso(),
        })

        def fail_fetch_json(*_args, **_kwargs):
            raise AssertionError("fresh KIS cache should avoid live token and quote calls")

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "app-key",
                "kisAppSecret": "app-secret",
                "kisMarketSignalsEnabled": "1",
                "kisMarketSignalCacheMinutes": "10",
                "kisMarketSignalPreferLiveDuringMarketHours": "0",
            },
            quote_cache=cache,
            fetch_json=fail_fetch_json,
        )
        samsung = normalize_position({"symbol": "005930", "name": "삼성전자", "market": "KR", "currency": "KRW"})

        positions, _watchlist = provider.enrich_collections([samsung], [])
        enriched = positions[0]

        self.assertEqual(71000, enriched.current_price)
        self.assertEqual(109, enriched.trade_strength)
        self.assertEqual(500, enriched.foreign_net_volume)
        self.assertEqual(-120, enriched.institution_net_volume)
        self.assertEqual(-380, enriched.individual_net_volume)
        self.assertEqual(1, provider.diagnostics["cached"])
        self.assertEqual(0, provider.diagnostics["live"])

    def test_kis_cached_fallback_marks_position_mixed_quality(self):
        db_path = Path(self.temp.name) / "service.db"
        cache = SQLiteMarketQuoteCache(db_path)
        cache.save(KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID, "005930", {
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 71000,
            "tradeStrength": 109,
            "quoteSource": "KIS Open API",
            "quoteStatus": "KIS 현재가 반영",
            "quoteMessage": "cached",
            "dataQuality": "actual",
            "updatedAt": utc_now_iso(),
        })

        def fail_fetch_json(*_args, **_kwargs):
            raise RuntimeError("KIS unavailable")

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "app-key",
                "kisAppSecret": "app-secret",
                "kisMarketSignalsEnabled": "1",
                "kisMarketSignalCacheMinutes": "10",
                "externalApiRetryAttempts": "1",
            },
            quote_cache=cache,
            fetch_json=fail_fetch_json,
            sleep=lambda _seconds: None,
        )
        samsung = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "dataQuality": "actual",
        })

        positions, _watchlist = provider.enrich_collections([samsung], [])

        self.assertEqual(71000, positions[0].current_price)
        self.assertEqual("mixed", positions[0].data_quality)
        self.assertEqual(1, provider.diagnostics["cached"])

    def test_kis_market_signal_provider_refreshes_partial_fresh_cache(self):
        db_path = Path(self.temp.name) / "service.db"
        cache = SQLiteMarketQuoteCache(db_path)
        cache.save(KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID, "005930", {
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 71000,
            "quoteSource": "KIS Open API",
            "quoteStatus": "KIS 현재가 반영",
            "quoteMessage": "partial",
            "dataQuality": "actual",
            "updatedAt": utc_now_iso(),
        })
        calls = []

        def fake_fetch_json(method, url, headers=None, body=None, query=None, timeout=12):
            path = urllib.parse.urlparse(url).path
            calls.append(path)
            if path.endswith("/oauth2/tokenP"):
                return {"access_token": "kis-token"}
            if path.endswith("/inquire-price"):
                return {"rt_cd": "0", "output": {"stck_prpr": "72000", "acml_vol": "1000"}}
            if path.endswith("/inquire-ccnl"):
                return {"rt_cd": "0", "output": [{"stck_prpr": "72000", "tday_rltv": "118.5", "total_shnu_qty": "900", "total_seln_qty": "700"}]}
            if path.endswith("/inquire-investor"):
                return {"rt_cd": "0", "output": {
                    "frgn_ntby_qty": "700",
                    "orgn_ntby_qty": "300",
                    "prsn_ntby_qty": "-400",
                    "frgn_ntby_tr_pbmn": "210000000",
                    "orgn_ntby_tr_pbmn": "90000000",
                    "prsn_ntby_tr_pbmn": "-120000000",
                }}
            if path.endswith("/inquire-asking-price-exp-ccn"):
                return {"rt_cd": "0", "output": {}}
            return {"rt_cd": "0", "output": {}}

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "app-key",
                "kisAppSecret": "app-secret",
                "kisMarketSignalsEnabled": "1",
                "kisMarketSignalCacheMinutes": "10",
                "kisMarketSignalGapSeconds": "0",
                "externalApiRetryAttempts": "1",
            },
            quote_cache=cache,
            fetch_json=fake_fetch_json,
            sleep=lambda _seconds: None,
        )
        samsung = normalize_position({"symbol": "005930", "name": "삼성전자", "market": "KR", "currency": "KRW"})

        positions, _watchlist = provider.enrich_collections([samsung], [])

        self.assertEqual(118.5, positions[0].trade_strength)
        self.assertEqual(900, positions[0].buy_volume)
        self.assertEqual(700, positions[0].sell_volume)
        self.assertEqual(700, positions[0].foreign_net_volume)
        self.assertEqual(300, positions[0].institution_net_volume)
        self.assertEqual(-400, positions[0].individual_net_volume)
        self.assertEqual(210000000, positions[0].foreign_net_amount)
        self.assertEqual(90000000, positions[0].institution_net_amount)
        self.assertEqual(-120000000, positions[0].individual_net_amount)
        self.assertEqual(1, provider.diagnostics["partialCached"])
        self.assertEqual(1, provider.diagnostics["live"])
        self.assertIn("/uapi/domestic-stock/v1/quotations/inquire-ccnl", calls)

    def test_kis_market_signal_provider_refreshes_fresh_cache_without_investor_flow(self):
        db_path = Path(self.temp.name) / "service.db"
        cache = SQLiteMarketQuoteCache(db_path)
        cache.save(KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID, "035420", {
            "symbol": "035420",
            "name": "NAVER",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 200000,
            "tradeStrength": 87.3,
            "orderbookBidVolume": 1178,
            "orderbookAskVolume": 883,
            "bidAskImbalance": 14.3,
            "quoteSource": "KIS Open API",
            "quoteStatus": "KIS 현재가, 체결강도, 호가 잔량 반영",
            "quoteMessage": "partial",
            "dataQuality": "actual",
            "updatedAt": utc_now_iso(),
        })
        calls = []

        def fake_fetch_json(method, url, headers=None, body=None, query=None, timeout=12):
            path = urllib.parse.urlparse(url).path
            calls.append(path)
            if path.endswith("/oauth2/tokenP"):
                return {"access_token": "kis-token"}
            if path.endswith("/inquire-price"):
                return {"rt_cd": "0", "output": {"stck_prpr": "201000", "acml_vol": "1026999"}}
            if path.endswith("/inquire-ccnl"):
                return {"rt_cd": "0", "output": [{"stck_prpr": "201000", "tday_rltv": "88.3", "cntg_vol": "100"}]}
            if path.endswith("/inquire-investor"):
                return {"rt_cd": "0", "output": [{
                    "frgn_ntby_qty": "243601",
                    "orgn_ntby_qty": "67401",
                    "prsn_ntby_qty": "-304684",
                }]}
            if path.endswith("/inquire-asking-price-exp-ccn"):
                return {"rt_cd": "0", "output1": {"total_bidp_rsqn": "1200", "total_askp_rsqn": "900"}}
            return {"rt_cd": "0", "output": {}}

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "app-key",
                "kisAppSecret": "app-secret",
                "kisMarketSignalsEnabled": "1",
                "kisMarketSignalCacheMinutes": "10",
                "kisMarketSignalGapSeconds": "0",
                "externalApiRetryAttempts": "1",
            },
            quote_cache=cache,
            fetch_json=fake_fetch_json,
            sleep=lambda _seconds: None,
        )
        naver = normalize_position({"symbol": "035420", "name": "NAVER", "market": "KR", "currency": "KRW"})

        positions, _watchlist = provider.enrich_collections([naver], [])

        self.assertEqual(243601, positions[0].foreign_net_volume)
        self.assertEqual(67401, positions[0].institution_net_volume)
        self.assertEqual(-304684, positions[0].individual_net_volume)
        self.assertEqual(1, provider.diagnostics["partialCached"])
        self.assertEqual(1, provider.diagnostics["live"])
        self.assertIn("/uapi/domestic-stock/v1/quotations/inquire-investor", calls)

    def test_kis_market_signal_provider_prefers_live_during_market_hours(self):
        db_path = Path(self.temp.name) / "service.db"
        cache = SQLiteMarketQuoteCache(db_path)
        cache.save(KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID, "035420", {
            "symbol": "035420",
            "name": "NAVER",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 200000,
            "tradeStrength": 87.3,
            "foreignNetVolume": 1,
            "institutionNetVolume": 2,
            "individualNetVolume": -3,
            "quoteSource": "KIS Open API",
            "quoteStatus": "KIS 현재가, 체결강도, 투자자별 수급 반영",
            "quoteMessage": "cached",
            "dataQuality": "actual",
            "updatedAt": "2026-07-07T01:55:00Z",
        })
        calls = []

        def fake_fetch_json(method, url, headers=None, body=None, query=None, timeout=12):
            path = urllib.parse.urlparse(url).path
            calls.append(path)
            if path.endswith("/oauth2/tokenP"):
                return {"access_token": "kis-token"}
            if path.endswith("/inquire-price"):
                return {"rt_cd": "0", "output": {"stck_prpr": "201000", "acml_vol": "1026999"}}
            if path.endswith("/inquire-ccnl"):
                return {"rt_cd": "0", "output": [{"stck_prpr": "201000", "tday_rltv": "88.3", "cntg_vol": "100"}]}
            if path.endswith("/inquire-investor"):
                return {"rt_cd": "0", "output": [{
                    "frgn_ntby_qty": "243601",
                    "orgn_ntby_qty": "67401",
                    "prsn_ntby_qty": "-304684",
                }]}
            if path.endswith("/inquire-asking-price-exp-ccn"):
                return {"rt_cd": "0", "output1": {"total_bidp_rsqn": "1200", "total_askp_rsqn": "900"}}
            return {"rt_cd": "0", "output": {}}

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "app-key",
                "kisAppSecret": "app-secret",
                "kisMarketSignalsEnabled": "1",
                "kisMarketSignalCacheMinutes": "10",
                "kisMarketSignalGapSeconds": "0",
                "kisMarketSignalPreferLiveDuringMarketHours": "1",
                "kisMarketSignalLiveRefreshSeconds": "60",
                "externalApiRetryAttempts": "1",
            },
            quote_cache=cache,
            fetch_json=fake_fetch_json,
            sleep=lambda _seconds: None,
            now_provider=lambda: datetime(2026, 7, 7, 2, 0, tzinfo=timezone.utc),
        )
        naver = normalize_position({"symbol": "035420", "name": "NAVER", "market": "KR", "currency": "KRW"})

        positions, _watchlist = provider.enrich_collections([naver], [])

        self.assertEqual(243601, positions[0].foreign_net_volume)
        self.assertEqual(1, provider.diagnostics["livePreferred"])
        self.assertEqual(1, provider.diagnostics["live"])
        self.assertIn("/uapi/domestic-stock/v1/quotations/inquire-investor", calls)

    def test_kis_market_signal_provider_reuses_near_live_cache_during_market_hours(self):
        db_path = Path(self.temp.name) / "service.db"
        cache = SQLiteMarketQuoteCache(db_path)
        cache.save(KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID, "035420", {
            "symbol": "035420",
            "name": "NAVER",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 200000,
            "tradeStrength": 87.3,
            "foreignNetVolume": 243601,
            "institutionNetVolume": 67401,
            "individualNetVolume": -304684,
            "quoteSource": "KIS Open API",
            "quoteStatus": "KIS 현재가, 체결강도, 투자자별 수급 반영",
            "quoteMessage": "cached",
            "dataQuality": "actual",
            "updatedAt": "2026-07-07T01:59:30Z",
        })

        def fail_fetch_json(*_args, **_kwargs):
            raise AssertionError("near-live KIS cache should avoid duplicate live calls")

        provider = KISMarketSignalProvider(
            settings={
                "kisBaseUrl": "https://kis.example.test",
                "kisAppKey": "app-key",
                "kisAppSecret": "app-secret",
                "kisMarketSignalsEnabled": "1",
                "kisMarketSignalCacheMinutes": "10",
                "kisMarketSignalPreferLiveDuringMarketHours": "1",
                "kisMarketSignalLiveRefreshSeconds": "60",
            },
            quote_cache=cache,
            fetch_json=fail_fetch_json,
            now_provider=lambda: datetime(2026, 7, 7, 2, 0, tzinfo=timezone.utc),
        )
        naver = normalize_position({"symbol": "035420", "name": "NAVER", "market": "KR", "currency": "KRW"})

        positions, _watchlist = provider.enrich_collections([naver], [])

        self.assertEqual(243601, positions[0].foreign_net_volume)
        self.assertEqual(1, provider.diagnostics["cached"])
        self.assertEqual(0, provider.diagnostics["live"])

    def test_kis_merge_recomputes_moving_average_distance_after_price_update(self):
        provider = KISMarketSignalProvider(settings={"kisMarketSignalsEnabled": "0"})
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 312500,
            "ma20": 329300,
            "ma60": 286275,
            "ma20Distance": -5.1,
            "ma60Distance": 9.16,
        })

        merged = provider.merge_position(position, {
            "currentPrice": 318000,
            "quoteSource": "KIS Open API",
            "quoteStatus": "KIS 현재가 반영",
            "quoteMessage": "KIS 현재가를 모델링 데이터에 반영했습니다.",
            "dataQuality": "actual",
        })

        self.assertAlmostEqual(-3.4315214090, merged.ma20_distance, places=4)
        self.assertAlmostEqual(11.0820015719, merged.ma60_distance, places=4)

    def test_toss_retries_transient_accounts_unauthorized(self):
        account = AccountConfig("main", "메인", "toss", "https://example.test", "id", "secret", "", [])
        account_calls = []
        token_calls = []

        def fake_http_json(method, url, headers, body=None, timeout=12):
            path = urllib.parse.urlparse(url).path
            if path == "/oauth2/token":
                token_calls.append(url)
                return {"access_token": "token-" + str(len(token_calls))}
            if path == "/api/v1/accounts":
                account_calls.append(headers.get("Authorization"))
                if len(account_calls) == 1:
                    raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
                return {"result": [{"accountSeq": "1", "currency": "KRW", "orderableAmount": "1000"}]}
            if path == "/api/v1/buying-power":
                return {"result": {"cashBuyingPower": "0"}}
            if path == "/api/v1/holdings":
                return {"result": {"holdings": []}}
            return {}

        provider = TossProvider(account, quote_cache=SQLiteMarketQuoteCache(Path(self.temp.name) / "service.db"))
        with mock.patch("digital_twin.infrastructure.toss_snapshots.http_json", side_effect=fake_http_json), \
                mock.patch("digital_twin.infrastructure.toss_snapshots.time.sleep", return_value=None):
            mode, status, positions, _, _, _ = provider.fetch_positions()

        self.assertEqual("live", mode)
        self.assertEqual("토스 계좌 동기화", status)
        self.assertEqual([], positions)
        self.assertEqual(2, len(account_calls))
        self.assertEqual(["Bearer token-1", "Bearer token-2"], account_calls)
        self.assertEqual(2, len(token_calls))
        diagnostics = provider.diagnostics_payload()["toss"]
        self.assertEqual(1, diagnostics["authRefreshes"])
        self.assertEqual(1, diagnostics["stageFailures"]["accounts"]["count"])
        self.assertEqual(1, diagnostics["stageFailures"]["accounts"]["recovered"])

    def test_toss_failure_metadata_tracks_failed_stage(self):
        account = AccountConfig("main", "메인", "toss", "https://example.test", "id", "secret", "", [])

        def fake_http_json(method, url, headers, body=None, timeout=12):
            path = urllib.parse.urlparse(url).path
            if path == "/oauth2/token":
                return {"access_token": "token"}
            if path == "/api/v1/accounts":
                raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
            return {}

        provider = TossProvider(account, quote_cache=SQLiteMarketQuoteCache(Path(self.temp.name) / "service.db"))
        with mock.patch("digital_twin.infrastructure.toss_snapshots.http_json", side_effect=fake_http_json), \
                mock.patch("digital_twin.infrastructure.toss_snapshots.time.sleep", return_value=None):
            mode, status, _, _, _, _ = provider.fetch_positions()

        diagnostics = provider.diagnostics_payload()["toss"]

        self.assertEqual("demo", mode)
        self.assertIn("Toss accounts 단계 실패", status)
        self.assertEqual(1, diagnostics["authRefreshes"])
        self.assertEqual(2, diagnostics["stageFailures"]["accounts"]["count"])
        self.assertEqual("HTTP 401 Unauthorized", diagnostics["stageFailures"]["accounts"]["lastError"])

    def test_toss_failure_message_includes_stage_without_url(self):
        account = AccountConfig("main", "메인", "toss", "https://example.test", "id", "secret", "", [])

        def fake_http_json(method, url, headers, body=None, timeout=12):
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

        provider = TossProvider(account, quote_cache=SQLiteMarketQuoteCache(Path(self.temp.name) / "service.db"))
        with mock.patch("digital_twin.infrastructure.toss_snapshots.http_json", side_effect=fake_http_json), \
                mock.patch("digital_twin.infrastructure.toss_snapshots.time.sleep", return_value=None):
            mode, status, _, _, _, _ = provider.fetch_positions()

        self.assertEqual("demo", mode)
        self.assertIn("Toss token 단계 실패", status)
        self.assertIn("HTTP 401 Unauthorized", status)
        self.assertNotIn("https://example.test", status)

    def test_toss_price_normalizer_accepts_official_result_shape(self):
        items = normalize_price_items({"result": [{"symbol": "AAPL", "lastPrice": "185.70", "currency": "USD"}]})

        self.assertEqual("AAPL", items[0]["symbol"])

    def test_account_registry_stores_telegram_per_account(self):
        registry = AccountRegistry()
        account = AccountConfig(
            "main",
            "메인",
            "toss",
            "https://example.test",
            "id1",
            "secret1",
            "1",
            ["AAPL"],
            notify_provider="telegram",
            telegram_bot_token="token",
            telegram_chat_id="chat",
            notify_link_url="http://127.0.0.1:3000",
        )
        registry.upsert(account)

        loaded = registry.load_all()[0]

        self.assertEqual("telegram", loaded.notify_provider)
        self.assertEqual("token", loaded.telegram_bot_token)
        self.assertEqual("chat", loaded.telegram_chat_id)

    def test_account_registry_falls_back_to_runtime_toss_credentials(self):
        SQLiteRuntimeSettingsStore(Path(self.temp.name) / "service.db").save({
            "tossClientId": "runtime-id",
            "tossClientSecret": "runtime-secret",
            "tossAccountSeq": "9",
        })
        registry = AccountRegistry()
        registry.upsert(AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"]))

        loaded = AccountRegistry().load()[0]

        self.assertEqual("runtime-id", loaded.client_id)
        self.assertEqual("runtime-secret", loaded.client_secret)
        self.assertEqual("9", loaded.account_seq)

    def test_runtime_settings_store_masks_kis_credentials(self):
        save_runtime_settings({
            "kisEnv": "prod",
            "kisBaseUrl": "https://openapi.koreainvestment.com:9443",
            "kisAppKey": "app-key",
            "kisAppSecret": "app-secret",
            "kisAccountNo": "12345678",
            "kisAccountProductCode": "01",
        })

        settings = runtime_settings()
        status = settings_status_payload()

        self.assertEqual("prod", settings["kisEnv"])
        self.assertEqual("https://openapi.koreainvestment.com:9443", settings["kisBaseUrl"])
        self.assertEqual("app-key", settings["kisAppKey"])
        self.assertEqual("app-secret", settings["kisAppSecret"])
        self.assertEqual("", status["settings"]["kisAppKey"])
        self.assertEqual("", status["settings"]["kisAppSecret"])
        self.assertIn("ontologyRelationRules", status["settings"])
        self.assertIn("aiPromptTemplates", status["settings"])
        self.assertIn("aiPromptPolicy", status["settings"])
        self.assertTrue(status["configured"]["kisAppKey"])
        self.assertTrue(status["configured"]["kisAppSecret"])
        self.assertTrue(status["configured"]["kisAccountNo"])
        self.assertTrue(status["configured"]["kisAccountProductCode"])

    def test_save_json_preserves_existing_secrets_when_omitted(self):
        registry = AccountRegistry()
        existing = AccountConfig(
            "main",
            "메인",
            "toss",
            "https://example.test",
            "id1",
            "secret1",
            "1",
            ["AAPL"],
            notify_provider="telegram",
            telegram_bot_token="token",
            telegram_chat_id="chat",
            quiet_hours_enabled=False,
            quiet_hours_start="23:00",
            quiet_hours_end="06:00",
        )
        registry.upsert(existing)
        updated = AccountConfig.from_dict({"id": "main", "label": "메인 수정", "watchlistSymbols": "NVDA"}, registry.settings)

        preserved = preserve_existing_secrets(registry, {"id": "main", "label": "메인 수정", "watchlistSymbols": "NVDA"}, updated)

        self.assertEqual("secret1", preserved.client_secret)
        self.assertEqual("token", preserved.telegram_bot_token)
        self.assertEqual("chat", preserved.telegram_chat_id)

    def test_account_application_service_manages_account_use_cases(self):
        registry = AccountRegistry()
        event_bus = EventBus()
        service = AccountApplicationService(registry, registry.settings, event_publisher=event_bus)
        existing = AccountConfig(
            "main",
            "메인",
            "toss",
            "https://example.test",
            "id1",
            "secret1",
            "1",
            ["AAPL"],
            notify_provider="telegram",
            telegram_bot_token="token",
            telegram_chat_id="chat",
            quiet_hours_enabled=False,
            quiet_hours_start="23:00",
            quiet_hours_end="06:00",
        )
        service.save(existing)

        updated = service.save_payload({"id": "main", "label": "메인 수정", "watchlistSymbols": "NVDA"})
        masked = service.list_masked()[0]

        self.assertEqual("secret1", updated.client_secret)
        self.assertEqual("token", updated.telegram_bot_token)
        self.assertFalse(updated.quiet_hours_enabled)
        self.assertEqual("23:00", updated.quiet_hours_start)
        self.assertEqual("06:00", updated.quiet_hours_end)
        self.assertEqual(["NVDA"], masked["watchlistSymbols"])
        self.assertFalse(masked["quietHoursEnabled"])
        self.assertTrue(masked["clientSecret"])
        self.assertEqual([ACCOUNT_SAVED, ACCOUNT_SAVED], [event.name for event in event_bus.published])
        self.assertFalse(event_bus.published[-1].payload["account"]["clientSecret"] == "secret1")

    def test_account_message_delivery_level_is_persisted_and_preserved(self):
        registry = AccountRegistry(Path(self.temp.name) / "delivery-level.db", legacy_path=Path(self.temp.name) / "missing-accounts.json")
        service = AccountApplicationService(registry, registry.settings)

        saved = service.save_payload({
            "account": {
                "id": "main",
                "label": "메인",
                "provider": "toss",
                "messageDeliveryLevel": "advanced",
            }
        })
        masked = service.list_masked()[0]

        self.assertEqual("advanced", saved.message_delivery_level)
        self.assertEqual("advanced", masked["messageDeliveryLevel"])
        self.assertEqual("고수", masked["messageDeliveryLevelLabel"])

        preserved = service.save_payload({
            "account": {
                "id": "main",
                "label": "메인 수정",
                "provider": "toss",
            }
        })

        self.assertEqual("advanced", preserved.message_delivery_level)

    def test_account_config_allows_empty_watchlist_override(self):
        account = AccountConfig.from_dict(
            {"id": "main", "label": "메인", "watchlistSymbols": ""},
            {"watchlistSymbols": "TSLA,AAPL"},
        )

        self.assertEqual([], account.watchlist_symbols)

    def test_strategy_formula_is_safe_and_scores(self):
        formula = SafeFormula("max(0, buyShare - 50) + abs(priceChangeRate) + clamp(9, 0, 3)")
        self.assertEqual(20, formula.evaluate({"buyShare": 65, "priceChangeRate": -2}))
        with self.assertRaises(ValueError):
            SafeFormula("__import__('os').system('echo no')")

        model = StrategyModel({
            "buyScoreFormula": "50 + tradeStrength / 10",
            "sellScoreFormula": "50 - priceChangeRate",
            "formulaWeights": "flowWeight=1",
        })
        score = model.score({"tradeStrength": 120, "priceChangeRate": -3})
        self.assertEqual(62, score["buyScore"])
        self.assertEqual(53, score["sellScore"])

    def test_strategy_default_formula_uses_directional_volume(self):
        model = StrategyModel({"formulaWeights": "flowWeight=1\nvaluationWeight=1"})

        buy_side = model.score({
            "tradeStrength": 130,
            "volumeRatio": 2.2,
            "buyVolume": 700,
            "sellVolume": 300,
            "bidAskImbalance": 14,
            "priceChangeRate": 2.0,
        })
        sell_side = model.score({
            "tradeStrength": 72,
            "volumeRatio": 2.2,
            "buyVolume": 300,
            "sellVolume": 700,
            "bidAskImbalance": -14,
            "priceChangeRate": -2.0,
        })

        self.assertGreater(buy_side["buyScore"], buy_side["sellScore"])
        self.assertGreater(sell_side["sellScore"], sell_side["buyScore"])

    def test_holding_decision_keeps_user_score_formulas_as_supporting_evidence(self):
        loss_position = normalize_position({
            "symbol": "000660",
            "name": "SK하이닉스",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000,
            "profitLossRate": -8.2,
            "sellableQuantity": 10,
            "sector": "반도체",
        })
        profit_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1200,
            "profitLossRate": 11.5,
            "sellableQuantity": 10,
            "sector": "반도체",
        })
        model = StrategyModel({
            "profitTakeScoreFormula": "77",
            "lossCutScoreFormula": "88",
        })

        decisions = decisions_for_positions(
            [loss_position, profit_position],
            portfolio_summary([loss_position, profit_position]),
            model,
        )
        loss_decision = next(item for item in decisions if item.symbol == "000660")
        profit_decision = next(item for item in decisions if item.symbol == "005930")

        self.assertEqual(88, loss_decision.loss_cut_pressure)
        self.assertEqual("ontologyRelationRules", loss_decision.decision_basis)
        self.assertEqual("손절·분할축소 권장", loss_decision.decision)
        self.assertEqual(77, profit_decision.profit_take_pressure)
        self.assertEqual("ontologyRelationRules", profit_decision.decision_basis)
        self.assertEqual("리밸런싱 권장", profit_decision.decision)
        self.assertEqual("supporting-evidence", loss_decision.ai_context["legacyModelRole"])
        self.assertEqual("ontology-relation-rule-ai-review", loss_decision.ai_context["role"])
        self.assertIn("relationRuleContext", loss_decision.ai_context)
        self.assertIn("관계 분석 데이터 JSON", loss_decision.ai_context["prompt"])
        self.assertIn("legacy_model", loss_decision.ontology_opinion)

    def test_loss_guard_near_threshold_requires_confirmation(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000000,
            "profitLossRate": -8.3,
            "sellableQuantity": 10,
            "currentPrice": 91300,
            "ma20": 100000,
            "ma60": 87035,
            "ma20Distance": -8.7,
            "ma60Distance": 4.9,
            "volumeRatio": 0.2,
            "sector": "반도체",
        })
        model = StrategyModel({})

        variables = model.holding_variables(position)
        scores = model.holding_pressure_scores(position)
        relation_context = evaluate_position_relation_rules(position, portfolio_summary([position]))

        self.assertEqual(1, variables["lossRateNearThreshold"])
        self.assertEqual(1, variables["lossGuardMa60Support"])
        self.assertEqual(0, variables["lossGuardVolumeConfirm"])
        self.assertGreaterEqual(variables["lossGuardWeakEvidencePenalty"], 30)
        self.assertLess(scores["lossCutPressure"], 55)
        self.assertEqual("손실 방어 관망", relation_context["decision"]["label"])
        self.assertEqual("hold", relation_context["decision"]["tone"])
        self.assertLess(relation_context["decision"]["score"], 55)
        self.assertTrue(any(
            "약한 확인 신호 감점" in " ".join(rule.get("evidence") or [])
            for rule in relation_context["activeRules"]
        ))

    def test_holding_timing_skips_weak_loss_guard_inside_buffer(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000000,
            "profitLossRate": -8.3,
            "sellableQuantity": 10,
            "currentPrice": 91300,
            "ma20": 100000,
            "ma60": 87035,
            "ma20Distance": -8.7,
            "ma60Distance": 4.9,
            "volumeRatio": 0.2,
            "sector": "반도체",
        })
        portfolio = portfolio_summary([position])
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio,
            [position],
            [],
        )

        events = RealtimeMonitor({}).events_for_snapshot(snapshot, {})

        self.assertFalse(any(event.rule == "holdingTiming" for event in events))

    def test_portfolio_ontology_builds_relations_and_ai_prompt(self):
        semis = normalize_position({
            "symbol": "000660",
            "name": "SK하이닉스",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 7000,
            "profitLossRate": -9,
            "currentPrice": 94000,
            "ma20": 100000,
            "ma60": 90000,
            "ma20Distance": -6,
            "sector": "반도체",
        })
        platform = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "marketValue": 3000,
            "profitLossRate": 14,
            "currentPrice": 210,
            "ma20": 200,
            "ma60": 180,
            "ma20Distance": 5,
            "sector": "AI/플랫폼",
        })
        watch = normalize_position({
            "symbol": "NVDA",
            "name": "NVIDIA",
            "market": "US",
            "currency": "USD",
            "currentPrice": 180,
            "ma20": 168,
            "ma60": 150,
            "ma20Distance": 7,
            "sector": "반도체",
            "source": "watchlist",
        })
        portfolio = portfolio_summary([semis, platform], 500, "KRW")

        graph = build_portfolio_ontology(
            [semis, platform, watch],
            portfolio,
            legacy_by_symbol={"000660": {"exitPressure": 76, "decisionBasis": "lossCut"}},
            external_signals=attach_external_signal_quality(
                {
                    "fetchedAt": "2026-07-07T21:30:00Z",
                    "secFilings": {
                        "AAPL": {
                            "provider": "SEC EDGAR",
                            "latestFiling": {"form": "10-Q", "filingDate": "2026-07-01"},
                            "facts": {"revenue": {"value": 1000, "form": "10-Q"}},
                        },
                    },
                    "newsHeadlines": {"AAPL": {"provider": "GDELT", "items": [{"title": "Apple result", "url": "https://example.test"}], "count": 1}},
                    "macro": {
                        "series": {
                            "DGS10": {"provider": "FRED", "value": 4.1},
                            "DGS2": {"provider": "FRED", "value": 3.8},
                        },
                        "yieldSpread10y2y": 0.3,
                    },
                    "fxRates": {
                        "USDKRW": {"provider": "RuntimeSettings", "base": "USD", "quote": "KRW", "rate": 1400},
                    },
                    "statuses": [{"source": "GDELT News", "ok": True, "message": "ok"}],
                },
                [semis, platform, watch],
                {"externalApiFetchIntervalMinutes": "30", "fredApiKey": "configured"},
            ),
            portfolio_id="main",
            runtime_context={
                "settings": {
                    "alertThresholds": "lossRateLow=-8",
                    "profitTakeScoreFormula": "baseScore + profitTakePnlScore",
                    "marketSnapshotIntervalMinutes": "3",
                    "watchlistSnapshotIntervalMinutes": "5",
                    "externalSignalsIntervalMinutes": "30",
                    "notificationNoveltyThreshold": "0.7",
                },
                "decisionItems": [
                    {"symbol": "000660", "decision": "손실 관리", "exitPressure": 76, "tone": "danger"},
                ],
                "account": {"accountId": "main", "accountLabel": "메인 계좌", "provider": "test"},
            },
        )
        payload = graph.to_dict()

        self.assertEqual("ontology-first", graph.worldview["model"])
        self.assertEqual("TBox", payload["tbox"]["box"])
        self.assertEqual("ABox", payload["abox"]["box"])
        self.assertEqual(6, len(payload["tbox"]["boundedContexts"]))
        self.assertIn("strategy-thesis", {item["key"] for item in payload["tbox"]["boundedContexts"]})
        self.assertIn("Stock", payload["tbox"]["classes"])
        self.assertIn("Instrument", payload["tbox"]["classes"])
        self.assertIn("InvestmentThesis", payload["tbox"]["classes"])
        self.assertIn("EntryCondition", payload["tbox"]["classes"])
        self.assertIn("PriceMetric", payload["tbox"]["classes"])
        self.assertIn("Observation", payload["tbox"]["classes"])
        self.assertIn("FlowObservation", payload["tbox"]["classes"])
        self.assertIn("MarketRisk", payload["tbox"]["classes"])
        self.assertIn("DataQualityRisk", payload["tbox"]["classes"])
        self.assertIn("RuntimeSetting", payload["tbox"]["classes"])
        self.assertIn("DataPipeline", payload["tbox"]["classes"])
        self.assertIn("CollectionSchedule", payload["tbox"]["classes"])
        self.assertIn("ReasoningCycle", payload["tbox"]["classes"])
        self.assertIn("Insight", payload["tbox"]["classes"])
        self.assertIn("NotificationDispatch", payload["tbox"]["classes"])
        self.assertIn("ActiveInvestmentOpinion", payload["tbox"]["classes"])
        self.assertIn("ExecutionPlan", payload["tbox"]["classes"])
        self.assertIn("ActionCandidate", payload["tbox"]["classes"])
        self.assertIn("BlockedAction", payload["tbox"]["classes"])
        self.assertIn("AIValidation", payload["tbox"]["classes"])
        self.assertIn("PriceBar", payload["tbox"]["classes"])
        self.assertIn("KeyLevel", payload["tbox"]["classes"])
        self.assertIn("ResearchEvidence", payload["tbox"]["classes"])
        self.assertIn("NewsTopic", payload["tbox"]["classes"])
        self.assertIn("PeerCompanyMention", payload["tbox"]["classes"])
        self.assertIn("Factor", payload["tbox"]["classes"])
        self.assertIn("LiquidityProfile", payload["tbox"]["classes"])
        self.assertIn("RelationStateSnapshot", payload["tbox"]["classes"])
        self.assertIn("InterestRate", payload["tbox"]["classes"])
        self.assertIn("YieldCurve", payload["tbox"]["classes"])
        self.assertIn("FXRateSignal", payload["tbox"]["classes"])
        self.assertIn("HOLDS", payload["tbox"]["relationTypes"])
        self.assertIn("WATCHES", payload["tbox"]["relationTypes"])
        self.assertIn("IS_A", payload["tbox"]["relationTypes"])
        self.assertIn("HAS_OBSERVATION", payload["tbox"]["relationTypes"])
        self.assertIn("USES_STRATEGY", payload["tbox"]["relationTypes"])
        self.assertIn("BASED_ON_THESIS", payload["tbox"]["relationTypes"])
        self.assertIn("WEAKENS_THESIS", payload["tbox"]["relationTypes"])
        self.assertIn("HAS_TIME_HORIZON", payload["tbox"]["relationTypes"])
        self.assertIn("HAS_PRICE", payload["tbox"]["relationTypes"])
        self.assertIn("HAS_MODEL_SCORE", payload["tbox"]["relationTypes"])
        self.assertIn("HAS_PIPELINE", payload["tbox"]["relationTypes"])
        self.assertIn("TRIGGERS_REASONING", payload["tbox"]["relationTypes"])
        self.assertIn("PRODUCES_INSIGHT", payload["tbox"]["relationTypes"])
        self.assertIn("DISPATCHED_BY", payload["tbox"]["relationTypes"])
        self.assertIn("HAS_EXECUTION_PLAN", payload["tbox"]["relationTypes"])
        self.assertIn("HAS_PRIMARY_ACTION", payload["tbox"]["relationTypes"])
        self.assertIn("BLOCKS_ACTION", payload["tbox"]["relationTypes"])
        self.assertIn("REQUIRES_NEXT_CHECK", payload["tbox"]["relationTypes"])
        self.assertIn("HAS_FACTOR_EXPOSURE", payload["tbox"]["relationTypes"])
        self.assertIn("HAS_FX_EXPOSURE", payload["tbox"]["relationTypes"])
        self.assertIn("HAS_RATE_SENSITIVITY", payload["tbox"]["relationTypes"])
        self.assertIn("LIMITED_BY_LIQUIDITY", payload["tbox"]["relationTypes"])
        self.assertIn("MENTIONS_INSTRUMENT", payload["tbox"]["relationTypes"])
        self.assertIn("MENTIONS_PEER", payload["tbox"]["relationTypes"])
        self.assertIn("HAS_TOPIC", payload["tbox"]["relationTypes"])
        self.assertIn("MATERIAL_TO", payload["tbox"]["relationTypes"])
        self.assertGreater(payload["abox"]["entityCount"], 0)
        self.assertTrue(any(item.relation_type == "HOLDS" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "WATCHES" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "EXPOSED_TO" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "HAS_PRICE" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "HAS_DATA_QUALITY" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "HAS_MODEL_SCORE" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "HAS_PIPELINE" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "HAS_OBSERVATION" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "USES_STRATEGY" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "BASED_ON_THESIS" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "HAS_OPINION" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "WEAKENS_THESIS" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "HAS_TIME_HORIZON" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "TRIGGERS_REASONING" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "PRODUCES_INSIGHT" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "DISPATCHED_BY" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "HAS_EXECUTION_PLAN" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "HAS_PRIMARY_ACTION" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "BLOCKS_ACTION" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "REQUIRES_NEXT_CHECK" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "HAS_FACTOR_EXPOSURE" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "HAS_FX_EXPOSURE" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "HAS_RATE_SENSITIVITY" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "LIMITED_BY_LIQUIDITY" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "MENTIONS_INSTRUMENT" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "MATERIAL_TO" for item in graph.relations))
        self.assertTrue(any(item.kind == "account" for item in graph.entities))
        self.assertTrue(any(item.kind == "position" for item in graph.entities))
        self.assertTrue(any(item.kind == "strategy" for item in graph.entities))
        self.assertTrue(any(item.kind == "investment-thesis" for item in graph.entities))
        self.assertTrue(any(item.kind == "active-opinion" for item in graph.entities))
        self.assertTrue(any(item.kind == "signal-horizon" for item in graph.entities))
        self.assertTrue(any(item.kind == "fx-pair" for item in graph.entities))
        self.assertTrue(any(item.kind == "fundamental-event" for item in graph.entities))
        self.assertTrue(any(item.kind == "data-pipeline" for item in graph.entities))
        self.assertTrue(any(item.kind == "collection-schedule" for item in graph.entities))
        self.assertTrue(any(item.kind == "reasoning-cycle" for item in graph.entities))
        self.assertTrue(any(item.kind == "insight" for item in graph.entities))
        self.assertTrue(any(item.kind == "notification-dispatch" for item in graph.entities))
        self.assertTrue(any(item.kind == "execution-plan" for item in graph.entities))
        self.assertTrue(any(item.kind == "action-candidate" for item in graph.entities))
        self.assertTrue(any(item.kind == "blocked-action" for item in graph.entities))
        self.assertTrue(any(item.kind == "next-check" for item in graph.entities))
        self.assertTrue(any(item.kind == "trend-scenario" for item in graph.entities))
        self.assertTrue(any(item.kind == "price-bar" for item in graph.entities))
        self.assertTrue(any(item.kind == "key-level" for item in graph.entities))
        self.assertTrue(any(item.kind == "liquidity-profile" for item in graph.entities))
        self.assertTrue(any(item.kind == "factor" for item in graph.entities))
        self.assertTrue(any(item.kind == "research-evidence" for item in graph.entities))
        self.assertTrue(any(item.kind == "price-metric" for item in graph.entities))
        self.assertTrue(any(item.kind == "fx-rate" for item in graph.entities))
        self.assertTrue(any(item.kind == "interest-rate" for item in graph.entities))
        self.assertTrue(any(item.kind == "yield-curve" for item in graph.entities))
        self.assertTrue(any(item.kind == "model-score" for item in graph.entities))
        self.assertTrue(any(item.kind == "runtime-setting" for item in graph.entities))
        self.assertTrue(any(item.kind == "strategy-signal" for item in graph.entities))
        self.assertTrue(any(item.kind == "bounded-context" for item in graph.entities))
        self.assertTrue(any(item.kind == "tbox-class" for item in graph.entities))
        self.assertTrue(any((item.properties or {}).get("boundedContext") == "strategy-thesis" for item in graph.entities if item.kind == "investment-thesis"))
        self.assertTrue(any((item.properties or {}).get("boundedContext") == "observation-data" for item in graph.entities if item.kind == "price-metric"))
        self.assertTrue(any((item.properties or {}).get("boundedContext") == "strategy-thesis" for item in graph.relations if item.relation_type == "BASED_ON_THESIS"))
        self.assertTrue(any("CurrencyRisk" in (item.properties or {}).get("tboxClasses", []) for item in graph.entities if item.kind == "risk"))
        self.assertTrue(any("FXRateSignal" in (item.properties or {}).get("tboxClasses", []) for item in graph.entities if item.kind == "fx-rate"))
        self.assertTrue(any("InterestRate" in (item.properties or {}).get("tboxClasses", []) for item in graph.entities if item.kind == "interest-rate"))
        self.assertTrue(any("CorrelationRisk" in (item.properties or {}).get("tboxClasses", []) for item in graph.entities if item.kind == "risk"))
        self.assertTrue(any(item.get("symbol") == "NVDA" for item in payload["reasoningCards"]))
        self.assertTrue(any("strategy-thesis" in item.get("graphContext", {}).get("boundedContexts", []) for item in payload["reasoningCards"]))
        self.assertEqual("investment-ontology-ai-inference-v1", payload["aiInferencePacket"]["contract"])
        self.assertEqual("insight-driven-dispatch", payload["aiInferencePacket"]["notificationRole"])
        self.assertIn("boundedContexts", payload["aiInferencePacket"]["inputOrder"])
        self.assertIn("operationalOntology", payload["aiInferencePacket"]["inputOrder"])
        self.assertIn("insights", payload["aiInferencePacket"]["inputOrder"])
        self.assertIn("activeInvestmentOpinions", payload["aiInferencePacket"]["inputOrder"])
        self.assertIn("executionPlans", payload["aiInferencePacket"]["inputOrder"])
        self.assertIn("relationInfluences", payload["aiInferencePacket"]["inputOrder"])
        self.assertIn("researchEvidence", payload["aiInferencePacket"]["inputOrder"])
        self.assertIn("signalTransitions", payload["aiInferencePacket"]["inputOrder"])
        self.assertIn("factorExposure", payload["aiInferencePacket"]["inputOrder"])
        self.assertIn("liquidityConstraints", payload["aiInferencePacket"]["inputOrder"])
        self.assertGreater(payload["aiInferencePacket"]["graphInputs"]["activeOpinionCount"], 0)
        self.assertGreater(payload["aiInferencePacket"]["graphInputs"]["executionPlanCount"], 0)
        self.assertTrue(payload["activeInvestmentOpinions"])
        self.assertTrue(payload["executionPlans"])
        self.assertTrue(any(item.get("symbol") == "AAPL" for item in payload["activeInvestmentOpinions"]))
        self.assertTrue(any(item.get("subject", {}).get("symbol") == "000660" for item in payload["executionPlans"]))
        self.assertEqual("insight-driven-only", graph.worldview["operationalOntology"]["dispatchMode"])
        self.assertEqual(3, graph.worldview["operationalOntology"]["collectionPipelineCount"])
        self.assertTrue(any(item.get("key") == "externalSignals" and item.get("configuredMinutes") == 30 for item in graph.worldview["operationalOntology"]["pipelines"]))
        self.assertIn("관계 분석 데이터 JSON", graph.prompt)
        self.assertIn("reasoningCards", graph.prompt)
        self.assertIn("TBox", graph.prompt)
        self.assertIn("ABox", graph.prompt)
        self.assertIn("activeInvestmentOpinions", graph.prompt)
        self.assertIn("executionPlans", graph.prompt)
        trend_evidence = next(item for item in graph.evidence if item.evidence_id == "evidence:000660:trend")
        self.assertIn("trendDynamics", trend_evidence.value)
        self.assertTrue(graph.opinion_for_symbol("000660").dominant_risks)
        self.assertTrue(graph.opinion_for_symbol("000660").relation_influences)
        sample_store = SQLiteOntologyQualitySampleStore(Path(self.temp.name) / "service.db")
        sample = sample_store.record_graph(graph, source="unit-test")
        latest_samples = sample_store.latest("main")
        self.assertGreater(sample.overall_score, 0)
        self.assertEqual(sample.sample_id, latest_samples[0]["sampleId"])
        self.assertIn("strategy-thesis", latest_samples[0]["payload"]["boundedContexts"])

    def test_portfolio_ontology_adds_news_scope_topics_and_peers(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000000,
            "currentPrice": 90000,
            "ma20": 93000,
            "ma60": 88000,
            "ma20Distance": -3.2,
            "ma60Distance": 2.3,
            "changeRate": -1.8,
            "volumeRatio": 1.4,
            "sector": "반도체",
        })
        graph = build_portfolio_ontology(
            [position],
            portfolio_summary([position], fx_rates={"KRW": 1}),
            external_signals={
                "researchEvidence": {
                    "005930": [
                        {
                            "evidenceId": "research:005930:news:direct-risk",
                            "symbol": "005930",
                            "kind": "news",
                            "source": "Reuters",
                            "title": "Samsung Electronics faces weak memory demand risk",
                            "summary": "Memory demand pressure",
                            "url": "https://example.test/direct",
                            "polarity": "risk",
                            "impactScore": 10,
                            "confidence": 0.82,
                            "payload": {
                                "relationScope": "direct",
                                "relevanceScore": 94,
                                "sourceReliability": 0.78,
                                "matchedAliases": ["Samsung Electronics"],
                                "topicTags": ["memory", "반도체"],
                                "directMention": True,
                            },
                        },
                        {
                            "evidenceId": "research:005930:news:peer-context",
                            "symbol": "005930",
                            "kind": "news",
                            "source": "GDELT",
                            "title": "SK Hynix HBM demand growth lifts memory peers",
                            "summary": "Peer memory context",
                            "url": "https://example.test/peer",
                            "polarity": "support",
                            "impactScore": 6,
                            "confidence": 0.68,
                            "payload": {
                                "relationScope": "peer",
                                "relevanceScore": 64,
                                "sourceReliability": 0.68,
                                "mentionedPeers": ["SK Hynix"],
                                "topicTags": ["HBM", "memory"],
                            },
                        },
                    ]
                }
            },
            portfolio_id="main",
        )

        relation_types = {item.relation_type for item in graph.relations}
        self.assertIn("HAS_TOPIC", relation_types)
        self.assertIn("MENTIONS_PEER", relation_types)
        self.assertIn("AFFECTS", relation_types)
        self.assertTrue(any(item.kind == "news-topic" and item.label == "memory" for item in graph.entities))
        self.assertTrue(any(item.kind == "peer-company" and item.label == "SK Hynix" for item in graph.entities))
        scoped_relations = [
            item
            for item in graph.relations
            if item.relation_type == "MENTIONS_INSTRUMENT"
            and (item.properties or {}).get("relationScope") == "direct"
        ]
        self.assertTrue(scoped_relations)
        self.assertEqual(94, scoped_relations[0].properties["relevanceScore"])

    def test_new_ontology_relation_can_change_ai_opinion_pressure(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "marketValue": 100,
            "profitLossRate": 1,
            "sector": "반도체",
        })
        other = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "marketValue": 900,
            "profitLossRate": 2,
            "sector": "AI/플랫폼",
        })
        portfolio = portfolio_summary([position, other])
        graph = build_portfolio_ontology([position, other], portfolio)
        opinion = graph.opinion_for_symbol("005930")
        base_pressure = opinion.ontology_pressure
        signal_id = entity_id("external-signal", "regulatory-risk")
        graph.entities.append(OntologyEntity(signal_id, "규제 리스크", "external-signal", abox_properties({
            "tboxClass": "ExternalSignal",
        })))
        graph.relations.append(OntologyRelation(
            entity_id("stock", "005930"),
            signal_id,
            "AFFECTS",
            weight=1.0,
            properties=abox_properties({
                "polarity": "risk",
                "opinionImpact": 35,
                "aiInfluenceLabel": "규제 리스크",
            }),
        ))

        apply_relation_driven_opinions(graph)

        updated = graph.opinion_for_symbol("005930")
        self.assertGreater(updated.ontology_pressure, base_pressure)
        self.assertTrue(any(item.get("label") == "규제 리스크" for item in updated.relation_influences))

    def test_portfolio_ontology_adds_relation_state_transition_from_previous_snapshot(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000000,
            "currentPrice": 290000,
            "profitLossRate": -4.0,
            "ma20": 300000,
            "ma60": 295000,
            "ma20Distance": -3.3,
            "ma60Distance": -1.7,
            "changeRate": -1.8,
            "volumeRatio": 1.4,
            "sector": "반도체",
        })
        portfolio = portfolio_summary([position], fx_rates={"KRW": 1})

        graph = build_portfolio_ontology(
            [position],
            portfolio,
            runtime_context={
                "metadata": {
                    "previousMonitorState": {
                        "positions": {
                            "005930": {
                                "currentPrice": 304000,
                                "profitLossRate": -1.0,
                                "ma20Distance": 1.2,
                                "ma60Distance": 0.8,
                            }
                        },
                        "decisions": {
                            "005930": {"selectedRuleId": "trend.recovery_attempt.v1", "exitPressure": 44}
                        },
                    }
                }
            },
        )

        self.assertTrue(any(item.kind == "relation-state" for item in graph.entities))
        self.assertTrue(any(item.kind == "signal-transition" for item in graph.entities))
        self.assertTrue(any(item.relation_type == "CHANGED_FROM" for item in graph.relations))
        self.assertTrue(any(item.relation_type == "CONFIRMED_OVER" for item in graph.relations))

    def test_neo4j_ontology_repository_builds_relation_statements(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "marketValue": 1000,
            "profitLossRate": 3,
            "sector": "반도체",
        })
        portfolio = portfolio_summary([position])
        graph = build_portfolio_ontology([position], portfolio)
        repository = Neo4jOntologyGraphRepository("http://127.0.0.1:7474", user="neo4j", password="secret")

        statements = repository.statements(graph)
        entity_rows = repository.rows_for_entities(graph)
        relation_rows = repository.rows_for_relations(graph)

        self.assertEqual("CONTRADICTS", safe_relation_type("contradicts"))
        self.assertIn("TBox", {row["ontologyBox"] for row in entity_rows})
        self.assertIn("ABox", {row["ontologyBox"] for row in relation_rows})
        self.assertTrue(any("OntologyEntity" in item["statement"] for item in statements))
        self.assertTrue(any("CREATE CONSTRAINT ontology_entity_id" in item["statement"] for item in repository.schema_statements()))
        self.assertTrue(any("OntologyReasoningCard" in item["statement"] for item in statements))
        self.assertGreater(len(repository.rows_for_reasoning_cards(graph)), 0)
        self.assertTrue(any("MERGE (a)-[r:HOLDS]" in item["statement"] for item in statements))
        self.assertFalse(NullOntologyGraphRepository().save_graph(graph)["saved"])

    def test_ontology_projection_recorder_persists_graph_and_quality_metadata(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "marketValue": 1000,
            "profitLossRate": 3,
            "sector": "반도체",
        })
        portfolio = portfolio_summary([position])
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio,
            [position],
            decisions_for_positions([position], portfolio),
        )

        class FakeRepository:
            def __init__(self):
                self.graphs = []

            def save_graph(self, graph):
                self.graphs.append(graph)
                return {"saved": True, "entityCount": len(graph.entities)}

        class FakeQualityStore:
            def record_graph(self, graph, source="monitoring", created_at=""):
                return SimpleNamespace(sample_id="sample-1", overall_score=91.5)

        repository = FakeRepository()
        recorder = PortfolioOntologyProjectionRecorder(
            repository,
            quality_store=FakeQualityStore(),
            settings={"externalApiFetchIntervalMinutes": "30"},
        )

        result = recorder.record_snapshot(snapshot)

        self.assertTrue(result["saved"])
        self.assertEqual(1, len(repository.graphs))
        self.assertEqual("main", repository.graphs[0].portfolio_id)
        self.assertEqual("sample-1", snapshot.metadata["ontology"]["neo4j"]["qualitySampleId"])
        self.assertEqual(91.5, snapshot.metadata["ontology"]["neo4j"]["qualityScore"])

    def test_ontology_projection_recorder_includes_watchlist_candidates(self):
        holding = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000,
            "quantity": 1,
            "currentPrice": 1000,
            "sector": "반도체",
        })
        watch = normalize_position({
            "symbol": "005380",
            "name": "현대차",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 200000,
            "ma20": 210000,
            "ma60": 190000,
            "sector": "자동차",
            "source": "watchlist",
        })
        portfolio = portfolio_summary([holding])
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio,
            [holding],
            decisions_for_positions([holding], portfolio),
            watchlist=[watch],
        )

        class FakeRepository:
            def __init__(self):
                self.graphs = []

            def save_graph(self, graph):
                self.graphs.append(graph)
                return {"saved": True, "entityCount": len(graph.entities)}

        repository = FakeRepository()
        recorder = PortfolioOntologyProjectionRecorder(repository)

        recorder.record_snapshot(snapshot)

        graph = repository.graphs[0]
        stock = next(item for item in graph.entities if item.kind == "stock" and item.properties.get("symbol") == "005380")
        relation_types = {
            item.relation_type
            for item in graph.relations
            if item.source == "stock:005380" or item.target == "stock:005380"
        }
        self.assertEqual("watchlist", stock.properties.get("source"))
        self.assertIn("WatchlistCandidate", stock.properties.get("tboxClasses"))
        self.assertIn("WATCHES", relation_types)
        self.assertEqual(2, len(graph.reasoning_cards))

    def test_ontology_projection_recorder_persists_watchlist_only_snapshot(self):
        watch = normalize_position({
            "symbol": "NVDA",
            "name": "NVIDIA",
            "market": "US",
            "currency": "USD",
            "currentPrice": 180,
            "ma20": 170,
            "ma60": 150,
            "ma20Distance": 5.8,
            "ma60Distance": 20.0,
            "sector": "반도체",
            "source": "watchlist",
        })
        snapshot = AccountSnapshot(
            "watch",
            "관심",
            "toss",
            "mock",
            "watchlist only",
            utc_now_iso(),
            portfolio_summary([]),
            [],
            [],
            watchlist=[watch],
        )

        class FakeRepository:
            def __init__(self):
                self.graphs = []

            def save_graph(self, graph):
                self.graphs.append(graph)
                return {"saved": True}

        repository = FakeRepository()

        result = PortfolioOntologyProjectionRecorder(repository).record_snapshot(snapshot)

        self.assertTrue(result["saved"])
        self.assertEqual(1, len(repository.graphs))
        self.assertTrue(any(item.kind == "stock" and item.properties.get("source") == "watchlist" for item in repository.graphs[0].entities))

    def test_holding_decision_score_uses_flow_and_trend_context(self):
        other_position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "marketValue": 4000,
            "sector": "AI/플랫폼",
        })
        neutral_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000,
            "profitLossRate": 6,
            "sellableQuantity": 10,
            "sector": "반도체",
        })
        weak_signal_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000,
            "profitLossRate": 6,
            "sellableQuantity": 10,
            "volumeRatio": 1.8,
            "buyVolume": 300,
            "sellVolume": 700,
            "foreignBuyVolume": 100,
            "foreignSellVolume": 500,
            "institutionBuyVolume": 80,
            "institutionSellVolume": 340,
            "tradeStrength": 78,
            "ma20Distance": -4,
            "ma20Slope": -1.2,
            "ma60Slope": -0.7,
            "sector": "반도체",
        })

        neutral_decision = next(item for item in decisions_for_positions(
            [neutral_position, other_position],
            portfolio_summary([neutral_position, other_position]),
        ) if item.symbol == "005930")
        weak_signal_decision = next(item for item in decisions_for_positions(
            [weak_signal_position, other_position],
            portfolio_summary([weak_signal_position, other_position]),
        ) if item.symbol == "005930")

        self.assertEqual("hold", neutral_decision.tone)
        self.assertEqual("caution", weak_signal_decision.tone)
        self.assertGreaterEqual(weak_signal_decision.exit_pressure - neutral_decision.exit_pressure, 12)

    def test_holding_decision_uses_ontology_relation_rules(self):
        loss_position = normalize_position({
            "symbol": "000660",
            "name": "SK하이닉스",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000,
            "profitLossRate": -8.2,
            "sellableQuantity": 10,
            "sector": "반도체",
        })
        profit_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000,
            "profitLossRate": 6,
            "sellableQuantity": 10,
            "sector": "반도체",
        })
        small_loss_position = normalize_position({
            "symbol": "035720",
            "name": "카카오",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000,
            "profitLossRate": -1.2,
            "sellableQuantity": 10,
            "sector": "플랫폼",
        })
        diversifier_position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "marketValue": 9000,
            "sector": "AI/플랫폼",
        })

        loss_decision = next(item for item in decisions_for_positions(
            [loss_position],
            portfolio_summary([loss_position]),
        ) if item.symbol == "000660")
        profit_decision = next(item for item in decisions_for_positions(
            [profit_position],
            portfolio_summary([profit_position]),
        ) if item.symbol == "005930")
        small_loss_decision = next(item for item in decisions_for_positions(
            [small_loss_position, diversifier_position],
            portfolio_summary([small_loss_position, diversifier_position]),
        ) if item.symbol == "035720")

        self.assertGreaterEqual(loss_decision.exit_pressure, 55)
        self.assertEqual("손절·분할축소 권장", loss_decision.decision)
        self.assertNotIn("익절", loss_decision.decision)
        self.assertEqual("ontologyRelationRules", loss_decision.decision_basis)
        self.assertIn("holding.loss_guard.breakdown.v1", [
            item.get("rule_id") or item.get("ruleId")
            for item in loss_decision.relation_rule_context.get("activeRules", [])
        ])
        self.assertEqual("리밸런싱 권장", profit_decision.decision)
        self.assertEqual("ontologyRelationRules", profit_decision.decision_basis)
        self.assertEqual("관계 규칙 관찰", small_loss_decision.decision)
        self.assertEqual("ontologyRelationRules", small_loss_decision.decision_basis)

    def test_portfolio_summary_converts_usd_holdings_to_krw_base(self):
        kr_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "currency": "KRW",
            "marketValue": 1000000,
        })
        us_position = normalize_position({
            "symbol": "MSTR",
            "name": "Strategy",
            "market": "US",
            "currency": "USD",
            "marketValue": 1000,
        })

        portfolio = portfolio_summary([kr_position, us_position], fx_rates={"USD": 1400, "KRW": 1})

        self.assertEqual(2400000, portfolio.invested)
        self.assertEqual(2400000, portfolio.total)
        self.assertEqual(1000000, next(item for item in portfolio.markets if item["key"] == "KR")["invested"])
        self.assertEqual(1400000, next(item for item in portfolio.markets if item["key"] == "US")["invested"])

    def test_portfolio_summary_uses_detected_sector_for_empty_sector(self):
        position = Position(symbol="005930", name="삼성전자", market="KR", currency="KRW", market_value=1000000, sector="")

        portfolio = portfolio_summary([position])

        self.assertEqual("반도체", portfolio.sectors[0]["sector"])
        self.assertEqual(1000000, portfolio.sectors[0]["value"])

    def test_ontology_relation_rules_include_prompt_and_missing_data(self):
        position = Position(
            symbol="MSTR",
            name="Strategy",
            market="US",
            currency="USD",
            market_value=1000,
            profit_loss_rate=18.5,
            sellable_quantity=1,
            current_price=105.36,
            ma20=108.07,
            ma60=144.62,
            ma20_distance=-2.5,
            ma60_distance=-27.1,
            sector="BTC",
        )
        portfolio = portfolio_summary([position], fx_rates={"USD": 1400, "KRW": 1})

        context = evaluate_position_relation_rules(
            position,
            portfolio,
            external_signals={
                "cryptoMarkets": {
                    "bitcoin": {
                        "provider": "CoinGecko",
                        "symbol": "BTC",
                        "price": 108000,
                        "volume24h": 42000000000,
                        "change24h": 3.8,
                        "change7d": 9.7,
                    }
                }
            },
        )

        active_ids = [item.get("rule_id") or item.get("ruleId") for item in context["activeRules"]]
        missing_labels = [item["label"] for item in context["missingData"]]
        self.assertIn("holding.profit_take.trend_weakness.v1", active_ids)
        self.assertIn("external.crypto.btc_sensitivity.v1", active_ids)
        self.assertNotIn("체결강도", missing_labels)
        self.assertNotIn("방향별 매수/매도 체결량", missing_labels)
        self.assertNotIn("투자자별 수급", missing_labels)
        self.assertEqual("holdingTiming", context["promptContext"]["promptId"])
        self.assertEqual("ontologyRelationRules", context["decision"]["basis"])

    def test_bitcoin_sensitive_holding_uses_review_label_below_action_threshold(self):
        position = Position(
            symbol="STRC",
            name="Strategy Preferred",
            market="US",
            currency="USD",
            market_value=1000,
            profit_loss_rate=-4.0,
            sellable_quantity=1,
            current_price=88.74,
            ma20=88.65,
            ma60=95.58,
            ma20_distance=0.1,
            ma60_distance=-7.2,
            sector="디지털자산",
        )
        context = evaluate_position_relation_rules(
            position,
            portfolio_summary([position], fx_rates={"USD": 1400, "KRW": 1}),
            external_signals={
                "cryptoMarkets": {
                    "bitcoin": {
                        "provider": "CoinGecko",
                        "symbol": "BTC",
                        "price": 108000,
                        "volume24h": 42000000000,
                        "change24h": 0.1,
                        "change7d": 5.7,
                    }
                }
            },
        )

        self.assertEqual("비트코인 민감도 점검", context["decision"]["label"])
        self.assertLess(context["decision"]["score"], 70)
        self.assertEqual("BTC_REVIEW", context["decision"]["decisionStage"])
        self.assertEqual("cryptoSensitivity", context["decision"]["actionGroup"])
        self.assertEqual("review", context["decision"]["actionLevel"])
        self.assertEqual("REVIEW", context["decision"]["scoreBand"]["key"])
        self.assertEqual(70.0, context["decision"]["nextStageAt"])

    def test_bitcoin_sensitive_holding_uses_reduction_label_after_action_threshold(self):
        position = Position(
            symbol="STRC",
            name="Strategy Preferred",
            market="US",
            currency="USD",
            market_value=1000,
            profit_loss_rate=-4.0,
            sellable_quantity=1,
            current_price=88.74,
            ma20=88.65,
            ma60=95.58,
            ma20_distance=0.1,
            ma60_distance=-7.2,
            sector="디지털자산",
        )
        context = evaluate_position_relation_rules(
            position,
            portfolio_summary([position], fx_rates={"USD": 1400, "KRW": 1}),
            external_signals={
                "cryptoMarkets": {
                    "bitcoin": {
                        "provider": "CoinGecko",
                        "symbol": "BTC",
                        "price": 108000,
                        "volume24h": 42000000000,
                        "change24h": 0.1,
                        "change7d": 8.5,
                    }
                }
            },
        )

        self.assertEqual("비트코인 민감도 축소 검토", context["decision"]["label"])
        self.assertGreaterEqual(context["decision"]["score"], 70)
        self.assertEqual("BTC_REDUCE", context["decision"]["decisionStage"])
        self.assertEqual("cryptoSensitivity", context["decision"]["actionGroup"])
        self.assertEqual("action", context["decision"]["actionLevel"])
        self.assertEqual("ACTION", context["decision"]["scoreBand"]["key"])

    def test_decision_action_group_uses_ontology_stage_aliases(self):
        self.assertEqual("cryptoSensitivity", decision_action_group_for_label("비트코인 민감도 점검"))
        self.assertEqual("cryptoSensitivity", decision_action_group_for_label("비트코인 민감도 축소 검토"))
        self.assertEqual("lossControl", decision_action_group_for_label("손실 관리 기준 확인"))
        self.assertEqual("profitTake", decision_action_group_for_label("분할 매도 기준 확인"))
        self.assertEqual("entry", decision_action_group_for_label("소액 분할매수 검토"))
        self.assertEqual("entryRisk", decision_action_group_for_label("추가매수 보류"))

    def test_ontology_relation_thresholds_are_separate_from_alert_thresholds(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=-6.0,
            sellable_quantity=10,
            current_price=94000,
            ma20=97000,
            ma60=90000,
            ma20_distance=-3.1,
            ma60_distance=4.4,
            sector="반도체",
        )

        context = evaluate_position_relation_rules(
            position,
            portfolio_summary([position], fx_rates={"KRW": 1}),
            settings={
                "alertThresholds": "lossRateLow=-20",
                "relationRuleThresholds": "lossRateLow=-5\nlossRateBufferPct=1",
            },
        )

        active_ids = [item.get("rule_id") or item.get("ruleId") for item in context["activeRules"]]
        self.assertIn("holding.loss_guard.breakdown.v1", active_ids)
        self.assertEqual(-5.0, context["facts"]["lossThreshold"])

    def test_ontology_entry_pullback_rule_creates_split_buy_candidate(self):
        watch = Position(
            symbol="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            current_price=100,
            ma20=104,
            ma60=99,
            ma20_distance=-3.8,
            ma60_distance=1.0,
            volume_ratio=1.1,
            trade_strength=105,
            foreign_net_volume=100,
            institution_net_volume=50,
            individual_net_volume=-30,
            source="watchlist",
            sector="AI/플랫폼",
        )

        context = evaluate_position_relation_rules(watch, portfolio_summary([]))

        active_ids = [item.get("rule_id") or item.get("ruleId") for item in context["activeRules"]]
        self.assertIn("entry.pullback.supported.v1", active_ids)
        self.assertEqual("소액 분할매수 검토", context["decision"]["label"])
        self.assertEqual("entry", context["decision"]["actionGroup"])
        self.assertTrue(context["facts"]["entryPullbackZone"])
        self.assertGreaterEqual(context["facts"]["entrySupportCount"], 2)

    def test_ontology_holding_breakdown_blocks_additional_buy(self):
        position = Position(
            symbol="035420",
            name="NAVER",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=-3.5,
            current_price=197200,
            ma20=215000,
            ma60=217000,
            ma20_distance=-8.3,
            ma60_distance=-9.1,
            source="holding",
            sector="플랫폼",
        )

        context = evaluate_position_relation_rules(
            position,
            portfolio_summary([position]),
            external_signals={
                "dartDisclosures": {"035420": {"reportName": "정정 공시", "receiptDate": "20260706"}},
                "newsHeadlines": {"035420": {"items": [{"title": "NAVER governance update"}]}},
            },
        )

        active_ids = [item.get("rule_id") or item.get("ruleId") for item in context["activeRules"]]
        self.assertIn("entry.add_buy.blocked.v1", active_ids)
        blocked = next(item for item in context["activeRules"] if item.get("rule_id") == "entry.add_buy.blocked.v1")
        self.assertIn("추가매수보다 회복 조건 확인 우선", blocked["evidence"])

    def test_active_investment_opinion_uses_news_and_disclosures(self):
        position = Position(
            symbol="035420",
            name="NAVER",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=-12.0,
            current_price=188000,
            ma20=215000,
            ma60=217000,
            ma20_distance=-12.6,
            ma60_distance=-13.4,
            source="holding",
            sector="플랫폼",
        )
        external_signals = {
            "dartDisclosures": {
                "035420": {
                    "provider": "OpenDART",
                    "reportName": "주요사항보고서(유상증자결정)",
                    "receiptNo": "20260706000123",
                    "receiptDate": "20260706",
                }
            },
            "newsHeadlines": {
                "035420": {
                    "provider": "GDELT",
                    "items": [
                        {
                            "title": "NAVER faces lawsuit and weak demand risk",
                            "domain": "example.test",
                            "url": "https://example.test/naver-risk",
                        }
                    ],
                }
            },
        }
        relation_context = evaluate_position_relation_rules(
            position,
            portfolio_summary([position]),
            external_signals=external_signals,
        )

        opinion = build_active_investment_opinion(
            position,
            relation_context=relation_context,
            external_signals=external_signals,
        ).to_dict()

        self.assertIn(opinion["action"], {"HOLD", "TRIM", "SELL"})
        self.assertGreater(opinion["scoreBreakdown"]["riskScore"], opinion["scoreBreakdown"]["supportScore"])
        self.assertTrue(opinion["sourceUrls"])
        self.assertTrue(any("dart.fss.or.kr" in url for url in opinion["sourceUrls"]))
        self.assertTrue(opinion["evidence"] or opinion["counterEvidence"])
        self.assertEqual("BUY|ADD|HOLD|TRIM|SELL|AVOID", opinion["promptContract"]["requiredDecision"])

    def test_research_evidence_includes_news_disclosure_and_company_facts(self):
        position = Position(
            symbol="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            market_value=1000,
            profit_loss_rate=4.2,
            current_price=215,
            ma20=220,
            ma60=198,
            ma20_distance=-2.3,
            ma60_distance=8.6,
            source="holding",
            sector="AI/플랫폼",
        )
        external_signals = {
            "newsHeadlines": {
                "AAPL": {
                    "provider": "GDELT",
                    "items": [{
                        "title": "Apple reports record revenue and strong demand",
                        "url": "https://example.test/aapl-news",
                        "domain": "example.test",
                        "seenDate": "20260708100000",
                    }],
                }
            },
            "secFilings": {
                "AAPL": {
                    "provider": "SEC EDGAR",
                    "symbol": "AAPL",
                    "cik": "0000320193",
                    "companyName": "Apple Inc.",
                    "latestFiling": {
                        "form": "10-Q",
                        "filingDate": "2026-05-01",
                        "accessionNumber": "0000320193-26-000002",
                        "primaryDocument": "aapl-20260328.htm",
                    },
                    "facts": {
                        "revenue": {"value": 95359000000, "end": "2026-03-28", "form": "10-Q"},
                        "netIncome": {"value": 24780000000, "end": "2026-03-28", "form": "10-Q"},
                    },
                }
            },
        }

        context = evaluate_position_relation_rules(
            position,
            portfolio_summary([position], fx_rates={"USD": 1400}),
            external_signals=external_signals,
        )

        evidence = context["facts"]["researchEvidence"]
        kinds = {item["kind"] for item in evidence}
        self.assertIn("news", kinds)
        self.assertIn("filing", kinds)
        self.assertIn("financial-fact", kinds)
        self.assertIn("secFiling", context["facts"])
        financial = next(item for item in evidence if item["kind"] == "financial-fact")
        self.assertIn("매출 $95.4B", financial["summary"])
        self.assertIn("순이익 $24.8B", financial["summary"])
        self.assertTrue(any("sec.gov" in item["url"] for item in evidence if item["kind"] == "filing"))
        self.assertEqual(evidence, context["promptContext"]["facts"]["researchEvidence"])

    def test_ontology_relation_rules_use_stored_direct_news_context(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=0.0,
            current_price=90000,
            ma20=93000,
            ma60=88000,
            ma20_distance=-3.2,
            ma60_distance=2.3,
            change_rate=-1.8,
            volume_ratio=1.4,
            source="holding",
            sector="반도체",
        )
        context = evaluate_position_relation_rules(
            position,
            portfolio_summary([position], fx_rates={"KRW": 1}),
            external_signals={
                "researchEvidence": {
                    "005930": [{
                        "evidenceId": "research:005930:news:risk-direct",
                        "symbol": "005930",
                        "kind": "news",
                        "source": "Reuters",
                        "title": "Samsung Electronics faces weak memory demand risk",
                        "summary": "Analysts warn memory demand may slow.",
                        "url": "https://example.test/samsung-risk",
                        "publishedAt": "2026-07-09T00:00:00Z",
                        "polarity": "risk",
                        "impactScore": 10,
                        "confidence": 0.82,
                        "payload": {
                            "relationScope": "direct",
                            "relevanceScore": 94,
                            "sourceReliability": 0.78,
                            "matchedAliases": ["Samsung Electronics"],
                            "topicTags": ["memory"],
                            "directMention": True,
                        },
                    }]
                }
            },
        )

        active_ids = [item.get("rule_id") or item.get("ruleId") for item in context["activeRules"]]
        self.assertIn("news.direct_risk.price_confirmed.v1", active_ids)
        self.assertEqual(1, context["facts"]["directRiskNewsCount"])
        self.assertEqual("eventRisk", context["decision"]["actionGroup"])
        self.assertEqual("EVENT_RISK_REVIEW", context["executionPlan"]["primaryAction"])
        self.assertEqual(context["facts"]["researchEvidence"], context["promptContext"]["facts"]["researchEvidence"])

    def test_decisions_include_active_opinion_and_research_prompt_context(self):
        position = Position(
            symbol="035420",
            name="NAVER",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=-12.0,
            current_price=188000,
            ma20=215000,
            ma60=217000,
            ma20_distance=-12.6,
            ma60_distance=-13.4,
            source="holding",
            sector="플랫폼",
        )
        external_signals = {
            "dartDisclosures": {
                "035420": {
                    "provider": "OpenDART",
                    "reportName": "주요사항보고서(유상증자결정)",
                    "receiptNo": "20260706000123",
                    "receiptDate": "20260706",
                }
            },
            "newsHeadlines": {
                "035420": {
                    "provider": "GDELT",
                    "items": [{"title": "NAVER faces lawsuit and weak demand risk", "url": "https://example.test/naver-risk"}],
                }
            },
        }

        decision = decisions_for_positions([position], portfolio_summary([position]), external_signals=external_signals)[0]

        active = decision.active_investment_opinion
        self.assertIn(active["action"], {"HOLD", "TRIM", "SELL"})
        self.assertEqual(active["action"], decision.ai_context["activeInvestmentOpinion"]["action"])
        self.assertTrue(active["sourceUrls"])
        self.assertTrue(decision.ai_prompt_context["facts"]["researchEvidence"])
        self.assertEqual(
            "BUY|ADD|HOLD|TRIM|SELL|AVOID",
            decision.ai_prompt_context["outputSchema"]["activeInvestmentOpinion"]["action"],
        )
        self.assertEqual("ExecutionPlan", decision.ai_prompt_context["outputSchema"]["activeInvestmentOpinion"]["executionPlan"])
        self.assertTrue(decision.ai_prompt_context["executionPlan"])

    def test_execution_plan_is_abox_from_relation_rules(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=-18.1,
            current_price=2115000,
            average_price=2571000,
            ma20=2494950,
            ma60=1951967,
            ma20_distance=-15.2,
            ma60_distance=8.4,
            ma20_slope=0.2,
            ma60_slope=1.0,
            change_rate=-3.91,
            volume=5215050,
            volume_ratio=0.8,
            trade_strength=95.2,
            orderbook_bid_volume=3371,
            orderbook_ask_volume=1215,
            bid_ask_imbalance=47.0,
            foreign_buy_volume=8922904,
            foreign_sell_volume=11937997,
            foreign_net_volume=-3015093,
            institution_buy_volume=12816837,
            institution_sell_volume=11845806,
            institution_net_volume=971031,
            individual_buy_volume=11457143,
            individual_sell_volume=9425438,
            individual_net_volume=2031705,
            sellable_quantity=4,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], fx_rates={"KRW": 1})

        context = evaluate_position_relation_rules(position, portfolio)
        plan = context["executionPlan"]

        self.assertEqual("ExecutionPlan", plan["tboxClass"])
        self.assertEqual("TRIM_OR_SELL_REVIEW", plan["primaryAction"])
        self.assertIn("20일선 회복 전 추가매수", plan["blockedActions"])
        self.assertTrue(any("60일선" in item for item in plan["counterSignals"]))
        self.assertEqual(2571000, plan["sourceFacts"]["averagePrice"])
        self.assertEqual(8922904, context["facts"]["foreignBuyVolume"])
        self.assertEqual(11937997, context["facts"]["foreignSellVolume"])
        self.assertEqual(-3015093, plan["sourceFacts"]["foreignNetVolume"])
        self.assertEqual(12816837, plan["sourceFacts"]["institutionBuyVolume"])
        self.assertEqual(9425438, plan["sourceFacts"]["individualSellVolume"])
        active = build_active_investment_opinion(position, relation_context=context).to_dict()
        self.assertEqual(plan["primaryAction"], active["executionPlan"]["primaryAction"])

        graph = build_portfolio_ontology([position], portfolio)
        payload = graph.to_dict()
        plan_entities = [item for item in graph.entities if item.kind == "execution-plan"]
        plan_relations = [item.relation_type for item in graph.relations if "execution-plan" in str((item.properties or {}).get("source") or "")]
        card = next(item for item in payload["reasoningCards"] if item["symbol"] == "000660")

        self.assertTrue(plan_entities)
        self.assertEqual("ExecutionPlan", (plan_entities[0].properties or {}).get("tboxClass"))
        self.assertIn("HAS_EXECUTION_PLAN", plan_relations)
        self.assertIn("HAS_PRIMARY_ACTION", plan_relations)
        self.assertIn("BLOCKS_ACTION", plan_relations)
        self.assertIn("WEAKENS_ACTION_IF", plan_relations)
        self.assertIn("REQUIRES_NEXT_CHECK", plan_relations)
        self.assertEqual("TRIM_OR_SELL_REVIEW", payload["executionPlans"][0]["primaryAction"])
        self.assertEqual("TRIM_OR_SELL_REVIEW", card["executionPlans"][0]["primaryAction"])

    def test_ontology_trend_dynamics_classifies_support_retest(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=-11.3,
            current_price=291000,
            average_price=327000,
            ma20=327850,
            ma60=287367,
            ma20_distance=-11.2,
            ma60_distance=1.3,
            ma20_slope=-0.1,
            ma60_slope=0.5,
            change_rate=0.4,
            trade_strength=116.4,
            orderbook_bid_volume=44544,
            orderbook_ask_volume=151603,
            bid_ask_imbalance=-54.6,
            sector="반도체",
        )

        context = evaluate_position_relation_rules(position, portfolio_summary([position], fx_rates={"KRW": 1}))
        active_ids = [item.get("rule_id") or item.get("ruleId") for item in context["activeRules"]]

        self.assertTrue(context["facts"]["supportRetest"])
        self.assertTrue(context["facts"]["mediumTermSupport"])
        self.assertFalse(context["facts"]["breakdownAcceleration"])
        self.assertEqual("60일선 지지 재확인", context["facts"]["trendDynamics"]["state"])
        self.assertIn("trend.support_retest.v1", active_ids)
        self.assertIn("trendDynamics", context["promptContext"]["inputContract"]["requiredBlocks"])

    def test_ontology_trend_dynamics_detects_breakdown_acceleration(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=-11.3,
            current_price=282000,
            average_price=327000,
            ma20=310000,
            ma60=289000,
            ma20_distance=-9.0,
            ma60_distance=-2.5,
            ma20_slope=-1.4,
            ma60_slope=-0.7,
            change_rate=-3.2,
            trade_strength=88.0,
            buy_volume=100,
            sell_volume=180,
            sector="반도체",
        )

        context = evaluate_position_relation_rules(position, portfolio_summary([position], fx_rates={"KRW": 1}))
        active_ids = [item.get("rule_id") or item.get("ruleId") for item in context["activeRules"]]

        self.assertTrue(context["facts"]["breakdownAcceleration"])
        self.assertEqual("하락 가속", context["facts"]["trendDynamics"]["state"])
        self.assertIn("trend.breakdown_acceleration.v1", active_ids)
        self.assertEqual("하락 가속 대응 점검", context["decision"]["label"])
        self.assertEqual("trend.breakdown_acceleration.v1", context["decision"]["selectedRuleId"])
        self.assertTrue(context["promptContext"]["trendDynamics"]["breakdownAcceleration"])

    def test_ontology_relation_rules_detect_temporal_failure_and_liquidity(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            quantity=20,
            sellable_quantity=20,
            market_value=5800000,
            trading_value=50000000,
            profit_loss_rate=-4.0,
            current_price=290000,
            ma20=300000,
            ma60=295000,
            ma20_distance=-3.3,
            ma60_distance=-1.7,
            ma20_slope=-0.8,
            ma60_slope=-0.2,
            change_rate=-1.8,
            volume_ratio=1.5,
            buy_volume=90,
            sell_volume=150,
            bid_ask_imbalance=-18,
            sector="반도체",
        )
        previous = {
            "currentPrice": 304000,
            "profitLossRate": -1.0,
            "ma20Distance": 1.2,
            "ma60Distance": 0.8,
        }

        context = evaluate_position_relation_rules(
            position,
            portfolio_summary([position], fx_rates={"KRW": 1}),
            previous_state=previous,
            previous_decision={"selectedRuleId": "trend.recovery_attempt.v1", "exitPressure": 44},
        )
        active_ids = [item.get("rule_id") or item.get("ruleId") for item in context["activeRules"]]

        self.assertIn("breakout.failure.v1", active_ids)
        self.assertIn("support.retest.failed.v1", active_ids)
        self.assertIn("liquidity.exit_capacity.v1", active_ids)
        self.assertTrue(context["facts"]["hasPreviousState"])
        self.assertLess(context["facts"]["ma20DistanceDeltaPct"], 0)
        self.assertGreater(context["facts"]["liquidityRiskScore"], 0)

    def test_ontology_loss_guard_requires_negative_pnl(self):
        position = Position(
            symbol="MSTR",
            name="Strategy",
            market="US",
            currency="USD",
            market_value=1000,
            profit_loss_rate=14.0,
            sellable_quantity=1,
            current_price=101.31,
            ma20=106.9,
            ma60=144.09,
            ma20_distance=-5.2,
            ma60_distance=-29.7,
            sector="BTC",
        )
        context = evaluate_position_relation_rules(position, portfolio_summary([position], fx_rates={"USD": 1400}))

        active_ids = [item.get("rule_id") or item.get("ruleId") for item in context["activeRules"]]
        self.assertNotIn("holding.loss_guard.breakdown.v1", active_ids)
        self.assertNotIn("손실", context["decision"]["label"])
        self.assertNotIn("손절", context["decision"]["label"])

    def test_ontology_relation_rules_report_domestic_microstructure_missing_data(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=-9.5,
            sellable_quantity=10,
            current_price=90000,
            ma20=100000,
            ma60=110000,
            ma20_distance=-10,
            ma60_distance=-18.2,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], fx_rates={"KRW": 1})

        context = evaluate_position_relation_rules(position, portfolio)

        missing_labels = [item["label"] for item in context["missingData"]]
        self.assertIn("체결강도", missing_labels)
        self.assertIn("방향별 매수/매도 체결량", missing_labels)
        self.assertIn("투자자별 수급", missing_labels)

    def test_ontology_relation_rules_use_execution_proxies_for_domestic_missing_data(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=-8.1,
            sellable_quantity=10,
            current_price=290000,
            ma20=330000,
            ma60=290000,
            ma20_distance=-10.4,
            ma60_distance=2.9,
            trade_strength=72.8,
            orderbook_bid_volume=520618,
            orderbook_ask_volume=102790,
            bid_ask_imbalance=67,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], fx_rates={"KRW": 1})

        context = evaluate_position_relation_rules(position, portfolio)

        missing_labels = [item["label"] for item in context["missingData"]]
        effects = {item["label"]: item["effect"] for item in context["missingData"]}
        self.assertNotIn("방향별 매수/매도 체결량", missing_labels)
        self.assertIn("투자자별 수급", missing_labels)
        self.assertIn("중립으로 처리", effects["투자자별 수급"])

    def test_ontology_relation_rules_distinguish_zero_investor_flow_from_missing_collection(self):
        position = Position(
            symbol="035420",
            name="NAVER",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=-3.6,
            sellable_quantity=5,
            current_price=197200,
            ma20=213940,
            ma60=217075,
            ma20_distance=-7.8,
            ma60_distance=-9.2,
            trade_strength=90,
            buy_volume=100,
            sell_volume=120,
            quote_status="KIS 현재가, 체결강도, 방향별 체결량, 투자자별 수급 반영",
            market_signal_coverage={
                "ccnl": {
                    "stage": "ccnl",
                    "status": "available",
                    "fields": ["tradeStrength", "buyVolume", "sellVolume"],
                    "nonZeroFields": ["tradeStrength", "buyVolume", "sellVolume"],
                },
                "investor": {
                    "stage": "investor",
                    "status": "available",
                    "fields": ["foreignNetVolume", "institutionNetVolume", "individualNetVolume"],
                    "nonZeroFields": [],
                },
            },
        )
        context = evaluate_position_relation_rules(position, portfolio_summary([position], fx_rates={"KRW": 1}))

        investor_item = next(item for item in context["missingData"] if item["label"] == "투자자별 수급")

        self.assertEqual("zero", investor_item["status"])
        self.assertIn("응답은 있었지만", investor_item["effect"])
        self.assertEqual("zero", context["facts"]["dataAvailability"]["investorFlow"]["status"])

    def test_ontology_settings_drive_rule_metadata_and_ai_prompt_template(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=-9.5,
            sellable_quantity=10,
            current_price=90000,
            ma20=100000,
            ma60=110000,
            ma20_distance=-10,
            ma60_distance=-18.2,
            trade_strength=82,
            buy_volume=420,
            sell_volume=580,
            foreign_net_volume=-210,
            institution_net_volume=-160,
            individual_net_volume=370,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], fx_rates={"KRW": 1})

        context = evaluate_position_relation_rules(
            position,
            portfolio,
            settings={
                "ontologyRelationRules": "holding.loss_guard.breakdown.v1 | 사용자 손실 규칙 | custom condition | CUSTOM_LOSS | custom_signal | 사용자 prompt hint",
                "aiPromptTemplates": "\n".join([
                    "[holdingTiming]",
                    "label=사용자 보유 질문",
                    "version=custom-prompt-v9",
                    "purpose=사용자 목적",
                    "system=custom system",
                    "user=custom user",
                    "guardrails=custom guard 1 / custom guard 2",
                ]),
                "aiPromptPolicy": "providedDataOnly=1\ncustomPolicy=1",
            },
        )

        active_rules = context["activeRules"]
        loss_rule = next(item for item in active_rules if item.get("rule_id") == "holding.loss_guard.breakdown.v1")
        self.assertEqual("사용자 손실 규칙", loss_rule["label"])
        self.assertEqual("CUSTOM_LOSS", loss_rule["relation_type"])
        self.assertEqual("custom_signal", loss_rule["signal_type"])
        self.assertEqual("사용자 prompt hint", loss_rule["prompt_hint"])
        trend_rule = next(item for item in active_rules if item.get("rule_id") == "holding.trend_flow.confirmation.v1")
        self.assertEqual("EVIDENCE_SUPPORT", trend_rule["relation_type"])
        prompt_context = context["promptContext"]
        prompt_template = prompt_context["promptTemplate"]
        self.assertEqual("custom-prompt-v9", prompt_context["promptVersion"])
        self.assertEqual("사용자 보유 질문", prompt_template["label"])
        self.assertEqual("custom user", prompt_template["userPrompt"])
        self.assertIn("customPolicy=1", prompt_context["promptPolicy"])
        self.assertIn("custom guard 1", prompt_context["guardrails"])

    def test_flow_lens_preserves_provider_portfolio_market_breakdown(self):
        account = AccountConfig("main", "메인", "toss", "https://example.test", "id", "secret", "1", [])
        kr_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000000,
        })
        portfolio = portfolio_summary([kr_position], 10, "USD", fx_rates={"KRW": 1, "USD": 1400})

        class Repo:
            def load(self):
                return [account]

        def snapshot_builder(_account):
            return AccountSnapshot(
                "main",
                "메인",
                "toss",
                "live",
                "토스 계좌 동기화",
                utc_now_iso(),
                portfolio,
                [kr_position],
                decisions_for_positions([kr_position], portfolio),
            )

        service = FlowLensService(
            Repo(),
            snapshot_builder,
            settings_provider=lambda: {"fxRates": "KRW=1\nUSD=1400"},
            fx_rates_provider=lambda _settings: {"KRW": 1, "USD": 1400},
        )

        payload = service.snapshot()
        us_market = next(item for item in payload["portfolio"]["markets"] if item["key"] == "US")
        kr_market = next(item for item in payload["portfolio"]["markets"] if item["key"] == "KR")

        self.assertEqual(14000, us_market["cash"])
        self.assertEqual(0, us_market["invested"])
        self.assertEqual(1000000, kr_market["invested"])
        self.assertEqual(portfolio.total, payload["portfolio"]["total"])

    def test_normalize_position_preserves_flow_metrics(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "currency": "KRW",
            "currentPrice": 72000,
            "volume": "1000",
            "volumeRatio": "1.7",
            "buyVolume": "620",
            "sellVolume": "380",
            "orderbookBidVolume": "900",
            "orderbookAskVolume": "300",
            "foreignBuyVolume": "420",
            "foreignSellVolume": "275",
            "institutionBuyVolume": "310",
            "institutionSellVolume": "228",
            "individualBuyVolume": "230",
            "individualSellVolume": "450",
            "executionStrength": "128.4",
            "marketValue": 720000,
        })

        self.assertEqual(128.4, position.trade_strength)
        self.assertEqual(1000, position.volume)
        self.assertEqual(1.7, position.volume_ratio)
        self.assertEqual(620, position.buy_volume)
        self.assertEqual(380, position.sell_volume)
        self.assertEqual(900, position.orderbook_bid_volume)
        self.assertEqual(300, position.orderbook_ask_volume)
        self.assertEqual(50, position.bid_ask_imbalance)
        self.assertEqual(420, position.foreign_buy_volume)
        self.assertEqual(275, position.foreign_sell_volume)
        self.assertEqual(145, position.foreign_net_volume)
        self.assertEqual(310, position.institution_buy_volume)
        self.assertEqual(228, position.institution_sell_volume)
        self.assertEqual(82, position.institution_net_volume)
        self.assertEqual(230, position.individual_buy_volume)
        self.assertEqual(450, position.individual_sell_volume)
        self.assertEqual(-220, position.individual_net_volume)
        self.assertEqual(72000000, position.trading_value)

    def test_technical_indicators_are_calculated_from_candles(self):
        candles = [
            {
                "timestamp": "2026-01-" + str(index + 1).zfill(2) + "T09:00:00+09:00",
                "closePrice": str(index + 1),
                "volume": str(1000 + index),
            }
            for index in range(28)
        ]

        indicators = technical_indicators_from_candles(candles)

        self.assertEqual(28, indicators["currentPrice"])
        self.assertEqual(26, indicators["ma5"])
        self.assertEqual(18.5, indicators["ma20"])
        self.assertAlmostEqual(((28 / 18.5) - 1) * 100, indicators["ma20Distance"])
        self.assertGreater(indicators["ma20Slope"], 0)
        self.assertEqual(1027, indicators["volume"])
        self.assertGreater(indicators["volumeRatio"], 1)

    def test_monitor_spike_messages_include_flow_context(self):
        previous_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000000,
            "quantity": 20,
            "currentPrice": 50000,
            "profitLossRate": 5,
            "sellableQuantity": 20,
            "sector": "반도체",
        })
        current_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1200000,
            "quantity": 20,
            "currentPrice": 60000,
            "profitLossRate": 9,
            "sellableQuantity": 20,
            "tradeStrength": 128,
            "volume": 30000,
            "volumeRatio": 1.8,
            "foreignBuyVolume": 420000,
            "foreignSellVolume": 275000,
            "institutionBuyVolume": 310000,
            "institutionSellVolume": 228000,
            "sector": "반도체",
        })
        previous_portfolio = portfolio_summary([previous_position])
        current_portfolio = portfolio_summary([current_position])
        previous_snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            previous_portfolio,
            [previous_position],
            decisions_for_positions([previous_position], previous_portfolio),
        )
        current_generated_at = "2026-07-03T06:58:00Z"
        current_snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            current_generated_at,
            current_portfolio,
            [current_position],
            decisions_for_positions([current_position], current_portfolio),
        )

        events = RealtimeMonitor().events_for_snapshot(current_snapshot, previous_snapshot.to_monitor_state())
        insight = self.insight_event(events, "005930")
        pnl_message = self.insight_source_message(insight, "monitorPnlChange")
        value_message = self.insight_source_message(insight, "monitorValueChange")

        self.assertTrue(all(event.generated_at == current_generated_at for event in events))
        self.assertEqual("investmentInsight", insight.rule)
        self.assertIn("monitorPnlChange", self.insight_source_rules(insight))
        self.assertIn("monitorValueChange", self.insight_source_rules(insight))
        self.assertFalse(any(event.rule == "monitorPnlChange" for event in events))
        self.assertIn("수급: 거래량 30,000(1.8x), 거래액 18억 원", pnl_message)
        self.assertIn("투자자:", pnl_message)
        self.assertIn("외국인: 순매수 145,000주, 매수 420,000주, 매도 275,000주", pnl_message)
        self.assertIn("기관: 순매수 82,000주, 매수 310,000주, 매도 228,000주", pnl_message)
        self.assertIn("수급: 거래량 30,000(1.8x), 거래액 18억 원", value_message)
        self.assertIn("투자자:", value_message)
        self.assertIn("외국인: 순매수 145,000주, 매수 420,000주, 매도 275,000주", value_message)
        self.assertIn("기관: 순매수 82,000주, 매수 310,000주, 매도 228,000주", value_message)

    def test_investment_insight_promotes_reference_data_and_action_title(self):
        position = normalize_position({
            "symbol": "MSTR",
            "name": "Strategy",
            "market": "US",
            "currency": "USD",
            "marketValue": 10130,
            "quantity": 100,
            "sellableQuantity": 100,
            "averagePrice": 90.2,
            "currentPrice": 101.3,
            "profitLossRate": 12.2,
            "volume": 90863,
            "volumeRatio": 1.4,
            "tradingValue": 3543834187,
            "ma20": 108.07,
            "ma60": 144.09,
            "sector": "디지털자산",
        })
        external_signals = {
            "cryptoMarkets": {
                "bitcoin": {
                    "provider": "CoinGecko",
                    "symbol": "BTC",
                    "price": 108000,
                    "volume24h": 42000000000,
                    "change24h": 2.6,
                    "change7d": 7.1,
                }
            }
        }
        portfolio = portfolio_summary([position])
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio,
            [position],
            decisions_for_positions([position], portfolio, external_signals=external_signals),
            external_signals=external_signals,
        )

        events = RealtimeMonitor().events_for_snapshot(snapshot, {})
        insight = self.insight_event(events, "MSTR")
        db_path = Path(self.temp.name) / "service.db"
        message = SQLiteNotificationTemplateStore(db_path).render(insight.rule, alert_context(insight))

        self.assertEqual("investmentInsight", insight.rule)
        self.assertIn("holdingTiming", self.insight_source_rules(insight))
        self.assertIn("상태: 분할매도 권장", "\n".join(insight.lines))
        self.assertIn("현재가: $101.3", "\n".join(insight.lines))
        self.assertIn("평균매입가: $90.2", "\n".join(insight.lines))
        self.assertIn("수익률: +12.2%", "\n".join(insight.lines))
        self.assertIn("보유 수량: 100주", "\n".join(insight.lines))
        self.assertIn("매도가능 수량: 100주", "\n".join(insight.lines))
        self.assertIn("종목 평가금액: $10,130", "\n".join(insight.lines))
        self.assertIn("계좌 평가금액: 1,418만 원", "\n".join(insight.lines))
        self.assertIn("수급: 거래량 90,863(1.4x), 거래액 $3,543,834,187", "\n".join(insight.lines))
        self.assertIn("추세: 20일선 $108.07보다 6.3% 낮음", "\n".join(insight.lines))
        self.assertIn("권장 액션: 분할매도", "\n".join(insight.lines))
        self.assertIn("<b>[관찰] 💰 Strategy: 수익 +12.2%: 분할매도·리밸런싱 점검</b>", message)
        self.assertIn("현재가", message)
        self.assertIn("평균매입가", message)
        self.assertIn("수익률", message)
        self.assertIn("보유", message)

    def test_investment_insight_uses_stable_keys_for_score_only_relation_changes(self):
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio_summary([]),
        )

        def insight_for_score(score):
            event = AlertEvent(
                "main",
                "메인",
                "ALERT",
                "watchlistOntologySignal",
                "main:watchlist-ontology:005380:riskWatch:data.conflict.v1+trend.breakdown_acceleration.v1:" + str(score),
                "현대차",
                ["상태: 하락 가속 대응 점검 (" + str(score) + "점)"],
                "005380",
                metadata={
                    "watchlistOntologySignalType": "riskWatch",
                    "watchlistSignalScore": score,
                    "dataFreshness": self.fresh_data_freshness("unit-test-position"),
                },
            )
            return build_investment_insight_events(snapshot, [event])[0]

        first = insight_for_score(97.3)
        second = insight_for_score(100.0)
        first_insight = first.metadata["ontologyInsight"]
        second_insight = second.metadata["ontologyInsight"]

        self.assertEqual(first_insight["cadenceKey"], second_insight["cadenceKey"])
        self.assertEqual(first_insight["sourceEventKeys"], second_insight["sourceEventKeys"])
        self.assertEqual(["main:watchlist-ontology:005380:riskWatch:data.conflict.v1+trend.breakdown_acceleration.v1"], first_insight["sourceEventKeys"])
        self.assertEqual("95", first_insight["scoreBucket"])
        self.assertEqual("100", second_insight["scoreBucket"])

    def test_investment_insight_loss_title_wins_over_rebalance_signal(self):
        event = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "investmentInsight",
            "main:insight:DNT",
            "하락 테스트",
            [
                "상태 손절·분할축소 권장 (92점)",
                "현재가: $87.19",
                "평단가: $118",
                "수익률: -26.1%",
                "권장 액션: 손절·분할축소 우선, 20일선 회복 전 추가매수 보류",
                "인사이트 유형: 리스크 관리",
                "핵심 결론: 하락 테스트의 손실 관리와 리밸런싱 규칙이 함께 성립했습니다.",
            ],
            "DNT",
            metadata={
                "ontologyRelationContext": {
                    "decision": {"label": "손절·분할축소 권장", "score": 92},
                    "activeRules": [
                        {"ruleId": "entry.add_buy.blocked.v1", "label": "보유 종목 + 추세 훼손 -> 추가매수 보류"},
                        {"ruleId": "holding.concentration.rebalance.v1", "label": "업종 집중 + 보유 비중 과대 -> 리밸런싱 점검"},
                    ],
                }
            },
        )

        message = render_notification(NotificationTemplate("investmentInsight", "{telegramMessage}"), alert_context(event))

        self.assertIn("<b>[주의] 🛡️ 하락 테스트: 손실 -26.1%: 손절·분할축소 점검</b>", message)
        self.assertNotIn("💰 분할매도·리밸런싱 점검", message)

    def test_holding_timing_status_includes_model_score_parentheses(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000000,
            "quantity": 10,
            "averagePrice": 110000,
            "currentPrice": 100000,
            "profitLossRate": -9,
            "sector": "반도체",
        })
        portfolio = portfolio_summary([position])
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio,
            [position],
            decisions_for_positions([position], portfolio),
        )

        event = RealtimeMonitor().holding_timing_events(snapshot)[0]
        message = event.message()

        self.assertRegex(message, r"상태 .+ \([0-9.]+점\)")
        self.assertIn("현재가: 100,000원", message)
        self.assertIn("평균매입가: 110,000원", message)
        self.assertIn("수익률: -9.0%", message)
        self.assertTrue(any("상태 " in item and "점)" in item for item in event.criteria))
        self.assertTrue(any("수익률 -9.0%" in item for item in event.criteria))

    def test_watchlist_buy_candidate_uses_dedicated_message_type(self):
        watch = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "currentPrice": 185,
            "volume": 1200000,
            "volumeRatio": 1.4,
            "sector": "AI/플랫폼",
        })
        portfolio = portfolio_summary([])
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio,
            [],
            [],
            watchlist=[watch],
        )

        events = RealtimeMonitor({
            "buyScoreFormula": "80",
            "alertThresholds": "modelBuyScore=99\nwatchlistBuyScore=74",
        }).events_for_snapshot(snapshot, {})

        candidate = self.insight_event(events, "AAPL")
        self.assertEqual("AAPL", candidate.symbol)
        source_message = self.insight_source_message(candidate, "watchlistBuyCandidate")
        self.assertEqual("investmentInsight", candidate.rule)
        self.assertIn("watchlistBuyCandidate", self.insight_source_rules(candidate))
        self.assertIn("관심종목 매수 후보", source_message)
        self.assertIn("현재가: $185", source_message)
        self.assertNotIn("평단가", source_message)
        self.assertNotIn("수익률", source_message)
        self.assertTrue(any("관심종목 매수 기준" in item for item in source_message.splitlines()))
        db_path = Path(self.temp.name) / "service.db"
        message = SQLiteNotificationTemplateStore(db_path).render(candidate.rule, alert_context(candidate))
        self.assertIn("<b>[관찰] 🟢 Apple: 분할매수 후보: 진입 조건 점검</b>", message)
        self.assertNotIn("투자 인사이트: 대응 기준 점검", message)
        self.assertFalse(any(event.rule == "modelBuy" for event in events))
        self.assertFalse(any(event.rule == "watchlistBuyCandidate" for event in events))

    def test_watchlist_buy_candidate_can_be_promoted_by_ontology_entry_rule(self):
        watch = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "currentPrice": 100,
            "ma20": 104,
            "ma60": 99,
            "ma20Distance": -3.8,
            "ma60Distance": 1.0,
            "volume": 1200000,
            "volumeRatio": 1.1,
            "tradeStrength": 118,
            "bidAskImbalance": 12,
            "foreignNetVolume": 180000,
            "institutionNetVolume": 90000,
            "individualNetVolume": -210000,
            "sector": "AI/플랫폼",
            "source": "watchlist",
        })
        portfolio = portfolio_summary([])
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio,
            [],
            [],
            watchlist=[watch],
        )

        events = RealtimeMonitor({
            "buyScoreFormula": "10",
            "alertThresholds": "modelBuyScore=99\nwatchlistBuyScore=74",
        }).events_for_snapshot(snapshot, {})

        candidate = self.insight_event(events, "AAPL")
        source_message = self.insight_source_message(candidate, "watchlistBuyCandidate")
        message = SQLiteNotificationTemplateStore(Path(self.temp.name) / "service.db").render(candidate.rule, alert_context(candidate))
        active_ids = self.insight_active_rule_ids(candidate)

        self.assertIn("watchlistBuyCandidate", self.insight_source_rules(candidate))
        self.assertIn("watchlistOntologySignal", self.insight_source_rules(candidate))
        self.assertIn("온톨로지 소액 분할매수 검토", source_message)
        self.assertIn(
            "관심종목 온톨로지 관계 신호",
            self.insight_source_message(candidate, "watchlistOntologySignal"),
        )
        self.assertIn("entry.pullback.supported.v1", active_ids)
        self.assertIn("<b>[관찰] 🟢 Apple: 분할매수 후보: 진입 조건 점검</b>", message)

    def test_watchlist_ontology_signal_promotes_risk_insight_without_buy_score(self):
        watch = normalize_position({
            "symbol": "005380",
            "name": "현대차",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 291000,
            "changeRate": -3.4,
            "ma20": 327850,
            "ma60": 287367,
            "ma20Distance": -11.2,
            "ma60Distance": 1.3,
            "ma20Slope": -1.4,
            "ma60Slope": -0.2,
            "volume": 2489768,
            "volumeRatio": 1.6,
            "tradeStrength": 116.4,
            "bidAskImbalance": -54.6,
            "foreignNetVolume": -190000,
            "institutionNetVolume": -65000,
            "individualNetVolume": 240000,
            "marketSignalCoverage": {"price": "available", "ccnl": "available", "investor": "available"},
            "sector": "자동차",
            "source": "watchlist",
        })
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio_summary([]),
            [],
            [],
            watchlist=[watch],
        )

        events = RealtimeMonitor({
            "buyScoreFormula": "10",
            "alertThresholds": "modelBuyScore=99\nwatchlistBuyScore=99",
        }).events_for_snapshot(snapshot, {})

        insight = self.insight_event(events, "005380")
        source_message = self.insight_source_message(insight, "watchlistOntologySignal")
        active_ids = self.insight_active_rule_ids(insight)
        ontology = insight.metadata.get("ontologyInsight") or {}

        self.assertIn("watchlistOntologySignal", self.insight_source_rules(insight))
        self.assertNotIn("watchlistBuyCandidate", self.insight_source_rules(insight))
        self.assertIn("관심종목 온톨로지 관계 신호", source_message)
        self.assertIn("신규 진입 보류", source_message)
        self.assertIn("trend.breakdown_acceleration.v1", active_ids)
        self.assertEqual("riskIncrease", ontology.get("insightType"))
        self.assertFalse(any(event.rule == "watchlistOntologySignal" for event in events))

    def test_legacy_signal_rule_controls_ontology_insight_sources(self):
        watch = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "currentPrice": 185,
            "volume": 1200000,
            "volumeRatio": 1.4,
            "sector": "AI/플랫폼",
        })
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio_summary([]),
            [],
            [],
            watchlist=[watch],
        )

        events = RealtimeMonitor({
            "buyScoreFormula": "80",
            "alertRules": "watchlistBuyCandidate=0\ninvestmentInsight=1",
            "alertThresholds": "modelBuyScore=99\nwatchlistBuyScore=74",
        }).events_for_snapshot(snapshot, {})

        insight = self.insight_event(events, "AAPL")
        self.assertNotIn("watchlistBuyCandidate", self.insight_source_rules(insight))
        self.assertFalse(any(event.rule == "watchlistBuyCandidate" for event in events))

    def test_investment_insight_rule_controls_final_investment_dispatch(self):
        watch = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "currentPrice": 185,
            "volume": 1200000,
            "volumeRatio": 1.4,
            "sector": "AI/플랫폼",
        })
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio_summary([]),
            [],
            [],
            watchlist=[watch],
        )

        events = RealtimeMonitor({
            "buyScoreFormula": "80",
            "alertRules": "watchlistBuyCandidate=1\ninvestmentInsight=0",
            "alertThresholds": "modelBuyScore=99\nwatchlistBuyScore=74",
        }).events_for_snapshot(snapshot, {})

        self.assertFalse(any(event.rule == "investmentInsight" for event in events))
        self.assertFalse(any(event.rule == "watchlistBuyCandidate" for event in events))

    def test_realtime_monitor_recomputes_decisions_with_user_formulas(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000000,
            "quantity": 10,
            "currentPrice": 100000,
            "profitLossRate": -9,
            "sector": "반도체",
        })
        portfolio = portfolio_summary([position])
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio,
            [position],
            decisions_for_positions([position], portfolio),
        )
        monitor = RealtimeMonitor({
            "lossCutScoreFormula": "91",
            "notificationScoreFormula": "baseScore + symbolScore",
        })

        rescored = monitor.snapshot_with_strategy_scores(snapshot)
        stamped = monitor.stamp_events(snapshot, [
            AlertEvent("main", "메인", "WATCH", "holdingTiming", "main:timing:005930", "삼성전자", ["상태 손절 기준 확인 (91점)"], "005930")
        ])

        self.assertEqual(91, rescored.decisions[0].loss_cut_pressure)
        self.assertEqual("ontologyRelationRules", rescored.decisions[0].decision_basis)
        self.assertNotEqual(91, rescored.decisions[0].exit_pressure)
        self.assertEqual("baseScore + symbolScore", stamped[0].metadata["notificationScoreFormula"])

    def test_account_data_failure_suppresses_investment_change_alerts(self):
        live_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 864000,
            "quantity": 10,
            "currentPrice": 86400,
            "profitLossRate": 3,
            "sector": "반도체",
        })
        failed_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 864000,
            "quantity": 10,
            "currentPrice": 86400,
            "profitLossRate": 3,
            "sector": "반도체",
        })
        live_portfolio = portfolio_summary([live_position], 1250000, "KRW")
        failed_portfolio = portfolio_summary([failed_position], 0, "KRW")
        previous_live = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "토스 계좌 동기화",
            utc_now_iso(),
            live_portfolio,
            [live_position],
            decisions_for_positions([live_position], live_portfolio),
        )
        current_failed = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "demo",
            "토스 조회 실패 · Toss holdings 단계 실패 · HTTP 401 Unauthorized",
            utc_now_iso(),
            failed_portfolio,
            [failed_position],
            decisions_for_positions([failed_position], failed_portfolio),
        )

        events = RealtimeMonitor().events_for_snapshot(current_failed, previous_live.to_monitor_state())

        blocked_rules = {"monitorCashChange", "monitorPositionChange", "monitorPnlChange", "monitorValueChange", "monitorDecisionChange", "holdingTiming"}
        self.assertTrue(any(event.rule == "monitorConnection" for event in events))
        self.assertFalse(any(event.rule in blocked_rules for event in events))

    def test_connection_failure_is_watch_once_and_alert_when_repeated(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 864000,
            "quantity": 10,
            "currentPrice": 86400,
            "sector": "반도체",
        })
        portfolio = portfolio_summary([position], 0, "KRW")
        previous_live = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "토스 계좌 동기화",
            utc_now_iso(),
            portfolio,
            [position],
            decisions_for_positions([position], portfolio),
        )
        first_failed = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "demo",
            "토스 조회 실패 · Toss accounts 단계 실패 · HTTP 401 Unauthorized",
            utc_now_iso(),
            portfolio,
            [position],
            decisions_for_positions([position], portfolio),
            metadata={"toss": {"stageFailures": {"accounts": {"count": 2, "lastError": "HTTP 401 Unauthorized", "recovered": 0}}, "authRefreshes": 1}},
        )
        monitor = RealtimeMonitor()

        first_event = next(event for event in monitor.connection_events(first_failed, previous_live.to_monitor_state()) if event.key.startswith("main:connection:demo"))
        second_failed = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "demo",
            "토스 조회 실패 · Toss accounts 단계 실패 · HTTP 401 Unauthorized",
            utc_now_iso(),
            portfolio,
            [position],
            decisions_for_positions([position], portfolio),
            metadata={"toss": {"stageFailures": {"accounts": {"count": 2, "lastError": "HTTP 401 Unauthorized", "recovered": 0}}, "authRefreshes": 1}},
        )
        second_event = next(event for event in monitor.connection_events(second_failed, first_failed.to_monitor_state()) if event.key.startswith("main:connection:demo"))

        self.assertEqual("WATCH", first_event.severity)
        self.assertIn("상태 일시 인증 실패", first_event.lines)
        self.assertIn("연속 실패 1회", first_event.lines)
        self.assertEqual(1, first_event.metadata["connectionFailureStreak"])
        self.assertEqual("ALERT", second_event.severity)
        self.assertIn("상태 연속 인증 실패", second_event.lines)
        self.assertIn("연속 실패 2회", second_event.lines)
        self.assertEqual(2, second_event.metadata["connectionFailureStreak"])

    def test_snapshot_collected_event_preserves_toss_failure_metadata(self):
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 864000,
            "quantity": 10,
            "currentPrice": 86400,
            "sector": "반도체",
        })
        portfolio = portfolio_summary([position], 0, "KRW")
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "demo",
            "토스 조회 실패 · Toss accounts 단계 실패 · HTTP 401 Unauthorized",
            utc_now_iso(),
            portfolio,
            [position],
            decisions_for_positions([position], portfolio),
            metadata={"connectionFailureStreak": 2, "toss": {"stageFailures": {"accounts": {"count": 2}}}},
        )

        event = snapshot_collected_event(snapshot)

        self.assertEqual(2, event.payload["metadata"]["connectionFailureStreak"])
        self.assertEqual(2, event.payload["metadata"]["toss"]["stageFailures"]["accounts"]["count"])

    def test_recovery_from_account_data_failure_starts_new_baseline(self):
        failed_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 864000,
            "quantity": 10,
            "currentPrice": 86400,
            "profitLossRate": 3,
            "sector": "반도체",
        })
        live_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 864000,
            "quantity": 10,
            "currentPrice": 86400,
            "profitLossRate": 3,
            "sector": "반도체",
        })
        failed_portfolio = portfolio_summary([failed_position], 1250000, "KRW")
        live_portfolio = portfolio_summary([live_position], 0, "KRW")
        previous_failed = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "demo",
            "토스 조회 실패 · Toss holdings 단계 실패 · HTTP 401 Unauthorized",
            utc_now_iso(),
            failed_portfolio,
            [failed_position],
            decisions_for_positions([failed_position], failed_portfolio),
        )
        current_live = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "토스 계좌 동기화",
            utc_now_iso(),
            live_portfolio,
            [live_position],
            decisions_for_positions([live_position], live_portfolio),
        )

        events = RealtimeMonitor().events_for_snapshot(current_live, previous_failed.to_monitor_state())

        baseline_rules = {"monitorCashChange", "monitorPositionChange", "monitorPnlChange", "monitorValueChange", "monitorDecisionChange"}
        self.assertTrue(any(event.rule == "monitorConnection" for event in events))
        self.assertFalse(any(event.rule in baseline_rules for event in events))

    def test_monitor_trend_change_uses_moving_average_data(self):
        previous_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 98000,
            "averagePrice": 100000,
            "marketValue": 1000000,
            "quantity": 10,
            "profitLossRate": 5,
            "ma20": 100000,
            "ma60": 105000,
            "sector": "반도체",
        })
        current_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 106000,
            "averagePrice": 100000,
            "marketValue": 1000000,
            "quantity": 10,
            "profitLossRate": 5,
            "tradeStrength": 121,
            "tradingValue": 2400000000,
            "volume": 40000,
            "volumeRatio": 2.1,
            "foreignBuyVolume": 510000,
            "foreignSellVolume": 440000,
            "institutionBuyVolume": 350000,
            "institutionSellVolume": 315000,
            "ma20": 104000,
            "ma60": 103000,
            "ma20Slope": 0.8,
            "ma60Slope": 0.2,
            "sector": "반도체",
        })
        previous_portfolio = portfolio_summary([previous_position])
        current_portfolio = portfolio_summary([current_position])
        previous_snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            previous_portfolio,
            [previous_position],
            decisions_for_positions([previous_position], previous_portfolio),
        )
        current_snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            current_portfolio,
            [current_position],
            decisions_for_positions([current_position], current_portfolio),
        )

        event = self.insight_event(
            RealtimeMonitor().events_for_snapshot(current_snapshot, previous_snapshot.to_monitor_state()),
            "005930",
        )
        message = self.insight_source_message(event, "monitorTrendChange")

        self.assertEqual("WATCH", event.severity)
        self.assertIn("monitorTrendChange", self.insight_source_rules(event))
        self.assertFalse("monitorTrendChange" == event.rule)
        self.assertIn("20일선 상향 돌파", message)
        self.assertIn("60일선 상향 돌파", message)
        self.assertIn("20/60일선 골든크로스", message)
        self.assertIn("현재가: 106,000원", message)
        self.assertIn("평균매입가: 100,000원", message)
        self.assertIn("계좌 평가금액: 100만 원", message)
        self.assertIn("수익률: +5.0%", message)
        self.assertIn("추세: 20일선 104,000원보다 1.9% 높음, 60일선 103,000원보다 2.9% 높음", message)
        self.assertIn("수급: 거래량 40,000(2.1x), 거래액 24억 원", message)
        self.assertIn("투자자:", message)
        self.assertIn("외국인: 순매수 70,000주, 매수 510,000주, 매도 440,000주", message)
        self.assertIn("기관: 순매수 35,000주, 매수 350,000주, 매도 315,000주", message)
        self.assertIn("설정: 20일/60일 이동평균 돌파, 크로스, 또는 현재가가 이동평균보다 8% 이상 높거나 낮을 때", message)
        self.assertIn("20일선 상향 돌파", message)
        self.assertNotIn("괴리", message)

    def test_monitor_value_change_formats_usd_with_krw_basis(self):
        previous_position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "marketValue": 1000,
            "quantity": 4,
            "profitLossRate": 10,
            "sellableQuantity": 4,
            "sector": "AI/플랫폼",
        })
        current_position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "marketValue": 1100,
            "quantity": 4,
            "profitLossRate": 10,
            "sellableQuantity": 4,
            "sector": "AI/플랫폼",
        })
        previous_portfolio = portfolio_summary([previous_position], fx_rates={"KRW": 1, "USD": 1400})
        current_portfolio = portfolio_summary([current_position], fx_rates={"KRW": 1, "USD": 1400})
        previous_snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            previous_portfolio,
            [previous_position],
            decisions_for_positions([previous_position], previous_portfolio),
        )
        current_snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            current_portfolio,
            [current_position],
            decisions_for_positions([current_position], current_portfolio),
        )

        events = RealtimeMonitor({
            "fxRates": "KRW=1\nUSD=1400",
            "alertThresholds": "monitorValueDelta=5",
        }).events_for_snapshot(current_snapshot, previous_snapshot.to_monitor_state())
        insight = self.insight_event(events, "AAPL")
        message = self.insight_source_message(insight, "monitorValueChange")

        self.assertIn("monitorValueChange", self.insight_source_rules(insight))
        self.assertFalse(any(event.rule == "monitorValueChange" for event in events))
        self.assertIn("이전 $1,000 (약 140만 원)", message)
        self.assertIn("현재 $1,100 (약 154만 원)", message)
        self.assertIn("변화 +10.0% (KRW 환산 기준)", message)
        self.assertNotIn("이전 1,000원", message)

    def test_monitor_cadence_is_account_and_type_scoped(self):
        account = AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"])
        position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "marketValue": 1000,
            "profitLossRate": 12,
            "sellableQuantity": 1,
            "sector": "AI/플랫폼",
        })
        portfolio = portfolio_summary([position])
        snapshot = AccountSnapshot(
            account_id="main",
            account_label="메인",
            provider="toss",
            mode="live",
            status="토스 계좌 동기화",
            generated_at=utc_now_iso(),
            portfolio=portfolio,
            positions=[position],
            decisions=decisions_for_positions([position], portfolio),
        )
        store = MonitorStore()
        monitor = RealtimeMonitor()

        first_events = monitor.apply_cadence(monitor.events_for_snapshot(snapshot, {}), store)
        store.mark_sent(first_events)
        store.save_snapshot(snapshot)
        store.write()
        second_events = monitor.apply_cadence(monitor.events_for_snapshot(snapshot, snapshot.to_monitor_state()), store)

        self.assertTrue(any(event.rule == "monitorHeartbeat" for event in first_events))
        self.assertFalse(any(event.rule == "monitorHeartbeat" for event in second_events))

    def test_default_message_type_cadence_is_ten_minutes(self):
        self.assertTrue(DEFAULT_CADENCE)
        self.assertTrue(all(value >= 10 for value in DEFAULT_CADENCE.values()))
        self.assertEqual(10, DEFAULT_CADENCE["monitorHeartbeat"])
        self.assertEqual(60, DEFAULT_CADENCE["externalMacroShift"])

    def test_default_watchlist_and_model_alerts_include_requested_symbols(self):
        symbols = runtime_settings()["watchlistSymbols"].split(",")

        self.assertIn("TSLA", symbols)
        self.assertIn("AAPL", symbols)
        self.assertEqual(1, DEFAULT_ALERT_RULES["investmentInsight"])
        self.assertEqual(1, DEFAULT_ALERT_RULES["modelBuy"])
        self.assertEqual(1, DEFAULT_ALERT_RULES["modelSell"])
        self.assertEqual(1, DEFAULT_ALERT_RULES["watchlistBuyCandidate"])
        self.assertEqual(1, DEFAULT_ALERT_RULES["watchlistOntologySignal"])
        self.assertEqual(10, DEFAULT_CADENCE["investmentInsight"])
        self.assertEqual(10, DEFAULT_CADENCE["modelBuy"])
        self.assertEqual(10, DEFAULT_CADENCE["modelSell"])
        self.assertEqual(10, DEFAULT_CADENCE["watchlistBuyCandidate"])
        self.assertEqual(10, DEFAULT_CADENCE["watchlistOntologySignal"])

    def test_investment_insight_rule_uses_ontology_novelty_for_cooldown_bypass(self):
        rule = default_notification_rule("investmentInsight")
        condition_ids = {condition.condition_id for condition in rule.conditions}
        bypass_ids = {condition.condition_id for condition in rule.similarity_bypass_conditions}
        self.assertIn("ontology_novelty_score", condition_ids)
        self.assertIn("insight_type_changed", bypass_ids)
        self.assertIn("new_relation_event", bypass_ids)
        self.assertEqual(["messageType", "accountId", "ontologyInsight.subject", "ontologyInsight.insightType"], rule.similarity_fields)
        job = NotificationJob.create(
            "관계 인사이트",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "ALERT",
                "ontologyInsight": {
                    "subject": "AAPL",
                    "insightType": "contradictionDetected",
                    "score": 72,
                    "noveltyScore": 82,
                    "confidence": 74,
                },
                "sourceSignalTypes": ["modelSell", "externalDartDisclosure"],
            },
        )
        previous_context = {
            "severity": "WATCH",
            "ontologyInsight": {
                "subject": "AAPL",
                "insightType": "riskIncrease",
                "score": 60,
                "noveltyScore": 64,
                "confidence": 70,
            },
            "sourceSignalTypes": ["modelSell"],
        }
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_score=decision.score,
            previous_context=previous_context,
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=5,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("material_change", decision.state_decision)
        self.assertTrue(decision.similarity_bypassed)

    def test_symbol_universe_parsers_and_store_support_market_catalog(self):
        nasdaq_text = "\n".join([
            "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares",
            "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N",
            "TEST|Test Issue|G|Y|N|100|N|N",
            "File Creation Time: 0701202600|||||||",
        ])
        krx_html = """
        <table><tr><th>회사명</th><th>종목코드</th><th>업종</th></tr>
        <tr><td>삼성전자</td><td>5930</td><td>전기전자</td></tr></table>
        """
        nasdaq = parse_nasdaq_listed(nasdaq_text, fetched_at="2026-07-01T00:00:00Z")
        krx = parse_krx_kind_table(krx_html, "KOSPI", fetched_at="2026-07-01T00:00:00Z")
        store = SQLiteSymbolUniverseStore(Path(self.temp.name) / "service.db")

        self.assertEqual(["AAPL"], [item.symbol for item in nasdaq])
        self.assertEqual("005930", krx[0].symbol)
        self.assertEqual(2, store.upsert_many(nasdaq + krx))
        self.assertEqual({"KOSPI": 1, "NASDAQ": 1}, store.counts_by_market())
        self.assertEqual("Apple Inc. - Common Stock", store.get("AAPL").name)
        self.assertEqual("삼성전자", store.search(query="삼성", market="KOSPI")[0].name)
        self.assertEqual(2, store.search_count())
        self.assertEqual(["AAPL"], [item.symbol for item in store.search(limit=1, offset=1)])

    def test_symbol_universe_refresh_market_records_catalog_and_source_together(self):
        db_path = Path(self.temp.name) / "service.db"
        store = SQLiteSymbolUniverseStore(db_path)
        item = seed_symbol("AAPL")
        item.source = "NASDAQ"
        item.source_url = "https://example.test/nasdaq"

        count = store.refresh_market("NASDAQ", "NASDAQ", "https://example.test/nasdaq", [item])

        self.assertEqual(1, count)
        self.assertEqual({"NASDAQ": 1}, store.counts_by_market())
        sources = store.source_states()
        self.assertEqual("NASDAQ", sources[0]["market"])
        self.assertEqual("ok", sources[0]["status"])
        self.assertEqual(1, sources[0]["recordCount"])
        self.assertTrue(sources[0]["lastSuccessAt"])

    def test_symbol_universe_service_seeds_and_reports_freshness(self):
        service = SymbolUniverseService(
            SQLiteSymbolUniverseStore(Path(self.temp.name) / "service.db"),
            RemoteSymbolSourceGateway(),
            runtime_settings(),
        )

        payload = service.search(query="TSLA")

        self.assertTrue(any(item["symbol"] == "TSLA" for item in payload["items"]))
        self.assertTrue(payload["summary"]["total"] >= 1)
        self.assertTrue(any(item["market"] == "NASDAQ" for item in payload["summary"]["markets"]))
        self.assertEqual(0, payload["offset"])
        self.assertIn("hasMore", payload)

    def test_symbol_universe_search_includes_collected_market_data(self):
        db_path = Path(self.temp.name) / "service.db"
        symbol_store = SQLiteSymbolUniverseStore(db_path)
        quote_cache = SQLiteMarketQuoteCache(db_path)
        symbol_store.upsert_many([seed_symbol("AAPL")])
        quote_cache.save("toss", MARKET_DATA_ACCOUNT_ID, "AAPL", {
            "symbol": "AAPL",
            "market": "NASDAQ",
            "currency": "USD",
            "currentPrice": 185.7,
            "quoteSource": "Toss /api/v1/prices",
            "dataQuality": "actual",
            "updatedAt": utc_now_iso(),
            "ma20": 180,
        })
        service = SymbolUniverseService(symbol_store, RemoteSymbolSourceGateway(), runtime_settings(), quote_cache=quote_cache)

        payload = service.search(query="AAPL")
        item = payload["items"][0]

        self.assertEqual(185.7, item["currentPrice"])
        self.assertEqual("Toss /api/v1/prices", item["quoteSource"])
        self.assertEqual("actual", item["dataQuality"])
        self.assertEqual(180, item["ma20"])
        self.assertEqual(1, payload["summary"]["marketData"]["count"])

    def test_market_data_collection_runner_collects_recommendation_universe(self):
        db_path = Path(self.temp.name) / "service.db"
        symbol_store = SQLiteSymbolUniverseStore(db_path)
        quote_cache = SQLiteMarketQuoteCache(db_path)
        symbol_store.upsert_many([seed_symbol("AAPL"), seed_symbol("TSLA")])
        registry = AccountRegistry()
        account = AccountConfig("main", "메인", "toss", "https://example.test", "id", "secret", "1", [])
        registry.upsert(account)

        class StaticSymbolService:
            def summary(self):
                return {
                    "markets": [{"market": "NASDAQ", "count": 2, "stale": False}],
                    "sources": [],
                    "total": 2,
                }

            def refresh(self, markets=None):
                return {"results": [], "summary": self.summary()}

        class FakeProvider:
            def __init__(self, account, cache):
                self.delegate = TossProvider(account, quote_cache=cache)

            def fetch_access_token(self):
                return "token"

            def fetch_prices(self, token, symbols):
                return {
                    symbol: {
                        "symbol": symbol,
                        "currentPrice": 100 + index,
                        "currency": "USD",
                        "quoteSource": "Toss /api/v1/prices",
                        "quoteStatus": "토스 prices 반영",
                        "dataQuality": "actual",
                        "updatedAt": "2026-07-04T00:00:00Z",
                    }
                    for index, symbol in enumerate(symbols)
                }, token

            def fetch_daily_candles(self, token, symbol):
                return [
                    {
                        "timestamp": "2026-01-" + str(index + 1).zfill(2) + "T09:00:00+09:00",
                        "closePrice": str(80 + index),
                        "volume": str(1000 + index),
                    }
                    for index in range(28)
                ], token

            def merge_market_data(self, *args, **kwargs):
                return self.delegate.merge_market_data(*args, **kwargs)

        events = EventBus()
        runner = MarketDataCollectionRunner(
            registry,
            StaticSymbolService(),
            quote_cache,
            {
                "marketDataCollectionMarkets": "NASDAQ",
                "marketDataPriceBatchSize": "2",
                "marketDataCandleBatchSize": "1",
                "marketDataMaxAgeMinutes": "240",
                "marketDataRefreshUniverse": "0",
            },
            provider_factory=lambda account, cache: FakeProvider(account, cache),
            event_publisher=events,
            sleep_fn=lambda _seconds: None,
        )

        result = runner.run_once()
        cached_aapl = quote_cache.load("toss", MARKET_DATA_ACCOUNT_ID, "AAPL")
        cached_tsla = quote_cache.load("toss", MARKET_DATA_ACCOUNT_ID, "TSLA")

        self.assertEqual("ok", result["status"])
        self.assertEqual(2, result["savedCount"])
        self.assertEqual(1, result["candleCount"])
        self.assertEqual("recommendation-universe", cached_aapl["collectionPurpose"])
        self.assertEqual(100, cached_aapl["currentPrice"])
        self.assertGreater(cached_aapl["ma20"], 0)
        self.assertEqual(101, cached_tsla["currentPrice"])
        self.assertEqual([MARKET_DATA_COLLECTED], [event.name for event in events.published])

    def test_market_quote_cache_selects_stale_symbols_before_fresh_symbols(self):
        db_path = Path(self.temp.name) / "service.db"
        symbol_store = SQLiteSymbolUniverseStore(db_path)
        quote_cache = SQLiteMarketQuoteCache(db_path)
        symbol_store.upsert_many([seed_symbol("AAPL"), seed_symbol("TSLA")])
        quote_cache.save("toss", MARKET_DATA_ACCOUNT_ID, "AAPL", {
            "symbol": "AAPL",
            "market": "NASDAQ",
            "currentPrice": 100,
            "updatedAt": "2026-07-04T00:00:00Z",
        })

        selected = quote_cache.stale_universe_symbols("toss", MARKET_DATA_ACCOUNT_ID, ["NASDAQ"], limit=2, max_age_minutes=240)

        self.assertEqual("TSLA", selected[0]["symbol"])

    def test_symbol_universe_search_marks_stale_market_data_without_attaching_price(self):
        db_path = Path(self.temp.name) / "service.db"
        symbol_store = SQLiteSymbolUniverseStore(db_path)
        quote_cache = SQLiteMarketQuoteCache(db_path)
        symbol_store.upsert_many([seed_symbol("AAPL")])
        quote_cache.save("toss", MARKET_DATA_ACCOUNT_ID, "AAPL", {
            "symbol": "AAPL",
            "market": "NASDAQ",
            "currentPrice": 100,
            "dataQuality": "actual",
            "updatedAt": "2026-07-04T00:00:00Z",
        })
        service = SymbolUniverseService(
            symbol_store,
            RemoteSymbolSourceGateway(),
            {"marketDataMaxAgeMinutes": "240"},
            quote_cache=quote_cache,
        )

        result = service.search(query="AAPL", market="NASDAQ")
        item = result["items"][0]

        self.assertTrue(item["marketDataStale"])
        self.assertEqual("2026-07-04T00:00:00Z", item["marketDataUpdatedAt"])
        self.assertNotIn("currentPrice", item)

    def test_application_layer_does_not_import_infrastructure(self):
        application_dir = Path(__file__).resolve().parents[1] / "digital_twin" / "application"
        offenders = []
        for path in application_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "infrastructure" in text:
                offenders.append(path.name)

        self.assertEqual([], offenders)

    def test_domain_layer_does_not_import_application_or_infrastructure(self):
        domain_dir = Path(__file__).resolve().parents[1] / "digital_twin" / "domain"
        offenders = []
        for path in domain_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "application" in text or "infrastructure" in text:
                offenders.append(path.name)

        self.assertEqual([], offenders)

    def test_message_catalog_is_shared_across_monitoring_and_notifications(self):
        catalog = public_message_catalog()

        self.assertEqual("투자 인사이트", MESSAGE_TYPE_LABELS["investmentInsight"])
        self.assertEqual("모델 매수", MESSAGE_TYPE_LABELS["modelBuy"])
        self.assertEqual("관심종목 매수 후보", MESSAGE_TYPE_LABELS["watchlistBuyCandidate"])
        self.assertEqual("관심종목 관계 신호", MESSAGE_TYPE_LABELS["watchlistOntologySignal"])
        self.assertEqual(10, catalog["investmentInsight"]["cadenceMinutes"])
        self.assertEqual(10, catalog["modelBuy"]["cadenceMinutes"])
        self.assertEqual(10, catalog["watchlistBuyCandidate"]["cadenceMinutes"])
        self.assertEqual(10, catalog["watchlistOntologySignal"]["cadenceMinutes"])
        self.assertEqual("🧭", catalog["investmentInsight"]["icon"])
        self.assertEqual("🟢", catalog["modelBuy"]["icon"])
        self.assertEqual("🧠", MESSAGE_TYPE_EMOJIS["modelReview"])
        self.assertTrue(all(item.get("icon") for item in catalog.values()))
        self.assertTrue(catalog["investmentInsight"]["monitoring"])
        self.assertTrue(catalog["modelBuy"]["monitoring"])
        self.assertTrue(catalog["watchlistBuyCandidate"]["monitoring"])
        self.assertTrue(catalog["watchlistOntologySignal"]["monitoring"])
        self.assertTrue(catalog["workHandoff"]["system"])

    def test_flow_lens_mock_contract_is_python_native(self):
        payload = flow_lens_snapshot(mock=True, watchlist_symbols="TSLA,AAPL,NVDA")

        self.assertEqual("mock", payload["dataMode"])
        self.assertIn("tossDecision", payload)
        self.assertTrue(any(item["symbol"] == "AAPL" for item in payload["tossDecision"]["items"]))
        self.assertTrue(any(item["symbol"] == "TSLA" for item in payload["tossDecision"]["items"]))
        self.assertIn("investmentAnalysis", payload["tossDecision"])
        self.assertTrue(payload["tossDecision"]["investmentAnalysis"]["reasoningCards"])
        self.assertEqual("investment-ontology-ai-inference-v1", payload["tossDecision"]["investmentAnalysis"]["aiInferencePacket"]["contract"])
        self.assertTrue(any(item.get("reasoningCard") for item in payload["tossDecision"]["items"]))
        ontology_strategy = payload["tossDecision"]["ontologyStrategy"]
        abox_kinds = {item.get("kind") for item in ontology_strategy["aboxEntities"]}
        abox_relation_types = {item.get("type") for item in ontology_strategy["aboxRelations"]}
        self.assertTrue(ontology_strategy["tboxEntities"])
        self.assertTrue(ontology_strategy["tboxRelations"])
        self.assertTrue(ontology_strategy["aboxEntities"])
        self.assertTrue(ontology_strategy["aboxRelations"])
        self.assertTrue(ontology_strategy["activeInvestmentOpinions"])
        self.assertTrue(ontology_strategy["executionPlans"])
        self.assertTrue(ontology_strategy["insights"])
        self.assertTrue(ontology_strategy["dataQuality"])
        self.assertEqual("insight-driven-only", ontology_strategy["operationalOntology"]["dispatchMode"])
        self.assertIn("stock", abox_kinds)
        self.assertIn("execution-plan", abox_kinds)
        self.assertIn("insight", abox_kinds)
        self.assertIn("HAS_EXECUTION_PLAN", abox_relation_types)
        self.assertIn("PRODUCES_INSIGHT", abox_relation_types)
        self.assertFalse("news" in payload)

    def test_mock_market_contract_is_python_native(self):
        payload = mock_market_payload({"scenario": "semiconductor-boom", "symbols": "NVDA,005930", "seed": "unit"})

        self.assertEqual("semiconductor-boom", payload["scenario"]["id"])
        self.assertEqual(["NVDA", "005930"], payload["request"]["symbols"])
        self.assertGreaterEqual(len(payload["series"]["NVDA"]["candles"]), 200)
        self.assertEqual(2, len(payload["signals"]))

    def test_monitor_type_check_events_use_real_alert_rules(self):
        position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "marketValue": 1000,
            "quantity": 2,
            "sellableQuantity": 2,
            "averagePrice": 100,
            "currentPrice": 125,
            "profitLossRate": 25,
            "sector": "AI/플랫폼",
        })
        portfolio = portfolio_summary([position], 300, "KRW")
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "토스 계좌 동기화",
            utc_now_iso(),
            portfolio,
            [position],
            decisions_for_positions([position], portfolio),
        )

        events = RealtimeMonitor().type_check_events_for_snapshot(snapshot)

        self.assertEqual(
            {
                "investmentInsight",
                "modelBuy",
                "modelSell",
                "watchlistBuyCandidate",
                "watchlistOntologySignal",
                "watchlistQuote",
                "watchlistQuotePending",
                "holdingTiming",
                "monitorHeartbeat",
                "monitorConnection",
                "monitorPositionChange",
                "monitorPnlChange",
                "monitorValueChange",
                "monitorTrendChange",
                "monitorCashChange",
                "monitorDecisionChange",
                "externalEquityMove",
                "externalCryptoMove",
                "externalMacroShift",
                "externalDartDisclosure",
                "externalDataConnection",
            },
            {event.rule for event in events},
        )
        self.assertTrue(all(not event.message().startswith("메인 ") for event in events))

    def test_external_signal_alerts_cover_configured_data_apis(self):
        position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "marketValue": 1000,
            "quantity": 2,
            "averagePrice": 100,
            "currentPrice": 125,
            "profitLossRate": 25,
            "sector": "AI/플랫폼",
        })
        kr_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000000,
            "quantity": 10,
            "averagePrice": 95000,
            "currentPrice": 100000,
            "profitLossRate": 5.3,
            "sector": "반도체",
        })
        portfolio = portfolio_summary([position, kr_position], 300, "KRW")
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "토스 계좌 동기화",
            utc_now_iso(),
            portfolio,
            [position, kr_position],
            decisions_for_positions([position, kr_position], portfolio),
            external_signals={
                "equityQuotes": {
                    "AAPL": {
                        "provider": "Alpha Vantage",
                        "price": 130,
                        "changePercent": 4.5,
                        "volume": 58000000,
                        "latestTradingDay": "2026-07-01",
                    }
                },
                "cryptoMarkets": {
                    "bitcoin": {
                        "provider": "CoinGecko",
                        "symbol": "BTC",
                        "price": 108000,
                        "volume24h": 42000000000,
                        "change24h": -5.2,
                        "change7d": -12.1,
                    }
                },
                "macro": {
                    "series": {
                        "DGS10": {"provider": "FRED", "date": "2026-07-01", "value": 4.35},
                        "DGS2": {"provider": "FRED", "date": "2026-07-01", "value": 3.95},
                    },
                    "yieldSpread10y2y": 0.4,
                },
                "dartDisclosures": {
                    "005930": {
                        "provider": "OpenDART",
                        "corpName": "삼성전자",
                        "reportName": "주요사항보고서",
                        "receiptNo": "20260701000001",
                        "receiptDate": "20260701",
                        "count": 2,
                    }
                },
                "statuses": [{"source": "FRED", "ok": False, "message": "rate limit"}],
            },
        )
        previous = json.loads(json.dumps(snapshot.to_monitor_state()))
        previous["externalSignals"]["macro"]["series"]["DGS10"]["value"] = 4.0
        previous["externalSignals"]["macro"]["yieldSpread10y2y"] = 0.0
        previous["externalSignals"]["dartDisclosures"]["005930"]["receiptNo"] = "20260630000001"

        events = RealtimeMonitor().external_signal_events(snapshot, previous)
        messages = {event.rule: event.message() for event in events}

        self.assertIn("externalEquityMove", messages)
        self.assertIn("Alpha Vantage", messages["externalEquityMove"])
        self.assertIn("현재가: $130", messages["externalEquityMove"])
        self.assertIn("평균매입가: $100", messages["externalEquityMove"])
        self.assertIn("수익률: +25.0%", messages["externalEquityMove"])
        self.assertIn("externalCryptoMove", messages)
        self.assertIn("CoinGecko", messages["externalCryptoMove"])
        self.assertIn("비트코인 변동", messages["externalCryptoMove"])
        self.assertIn("크립토 거래액", messages["externalCryptoMove"])
        self.assertIn("externalMacroShift", messages)
        self.assertIn("DGS10", messages["externalMacroShift"])
        self.assertIn("externalDartDisclosure", messages)
        self.assertIn("주요사항보고서", messages["externalDartDisclosure"])
        self.assertIn("현재가: 100,000원", messages["externalDartDisclosure"])
        self.assertIn("평균매입가: 95,000원", messages["externalDartDisclosure"])
        self.assertIn("수익률: +5.3%", messages["externalDartDisclosure"])
        self.assertIn("externalDataConnection", messages)
        criteria_by_rule = {event.rule: event.criteria for event in events}
        self.assertTrue(any("±3% 이상" in item for item in criteria_by_rule["externalEquityMove"]))
        self.assertTrue(any("관계 규칙 강도" in item for item in criteria_by_rule["externalCryptoMove"]))
        self.assertTrue(any("7일 -12.1%" in item for item in criteria_by_rule["externalCryptoMove"]))
        self.assertTrue(any("±15bp 이상" in item for item in criteria_by_rule["externalMacroShift"]))

    def test_bitcoin_crypto_alert_uses_lower_bitcoin_thresholds(self):
        portfolio = portfolio_summary([])
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio,
            [],
            [],
            external_signals={
                "cryptoMarkets": {
                    "bitcoin": {
                        "provider": "CoinGecko",
                        "symbol": "BTC",
                        "name": "Bitcoin",
                        "price": 63251,
                        "volume24h": 20330791035,
                        "change24h": 1.8,
                        "change7d": 5.3,
                    },
                    "ethereum": {
                        "provider": "CoinGecko",
                        "symbol": "ETH",
                        "name": "Ethereum",
                        "price": 1791,
                        "volume24h": 7939639331,
                        "change24h": 1.8,
                        "change7d": 5.3,
                    },
                }
            },
        )

        events = RealtimeMonitor().external_signal_events(snapshot, {})

        crypto_events = [event for event in events if event.rule == "externalCryptoMove"]
        self.assertEqual(["BTC"], [event.symbol for event in crypto_events])
        self.assertIn("비트코인 변동", crypto_events[0].message())
        self.assertTrue(any("비트코인 24h ±3% 또는 7d ±4% 이상" in item for item in crypto_events[0].criteria))

    def test_external_crypto_positive_weekly_move_with_minor_day_drop_is_watch(self):
        portfolio = portfolio_summary([])
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio,
            [],
            [],
            external_signals={
                "cryptoMarkets": {
                    "ethereum": {
                        "provider": "CoinGecko",
                        "symbol": "ETH",
                        "name": "Ethereum",
                        "price": 1765,
                        "volume24h": 9319846169,
                        "change24h": -0.1,
                        "change7d": 11.8,
                    },
                }
            },
        )

        events = RealtimeMonitor().external_signal_events(snapshot, {})

        self.assertEqual(1, len(events))
        event = events[0]
        self.assertEqual("externalCryptoMove", event.rule)
        self.assertEqual("WATCH", event.severity)
        self.assertTrue(any("관계 규칙 강도 70.8점" in item for item in event.criteria))
        self.assertTrue(any("7일 +11.8%" in item for item in event.criteria))
        model = event.metadata.get("cryptoMoveModel")
        self.assertEqual("크립토 가격 급등", model.get("titleLabel"))
        self.assertEqual("상승", model.get("directionLabel"))
        self.assertEqual("7d", model.get("dominantPeriod"))
        self.assertEqual(70.8, model.get("score"))
        self.assertEqual("크립토 가격 급등", event.metadata.get("cryptoMoveTitle"))
        self.assertEqual(70.8, event.metadata.get("cryptoMoveScore"))
        self.assertEqual("external.crypto.market_move.v1", event.metadata.get("ontologyRelationContext", {}).get("activeRules", [{}])[0].get("ruleId"))
        active_opinion = event.metadata.get("activeInvestmentOpinion")
        self.assertEqual("HOLD", active_opinion.get("action"))
        self.assertEqual("HOLD", active_opinion.get("executionPlan", {}).get("primaryAction"))
        self.assertTrue(any("변동만 보고 주식 신규 매수·매도" in item for item in active_opinion.get("executionPlan", {}).get("blockedActions", [])))
        self.assertEqual("HOLD", event.metadata.get("ontologyRelationContext", {}).get("activeInvestmentOpinion", {}).get("action"))
        self.assertEqual("HOLD", event.metadata.get("ontologyRelationContext", {}).get("executionPlan", {}).get("primaryAction"))
        self.assertEqual("cryptoMoveScoreFormula", event.metadata.get("legacyFormulaAudits")[0].get("key"))

        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        message = templates.render(event.rule, alert_context(event))
        self.assertIn("<b>[관찰] 🪙 이더리움: 크립토 가격 급등</b>", message)
        self.assertNotIn("크립토 가격 급락", message)
        self.assertIn("관계 규칙", message)
        self.assertIn("크립토 급변", message)
        self.assertIn("관계 판단", message)
        self.assertIn("대표 변화", message)
        self.assertIn("7일 +11.8%", message)
        self.assertIn("AI 프롬프트", message)
        self.assertNotIn("크립토 변동 공식(cryptoMoveScoreFormula)", message)

    def test_crypto_investment_insight_uses_active_opinion_beginner_summary(self):
        portfolio = portfolio_summary([])
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio,
            [],
            [],
            external_signals={
                "cryptoMarkets": {
                    "ethereum": {
                        "provider": "CoinGecko",
                        "symbol": "ETH",
                        "name": "Ethereum",
                        "price": 1765,
                        "volume24h": 9319846169,
                        "change24h": -0.8,
                        "change7d": 11.3,
                    },
                }
            },
        )

        events = RealtimeMonitor().events_for_snapshot(snapshot, {})
        insight = self.insight_event(events, "ETH")
        message = SQLiteNotificationTemplateStore(Path(self.temp.name) / "service.db").render(insight.rule, alert_context(insight))
        active = insight.metadata.get("activeInvestmentOpinion", {})

        self.assertEqual("HOLD", active.get("action"))
        self.assertIn("보유 영향만 점검", active.get("executionPlan", {}).get("primaryActionLabel"))
        self.assertIn("외부 신호: 보유 영향 점검", message)
        self.assertIn("쉽게 말하면", message)
        self.assertIn("보유 영향만 점검", message)
        self.assertIn("지금 피할 일", message)
        self.assertIn("직접 민감 보유 종목이 없어 단독 매매 근거는 약함", message)
        self.assertNotIn("실행보다 관찰 우선", message)
        self.assertNotIn("크립토 변동가", message)

    def test_external_signal_provider_normalizes_api_responses_and_caches(self):
        calls = []

        def fake_fetch(url, headers=None):
            calls.append(url)
            if "alphavantage" in url and "NEWS_SENTIMENT" in url:
                return {"feed": [{
                    "title": "Apple unveils AI chips roadmap",
                    "url": "https://example.test/apple-ai",
                    "source": "Example Markets",
                    "time_published": "20260707T120000",
                    "summary": "Apple supplier plans and AI chip roadmap update.",
                    "overall_sentiment_score": "0.35",
                    "overall_sentiment_label": "Somewhat-Bullish",
                    "ticker_sentiment": [{
                        "ticker": "AAPL",
                        "relevance_score": "0.92",
                        "ticker_sentiment_score": "0.41",
                        "ticker_sentiment_label": "Bullish",
                    }],
                }]}
            if "alphavantage" in url:
                return {"Global Quote": {
                    "05. price": "130.25",
                    "06. volume": "58000000",
                    "07. latest trading day": "2026-07-01",
                    "09. change": "5.25",
                    "10. change percent": "4.2%",
                }}
            if "coingecko" in url:
                return [{
                    "id": "bitcoin",
                    "symbol": "btc",
                    "name": "Bitcoin",
                    "current_price": 108000,
                    "market_cap": 2100000000000,
                    "total_volume": 42000000000,
                    "price_change_percentage_24h_in_currency": -5.4,
                    "price_change_percentage_7d_in_currency": -11.2,
                }]
            if "fred" in url:
                return {"observations": [{"date": "2026-07-01", "value": "4.35"}]}
            if "data.sec.gov/submissions" in url:
                return {"name": "Apple Inc.", "filings": {"recent": {
                    "form": ["4", "10-Q"],
                    "filingDate": ["2026-06-17", "2026-05-01"],
                    "reportDate": ["2026-06-16", "2026-03-28"],
                    "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002"],
                    "primaryDocument": ["xslF345X05/wk-form4_1.xml", "aapl-20260328.htm"],
                }}}
            if "data.sec.gov/api/xbrl/companyfacts" in url:
                return {"entityName": "Apple Inc.", "facts": {"us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                        {"val": 95359000000, "end": "2026-03-28", "filed": "2026-05-01", "fy": 2026, "fp": "Q2", "form": "10-Q"},
                    ]}},
                    "NetIncomeLoss": {"units": {"USD": [
                        {"val": 24780000000, "end": "2026-03-28", "filed": "2026-05-01", "fy": 2026, "fp": "Q2", "form": "10-Q"},
                    ]}},
                }}}
            if "opendart" in url:
                return {"status": "000", "list": [{
                    "corp_name": "삼성전자",
                    "report_nm": "주요사항보고서",
                    "rcept_no": "20260701000001",
                    "rcept_dt": "20260701",
                }]}
            if "api.gdeltproject.org" in url:
                return {"articles": [{
                    "title": "Samsung Electronics shares move as investors watch memory demand",
                    "url": "https://example.test/samsung-memory",
                    "domain": "example.test",
                    "seendate": "20260707120000",
                    "language": "English",
                }]}
            return {}

        settings = {
            "alphaVantageApiKey": "alpha-key",
            "coingeckoApiKey": "cg-key",
            "fredApiKey": "fred-key",
            "opendartApiKey": "dart-key",
            "externalApiFetchIntervalMinutes": "60",
            "externalFredSeries": "DGS10",
            "externalCryptoIds": "bitcoin",
            "externalAlphaMaxSymbols": "2",
            "externalSecMaxSymbols": "2",
            "externalNewsMaxSymbols": "2",
            "externalSecCompanyCiks": "AAPL=0000320193",
            "externalDartLookbackDays": "14",
            "externalDartCorpCodes": "005930=00126380",
            "fxRates": "KRW=1\nUSD=1400",
        }
        provider = ExternalSignalProvider(
            settings=settings,
            cache=SQLiteExternalSignalCache(Path(self.temp.name) / "service.db"),
            fetch_json=fake_fetch,
        )
        positions = [
            normalize_position({"symbol": "AAPL", "name": "Apple", "market": "US", "currency": "USD"}),
            normalize_position({"symbol": "005930", "name": "삼성전자", "market": "KR", "currency": "KRW"}),
            normalize_position({"symbol": "123456", "name": "미매핑", "market": "KR", "currency": "KRW"}),
        ]

        signals = provider.signals_for_positions(positions)
        cached_signals = provider.signals_for_positions(positions)

        self.assertEqual(8, len(calls))
        self.assertEqual(signals["fetchedAt"], cached_signals["fetchedAt"])
        self.assertEqual(signals["equityQuotes"], cached_signals["equityQuotes"])
        self.assertEqual(signals["cryptoMarkets"], cached_signals["cryptoMarkets"])
        self.assertEqual(4.2, signals["equityQuotes"]["AAPL"]["changePercent"])
        self.assertEqual("10-Q", signals["secFilings"]["AAPL"]["latestFiling"]["form"])
        self.assertEqual(95359000000, signals["secFilings"]["AAPL"]["facts"]["revenue"]["value"])
        self.assertEqual(-5.4, signals["cryptoMarkets"]["bitcoin"]["change24h"])
        self.assertEqual(4.35, signals["macro"]["series"]["DGS10"]["value"])
        self.assertEqual("20260701000001", signals["dartDisclosures"]["005930"]["receiptNo"])
        self.assertEqual("Alpha Vantage", signals["newsHeadlines"]["AAPL"]["provider"])
        self.assertEqual("Bullish", signals["newsHeadlines"]["AAPL"]["items"][0]["tickerSentimentLabel"])
        self.assertEqual("GDELT", signals["newsHeadlines"]["005930"]["provider"])
        self.assertIn("Samsung Electronics", signals["newsHeadlines"]["005930"]["items"][0]["title"])
        self.assertEqual(1400, signals["fxRates"]["USDKRW"]["rate"])
        self.assertEqual("RuntimeSettings", signals["fxRates"]["USDKRW"]["provider"])

    def test_external_signal_cache_recomputes_freshness_age_on_read(self):
        db_path = Path(self.temp.name) / "service.db"
        cache = SQLiteExternalSignalCache(db_path)
        settings = {
            "externalApiFetchIntervalMinutes": "60",
            "externalSignalCacheMaxAgeMinutes": "60",
            "externalAlphaEnabled": "0",
            "externalCoinGeckoEnabled": "1",
            "externalFredEnabled": "0",
            "externalDartEnabled": "0",
            "externalSecEnabled": "0",
            "externalNewsEnabled": "0",
        }
        fetched_at = datetime.now(timezone.utc) - timedelta(minutes=7)
        signals = attach_external_signal_quality({
            "fetchedAt": fetched_at.isoformat().replace("+00:00", "Z"),
            "equityQuotes": {},
            "cryptoMarkets": {
                "bitcoin": {
                    "provider": "CoinGecko",
                    "symbol": "BTC",
                    "price": 100,
                    "change24h": 1,
                    "change7d": 2,
                }
            },
            "macro": {},
            "fxRates": {},
            "secFilings": {},
            "dartDisclosures": {},
            "newsHeadlines": {},
            "statuses": [],
        }, settings=settings, now=fetched_at)
        provider = ExternalSignalProvider(
            settings=settings,
            cache=cache,
            fetch_json=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache hit should not fetch")),
        )
        cache_key = provider.cache_key_for_positions([])
        cache.replace(provider.next_cache_payload({}, cache_key, signals))

        cached = provider.signals_for_positions([])

        self.assertGreaterEqual(cached["freshness"]["ageMinutes"], 6)
        self.assertEqual(cached["freshness"]["ageMinutes"], cached["quality"]["ageMinutes"])
        self.assertEqual("fresh", cached["freshness"]["status"])

    def test_news_research_evidence_uses_stable_article_identity(self):
        item = {
            "title": "Samsung Electronics shares move as investors watch memory demand",
            "url": "https://example.test/samsung-memory",
            "domain": "example.test",
            "seenDate": "20260707120000",
        }

        first = research_evidence_from_facts("005930", {"newsHeadlines": {"items": [item]}})
        second = research_evidence_from_facts("005930", {"newsHeadlines": {"items": [{"title": "삼성전자 공급망 점검", "url": "https://example.test/other"}, item]}})

        self.assertEqual(first[0].evidence_id, second[1].evidence_id)
        self.assertNotIn(":news:0", first[0].evidence_id)

    def test_news_source_gateway_scores_and_filters_unrelated_news(self):
        published = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

        def fake_text(_url, headers=None):
            return (
                "<rss><channel>"
                "<item>"
                "<title>삼성전자 반도체 실적 기대</title>"
                "<link>https://news.example.kr/samsung</link>"
                "<pubDate>" + published + "</pubDate>"
                "<description>메모리 수요 회복과 실적 개선 전망</description>"
                "<source>연합뉴스</source>"
                "</item>"
                "<item>"
                "<title>Apple launches a new consumer device</title>"
                "<link>https://news.example.us/apple-device</link>"
                "<pubDate>" + published + "</pubDate>"
                "<description>Consumer gadget release</description>"
                "<source>Global Tech</source>"
                "</item>"
                "</channel></rss>"
            )

        gateway = NewsSourceGateway({
            "newsCollectionProviders": "google_rss_kr",
            "newsCollectionPerSymbolLimit": "5",
            "newsCollectionLookbackMinutes": "1440",
            "newsCollectionMinRelevanceScore": "35",
        }, fetch_text=fake_text)

        evidence = gateway.fetch_google_news_rss(
            NewsCollectionTarget("005930", "삼성전자", "KOSPI", "KRW", "반도체"),
            locale="KR",
        )

        self.assertEqual(1, len(evidence))
        self.assertEqual("direct", evidence[0].raw_payload["relationScope"])
        self.assertGreaterEqual(evidence[0].raw_payload["relevanceScore"], 80)
        self.assertIn("삼성전자", evidence[0].raw_payload["matchedAliases"])

    def test_news_collection_runner_stores_domestic_and_overseas_news(self):
        path = Path(self.temp.name) / "service.db"
        monitor_store = SQLiteMonitorStore(path)
        monitor_store.previous["main"] = {
            "positions": {
                "005930": {"symbol": "005930", "name": "삼성전자", "market": "KOSPI", "currency": "KRW"},
            },
            "watchlist": {
                "AAPL": {"symbol": "AAPL", "name": "Apple", "market": "NASDAQ", "currency": "USD"},
            },
        }
        account_repository = SimpleNamespace(load=lambda: [AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"])])
        published = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

        def fake_text(url, headers=None):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("q", [""])[0]
            if "005930" in query or "삼성전자" in query:
                title = "삼성전자 반도체 실적 기대"
                link = "https://news.example.kr/samsung"
                source = "국내경제"
            else:
                title = "Apple shares rise after services update"
                link = "https://news.example.us/apple"
                source = "Global Markets"
            return (
                "<rss><channel><item>"
                "<title>" + title + "</title>"
                "<link>" + link + "</link>"
                "<pubDate>" + published + "</pubDate>"
                "<description>시장 영향 점검</description>"
                "<source>" + source + "</source>"
                "</item></channel></rss>"
            )

        runner = NewsCollectionRunner(
            account_repository=account_repository,
            monitor_store=monitor_store,
            symbol_store=SQLiteSymbolUniverseStore(path),
            evidence_store=SQLiteResearchEvidenceStore(path),
            gateway=NewsSourceGateway({
                "newsCollectionProviders": "google_rss_kr,google_rss_us",
                "newsCollectionPerSymbolLimit": "4",
                "newsCollectionLookbackMinutes": "1440",
            }, fetch_text=fake_text),
            settings={
                "newsCollectionMaxSymbols": "10",
                "newsCollectionProviders": "google_rss_kr,google_rss_us",
                "newsCollectionRateLimitSeconds": "0",
            },
            sleep_fn=lambda _: None,
        )

        result = runner.run_once()
        store = SQLiteResearchEvidenceStore(path)
        latest = store.latest(kind="news", limit=10)

        self.assertEqual("ok", result["status"])
        self.assertEqual(2, result["targetCount"])
        self.assertGreaterEqual(result["savedCount"], 2)
        self.assertTrue(any(item.symbol == "005930" and "삼성전자" in item.title for item in latest))
        self.assertTrue(any(item.symbol == "AAPL" and "Apple" in item.title for item in latest))
        self.assertEqual(store.summary()["byKind"][0]["name"], "news")

    def test_external_signal_provider_attaches_stored_news_evidence(self):
        path = Path(self.temp.name) / "service.db"
        store = SQLiteResearchEvidenceStore(path)
        facts = {
            "newsHeadlines": {
                "items": [{
                    "title": "Apple shares rise after services update",
                    "url": "https://news.example.us/apple",
                    "domain": "Global Markets",
                    "seenDate": "20260707120000",
                }]
            }
        }
        store.upsert_many(research_evidence_from_facts("AAPL", facts))
        provider = ExternalSignalProvider(
            settings={
                "externalAlphaEnabled": "0",
                "externalCoinGeckoEnabled": "0",
                "externalFredEnabled": "0",
                "externalDartEnabled": "0",
                "externalSecEnabled": "0",
                "externalNewsEnabled": "0",
                "externalResearchEvidenceMaxItems": "4",
            },
            cache=SQLiteExternalSignalCache(path),
            evidence_store=store,
            fetch_json=lambda _url, headers=None: {},
        )

        signals = provider.signals_for_positions([
            normalize_position({"symbol": "AAPL", "name": "Apple", "market": "NASDAQ", "currency": "USD"}),
        ])

        self.assertIn("researchEvidence", signals)
        self.assertEqual("Apple shares rise after services update", signals["researchEvidence"]["AAPL"][0]["title"])

    def test_external_signal_provider_skips_disabled_sources(self):
        calls = []

        def fake_fetch(url, headers=None):
            calls.append(url)
            return {}

        settings = {
            "alphaVantageApiKey": "alpha-key",
            "coingeckoApiKey": "cg-key",
            "fredApiKey": "fred-key",
            "opendartApiKey": "dart-key",
            "externalAlphaEnabled": "0",
            "externalCoinGeckoEnabled": "0",
            "externalFredEnabled": "0",
            "externalDartEnabled": "0",
            "externalSecEnabled": "0",
            "externalNewsEnabled": "0",
            "externalDartCorpCodes": "005930=00126380",
            "externalSecCompanyCiks": "AAPL=0000320193",
        }
        provider = ExternalSignalProvider(
            settings=settings,
            cache=SQLiteExternalSignalCache(Path(self.temp.name) / "service.db"),
            fetch_json=fake_fetch,
        )
        positions = [
            normalize_position({"symbol": "AAPL", "name": "Apple", "market": "US", "currency": "USD"}),
            normalize_position({"symbol": "005930", "name": "삼성전자", "market": "KR", "currency": "KRW"}),
        ]

        signals = provider.fetch_signals(positions)

        self.assertEqual([], calls)
        self.assertEqual({}, signals["equityQuotes"])
        self.assertEqual({}, signals["cryptoMarkets"])
        self.assertEqual({}, signals["macro"])
        self.assertEqual({}, signals["secFilings"])
        self.assertEqual({}, signals["dartDisclosures"])
        self.assertEqual({}, signals["newsHeadlines"])

    def test_external_signal_provider_rate_limits_repeated_targets(self):
        calls = []

        def fake_fetch(url, headers=None):
            calls.append(url)
            return {"Global Quote": {
                "05. price": "130.25",
                "06. volume": "58000000",
                "07. latest trading day": "2026-07-01",
                "09. change": "5.25",
                "10. change percent": "4.2%",
            }}

        provider = ExternalSignalProvider(
            settings={
                "alphaVantageApiKey": "alpha-key",
                "externalApiRetryAttempts": "1",
                "externalApiRateLimitSeconds": "3600",
                "externalApiCircuitFailures": "2",
                "externalApiCircuitCooldownMinutes": "30",
            },
            cache=SQLiteExternalSignalCache(Path(self.temp.name) / "service.db"),
            fetch_json=fake_fetch,
            sleep=lambda _: None,
        )
        positions = [normalize_position({"symbol": "AAPL", "market": "US", "currency": "USD"})]
        first = {"equityQuotes": {}, "statuses": []}
        second = {"equityQuotes": {}, "statuses": []}

        provider.add_alpha_vantage(first, positions)
        provider.add_alpha_vantage(second, positions)

        self.assertEqual(1, len(calls))
        self.assertEqual(130.25, first["equityQuotes"]["AAPL"]["price"])
        self.assertTrue(any("local rate limit" in item["message"] for item in second["statuses"]))

    def test_external_signal_provider_opens_circuit_after_repeated_failures(self):
        calls = []

        def fake_fetch(url, headers=None):
            calls.append(url)
            raise urllib.error.URLError("temporary outage")

        provider = ExternalSignalProvider(
            settings={
                "alphaVantageApiKey": "alpha-key",
                "externalApiRetryAttempts": "1",
                "externalApiRateLimitSeconds": "0",
                "externalApiCircuitFailures": "2",
                "externalApiCircuitCooldownMinutes": "30",
            },
            cache=SQLiteExternalSignalCache(Path(self.temp.name) / "service.db"),
            fetch_json=fake_fetch,
            sleep=lambda _: None,
        )
        positions = [normalize_position({"symbol": "AAPL", "market": "US", "currency": "USD"})]

        for _ in range(2):
            provider.add_alpha_vantage({"equityQuotes": {}, "statuses": []}, positions)
        third = {"equityQuotes": {}, "statuses": []}
        provider.add_alpha_vantage(third, positions)

        self.assertEqual(2, len(calls))
        self.assertTrue(any("circuit open until" in item["message"] for item in third["statuses"]))

    def test_external_signal_provider_caps_fred_series_bulk(self):
        calls = []

        def fake_fetch(url, headers=None):
            calls.append(url)
            return {"observations": [{"date": "2026-07-01", "value": "4.35"}]}

        provider = ExternalSignalProvider(
            settings={
                "fredApiKey": "fred-key",
                "externalFredSeries": "DGS10,DGS2,DFF",
                "externalCryptoIds": " ",
                "externalFredMaxSeries": "2",
                "externalApiRetryAttempts": "1",
                "externalApiRateLimitSeconds": "0",
                "externalApiCircuitFailures": "2",
                "externalApiCircuitCooldownMinutes": "30",
            },
            cache=SQLiteExternalSignalCache(Path(self.temp.name) / "service.db"),
            fetch_json=fake_fetch,
            sleep=lambda _: None,
        )

        signals = provider.fetch_signals([])

        self.assertEqual(2, len(calls))
        self.assertEqual(["DGS10", "DGS2"], list(signals["macro"]["series"].keys()))
        self.assertTrue(any(item["source"] == "FRED" and item["ok"] for item in signals["statuses"]))

    def test_message_type_check_command_does_not_send_by_default(self):
        args = build_parser().parse_args(["monitor", "message-types"])

        self.assertFalse(args.send)
        self.assertFalse(args.json)
        self.assertEqual("message-types", args.monitor_action)

    def test_handoff_message_includes_summary_without_secrets(self):
        message = build_handoff_message(
            "환율 평가액 수정",
            commit="abc1234",
            validation="npm test 통과",
            push="origin/main 성공",
            details="토큰 없음",
        )

        self.assertTrue(message.startswith("작업 완료"))
        self.assertIn("타입: workHandoff", message)
        self.assertIn("환율 평가액 수정", message)
        self.assertIn("abc1234", message)
        self.assertNotIn("telegram", message.lower())
        self.assertNotIn("secret", message.lower())

    def test_decision_change_message_includes_model_review(self):
        previous_position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "marketValue": 1000,
            "quantity": 1,
            "sellableQuantity": 1,
            "averagePrice": 100,
            "currentPrice": 103,
            "profitLossRate": 3,
            "sector": "AI/플랫폼",
        })
        previous_diversifier = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 100000000,
            "quantity": 10,
            "sector": "반도체",
        })
        previous_portfolio = portfolio_summary([previous_position, previous_diversifier])
        previous_snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            previous_portfolio,
            [previous_position, previous_diversifier],
            decisions_for_positions([previous_position, previous_diversifier], previous_portfolio),
        )
        current_position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "marketValue": 1250,
            "quantity": 1,
            "sellableQuantity": 1,
            "averagePrice": 100,
            "currentPrice": 125,
            "profitLossRate": 25,
            "sector": "AI/플랫폼",
        })
        current_portfolio = portfolio_summary([current_position])
        current_snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            current_portfolio,
            [current_position],
            decisions_for_positions([current_position], current_portfolio),
        )

        events = RealtimeMonitor().events_for_snapshot(current_snapshot, previous_snapshot.to_monitor_state())
        decision_event = self.insight_event(events, "AAPL")
        message = self.insight_source_message(decision_event, "monitorDecisionChange")

        self.assertEqual("Apple", decision_event.message().splitlines()[0])
        self.assertNotIn("메인 Apple", message)
        self.assertEqual("investmentInsight", decision_event.rule)
        self.assertIn("monitorDecisionChange", self.insight_source_rules(decision_event))
        self.assertFalse(any(event.rule == "monitorDecisionChange" for event in events))
        self.assertIn("보유 종목 판단 변화", message)
        self.assertIn("권장 액션:", message)
        self.assertIn("Codex 답변:", message)
        self.assertIn("점수 해석:", message)
        self.assertIn("대응 필요 강도", message)
        self.assertIn("데이터 검증:", message)
        self.assertIn("모델 보완:", message)
        self.assertIn("손익률 급변", message)
        self.assertRegex(message, r"이전 .+ \([0-9.]+점\)")
        self.assertRegex(message, r"현재 .+ \([0-9.]+점\)")
        self.assertTrue(any("(" in item and "점)" in item for item in message.splitlines()))
        self.assertTrue(any("보유 종목" in item for item in message.splitlines()))

    def test_decision_change_event_enqueues_async_model_review(self):
        event = alerts_detected_event([
            SimpleNamespace(
                account_id="main",
                account_label="메인",
                severity="WATCH",
                rule="monitorDecisionChange",
                key="main:decision:AAPL",
                title="Apple",
                symbol="AAPL",
                lines=["판단 변화", "Codex 답변: 판단 변경"],
            )
        ])
        store = ModelReviewJobStore(Path(self.temp.name) / "model-review-queue.json")

        ModelReviewEnqueuer(store).handle(event)
        ModelReviewEnqueuer(store).handle(event)

        jobs = store.pending(limit=10)
        self.assertEqual(1, len(jobs))
        self.assertEqual("main", jobs[0].account_id)
        self.assertEqual("AAPL", jobs[0].symbol)
        self.assertIn("Codex 답변", "\n".join(jobs[0].alert_lines))

    def test_model_review_job_keeps_active_investment_opinion_context(self):
        job = ModelReviewJob.create({
            "key": "main:decision:035420",
            "accountId": "main",
            "accountLabel": "메인",
            "symbol": "035420",
            "title": "NAVER",
            "lines": ["판단 변화", "AI 적극 의견: 매도 · 확신 83.5%"],
            "metadata": {
                "ontologyRelationContext": {"decision": {"label": "손절·분할축소 권장"}},
                "activeInvestmentOpinion": {
                    "action": "SELL",
                    "actionLabel": "매도",
                    "thesis": "유상증자 공시와 하락 추세가 겹쳤습니다.",
                    "invalidationCondition": "20일선 회복 시 재검토",
                },
            },
        })

        prompt = build_model_review_prompt(job)

        self.assertEqual("SELL", job.review_context["activeInvestmentOpinion"]["action"])
        self.assertIn("BUY/ADD/HOLD/TRIM/SELL/AVOID", prompt)
        self.assertIn("activeInvestmentOpinion", prompt)

    def test_monitor_decision_change_is_only_for_current_holdings(self):
        previous_position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "marketValue": 1000,
            "quantity": 1,
            "sellableQuantity": 1,
            "averagePrice": 100,
            "currentPrice": 125,
            "profitLossRate": 25,
            "sector": "AI/플랫폼",
        })
        previous_portfolio = portfolio_summary([previous_position])
        previous_snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            previous_portfolio,
            [previous_position],
            decisions_for_positions([previous_position], previous_portfolio),
        )
        current_watch = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "marketValue": 0,
            "quantity": 0,
            "currentPrice": 130,
            "profitLossRate": 0,
            "sector": "AI/플랫폼",
        })
        current_snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio_summary([]),
            [],
            [],
            watchlist=[current_watch],
        )

        events = RealtimeMonitor().events_for_snapshot(current_snapshot, previous_snapshot.to_monitor_state())

        insight = self.insight_event(events, "AAPL")
        self.assertIn("monitorPositionChange", self.insight_source_rules(insight))
        self.assertFalse(any(event.rule == "monitorPositionChange" for event in events))
        self.assertFalse(any(event.rule == "monitorDecisionChange" for event in events))

    def test_monitor_decision_change_suppresses_equivalent_crypto_label_noise(self):
        monitor = RealtimeMonitor()
        position = normalize_position({
            "symbol": "STRC",
            "name": "Strategy Preferred",
            "market": "US",
            "currency": "USD",
            "marketValue": 1000,
            "quantity": 1,
            "sellableQuantity": 1,
            "currentPrice": 88.74,
            "profitLossRate": -4.0,
            "ma20": 88.65,
            "ma60": 95.58,
            "ma20Distance": 0.1,
            "ma60Distance": -7.2,
            "sector": "디지털자산",
        })
        portfolio = portfolio_summary([position], fx_rates={"USD": 1400, "KRW": 1})
        external_signals = {
            "cryptoMarkets": {
                "bitcoin": {
                    "provider": "CoinGecko",
                    "symbol": "BTC",
                    "price": 108000,
                    "volume24h": 42000000000,
                    "change24h": 0.1,
                    "change7d": 5.7,
                }
            }
        }
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio,
            [position],
            [],
            external_signals=external_signals,
        )
        scored_snapshot = monitor.snapshot_with_strategy_scores(snapshot)
        previous = scored_snapshot.to_monitor_state()
        previous["decisions"]["STRC"]["decision"] = "비트코인 민감도 축소 검토"
        previous["decisions"]["STRC"]["exit_pressure"] = 64.5

        events = monitor.events_for_snapshot(scored_snapshot, previous)

        insight = self.insight_event(events, "BTC")
        self.assertFalse(any(event.rule == "monitorDecisionChange" for event in events))
        self.assertNotIn("monitorDecisionChange", self.insight_source_rules(insight))

    def test_model_review_runner_sends_deferred_review_message(self):
        registry = AccountRegistry()
        registry.upsert(AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"]))
        store = ModelReviewJobStore(Path(self.temp.name) / "model-review-queue.json")
        store.enqueue(ModelReviewJob.create({
            "accountId": "main",
            "accountLabel": "메인",
            "symbol": "AAPL",
            "title": "Apple",
            "key": "main:decision:AAPL",
            "lines": ["판단 변화", "데이터 검증: 평가액, 수량, 손익률, 판단 라벨이 모두 비교 가능"],
            "metadata": {"activeInvestmentOpinion": {"action": "SELL"}},
        }))
        sent = []

        class FakeReviewer:
            def review(self, job):
                return local_model_review(job)

        class FakeNotifier:
            def send(self, message):
                sent.append(message)
                return SimpleNamespace(delivered=True, reason="")

        runner = ModelReviewRunner(store, FakeReviewer(), registry, lambda _account: FakeNotifier())

        processed = runner.run_once(limit=1)

        self.assertEqual(1, processed)
        self.assertEqual({"done": 1}, store.summary())
        self.assertIn("이 메시지는 실시간 알림 이후 생성된 후속 분석입니다.", sent[0])
        self.assertTrue(sent[0].startswith("🧠 판단 변화 후속 리뷰: Apple / AAPL"))
        self.assertNotIn("메인 AAPL 모델 리뷰", sent[0])

    def test_model_review_runner_stores_non_actionable_review_without_telegram(self):
        registry = AccountRegistry()
        registry.upsert(AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["STRC"]))
        store = ModelReviewJobStore(Path(self.temp.name) / "model-review-queue.json")
        store.enqueue(ModelReviewJob.create({
            "accountId": "main",
            "accountLabel": "메인",
            "symbol": "STRC",
            "title": "Strategy Preferred",
            "key": "main:decision:STRC",
            "lines": ["판단 변화", "현재 보유 유지 (68점)"],
            "metadata": {"activeInvestmentOpinion": {"action": "HOLD"}},
        }))
        sent = []

        class FakeReviewer:
            def review(self, job):
                return local_model_review(job)

        class FakeNotifier:
            def send(self, message):
                sent.append(message)
                return SimpleNamespace(delivered=True, reason="")

        runner = ModelReviewRunner(store, FakeReviewer(), registry, lambda _account: FakeNotifier())

        processed = runner.run_once(limit=1)
        jobs = store.jobs()

        self.assertEqual(1, processed)
        self.assertEqual([], sent)
        self.assertEqual({"done": 1}, store.summary())
        self.assertTrue(jobs[0].result.startswith("🧠 판단 변화 후속 리뷰: Strategy Preferred / STRC"))
        self.assertIn("이 메시지는 실시간 알림 이후 생성된 후속 분석입니다.", jobs[0].result)

    def test_model_review_normalizes_code_only_subject_and_same_score_label_change(self):
        job = ModelReviewJob.create({
            "accountId": "main",
            "accountLabel": "메인",
            "symbol": "035420",
            "title": "035420",
            "key": "main:decision:035420",
            "lines": [
                "보유 종목 판단 변화",
                "이전 손실 관리 기준 확인 (80점)",
                "현재 손실 축소 권장 (80점)",
            ],
        })

        message = local_model_review(job)

        self.assertTrue(message.startswith("NAVER / 035420 모델 리뷰"))
        self.assertIn("점수는 80점으로 같고", message)
        self.assertIn("판단 라벨 체계", message)
        self.assertIn("라벨만 바뀐 알림", message)

    def test_model_review_runner_rewrites_code_only_llm_title(self):
        registry = AccountRegistry()
        registry.upsert(AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["035420"]))
        store = ModelReviewJobStore(Path(self.temp.name) / "model-review-queue.json")
        store.enqueue(ModelReviewJob.create({
            "accountId": "main",
            "accountLabel": "메인",
            "symbol": "035420",
            "title": "035420",
            "key": "main:decision:035420",
            "lines": ["판단 변화"],
            "metadata": {"activeInvestmentOpinion": {"action": "SELL"}},
        }))
        sent = []

        class FakeReviewer:
            def review(self, job):
                return "035420 판단 변화 리뷰\n- 판단 변화 원인: 라벨 변경"

        class FakeNotifier:
            def send(self, message):
                sent.append(message)
                return SimpleNamespace(delivered=True, reason="")

        runner = ModelReviewRunner(store, FakeReviewer(), registry, lambda _account: FakeNotifier())

        processed = runner.run_once(limit=1)

        self.assertEqual(1, processed)
        self.assertTrue(sent[0].startswith("🧠 판단 변화 후속 리뷰: NAVER / 035420"))
        self.assertIn("이 메시지는 실시간 알림 이후 생성된 후속 분석입니다.", sent[0])
        self.assertFalse(sent[0].splitlines()[0].startswith("035420 "))

    def test_send_events_enqueues_notifications_without_direct_delivery(self):
        account = AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"], message_delivery_level="absoluteBeginner")
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        rules = SQLiteNotificationRuleStore(db_path)
        rule = rules.get("monitorTrendChange")
        rule.market_hours_enabled = False
        rules.upsert(rule)
        event = self.mark_event_fresh(AlertEvent("main", "메인", "ALERT", "monitorTrendChange", "main:trend", "SK하이닉스", ["이동평균 변화", "20일선 하향 이탈"], "000660"))
        source_event = alerts_detected_event([event])

        result = send_events([event], accounts={"main": account}, queue=queue, source_event=source_event)
        duplicate = send_events([event], accounts={"main": account}, queue=queue, source_event=source_event)

        self.assertTrue(result.delivered)
        self.assertEqual(1, result.queued)
        self.assertEqual(0, duplicate.queued)
        jobs = queue.pending(limit=10)
        self.assertEqual(1, len(jobs))
        self.assertEqual("pending", jobs[0].status)
        self.assertEqual("monitorTrendChange", jobs[0].message_type)
        self.assertEqual(source_event.event_id, jobs[0].source_event_id)
        self.assertEqual(source_event.name, jobs[0].source_event_name)
        self.assertTrue(jobs[0].dedupe_key)
        self.assertEqual("main:trend", jobs[0].context["key"])
        self.assertEqual("monitorTrendChange", jobs[0].context["rule"])
        self.assertEqual("absoluteBeginner", jobs[0].context["messageDeliveryLevel"])
        self.assertEqual("왕초보", jobs[0].context["messageDeliveryProfile"]["label"])
        self.assertEqual("SK하이닉스", jobs[0].context["title"])
        self.assertIn("이동평균", jobs[0].context["lines"])
        self.assertGreaterEqual(jobs[0].context["honeyScore"], jobs[0].context["honeyThreshold"])
        self.assertIn("SK하이닉스", jobs[0].text)

    def test_notification_rule_suppresses_low_score_heartbeat(self):
        queue = SQLiteNotificationJobStore(Path(self.temp.name) / "service.db")
        event = AlertEvent("main", "메인", "INFO", "monitorHeartbeat", "main:heartbeat", "상태 확인", ["모니터링 정상 작동", "보유 5개"], "")

        result = send_events([event], queue=queue)

        self.assertTrue(result.delivered)
        self.assertEqual(0, result.queued)
        self.assertEqual([], queue.pending(limit=10))
        jobs = queue.jobs()
        self.assertEqual(1, len(jobs))
        self.assertEqual("suppressed", jobs[0].status)
        self.assertIn("발송 우선도", jobs[0].last_error)
        self.assertLess(jobs[0].context["honeyScore"], jobs[0].context["honeyThreshold"])

    def test_notification_jobs_payload_exposes_honey_decisions(self):
        queue = SQLiteNotificationJobStore()
        event = AlertEvent("main", "메인", "INFO", "monitorHeartbeat", "main:heartbeat", "상태 확인", ["모니터링 정상 작동", "보유 5개"], "")
        send_events([event], queue=queue)

        payload = notification_jobs_payload({"limit": ["10"]})

        self.assertEqual(1, len(payload["jobs"]))
        item = payload["jobs"][0]
        self.assertEqual("monitorHeartbeat", item["messageType"])
        self.assertEqual("suppressed", item["status"])
        self.assertEqual("suppressed", item["honeyDecision"])
        self.assertIn("발송 우선도", item["lastError"])
        self.assertIn("모니터링 정상 작동", item["fullText"])
        self.assertNotIn("<b>", item["fullText"])
        self.assertLess(item["honeyScore"], item["honeyThreshold"])
        self.assertTrue(item["honeyReasons"])
        self.assertEqual(10, payload["limit"])
        self.assertEqual({"suppressed": 1}, payload["summary"])

    def test_alert_context_scores_from_structured_notification_signals(self):
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "monitorTrendChange",
            "main:trend:000660",
            "SK하이닉스",
            ["추세: 현재 2,360,000원, 20일선 2,490,000원(-5.4%)", "수급: 거래량 4,816,364(0.5x)"],
            "000660",
        )
        context = alert_context(event)
        job = NotificationJob.create(
            "구조화 신호 검증",
            account_id="main",
            account_label="메인",
            message_type=event.rule,
            context=context,
        )

        decision = evaluate_notification_rule(job, default_notification_rule(event.rule))

        self.assertIn("important", context["notificationSignals"])
        self.assertIn("confirmingData", context["notificationSignals"])
        self.assertIn("핵심 투자 단어 +15", decision.reasons)
        self.assertIn("확인 데이터 포함 +10", decision.reasons)

    def test_notification_render_uses_symbol_display_name(self):
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "holdingTiming",
            "main:timing:005930",
            "삼성전자",
            ["상태 손절 기준 확인 (91점)"],
            "005930",
        )

        context = alert_context(event)
        message = render_notification(
            NotificationTemplate("holdingTiming", "{symbol}\n{symbolLine}\n{targetLine}\n{readableMessage}"),
            context,
        )

        self.assertEqual("005930", context["symbol"])
        self.assertEqual("삼성전자", context["symbolDisplayName"])
        self.assertEqual("삼성전자 / 005930", context["symbolWithCode"])
        self.assertIn("삼성전자 / 005930", message)

    def test_notification_render_falls_back_to_company_name_before_symbol_code(self):
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "monitorDecisionChange",
            "main:decision:035420",
            "035420",
            ["판단 변화", "이전 조건부 보유 (52점)", "현재 손실 관리 기준 확인 (56점)"],
            "035420",
        )

        context = alert_context(event)
        message = render_notification(
            NotificationTemplate("monitorDecisionChange", "{symbol}\n{symbolLine}\n{targetLine}\n{telegramMessage}"),
            context,
        )

        self.assertEqual("035420", context["symbol"])
        self.assertEqual("NAVER", context["symbolDisplayName"])
        self.assertEqual("NAVER / 035420", context["symbolWithCode"])
        self.assertIn("NAVER / 035420", message)
        self.assertNotIn("<code>035420</code>", message)

    def test_notification_render_shows_ontology_rule_context(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        position = Position(
            symbol="MSTR",
            name="Strategy",
            market="US",
            currency="USD",
            market_value=1000,
            profit_loss_rate=18.5,
            sellable_quantity=1,
            current_price=105.36,
            ma20=108.07,
            ma60=144.62,
            ma20_distance=-2.5,
            ma60_distance=-27.1,
            sector="BTC",
        )
        relation_context = evaluate_position_relation_rules(
            position,
            portfolio_summary([position]),
            external_signals={
                "cryptoMarkets": {
                    "bitcoin": {
                        "provider": "CoinGecko",
                        "symbol": "BTC",
                        "price": 108000,
                        "volume24h": 42000000000,
                        "change24h": 3.8,
                        "change7d": 9.7,
                    }
                }
            },
        )
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "holdingTiming",
            "main:timing:MSTR",
            "Strategy",
            ["상태 분할 매도 기준 확인 (80점)", "손익 +18.5%", "온톨로지: 보유 유지 · 관계 압력 21점", "thesis: 내부 용어 노출"],
            "MSTR",
            metadata={
                "ontologyRelationContext": relation_context,
                "ontologyPromptContext": relation_context["promptContext"],
            },
        )

        message = templates.render(event.rule, alert_context(event))

        self.assertIn("관계 규칙", message)
        self.assertIn("수익 보유 + 추세 약화", message)
        self.assertIn("점수 해석", message)
        self.assertIn("가격 방향 예측 점수가 아닙니다", message)
        self.assertIn("AI 분석 기준", message)
        self.assertNotIn("체결강도", message)
        self.assertIn("수익 +18.5%: 분할매도 권장", message)
        self.assertNotIn("온톨로지 판단", message)
        self.assertNotIn("AI 프롬프트", message)
        self.assertNotIn("알림 발송", message)
        self.assertNotIn("온톨로지: 온톨로지", message)
        self.assertNotIn("온톨로지: 보유 유지", message)
        self.assertNotIn("thesis:", message)
        self.assertNotIn("thesis", message)
        self.assertNotIn("관계 압력", message)
        self.assertNotIn("모델 공식", message)

    def test_notification_render_shows_fx_and_rate_context(self):
        position = Position(
            symbol="NVDA",
            name="NVIDIA",
            market="US",
            currency="USD",
            market_value=1000,
            profit_loss_rate=4.2,
            sellable_quantity=1,
            current_price=180.25,
            ma20=175.0,
            ma60=160.0,
            sector="반도체",
        )
        external_signals = {
            "fxRates": {
                "USDKRW": {
                    "base": "USD",
                    "quote": "KRW",
                    "rate": 1400,
                    "provider": "RuntimeSettings",
                }
            },
            "macro": {
                "series": {
                    "DGS10": {"value": 4.35, "provider": "FRED"},
                    "DGS2": {"value": 4.1, "provider": "FRED"},
                    "DFF": {"value": 5.33, "provider": "FRED"},
                },
                "yieldSpread10y2y": 0.25,
            },
        }
        relation_context = evaluate_position_relation_rules(
            position,
            portfolio_summary([position]),
            external_signals=external_signals,
        )
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "investmentInsight",
            "main:insight:NVDA",
            "NVIDIA",
            ["상태 조건부 보유 (62점)", "핵심 결론: 환율과 금리 민감도를 함께 점검합니다."],
            "NVDA",
            metadata={
                "ontologyRelationContext": relation_context,
                "ontologyPromptContext": relation_context["promptContext"],
            },
        )

        context = alert_context(event)
        message = render_notification(NotificationTemplate("investmentInsight", "{telegramMessage}"), context)

        self.assertIn("환율: USD/KRW", context["rawLines"])
        self.assertIn("금리: 미국10년 4.35%", context["rawLines"])
        self.assertIn("1 USD = 1,400 KRW", message)
        self.assertIn("<b>환율</b>", message)
        self.assertIn("<b>금리</b>", message)
        self.assertIn("미국2년 4.1%", message)
        self.assertIn("10Y-2Y +0.25%p", message)

    def test_notification_render_skips_zero_or_base_currency_fx_context(self):
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "investmentInsight",
            "main:insight:005930",
            "삼성전자",
            ["상태 조건부 보유 (62점)"],
            "005930",
            metadata={
                "ontologyRelationContext": {
                    "executionPlan": {
                        "sourceFacts": {
                            "fxRatePair": "",
                            "fxBaseCurrency": "KRW",
                            "fxQuoteCurrency": "KRW",
                            "fxRateToKrw": 0,
                            "usdKrwRate": 0,
                            "fxRegime": "base_currency_or_unknown",
                            "macroDgs10": 4.55,
                            "macroDgs2": 4.19,
                            "macroYieldSpread10y2y": 0.36,
                            "rateRegime": "high_rate",
                            "yieldCurveRegime": "positive_curve",
                        }
                    }
                }
            },
        )

        context = alert_context(event)
        message = render_notification(NotificationTemplate("investmentInsight", "{telegramMessage}"), context)

        self.assertNotIn("1 USD = 0 KRW", message)
        self.assertNotIn("KRW/KRW", message)
        self.assertNotIn("<b>환율</b>", message)
        self.assertIn("<b>금리</b>", message)

    def test_investment_insight_telegram_message_does_not_duplicate_ontology_sections(self):
        position = Position(
            symbol="035420",
            name="NAVER",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=-3.6,
            sellable_quantity=5,
            current_price=197200,
            ma20=213940,
            ma60=217075,
            ma20_distance=-7.8,
            ma60_distance=-9.2,
            trade_strength=90,
            buy_volume=100,
            sell_volume=120,
        )
        relation_context = evaluate_position_relation_rules(position, portfolio_summary([position], fx_rates={"KRW": 1}))
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "investmentInsight",
            "main:insight:035420",
            "NAVER",
            ["상태 손실 축소 권장 (80점)", "핵심 결론: NAVER의 보유 판단과 관계 신호가 리스크 관리 쪽으로 기울었습니다."],
            "035420",
            metadata={
                "ontologyRelationContext": relation_context,
                "ontologyPromptContext": relation_context["promptContext"],
            },
        )

        message = render_notification(NotificationTemplate("investmentInsight", "{telegramMessage}"), alert_context(event))

        self.assertEqual(1, message.count("<b>관계 규칙</b>"))
        self.assertEqual(1, message.count("<b>AI 분석 기준</b>"))
        self.assertEqual(1, message.count("\n<b>부족 데이터</b>\n"))
        self.assertNotIn("관계 판단", message)
        self.assertNotIn("알림 발송", message)

    def test_all_alert_types_have_managed_ai_prompt_templates(self):
        settings = {
            "aiPromptTemplates": "\n".join([
                "[holdingTiming]",
                "label=사용자 보유 프롬프트",
                "version=custom-holding-v1",
                "purpose=사용자 목적",
                "system=custom system",
                "user=custom user",
            ])
        }

        for message_type in sorted(DEFAULT_ALERT_RULES.keys()):
            template = prompt_template_for_message_type(message_type, settings)
            self.assertEqual(message_type, template.prompt_id)
            self.assertTrue(template.label)

        holding = prompt_template_for_message_type("holdingTiming", settings)
        self.assertEqual("사용자 보유 프롬프트", holding.label)
        self.assertEqual("custom-holding-v1", holding.version)

    def test_notification_render_adds_ai_opinion_and_prompt_for_every_alert(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "holdingTiming",
            "main:timing:005930",
            "삼성전자",
            ["상태 조건부 보유 (52점)", "현재가: 100,000원", "평단가: 110,000원", "수익률: -9.0%"],
            "005930",
            criteria=[
                "설정: 판단 상태가 위험/주의이거나 손익률이 -8% 이하일 때",
                "감지: 상태 조건부 보유 (52점), 수익률 -9.0%",
            ],
        )

        message = templates.render(event.rule, alert_context(event))

        self.assertIn("<b>AI 의견</b>", message)
        self.assertIn("• <b>상황</b>:", message)
        self.assertIn("• <b>의견</b>:", message)
        self.assertIn("• <b>다음 확인</b>:", message)
        self.assertIn("• <b>분석출처</b>: 알림 AI 의견 / holdingTiming", message)
        self.assertIn("<b>AI 프롬프트</b>", message)
        self.assertIn("보유 타이밍 AI 분석", message)
        self.assertNotIn("thesis", message)
        self.assertLess(message.index("<b>데이터</b>"), message.index("<b>AI 의견</b>"))
        self.assertLess(message.index("<b>AI 의견</b>"), message.index("<b>발송 기준</b>"))

    def test_holding_timing_ai_opinion_uses_news_and_disclosure_context(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        position = normalize_position({
            "symbol": "035420",
            "name": "NAVER",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1972000,
            "quantity": 10,
            "averagePrice": 204000,
            "currentPrice": 197200,
            "profitLossRate": -3.4,
            "volume": 1077802,
            "volumeRatio": 1.6,
            "tradingValue": 211200000000,
            "tradeStrength": 87.3,
            "orderbookBidVolume": 11629,
            "orderbookAskVolume": 23639,
            "bidAskImbalance": -34.1,
            "ma20": 215135,
            "ma60": 217132,
        })
        relation_context = evaluate_position_relation_rules(
            position,
            portfolio_summary([position]),
            external_signals={
                "dartDisclosures": {
                    "035420": {
                        "provider": "OpenDART",
                        "corpName": "NAVER",
                        "reportName": "[기재정정]주식교환ㆍ이전결정",
                        "receiptNo": "20260706000001",
                        "receiptDate": "20260706",
                    }
                },
                "newsHeadlines": {
                    "035420": {
                        "provider": "GDELT",
                        "items": [{
                            "title": "NAVER investors weigh governance update and platform growth",
                            "url": "https://example.test/naver",
                            "domain": "example.test",
                            "seenDate": "20260707120000",
                        }],
                    }
                },
            },
        )
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "holdingTiming",
            "main:timing:035420",
            "NAVER",
            [
                "상태 손실 축소 권장 (80점)",
                "현재가: 197,200원",
                "평단가: 204,000원",
                "수익률: -3.4%",
                "수급: 거래량 1,077,802(1.6x), 거래액 2112억 원, 체결강도 87.3, 호가불균형 -34.1%",
                "추세: 20일선 215,135원보다 8.3% 낮음, 60일선 217,132원보다 9.2% 낮음",
                "권장 액션: 손절·분할축소 우선, 20일선 회복 전 추가매수 보류",
            ],
            "035420",
            criteria=[
                "설정: 관계 규칙이 위험/주의 상태로 성립할 때",
                "감지: 상태 손실 축소 권장 (80점)",
            ],
            metadata={
                "ontologyRelationContext": relation_context,
                "ontologyPromptContext": relation_context["promptContext"],
            },
        )

        message = templates.render(event.rule, alert_context(event))

        self.assertIn("• <b>상황</b>:", message)
        self.assertIn("• <b>수급·추세</b>:", message)
        self.assertIn("• <b>뉴스·공시</b>:", message)
        self.assertIn("[기재정정]주식교환ㆍ이전결정", message)
        self.assertIn("NAVER investors weigh governance update", message)
        self.assertNotIn("신호가 성립했습니다", message)
        self.assertNotIn("새 매수보다 기존 보유 이유", message)

    def test_investment_insight_ai_opinion_is_action_oriented_and_sanitized(self):
        context = {
            "messageType": "investmentInsight",
            "target": "NAVER / 035420",
            "rawLines": [
                "현재가: 197,200원",
                "평단가: 204,000원",
                "수익률: -3.4%",
                "수급: 거래량 1,077,802(1.6x), 체결강도 87.3",
                "추세: 20일선보다 8.3% 낮음, 60일선보다 9.2% 낮음",
                "권장 액션: 손절·분할축소 우선, 20일선 회복 전 추가매수 보류",
                "주요 리스크: 추세 관계가 약화",
            ],
            "ontologyInsight": {
                "insightLabel": "리스크 관리",
                "thesis": "가격·추세·공시가 리스크 관리 쪽으로 연결됐습니다.",
                "sourceSignalTypes": ["holdingTiming"],
                "nextCheck": "20일선 회복과 공시 원문을 확인하세요.",
            },
            "metadata": {
                "telegramBotToken": "secret-token",
                "ontologyRelationContext": {
                    "facts": {
                        "trendDynamics": {
                            "state": "60일선 지지 재확인",
                            "priceMomentum": "보합",
                            "priceChangeRate": 0.2,
                            "slope": "단기 둔화·중기 지지",
                            "curve": "단기 둔화 커브",
                            "trendCurve": -0.6,
                            "supportRetest": True,
                            "recoveryAttempt": False,
                            "breakdownAcceleration": False,
                            "dynamicRiskScore": 42.5,
                        },
                        "dartDisclosure": {
                            "reportName": "주식교환ㆍ이전결정",
                            "receiptDate": "20260706",
                        },
                        "newsHeadlines": {
                            "items": [{"title": "NAVER governance update", "domain": "example.test"}],
                        },
                        "researchEvidence": [{
                            "evidenceId": "research:035420:financial-facts",
                            "symbol": "035420",
                            "kind": "financial-fact",
                            "source": "SEC EDGAR",
                            "title": "회사 재무 요약",
                            "summary": "NAVER: 매출 $2.1B, 순이익 $0.3B",
                            "polarity": "context",
                            "impactScore": 4,
                            "confidence": 0.7,
                        }],
                    }
                },
            },
        }

        opinion = build_notification_ai_opinion(context)
        text = "\n".join(opinion["lines"])

        self.assertIn("판단: 추가매수 보류", text)
        self.assertIn("가격 위치", text)
        self.assertIn("뉴스·공시", text)
        self.assertIn("NAVER governance update", text)
        self.assertIn("회사 재무 요약", text)
        self.assertIn("공시 의미", text)
        self.assertIn("추세 동역학", text)
        self.assertEqual("회사 재무 요약", opinion["promptContext"]["facts"]["researchEvidence"][0]["title"])
        self.assertTrue(opinion["promptContext"]["facts"]["trendDynamics"]["supportRetest"])
        self.assertEqual("[redacted]", opinion["promptContext"]["facts"]["allAvailableData"]["metadata"]["telegramBotToken"])
        self.assertIn("allAvailableData", opinion["promptContext"]["facts"])

    def test_investment_insight_ai_opinion_interprets_self_stock_disposal_disclosure(self):
        context = {
            "messageType": "investmentInsight",
            "target": "삼성전자 / 005930",
            "symbol": "005930",
            "rawLines": [
                "현재가: 291,000원",
                "평단가: 327,000원",
                "수익률: -11.3%",
                "권장 액션: 손절·분할축소 우선, 20일선 회복 전 추가매수 보류",
                "인사이트 유형: 리스크 증가",
            ],
            "ontologyInsight": {
                "insightLabel": "리스크 증가",
                "thesis": "손익률 변화, 이동평균 변화, 보유 타이밍이 함께 강해졌습니다.",
                "sourceSignalTypes": ["holdingTiming"],
            },
            "metadata": {
                "ontologyRelationContext": {
                    "facts": {
                        "dartDisclosure": {
                            "provider": "OpenDART",
                            "corpName": "삼성전자",
                            "reportName": "주요사항보고서(자기주식처분결정)",
                            "receiptNo": "20260707000403",
                            "receiptDate": "20260707",
                        }
                    }
                }
            },
        }

        opinion = build_notification_ai_opinion(context)
        text = "\n".join(opinion["lines"])

        self.assertIn("공시 의미", text)
        self.assertIn("보유 자기주식을 처분", text)
        self.assertIn("물량 부담", text)
        self.assertIn("추가매수는 보류", text)

    def test_investment_insight_ai_opinion_prefers_active_investment_opinion(self):
        context = {
            "messageType": "investmentInsight",
            "target": "NAVER / 035420",
            "rawLines": [
                "인사이트 유형: 리스크 관리",
                "핵심 결론: 가격·추세·공시가 리스크 관리 쪽으로 연결됐습니다.",
                "현재가: 188,000원",
                "수익률: -12.0%",
            ],
            "ontologyInsight": {
                "insightLabel": "리스크 관리",
                "thesis": "가격·추세·공시가 리스크 관리 쪽으로 연결됐습니다.",
                "sourceSignalTypes": ["holdingTiming"],
            },
            "metadata": {
                "activeInvestmentOpinion": {
                    "action": "SELL",
                    "actionLabel": "매도",
                    "conviction": 83.5,
                    "thesis": "유상증자 공시와 하락 추세가 겹쳐 리스크 축소가 우선입니다.",
                    "evidence": [
                        {
                            "title": "주요사항보고서(유상증자결정)",
                            "source": "OpenDART",
                            "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260706000123",
                        }
                    ],
                    "counterEvidence": [{"title": "20일선 회복 시 매도 강도 완화"}],
                    "invalidationCondition": "20일선 회복과 공시 리스크 해소가 확인되면 매도 강도를 낮춥니다.",
                    "nextCheck": "공시 원문, 발행 규모, 장중 거래량 반응을 확인하세요.",
                }
            },
        }

        opinion = build_notification_ai_opinion(context)
        text = "\n".join(opinion["lines"])

        self.assertIn("판단: 매도", text)
        self.assertIn("투자 의견 근거", text)
        self.assertIn("주요사항보고서(유상증자결정)", text)
        self.assertIn("반대 근거", text)
        self.assertIn("무효화 조건", text)
        self.assertEqual("SELL", opinion["promptContext"]["facts"]["activeInvestmentOpinion"]["action"])

    def test_validated_ai_response_rebuilds_execution_message(self):
        context = {
            "messageType": "investmentInsight",
            "headline": "[주의] 🛡️ 손실 -18.1%: 손절·분할축소 점검",
            "displayTarget": "SK하이닉스 / 000660",
            "referenceDate": "2026-07-08 14:26 KST",
            "sentTime": "2026-07-08 14:27 KST",
            "rawLines": "\n".join([
                "현재가: 2,115,000원",
                "평균매입가: 2,571,000원",
                "수익률: -18.1%",
                "보유 수량: 10주",
                "매도가능 수량: 10주",
                "종목 평가금액: 2,115만 원",
                "계좌 평가금액: 4,000만 원",
                "수급: 거래량 5,215,050(0.8x), 체결강도 95.2",
                "투자자: 외국인 -3,015,093(매수 8,922,904/매도 11,937,997), 기관 +971,031(매수 12,816,837/매도 11,845,806), 개인 +2,031,705(매수 11,457,143/매도 9,425,438)",
                "추세: 20일선보다 15.2% 낮음, 60일선보다 8.4% 높음",
                "기준일: 2026-07-08 14:26 KST",
            ]),
            "criterionLines": "설정: 관계 그래프에서 의미 있는 투자 인사이트가 생성될 때",
            "ontologyRelationContext": {
                "missingData": [{"label": "투자자별 수급", "effect": "응답 비어 있음"}],
                "executionPlan": {
                    "tboxClass": "ExecutionPlan",
                    "primaryAction": "TRIM_OR_SELL_REVIEW",
                    "primaryActionLabel": "추가매수 보류, 분할축소/매도 기준 검토",
                    "blockedActions": ["20일선 회복 전 추가매수"],
                    "nextChecks": ["매도 가능 수량 확인"],
                },
            },
        }

        response = validated_response_from_payload(context, {
            "action": "SELL",
            "confidence": 94,
            "summary": "손실과 20일선 이탈이 함께 강합니다.",
            "opinion": "분할축소를 우선 검토하고 추가매수는 보류하세요.",
            "evidence": ["손실 -18.1%", "20일선보다 15.2% 낮음"],
            "counterEvidence": ["60일선은 아직 위에 있음"],
            "invalidationCondition": "20일선 회복과 거래량 동반 반등이 확인되면 매도 강도를 낮춥니다.",
            "nextChecks": ["매도 가능 수량과 공시 원문을 확인"],
            "missingDataImpact": [],
            "referenceDate": "2026-07-08 14:26 KST",
        }, source="test AI")
        enriched = context_with_validated_ai_response(context, response)
        message = enriched["telegramMessage"]

        self.assertIn("<b>판단</b>", message)
        self.assertIn("매도", message)
        self.assertIn("<b>평균매입가</b>: <code>2,571,000원</code>", message)
        self.assertIn("<b>보유 수량</b>: <code>10주</code>", message)
        self.assertIn("<b>매도가능 수량</b>: <code>10주</code>", message)
        self.assertIn("<b>종목 평가금액</b>: <code>2,115만 원</code>", message)
        self.assertIn("<b>계좌 평가금액</b>: <code>4,000만 원</code>", message)
        self.assertIn("반대 신호", message)
        self.assertIn("60일선은 아직 위", message)
        self.assertIn("투자자별 수급", message)
        self.assertIn("외국인 -3,015,093", message)
        self.assertIn("2026-07-08 14:26 KST", message)
        self.assertNotIn("관계 규칙", message)
        self.assertNotIn("AI 분석 기준", message)
        self.assertEqual("SELL", enriched["notificationAiValidatedResponse"]["action"])
        assertions = enriched["ontologyAssertions"]
        self.assertEqual("ABox", assertions["box"])
        self.assertIn("AIValidation", {item["tboxClass"] for item in assertions["entities"]})
        self.assertIn("ValidatedOpinion", {item["tboxClass"] for item in assertions["entities"]})
        self.assertIn("ExecutionPlan", {item["tboxClass"] for item in assertions["entities"]})
        self.assertIn("VALIDATES_OPINION", {item["relationType"] for item in assertions["relations"]})
        self.assertIn("HAS_EXECUTION_PLAN", {item["relationType"] for item in assertions["relations"]})
        self.assertIn("PRODUCES_VALIDATED_MESSAGE", {item["relationType"] for item in assertions["relations"]})
        self.assertIn("PRODUCES_AI_DECISION", {item["relationType"] for item in assertions["relations"]})
        self.assertTrue(enriched["ontologyAiValidation"]["assertionIds"])
        self.assertEqual("ai-first", enriched["notificationAiGate"]["decisionMode"])
        self.assertEqual("aiResponse", enriched["ontologyAiValidation"]["finalDecisionOwner"])
        rendered = render_notification(NotificationTemplate("investmentInsight", "{telegramMessage}"), enriched)
        self.assertNotIn("AI 의견", rendered)
        self.assertEqual(1, rendered.count("<b>알림 정보</b>"))
        self.assertIn("• <b>분석</b>: <code>AI 투자 판단 / test AI</code>", rendered)

    def test_validated_ai_response_omits_empty_current_state_section(self):
        context = {
            "messageType": "investmentInsight",
            "headline": "[관찰] 🛡️ 손절·분할축소 점검",
            "displayTarget": "크립토 변동 / 이더리움 / ETH",
            "referenceDate": "2026-07-08 22:26 KST",
            "sentTime": "2026-07-08 22:26 KST",
            "rawLines": "\n".join([
                "인사이트 유형: 외부 환경 변화",
                "핵심 결론: 크립토 변동에 연결된 외부 시장 관계가 바뀌었습니다.",
                "기준일: 2026-07-08 22:26 KST",
            ]),
            "criterionLines": "설정: 관계 분석 관계 그래프에서 의미 있는 투자 인사이트가 생성될 때",
        }

        response = validated_response_from_payload(context, {
            "action": "HOLD",
            "confidence": 72.1,
            "summary": "이더리움 변동이 기준을 넘었습니다.",
            "opinion": "보유를 유지하되 민감 보유 종목 반응을 확인하세요.",
            "evidence": ["이더리움 7일 변동은 +10.8%로 설정 기준을 넘었습니다."],
            "counterEvidence": ["민감 보유 종목의 현재가, 거래량, 수익률 방향이 제공되지 않았습니다."],
            "nextChecks": ["민감 보유 종목이 실제로 가격 반응을 보이는지 확인합니다."],
            "missingDataImpact": ["민감 보유 종목의 현재 상태 자료가 없어 판단 강도를 낮춥니다."],
            "referenceDate": "2026-07-08 22:26 KST",
        }, source="test AI")

        enriched = context_with_validated_ai_response(context, response)
        message = enriched["telegramMessage"]

        self.assertIn("<b>판단</b>", message)
        self.assertNotIn("<b>현재 상태</b>", message)
        self.assertIn("<b>핵심 근거</b>", message)

    def test_validated_ai_response_uses_absolute_beginner_delivery_level(self):
        context = {
            "messageType": "investmentInsight",
            "messageDeliveryLevel": "absoluteBeginner",
            "headline": "[주의] 🛡️ 손실 -18%: 손절·분할축소 점검",
            "displayTarget": "삼성전자 / 005930",
            "referenceDate": "2026-07-08 22:26 KST",
            "sentTime": "2026-07-08 22:27 KST",
            "rawLines": "\n".join([
                "현재가: 277,500원",
                "평균매입가: 327,000원",
                "수익률: -18.7%",
                "보유 수량: 10주",
                "추세: 20일 평균보다 15% 낮음",
                "투자자: 외국인: 순매도 3,015,093주, 매수 8,922,904주, 매도 11,937,997주, 기관: 순매수 971,031주, 매수 12,816,837주, 매도 11,845,806주, 개인: 순매수 2,031,705주, 매수 11,457,143주, 매도 9,425,438주",
                "기준일: 2026-07-08 22:26 KST",
            ]),
            "criterionLines": "설정: 관계 그래프에서 의미 있는 투자 인사이트가 생성될 때",
        }
        response = validated_response_from_payload(context, {
            "action": "SELL",
            "confidence": 94,
            "summary": "손실이 커졌습니다.",
            "opinion": "매도 가능 수량과 손실 관리 기준을 먼저 확인하세요.",
            "evidence": ["수익률이 -18.7%입니다.", "현재가가 20일 평균보다 낮습니다."],
            "counterEvidence": ["단기 반등 가능성은 남아 있습니다."],
            "nextChecks": ["매도 가능 수량 확인", "다음 조회에서도 약한 흐름이 유지되는지 확인"],
            "referenceDate": "2026-07-08 22:26 KST",
        }, source="test AI")

        message = context_with_validated_ai_response(context, response)["telegramMessage"]

        self.assertIn("<b>한줄 판단</b>", message)
        self.assertIn("<b>현재 상황</b>", message)
        self.assertIn("<b>투자자</b>", message)
        self.assertIn("외국인: 순매도 3,015,093주", message)
        self.assertIn("기관: 순매수 971,031주", message)
        self.assertIn("개인: 순매수 2,031,705주", message)
        self.assertIn("<b>왜 이렇게 봤나</b>", message)
        self.assertIn("<b>다르게 볼 점</b>", message)
        self.assertIn("<b>왜 온 알림</b>", message)
        self.assertNotIn("<b>핵심 근거</b>", message)

    def test_holding_snapshot_enricher_adds_missing_price_rows(self):
        position = normalize_position({
            "symbol": "MSTR",
            "name": "Strategy",
            "market": "US",
            "currency": "USD",
            "marketValue": 22535.4,
            "quantity": 230,
            "sellableQuantity": 230,
            "averagePrice": 88.9,
            "currentPrice": 97.98,
            "profitLossRate": 10.2,
            "sector": "디지털자산",
        })
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio_summary([position]),
            [position],
            decisions_for_positions([position], portfolio_summary([position])),
        )
        job = NotificationJob.create(
            "Strategy",
            account_id="main",
            account_label="메인",
            message_type="holdingTiming",
            context={
                "target": "MSTR",
                "displayTarget": "Strategy / MSTR",
                "rawLines": "\n".join([
                    "상태: 뉴스 근거 포함 보유 점검 (72점)",
                    "손익: +10.2%",
                    "추세: 현재 $97.98, 20일선 $106.72(-8.2%), 60일선 $144.03(-32.0%)",
                ]),
            },
        )

        NotificationHoldingSnapshotEnricher(lambda: {"main": snapshot.to_monitor_state()})(job)
        raw_lines = job.context["rawLines"]

        self.assertIn("현재가: $97.98", raw_lines)
        self.assertIn("평균매입가: $88.9", raw_lines)
        self.assertIn("보유 수량: 230주", raw_lines)
        self.assertIn("매도가능 수량: 230주", raw_lines)
        self.assertIn("종목 평가금액: $22,535", raw_lines)
        self.assertIn("계좌 평가금액: 3,155만 원", raw_lines)

    def test_validated_ai_response_hides_internal_variables_and_jargon(self):
        context = {
            "messageType": "investmentInsight",
            "headline": "[주의] 🛡️ 손실 -18.7%: 손절·분할축소 점검",
            "displayTarget": "삼성전자 / 005930",
            "referenceDate": "2026-07-08 18:54 KST",
            "sentTime": "2026-07-08 19:17 KST",
            "rawLines": "\n".join([
                "현재가: 277,500원",
                "평단가: 327,000원",
                "수익률: -18.7%",
                "수급: 거래량 33,525,758(1x), 체결강도 89, 호가불균형 +9.9%",
                "투자자: 외국인 -3,015,093(매수 8,922,904/매도 11,937,997), 기관 +971,031(매수 12,816,837/매도 11,845,806), 개인 +2,031,705(매수 11,457,143/매도 9,425,438)",
                "추세: 20일선 326,650원보다 15% 낮음, 60일선 286,967원보다 3.3% 낮음",
                "기준일: 2026-07-08 18:54 KST",
            ]),
            "criterionLines": "설정: 관계 그래프에서 의미 있는 투자 인사이트가 생성될 때",
        }

        response = validated_response_from_payload(context, {
            "action": "SELL",
            "confidence": 94,
            "summary": "추세 훼손과 하락 가속이 커져 SELL 의견이다.",
            "opinion": "자동 주문 지시가 아니라 투자 실행 알림 검증 의견으로 SELL을 선택한다.",
            "evidence": [
                "손실 보유 + 기준선 이탈 -> 손실 관리 규칙이 성립했다.",
                "추세 훼손 + 하락 가속 -> 리스크 강화 규칙의 강도는 98.4점이다.",
            ],
            "counterEvidence": [
                "entryAllocationRoom이 true이고 entrySupportCount가 2로 제공됐지만 entryExternalRiskBlocked도 true다.",
            ],
            "invalidationCondition": "기준선 이탈이 해소되고 하락 가속이 멈추면 SELL 의견을 낮춘다.",
            "nextChecks": ["entrySupportCount와 entryExternalRiskBlocked를 다시 확인"],
            "missingDataImpact": ["missingData는 빈 배열이다."],
            "referenceDate": "2026-07-08 18:54 KST",
        }, source="Codex AI")
        enriched = context_with_validated_ai_response(context, response)
        message = enriched["telegramMessage"]

        for hidden in ["entryAllocationRoom", "entrySupportCount", "entryExternalRiskBlocked", "SELL", "true", "false"]:
            self.assertNotIn(hidden, message)
        for jargon in ["기준선 이탈", "추세 훼손", "하락 가속", "무효화 조건"]:
            self.assertNotIn(jargon, message)
        self.assertIn("매도 의견을 선택", message)
        self.assertIn("주요 평균선 아래", message)
        self.assertIn("하락 속도가", message)
        self.assertIn("추가매수 여력과 일부 지지 신호", message)
        self.assertIn("의견이 약해지는 조건", message)

    def test_notification_ai_gate_prompt_requires_user_friendly_language(self):
        prompt = build_notification_ai_gate_prompt({
            "messageType": "investmentInsight",
            "displayTarget": "삼성전자 / 005930",
            "referenceDate": "2026-07-08 18:54 KST",
            "rawLines": "현재가: 277,500원",
        })

        self.assertIn("내부 변수명을 쓰지 않는다", prompt)
        self.assertIn("한국어 행동명만 쓴다", prompt)
        self.assertIn("주요 평균선 아래로 내려감", prompt)
        self.assertIn("최종 투자 의견을 판단하는 AI 분석가", prompt)
        self.assertIn("사전 계산 후보일 뿐 최종 답변이 아니다", prompt)
        self.assertIn("관계형/온톨로지 데이터베이스 추론", prompt)
        self.assertIn('"aiDecisionInput"', prompt)
        payload = json.loads(prompt.split("입력:", 1)[1].strip())
        self.assertEqual("ai-first", payload["aiDecisionInput"]["decisionMode"])
        self.assertEqual("aiResponse", payload["aiDecisionInput"]["finalDecisionOwner"])
        self.assertEqual("candidateEvidenceOnly", payload["aiDecisionInput"]["precomputedOpinionRole"])

    def test_notification_ai_gate_allows_ai_to_override_precomputed_opinion(self):
        context = {
            "messageType": "investmentInsight",
            "headline": "[주의] 🛡️ 손실 방어 점검",
            "displayTarget": "삼성전자 / 005930",
            "referenceDate": "2026-07-08 18:54 KST",
            "rawLines": "\n".join([
                "현재가: 277,500원",
                "수익률: -18.0%",
                "추세: 20일선보다 14.0% 낮음",
            ]),
            "activeInvestmentOpinion": {
                "action": "HOLD",
                "actionLabel": "보유",
                "conviction": 62,
                "thesis": "사전 계산은 보유 유지 후보입니다.",
            },
            "ontologyRelationContext": {
                "decision": {
                    "actionGroup": "lossControl",
                    "actionLevel": "action",
                    "score": 82,
                },
                "activeRules": [
                    {"ruleId": "holding.loss_guard.breakdown.v1", "label": "손실 보유 + 기준선 이탈 -> 손실 관리", "strengthScore": 82}
                ],
            },
        }
        response = validated_response_from_payload(context, {
            "action": "SELL",
            "confidence": 89,
            "summary": "손실과 주요 평균선 아래 상태가 겹쳐 AI가 사전 보유 후보보다 방어를 우선했습니다.",
            "opinion": "사전 계산 후보는 보유였지만, 관계 분석 위험이 더 강해 매도 기준을 먼저 확인합니다.",
            "evidence": ["손실 -18.0%", "손실 관리 관계 규칙 82점"],
            "counterEvidence": ["사전 계산 후보는 보유였음"],
            "invalidationCondition": "20일선 회복과 거래량 동반 반등이 확인되면 매도 강도를 낮춥니다.",
            "nextChecks": ["매도 가능 수량과 공시 원문 확인"],
            "missingDataImpact": [],
            "referenceDate": "2026-07-08 18:54 KST",
        }, source="test AI")
        enriched = context_with_validated_ai_response(context, response)

        self.assertEqual("SELL", enriched["notificationAiValidatedResponse"]["action"])
        self.assertEqual("HOLD", context["activeInvestmentOpinion"]["action"])
        self.assertIn("AI 투자 판단", enriched["telegramMessage"])
        self.assertIn("매도", enriched["telegramMessage"])
        self.assertIn("사전 계산 후보는 보유", enriched["telegramMessage"])

    def test_notification_worker_waits_for_validated_ai_before_rendering(self):
        class FakeReviewer:
            def review(self, context):
                return validated_response_from_payload(context, {
                    "action": "TRIM",
                    "confidence": 88,
                    "summary": "하락 신호가 커져 분할축소 검토가 우선입니다.",
                    "opinion": "추가매수는 보류하고 분할축소 기준을 확인하세요.",
                    "evidence": ["수익률 -12%", "20일선 아래"],
                    "counterEvidence": ["거래량은 평균 이하라 투매 확정은 아님"],
                    "invalidationCondition": "20일선 회복 시 축소 강도를 낮춥니다.",
                    "nextChecks": ["다음 조회에서도 같은 규칙이 유지되는지 확인"],
                    "referenceDate": "2026-07-08 14:30 KST",
                }, source="fake AI")

        job = NotificationJob.create(
            "old rendered message",
            account_id="main",
            account_label="메인",
            message_type="investmentInsight",
            context={
                "messageType": "investmentInsight",
                "headline": "[주의] 🛡️ 손실 -12%: 손절·분할축소 점검",
                "displayTarget": "삼성전자 / 005930",
                "referenceDate": "2026-07-08 14:30 KST",
                "sentTime": "2026-07-08 14:31 KST",
                "rawLines": "\n".join([
                    "현재가: 286,500원",
                    "평단가: 327,000원",
                    "수익률: -12.4%",
                    "추세: 20일선보다 12.6% 낮음",
                ]),
            },
        )
        enricher = NotificationAIValidatedGateEnricher(FakeReviewer(), {
            "notificationAiGateEnabled": "1",
            "notificationAiGateMessageTypes": "investmentInsight",
        })
        enricher(job)

        self.assertEqual("TRIM", job.context["notificationAiValidatedResponse"]["action"])
        self.assertIn("AI 투자 판단", job.context["telegramMessage"])
        self.assertIn("분할축소", job.context["telegramMessage"])
        self.assertNotIn("old rendered message", job.context["telegramMessage"])

    def test_notification_delivery_score_uses_user_formula(self):
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "monitorTrendChange",
            "main:trend:000660",
            "SK하이닉스",
            ["추세: 현재 2,360,000원, 20일선 2,490,000원(-5.4%)", "수급: 거래량 4,816,364(0.5x)"],
            "000660",
            metadata={"notificationScoreFormula": "baseScore + symbolScore"},
        )
        context = alert_context(event)
        job = NotificationJob.create(
            "사용자 공식 검증",
            account_id="main",
            account_label="메인",
            message_type=event.rule,
            context=context,
        )

        decision = evaluate_notification_rule(job, default_notification_rule(event.rule))

        self.assertEqual(45, decision.score)
        self.assertIn("사용자 발송 공식 적용 45점", decision.reasons)
        audit = decision.to_context()["notificationFormulaAudit"]
        self.assertEqual("notificationScoreFormula", audit["key"])
        self.assertEqual("baseScore + symbolScore", audit["expression"])
        self.assertEqual({"baseScore": 35.0, "symbolScore": 10.0}, audit["variables"])

    def test_notification_rule_seed_migrates_default_text_conditions_to_signals(self):
        db_path = Path(self.temp.name) / "service.db"
        store = SQLiteNotificationRuleStore(db_path)
        rule = store.get("holdingTiming")
        important = next(condition for condition in rule.conditions if condition.condition_id == "important_terms")
        important.condition_type = "text_contains_any"
        important.field = ""
        important.terms = ["손절"]
        important.score = 17
        store.upsert(rule)

        refreshed = SQLiteNotificationRuleStore(db_path)
        migrated = next(condition for condition in refreshed.get("holdingTiming").conditions if condition.condition_id == "important_terms")

        self.assertEqual("context_contains_any", migrated.condition_type)
        self.assertEqual("notificationSignals", migrated.field)
        self.assertEqual(["important"], migrated.terms)
        self.assertEqual(17, migrated.score)

    def test_market_hours_rule_suppresses_stock_alerts_after_close(self):
        rule = default_notification_rule("holdingTiming")
        job = NotificationJob.create(
            "보유 타이밍",
            account_id="main",
            account_label="메인",
            message_type="holdingTiming",
            context={"symbol": "005930", "title": "삼성전자"},
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_market_hours_rule(decision, rule, job, datetime(2026, 7, 3, 11, 30, tzinfo=timezone.utc))

        self.assertFalse(decision.should_send)
        self.assertEqual("market_closed", decision.suppression_reason)
        self.assertEqual("KR", decision.market_hours_market)
        self.assertEqual("closed", decision.market_hours_status)
        self.assertIn("국장", decision.market_hours_reason)

    def test_market_hours_rule_allows_stock_alerts_during_regular_session(self):
        rule = default_notification_rule("holdingTiming")
        job = NotificationJob.create(
            "보유 타이밍",
            account_id="main",
            account_label="메인",
            message_type="holdingTiming",
            context={"symbol": "AAPL", "title": "Apple"},
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_market_hours_rule(decision, rule, job, datetime(2026, 7, 2, 15, 0, tzinfo=timezone.utc))

        self.assertTrue(decision.should_send)
        self.assertEqual("US", decision.market_hours_market)
        self.assertEqual("open", decision.market_hours_status)
        self.assertIn("미장", decision.market_hours_reason)

    def test_market_hours_rule_allows_extended_sessions_by_default(self):
        rule = default_notification_rule("holdingTiming")
        kr_job = NotificationJob.create(
            "보유 타이밍",
            account_id="main",
            account_label="메인",
            message_type="holdingTiming",
            context={"symbol": "005930", "title": "삼성전자"},
        )
        us_job = NotificationJob.create(
            "보유 타이밍",
            account_id="main",
            account_label="메인",
            message_type="holdingTiming",
            context={"symbol": "AAPL", "title": "Apple"},
        )

        kr_decision = apply_market_hours_rule(
            evaluate_notification_rule(kr_job, rule),
            rule,
            kr_job,
            datetime(2026, 7, 2, 23, 30, tzinfo=timezone.utc),
        )
        us_decision = apply_market_hours_rule(
            evaluate_notification_rule(us_job, rule),
            rule,
            us_job,
            datetime(2026, 7, 2, 21, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(kr_decision.should_send)
        self.assertEqual("open", kr_decision.market_hours_status)
        self.assertIn("프리마켓", kr_decision.market_hours_reason)
        self.assertTrue(us_decision.should_send)
        self.assertEqual("open", us_decision.market_hours_status)
        self.assertIn("애프터마켓", us_decision.market_hours_reason)

    def test_notification_rule_seed_restores_default_market_hours_enabled(self):
        db_path = Path(self.temp.name) / "service.db"
        SQLiteNotificationRuleStore(db_path)
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE notification_rules SET market_hours_enabled = 0, market_hours_markets_json = ?, updated_at = ? WHERE message_type = ?",
                (json.dumps(["US"]), "2026-07-01T00:00:00Z", "externalEquityMove"),
            )

        refreshed = SQLiteNotificationRuleStore(db_path).get("externalEquityMove")

        self.assertTrue(refreshed.market_hours_enabled)
        self.assertEqual(["US"], refreshed.market_hours_markets)

    def test_notification_queue_suppresses_stale_data_freshness(self):
        queue = SQLiteNotificationJobStore(Path(self.temp.name) / "service.db")
        stale_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
        job = NotificationJob.create(
            "크립토 변동",
            account_id="main",
            account_label="메인",
            message_type="externalCryptoMove",
            context={
                "messageType": "externalCryptoMove",
                "severity": "ALERT",
                "symbol": "BTC",
                "body": "크립토 변동",
                "notificationSignals": ["important", "confirmingData", "actionable"],
                "dataFreshness": {
                    "source": "CoinGecko",
                    "status": "stale",
                    "reason": "기준 10분 초과",
                    "ageMinutes": 30,
                    "maxAgeMinutes": 10,
                    "sourceFetchedAt": stale_time,
                },
            },
        )

        self.assertFalse(queue.enqueue(job))
        saved = queue.jobs()[0]

        self.assertEqual("suppressed", saved.status)
        self.assertIn("데이터 신선도 기준 미통과", saved.last_error)
        self.assertEqual("stale_data", saved.context["honeySuppressionReason"])
        self.assertEqual("suppressed", saved.context["dataFreshnessDecision"])

    def test_notification_queue_suppresses_missing_required_data_freshness(self):
        queue = SQLiteNotificationJobStore(Path(self.temp.name) / "service.db")
        job = NotificationJob.create(
            "크립토 변동",
            account_id="main",
            account_label="메인",
            message_type="externalCryptoMove",
            context={
                "messageType": "externalCryptoMove",
                "severity": "ALERT",
                "symbol": "BTC",
                "body": "크립토 변동 7d +13.4%",
                "notificationSignals": ["important", "confirmingData", "actionable"],
            },
        )

        self.assertFalse(queue.enqueue(job))
        saved = queue.jobs()[0]

        self.assertEqual("suppressed", saved.status)
        self.assertEqual("missing", saved.context["dataFreshnessStatus"])
        self.assertEqual(["unknown"], saved.context["dataFreshnessStaleSources"])
        self.assertIn("신선도 메타데이터 없음", saved.last_error)

    def test_investment_insight_state_cooldown_suppresses_score_only_relation_noise(self):
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        rules = SQLiteNotificationRuleStore(db_path)
        rule = rules.get("investmentInsight")
        rule.market_hours_enabled = False
        rules.upsert(rule)

        def event_for_score(score, source_event_key):
            return AlertEvent(
                "main",
                "메인",
                "ALERT",
                "investmentInsight",
                "main:ontology-insight:005380:riskIncrease:watchlistOntologySignal",
                "현대차",
                ["인사이트 유형: 리스크 증가", "핵심 결론: 현대차 하락 가속 대응 점검"],
                "005380",
                metadata={
                    "ontologyInsight": {
                        "subject": "005380",
                        "insightType": "riskIncrease",
                        "score": score,
                        "noveltyScore": 25,
                        "sourceSignalTypes": ["watchlistOntologySignal"],
                        "sourceEventKeys": [source_event_key],
                        "cadenceKey": "cadence:python:main:investmentInsight:005380:riskIncrease:watchlistOntologySignal",
                    },
                    "sourceSignalTypes": ["watchlistOntologySignal"],
                    "dataFreshness": self.fresh_data_freshness("unit-test-position"),
                },
            )

        first_result = send_events([event_for_score(
            97.3,
            "main:watchlist-ontology:005380:riskWatch:data.conflict.v1+trend.breakdown_acceleration.v1:97.3",
        )], queue=queue)
        second_result = send_events([event_for_score(
            100.0,
            "main:watchlist-ontology:005380:riskWatch:data.conflict.v1+trend.breakdown_acceleration.v1",
        )], queue=queue)

        self.assertEqual(1, first_result.queued)
        self.assertEqual(0, second_result.queued)
        jobs = queue.jobs()
        self.assertEqual(["pending", "suppressed"], [job.status for job in jobs])
        self.assertEqual("cooldown", jobs[1].context["honeyStateDecision"])
        self.assertFalse(jobs[1].context["honeySimilarityBypassed"])
        self.assertIn("같은 임계값 상태 지속", jobs[1].last_error)

    def test_notification_rule_penalizes_similar_recent_messages(self):
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        rules = SQLiteNotificationRuleStore(db_path)
        rule = rules.get("monitorTrendChange")
        rule.threshold = 80
        rule.similarity_enabled = True
        rule.similarity_window_minutes = 120
        rule.similarity_penalty = -40
        rule.similarity_bypass_score_delta = 20
        rule.similarity_fields = ["messageType", "accountId", "symbol", "severity", "title"]
        rule.market_hours_enabled = False
        rules.upsert(rule)
        first = self.mark_event_fresh(AlertEvent("main", "메인", "ALERT", "monitorTrendChange", "main:trend:1", "SK하이닉스", ["이동평균 변화", "20일선 하향 이탈"], "000660"))
        second = self.mark_event_fresh(AlertEvent("main", "메인", "ALERT", "monitorTrendChange", "main:trend:2", "SK하이닉스", ["이동평균 변화", "20일선 하향 이탈", "변화 -0.1%"], "000660"))

        first_result = send_events([first], queue=queue)
        second_result = send_events([second], queue=queue)

        self.assertEqual(1, first_result.queued)
        self.assertEqual(0, second_result.queued)
        jobs = queue.jobs()
        self.assertEqual(["pending", "suppressed"], [job.status for job in jobs])
        self.assertEqual(jobs[0].context["honeyFingerprint"], jobs[1].context["honeyFingerprint"])
        self.assertEqual(1, jobs[1].context["honeySimilarityRecentCount"])
        self.assertEqual(-40, jobs[1].context["honeySimilarityPenalty"])
        self.assertLess(jobs[1].context["honeyScore"], jobs[1].context["honeyThreshold"])

    def test_external_move_rules_suppress_repeated_market_noise(self):
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        rules = SQLiteNotificationRuleStore(db_path)
        rule = rules.get("externalEquityMove")
        self.assertEqual(60, rule.threshold)
        self.assertTrue(rule.similarity_bypass_conditions)
        rule.market_hours_enabled = False
        rules.upsert(rule)
        first = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "externalEquityMove",
            "main:alpha:MSTR:1",
            "MSTR",
            ["미장 가격 변동 +7.9%", "가격 $100.77", "거래량 34,757,614", "출처 Alpha Vantage"],
            "MSTR",
        )
        second = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "externalEquityMove",
            "main:alpha:MSTR:2",
            "MSTR",
            ["미장 가격 변동 +7.9%", "가격 $100.77", "거래량 34,757,614", "출처 Alpha Vantage"],
            "MSTR",
        )
        first = self.mark_event_fresh(first, "Alpha Vantage")
        second = self.mark_event_fresh(second, "Alpha Vantage")

        self.assertEqual(1, send_events([first], queue=queue).queued)
        self.assertEqual(0, send_events([second], queue=queue).queued)

        jobs = queue.jobs()
        self.assertEqual(["pending", "suppressed"], [job.status for job in jobs])
        self.assertEqual("cooldown", jobs[1].context["honeyStateDecision"])
        self.assertTrue(jobs[1].context["honeyStateSuppressed"])
        self.assertEqual(360, jobs[1].context["honeySimilarityWindowMinutes"])
        self.assertIn("같은 임계값 상태 지속", jobs[1].last_error)

    def test_external_move_similarity_bypass_sends_material_change(self):
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        rules = SQLiteNotificationRuleStore(db_path)
        rule = rules.get("externalEquityMove")
        rule.market_hours_enabled = False
        rules.upsert(rule)
        first = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "externalEquityMove",
            "main:alpha:TSLA:1",
            "TSLA",
            ["미장 가격 변동 -7.5%", "가격 $393.45", "거래량 73,915,762", "출처 Alpha Vantage"],
            "TSLA",
            metadata={"market": "US", "changePercent": -7.5, "price": 393.45, "volume": 73915762},
        )
        second = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "externalEquityMove",
            "main:alpha:TSLA:2",
            "TSLA",
            ["미장 가격 변동 -10.2%", "가격 $382.10", "거래량 74,500,000", "출처 Alpha Vantage"],
            "TSLA",
            metadata={"market": "US", "changePercent": -10.2, "price": 382.10, "volume": 74500000},
        )
        first = self.mark_event_fresh(first, "Alpha Vantage")
        second = self.mark_event_fresh(second, "Alpha Vantage")

        self.assertEqual(1, send_events([first], queue=queue).queued)
        self.assertEqual(1, send_events([second], queue=queue).queued)

        jobs = queue.jobs()
        self.assertEqual(["pending", "pending"], [job.status for job in jobs])
        self.assertEqual(jobs[0].context["honeyFingerprint"], jobs[1].context["honeyFingerprint"])
        self.assertEqual(1, jobs[1].context["honeySimilarityRecentCount"])
        self.assertTrue(jobs[1].context["honeySimilarityBypassed"])
        self.assertIn("변동률 추가 확대", jobs[1].context["honeySimilarityBypassReason"])
        self.assertGreaterEqual(jobs[1].context["honeyScore"], jobs[1].context["honeyThreshold"])

    def test_crypto_state_cooldown_suppresses_same_threshold_state(self):
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        rules = SQLiteNotificationRuleStore(db_path)
        rule = rules.get("externalCryptoMove")
        self.assertEqual(60, rule.threshold)
        self.assertTrue(rule.state_cooldown_enabled)
        self.assertEqual(360, rule.state_cooldown_minutes)
        self.assertTrue(any(condition.condition_id == "change_7d_abs_delta" for condition in rule.similarity_bypass_conditions))
        first = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "externalCryptoMove",
            "main:crypto:ETH:1",
            "크립토 변동",
            ["크립토 변동 24h -0.7% · 7d +13.4%", "크립토 가격 $1,780", "크립토 거래액 $9,941,259,360", "출처 CoinGecko"],
            "ETH",
            metadata={"market": "CRYPTO", "change24h": -0.7, "change7d": 13.4, "volume24h": 9941259360},
        )
        second = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "externalCryptoMove",
            "main:crypto:ETH:2",
            "크립토 변동",
            ["크립토 변동 24h -0.7% · 7d +13.4%", "크립토 가격 $1,780", "크립토 거래액 $9,941,259,360", "출처 CoinGecko"],
            "ETH",
            metadata={"market": "CRYPTO", "change24h": -0.7, "change7d": 13.4, "volume24h": 9941259360},
        )
        first = self.mark_event_fresh(first, "CoinGecko")
        second = self.mark_event_fresh(second, "CoinGecko")

        self.assertEqual(1, send_events([first], queue=queue).queued)
        self.assertEqual(0, send_events([second], queue=queue).queued)

        jobs = queue.jobs()
        self.assertEqual(["pending", "suppressed"], [job.status for job in jobs])
        self.assertEqual("new_threshold", jobs[0].context["honeyStateDecision"])
        self.assertEqual("cooldown", jobs[1].context["honeyStateDecision"])
        self.assertTrue(jobs[1].context["honeyStateSuppressed"])
        self.assertIn("같은 임계값 상태 지속", jobs[1].last_error)

    def test_holding_timing_state_cooldown_suppresses_same_status(self):
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        rules = SQLiteNotificationRuleStore(db_path)
        rule = rules.get("holdingTiming")
        self.assertTrue(rule.state_cooldown_enabled)
        self.assertEqual(360, rule.state_cooldown_minutes)
        self.assertTrue(any(condition.condition_id == "holding_score_delta" for condition in rule.similarity_bypass_conditions))
        rule.market_hours_enabled = False
        rules.upsert(rule)
        first = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "holdingTiming",
            "main:timing:000660:1",
            "SK하이닉스",
            ["상태 손절 기준 확인 (63점)", "손익 -8.4%", "수급: 거래량 6,373,255(0.6x)", "추세: 현재 2,360,000원"],
            "000660",
            metadata={"holdingDecision": "손절 기준 확인", "holdingDecisionBasis": "lossCut", "holdingDecisionScore": 63, "profitLossRate": -8.4},
        )
        second = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "holdingTiming",
            "main:timing:000660:2",
            "SK하이닉스",
            ["상태 손절 기준 확인 (63점)", "손익 -8.4%", "수급: 거래량 6,373,255(0.6x)", "추세: 현재 2,360,000원"],
            "000660",
            metadata={"holdingDecision": "손절 기준 확인", "holdingDecisionBasis": "lossCut", "holdingDecisionScore": 63, "profitLossRate": -8.4},
        )
        first = self.mark_event_fresh(first, "unit-test-position")
        second = self.mark_event_fresh(second, "unit-test-position")

        self.assertEqual(1, send_events([first], queue=queue).queued)
        self.assertEqual(0, send_events([second], queue=queue).queued)

        jobs = queue.jobs()
        self.assertEqual(["pending", "suppressed"], [job.status for job in jobs])
        self.assertEqual("new_threshold", jobs[0].context["honeyStateDecision"])
        self.assertEqual("cooldown", jobs[1].context["honeyStateDecision"])
        self.assertTrue(jobs[1].context["honeyStateSuppressed"])
        self.assertIn("같은 임계값 상태 지속", jobs[1].last_error)

    def test_holding_timing_state_cooldown_allows_material_worsening(self):
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        rules = SQLiteNotificationRuleStore(db_path)
        rule = rules.get("holdingTiming")
        rule.market_hours_enabled = False
        rules.upsert(rule)
        first = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "holdingTiming",
            "main:timing:000660:1",
            "SK하이닉스",
            ["상태 손절 기준 확인 (63점)", "손익 -8.4%"],
            "000660",
            metadata={"holdingDecision": "손절 기준 확인", "holdingDecisionBasis": "lossCut", "holdingDecisionScore": 63, "profitLossRate": -8.4},
        )
        worsened = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "holdingTiming",
            "main:timing:000660:2",
            "SK하이닉스",
            ["상태 손절 기준 확인 (64점)", "손익 -10.7%"],
            "000660",
            metadata={"holdingDecision": "손절 기준 확인", "holdingDecisionBasis": "lossCut", "holdingDecisionScore": 64, "profitLossRate": -10.7},
        )
        first = self.mark_event_fresh(first, "unit-test-position")
        worsened = self.mark_event_fresh(worsened, "unit-test-position")

        self.assertEqual(1, send_events([first], queue=queue).queued)
        self.assertEqual(1, send_events([worsened], queue=queue).queued)

        jobs = queue.jobs()
        self.assertEqual(["pending", "pending"], [job.status for job in jobs])
        self.assertEqual("material_change", jobs[1].context["honeyStateDecision"])
        self.assertTrue(jobs[1].context["honeySimilarityBypassed"])
        self.assertIn("손익률 추가 악화", jobs[1].context["honeyStateReason"])

    def test_crypto_state_cooldown_allows_material_7d_expansion(self):
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        first = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "externalCryptoMove",
            "main:crypto:ETH:1",
            "크립토 변동",
            ["크립토 변동 24h -0.7% · 7d +13.4%", "크립토 가격 $1,780", "크립토 거래액 $9,941,259,360", "출처 CoinGecko"],
            "ETH",
            metadata={"market": "CRYPTO", "change24h": -0.7, "change7d": 13.4, "volume24h": 9941259360},
        )
        expanded = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "externalCryptoMove",
            "main:crypto:ETH:3",
            "크립토 변동",
            ["크립토 변동 24h -0.8% · 7d +16.8%", "크립토 가격 $1,825", "크립토 거래액 $10,000,000,000", "출처 CoinGecko"],
            "ETH",
            metadata={"market": "CRYPTO", "change24h": -0.8, "change7d": 16.8, "volume24h": 10000000000},
        )
        first = self.mark_event_fresh(first, "CoinGecko")
        expanded = self.mark_event_fresh(expanded, "CoinGecko")

        self.assertEqual(1, send_events([first], queue=queue).queued)
        self.assertEqual(1, send_events([expanded], queue=queue).queued)

        jobs = queue.jobs()
        self.assertEqual(["pending", "pending"], [job.status for job in jobs])
        self.assertEqual("material_change", jobs[1].context["honeyStateDecision"])
        self.assertTrue(jobs[1].context["honeySimilarityBypassed"])
        self.assertIn("7일 변동 확대", jobs[1].context["honeyStateReason"])

    def test_crypto_state_cooldown_allows_sustained_summary_after_cooldown(self):
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        rules = SQLiteNotificationRuleStore(db_path)
        rule = rules.get("externalCryptoMove")
        rule.state_cooldown_minutes = 10
        rules.upsert(rule)
        old_job = NotificationJob.create(
            "크립토 변동 ETH",
            account_id="main",
            account_label="메인",
            message_type="externalCryptoMove",
            context={
                "messageType": "externalCryptoMove",
                "accountId": "main",
                "accountLabel": "메인",
                "severity": "ALERT",
                "title": "크립토 변동",
                "symbol": "ETH",
                "body": "크립토 변동 24h -0.7% · 7d +13.4%",
                "change24h": -0.7,
                "change7d": 13.4,
                "volume24h": 9941259360,
                "dataFreshness": self.fresh_data_freshness("CoinGecko"),
            },
        )
        old_job.created_at = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
        new_job = NotificationJob.create(
            "크립토 변동 ETH",
            account_id="main",
            account_label="메인",
            message_type="externalCryptoMove",
            context={
                "messageType": "externalCryptoMove",
                "accountId": "main",
                "accountLabel": "메인",
                "severity": "ALERT",
                "title": "크립토 변동",
                "symbol": "ETH",
                "body": "크립토 변동 24h -0.7% · 7d +13.4%",
                "change24h": -0.7,
                "change7d": 13.4,
                "volume24h": 9941259360,
                "dataFreshness": self.fresh_data_freshness("CoinGecko"),
            },
        )

        self.assertTrue(queue.enqueue(old_job))
        self.assertTrue(queue.enqueue(new_job))

        jobs = queue.jobs()
        self.assertEqual(["pending", "pending"], [job.status for job in jobs])
        self.assertEqual("sustained_summary", jobs[1].context["honeyStateDecision"])
        self.assertIn("지속 상태 요약", jobs[1].context["honeyStateReason"])

    def test_notification_rule_payload_saves_similarity_bypass_conditions(self):
        payload = list_notification_rules_payload()
        equity_rule = next(item for item in payload["rules"] if item["messageType"] == "externalEquityMove")
        self.assertTrue(equity_rule["similarityBypassConditions"])
        self.assertTrue(equity_rule["stateCooldownEnabled"])
        change_condition = next(item for item in equity_rule["similarityBypassConditions"] if item["id"] == "change_abs_delta")
        change_condition["value"] = 3.5
        change_condition["enabled"] = False
        equity_rule["stateCooldownMinutes"] = 720

        saved = save_notification_rule_payload({"rule": equity_rule})["rule"]

        saved_condition = next(item for item in saved["similarityBypassConditions"] if item["id"] == "change_abs_delta")
        self.assertEqual("3.5", str(saved_condition["value"]))
        self.assertFalse(saved_condition["enabled"])
        self.assertEqual(720, saved["stateCooldownMinutes"])
        reloaded = next(item for item in list_notification_rules_payload()["rules"] if item["messageType"] == "externalEquityMove")
        reloaded_condition = next(item for item in reloaded["similarityBypassConditions"] if item["id"] == "change_abs_delta")
        self.assertEqual("3.5", str(reloaded_condition["value"]))
        self.assertFalse(reloaded_condition["enabled"])
        self.assertEqual(720, reloaded["stateCooldownMinutes"])

    def test_notification_queue_runner_delivers_pending_messages_in_order(self):
        registry = AccountRegistry()
        registry.upsert(AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"]))
        queue = SQLiteNotificationJobStore(Path(self.temp.name) / "service.db")
        queue.enqueue(NotificationJob.create("첫 번째", account_id="main", account_label="메인", message_type="notification"))
        queue.enqueue(NotificationJob.create("두 번째", account_id="main", account_label="메인", message_type="notification"))
        sent = []

        class FakeNotifier:
            def send(self, message):
                sent.append(message)
                return SimpleNamespace(delivered=True, reason="")

        runner = NotificationQueueRunner(
            queue,
            registry,
            lambda _account: FakeNotifier(),
            now_provider=lambda: datetime(2026, 7, 4, 3, 0, tzinfo=timezone.utc),
        )

        processed = runner.run_once(limit=10)

        self.assertEqual(2, processed)
        self.assertEqual(["첫 번째", "두 번째"], sent)
        self.assertEqual({"done": 2}, queue.summary())

    def test_notification_queue_runner_suppresses_account_quiet_hours(self):
        registry = AccountRegistry()
        registry.upsert(AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"]))
        queue = SQLiteNotificationJobStore(Path(self.temp.name) / "service.db")
        queue.enqueue(NotificationJob.create("밤 알림", account_id="main", account_label="메인", message_type="notification"))
        sent = []

        class FakeNotifier:
            def send(self, message):
                sent.append(message)
                return SimpleNamespace(delivered=True, reason="")

        runner = NotificationQueueRunner(
            queue,
            registry,
            lambda _account: FakeNotifier(),
            now_provider=lambda: datetime(2026, 7, 4, 14, 30, tzinfo=timezone.utc),
        )

        processed = runner.run_once(limit=10)

        self.assertEqual(1, processed)
        self.assertEqual([], sent)
        self.assertEqual({"suppressed": 1}, queue.summary())
        jobs = queue.jobs()
        self.assertTrue(jobs[0].context["quietHoursSuppressed"])
        self.assertIn("22:00-05:00", jobs[0].last_error)

    def test_notification_queue_runner_bypasses_quiet_hours_for_work_handoff(self):
        registry = AccountRegistry()
        registry.upsert(AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"]))
        queue = SQLiteNotificationJobStore(Path(self.temp.name) / "service.db")
        queue.enqueue(NotificationJob.create("작업 완료", account_id="main", account_label="메인", message_type="workHandoff"))
        sent = []

        class FakeNotifier:
            def send(self, message):
                sent.append(message)
                return SimpleNamespace(delivered=True, reason="")

        runner = NotificationQueueRunner(
            queue,
            registry,
            lambda _account: FakeNotifier(),
            now_provider=lambda: datetime(2026, 7, 4, 14, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(1, runner.run_once(limit=10))
        self.assertEqual(["작업 완료"], sent)
        self.assertEqual({"done": 1}, queue.summary())

    def test_notification_templates_render_pending_jobs_at_delivery_time(self):
        registry = AccountRegistry()
        registry.upsert(AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"]))
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        templates = SQLiteNotificationTemplateStore(db_path)
        rules = SQLiteNotificationRuleStore(db_path)
        heartbeat_rule = rules.get("monitorHeartbeat")
        heartbeat_rule.threshold = 0
        rules.upsert(heartbeat_rule)
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "monitorHeartbeat",
            "main:heartbeat",
            "상태 확인",
            ["정상"],
            "",
            generated_at="2026-07-03T06:58:00Z",
        )
        send_events([event], queue=queue)
        templates.upsert("monitorHeartbeat", "[{messageType}] {title}\n{rawLines}", "테스트 템플릿", True)
        sent = []

        class FakeNotifier:
            def send(self, message):
                sent.append(message)
                return SimpleNamespace(delivered=True, reason="")

        runner = NotificationQueueRunner(
            queue,
            registry,
            lambda _account: FakeNotifier(),
            template_renderer=templates.render_job,
            now_provider=lambda: datetime(2026, 7, 4, 3, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(1, runner.run_once(limit=10))
        self.assertTrue(sent[0].startswith("[monitorHeartbeat] 상태 확인\n정상\n기준일 2026-07-03 15:58 KST"))
        self.assertIn("알림 발송", sent[0])
        self.assertIn("발송 우선도", sent[0])
        self.assertIn("기본 우선도", sent[0])

    def test_dart_disclosure_notification_includes_ai_analysis_at_delivery_time(self):
        registry = AccountRegistry()
        registry.upsert(AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["005930"]))
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        templates = SQLiteNotificationTemplateStore(db_path)
        rules = SQLiteNotificationRuleStore(db_path)
        dart_rule = rules.get("externalDartDisclosure")
        dart_rule.threshold = 0
        dart_rule.market_hours_enabled = False
        rules.upsert(dart_rule)
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "externalDartDisclosure",
            "main:dart:005930:20260701000001",
            "삼성전자",
            [
                "신규 공시 감지",
                "단일판매·공급계약체결",
                "접수일 20260701",
                "최근 공시 2건",
                "출처 OpenDART",
            ],
            "005930",
            criteria=[
                "설정: OpenDART 접수번호가 직전 조회와 다를 때",
                "감지: 접수번호 20260701000001, 접수일 20260701",
            ],
            metadata={
                "provider": "OpenDART",
                "corpCode": "00126380",
                "corpName": "삼성전자",
                "reportName": "단일판매·공급계약체결",
                "receiptNo": "20260701000001",
                "receiptDate": "20260701",
                "dataFreshness": self.fresh_data_freshness("OpenDART", 120),
            },
            generated_at="2026-07-03T06:58:00Z",
        )
        queue.enqueue(NotificationJob.create(
            templates.render(event.rule, alert_context(event)),
            account_id="main",
            account_label="메인",
            message_type=event.rule,
            context=alert_context(event),
        ))
        sent = []
        analyzed = []

        class FakeDisclosureAnalyzer:
            def analyze(self, context):
                analyzed.append(context)
                return DisclosureAnalysisResult([
                    "의미: 계약 매출 가능성을 알리는 수주성 공시입니다.",
                    "영향: 규모가 크면 실적 가시성과 투자심리에 긍정적일 수 있습니다.",
                    "확인: 계약금액의 매출 대비 비중과 상대방을 확인하세요.",
                    "대응: 원문 확인 전 추격 비중은 제한하고 분할 대응하세요.",
                ], "테스트 AI")

        class FakeNotifier:
            def send(self, message):
                sent.append(message)
                return SimpleNamespace(delivered=True, reason="")

        runner = NotificationQueueRunner(
            queue,
            registry,
            lambda _account: FakeNotifier(),
            template_renderer=templates.render_job,
            context_enricher=DisclosureAnalysisNotificationEnricher(FakeDisclosureAnalyzer(), {
                "dartDisclosureAiAnalysisEnabled": "1",
            }),
            now_provider=lambda: datetime(2026, 7, 4, 3, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(1, runner.run_once(limit=10))
        self.assertEqual(1, len(analyzed))
        self.assertIn("<b>AI 공시 해석</b>", sent[0])
        self.assertIn("<b>의미</b>: 계약 매출 가능성을 알리는 수주성 공시입니다.", sent[0])
        self.assertIn("<b>영향</b>: 규모가 크면 실적 가시성과 투자심리에 긍정적일 수 있습니다.", sent[0])
        self.assertIn("<b>대응</b>: 원문 확인 전 추격 비중은 제한하고 분할 대응하세요.", sent[0])
        self.assertIn("<b>분석출처</b>: 테스트 AI", sent[0])
        self.assertLess(sent[0].index("<b>AI 공시 해석</b>"), sent[0].index("<b>발송 기준</b>"))
        saved_job = queue.jobs()[0]
        self.assertEqual("done", saved_job.status)
        self.assertEqual("테스트 AI", saved_job.context["disclosureAnalysisSource"])

    def test_local_disclosure_analysis_classifies_financing_dilution(self):
        result = local_disclosure_analysis({
            "metadata": {
                "corpName": "테스트",
                "symbol": "123456",
                "reportName": "주요사항보고서(유상증자결정)",
                "receiptNo": "20260701000002",
            },
            "rawLines": "신규 공시 감지\n주요사항보고서(유상증자결정)\n출처 OpenDART",
        })

        self.assertTrue(any("희석" in line for line in result.lines))
        self.assertTrue(any("발행 규모" in line for line in result.lines))

    def test_local_disclosure_analysis_classifies_self_stock_disposal(self):
        result = local_disclosure_analysis({
            "metadata": {
                "corpName": "삼성전자",
                "symbol": "005930",
                "reportName": "주요사항보고서(자기주식처분결정)",
                "receiptNo": "20260707000403",
            },
            "rawLines": "신규 공시 감지\n주요사항보고서(자기주식처분결정)\n출처 OpenDART",
        })
        text = "\n".join(result.lines)

        self.assertIn("보유 자기주식을 처분", text)
        self.assertIn("물량 부담", text)
        self.assertIn("처분 수량", text)
        self.assertIn("추가매수는 보류", text)

    def test_holding_timing_delivery_adds_sent_time(self):
        registry = AccountRegistry()
        registry.upsert(AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"]))
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        templates = SQLiteNotificationTemplateStore(db_path)
        rules = SQLiteNotificationRuleStore(db_path)
        timing_rule = rules.get("holdingTiming")
        timing_rule.threshold = 0
        timing_rule.market_hours_enabled = False
        rules.upsert(timing_rule)
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "holdingTiming",
            "main:timing:005930",
            "삼성전자",
            ["상태 조건부 보유 (52점)", "손익 -3.2%"],
            "005930",
            metadata={"dataFreshness": self.fresh_data_freshness("unit-test-position")},
            generated_at="2026-07-05T00:00:00Z",
        )
        context = alert_context(event)
        queue.enqueue(NotificationJob.create(
            templates.render(event.rule, context),
            account_id="main",
            account_label="메인",
            message_type=event.rule,
            context=context,
        ))
        sent = []

        class FakeNotifier:
            def send(self, message):
                sent.append(message)
                return SimpleNamespace(delivered=True, reason="")

        runner = NotificationQueueRunner(
            queue,
            registry,
            lambda _account: FakeNotifier(),
            template_renderer=templates.render_job,
            now_provider=lambda: datetime(2026, 7, 5, 0, 6, tzinfo=timezone.utc),
        )

        self.assertEqual(1, runner.run_once(limit=10))
        self.assertIn("<b>알림 정보</b>", sent[0])
        self.assertIn("• <b>발송</b>: <code>2026-07-05 09:06 KST</code>", sent[0])
        self.assertLess(sent[0].index("<b>발송 기준</b>"), sent[0].index("<b>알림 정보</b>"))
        self.assertEqual("2026-07-05 09:06 KST", queue.jobs()[0].context["sentTime"])

    def test_notification_score_explanation_uses_friendly_korean(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "holdingTiming",
            "main:timing:000660",
            "SK하이닉스",
            ["상태 조건부 보유 (52점)", "손익 -3.2%", "수급: 거래량 31,000(1.7x), 거래액 48억 원"],
            "000660",
        )
        context = alert_context(event)
        context.update({
            "honeyScore": 70,
            "honeyThreshold": 45,
            "honeyReasons": ["기본 35점", "관찰 등급 +10", "확인 데이터 포함 +10", "행동 필요 표현 +10", "본문 있음 +5"],
            "holdingDecisionBasis": "lossCut",
            "holdingDecisionScore": 52,
            "profitLossRate": -3.2,
            "notificationScoreFormula": "rawScore",
        })

        message = templates.render(event.rule, context)

        self.assertIn("<b>모델 판단</b>", message)
        self.assertIn("<b>알림 발송</b>", message)
        self.assertIn("모델", message)
        self.assertIn("보유 타이밍 모델", message)
        self.assertIn("손실 관리 공식(lossCutScoreFormula)", message)
        self.assertIn("알림 발송 공식(notificationScoreFormula)", message)
        self.assertIn("발송 우선도", message)
        self.assertIn("기본 우선도 35점", message)
        self.assertIn("수급·추세 같은 확인 데이터 포함 +10점", message)
        self.assertIn("보유 모델 점수", message)
        self.assertIn("사용자가 설정한 익절 공식과 손절/손실 관리 공식", message)
        self.assertIn("발송 공식", message)
        self.assertIn("알림 발송 공식(notificationScoreFormula)", message)
        self.assertIn("발송 대입값", message)
        self.assertIn("rawScore=70", message)
        self.assertIn("발송 부족 데이터", message)
        self.assertNotIn("점수 계산", message)
        self.assertNotIn("발송 점수", message)
        self.assertNotIn("보유 판단 점수", message)
        self.assertNotIn("honey", message.lower())
        self.assertNotIn("danger", message.lower())
        self.assertNotIn("caution", message.lower())

    def test_formula_audit_details_render_for_holding_messages(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "holdingTiming",
            "main:timing:000660",
            "SK하이닉스",
            ["상태 손실 관리 조건부 보유 (40점)", "손익 -3.2%"],
            "000660",
        )
        context = alert_context(event)
        context.update({
            "honeyScore": 55,
            "honeyThreshold": 45,
            "honeyReasons": ["기본 35점", "관찰 등급 +10"],
            "formulaAudits": [{
                "key": "lossCutScoreFormula",
                "label": "손실 관리 공식",
                "expression": "baseScore + lossCutPnlScore + sellableScore",
                "result": 40,
                "selected": True,
                "variables": {"baseScore": 24, "lossCutPnlScore": 10, "sellableScore": 0},
                "missing": ["매도 가능 수량 없음 -> 0점"],
            }],
            "notificationFormulaAudit": {
                "key": "notificationScoreFormula",
                "label": "알림 발송 공식",
                "expression": "rawScore",
                "result": 55,
                "variables": {"rawScore": 55},
                "missing": [],
            },
        })

        message = templates.render(event.rule, context)

        self.assertIn("손실 관리 공식(lossCutScoreFormula)", message)
        self.assertIn("baseScore + lossCutPnlScore + sellableScore", message)
        self.assertIn("baseScore=24", message)
        self.assertIn("lossCutPnlScore=10", message)
        self.assertIn("매도 가능 수량 없음", message)
        self.assertIn("모델 공식", message)
        self.assertIn("모델 대입값", message)
        self.assertIn("모델 부족 데이터", message)
        self.assertIn("발송 공식", message)

    def test_holding_formula_audit_skips_domestic_signal_inputs_for_us_positions(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        position = Position(
            symbol="MSTR",
            name="스트래티지",
            market="US",
            currency="USD",
            market_value=1000,
            profit_loss_rate=18.5,
            sellable_quantity=1,
            current_price=105.36,
            volume=90863,
            trading_value=3543834187,
            ma20=108.07,
            ma60=144.62,
            ma20_distance=-2.5,
            ma60_distance=-27.1,
            sector="BTC",
        )
        audits = StrategyModel({}).holding_formula_audits(position, 50)
        event = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "holdingTiming",
            "main:timing:MSTR",
            "스트래티지",
            [
                "상태: 분할 매도 기준 확인 (80점)",
                "손익: +18.5%",
                "수급: 거래량 90,863(0x), 거래액 $3,543,834,187",
                "추세: 현재 $105.36, 20일선 $108.07(-2.5%), 60일선 $144.62(-27.1%)",
            ],
            "MSTR",
        )
        context = alert_context(event)
        context.update({
            "honeyScore": 100,
            "honeyThreshold": 45,
            "honeyReasons": ["기본 35점", "주의 등급 +25"],
            "formulaAudits": audits,
            "holdingDecisionBasis": "profitTake",
        })

        message = templates.render(event.rule, context)

        self.assertIn("거래량 배율 없음", message)
        self.assertNotIn("체결강도 없음", message)
        self.assertNotIn("매수/매도 체결량 없음", message)
        self.assertNotIn("투자자별 수급 없음", message)

    def test_holding_formula_audit_reports_domestic_signal_inputs_for_kr_positions(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=-9.5,
            sellable_quantity=10,
            current_price=90000,
            volume=230091,
            trading_value=2944270000000,
            ma20=100000,
            ma60=110000,
            ma20_distance=-10,
            ma60_distance=-18.2,
            sector="반도체",
        )
        audits = StrategyModel({}).holding_formula_audits(position, 50)
        event = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "holdingTiming",
            "main:timing:000660",
            "SK하이닉스",
            [
                "상태: 손실 관리 기준 확인 (80점)",
                "손익: -9.5%",
                "수급: 거래량 230,091(0x), 거래액 294427억 원",
                "추세: 현재 90,000원, 20일선 100,000원(-10.0%), 60일선 110,000원(-18.2%)",
            ],
            "000660",
        )
        context = alert_context(event)
        context.update({
            "honeyScore": 100,
            "honeyThreshold": 45,
            "honeyReasons": ["기본 35점", "주의 등급 +25"],
            "formulaAudits": audits,
            "holdingDecisionBasis": "lossCut",
        })

        message = templates.render(event.rule, context)

        self.assertIn("체결강도 없음", message)
        self.assertIn("매수/매도 체결량 없음", message)
        self.assertIn("투자자별 수급 없음", message)

    def test_holding_formula_audit_uses_domestic_execution_proxies(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            market_value=1000000,
            profit_loss_rate=-8.1,
            sellable_quantity=10,
            current_price=290000,
            volume=11223383,
            volume_ratio=0.5,
            trading_value=3366700000000,
            ma20=330000,
            ma60=290000,
            ma20_distance=-10.4,
            ma60_distance=2.9,
            trade_strength=72.8,
            orderbook_bid_volume=520618,
            orderbook_ask_volume=102790,
            bid_ask_imbalance=67,
            sector="반도체",
        )
        audits = StrategyModel({}).holding_formula_audits(position, 50)
        event = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "holdingTiming",
            "main:timing:005930",
            "삼성전자",
            ["상태: 손실 기준 근접 관찰 (50점)", "손익: -8.1%"],
            "005930",
        )
        context = alert_context(event)
        context.update({
            "honeyScore": 100,
            "honeyThreshold": 45,
            "honeyReasons": ["기본 35점", "주의 등급 +25"],
            "formulaAudits": audits,
            "holdingDecisionBasis": "lossCut",
        })

        message = templates.render(event.rule, context)

        self.assertNotIn("체결강도 없음", message)
        self.assertNotIn("매수/매도 체결량 없음", message)
        self.assertIn("투자자별 수급 없음", message)

    def test_model_review_message_skips_delivery_score_explanation(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)

        message = templates.render("modelReview", {
            "messageType": "modelReview",
            "body": "🧠 판단 변화 후속 리뷰: AAPL\n\n이 메시지는 실시간 알림 이후 생성된 후속 분석입니다.\n\n- 판단 변화 원인: 판단 이름이 바뀜",
            "honeyScore": 85,
            "honeyThreshold": 20,
            "honeyReasons": ["기본 85점", "본문 있음 +5"],
        })

        self.assertIn("🧠 판단 변화 후속 리뷰: AAPL", message)
        self.assertIn("실시간 알림 이후 생성된 후속 분석", message)
        self.assertNotIn("알림 발송", message)
        self.assertNotIn("발송 우선도", message)
        self.assertNotIn("기본 우선도 85점", message)

    def test_default_notification_template_is_readable_and_skips_empty_fields(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "monitorTrendChange",
            "main:trend:AAPL",
            "Apple",
            ["이동평균 변화", "", "신호 20일선 상향 돌파"],
            "AAPL",
        )

        message = templates.render(event.rule, alert_context(event))

        self.assertIn("Apple", message)
        self.assertNotIn("━━━━━━━━", message)
        self.assertIn("<b>[관찰] 📈 Apple: 이동평균 상향 신호</b>", message)
        self.assertIn("<code>Apple / AAPL</code>", message)
        self.assertIn("<b>발송 기준</b>", message)
        self.assertIn("<b>데이터</b>", message)
        self.assertLess(message.index("<b>데이터</b>"), message.index("<b>발송 기준</b>"))
        self.assertIn("• <b>신호</b>: <code>20일선 상향 돌파</code>", message)
        self.assertIn("• <b>설정</b>: <code>이동평균 돌파, 크로스, 현재가와 이동평균 차이가 커질 때 보냅니다.</code>", message)
        self.assertIn("• <b>감지</b>: <code>20일선 상향 돌파</code>", message)
        self.assertNotIn("\n\n\n", message)

    def test_monitor_decision_title_uses_recommended_action(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "monitorDecisionChange",
            "main:decision:STRC",
            "Strategy Preferred",
            [
                "보유: 종목 판단 변화",
                "이전: 비트코인 민감도 점검 (64.5점)",
                "현재: 비트코인 민감도 축소 검토 (64.5점)",
                "권장 액션: 손실 축소 우선, 회복 조건 확인 전 비중 확대 보류",
            ],
            "STRC",
        )

        message = templates.render(event.rule, alert_context(event))

        self.assertIn("<b>[관찰] 🛡️ Strategy Preferred: 판단 변경: 손실 축소 우선</b>", message)
        self.assertIn("• <b>권장 액션</b>: <code>손실 축소 우선, 회복 조건 확인 전 비중 확대 보류</code>", message)

    def test_external_equity_alert_uses_colon_pairs_without_divider(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        event = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "externalEquityMove",
            "main:alpha:TSLA:-7.5",
            "TSLA",
            [
                "미장 가격 변동 -7.5%",
                "현재가: $393.45",
                "거래량 71,917,610",
                "기준일 2026-07-02",
                "출처 Alpha Vantage",
            ],
            "TSLA",
        )

        message = templates.render(event.rule, alert_context(event))

        self.assertIn("<b>[주의] 🇺🇸 Tesla: 미장 가격 급락</b>\n<code>Tesla / TSLA</code>", message)
        self.assertNotIn("<code>TSLA</code>", message)
        self.assertNotIn("━━━━━━━━", message)
        self.assertIn("• <b>현재가</b>: <code>$393.45</code>", message)
        self.assertIn("• <b>미장 가격 변동</b>: <code>-7.5%</code>", message)
        self.assertIn("• <b>거래량</b>: <code>71,917,610</code>", message)
        self.assertIn("<b>알림 정보</b>", message)
        self.assertIn("• <b>기준</b>: <code>2026-07-02</code>", message)
        self.assertIn("• <b>출처</b>: <code>Alpha Vantage</code>", message)
        self.assertNotIn("<b>기준일</b>", message)
        self.assertLess(message.index("<b>데이터</b>"), message.index("<b>발송 기준</b>"))
        self.assertIn("• <b>감지</b>: <code>가격 변동 -7.5%, 현재가 $393.45</code>", message)

    def test_holding_timing_alert_title_uses_detected_decision(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        event = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "holdingTiming",
            "main:timing:MSTR",
            "스트래티지",
            [
                "상태: 분할 매도 기준 확인 (80점)",
                "손익: +18.5%",
                "수급: 거래량 90,863(0x), 거래액 $3,543,834,187",
            ],
            "MSTR",
        )

        message = templates.render(event.rule, alert_context(event))

        self.assertIn("<b>[주의] 💰 스트래티지: 수익 +18.5%: 분할매도 권장</b>", message)
        self.assertNotIn("<b>[주의] 보유 타이밍</b>", message)

        loss_event = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "holdingTiming",
            "main:timing:000660",
            "SK하이닉스",
            ["상태: 손절·분할축소 권장 (88점)", "손익: -13.4%"],
            "000660",
        )
        loss_message = templates.render(loss_event.rule, alert_context(loss_event))
        self.assertIn("<b>[주의] 🛡️ SK하이닉스: 손실 -13.4%: 손절·분할축소 권장</b>", loss_message)
        self.assertNotIn("분할매도 권장</b>", loss_message)

    def test_external_crypto_alert_orders_bitcoin_price_and_trading_value(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        event = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "externalCryptoMove",
            "main:crypto:BTC:-5.2",
            "크립토 변동",
            [
                "비트코인 변동 24h -5.2% · 7d -12.1%",
                "크립토 가격 $108,000",
                "크립토 거래액 $42,000,000,000",
                "출처 CoinGecko",
            ],
            "BTC",
            criteria=[
                "설정: 크립토 24h ±4% 또는 7d ±10% 이상",
                "감지: 비트코인 24h -5.2%, 7d -12.1%",
            ],
            generated_at="2026-07-03T06:58:00Z",
        )

        message = templates.render(event.rule, alert_context(event))

        change_line = "• <b>비트코인 변동</b>: <code>24h -5.2% · 7d -12.1%</code>"
        price_line = "• <b>크립토 가격</b>: <code>$108,000</code>"
        value_line = "• <b>크립토 거래액</b>: <code>$42,000,000,000</code>"
        self.assertIn(change_line + "\n" + price_line + "\n" + value_line, message)
        self.assertLess(message.index(price_line), message.index(value_line))
        self.assertIn("<b>알림 정보</b>", message)
        self.assertIn("• <b>기준</b>: <code>2026-07-03 15:58 KST</code>", message)
        self.assertIn("• <b>감지</b>: <code>비트코인 24h -5.2%, 7d -12.1%</code>", message)

    def test_external_crypto_alert_title_uses_dominant_change_direction(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        event = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "externalCryptoMove",
            "main:crypto:ETH:+11.8",
            "크립토 변동",
            [
                "크립토 변동 24h -0.1% · 7d +11.8%",
                "크립토 가격 $1,765",
                "크립토 거래액 $9,319,846,169",
                "출처 CoinGecko",
            ],
            "ETH",
            criteria=[
                "설정: 크립토 24h ±4% 또는 7d ±10% 이상",
                "감지: ETH 24h -0.1%, 7d +11.8%",
            ],
            generated_at="2026-07-06T08:45:00Z",
            metadata={"market": "CRYPTO", "change24h": -0.1, "change7d": 11.8},
        )

        message = templates.render(event.rule, alert_context(event))

        self.assertIn("<b>[주의] 🪙 이더리움: 크립토 가격 급등</b>", message)
        self.assertNotIn("<b>[주의] 크립토 가격 급락</b>", message)
        self.assertIn("• <b>크립토 변동</b>: <code>24h -0.1% · 7d +11.8%</code>", message)

    def test_alert_context_adds_reference_date_when_missing(self):
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "externalCryptoMove",
            "main:crypto:ETH:11.7",
            "크립토 변동",
            ["크립토 변동 24h +3.6% · 7d +11.7%", "크립토 가격 $1,758", "크립토 거래액 $10,240,123,215", "출처 CoinGecko"],
            "ETH",
            generated_at="2026-07-03T06:58:00Z",
        )

        context = alert_context(event)

        self.assertIn("기준일 2026-07-03 15:58 KST", context["rawLines"])
        self.assertEqual("2026-07-03 15:58 KST", context["referenceDate"])
        self.assertNotIn("<b>기준일</b>", context["telegramDataLines"])

    def test_model_score_alert_uses_phrase_with_score_in_parentheses(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "modelBuy",
            "main:model-buy:005930",
            "삼성전자",
            ["매수 판단 매수 후보 (78점)", "현재 71,000원"],
            "005930",
        )

        message = templates.render(event.rule, alert_context(event))

        self.assertIn("• <b>매수 판단</b>: <code>매수 후보 (78점)</code>", message)
        self.assertNotIn("모델 매수 점수 78점", message)
        self.assertIn("• <b>감지</b>: <code>매수 후보 (78점)</code>", message)
        self.assertIn("매수 모델 점수", message)
        self.assertIn("체결 흐름", message)

    def test_model_score_event_renders_formula_audit_details(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "marketValue": 1000000,
            "averagePrice": 74000,
            "currentPrice": 71000,
            "profitLossRate": -4.1,
            "tradeStrength": 130,
            "volumeRatio": 2.1,
            "buyVolume": 700,
            "sellVolume": 300,
            "priceChangeRate": 2.4,
            "ma20": 69000,
            "ma60": 65000,
            "sector": "반도체",
        })
        portfolio = portfolio_summary([position])
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio,
            [position],
            decisions_for_positions([position], portfolio),
        )
        monitor = RealtimeMonitor({"alertThresholds": "modelBuyScore=1\nmodelSellScore=99\nwatchlistBuyScore=99"})
        event = next(item for item in monitor.model_score_events(snapshot) if item.rule == "modelBuy")

        message = templates.render(event.rule, alert_context(event))

        self.assertTrue(event.metadata.get("formulaAudits"))
        self.assertIn("• <b>현재가</b>: <code>71,000원</code>", message)
        self.assertIn("• <b>평균매입가</b>: <code>74,000원</code>", message)
        self.assertIn("• <b>수익률</b>: <code>-4.1%</code>", message)
        self.assertIn("매수 공식(buyScoreFormula)", message)
        self.assertIn("매도 공식(sellScoreFormula)", message)
        self.assertIn("모델 대입값", message)
        self.assertIn("executionScore", message)
        self.assertIn("모델 부족 데이터", message)

    def test_model_sell_alert_explains_sell_score_inputs(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        event = AlertEvent(
            "main",
            "메인",
            "ALERT",
            "modelSell",
            "main:model-sell:005930",
            "삼성전자",
            ["매도 판단 분할매도 점검 (82점)", "손익률 -9.2%", "현재 71,000원"],
            "005930",
        )

        message = templates.render(event.rule, alert_context(event))

        self.assertIn("• <b>매도 판단</b>: <code>분할매도 점검 (82점)</code>", message)
        self.assertIn("매도 모델 점수", message)
        self.assertIn("손절 기준", message)

    def test_flow_and_trend_lines_use_colon_pair_template_format(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "monitorTrendChange",
            "main:trend:005930",
            "삼성전자",
            [
                "추세: 현재 106,000원, 20일선 104,000원(+1.9%), 60일선 103,000원(+2.9%)",
                "수급: 거래량 40,000(2.1x), 거래액 24억 원",
                "투자자: 외국인 +70,000(매수 510,000/매도 440,000), 기관 +35,000(매수 350,000/매도 315,000)",
            ],
            "005930",
        )

        message = templates.render(event.rule, alert_context(event))

        flow_line = "• <b>수급</b>: <code>거래량 40,000(2.1x), 거래액 24억 원</code>"
        trend_line = "• <b>추세</b>: <code>현재 106,000원, 20일선 104,000원(+1.9%), 60일선 103,000원(+2.9%)</code>"
        investor_line = "• <b>투자자</b>: <code>외국인 +70,000(매수 510,000/매도 440,000), 기관 +35,000(매수 350,000/매도 315,000)</code>"
        self.assertIn(flow_line + "\n" + trend_line + "\n" + investor_line, message)
        self.assertLess(message.index(flow_line), message.index(trend_line))
        self.assertLess(message.index(trend_line), message.index(investor_line))
        self.assertIn("• <b>설정</b>: <code>이동평균 돌파, 크로스, 현재가와 이동평균 차이가 커질 때 보냅니다.</code>", message)

    def test_status_profit_flow_and_trend_are_separate_ordered_rows(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "holdingTiming",
            "main:timing:000660",
            "SK하이닉스",
            [
                "추세: 현재 150,000원, 20일선 144,000원(+4.2%)",
                "손익 -3.2%",
                "수급: 거래량 31,000(1.7x), 거래액 48억 원",
                "상태 조건부 보유 (52점)",
            ],
            "000660",
            criteria=[
                "설정: 판단 상태가 위험/주의이거나 손익률이 -8% 이하일 때",
                "감지: 상태 조건부 보유 (52점), 손익 -3.2%",
            ],
        )

        message = templates.render(event.rule, alert_context(event))

        status_line = "• <b>상태</b>: <code>조건부 보유 (52점)</code>"
        profit_line = "• <b>손익</b>: <code>-3.2%</code>"
        flow_line = "• <b>수급</b>: <code>거래량 31,000(1.7x), 거래액 48억 원</code>"
        trend_line = "• <b>추세</b>: <code>현재 150,000원, 20일선 144,000원(+4.2%)</code>"
        self.assertIn(status_line + "\n" + profit_line + "\n" + flow_line + "\n" + trend_line, message)
        self.assertLess(message.index(status_line), message.index(profit_line))
        self.assertLess(message.index(flow_line), message.index(trend_line))
        self.assertIn("• <b>설정</b>: <code>판단 상태가 위험/주의이거나 손익률이 -8% 이하일 때</code>", message)
        self.assertIn("• <b>감지</b>: <code>상태 조건부 보유 (52점), 손익 -3.2%</code>", message)

    def test_notification_template_seed_migrates_previous_readable_default(self):
        db_path = Path(self.temp.name) / "service.db"
        templates = SQLiteNotificationTemplateStore(db_path)
        templates.upsert("monitorHeartbeat", "{readableMessage}", "이전 기본 템플릿", True)

        refreshed = SQLiteNotificationTemplateStore(db_path)

        self.assertEqual("{telegramMessage}", refreshed.get("monitorHeartbeat").template)

    def test_telegram_notifier_uses_html_parse_mode_for_rich_messages(self):
        sent_payloads = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"ok": true}'

        def fake_urlopen(request, timeout=0):
            sent_payloads.append(json.loads(request.data.decode("utf-8")))
            return FakeResponse()

        notifier = TelegramNotifier("token", "chat")
        with mock.patch("digital_twin.infrastructure.notifications.urllib.request.urlopen", side_effect=fake_urlopen):
            result = notifier.send("<b>[관찰] 이동평균 변화</b>\n<code>AAPL</code>")

        self.assertTrue(result.delivered)
        self.assertEqual("HTML", sent_payloads[0]["parse_mode"])
        self.assertEqual("<b>[관찰] 이동평균 변화</b>\n<code>AAPL</code>", sent_payloads[0]["text"])

    def test_monitor_context_lines_skip_unavailable_market_data(self):
        position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "currentPrice": 180,
            "marketValue": 1000,
            "profitLossRate": -9,
            "sector": "AI/플랫폼",
        }).to_dict()
        monitor = RealtimeMonitor()

        self.assertEqual("", monitor.flow_context_line(position))
        self.assertEqual("", monitor.investor_context_line(position))
        self.assertNotIn("체결강도", "\n".join([
            monitor.flow_context_line(position),
            monitor.investor_context_line(position),
        ]))

        position.update({
            "volume": 5004985,
            "volumeRatio": 0.2097,
            "tradingValue": 1516201852750,
            "tradeStrength": 70.87,
            "orderbookBidVolume": 509290,
            "orderbookAskVolume": 76754,
            "bidAskImbalance": 73.806,
        })
        flow_line = monitor.flow_context_line(position)
        self.assertIn("체결강도 70.9", flow_line)
        self.assertIn("호가잔량 매수", flow_line)
        self.assertIn("호가불균형 +73.8%", flow_line)

        position.update({
            "market": "KR",
            "currency": "KRW",
            "foreignBuyVolume": 1300,
            "foreignSellVolume": 600,
            "foreignNetAmount": 210000000,
            "institutionBuyVolume": 900,
            "institutionSellVolume": 600,
            "institutionNetAmount": 90000000,
            "individualBuyVolume": 2000,
            "individualSellVolume": 2400,
            "individualNetAmount": -120000000,
        })
        investor_line = monitor.investor_context_line(position)
        self.assertIn("투자자:", investor_line)
        self.assertIn("외국인: 순매수 700주, 매수 1,300주, 매도 600주, 금액 +2억 원", investor_line)
        self.assertIn("기관: 순매수 300주, 매수 900주, 매도 600주, 금액 +9,000만 원", investor_line)
        self.assertIn("개인: 순매도 400주, 매수 2,000주, 매도 2,400주, 금액 -1억 원", investor_line)
        position.update({
            "current_price": 277500,
            "currentPrice": 277500,
            "foreignBuyVolume": 8922904,
            "foreignSellVolume": 11937997,
            "foreignNetVolume": -3015093,
            "foreignNetAmount": -870963,
            "institutionBuyVolume": 12816837,
            "institutionSellVolume": 11845806,
            "institutionNetVolume": 971031,
            "institutionNetAmount": 283642,
            "individualBuyVolume": 11457143,
            "individualSellVolume": 9425438,
            "individualNetVolume": 2031705,
            "individualNetAmount": 583729,
        })
        corrected_investor_line = monitor.investor_context_line(position)
        self.assertIn("외국인: 순매도 3,015,093주", corrected_investor_line)
        self.assertIn("금액 -8,710억 원", corrected_investor_line)
        self.assertIn("기관: 순매수 971,031주", corrected_investor_line)
        self.assertIn("금액 +2,836억 원", corrected_investor_line)
        self.assertIn("개인: 순매수 2,031,705주", corrected_investor_line)
        self.assertIn("금액 +5,837억 원", corrected_investor_line)
        position.update({
            "quantity": 12,
            "sellable_quantity": 9,
            "sellableQuantity": 9,
            "market_value": 3330000,
            "marketValue": 3330000,
            "currency": "KRW",
        })
        self.assertEqual(
            "보유: 수량 12주, 매도가능 9주, 평가금액 333만 원",
            monitor.holding_balance_line(position),
        )
        self.assertEqual(
            "권장 액션: 손절·분할축소 우선, 20일선 회복 전 추가매수 보류",
            monitor.holding_action_line("손절·분할축소 권장", -13.4),
        )

    def test_notification_schedules_use_real_monitor_sent_history(self):
        registry = AccountRegistry()
        registry.upsert(AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"]))
        store = SQLiteMonitorStore(Path(self.temp.name) / "service.db")
        event = AlertEvent("main", "메인", "WATCH", "monitorHeartbeat", "main:heartbeat", "상태 확인", ["정상"], "")
        store.mark_sent([event])

        payload = notification_schedules_payload()
        schedule = next(item for item in payload["schedules"] if item["messageType"] == "monitorHeartbeat")

        self.assertTrue(schedule["enabled"])
        self.assertEqual("💓", schedule["icon"])
        self.assertEqual(10, schedule["cadenceMinutes"])
        self.assertTrue(schedule["lastSentAt"])
        self.assertTrue(schedule["nextEligibleAt"])
        self.assertEqual("waiting", schedule["status"])
        self.assertEqual("전체", schedule["recentTargets"][0]["target"] or "전체")
        self.assertIn("실시간 모니터링", schedule["triggerSummary"])

    def test_notification_template_test_send_queues_live_snapshot_message(self):
        registry = AccountRegistry()
        account = AccountConfig(
            "main",
            "메인",
            "toss",
            "https://example.test",
            "id",
            "secret",
            "1",
            ["AAPL"],
            notify_provider="telegram",
            telegram_bot_token="token",
            telegram_chat_id="chat",
        )
        registry.upsert(account)
        position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "market": "US",
            "currency": "USD",
            "marketValue": 1000,
            "quantity": 2,
            "sellableQuantity": 2,
            "averagePrice": 100,
            "currentPrice": 125,
            "profitLossRate": 25,
            "sector": "AI/플랫폼",
        })
        portfolio = portfolio_summary([position], 300, "KRW")
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "토스 계좌 동기화",
            utc_now_iso(),
            portfolio,
            [position],
            decisions_for_positions([position], portfolio),
        )
        SQLiteNotificationTemplateStore().upsert("monitorHeartbeat", "[{messageType}] {title}\n{rawLines}", "상태 확인 템플릿", True)
        rules = SQLiteNotificationRuleStore()
        heartbeat_rule = rules.get("monitorHeartbeat")
        heartbeat_rule.threshold = 0
        rules.upsert(heartbeat_rule)

        with mock.patch("digital_twin.infrastructure.web_server.build_snapshot", return_value=snapshot):
            status, payload = notification_template_test_payload({"messageType": "monitorHeartbeat"})

        self.assertEqual(202, status)
        self.assertFalse(payload["delivered"])
        self.assertTrue(payload["queued"])
        self.assertEqual("monitorHeartbeat", payload["event"]["messageType"])
        jobs = SQLiteNotificationJobStore().pending(limit=10)
        self.assertEqual(1, len(jobs))
        self.assertEqual("pending", jobs[0].status)
        self.assertEqual("monitorHeartbeat", jobs[0].message_type)
        self.assertIn("상태 토스 계좌 동기화", jobs[0].context["rawLines"])
        self.assertEqual("notification.test_requested", jobs[0].source_event_name)
        self.assertTrue(jobs[0].source_event_id)
        counts = SQLiteEventLog().event_counts()
        self.assertEqual(1, counts["notification.test_requested"])
        self.assertEqual(1, counts["notification.job_queued"])

    def test_notification_template_test_send_rejects_demo_snapshot_by_default(self):
        registry = AccountRegistry()
        account = AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"])
        registry.upsert(account)
        portfolio = portfolio_summary([], 0, "KRW")
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "demo",
            "토스 credentials 미설정",
            utc_now_iso(),
            portfolio,
            [],
            [],
        )

        with mock.patch("digital_twin.infrastructure.web_server.build_snapshot", return_value=snapshot):
            status, payload = notification_template_test_payload({"messageType": "monitorHeartbeat"})

        self.assertEqual(409, status)
        self.assertFalse(payload["delivered"])
        self.assertIn("실제 토스 데이터를", payload["error"])

    def test_realtime_status_payload_includes_monitoring_and_queue_state(self):
        event_log = SQLiteEventLog()
        event_log.handle(monitoring_cycle_completed_event(["main"], 2, 1, False, True))
        queue = SQLiteNotificationJobStore()
        queue.enqueue(NotificationJob.create("queued", account_id="main", message_type="monitorHeartbeat"))

        payload = realtime_status_payload()

        self.assertEqual(1, payload["events"][MONITORING_CYCLE_COMPLETED])
        self.assertEqual(1, sum(payload["notificationJobs"].values()))
        self.assertTrue(any(event["name"] == MONITORING_CYCLE_COMPLETED for event in payload["latestEvents"]))
        self.assertEqual(MONITORING_CYCLE_COMPLETED, payload["monitoring"]["cycle"]["name"])
        self.assertEqual(1, payload["monitoring"]["cycle"]["payload"]["alertCount"])

    def test_admin_preview_config_is_static_and_sanitized(self):
        registry = AccountRegistry()
        registry.upsert(AccountConfig(
            "main",
            "메인",
            "toss",
            "https://example.test",
            "client-id-that-must-not-leak",
            "secret-that-must-not-leak",
            "account-seq-that-must-not-leak",
            ["AAPL", "005930"],
            notify_provider="telegram",
            telegram_bot_token="telegram-secret-that-must-not-leak",
            telegram_chat_id="chat-id-that-must-not-leak",
            notify_link_url="http://127.0.0.1:3000?tab=notifications",
        ))
        with mock.patch.dict(os.environ, {
            "TOSS_CLIENT_SECRET": "secret-that-must-not-leak",
            "TELEGRAM_BOT_TOKEN": "telegram-secret-that-must-not-leak",
        }, clear=False):
            payload = admin_preview_config()

        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertEqual("github-pages-readonly-preview", payload["mode"])
        self.assertTrue(payload["buildId"])
        self.assertTrue(any(page["id"] == "model-review" for page in payload["pages"]))
        self.assertIn("clientSecret", encoded)
        self.assertEqual(1, payload["localData"]["accountCount"])
        self.assertEqual(["AAPL", "005930"], payload["localData"]["accounts"][0]["watchlistSymbols"])
        self.assertTrue(payload["localData"]["accounts"][0]["clientSecret"])
        self.assertTrue(payload["localData"]["accounts"][0]["telegramChatId"])
        self.assertNotIn("secret-that-must-not-leak", encoded)
        self.assertNotIn("telegram-secret-that-must-not-leak", encoded)
        self.assertNotIn("client-id-that-must-not-leak", encoded)
        self.assertNotIn("account-seq-that-must-not-leak", encoded)
        self.assertNotIn("chat-id-that-must-not-leak", encoded)

    def test_admin_preview_writes_pages_assets(self):
        output_dir = Path(self.temp.name) / "admin"

        payload = write_admin_preview(output_dir)

        html = (output_dir / "index.html").read_text(encoding="utf-8")
        config = json.loads((output_dir / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["buildId"], config["buildId"])
        self.assertIn("Orbit Alpha Python Admin", html)
        self.assertIn("로컬 DB 빌드 스냅샷", html)
        self.assertIn("--ds-color-bg", html)
        self.assertIn("config.json?v=" + payload["buildId"], html)

    def test_runner_uses_provider_snapshot(self):
        account = AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"])

        with mock.patch("digital_twin.scheduler.build_snapshot") as build_snapshot, mock.patch("digital_twin.scheduler.send_events") as send_events:
            position = normalize_position({"symbol": "AAPL", "name": "Apple", "marketValue": 1000, "profitLossRate": 15, "sellableQuantity": 1})
            portfolio = portfolio_summary([position])
            build_snapshot.return_value = AccountSnapshot("main", "메인", "toss", "live", "ok", utc_now_iso(), portfolio, [position], decisions_for_positions([position], portfolio))
            send_events.return_value.delivered = True

            events = MonitorRunner([account]).run_once(dry_run=True, force=True)

        self.assertTrue(events)

    def test_application_runner_uses_injected_ports(self):
        account = AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"])
        sent = []

        def snapshot_builder(_account):
            position = normalize_position({"symbol": "AAPL", "name": "Apple", "marketValue": 1000, "profitLossRate": 15, "sellableQuantity": 1})
            portfolio = portfolio_summary([position])
            return AccountSnapshot("main", "메인", "toss", "live", "ok", utc_now_iso(), portfolio, [position], decisions_for_positions([position], portfolio))

        def sender(events, dry_run=False, accounts=None):
            sent.extend(events)
            return SimpleNamespace(delivered=True)

        event_bus = EventBus()
        events = ApplicationMonitorRunner(
            [account],
            store=MonitorStore(),
            monitor=RealtimeMonitor(),
            snapshot_builder=snapshot_builder,
            event_sender=sender,
            event_publisher=event_bus,
        ).run_once(dry_run=True, force=True)

        self.assertTrue(events)
        self.assertEqual(events, sent)
        self.assertEqual(
            [MONITORING_SNAPSHOT_COLLECTED, MONITORING_ALERTS_DETECTED, MONITORING_CYCLE_COMPLETED],
            [event.name for event in event_bus.published],
        )
        self.assertEqual(1, event_bus.published[-1].payload["snapshotCount"])
        self.assertEqual(len(events), event_bus.published[-1].payload["alertCount"])

    def test_event_bus_dispatches_named_and_wildcard_handlers(self):
        registry = AccountRegistry()
        named = []
        all_events = []
        event_bus = EventBus()
        event_bus.subscribe(ACCOUNT_SAVED, named.append)
        event_bus.subscribe_all(all_events.append)
        service = AccountApplicationService(registry, registry.settings, event_publisher=event_bus)

        service.save(AccountConfig("main", "메인", "toss", "https://example.test", "id1", "secret1", "1", ["AAPL"]))

        self.assertEqual(1, len(named))
        self.assertEqual(named, all_events)

    def test_account_application_service_logs_event_in_account_transaction(self):
        registry = AccountRegistry()
        service = AccountApplicationService(registry, registry.settings)

        service.save(AccountConfig("main", "메인", "toss", "https://example.test", "id1", "secret1", "1", ["AAPL"]))

        with sqlite3.connect(str(Path(self.temp.name) / "service.db")) as connection:
            rows = connection.execute(
                "SELECT name, aggregate_id, event_json FROM domain_events ORDER BY occurred_at"
            ).fetchall()
        self.assertEqual([(ACCOUNT_SAVED, "main")], [(row[0], row[1]) for row in rows])
        self.assertNotIn("secret1", rows[0][2])

    def test_event_bus_records_handler_errors_without_breaking_publish(self):
        event_bus = EventBus()

        def fail(_event):
            raise RuntimeError("handler failed")

        event_bus.subscribe_all(fail)
        registry = AccountRegistry()
        service = AccountApplicationService(registry, registry.settings, event_publisher=event_bus)

        service.save(AccountConfig("main", "메인", "toss", "https://example.test", "id1", "secret1", "1", ["AAPL"]))

        self.assertEqual(1, len(event_bus.published))
        self.assertEqual(1, len(event_bus.handler_errors))

    def test_application_runner_records_monitoring_cycle_transactionally(self):
        db_path = Path(self.temp.name) / "service.db"
        legacy_missing = Path(self.temp.name) / "missing.json"
        store = SQLiteMonitorStore(db_path, legacy_path=legacy_missing)
        cycle_recorder = SQLiteMonitoringCycleRecorder(db_path, monitor_store=store)
        rules = SQLiteNotificationRuleStore(db_path)
        rule = rules.get("monitorDecisionChange")
        rule.market_hours_enabled = False
        rules.upsert(rule)
        account = AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"])
        alert = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "monitorDecisionChange",
            "main:decision:AAPL",
            "Apple",
            ["판단 변화", "Codex 답변: 판단 변경"],
            "AAPL",
            metadata={"dataFreshness": self.fresh_data_freshness("unit-test-position")},
        )

        def snapshot_builder(_account):
            position = normalize_position({
                "symbol": "AAPL",
                "name": "Apple",
                "marketValue": 1000,
                "profitLossRate": 15,
                "sellableQuantity": 1,
            })
            portfolio = portfolio_summary([position])
            return AccountSnapshot(
                "main",
                "메인",
                "toss",
                "live",
                "ok",
                utc_now_iso(),
                portfolio,
                [position],
                decisions_for_positions([position], portfolio),
            )

        class FakeMonitor:
            def events_for_snapshot(self, _snapshot, _previous):
                return [alert]

            def apply_cadence(self, events, _store, force=False):
                return events

        def sender(_events, dry_run=False, accounts=None, source_event=None):
            raise AssertionError("cycle recorder should own monitor side effects")

        event_bus = EventBus()
        events = ApplicationMonitorRunner(
            [account],
            store=store,
            monitor=FakeMonitor(),
            snapshot_builder=snapshot_builder,
            event_sender=sender,
            event_publisher=event_bus,
            cycle_recorder=cycle_recorder,
        ).run_once(dry_run=False, force=True)

        self.assertEqual([alert], events)
        self.assertEqual([], event_bus.published)
        self.assertIn("main", store.previous)
        self.assertIn(alert.key, store.sent)
        self.assertIn(alert.cadence_key(), store.sent)
        with sqlite3.connect(str(db_path)) as connection:
            event_counts = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT name, COUNT(*) FROM domain_events GROUP BY name"
                ).fetchall()
            }
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM monitor_snapshots").fetchone()[0])
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM monitor_sent").fetchone()[0])
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM notification_jobs WHERE status = 'pending'").fetchone()[0])
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM model_review_jobs WHERE status = 'pending'").fetchone()[0])
        self.assertEqual(
            {
                MONITORING_SNAPSHOT_COLLECTED: 1,
                MONITORING_ALERTS_DETECTED: 1,
                MONITORING_CYCLE_COMPLETED: 1,
            },
            event_counts,
        )

    def test_json_event_log_writes_jsonl(self):
        event_bus = EventBus()
        event_log = JsonEventLog(Path(self.temp.name) / "domain-events.jsonl")
        event_bus.subscribe_all(event_log.handle)
        registry = AccountRegistry()
        service = AccountApplicationService(registry, registry.settings, event_publisher=event_bus)

        service.save(AccountConfig("main", "메인", "toss", "https://example.test", "id1", "secret1", "1", ["AAPL"]))

        payload = (Path(self.temp.name) / "domain-events.jsonl").read_text(encoding="utf-8")
        self.assertIn('"name": "account.saved"', payload)
        self.assertNotIn("secret1", payload)

    def test_sqlite_operational_store_persists_runtime_data(self):
        db_path = Path(self.temp.name) / "service.db"
        legacy_missing = Path(self.temp.name) / "missing.json"
        position = normalize_position({
            "symbol": "AAPL",
            "name": "Apple",
            "marketValue": 1000,
            "profitLossRate": 12,
            "sellableQuantity": 1,
        })
        portfolio = portfolio_summary([position])
        snapshot = AccountSnapshot(
            "main",
            "메인",
            "toss",
            "live",
            "ok",
            utc_now_iso(),
            portfolio,
            [position],
            decisions_for_positions([position], portfolio),
        )
        alert = AlertEvent("main", "메인", "WATCH", "monitorDecisionChange", "main:decision:AAPL", "Apple", ["판단 변화"], "AAPL")

        monitor_store = SQLiteMonitorStore(db_path, legacy_path=legacy_missing)
        monitor_store.save_snapshot(snapshot)
        monitor_store.mark_sent([alert])
        reopened = SQLiteMonitorStore(db_path, legacy_path=legacy_missing)

        self.assertIn("main", reopened.previous)
        self.assertIn(alert.key, reopened.sent)
        self.assertIn(alert.cadence_key(), reopened.sent)

        event_log = SQLiteEventLog(db_path, legacy_path=Path(self.temp.name) / "missing.jsonl")
        source_event = alerts_detected_event([alert])
        event_log.handle(source_event)
        replayed = event_log.events(name=MONITORING_ALERTS_DETECTED)
        self.assertEqual([source_event.event_id], [event.event_id for event in replayed])
        self.assertEqual({MONITORING_ALERTS_DETECTED: 1}, event_log.event_counts())
        job_store = SQLiteModelReviewJobStore(db_path, legacy_path=legacy_missing)
        self.assertEqual(1, job_store.enqueue_from_event(source_event))
        self.assertEqual(1, len(job_store.pending(limit=10)))
        notification_store = SQLiteNotificationJobStore(db_path)
        self.assertTrue(notification_store.enqueue(NotificationJob.create("queued", account_id="main", message_type="notification")))
        self.assertEqual(1, len(notification_store.pending(limit=10)))
        template_store = SQLiteNotificationTemplateStore(db_path)
        template_store.upsert("test", "테스트 {body}", "테스트", True)
        settings_store = SQLiteRuntimeSettingsStore(db_path, legacy_path=legacy_missing)
        settings_store.save({"watchlistSymbols": "AAPL,NVDA", "tossClientSecret": "secret"})
        app_store = SQLiteAppStore(db_path, legacy_path=legacy_missing)
        app_store.replace({"messages": [{"id": "msg-1", "content": "hello"}]})

        self.assertEqual("AAPL,NVDA", runtime_settings()["watchlistSymbols"])
        self.assertEqual("msg-1", app_store.load()["messages"][0]["id"])

        with sqlite3.connect(str(db_path)) as connection:
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM monitor_snapshots").fetchone()[0])
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM monitor_sent").fetchone()[0])
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM domain_events").fetchone()[0])
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM model_review_jobs").fetchone()[0])
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM notification_jobs").fetchone()[0])
            self.assertGreaterEqual(connection.execute("SELECT COUNT(*) FROM notification_templates").fetchone()[0], 1)
            self.assertGreaterEqual(connection.execute("SELECT COUNT(*) FROM notification_rules").fetchone()[0], 1)
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM runtime_settings").fetchone()[0])
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM app_store").fetchone()[0])


class AssignmentTests(unittest.TestCase):
    def test_parse_assignments_preserves_defaults(self):
        values = parse_assignments("a=2\nb:3\nbad", {"a": 1, "c": 4})
        self.assertEqual(values["a"], 2)
        self.assertEqual(values["b"], 3)
        self.assertEqual(values["c"], 4)


if __name__ == "__main__":
    unittest.main()
