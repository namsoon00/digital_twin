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
from digital_twin.domain.crypto_market_signals import (
    CRYPTO_TRANSITION_BASELINE_METADATA_KEY,
    crypto_transition_baseline,
)
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

    def test_portfolio_cash_change_does_not_fan_out_immaterial_symbol_turns(self):
        previous = snapshot()
        current = snapshot(aapl_price=100.2, msft_price=200.2)
        current.portfolio.cash = 325.0

        event = verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())

        self.assertIsNone(event)

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

    def test_company_knowledge_section_revision_routes_only_changed_fact_family(self):
        base_company = {
            "AAPL": {
                "schemaVersion": "company-knowledge-v1",
                "symbol": "AAPL",
                "factRevision": "revision-a",
                "materialRevision": "material-a",
                "materialSectionRevisions": {
                    "profile": "profile-a",
                    "valuation": "valuation-a",
                    "financials": "financials-a",
                    "governance": "governance-a",
                    "ownership": "ownership-a",
                    "capital": "capital-a",
                    "coverage": "coverage-a",
                },
                "financials": {"annual": [{"period": "2025-09-30", "revenueGrowthPct": 5.0}]},
            },
        }
        changed_company = copy.deepcopy(base_company)
        changed_company["AAPL"]["factRevision"] = "revision-b"
        changed_company["AAPL"]["materialRevision"] = "material-b"
        changed_company["AAPL"]["materialSectionRevisions"]["financials"] = "financials-b"
        changed_company["AAPL"]["financials"]["annual"][0]["revenueGrowthPct"] = 8.0

        event = verified_monitor_snapshot_reasoning_event(
            snapshot(external_signals={"companyKnowledge": changed_company}),
            snapshot(external_signals={"companyKnowledge": base_company}).to_monitor_state(),
        )

        self.assertEqual(["FinancialFact"], event.payload["factTypesBySymbol"]["AAPL"])
        self.assertEqual(
            ["external.companyKnowledge.financials"],
            event.payload["changedFieldsBySymbol"]["AAPL"],
        )
        self.assertEqual(
            ["fundamental"],
            event.payload["factChangeContract"]["scopeFamiliesBySymbol"]["AAPL"],
        )

    def test_official_listing_change_routes_as_company_profile_only(self):
        base_company = {
            "AAPL": {
                "schemaVersion": "company-knowledge-v1",
                "symbol": "AAPL",
                "materialRevision": "material-a",
                "materialSectionRevisions": {
                    "identity": "identity-a",
                    "profile": "profile-a",
                    "listing": "listing-a",
                    "relationships": "relationships-a",
                    "valuation": "valuation-a",
                    "financials": "financials-a",
                    "governance": "governance-a",
                    "ownership": "ownership-a",
                    "capital": "capital-a",
                    "coverage": "coverage-a",
                },
                "listing": {"market": "NASDAQ"},
            },
        }
        changed_company = copy.deepcopy(base_company)
        changed_company["AAPL"]["materialRevision"] = "material-b"
        changed_company["AAPL"]["materialSectionRevisions"]["listing"] = "listing-b"
        changed_company["AAPL"]["listing"]["shareClassName"] = "Common Stock"

        event = verified_monitor_snapshot_reasoning_event(
            snapshot(external_signals={"companyKnowledge": changed_company}),
            snapshot(external_signals={"companyKnowledge": base_company}).to_monitor_state(),
        )

        self.assertEqual(["CompanyProfile"], event.payload["factTypesBySymbol"]["AAPL"])
        self.assertEqual(
            ["external.companyKnowledge.listing"],
            event.payload["changedFieldsBySymbol"]["AAPL"],
        )
        self.assertEqual(
            ["profile"],
            event.payload["factChangeContract"]["scopeFamiliesBySymbol"]["AAPL"],
        )

    def test_official_corporate_action_change_routes_capital_and_evidence(self):
        previous = snapshot(external_signals={"corporateActions": {"AAPL": {}}})
        current = snapshot(external_signals={
            "corporateActions": {
                "AAPL": {
                    "issue-1": {
                        "eventId": "issue-1",
                        "eventType": "equity-issuance",
                        "eventLifecycleState": "upcoming",
                        "issuedShareCount": 1000,
                    },
                },
            },
        })

        event = verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())

        self.assertEqual(
            ["CapitalStructureChange", "ResearchEvidence"],
            event.payload["factTypesBySymbol"]["AAPL"],
        )
        self.assertEqual(
            ["external.corporateActions"],
            event.payload["changedFieldsBySymbol"]["AAPL"],
        )
        self.assertEqual(
            ["capital", "evidence"],
            event.payload["factChangeContract"]["scopeFamiliesBySymbol"]["AAPL"],
        )

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
        contract = event.payload["factChangeContract"]
        self.assertEqual(
            ["kind:crypto-exposure", "kind:crypto-market-signal"],
            contract["dependencyKeysBySymbol"]["BTC"],
        )
        self.assertEqual(
            ["kind:crypto-exposure", "kind:crypto-market-signal"],
            contract["dependencyKeysBySymbol"]["MSTR"],
        )
        self.assertNotIn(
            "kind:stock:field:cryptomarkets",
            contract["dependencyKeys"],
        )

    def test_missing_crypto_baseline_bootstraps_an_existing_threshold_move_once(self):
        previous = snapshot(external_signals={
            "cryptoFreshness": {"status": "fresh", "fetchedAt": "2026-07-29T00:00:00Z"},
            "cryptoMarkets": {"bitcoin": {"symbol": "BTC", "change24h": 6.2}},
        })
        current = snapshot(external_signals={
            "cryptoFreshness": {"status": "fresh", "fetchedAt": "2026-07-29T00:10:00Z"},
            "cryptoMarkets": {"bitcoin": {"symbol": "BTC", "change24h": 6.2}},
        })

        event = verified_monitor_snapshot_reasoning_event(current, previous.to_monitor_state())

        self.assertEqual(["BTC"], event.payload["symbols"])
        transition = event.payload["verifiedSourceSnapshot"]["cryptoTransitions"][0]
        self.assertEqual("threshold-crossed", transition["transition"])
        self.assertEqual("major", transition["severity"])

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
