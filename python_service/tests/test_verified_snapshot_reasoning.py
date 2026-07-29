import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.kis_realtime_service import KISRealtimeWebSocketRunner
from digital_twin.application.ontology_reasoning_service import OntologyReasoningRunner
from digital_twin.domain.events import DomainEvent, MARKET_DATA_COLLECTED, ontology_reasoning_requested_event
from digital_twin.domain.ontology_reasoning_queue import durable_mailbox_entries
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position
from digital_twin.domain.verified_snapshot_reasoning import (
    VERIFIED_MONITOR_SNAPSHOT_SLOT_FAMILY,
    VERIFIED_MONITOR_SNAPSHOT_TRIGGER,
    verified_monitor_snapshot_reasoning_event,
)
from digital_twin.infrastructure.event_bus import EventBus


def snapshot(
    *,
    aapl_price=100.0,
    msft_price=200.0,
    external_signals=None,
    generated_at="2026-07-29T00:03:00Z",
):
    return AccountSnapshot(
        account_id="acct",
        account_label="Test",
        provider="toss",
        mode="live",
        status="ok",
        generated_at=generated_at,
        portfolio=PortfolioSummary(1000.0, 700.0, 300.0, [], [], 70.0),
        positions=[
            Position(
                symbol="AAPL",
                name="Apple",
                market="US",
                currency="USD",
                quantity=1.0,
                sellable_quantity=1.0,
                average_price=90.0,
                current_price=aapl_price,
                ma20=95.0,
                source="holding",
            ),
            Position(
                symbol="MSFT",
                name="Microsoft",
                market="US",
                currency="USD",
                quantity=1.0,
                sellable_quantity=1.0,
                average_price=190.0,
                current_price=msft_price,
                ma20=195.0,
                source="holding",
            ),
        ],
        external_signals=dict(external_signals or {}),
    )


class VerifiedSnapshotReasoningTests(unittest.TestCase):
    def test_first_snapshot_creates_a_replayable_latest_state_request(self):
        current = snapshot()

        event = verified_monitor_snapshot_reasoning_event(current)

        self.assertEqual(VERIFIED_MONITOR_SNAPSHOT_TRIGGER, event.payload["trigger"])
        self.assertEqual(current.generated_at, event.payload["sourceObservedAt"])
        self.assertEqual(current.generated_at, event.payload["verifiedSourceSnapshot"]["generatedAt"])
        self.assertEqual(["AAPL", "MSFT"], event.payload["symbols"])
        self.assertIn("MarketQuote", event.payload["factTypes"])
        self.assertTrue(event.payload["factRevisionsBySymbol"]["AAPL"])

    def test_identical_snapshot_does_not_schedule_another_projection(self):
        current = snapshot()

        event = verified_monitor_snapshot_reasoning_event(current, current.to_monitor_state())

        self.assertIsNone(event)

    def test_one_quote_change_targets_only_that_symbol(self):
        previous = snapshot()
        current = snapshot(aapl_price=101.0)

        event = verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())

        self.assertEqual(["AAPL"], event.payload["symbols"])
        self.assertIn("current_price", event.payload["changedFieldsBySymbol"]["AAPL"])
        self.assertNotIn("portfolioContext", event.payload["changedFieldsBySymbol"]["AAPL"])

    def test_changed_direct_research_evidence_targets_the_related_symbol(self):
        previous = snapshot(external_signals={
            "newsHeadlines": {
                "AAPL": {"items": [{"symbol": "AAPL", "title": "Old title", "url": "https://example.test/old"}]},
            },
        })
        current = snapshot(external_signals={
            "newsHeadlines": {
                "AAPL": {"items": [{"symbol": "AAPL", "title": "New title", "url": "https://example.test/new"}]},
            },
        })

        event = verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())

        self.assertEqual(["AAPL"], event.payload["symbols"])
        self.assertIn("ResearchEvidence", event.payload["factTypes"])
        self.assertIn("external.newsHeadlines", event.payload["changedFieldsBySymbol"]["AAPL"])

    def test_snapshot_barrier_has_an_independent_mailbox_slot_from_raw_kis_ticks(self):
        current = snapshot()
        barrier = verified_monitor_snapshot_reasoning_event(current)
        raw = ontology_reasoning_requested_event(
            DomainEvent(
                name="market_data.collected",
                aggregate_id="market:AAPL",
                payload={"sourceObservedAt": "2026-07-29T00:03:10Z", "accountId": "acct"},
            ),
            "kis-realtime-websocket",
            ["AAPL"],
            changed_count=1,
            fact_types=["MarketQuote", "ExecutionFlow", "OrderBook"],
        )

        barrier_entry = durable_mailbox_entries(barrier)[0]
        raw_entry = durable_mailbox_entries(raw)[0]

        self.assertEqual(VERIFIED_MONITOR_SNAPSHOT_SLOT_FAMILY, barrier_entry["mailboxSlotFamily"])
        self.assertNotEqual(barrier_entry["mailboxKey"], raw_entry["mailboxKey"])

    def test_calendar_updates_now_use_a_latest_state_mailbox_slot(self):
        calendar = ontology_reasoning_requested_event(
            DomainEvent(
                name="investment_calendar.event_saved",
                aggregate_id="calendar:AAPL",
                payload={"sourceObservedAt": "2026-07-29T00:03:00Z", "accountId": "acct"},
            ),
            "investment-calendar-update",
            ["AAPL"],
            changed_count=1,
            fact_types=["InvestmentCalendarEvent"],
        )
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=None,
            monitor_runner_factory=lambda: None,
        )

        entries = runner.mailbox_entries_for_event(calendar)

        self.assertEqual(1, len(durable_mailbox_entries(calendar)))
        self.assertEqual(1, len(entries))
        self.assertEqual("InvestmentCalendarEvent", entries[0]["mailboxSlotFamily"])

    def test_kis_tick_is_retained_as_source_data_without_starting_an_unreplayable_turn(self):
        events = EventBus()
        runner = KISRealtimeWebSocketRunner(
            client=SimpleNamespace(enabled=lambda: True, configured=lambda: True),
            symbol_selector=SimpleNamespace(symbols=lambda: ["005930"], reasoning_symbols=lambda: ["005930"]),
            quote_cache=SimpleNamespace(load=lambda *_args: {}),
            settings={"materialityGateEnabled": "0"},
            event_publisher=events,
        )
        runner.record_updates([{
            "symbol": "005930",
            "previous": {"symbol": "005930", "currentPrice": 70000.0},
            "payload": {"symbol": "005930", "currentPrice": 70100.0, "market": "KR", "dataQuality": "actual"},
        }])

        result = runner.flush_events(force=True)

        self.assertEqual("verified-monitor-snapshot-barrier", result["investmentReasoningScheduling"])
        self.assertEqual("next-verified-monitor-snapshot", result["reasoningDispatch"])
        self.assertEqual([MARKET_DATA_COLLECTED], [event.name for event in events.published])


if __name__ == "__main__":
    unittest.main()
