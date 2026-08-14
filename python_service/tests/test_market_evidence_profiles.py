import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.investment_outcome_observation_service import InvestmentOutcomeObservationService
from digital_twin.domain.decision_follow_up import (
    evaluate_follow_up_conditions,
    normalize_follow_up_conditions,
)
from digital_twin.domain.market_evidence_profiles import market_evidence_profile
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position
from digital_twin.domain.portfolio_ontology_market_concepts import add_market_evidence_profile_concepts
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_schema import add_entity


def position(symbol, market, currency, **values):
    payload = {
        "symbol": symbol,
        "name": values.pop("name", symbol),
        "market": market,
        "currency": currency,
        "current_price": 100.0,
        "ma5": 98.0,
        "ma20": 95.0,
        "ma60": 90.0,
        "volume": 1200.0,
        "volume_ratio": 1.2,
        "updated_at": "2026-08-15T01:00:00Z",
        "source_as_of": "2026-08-15T01:00:00Z",
        "freshness_status": "fresh",
    }
    payload.update(values)
    return Position(**payload)


class MarketEvidenceProfileTests(unittest.TestCase):
    def test_kr_equity_uses_current_microstructure_confirmation(self):
        item = position(
            "005930",
            "KR",
            "KRW",
            trade_strength=108.0,
            market_signal_coverage={
                "ccnl": {
                    "observedFields": ["volume", "volumeRatio", "tradeStrength"],
                    "judgementEvidenceUsable": True,
                    "aiUsableAsStrongEvidence": True,
                }
            },
        )

        profile = market_evidence_profile(item)

        self.assertEqual("KR_EQUITY", profile["profileKey"])
        self.assertEqual("sufficient", profile["dataState"])
        self.assertEqual("fresh", profile["capabilities"]["tradeFlow"]["state"])

    def test_stale_quote_cannot_be_promoted_by_fallback_volume(self):
        item = position("NVDA", "NASDAQ", "USD", freshness_status="stale")

        profile = market_evidence_profile(item)

        self.assertEqual("partial", profile["dataState"])
        self.assertEqual("stale", profile["capabilities"]["pricePath"]["state"])
        self.assertEqual("stale", profile["capabilities"]["volume"]["state"])
        self.assertNotIn("currentPrice", profile["observableFollowUpFields"])

    def test_adr_does_not_require_unsupported_kr_flow_fields(self):
        item = position("SKHY", "NASDAQ", "USD", name="SK하이닉스 ADR")

        profile = market_evidence_profile(item)

        self.assertEqual("ADR", profile["profileKey"])
        self.assertEqual("sufficient", profile["dataState"])
        self.assertEqual("fresh", profile["capabilities"]["crossListingIdentity"]["state"])
        for capability in ("tradeFlow", "orderBook", "investorFlow"):
            self.assertEqual("providerUnsupported", profile["capabilities"][capability]["state"])
        self.assertIn("volumeRatio", profile["observableFollowUpFields"])
        self.assertNotIn("foreignNetVolume", profile["observableFollowUpFields"])

    def test_adr_abox_keeps_profile_and_only_degraded_capability_nodes(self):
        item = position("SKHY", "NASDAQ", "USD", name="SK하이닉스 ADR")
        graph = PortfolioOntology("account-1")
        stock_id = add_entity(graph, "stock", "SKHY", item.name, {"tboxClass": "Stock"})

        add_market_evidence_profile_concepts(graph, stock_id, item, "holding")

        profiles = [entity for entity in graph.entities if entity.kind == "market-evidence-profile"]
        availability = [entity for entity in graph.entities if entity.kind == "data-availability-assessment"]
        self.assertEqual(1, len(profiles))
        self.assertEqual("sufficient", profiles[0].properties["dataState"])
        self.assertEqual(4, len(availability))
        self.assertNotIn("fresh", {entity.properties["dataState"] for entity in availability})
        self.assertIn("HAS_EVIDENCE_PROFILE", {relation.relation_type for relation in graph.relations})

    def test_follow_up_only_tracks_observable_adr_fields(self):
        item = position("SKHY", "NASDAQ", "USD")
        profile = market_evidence_profile(item)
        facts = {
            "symbol": "SKHY",
            "updatedAt": "2026-08-15T01:00:00Z",
            "volumeRatio": 1.2,
            "foreignNetVolume": 0,
            "marketEvidenceProfile": profile,
        }
        raw = [
            {"field": "volumeRatio", "operator": ">=", "threshold": 1.5, "purpose": "strengthen", "label": "거래량 확인"},
            {"field": "foreignNetVolume", "operator": ">", "threshold": 0, "purpose": "strengthen", "label": "외국인 수급 확인"},
        ]

        tracked, unsupported = normalize_follow_up_conditions(raw, facts, "SKHY")

        self.assertEqual(["volumeRatio"], [item["field"] for item in tracked])
        self.assertEqual("pending", tracked[0]["status"])
        self.assertEqual(["foreignNetVolume"], [item["field"] for item in unsupported])
        self.assertEqual("unobservable", unsupported[0]["status"])
        updated, material = evaluate_follow_up_conditions(
            tracked,
            {**facts, "volumeRatio": 1.6},
            "2026-08-15T01:05:00Z",
        )
        self.assertTrue(material)
        self.assertEqual("satisfied", updated[0]["status"])

        invalidating, material = evaluate_follow_up_conditions(
            [{**tracked[0], "status": "pending", "purpose": "invalidate"}],
            {**facts, "volumeRatio": 1.6},
            "2026-08-15T01:06:00Z",
        )
        self.assertTrue(material)
        self.assertEqual("invalidated", invalidating[0]["status"])

    def test_snapshot_observation_advances_follow_up_without_second_inference_cycle(self):
        class EpisodeStore:
            def __init__(self):
                self.observations = []

            def pending_outcome_targets(self, account_id, observed_at, limit=0):
                return []

            def record_outcome_observations(self, account_id, records):
                return []

            def evaluate_follow_up_observation(self, account_id, symbol, facts, observed_at):
                self.observations.append((account_id, symbol, facts, observed_at))
                return [{"conditionId": "follow-up-1", "status": "satisfied"}]

        class TimeSeriesStore:
            def load_outcome_observations(self, account_id, targets, max_delay_minutes=0):
                return {}

        snapshot = AccountSnapshot(
            "account-1",
            "테스트",
            "toss",
            "live",
            "ok",
            "2026-08-15T01:05:00Z",
            PortfolioSummary(1000, 1000, 0, [], [], 0),
            positions=[position("SKHY", "NASDAQ", "USD")],
        )
        store = EpisodeStore()

        result = InvestmentOutcomeObservationService(store, TimeSeriesStore()).observe_snapshot(snapshot)

        self.assertEqual("follow-up-observed", result["status"])
        self.assertEqual(1, result["followUpObservation"]["transitionCount"])
        self.assertEqual("ADR", store.observations[0][2]["marketEvidenceProfile"]["profileKey"])


if __name__ == "__main__":
    unittest.main()
