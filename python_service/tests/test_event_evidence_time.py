import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.evidence_time import event_time_contract
from digital_twin.domain.ontology_contracts import OntologyEntity, PortfolioOntology, entity_id
from digital_twin.domain.ontology_external_abox import add_symbol_external_signal_concepts
from digital_twin.domain.ontology_tbox import CLASS_DEFS, RELATION_DEFS
from digital_twin.infrastructure.external_signal_provider_yfinance import earnings_report_from_yfinance


class EventEvidenceTimeTests(unittest.TestCase):
    def test_event_contract_uses_snapshot_clock_and_expires_old_event(self):
        active = event_time_contract(
            event_kind="earnings",
            effective_at="2026-08-20T00:00:00Z",
            retrieved_at="2026-08-21T00:00:00Z",
            evaluated_at="2026-08-27T00:00:00Z",
        )
        expired = event_time_contract(
            event_kind="earnings",
            effective_at="2020-10-21T00:00:00Z",
            retrieved_at="2026-08-27T00:00:00Z",
            evaluated_at="2026-08-27T00:00:00Z",
        )

        self.assertTrue(active["eventDecisionEligible"])
        self.assertEqual("active", active["eventLifecycleState"])
        self.assertFalse(expired["eventDecisionEligible"])
        self.assertEqual("expired", expired["eventLifecycleState"])
        self.assertEqual("2020-10-21T00:00:00+00:00", expired["sourceAsOf"])
        self.assertEqual("2026-08-27T00:00:00+00:00", expired["sourceFetchedAt"])

    def test_yfinance_selects_latest_completed_report_not_frame_tail_or_future_estimate(self):
        report = earnings_report_from_yfinance("TSLA", {
            "collectedAt": "2026-08-27T00:00:00Z",
            "earningsDates": [
                {"Earnings Date": "2026-10-20T00:00:00Z", "EPS Estimate": 0.9},
                {"Earnings Date": "2026-07-22T00:00:00Z", "Reported EPS": 0.8, "EPS Estimate": 0.7, "Surprise(%)": 14.2},
                {"Earnings Date": "2020-10-21T00:00:00Z", "Reported EPS": 0.3, "EPS Estimate": 0.2, "Surprise(%)": 25.7},
            ],
        })

        self.assertEqual("2026-07-22T00:00:00Z", report["latestQuarter"]["reportedDate"])
        self.assertEqual(14.2, report["latestQuarter"]["surprisePercentage"])

    def test_old_earnings_remains_auditable_but_not_decision_eligible(self):
        stock_id = entity_id("stock", "TSLA")
        graph = PortfolioOntology("account:1", entities=[
            OntologyEntity(stock_id, "Tesla", "stock", {"symbol": "TSLA", "source": "watchlist"}),
        ])
        add_symbol_external_signal_concepts(
            graph,
            stock_id,
            "TSLA",
            {"earningsReports": {"TSLA": {
                "provider": "yfinance",
                "fetchedAt": "2026-08-27T00:00:00Z",
                "latestQuarter": {
                    "fiscalDateEnding": "2020-10-21T00:00:00Z",
                    "reportedDate": "2020-10-21T00:00:00Z",
                    "reportedEPS": 0.3,
                    "estimatedEPS": 0.2,
                    "surprisePercentage": 25.7,
                },
            }}},
            evaluated_at="2026-08-27T00:00:00Z",
        )

        event = next(item for item in graph.entities if item.kind == "earnings-calendar-event")
        validity = next(item for item in graph.entities if item.kind == "event-validity-assessment")
        self.assertEqual("expired", event.properties["eventLifecycleState"])
        self.assertFalse(event.properties["eventDecisionEligible"])
        self.assertTrue(any(item.target == event.entity_id for item in graph.relations))
        self.assertTrue(any(item.source == event.entity_id and item.target == validity.entity_id and item.relation_type == "HAS_EVENT_VALIDITY" for item in graph.relations))

    def test_tbox_exposes_event_time_and_validity_contracts(self):
        self.assertIn("EvidenceTimeContract", {item.name for item in CLASS_DEFS})
        self.assertIn("EventValidityAssessment", {item.name for item in CLASS_DEFS})
        self.assertIn("HAS_EVENT_VALIDITY", {item.name for item in RELATION_DEFS})


if __name__ == "__main__":
    unittest.main()
