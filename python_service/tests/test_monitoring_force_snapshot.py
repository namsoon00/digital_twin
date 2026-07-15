import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.monitoring_service import MonitorRunner
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.market_data import normalize_position
from digital_twin.domain.message_types import PORTFOLIO_HOLDINGS_SNAPSHOT
from digital_twin.domain.monitoring import RealtimeMonitor
from digital_twin.domain.portfolio import AccountSnapshot, utc_now_iso
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.strategy import decisions_for_positions


class MemoryMonitorStore:
    def __init__(self):
        self._previous = {}
        self._sent = {}

    @property
    def previous(self):
        return self._previous

    @property
    def sent(self):
        return self._sent

    def save_snapshot(self, snapshot):
        self._previous[snapshot.account_id] = snapshot.to_monitor_state()

    def mark_sent(self, events):
        stamp = utc_now_iso()
        for event in events:
            self._sent[event.key] = stamp
            self._sent[event.cadence_key()] = stamp

    def write(self):
        return None


class MonitoringForceSnapshotTests(unittest.TestCase):
    def test_force_run_adds_all_holdings_snapshot_event_with_freshness(self):
        account = AccountConfig("main", "메인", "toss", "https://example.test", "", "", "", ["AAPL"])
        sent = []

        def snapshot_builder(_account):
            position = normalize_position({
                "symbol": "AAPL",
                "name": "Apple",
                "currency": "USD",
                "currentPrice": 327.5,
                "averagePrice": 313.5,
                "marketValue": 327.5,
                "marketValueKrw": 450000,
                "profitLossRate": 4.48,
                "quantity": 1,
                "sellableQuantity": 1,
                "updatedAt": utc_now_iso(),
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

        def sender(events, dry_run=False, accounts=None, source_event=None):
            sent.extend(events)
            return SimpleNamespace(delivered=True)

        events = MonitorRunner(
            [account],
            store=MemoryMonitorStore(),
            monitor=RealtimeMonitor(),
            snapshot_builder=snapshot_builder,
            event_sender=sender,
        ).run_once(dry_run=True, force=True)

        holdings_events = [event for event in events if event.rule == PORTFOLIO_HOLDINGS_SNAPSHOT]
        self.assertEqual(1, len(holdings_events))
        self.assertEqual(events, sent)
        self.assertIn("Apple / AAPL", "\n".join(holdings_events[0].lines))
        self.assertTrue(holdings_events[0].metadata["dataFreshnessRequired"])
        self.assertEqual("fresh", holdings_events[0].metadata["dataFreshness"]["status"])


if __name__ == "__main__":
    unittest.main()
