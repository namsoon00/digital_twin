import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.monitoring_position_context import MonitoringPositionContextMixin
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position
from digital_twin.domain.strategy_alerts import StrategyAlertMixin
from digital_twin.domain.crypto_market_signals import crypto_market_positions


class WatchlistAlertHarness(StrategyAlertMixin, MonitoringPositionContextMixin):
    def criteria(self, setting: str, detected: str = ""):
        return [setting, detected]


class StrategyAlertTests(unittest.TestCase):
    def setUp(self):
        self.harness = WatchlistAlertHarness()
        self.snapshot = AccountSnapshot(
            account_id="main",
            account_label="주 계정",
            provider="test",
            mode="live",
            status="ok",
            generated_at="2026-07-25T00:00:00Z",
            portfolio=PortfolioSummary(0, 0, 0, [], [], 0),
        )
        self.position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            current_price=70000,
            source="watchlist",
        )
        self.context = {
            "activeRules": [{"ruleId": "graph.loss_guard.breakdown.v1", "label": "손실 방어 추론"}],
            "reviewLevel": "observe",
            "dataState": "sufficient",
            "changeState": "changed",
            "conflictState": "none",
            "decision": {
                "label": "손실 축소 기준 점검",
                "notificationCategory": "riskWatch",
            },
        }

    def test_rulebox_notification_severity_is_the_only_delivery_instruction(self):
        context = dict(self.context)
        context["executionPlan"] = {"notificationSeverity": "ALERT", "notificationCategory": "riskWatch"}

        event = self.harness.watchlist_ontology_event(
            self.snapshot,
            self.position,
            self.position.to_dict(),
            context,
        )

        self.assertIsNotNone(event)
        self.assertEqual("ALERT", event.severity)
        self.assertEqual("riskWatch", event.metadata["watchlistOntologySignalType"])

    def test_relation_without_materialized_delivery_instruction_is_not_sent(self):
        event = self.harness.watchlist_ontology_event(
            self.snapshot,
            self.position,
            self.position.to_dict(),
            self.context,
        )

        self.assertIsNone(event)

    def test_crypto_alert_requires_fresh_data_materialized_rule_and_transition(self):
        self.snapshot.external_signals = {
            "cryptoFreshness": {"status": "fresh", "fetchedAt": "2026-08-01T00:00:00Z", "ageMinutes": 2},
            "cryptoMarkets": {
                "bitcoin": {"symbol": "BTC", "name": "Bitcoin", "price": 65000, "change24h": -3.2},
            },
        }
        position = crypto_market_positions(self.snapshot.external_signals)[0]
        context = {
            "activeRules": [{
                "ruleId": "graph.crypto.market.24h.down.watch.v1",
                "label": "BTC/ETH 24h 원시 하락 경로 -> 크립토 변동 재확인",
                "ruleSourceKind": "crypto-asset",
                "ruleScopeFamilies": ["market"],
            }],
            "reviewLevel": "observe",
            "dataState": "sufficient",
            "changeState": "new-condition",
            "decision": {"label": "크립토 하락 경로 재확인"},
            "executionPlan": {"notificationSeverity": "WATCH"},
        }
        transitions = [{"symbol": "BTC", "horizon": "24h", "changePct": -3.2, "signature": "BTC:24h:down:watch"}]

        event = self.harness.crypto_ontology_event(self.snapshot, position, context, transitions)

        self.assertIsNotNone(event)
        self.assertEqual("cryptoOntologySignal", event.rule)
        self.assertEqual("WATCH", event.severity)
        self.assertIn("CoinGecko", "\n".join(event.lines))
        self.assertEqual(["BTC:24h:down:watch"], [item["signature"] for item in event.metadata["cryptoTransitions"]])

        self.snapshot.external_signals["cryptoFreshness"]["status"] = "stale"
        self.assertIsNone(self.harness.crypto_ontology_event(self.snapshot, position, context, transitions))


if __name__ == "__main__":
    unittest.main()
