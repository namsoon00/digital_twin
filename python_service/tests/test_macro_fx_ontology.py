import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.ontology_rules import evaluate_position_relation_rules, relation_rule_context_summary_lines
from digital_twin.domain.portfolio import Position
from digital_twin.domain.portfolio_calculations import portfolio_summary


class MacroFxOntologyTests(unittest.TestCase):
    def test_rate_and_fx_relation_rules_are_scored_from_ontology_context(self):
        position = Position(
            symbol="NVDA",
            name="NVIDIA",
            market="US",
            currency="USD",
            market_value=1000,
            current_price=180,
            ma20=175,
            ma60=160,
            sellable_quantity=2,
            sector="반도체",
        )
        context = evaluate_position_relation_rules(
            position,
            portfolio_summary([position], fx_rates={"USD": 1502, "KRW": 1}),
            external_signals={
                "macro": {
                    "series": {
                        "DGS10": {"provider": "FRED", "value": 4.56},
                        "DGS2": {"provider": "FRED", "value": 4.21},
                        "DFF": {"provider": "FRED", "value": 3.62},
                    },
                    "yieldSpread10y2y": 0.35,
                },
                "fxRates": {
                    "USDKRW": {
                        "provider": "RuntimeSettings",
                        "base": "USD",
                        "quote": "KRW",
                        "rate": 1502,
                    }
                },
            },
        )

        active_ids = [item.get("ruleId") or item.get("rule_id") for item in context["activeRules"]]
        summary_lines = relation_rule_context_summary_lines(context)

        self.assertIn("rates.interest_rate.sensitivity.v1", active_ids)
        self.assertIn("fx.usd_krw.exposure.v1", active_ids)
        self.assertTrue(any(line.startswith("금리: 미국10년 4.56%") for line in summary_lines))
        self.assertTrue(any("환율: USD/KRW" in line and "1 USD = 1,502 KRW" in line for line in summary_lines))
        self.assertEqual(1502, context["executionPlan"]["sourceFacts"]["usdKrwRate"])

    def test_fx_relation_rule_does_not_attach_usd_pair_to_krw_holding(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            market_value=1000000,
            current_price=75000,
            ma20=76000,
            ma60=72000,
            sellable_quantity=10,
            sector="반도체",
        )
        context = evaluate_position_relation_rules(
            position,
            portfolio_summary([position], fx_rates={"KRW": 1}),
            external_signals={
                "fxRates": {
                    "USDKRW": {
                        "provider": "RuntimeSettings",
                        "base": "USD",
                        "quote": "KRW",
                        "rate": 1502,
                    }
                }
            },
        )

        active_ids = [item.get("ruleId") or item.get("rule_id") for item in context["activeRules"]]
        summary_lines = relation_rule_context_summary_lines(context)

        self.assertNotIn("fx.usd_krw.exposure.v1", active_ids)
        self.assertFalse(any(line.startswith("환율:") for line in summary_lines))
        self.assertEqual("base_currency_or_unknown", context["facts"]["fxRegime"])


if __name__ == "__main__":
    unittest.main()
