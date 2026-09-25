import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.portfolio.domain.valuation.dcf import calculate_driver_dcf
from digital_twin.modules.portfolio.domain.valuation.reverse_dcf import solve_implied_revenue_growth


class ReverseDcfTests(unittest.TestCase):
    def inputs(self, growth_pct=8.0):
        base_revenue = 100.0
        return {
            "symbol": "TEST", "currency": "USD", "valuationAt": "2026-01-01T00:00:00Z",
            "quoteAsOf": "2026-01-01T00:00:00Z", "modelApplicability": "non-financial-company",
            "modelApprovalState": "qualified", "baseRevenue": base_revenue,
            "waccPct": 10, "terminalGrowthPct": 2, "cash": 10, "debt": 5,
            "dilutedShares": 10, "sbcPolicy": "expense-in-ebit",
            "sourceReferences": [{"datasetId": "sec.company_facts", "revisionId": "r1"}],
            "assumptions": [{"id": "all", "status": "approved"}],
            "projectionYears": [
                {"year": year, "revenue": base_revenue * ((1 + growth_pct / 100) ** year),
                 "ebitMarginPct": 20, "taxRatePct": 25, "depreciationAmortization": 5,
                 "capitalExpenditure": 10, "changeInWorkingCapital": 1, "currency": "USD"}
                for year in range(1, 6)
            ],
        }

    def test_recovers_known_growth_rate_with_forward_residual(self):
        inputs = self.inputs(8.0)
        target = calculate_driver_dcf(inputs)["valuePerShare"]
        result = solve_implied_revenue_growth(inputs, target_price=target, lower_growth_pct=-10, upper_growth_pct=30)

        self.assertEqual("solved", result["status"])
        self.assertAlmostEqual(8.0, result["impliedRevenueGrowthPct"], places=4)
        self.assertLessEqual(abs(result["residual"]), result["tolerance"])
        self.assertFalse(result["independentEvidence"])

    def test_unbracketed_target_and_invalid_forward_inputs_do_not_invent_solution(self):
        result = solve_implied_revenue_growth(self.inputs(), target_price=10000, lower_growth_pct=-10, upper_growth_pct=20)
        self.assertEqual("blocked", result["status"])
        self.assertIn("target-not-bracketed", result["blockedReasons"])

        invalid = self.inputs()
        invalid["waccPct"] = invalid["terminalGrowthPct"]
        result = solve_implied_revenue_growth(invalid, target_price=10, lower_growth_pct=-10, upper_growth_pct=20)
        self.assertEqual("blocked", result["status"])
        self.assertIn("forward-dcf-invalid-at-bracket", result["blockedReasons"])


if __name__ == "__main__":
    unittest.main()
