import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_quality import build_ontology_quality_sample
from digital_twin.domain.ontology_schema import abox_properties, abox_relation_properties
from digital_twin.domain.ontology_validator import validate_ontology
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.portfolio import Position
from digital_twin.domain.portfolio_calculations import portfolio_summary


class OntologyValidatorTests(unittest.TestCase):
    def test_portfolio_ontology_validates_against_tbox(self):
        position = Position(
            symbol="NVDA",
            name="NVIDIA",
            market="US",
            currency="USD",
            market_value=1000,
            current_price=180,
            ma20=175,
            ma60=160,
            sector="반도체",
        )
        graph = build_portfolio_ontology(
            [position],
            portfolio_summary([position], fx_rates={"USD": 1400, "KRW": 1}),
            external_signals={
                "macro": {
                    "series": {"DGS10": {"provider": "FRED", "value": 4.56}},
                    "yieldSpread10y2y": 0.35,
                },
                "fxRates": {
                    "USDKRW": {
                        "provider": "RuntimeSettings",
                        "base": "USD",
                        "quote": "KRW",
                        "rate": 1400,
                    }
                },
            },
        )

        report = validate_ontology(graph)
        quality = build_ontology_quality_sample(graph)

        self.assertEqual("valid", report.status)
        self.assertEqual(0, report.error_count)
        self.assertEqual("valid", quality.payload["validation"]["status"])
        self.assertEqual(0, quality.payload["scores"]["validationPenalty"])

    def test_account_investment_strategy_profile_is_normalized(self):
        account = AccountConfig.from_dict(
            {
                "id": "namsoon00",
                "label": "테스트 계정",
                "investmentStrategyProfile": "성장형",
            },
            {},
        )

        masked = account.masked()
        context = account.ontology_account_context()

        self.assertEqual("growth", masked["investmentStrategyProfile"])
        self.assertEqual("성장형", masked["investmentStrategyProfileLabel"])
        self.assertEqual("growth", context["investmentStrategyProfile"])
        self.assertEqual(-12, context["investmentStrategy"]["lossTolerancePct"])

    def test_portfolio_ontology_contains_account_strategy_profile(self):
        holding = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            market_value=2500000,
            quantity=10,
            current_price=250000,
            average_price=270000,
            profit_loss_rate=-7.4,
            ma5=252000,
            ma20=260000,
            ma60=240000,
            sector="반도체",
        )
        watch = Position(
            symbol="NVDA",
            name="NVIDIA",
            market="US",
            currency="USD",
            source="watchlist",
            current_price=200,
            ma5=198,
            ma20=195,
            ma60=185,
            sector="반도체",
        )
        graph = build_portfolio_ontology(
            [holding, watch],
            portfolio_summary([holding], fx_rates={"USD": 1400, "KRW": 1}),
            runtime_context={
                "account": {
                    "accountId": "namsoon00",
                    "accountLabel": "테스트 계정",
                    "investmentStrategyProfile": "capitalPreservation",
                },
            },
        )

        relation_types = {item.relation_type for item in graph.relations}
        tbox_classes = {
            str((item.properties or {}).get("tboxClass") or "")
            for item in graph.entities
        }
        strategy_profiles = [
            item
            for item in graph.entities
            if str((item.properties or {}).get("tboxClass") or "") == "InvestmentStrategyProfile"
        ]

        self.assertIn("InvestmentStrategyProfile", tbox_classes)
        self.assertIn("RiskBudget", tbox_classes)
        self.assertIn("ProfitPolicy", tbox_classes)
        self.assertIn("PositionRole", tbox_classes)
        self.assertIn("HAS_INVESTOR_PROFILE", relation_types)
        self.assertIn("USES_INVESTMENT_STRATEGY_PROFILE", relation_types)
        self.assertIn("HAS_POSITION_ROLE", relation_types)
        self.assertIn("HAS_RISK_BUDGET", relation_types)
        self.assertIn("HAS_PROFIT_POLICY", relation_types)
        self.assertTrue(any(item.source == "stock:000660" and item.relation_type == "HAS_RISK_BUDGET" for item in graph.relations))
        self.assertTrue(any(item.source == "stock:000660" and item.relation_type == "HAS_PROFIT_POLICY" for item in graph.relations))
        self.assertTrue(any(item.source == "stock:NVDA" and item.relation_type == "HAS_POSITION_ROLE" for item in graph.relations))
        self.assertTrue(any(item.source == "stock:NVDA" and item.relation_type == "EVALUATED_UNDER_STRATEGY" for item in graph.relations))
        self.assertEqual("capitalPreservation", strategy_profiles[-1].properties["profile"])
        self.assertEqual("valid", validate_ontology(graph).status)

    def test_validator_reports_unknown_relation_and_missing_target(self):
        graph = PortfolioOntology(portfolio_id="broken")
        graph.entities.append(OntologyEntity(
            "stock:ABC",
            "ABC",
            "stock",
            abox_properties({"tboxClass": "Stock"}),
        ))
        graph.relations.append(OntologyRelation(
            "stock:ABC",
            "missing:target",
            "UNKNOWN_RELATION",
            properties=abox_relation_properties("UNKNOWN_RELATION"),
        ))

        report = validate_ontology(graph)
        codes = [item.code for item in report.issues]

        self.assertEqual("invalid", report.status)
        self.assertIn("missing_relation_target", codes)
        self.assertIn("unknown_relation_type", codes)


if __name__ == "__main__":
    unittest.main()
