import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.admin_preview import admin_preview_config, write_admin_preview
from digital_twin.application.account_service import AccountApplicationService
from digital_twin.application.flow_lens_service import flow_lens_snapshot
from digital_twin.application.model_review_service import ModelReviewRunner
from digital_twin.application.monitoring_service import MonitorRunner as ApplicationMonitorRunner
from digital_twin.application.notification_service import NotificationQueueRunner
from digital_twin.application.symbol_universe_service import SymbolUniverseService
from digital_twin.cli import build_handoff_message
from digital_twin.cli import preserve_existing_secrets
from digital_twin.cli import build_parser
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.analytics import SafeFormula, StrategyModel, decisions_for_positions, normalize_position, portfolio_summary, technical_indicators_from_candles
from digital_twin.domain.events import ACCOUNT_SAVED, MONITORING_ALERTS_DETECTED, MONITORING_CYCLE_COMPLETED, MONITORING_SNAPSHOT_COLLECTED, alerts_detected_event, monitoring_cycle_completed_event
from digital_twin.domain.monitoring import DEFAULT_ALERT_RULES, DEFAULT_CADENCE, RealtimeMonitor
from digital_twin.domain.model_review import ModelReviewJob, local_model_review
from digital_twin.domain.notification_templates import alert_context
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.parsing import parse_assignments
from digital_twin.domain.portfolio import AccountSnapshot, AlertEvent, utc_now_iso
from digital_twin.infrastructure.event_bus import EventBus, JsonEventLog
from digital_twin.infrastructure.external_signals import ExternalSignalProvider
from digital_twin.infrastructure.json_monitor_state import MonitorStore
from digital_twin.infrastructure.model_review_queue import ModelReviewEnqueuer, ModelReviewJobStore
from digital_twin.infrastructure.mock_market import mock_market_payload
from digital_twin.infrastructure.notifications import TelegramNotifier, send_events
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.sqlite_operational import SQLiteAppStore, SQLiteEventLog, SQLiteExternalSignalCache, SQLiteMarketQuoteCache, SQLiteModelReviewJobStore, SQLiteMonitorStore, SQLiteNotificationJobStore, SQLiteNotificationRuleStore, SQLiteNotificationTemplateStore, SQLiteRuntimeSettingsStore, SQLiteSymbolUniverseStore
from digital_twin.infrastructure.symbol_sources import parse_krx_kind_table, parse_nasdaq_listed
from digital_twin.infrastructure.sqlite_accounts import AccountRegistry
from digital_twin.infrastructure.toss_snapshots import TossProvider, account_cash_amount, normalize_price_items, select_account
from digital_twin.infrastructure.web_server import notification_schedules_payload, notification_template_test_payload, realtime_status_payload
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
        self.assertEqual("cached", tsla.data_quality)
        self.assertEqual("마지막 저장 시세", tsla.quote_status)

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
        )
        service.save(existing)

        updated = service.save_payload({"id": "main", "label": "메인 수정", "watchlistSymbols": "NVDA"})
        masked = service.list_masked()[0]

        self.assertEqual("secret1", updated.client_secret)
        self.assertEqual("token", updated.telegram_bot_token)
        self.assertEqual(["NVDA"], masked["watchlistSymbols"])
        self.assertTrue(masked["clientSecret"])
        self.assertEqual([ACCOUNT_SAVED, ACCOUNT_SAVED], [event.name for event in event_bus.published])
        self.assertFalse(event_bus.published[-1].payload["account"]["clientSecret"] == "secret1")

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
            "foreignBuyVolume": "420",
            "foreignSellVolume": "275",
            "institutionBuyVolume": "310",
            "institutionSellVolume": "228",
            "executionStrength": "128.4",
            "marketValue": 720000,
        })

        self.assertEqual(128.4, position.trade_strength)
        self.assertEqual(1000, position.volume)
        self.assertEqual(1.7, position.volume_ratio)
        self.assertEqual(620, position.buy_volume)
        self.assertEqual(380, position.sell_volume)
        self.assertEqual(420, position.foreign_buy_volume)
        self.assertEqual(275, position.foreign_sell_volume)
        self.assertEqual(145, position.foreign_net_volume)
        self.assertEqual(310, position.institution_buy_volume)
        self.assertEqual(228, position.institution_sell_volume)
        self.assertEqual(82, position.institution_net_volume)
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
        pnl_message = next(event for event in events if event.rule == "monitorPnlChange").message()
        value_message = next(event for event in events if event.rule == "monitorValueChange").message()

        self.assertIn("수급: 거래량 30,000(1.8x), 거래액 18억 원", pnl_message)
        self.assertIn("투자자: 외국인 +145,000(매수 420,000/매도 275,000), 기관 +82,000(매수 310,000/매도 228,000)", pnl_message)
        self.assertIn("수급: 거래량 30,000(1.8x), 거래액 18억 원", value_message)
        self.assertIn("투자자: 외국인 +145,000(매수 420,000/매도 275,000), 기관 +82,000(매수 310,000/매도 228,000)", value_message)

    def test_monitor_trend_change_uses_moving_average_data(self):
        previous_position = normalize_position({
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 98000,
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

        event = next(
            event
            for event in RealtimeMonitor().events_for_snapshot(current_snapshot, previous_snapshot.to_monitor_state())
            if event.rule == "monitorTrendChange"
        )
        message = event.message()

        self.assertEqual("WATCH", event.severity)
        self.assertIn("20일선 상향 돌파", message)
        self.assertIn("60일선 상향 돌파", message)
        self.assertIn("20/60일선 골든크로스", message)
        self.assertIn("추세: 현재 11만 원", message)
        self.assertIn("수급: 거래량 40,000(2.1x), 거래액 24억 원", message)
        self.assertIn("투자자: 외국인 +70,000(매수 510,000/매도 440,000), 기관 +35,000(매수 350,000/매도 315,000)", message)
        self.assertIn("설정: 20일/60일 이동평균 돌파, 크로스, 또는 괴리 ±8% 이상", event.criteria)
        self.assertTrue(any("20일선 상향 돌파" in item for item in event.criteria))

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
        message = next(event for event in events if event.rule == "monitorValueChange").message()

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
        self.assertEqual(1, DEFAULT_ALERT_RULES["modelBuy"])
        self.assertEqual(1, DEFAULT_ALERT_RULES["modelSell"])
        self.assertEqual(10, DEFAULT_CADENCE["modelBuy"])
        self.assertEqual(10, DEFAULT_CADENCE["modelSell"])

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

    def test_symbol_universe_service_seeds_and_reports_freshness(self):
        service = SymbolUniverseService(SQLiteSymbolUniverseStore(Path(self.temp.name) / "service.db"), runtime_settings())

        payload = service.search(query="TSLA")

        self.assertTrue(any(item["symbol"] == "TSLA" for item in payload["items"]))
        self.assertTrue(payload["summary"]["total"] >= 1)
        self.assertTrue(any(item["market"] == "NASDAQ" for item in payload["summary"]["markets"]))
        self.assertEqual(0, payload["offset"])
        self.assertIn("hasMore", payload)

    def test_flow_lens_mock_contract_is_python_native(self):
        payload = flow_lens_snapshot(mock=True, watchlist_symbols="TSLA,AAPL,NVDA")

        self.assertEqual("mock", payload["dataMode"])
        self.assertIn("tossDecision", payload)
        self.assertTrue(any(item["symbol"] == "AAPL" for item in payload["tossDecision"]["items"]))
        self.assertTrue(any(item["symbol"] == "TSLA" for item in payload["tossDecision"]["items"]))
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
                "modelBuy",
                "modelSell",
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
            "currentPrice": 100000,
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
        self.assertIn("externalCryptoMove", messages)
        self.assertIn("CoinGecko", messages["externalCryptoMove"])
        self.assertIn("비트코인 변동", messages["externalCryptoMove"])
        self.assertIn("크립토 거래액", messages["externalCryptoMove"])
        self.assertIn("externalMacroShift", messages)
        self.assertIn("DGS10", messages["externalMacroShift"])
        self.assertIn("externalDartDisclosure", messages)
        self.assertIn("주요사항보고서", messages["externalDartDisclosure"])
        self.assertIn("externalDataConnection", messages)
        criteria_by_rule = {event.rule: event.criteria for event in events}
        self.assertTrue(any("±3% 이상" in item for item in criteria_by_rule["externalEquityMove"]))
        self.assertTrue(any("24h -5.2%" in item for item in criteria_by_rule["externalCryptoMove"]))
        self.assertTrue(any("±15bp 이상" in item for item in criteria_by_rule["externalMacroShift"]))

    def test_external_signal_provider_normalizes_api_responses_and_caches(self):
        calls = []

        def fake_fetch(url, headers=None):
            calls.append(url)
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
            if "opendart" in url:
                return {"status": "000", "list": [{
                    "corp_name": "삼성전자",
                    "report_nm": "주요사항보고서",
                    "rcept_no": "20260701000001",
                    "rcept_dt": "20260701",
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
            "externalDartLookbackDays": "14",
            "externalDartCorpCodes": "005930=00126380",
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

        self.assertEqual(4, len(calls))
        self.assertEqual(signals, cached_signals)
        self.assertEqual(4.2, signals["equityQuotes"]["AAPL"]["changePercent"])
        self.assertEqual(-5.4, signals["cryptoMarkets"]["bitcoin"]["change24h"])
        self.assertEqual(4.35, signals["macro"]["series"]["DGS10"]["value"])
        self.assertEqual("20260701000001", signals["dartDisclosures"]["005930"]["receiptNo"])

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
        decision_event = next(event for event in events if event.rule == "monitorDecisionChange")
        message = decision_event.message()

        self.assertEqual("Apple", message.splitlines()[0])
        self.assertNotIn("메인 Apple", message)
        self.assertIn("Codex 답변:", message)
        self.assertIn("데이터 검증:", message)
        self.assertIn("모델 보완:", message)
        self.assertIn("손익률 급변", message)

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
        self.assertIn("모델 리뷰", sent[0])
        self.assertTrue(sent[0].startswith("AAPL 모델 리뷰"))
        self.assertNotIn("메인 AAPL 모델 리뷰", sent[0])

    def test_send_events_enqueues_notifications_without_direct_delivery(self):
        account = AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"])
        queue = SQLiteNotificationJobStore(Path(self.temp.name) / "service.db")
        event = AlertEvent("main", "메인", "ALERT", "monitorTrendChange", "main:trend", "SK하이닉스", ["이동평균 변화", "20일선 하향 이탈"], "000660")
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
        self.assertIn("꿀점수", jobs[0].last_error)
        self.assertLess(jobs[0].context["honeyScore"], jobs[0].context["honeyThreshold"])

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
        rules.upsert(rule)
        first = AlertEvent("main", "메인", "ALERT", "monitorTrendChange", "main:trend:1", "SK하이닉스", ["이동평균 변화", "20일선 하향 이탈"], "000660")
        second = AlertEvent("main", "메인", "ALERT", "monitorTrendChange", "main:trend:2", "SK하이닉스", ["이동평균 변화", "20일선 하향 이탈", "변화 -0.1%"], "000660")

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

        self.assertEqual(1, send_events([first], queue=queue).queued)
        self.assertEqual(0, send_events([second], queue=queue).queued)

        jobs = queue.jobs()
        self.assertEqual(["pending", "suppressed"], [job.status for job in jobs])
        self.assertEqual(-55, jobs[1].context["honeySimilarityPenalty"])
        self.assertEqual(360, jobs[1].context["honeySimilarityWindowMinutes"])
        self.assertLess(jobs[1].context["honeyScore"], jobs[1].context["honeyThreshold"])

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

        runner = NotificationQueueRunner(queue, registry, lambda _account: FakeNotifier())

        processed = runner.run_once(limit=10)

        self.assertEqual(2, processed)
        self.assertEqual(["첫 번째", "두 번째"], sent)
        self.assertEqual({"done": 2}, queue.summary())

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
        event = AlertEvent("main", "메인", "WATCH", "monitorHeartbeat", "main:heartbeat", "상태 확인", ["정상"], "")
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
        )

        self.assertEqual(1, runner.run_once(limit=10))
        self.assertEqual("[monitorHeartbeat] 상태 확인\n정상", sent[0])

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
        self.assertIn("<b>[관찰] 이동평균 변화</b>", message)
        self.assertIn("<code>Apple / AAPL</code>", message)
        self.assertIn("<b>발송 기준</b>", message)
        self.assertIn("<b>데이터</b>", message)
        self.assertLess(message.index("<b>데이터</b>"), message.index("<b>발송 기준</b>"))
        self.assertIn("• <b>신호</b>: <code>20일선 상향 돌파</code>", message)
        self.assertIn("• <b>설정</b>: <code>이동평균 돌파, 크로스, 큰 괴리가 감지될 때 보냅니다.</code>", message)
        self.assertIn("• <b>감지</b>: <code>20일선 상향 돌파</code>", message)
        self.assertNotIn("\n\n\n", message)

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
                "가격 $393.45",
                "거래량 71,917,610",
                "기준일 2026-07-02",
                "출처 Alpha Vantage",
            ],
            "TSLA",
        )

        message = templates.render(event.rule, alert_context(event))

        self.assertIn("<b>[주의] 미장 가격/거래량</b>\n<code>TSLA</code>", message)
        self.assertNotIn("━━━━━━━━", message)
        self.assertIn("• <b>미장 가격 변동</b>: <code>-7.5%</code>, <b>가격</b>: <code>$393.45</code>", message)
        self.assertIn("• <b>거래량</b>: <code>71,917,610</code>, <b>기준일</b>: <code>2026-07-02</code>", message)
        self.assertIn("• <b>출처</b>: <code>Alpha Vantage</code>", message)
        self.assertLess(message.index("<b>데이터</b>"), message.index("<b>발송 기준</b>"))
        self.assertIn("• <b>감지</b>: <code>가격 변동 -7.5%, 가격 $393.45</code>", message)

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
        )

        message = templates.render(event.rule, alert_context(event))

        change_line = "• <b>비트코인 변동</b>: <code>24h -5.2% · 7d -12.1%</code>"
        price_line = "• <b>크립토 가격</b>: <code>$108,000</code>"
        value_line = "• <b>크립토 거래액</b>: <code>$42,000,000,000</code>"
        self.assertIn(change_line + "\n" + price_line + "\n" + value_line, message)
        self.assertLess(message.index(price_line), message.index(value_line))
        self.assertIn("• <b>감지</b>: <code>비트코인 24h -5.2%, 7d -12.1%</code>", message)

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
                "추세: 현재 11만 원, 20일선 10만 원(+1.9%), 60일선 10만 원(+2.9%)",
                "수급: 거래량 40,000(2.1x), 거래액 24억 원",
                "투자자: 외국인 +70,000(매수 510,000/매도 440,000), 기관 +35,000(매수 350,000/매도 315,000)",
            ],
            "005930",
        )

        message = templates.render(event.rule, alert_context(event))

        flow_line = "• <b>수급</b>: <code>거래량 40,000(2.1x), 거래액 24억 원</code>"
        trend_line = "• <b>추세</b>: <code>현재 11만 원, 20일선 10만 원(+1.9%), 60일선 10만 원(+2.9%)</code>"
        investor_line = "• <b>투자자</b>: <code>외국인 +70,000(매수 510,000/매도 440,000), 기관 +35,000(매수 350,000/매도 315,000)</code>"
        self.assertIn(flow_line + "\n" + trend_line + "\n" + investor_line, message)
        self.assertLess(message.index(flow_line), message.index(trend_line))
        self.assertLess(message.index(trend_line), message.index(investor_line))
        self.assertIn("• <b>설정</b>: <code>이동평균 돌파, 크로스, 큰 괴리가 감지될 때 보냅니다.</code>", message)

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
                "추세: 현재 15만 원, 20일선 14만 원(+4.2%)",
                "손익 -3.2%",
                "수급: 거래량 31,000(1.7x), 거래액 48억 원",
                "상태 조건부 보유",
            ],
            "000660",
            criteria=[
                "설정: 판단 톤이 danger/caution 이거나 손익률이 -8% 이하일 때",
                "감지: 상태 조건부 보유, 손익 -3.2%",
            ],
        )

        message = templates.render(event.rule, alert_context(event))

        status_line = "• <b>상태</b>: <code>조건부 보유</code>"
        profit_line = "• <b>손익</b>: <code>-3.2%</code>"
        flow_line = "• <b>수급</b>: <code>거래량 31,000(1.7x), 거래액 48억 원</code>"
        trend_line = "• <b>추세</b>: <code>현재 15만 원, 20일선 14만 원(+4.2%)</code>"
        self.assertIn(status_line + "\n" + profit_line + "\n" + flow_line + "\n" + trend_line, message)
        self.assertLess(message.index(status_line), message.index(profit_line))
        self.assertLess(message.index(flow_line), message.index(trend_line))
        self.assertIn("• <b>설정</b>: <code>판단 톤이 danger/caution 이거나 손익률이 -8% 이하일 때</code>", message)
        self.assertIn("• <b>감지</b>: <code>상태 조건부 보유, 손익 -3.2%</code>", message)

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

    def test_notification_schedules_use_real_monitor_sent_history(self):
        registry = AccountRegistry()
        registry.upsert(AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"]))
        store = SQLiteMonitorStore(Path(self.temp.name) / "service.db")
        event = AlertEvent("main", "메인", "WATCH", "monitorHeartbeat", "main:heartbeat", "상태 확인", ["정상"], "")
        store.mark_sent([event])

        payload = notification_schedules_payload()
        schedule = next(item for item in payload["schedules"] if item["messageType"] == "monitorHeartbeat")

        self.assertTrue(schedule["enabled"])
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
        self.assertIn("Exit Lens Python Admin", html)
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
