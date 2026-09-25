import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.portfolio.domain.valuation.dcf import calculate_driver_dcf


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


if __name__ == "__main__":
    unittest.main()
