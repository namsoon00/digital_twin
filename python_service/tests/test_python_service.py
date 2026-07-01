import json
import os
import sqlite3
import sys
import tempfile
import unittest
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
from digital_twin.cli import build_handoff_message
from digital_twin.cli import preserve_existing_secrets
from digital_twin.cli import build_parser
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.analytics import SafeFormula, StrategyModel, decisions_for_positions, normalize_position, portfolio_summary
from digital_twin.domain.events import ACCOUNT_SAVED, MONITORING_ALERTS_DETECTED, MONITORING_CYCLE_COMPLETED, MONITORING_SNAPSHOT_COLLECTED, alerts_detected_event
from digital_twin.domain.monitoring import DEFAULT_ALERT_RULES, DEFAULT_CADENCE, RealtimeMonitor
from digital_twin.domain.model_review import ModelReviewJob, local_model_review
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.parsing import parse_assignments
from digital_twin.domain.portfolio import AccountSnapshot, AlertEvent, utc_now_iso
from digital_twin.infrastructure.event_bus import EventBus, JsonEventLog
from digital_twin.infrastructure.json_monitor_state import MonitorStore
from digital_twin.infrastructure.model_review_queue import ModelReviewEnqueuer, ModelReviewJobStore
from digital_twin.infrastructure.mock_market import mock_market_payload
from digital_twin.infrastructure.notifications import send_events
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.sqlite_operational import SQLiteAppStore, SQLiteEventLog, SQLiteModelReviewJobStore, SQLiteMonitorStore, SQLiteNotificationJobStore, SQLiteNotificationTemplateStore, SQLiteRuntimeSettingsStore
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
            "executionStrength": "128.4",
            "marketValue": 720000,
        })

        self.assertEqual(128.4, position.trade_strength)
        self.assertEqual(1000, position.volume)
        self.assertEqual(72000000, position.trading_value)

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

        self.assertIn("수급 체결강도 128 · 거래액 18억 원", pnl_message)
        self.assertIn("수급 체결강도 128 · 거래액 18억 원", value_message)

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
        self.assertEqual({10}, set(DEFAULT_CADENCE.values()))

    def test_default_watchlist_and_model_alerts_include_requested_symbols(self):
        symbols = runtime_settings()["watchlistSymbols"].split(",")

        self.assertIn("TSLA", symbols)
        self.assertIn("AAPL", symbols)
        self.assertEqual(1, DEFAULT_ALERT_RULES["modelBuy"])
        self.assertEqual(1, DEFAULT_ALERT_RULES["modelSell"])
        self.assertEqual(10, DEFAULT_CADENCE["modelBuy"])
        self.assertEqual(10, DEFAULT_CADENCE["modelSell"])

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
                "holdingTiming",
                "monitorHeartbeat",
                "monitorConnection",
                "monitorPositionChange",
                "monitorPnlChange",
                "monitorValueChange",
                "monitorCashChange",
                "monitorDecisionChange",
            },
            {event.rule for event in events},
        )
        self.assertTrue(all(not event.message().startswith("메인 ") for event in events))

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
        event = AlertEvent("main", "메인", "WATCH", "monitorHeartbeat", "main:heartbeat", "상태 확인", ["정상"], "")
        source_event = alerts_detected_event([event])

        result = send_events([event], accounts={"main": account}, queue=queue, source_event=source_event)
        duplicate = send_events([event], accounts={"main": account}, queue=queue, source_event=source_event)

        self.assertTrue(result.delivered)
        self.assertEqual(1, result.queued)
        self.assertEqual(0, duplicate.queued)
        jobs = queue.pending(limit=10)
        self.assertEqual(1, len(jobs))
        self.assertEqual("pending", jobs[0].status)
        self.assertEqual("monitorHeartbeat", jobs[0].message_type)
        self.assertEqual(source_event.event_id, jobs[0].source_event_id)
        self.assertEqual(source_event.name, jobs[0].source_event_name)
        self.assertTrue(jobs[0].dedupe_key)
        self.assertEqual("main:heartbeat", jobs[0].context["key"])
        self.assertEqual("monitorHeartbeat", jobs[0].context["rule"])
        self.assertEqual("상태 확인", jobs[0].context["title"])
        self.assertIn("정상", jobs[0].context["lines"])
        self.assertIn("상태 확인", jobs[0].text)

    def test_notification_queue_runner_delivers_pending_messages_in_order(self):
        registry = AccountRegistry()
        registry.upsert(AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"]))
        queue = SQLiteNotificationJobStore(Path(self.temp.name) / "service.db")
        queue.enqueue(NotificationJob.create("첫 번째", account_id="main", account_label="메인", message_type="test"))
        queue.enqueue(NotificationJob.create("두 번째", account_id="main", account_label="메인", message_type="test"))
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
        self.assertTrue(notification_store.enqueue(NotificationJob.create("queued", account_id="main", message_type="test")))
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
