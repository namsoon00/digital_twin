import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.news_intelligence.domain.causal_attribution import evaluate_causal_attribution
from digital_twin.modules.news_intelligence.domain.company_drivers import build_company_driver_map
from digital_twin.modules.news_intelligence.domain.company_event import company_event_contract
from digital_twin.modules.news_intelligence.domain.company_knowledge import merge_company_knowledge_rows
from digital_twin.modules.news_intelligence.domain.financial_reporting import FINANCIAL_REPORTING_VERSION, bind_financial_report_contract


class CompanyDriverCausalityTests(unittest.TestCase):
    def company(self, exposures=None):
        row = bind_financial_report_contract({
            "period": "2025-12-31",
            "periodEnd": "2025-12-31",
            "frequency": "annual",
            "provider": "SEC EDGAR",
            "financialReportingVersion": FINANCIAL_REPORTING_VERSION,
            "revenueGrowthPct": 18.0,
            "operatingMarginPct": 24.0,
            "capitalExpenditure": -12.0,
            "metricProvenance": {
                "revenueGrowthPct": {"provider": "SEC EDGAR", "currency": "USD", "scope": "consolidated", "durationBasis": "annual", "tag": "Revenue"},
                "operatingMarginPct": {"provider": "SEC EDGAR", "currency": "USD", "scope": "consolidated", "durationBasis": "annual", "tag": "OperatingIncome"},
                "capitalExpenditure": {"provider": "SEC EDGAR", "currency": "USD", "scope": "consolidated", "durationBasis": "annual", "tag": "PaymentsToAcquireProperty"},
            },
        }, [{
            "datasetId": "sec.company_facts", "providerId": "sec-edgar", "subjectKey": "TEST",
            "revisionId": "sec-r1", "payloadHash": "sec-h1",
        }])
        return {"symbol": "TEST", "financials": {"annual": [row]}, "businessExposures": exposures or []}

    def event(self, announced="2026-09-25T10:00:00Z"):
        return company_event_contract(
            symbol="TEST",
            kind="filing",
            payload={"receiptNo": "filing-1", "eventType": "guidance", "announcedAt": announced},
            title="Annual guidance raised",
            source_references=[{
                "datasetId": "sec.document", "providerId": "sec-edgar", "subjectKey": "TEST",
                "revisionId": "filing-r1", "payloadHash": "filing-h1",
            }],
        )

    def reaction(self, start="2026-09-25T10:05:00Z"):
        return {
            "windowStart": start,
            "windowEnd": "2026-09-25T10:35:00Z",
            "session": "regular",
            "corporateActionAdjusted": True,
            "stockReturnPct": 4.2,
            "benchmarkReturnPct": 0.4,
            "sectorReturnPct": 0.8,
            "abnormalReturnPct": 3.4,
        }

    def test_verified_drivers_require_report_revisions_and_macro_exposure(self):
        without_exposure = build_company_driver_map(
            "TEST", self.company(), macro_context={"series": {"DGS10": {"value": 4.1}}}, fx_rates={"USD/KRW": {"value": 1400}},
        )
        self.assertEqual(3, len(without_exposure["drivers"]))
        self.assertTrue(without_exposure["modelInputEligible"])
        self.assertEqual({"fx-company-impact", "rate-company-impact"}, {item["kind"] for item in without_exposure["unresolved"]})
        self.assertEqual("official-filing", without_exposure["annualReportSourceClass"])
        self.assertEqual("unresolved", without_exposure["exposureReadiness"]["currency"]["status"])
        self.assertEqual("unresolved", without_exposure["exposureReadiness"]["debtRate"]["status"])
        capex = next(item for item in without_exposure["drivers"] if item["modelInput"] == "capital-expenditure")
        self.assertEqual(12.0, capex["value"])
        self.assertEqual(-12.0, capex["reportedValue"])
        self.assertEqual("positive-outflow-magnitude", capex["valueConvention"])

        with_exposure = build_company_driver_map("TEST", self.company([{
            "exposureId": "exp-usd-revenue", "type": "fx-revenue", "currency": "USD", "sharePct": 40,
            "period": "2025", "sourceReferences": [{"datasetId": "sec.segment", "revisionId": "seg-r1"}],
        }]), fx_rates={"USD/KRW": {"value": 1400}})
        self.assertEqual("linked", with_exposure["macroLinks"][0]["state"])
        self.assertEqual("revenue", with_exposure["macroLinks"][0]["impactTarget"])
        self.assertEqual("verified-linked", with_exposure["exposureReadiness"]["currency"]["status"])
        merged = merge_company_knowledge_rows(
            self.company(),
            {"symbol": "TEST", "businessExposures": [{
                "exposureId": "exp-floating-debt", "type": "floating-rate-debt",
                "rateKind": "DFF", "amount": 25, "unit": "USD",
                "sourceReferences": [{"datasetId": "sec.document", "revisionId": "doc-r1"}],
            }]},
        )
        debt_map = build_company_driver_map(
            "TEST", merged, macro_context={"series": {"DFF": {"value": 4.5}}},
        )
        self.assertEqual("exp-floating-debt", merged["businessExposures"][0]["exposureId"])
        self.assertEqual("verified-linked", debt_map["exposureReadiness"]["debtRate"]["status"])

    def test_event_after_reaction_cannot_be_price_cause(self):
        result = evaluate_causal_attribution(
            self.event("2026-09-25T10:00:00Z"),
            self.reaction("2026-09-25T09:30:00Z"),
            driver_map=build_company_driver_map("TEST", self.company()),
            alternative_explanations=[{"kind": "market", "state": "checked"}],
        )

        self.assertFalse(result["priceCauseClaimEligible"])
        self.assertIn("event-after-price-reaction", result["blockingReasons"])
        self.assertEqual("supported-mechanism", result["claimStrength"])

    def test_precise_event_with_benchmarks_is_bounded_causal_hypothesis(self):
        result = evaluate_causal_attribution(
            self.event(), self.reaction(),
            driver_map=build_company_driver_map("TEST", self.company()),
            alternative_explanations=[{"kind": "market", "state": "checked"}],
        )

        self.assertEqual("causal-hypothesis", result["claimStrength"])
        self.assertTrue(result["priceCauseClaimEligible"])
        self.assertTrue(result["valuationImpactSeparateFromPriceCause"])


if __name__ == "__main__":
    unittest.main()
