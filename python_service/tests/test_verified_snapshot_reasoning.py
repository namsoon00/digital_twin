import copy
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.kis_realtime_service import KISRealtimeWebSocketRunner
from digital_twin.application.ontology_reasoning_service import OntologyReasoningRunner
from digital_twin.domain.events import DomainEvent, MARKET_DATA_COLLECTED, ontology_reasoning_requested_event
from digital_twin.domain.ontology_reasoning_queue import (
    OBSERVATION_FOLLOWUP_PRIORITY_HINT,
    REALTIME_LATEST_STATE_SLOT,
    durable_mailbox_entries,
)
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position
from digital_twin.domain.verified_snapshot_reasoning import (
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

    def test_subthreshold_quote_refresh_does_not_create_another_typedb_turn(self):
        previous = snapshot()
        current = snapshot(aapl_price=100.2)

        event = verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())

        self.assertIsNone(event)

    def test_foreign_flow_direction_change_creates_a_flow_request(self):
        previous = snapshot()
        current = snapshot()
        previous.positions[0].foreign_buy_volume = 100.0
        previous.positions[0].foreign_sell_volume = 110.0
        previous.positions[0].foreign_net_volume = -10.0
        current.positions[0].foreign_buy_volume = 130.0
        current.positions[0].foreign_sell_volume = 100.0
        current.positions[0].foreign_net_volume = 30.0

        event = verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())
        assessments = {
            item["subject"]: item
            for item in event.payload["materialityAssessments"]
        }

        self.assertEqual(["AAPL"], event.payload["symbols"])
        self.assertEqual(["ExecutionFlow"], event.payload["factTypesBySymbol"]["AAPL"])
        self.assertIn("foreign-flow-direction", assessments["AAPL"]["matchedConditions"])

    def test_notified_market_observation_marks_only_the_changed_symbol_for_prompt_followup(self):
        previous = snapshot()
        current = snapshot(aapl_price=101.0)

        event = verified_monitor_snapshot_reasoning_event(
            current,
            previous.to_monitor_state(),
            observation_followup_symbols=["AAPL", "UNKNOWN"],
        )

        self.assertEqual(["AAPL"], event.payload["observationFollowupSymbols"])
        entries = durable_mailbox_entries(event)
        self.assertEqual(1, len(entries))
        self.assertTrue(entries[0]["observationFollowup"])
        self.assertGreaterEqual(entries[0]["priorityHint"], OBSERVATION_FOLLOWUP_PRIORITY_HINT)

    def test_notified_market_observation_rechecks_an_unchanged_snapshot_symbol(self):
        current = snapshot()

        event = verified_monitor_snapshot_reasoning_event(
            current,
            current.to_monitor_state(),
            observation_followup_symbols=["AAPL", "UNKNOWN"],
        )

        self.assertIsNotNone(event)
        self.assertEqual(["AAPL"], event.payload["symbols"])
        self.assertEqual(["AAPL"], event.payload["observationFollowupSymbols"])
        self.assertEqual(0, event.payload["changedCount"])
        self.assertEqual(["marketObservationFollowup"], event.payload["changedFieldsBySymbol"]["AAPL"])
        self.assertIn("MarketQuote", event.payload["factTypes"])
        self.assertTrue(event.payload["factRevisionsBySymbol"]["AAPL"])
        entries = durable_mailbox_entries(event)
        self.assertEqual(1, len(entries))
        self.assertTrue(entries[0]["observationFollowup"])

    def test_unchanged_market_observation_followup_is_retained_by_scheduler(self):
        current = snapshot()
        request = verified_monitor_snapshot_reasoning_event(
            current,
            current.to_monitor_state(),
            observation_followup_symbols=["AAPL"],
        )

        class Reader:
            def recent_events(self, **_kwargs):
                return [request]

        class Cursor:
            def processed_event_ids(self):
                return []

            def load(self):
                return {}

        runner = OntologyReasoningRunner(
            Reader(),
            Cursor(),
            monitor_runner_factory=lambda: None,
            now_provider=lambda: datetime(2026, 7, 29, 1, 0),
        )

        self.assertEqual([request.event_id], [event.event_id for event in runner.pending_requests()])

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

    def test_research_cache_rotation_without_eligible_evidence_does_not_enqueue(self):
        previous = snapshot(external_signals={
            "researchEvidence": {
                "AAPL": [{
                    "evidenceId": "research:AAPL:yfinance:old",
                    "symbol": "AAPL",
                    "kind": "financial-fact",
                    "source": "yfinance",
                    "title": "yfinance 종합 데이터",
                    "observedAt": "2026-07-29T00:00:00Z",
                    "payload": {
                        "relationScope": "",
                        "sourceKind": "unofficial-yahoo-finance-wrapper",
                        "evidenceGovernance": {"investmentJudgmentEligible": False},
                    },
                }],
            },
        })
        current = snapshot(external_signals={
            "researchEvidence": {
                "AAPL": [{
                    "evidenceId": "research:AAPL:yfinance:new",
                    "symbol": "AAPL",
                    "kind": "financial-fact",
                    "source": "yfinance",
                    "title": "yfinance 종합 데이터",
                    "observedAt": "2026-07-29T00:05:00Z",
                    "payload": {
                        "relationScope": "",
                        "sourceKind": "unofficial-yahoo-finance-wrapper",
                        "evidenceGovernance": {"investmentJudgmentEligible": False},
                    },
                }],
            },
        })

        self.assertIsNone(
            verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())
        )

    def test_eligible_research_set_change_targets_only_its_symbol(self):
        previous = snapshot(external_signals={
            "freshness": {"status": "fresh", "dataState": "sufficient"},
        })
        current = snapshot(aapl_price=100.2, external_signals={
            "freshness": {"status": "stale", "dataState": "partial"},
            "researchEvidence": {
                "AAPL": [{
                    "evidenceId": "research:AAPL:direct:1",
                    "symbol": "AAPL",
                    "kind": "news",
                    "source": "Reuters",
                    "title": "Apple guidance changes",
                    "url": "https://example.test/aapl-guidance",
                    "polarity": "risk",
                    "observedAt": "2026-07-29T00:05:00Z",
                    "payload": {
                        "relationScope": "direct",
                        "articleReadStatus": "body",
                        "articleFacts": {"bodyQualityPassed": True},
                        "evidenceGovernance": {"investmentJudgmentEligible": True},
                    },
                }],
            },
        })

        event = verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())

        self.assertEqual(["AAPL"], event.payload["symbols"])
        self.assertEqual(["ResearchEvidence"], event.payload["factTypesBySymbol"]["AAPL"])
        self.assertIn("external.researchEvidence", event.payload["changedFieldsBySymbol"]["AAPL"])
        self.assertNotIn("current_price", event.payload["changedFieldsBySymbol"]["AAPL"])

    def test_external_quality_clock_changes_do_not_fan_out_a_new_reasoning_request(self):
        previous = snapshot(external_signals={
            "quality": {
                "generatedAt": "2026-07-29T00:00:00Z",
                "fetchedAt": "2026-07-29T00:00:00Z",
                "ageMinutes": 1,
                "dataState": "sufficient",
                "coverageState": "complete",
                "sourceHealthState": "healthy",
            },
            "freshness": {
                "fetchedAt": "2026-07-29T00:00:00Z",
                "ageMinutes": 1,
                "status": "fresh",
                "transportStatus": "fresh",
                "dataState": "sufficient",
            },
        })
        clock_only_refresh = snapshot(external_signals={
            "quality": {
                "generatedAt": "2026-07-29T00:05:00Z",
                "fetchedAt": "2026-07-29T00:05:00Z",
                "ageMinutes": 0,
                "dataState": "sufficient",
                "coverageState": "complete",
                "sourceHealthState": "healthy",
            },
            "freshness": {
                "fetchedAt": "2026-07-29T00:05:00Z",
                "ageMinutes": 0,
                "status": "fresh",
                "transportStatus": "fresh",
                "dataState": "sufficient",
            },
        })

        self.assertIsNone(
            verified_monitor_snapshot_reasoning_event(
                clock_only_refresh,
                previous.to_monitor_state(),
            )
        )

    def test_external_quality_state_change_stays_out_of_per_symbol_reasoning(self):
        previous = snapshot(external_signals={
            "quality": {
                "dataState": "sufficient",
                "coverageState": "complete",
                "sourceHealthState": "healthy",
            },
            "freshness": {
                "fetchedAt": "2026-07-29T00:00:00Z",
                "ageMinutes": 1,
                "status": "fresh",
                "transportStatus": "fresh",
                "dataState": "sufficient",
            },
            "provenance": {"sources": ["OpenDART"], "unavailableSources": []},
            "statuses": [{"source": "OpenDART", "ok": True}],
        })
        stale = snapshot(external_signals={
            "quality": {
                "dataState": "partial",
                "coverageState": "incomplete",
                "sourceHealthState": "degraded",
            },
            "freshness": {
                "fetchedAt": "2026-07-29T01:00:00Z",
                "ageMinutes": 61,
                "status": "stale",
                "transportStatus": "stale",
                "dataState": "partial",
            },
            "provenance": {"sources": ["OpenDART"], "unavailableSources": ["OpenDART"]},
            "statuses": [{"source": "OpenDART", "ok": False, "message": "unauthorized"}],
        })

        self.assertIsNone(
            verified_monitor_snapshot_reasoning_event(stale, previous.to_monitor_state())
        )

    def test_ordinary_rate_refresh_waits_for_the_next_symbol_event(self):
        previous = snapshot(external_signals={
            "macro": {"series": {"DGS10": {"value": 4.0}}},
        })
        current = snapshot(external_signals={
            "macro": {"series": {"DGS10": {"value": 4.1}}},
        })

        self.assertIsNone(
            verified_monitor_snapshot_reasoning_event(
                current,
                previous.to_monitor_state(),
            )
        )

    def test_systemic_rate_transition_can_recheck_all_live_symbols(self):
        previous = snapshot(external_signals={
            "macro": {"series": {"DGS10": {"value": 4.0}}},
        })
        current = snapshot(external_signals={
            "macro": {"series": {"DGS10": {"value": 4.3}}},
        })

        event = verified_monitor_snapshot_reasoning_event(
            current,
            previous.to_monitor_state(),
        )

        self.assertEqual(["AAPL", "MSFT"], event.payload["symbols"])
        self.assertEqual(["InterestRate"], event.payload["factTypesBySymbol"]["AAPL"])
        transition = event.payload["verifiedSourceSnapshot"]["systemicMacroTransition"]
        self.assertTrue(transition["systemic"])
        self.assertAlmostEqual(30.0, transition["rateChangesBp"]["DGS10"])

    def test_price_event_reads_new_macro_context_without_macro_fanout(self):
        previous = snapshot(external_signals={
            "macro": {"series": {"DGS10": {"value": 4.0}}},
        })
        current = snapshot(aapl_price=102.0, external_signals={
            "macro": {"series": {"DGS10": {"value": 4.1}}},
        })

        event = verified_monitor_snapshot_reasoning_event(
            current,
            previous.to_monitor_state(),
        )

        self.assertEqual(["AAPL"], event.payload["symbols"])
        self.assertEqual(["MarketQuote"], event.payload["factTypesBySymbol"]["AAPL"])
        self.assertNotIn("external.macro", event.payload["changedFieldsBySymbol"]["AAPL"])

    def test_material_price_change_keeps_global_quality_as_context(self):
        previous = snapshot(external_signals={
            "freshness": {"status": "fresh", "dataState": "sufficient"},
        })
        current = snapshot(aapl_price=102.0, external_signals={
            "freshness": {"status": "stale", "dataState": "partial"},
        })

        event = verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())

        self.assertEqual(["AAPL"], event.payload["symbols"])
        self.assertEqual(["MarketQuote"], event.payload["factTypesBySymbol"]["AAPL"])
        self.assertNotIn("external.freshness", event.payload["changedFieldsBySymbol"]["AAPL"])

    def test_supplemental_quote_cache_refresh_does_not_enqueue_a_duplicate_turn(self):
        previous = snapshot(external_signals={
            "yfinanceData": {"AAPL": {"marketCap": 1000, "trailingPE": 20}},
        })
        current = snapshot(external_signals={
            "yfinanceData": {"AAPL": {"marketCap": 1100, "trailingPE": 21}},
        })

        self.assertIsNone(
            verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())
        )

    def test_company_knowledge_revision_enqueues_company_fact_families_once(self):
        base_company = {
            "AAPL": {
                "schemaVersion": "company-knowledge-v1",
                "symbol": "AAPL",
                "factRevision": "revision-a",
                "financials": {"annual": [{"period": "2025-09-30", "revenueGrowthPct": 5.0}]},
                "coverage": {"financialPeriods": 1, "dataState": "partial"},
            },
        }
        changed_company = copy.deepcopy(base_company)
        changed_company["AAPL"]["factRevision"] = "revision-b"
        changed_company["AAPL"]["financials"]["annual"][0]["revenueGrowthPct"] = 8.0
        previous = snapshot(external_signals={"companyKnowledge": base_company})
        current = snapshot(external_signals={"companyKnowledge": changed_company})

        event = verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())

        self.assertEqual(["AAPL"], event.payload["symbols"])
        self.assertEqual(
            ["CapitalStructureChange", "CompanyProfile", "FinancialFact", "GovernanceChange", "ValuationObservation"],
            event.payload["factTypesBySymbol"]["AAPL"],
        )
        entries = durable_mailbox_entries(event)
        self.assertEqual({"EVIDENCE"}, {entry["workClass"] for entry in entries})

    def test_price_change_reads_company_knowledge_without_company_change_event(self):
        company = {
            "AAPL": {
                "schemaVersion": "company-knowledge-v1",
                "symbol": "AAPL",
                "factRevision": "stable-revision",
                "financials": {"annual": [{"period": "2025-09-30", "revenueGrowthPct": 5.0}]},
                "coverage": {"financialPeriods": 1, "dataState": "partial"},
            },
        }
        previous = snapshot(external_signals={"companyKnowledge": company})
        current = snapshot(aapl_price=102.0, external_signals={"companyKnowledge": company})

        event = verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())

        self.assertEqual(["AAPL"], event.payload["symbols"])
        self.assertEqual(["MarketQuote"], event.payload["factTypesBySymbol"]["AAPL"])
        self.assertNotIn("external.companyKnowledge", event.payload["changedFieldsBySymbol"]["AAPL"])

    def test_mailbox_entries_keep_fact_types_bound_to_the_changed_symbol(self):
        previous = snapshot()
        current = snapshot(
            msft_price=202.0,
            external_signals={
                "newsHeadlines": {
                    "AAPL": {"items": [{"symbol": "AAPL", "title": "New", "url": "https://example.test/new"}]},
                },
            },
        )

        event = verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())
        entries = {
            entry["symbol"]: entry
            for entry in durable_mailbox_entries(event)
        }

        self.assertEqual(["ResearchEvidence"], event.payload["factTypesBySymbol"]["AAPL"])
        self.assertEqual(["MarketQuote"], event.payload["factTypesBySymbol"]["MSFT"])
        self.assertEqual("ResearchEvidence", entries["AAPL"]["factFamily"])
        self.assertEqual("MarketQuote", entries["MSFT"]["factFamily"])

    def test_crypto_transition_targets_direct_assets_and_sensitive_positions_only(self):
        previous = snapshot(external_signals={
            "cryptoFreshness": {"status": "fresh", "fetchedAt": "2026-07-29T00:00:00Z"},
            "cryptoMarkets": {
                "bitcoin": {"symbol": "BTC", "change24h": -1.0, "change7d": 0.0},
            },
        })
        current = snapshot(external_signals={
            "cryptoFreshness": {"status": "fresh", "fetchedAt": "2026-07-29T00:10:00Z"},
            "cryptoMarkets": {
                "bitcoin": {"symbol": "BTC", "change24h": -3.2, "change7d": 0.0},
            },
        })
        for item in (previous, current):
            item.positions.append(Position(
                symbol="MSTR",
                name="Strategy",
                market="US",
                currency="USD",
                quantity=1.0,
                current_price=100.0,
                source="holding",
            ))

        event = verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())

        self.assertEqual(["BTC", "MSTR"], event.payload["symbols"])
        self.assertNotIn("AAPL", event.payload["symbols"])
        self.assertNotIn("MSFT", event.payload["symbols"])
        self.assertEqual(["BTC", "MSTR"], event.payload["verifiedSourceSnapshot"]["cryptoTransitionTargetSymbols"])
        self.assertEqual("down", event.payload["verifiedSourceSnapshot"]["cryptoTransitions"][0]["direction"])

    def test_steady_crypto_band_does_not_enqueue_another_reasoning_request(self):
        previous = snapshot(external_signals={
            "cryptoFreshness": {"status": "fresh", "fetchedAt": "2026-07-29T00:00:00Z"},
            "cryptoMarkets": {"bitcoin": {"symbol": "BTC", "change24h": 3.2}},
        })
        current = snapshot(external_signals={
            "cryptoFreshness": {"status": "fresh", "fetchedAt": "2026-07-29T00:10:00Z"},
            "cryptoMarkets": {"bitcoin": {"symbol": "BTC", "change24h": 4.8}},
        })

        self.assertIsNone(verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state()))

    def test_snapshot_barrier_reuses_the_latest_realtime_slot_from_raw_kis_ticks(self):
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

        barrier_entry = next(
            entry for entry in durable_mailbox_entries(barrier)
            if entry["mailboxSlotFamily"] == REALTIME_LATEST_STATE_SLOT
        )
        raw_entries = durable_mailbox_entries(raw)
        raw_entry = next(
            entry for entry in raw_entries
            if entry["mailboxSlotFamily"] == REALTIME_LATEST_STATE_SLOT
        )

        self.assertEqual(REALTIME_LATEST_STATE_SLOT, barrier_entry["mailboxSlotFamily"])
        self.assertEqual(REALTIME_LATEST_STATE_SLOT, raw_entry["mailboxSlotFamily"])
        self.assertEqual(barrier_entry["mailboxKey"], raw_entry["mailboxKey"])
        self.assertEqual(2, len(raw_entries))
        self.assertIn(
            REALTIME_LATEST_STATE_SLOT + ":flow",
            {entry["mailboxSlotFamily"] for entry in raw_entries},
        )

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

    def test_queue_status_counts_verified_snapshot_work_without_a_key_error(self):
        barrier = verified_monitor_snapshot_reasoning_event(snapshot())
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=None,
            monitor_runner_factory=lambda: None,
        )

        dispatch = runner.queue_dispatch_summary([barrier], selected_requests=[barrier], selected_symbols=["AAPL"])

        self.assertEqual(1, dispatch["pendingByClass"]["PORTFOLIO"])
        self.assertEqual(1, dispatch["selectedByClass"]["PORTFOLIO"])

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
