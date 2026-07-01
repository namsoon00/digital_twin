import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.account_service import AccountApplicationService
from digital_twin.application.monitoring_service import MonitorRunner as ApplicationMonitorRunner
from digital_twin.cli import preserve_existing_secrets
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.analytics import SafeFormula, StrategyModel, decisions_for_positions, normalize_position, portfolio_summary
from digital_twin.domain.events import ACCOUNT_SAVED, MONITORING_ALERTS_DETECTED, MONITORING_CYCLE_COMPLETED, MONITORING_SNAPSHOT_COLLECTED
from digital_twin.domain.monitoring import RealtimeMonitor
from digital_twin.domain.parsing import parse_assignments
from digital_twin.domain.portfolio import AccountSnapshot, utc_now_iso
from digital_twin.infrastructure.event_bus import EventBus, JsonEventLog
from digital_twin.infrastructure.json_monitor_state import MonitorStore
from digital_twin.infrastructure.sqlite_accounts import AccountRegistry
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

    def test_strategy_formula_is_safe_and_scores(self):
        formula = SafeFormula("max(0, buyShare - 50) + abs(priceChangeRate)")
        self.assertEqual(17, formula.evaluate({"buyShare": 65, "priceChangeRate": -2}))
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

        self.assertIn("Codex 답변:", message)
        self.assertIn("데이터 검증:", message)
        self.assertIn("모델 보완:", message)
        self.assertIn("손익률 급변", message)

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


class AssignmentTests(unittest.TestCase):
    def test_parse_assignments_preserves_defaults(self):
        values = parse_assignments("a=2\nb:3\nbad", {"a": 1, "c": 4})
        self.assertEqual(values["a"], 2)
        self.assertEqual(values["b"], 3)
        self.assertEqual(values["c"], 4)


if __name__ == "__main__":
    unittest.main()
