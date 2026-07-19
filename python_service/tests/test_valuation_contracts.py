import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.portfolio import Position
from digital_twin.domain.ontology_relation_facts import position_signal_facts
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.valuation_ai_proposals import ai_valuation_proposal_rows
from digital_twin.domain.valuation_contracts import (
    annual_eps_observation,
    fair_value_scenarios,
    valuation_decision_eligible,
)


class ValuationContractTests(unittest.TestCase):
    def test_quarterly_eps_is_not_combined_with_annual_per(self):
        observation = annual_eps_observation(
            {},
            {
                "latestQuarter": {
                    "reportedEPS": 2500,
                    "epsPeriod": "quarterly",
                    "fiscalDateEnding": "2026-06-30",
                }
            },
        )

        self.assertEqual({}, observation)
        self.assertEqual({}, fair_value_scenarios(2500, "quarterly", [8, 12, 16]))

    def test_annual_eps_creates_bear_base_bull_range(self):
        values = fair_value_scenarios(10000, "annual", [8, 12, 16])

        self.assertEqual(68000, values["fairValueLow"])
        self.assertEqual(120000, values["fairValueBase"])
        self.assertEqual(184000, values["fairValueHigh"])

    def test_unreviewed_ai_proposal_cannot_drive_investment_decision(self):
        self.assertFalse(
            valuation_decision_eligible(
                source_type="ai",
                reliability_score=90,
                approval_status="ai_applied_pending_review",
                freshness_status="fresh",
                period_compatible=True,
                fair_value=120000,
            )
        )
        self.assertTrue(
            valuation_decision_eligible(
                source_type="ai",
                reliability_score=90,
                approval_status="user_approved",
                freshness_status="fresh",
                period_compatible=True,
                fair_value=120000,
            )
        )

    def test_semiconductor_ai_valuation_uses_eps_not_moving_average(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            current_price=210000,
            ma5=100000,
            ma20=500000,
            ma60=900000,
        )
        rows = ai_valuation_proposal_rows(
            position,
            {
                "companyOverviews": {
                    "000660": {"provider": "KIS Open API", "trailingEPS": 10000, "epsPeriod": "annual"}
                }
            },
            {},
        )

        self.assertEqual(1, len(rows))
        self.assertEqual(120000, rows[0]["fairValue"])
        self.assertEqual("ai-semiconductor-eps-per-scenarios", rows[0]["valuationMethod"])
        self.assertNotIn("이동평균", rows[0]["formula"])
        self.assertFalse(rows[0]["valuationDecisionEligible"])

    def test_bitcoin_proxy_without_treasury_inputs_has_no_fair_value(self):
        position = Position(symbol="MSTR", name="Strategy", market="US", currency="USD", current_price=100)
        rows = ai_valuation_proposal_rows(
            position,
            {"cryptoMarkets": {"bitcoin": {"price": 65000, "change24h": 2.0}}},
            {},
        )

        self.assertEqual(1, len(rows))
        self.assertEqual(0, rows[0].get("fairValue", 0))
        self.assertIn("BTC 보유량", rows[0]["missingInputs"])

    def test_large_model_disagreement_blocks_valuation_decision(self):
        position = Position(symbol="AAPL", name="Apple", market="US", currency="USD", current_price=100)
        facts = position_signal_facts(
            position,
            portfolio_summary([], account_cash=1000, fx_rates={"USD": 1400}),
            external_signals={
                "companyOverviews": {
                    "AAPL": {
                        "provider": "yfinance",
                        "analystTargetPrice": 300,
                        "fetchedAt": "2026-07-20T00:00:00Z",
                    }
                }
            },
            settings={
                "valuationAssumptions": {
                    "AAPL": {"fairValue": 100, "formula": "사용자 적정가"}
                },
                "aiValuationAutoProposalEnabled": "0",
            },
        )

        self.assertEqual("conflict", facts["valuationConsensusStatus"])
        self.assertEqual(2, facts["valuationModelCount"])
        self.assertFalse(facts["valuationDecisionEligible"])


if __name__ == "__main__":
    unittest.main()
