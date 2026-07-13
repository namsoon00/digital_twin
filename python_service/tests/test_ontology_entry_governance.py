import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.investment_research import build_active_investment_opinion
from digital_twin.domain.ontology_relation_facts import position_signal_facts
from digital_twin.domain.ontology_relation_reasoning import evaluate_position_relation_rules
from digital_twin.domain.portfolio import Position
from digital_twin.domain.portfolio_calculations import portfolio_summary


class OntologyEntryGovernanceTests(unittest.TestCase):
    def test_watchlist_near_20_day_average_waits_when_macro_volume_and_60_day_are_not_ready(self):
        nvda = Position(
            symbol="NVDA",
            name="NVIDIA",
            market="US",
            currency="USD",
            current_price=201.84,
            ma5=201.7,
            ma20=201.5,
            ma60=208.2,
            volume=258773,
            volume_ratio=0.0,
            source="watchlist",
            sector="반도체",
        )
        external_signals = {
            "macro": {
                "series": {
                    "DGS10": {"provider": "FRED", "value": 4.55},
                    "DGS2": {"provider": "FRED", "value": 4.21},
                },
                "yieldSpread10y2y": 0.34,
            },
            "fxRates": {
                "USDKRW": {"provider": "RuntimeSettings", "base": "USD", "quote": "KRW", "rate": 1400}
            },
            "newsHeadlines": {
                "NVDA": {
                    "items": [
                        {"title": "NVIDIA institutional ownership update", "source": "MarketBeat"}
                    ],
                    "count": 1,
                }
            },
        }

        context = evaluate_position_relation_rules(nvda, portfolio_summary([]), external_signals=external_signals)
        active_ids = [item.get("rule_id") or item.get("ruleId") for item in context["activeRules"]]
        opinion = build_active_investment_opinion(nvda, context, external_signals=external_signals)

        self.assertIn("entry.wait_for_confirmation.v1", active_ids)
        self.assertNotIn("entry.momentum.confirmed.v1", active_ids)
        self.assertNotIn("entry.pullback.supported.v1", active_ids)
        self.assertEqual("신규 진입 대기", context["decision"]["label"])
        self.assertEqual("entryWait", context["decision"]["actionGroup"])
        self.assertTrue(context["facts"]["entryMacroBlocked"])
        self.assertIn("거래량 확인 부족", context["facts"]["entryBlockReasons"])
        self.assertEqual("AVOID", opinion.action)

    def test_watchlist_entry_needs_5_20_60_day_volume_and_macro_fx_clearance(self):
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
        external_signals = {
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
        }

        context = evaluate_position_relation_rules(candidate, portfolio_summary([]), external_signals=external_signals)
        active_ids = [item.get("rule_id") or item.get("ruleId") for item in context["activeRules"]]
        opinion = build_active_investment_opinion(candidate, context, external_signals=external_signals)

        self.assertIn("entry.momentum.confirmed.v1", active_ids)
        self.assertEqual("entry", context["decision"]["actionGroup"])
        self.assertTrue(context["facts"]["entryMa5TimingOk"])
        self.assertTrue(context["facts"]["entryMomentumTrendReady"])
        self.assertFalse(context["facts"]["entryMacroBlocked"])
        self.assertFalse(context["facts"]["entryFxBlocked"])
        self.assertEqual("BUY", opinion.action)

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


if __name__ == "__main__":
    unittest.main()
