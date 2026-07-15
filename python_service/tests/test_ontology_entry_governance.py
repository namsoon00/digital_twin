import importlib
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.ontology_relation_facts import position_signal_facts
from digital_twin.domain.portfolio import Position
from digital_twin.domain.portfolio_calculations import portfolio_summary


class OntologyEntryGovernanceTests(unittest.TestCase):
    def test_python_offline_fallback_evaluator_is_physically_removed(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("digital_twin.domain.offline.ontology_relation_fallback_evaluator")

    def test_watchlist_entry_facts_feed_typedb_without_deciding_in_python(self):
        candidate = Position(
            symbol="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            current_price=210,
            ma5=207,
            ma20=205,
            ma60=202,
            volume_ratio=1.3,
            trade_strength=112,
            bid_ask_imbalance=8,
            source="watchlist",
            sector="AI/플랫폼",
        )
        facts = position_signal_facts(
            candidate,
            portfolio_summary([], fx_rates={"USD": 1390, "KRW": 1}),
            external_signals={
                "macro": {
                    "series": {
                        "DGS10": {"provider": "FRED", "value": 4.0},
                        "DGS2": {"provider": "FRED", "value": 3.8},
                    },
                    "yieldSpread10y2y": 0.2,
                },
                "fxRates": {
                    "USDKRW": {"provider": "RuntimeSettings", "base": "USD", "quote": "KRW", "rate": 1390}
                },
            },
        )

        self.assertFalse(facts["isHolding"])
        self.assertGreater(facts["ma5Distance"], 0)
        self.assertGreater(facts["ma20Distance"], 0)
        self.assertGreater(facts["ma60Distance"], 0)
        self.assertEqual(1.3, facts["rawVolumeRatio"])
        self.assertTrue(facts["hasMacroSignals"])
        self.assertTrue(facts["hasFxRateSignal"])
        self.assertNotIn("decision", facts)
        self.assertNotIn("activeRules", facts)

    def test_relation_facts_include_time_adjusted_volume_pace(self):
        candidate = Position(
            symbol="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            current_price=316.18,
            volume=8737438,
            volume_ratio=0.3,
            trading_value=1050906655,
            updated_at="2026-07-13T10:00:00-04:00",
            source="watchlist",
        )

        facts = position_signal_facts(candidate, portfolio_summary([]), external_signals={})

        self.assertEqual(0.3, facts["rawVolumeRatio"])
        self.assertGreater(facts["timeAdjustedVolumeRatio"], facts["rawVolumeRatio"])
        self.assertEqual("regular", facts["volumePaceSession"])
        self.assertIn("미장 정규장", facts["volumePaceSessionLabel"])

    def test_loss_holding_smart_money_facts_are_available_for_typedb_rules(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            quantity=5,
            market_value=8400000,
            profit_loss_rate=-34.7,
            current_price=1681000,
            average_price=2571000,
            ma5=1997600,
            ma20=2437200,
            ma60=2011467,
            ma20_distance=-31.0,
            ma60_distance=-16.4,
            volume_ratio=0.7,
            trade_strength=94,
            bid_ask_imbalance=28.1,
            foreign_net_volume=704671,
            institution_net_volume=781727,
            individual_net_volume=-1440630,
            sector="반도체",
            source="holding",
        )

        facts = position_signal_facts(
            position,
            portfolio_summary([position], account_cash=20000000, fx_rates={"KRW": 1}),
            settings={"investmentStrategyProfile": "growth"},
        )

        self.assertTrue(facts["isHolding"])
        self.assertTrue(facts["jointSmartMoneyInflow"])
        self.assertEqual("FLOW_DEFENSE", facts["addBuyEligibilityStage"])
        self.assertGreater(facts["positionAccountWeight"], 0)
        self.assertNotIn("decision", facts)

    def test_profitable_holding_add_buy_inputs_are_facts_not_python_judgement(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            quantity=4,
            market_value=900000,
            profit_loss_rate=8.5,
            current_price=228000,
            average_price=210000,
            ma5=224000,
            ma20=219000,
            ma60=214000,
            volume_ratio=1.25,
            trade_strength=108,
            bid_ask_imbalance=9,
            sector="반도체",
            source="holding",
        )
        portfolio = portfolio_summary([position], account_cash=12000000, fx_rates={"KRW": 1})

        facts = position_signal_facts(
            position,
            portfolio,
            external_signals={
                "macro": {
                    "series": {
                        "DGS10": {"provider": "FRED", "value": 4.0},
                        "DGS2": {"provider": "FRED", "value": 3.8},
                    },
                    "yieldSpread10y2y": 0.2,
                }
            },
            settings={"investmentStrategyProfile": "balanced"},
        )

        self.assertTrue(facts["isHolding"])
        self.assertGreater(facts["profitLossRate"], 0)
        self.assertGreater(facts["ma5Distance"], 0)
        self.assertGreater(facts["ma20Distance"], 0)
        self.assertGreater(facts["ma60Distance"], 0)
        self.assertGreater(facts["volumeRatio"], 1)
        self.assertNotIn("decision", facts)


if __name__ == "__main__":
    unittest.main()
