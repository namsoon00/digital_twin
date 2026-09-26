import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.portfolio.domain.valuation.dcf import calculate_driver_dcf, calculate_driver_dcf_sensitivity
from digital_twin.modules.portfolio.domain.valuation.dcf_inputs import build_driver_dcf_input_bundle
from digital_twin.modules.portfolio.domain.valuation.reverse_dcf import solve_implied_revenue_growth
from digital_twin.modules.portfolio.domain.valuation.models import apply_review_override
from digital_twin.modules.news_intelligence.domain.financial_reporting import FINANCIAL_REPORTING_VERSION, bind_financial_report_contract


class DriverDcfTests(unittest.TestCase):
    def inputs(self):
        return {
            "symbol": "TEST",
            "currency": "USD",
            "valuationAt": "2026-01-01T00:00:00Z",
            "knowledgeCutoffAt": "2026-01-01T00:00:00Z",
            "modelApplicability": "non-financial-company",
            "modelApprovalState": "qualified",
            "waccPct": 10,
            "terminalGrowthPct": 0,
            "cash": 10,
            "debt": 0,
            "preferredEquity": 0,
            "nonControllingInterest": 0,
            "nonOperatingAssets": 0,
            "dilutedShares": 10,
            "sbcPolicy": "expense-remains-in-ebit-and-existing-dilution-in-share-count",
            "sourceReferences": [{"datasetId": "sec.company_facts", "revisionId": "r1", "payloadHash": "h1"}],
            "financialEvidence": {"officialDecisionReady": True, "sourceClass": "official-filing"},
            "assumptions": [
                {"id": "wacc", "value": 10, "unit": "percent", "status": "approved"},
                {"id": "terminal-growth", "value": 0, "unit": "percent", "status": "approved"},
            ],
            "projectionYears": [
                {"year": year, "revenue": 100, "ebitMarginPct": 20, "taxRatePct": 25,
                 "depreciationAmortization": 5, "capitalExpenditure": 10,
                 "changeInWorkingCapital": 0, "currency": "USD"}
                for year in range(1, 6)
            ],
        }

    def test_closed_form_fixture_equals_eleven_per_share(self):
        result = calculate_driver_dcf(self.inputs())

        self.assertEqual("calculated", result["status"])
        self.assertAlmostEqual(100.0, result["enterpriseValue"], places=6)
        self.assertAlmostEqual(110.0, result["equityValue"], places=6)
        self.assertAlmostEqual(11.0, result["valuePerShare"], places=6)
        self.assertTrue(result["valuationDecisionEligible"])

    def test_higher_discount_rate_reduces_value_and_cash_only_changes_bridge(self):
        baseline = calculate_driver_dcf(self.inputs())
        higher_wacc_inputs = copy.deepcopy(self.inputs())
        higher_wacc_inputs["waccPct"] = 12
        higher_wacc = calculate_driver_dcf(higher_wacc_inputs)
        more_cash_inputs = copy.deepcopy(self.inputs())
        more_cash_inputs["cash"] = 20
        more_cash = calculate_driver_dcf(more_cash_inputs)

        self.assertLess(higher_wacc["valuePerShare"], baseline["valuePerShare"])
        self.assertAlmostEqual(1.0, more_cash["valuePerShare"] - baseline["valuePerShare"], places=6)
        self.assertAlmostEqual(baseline["enterpriseValue"], more_cash["enterpriseValue"], places=6)
        sensitivity = calculate_driver_dcf_sensitivity(self.inputs())
        self.assertEqual("calculated", sensitivity["status"])
        self.assertEqual(9, len(sensitivity["rows"]))
        self.assertEqual(9, sensitivity["validScenarioCount"])
        base = next(item for item in sensitivity["rows"] if item["isBase"])
        self.assertAlmostEqual(11.0, base["valuePerShare"], places=6)
        self.assertLess(sensitivity["valueRange"]["low"], 11.0)
        self.assertGreater(sensitivity["valueRange"]["high"], 11.0)
        self.assertFalse(sensitivity["independentEvidence"])

    def test_invalid_terminal_and_currency_mismatch_are_blocked(self):
        terminal = self.inputs()
        terminal["terminalGrowthPct"] = 10
        self.assertIn("wacc-not-greater-than-terminal-growth", calculate_driver_dcf(terminal)["blockedReasons"])

        currency = self.inputs()
        currency["projectionYears"][2]["currency"] = "KRW"
        self.assertIn("year-3-currency-mismatch", calculate_driver_dcf(currency)["blockedReasons"])

    def test_unapproved_assumption_stays_reference_only(self):
        inputs = self.inputs()
        inputs["assumptions"][0]["status"] = "candidate"
        result = calculate_driver_dcf(inputs)

        self.assertEqual("calculated", result["status"])
        self.assertFalse(result["valuationDecisionEligible"])
        self.assertIn("unapproved-assumptions-present", result["warnings"])
        row = {
            "symbol": "TEST", "valuationModelFamily": "driver-dcf",
            "approvalStatus": "shadow", "valuationDecisionEligible": False,
            "valuationInputState": "sufficient", "fairValue": 11,
        }

        reviewed = apply_review_override(
            row,
            {"valuationReviewOverrides": "TEST,user_approved,legacy symbol review"},
        )

        self.assertEqual("shadow", reviewed["approvalStatus"])
        self.assertFalse(reviewed["valuationDecisionEligible"])

    def evidence_inputs(self):
        row = bind_financial_report_contract({
            "period": "2025-12-31", "periodEnd": "2025-12-31", "frequency": "annual",
            "provider": "yfinance", "financialReportingVersion": FINANCIAL_REPORTING_VERSION,
            "revenue": 1000, "operatingIncome": 250, "pretaxIncome": 230, "taxProvision": 46,
            "interestExpense": 10, "depreciationAmortization": 40, "capitalExpenditure": -60,
            "changeInWorkingCapital": -20, "stockBasedCompensation": 15, "cash": 100,
            "totalDebt": 80, "weightedAverageSharesDiluted": 100,
            "metricProvenance": {
                metric: {"provider": "yfinance", "currency": "USD", "scope": "consolidated", "durationBasis": "annual"}
                for metric in (
                    "revenue", "operatingIncome", "pretaxIncome", "taxProvision", "interestExpense",
                    "depreciationAmortization", "capitalExpenditure", "changeInWorkingCapital",
                    "stockBasedCompensation", "cash", "totalDebt", "weightedAverageSharesDiluted",
                )
            },
        }, [{"datasetId": "yfinance.fundamental", "subjectKey": "TEST", "revisionId": "fund-r1", "payloadHash": "fund-h1"}])
        lineage = {
            "fund": {"datasetId": "yfinance.fundamental", "subjectKey": "TEST", "revisionId": "fund-r1", "payloadHash": "fund-h1", "fetchedAt": "2026-01-02T00:00:00Z"},
            "analyst": {"datasetId": "yfinance.analyst", "subjectKey": "TEST", "revisionId": "analyst-r1", "payloadHash": "analyst-h1", "fetchedAt": "2026-01-03T00:00:00Z"},
            "macro": {"datasetId": "fred.macro", "subjectKey": "GLOBAL", "revisionId": "macro-r1", "payloadHash": "macro-h1", "fetchedAt": "2026-01-04T00:00:00Z"},
        }
        return {
            "symbol": "TEST", "company": {"financials": {"annual": [row]}},
            "overview": {"marketCapitalization": 5000, "beta": 1.2, "fetchedAt": "2026-01-03T00:00:00Z"},
            "yfinance": {"revenueEstimate": [
                {"period": "0y", "avg": 1100, "numberOfAnalysts": 10},
                {"period": "+1y", "avg": 1210, "numberOfAnalysts": 11},
            ]},
            "macro": {"series": {"DGS10": {"value": 4.0}}}, "lineage": lineage,
        }

    def test_operational_input_builder_preserves_sources_and_requires_assumption_review(self):
        source = self.evidence_inputs()
        built = build_driver_dcf_input_bundle(**source, valuation_at="2026-01-04T00:00:00Z")

        self.assertEqual("ready-for-shadow", built["status"])
        self.assertEqual(3, len(built["sourceReferences"]))
        self.assertEqual("required", built["assumptionReviewState"])
        self.assertEqual("secondary-aggregator", built["financialEvidence"]["sourceClass"])
        self.assertFalse(built["financialEvidence"]["officialDecisionReady"])
        self.assertEqual("exact-input-bundle-and-assumption-version", built["assumptionReview"]["approvalScope"])
        self.assertFalse(built["assumptionReview"]["automaticApprovalAllowed"])
        self.assertEqual(8, built["assumptionReview"]["pendingCount"])
        self.assertEqual("USD", built["input"]["currency"])
        self.assertEqual("DGS10", built["observedInputs"]["riskFreeSeriesId"])
        self.assertEqual("fred.macro", built["observedInputs"]["riskFreeDatasetId"])
        self.assertEqual(22, built["input"]["projectionYears"][0]["changeInWorkingCapital"])
        result = calculate_driver_dcf(built["input"])
        self.assertEqual("calculated", result["status"])
        self.assertFalse(result["valuationDecisionEligible"])
        self.assertIn("official-financial-evidence-incomplete", result["warnings"])
        source = self.evidence_inputs()
        original = source["company"]["financials"]["annual"][0]
        official = dict(original)
        official["provider"] = "SEC EDGAR"
        official["officialSource"] = True
        official["metricProvenance"] = {
            key: {**dict(value), "provider": "SEC EDGAR", "official": True}
            for key, value in original["metricProvenance"].items()
        }
        official.pop("reportContract", None)
        official = bind_financial_report_contract(official, [{
            "datasetId": "sec.company_facts", "subjectKey": "TEST",
            "revisionId": "sec-r1", "payloadHash": "sec-h1",
        }])
        source["company"]["financials"]["annual"] = [official]
        source["lineage"]["sec"] = {
            "datasetId": "sec.company_facts", "subjectKey": "TEST",
            "revisionId": "sec-r1", "payloadHash": "sec-h1",
            "fetchedAt": "2026-01-02T00:00:00Z",
        }

        built = build_driver_dcf_input_bundle(**source)

        self.assertEqual("official-filing", built["financialEvidence"]["sourceClass"])
        self.assertTrue(built["financialEvidence"]["officialDecisionReady"])
        self.assertEqual(12, built["financialEvidence"]["officialMetricCount"])
        self.assertIn("sec.company_facts", {item["datasetId"] for item in built["sourceReferences"]})
        self.assertNotIn("yfinance.fundamental", {item["datasetId"] for item in built["sourceReferences"]})

        source = self.evidence_inputs()
        public_official = source["company"]["financials"]["annual"][0]
        public_official["provider"] = "금융위원회 기업재무정보"
        public_official["currency"] = "KRW"
        public_official["metricProvenance"] = {
            key: {**dict(value), "provider": "금융위원회 기업재무정보", "currency": "KRW", "official": True}
            for key, value in public_official["metricProvenance"].items()
        }
        public_official.pop("reportContract", None)
        source["company"]["financials"]["annual"] = [bind_financial_report_contract(public_official, [{
            "datasetId": "public-data.kr-company-financials", "subjectKey": "TEST",
            "revisionId": "public-r1", "payloadHash": "public-h1",
        }])]
        source["macro"] = {"series": {"KRGB10Y": {"value": 4.392}}}
        source["lineage"]["macro"] = {
            "datasetId": "ecos.macro", "subjectKey": "GLOBAL",
            "revisionId": "ecos-r1", "payloadHash": "ecos-h1",
            "fetchedAt": "2026-01-04T00:00:00Z",
        }
        built = build_driver_dcf_input_bundle(**source)

        self.assertEqual("ready-for-shadow", built["status"])
        self.assertEqual("official-filing", built["financialEvidence"]["sourceClass"])
        self.assertTrue(built["financialEvidence"]["officialDecisionReady"])
        self.assertIn(
            "public-data.kr-company-financials",
            {item["datasetId"] for item in built["sourceReferences"]},
        )

        source = self.evidence_inputs()
        annual = source["company"]["financials"]["annual"][0]
        annual["currency"] = "KRW"
        annual["metricProvenance"] = {
            key: {**dict(value), "currency": "KRW"}
            for key, value in annual["metricProvenance"].items()
        }
        annual.pop("reportContract", None)
        source["company"]["financials"]["annual"] = [bind_financial_report_contract(annual, [{
            "datasetId": "yfinance.fundamental", "subjectKey": "TEST",
            "revisionId": "fund-r1", "payloadHash": "fund-h1",
        }])]
        source["macro"] = {"series": {"KRGB10Y": {
            "value": 4.392, "date": "2026-01-03", "provider": "ECOS",
        }}}
        source["lineage"]["macro"] = {
            "datasetId": "ecos.macro", "subjectKey": "GLOBAL",
            "revisionId": "ecos-r1", "payloadHash": "ecos-h1",
            "fetchedAt": "2026-01-04T00:00:00Z",
        }
        built = build_driver_dcf_input_bundle(
            **source,
            equity_risk_premium_pct_by_currency={"USD": 5.0, "KRW": 6.0},
            terminal_growth_pct_by_currency={"USD": 2.5, "KRW": 3.0},
        )

        self.assertEqual("ready-for-shadow", built["status"])
        self.assertEqual("KRW", built["input"]["currency"])
        self.assertEqual("KRGB10Y", built["observedInputs"]["riskFreeSeriesId"])
        self.assertEqual("ecos.macro", built["observedInputs"]["riskFreeDatasetId"])
        self.assertEqual(4.392, built["observedInputs"]["riskFreeRatePct"])
        self.assertEqual(3.0, built["input"]["terminalGrowthPct"])
        assumptions = {item["id"]: item for item in built["input"]["assumptions"]}
        self.assertEqual(6.0, assumptions["equity-risk-premium"]["value"])
        self.assertEqual("KRW", assumptions["equity-risk-premium"]["currency"])
        source_ids = {item["datasetId"] for item in built["sourceReferences"]}
        self.assertIn("ecos.macro", source_ids)
        self.assertNotIn("fred.macro", source_ids)
        self.assertFalse(calculate_driver_dcf(built["input"])["valuationDecisionEligible"])

        annual["metricProvenance"] = {
            key: {**dict(value), "currency": "EUR"}
            for key, value in annual["metricProvenance"].items()
        }
        annual.pop("reportContract", None)
        source["company"]["financials"]["annual"] = [bind_financial_report_contract(annual, [{
            "datasetId": "yfinance.fundamental", "subjectKey": "TEST",
            "revisionId": "fund-r1", "payloadHash": "fund-h1",
        }])]
        blocked = build_driver_dcf_input_bundle(**source)
        self.assertEqual("blocked", blocked["status"])
        self.assertIn("valuation-currency-not-supported", blocked["missingInputs"])

    def test_operational_input_builder_fails_closed_on_missing_working_capital(self):
        source = self.evidence_inputs()
        source["company"]["financials"]["annual"][0].pop("changeInWorkingCapital")
        built = build_driver_dcf_input_bundle(**source)

        self.assertEqual("blocked", built["status"])
        self.assertIn("working-capital-change-missing", built["missingInputs"])
        self.assertNotIn("input", built)

    def test_shadow_input_can_reverse_solve_a_known_constant_growth_price(self):
        source = self.evidence_inputs()
        built = build_driver_dcf_input_bundle(**source)
        inputs = copy.deepcopy(built["input"])
        for year, row in enumerate(inputs["projectionYears"], start=1):
            row["revenue"] = inputs["baseRevenue"] * (1.1 ** year)
        target = calculate_driver_dcf(inputs)["valuePerShare"]
        solved = solve_implied_revenue_growth(
            built["input"], target_price=target, lower_growth_pct=0, upper_growth_pct=100,
        )

        self.assertEqual("solved", solved["status"])
        self.assertAlmostEqual(10.0, solved["impliedRevenueGrowthPct"], places=4)


if __name__ == "__main__":
    unittest.main()
