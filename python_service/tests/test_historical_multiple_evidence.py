import unittest

from digital_twin.modules.portfolio.application.valuation_evidence_service import HistoricalMultipleEvidenceService
from digital_twin.modules.portfolio.domain.valuation.evidence import (
    collect_earnings_observations,
    collect_multiple_observations,
    earnings_scenario,
    multiple_evidence_band,
)
from digital_twin.modules.portfolio.domain.valuation.historical_multiples import (
    build_historical_forward_multiple_observations,
    normalize_current_consensus_contract,
)


def fact(dataset, revision, fetched_at, *, price=None, eps=None):
    symbol = "TEST"
    overview = {
        "provider": "yfinance",
        "symbol": symbol,
        "currency": "USD",
        "fetchedAt": fetched_at,
    }
    if price is not None:
        overview["currentPrice"] = price
    if eps is not None:
        overview["earningsEstimates"] = [{
            "period": "fy1", "base": eps, "provider": "yfinance",
        }]
    return {
        "datasetId": dataset,
        "subjectKey": symbol,
        "providerId": "yfinance",
        "revisionId": revision,
        "sourceRevision": revision + "-source",
        "payloadHash": revision + "-hash",
        "sourceSchemaVersion": dataset + "-v1",
        "availability": "observed",
        "sourceAsOf": fetched_at,
        "fetchedAt": fetched_at,
        "payload": {
            "companyOverviews": {symbol: overview},
            "yfinanceData": {symbol: {"querySymbol": symbol}},
        },
    }


class HistoricalMultipleEvidenceTest(unittest.TestCase):
    def test_point_in_time_join_rejects_future_consensus_and_samples_weekly(self):
        analysts = [
            fact("yfinance.analyst", "a1", "2026-08-31T09:00:00Z", eps=10),
            fact("yfinance.analyst", "a2", "2026-09-07T09:00:00Z", eps=10),
            fact("yfinance.analyst", "a3", "2026-09-14T09:00:00Z", eps=10),
            fact("yfinance.analyst", "future", "2026-09-22T09:00:00Z", eps=20),
        ]
        fundamentals = [
            fact("yfinance.fundamental", "p1", "2026-09-01T10:00:00Z", price=100),
            fact("yfinance.fundamental", "p2", "2026-09-03T10:00:00Z", price=110),
            fact("yfinance.fundamental", "p3", "2026-09-08T10:00:00Z", price=120),
            fact("yfinance.fundamental", "p4", "2026-09-15T10:00:00Z", price=130),
        ]
        rows = build_historical_forward_multiple_observations("TEST", fundamentals, analysts)
        self.assertEqual(3, len(rows))
        self.assertEqual([11.0, 12.0, 13.0], [row["value"] for row in rows])
        self.assertTrue(all(row["earningsAsOf"] <= row["priceAsOf"] for row in rows))
        self.assertTrue(all(len(row["sourceReferences"]) == 2 for row in rows))
        self.assertTrue(all(row["comparabilityState"] == "verified" for row in rows))

    def test_stale_consensus_does_not_create_historical_sample(self):
        rows = build_historical_forward_multiple_observations(
            "TEST",
            [fact("yfinance.fundamental", "p1", "2026-09-30T10:00:00Z", price=100)],
            [fact("yfinance.analyst", "a1", "2026-09-01T09:00:00Z", eps=10)],
            max_analyst_age_days=14,
        )
        self.assertEqual([], rows)

    def test_enriched_evidence_passes_comparability_gate_without_claiming_eps_basis(self):
        analysts = [
            fact("yfinance.analyst", "a1", "2026-08-31T09:00:00Z", eps=10),
            fact("yfinance.analyst", "a2", "2026-09-07T09:00:00Z", eps=10),
            fact("yfinance.analyst", "a3", "2026-09-14T09:00:00Z", eps=10),
        ]
        fundamentals = [
            fact("yfinance.fundamental", "p1", "2026-09-01T10:00:00Z", price=100),
            fact("yfinance.fundamental", "p2", "2026-09-08T10:00:00Z", price=120),
            fact("yfinance.fundamental", "p3", "2026-09-15T10:00:00Z", price=140),
        ]

        class Store:
            def list_revisions(self, **_kwargs):
                return analysts + fundamentals

        overview = normalize_current_consensus_contract({
            "provider": "yfinance", "symbol": "TEST", "currency": "USD",
            "earningsEstimates": [{
                "observationId": "current-fy1", "period": "fy1", "base": 10,
                "provider": "yfinance", "asOf": "2026-09-15T09:00:00Z",
                "sourceReferences": [{"datasetId": "yfinance.analyst", "revisionId": "current"}],
            }],
        }, security_line="TEST")
        signals = HistoricalMultipleEvidenceService(Store()).enrich(
            {"companyOverviews": {"TEST": overview}, "yfinanceData": {"TEST": {"querySymbol": "TEST"}}},
            ["TEST"],
        )
        enriched = signals["companyOverviews"]["TEST"]
        earnings = earnings_scenario(collect_earnings_observations(enriched, {}))
        band = multiple_evidence_band(collect_multiple_observations(enriched, {}), [], earnings=earnings)
        self.assertEqual("provider-reported-unspecified", earnings["epsBasis"])
        self.assertTrue(band["evidenceBacked"])
        self.assertEqual(3, band["sampleCount"])
        self.assertTrue(signals["valuationEvidenceFeeds"]["TEST"]["decisionSampleThresholdMet"])


if __name__ == "__main__":
    unittest.main()
