import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_quality import build_ontology_quality_sample
from digital_twin.domain.ontology_schema import abox_properties, abox_relation_properties
from digital_twin.domain.ontology_prompting import prompt_payload
from digital_twin.domain.ontology_tbox import tbox_class_def, tbox_relation_def
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
        self.assertEqual("ready", quality.payload["states"]["validation"])
        self.assertNotIn("scores", quality.payload)

    def test_portfolio_ontology_records_coverage_gap_as_abox_fact(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            market_value=1000000,
            quantity=10,
            current_price=100000,
            ma20=104000,
            ma60=99000,
            sector="반도체",
        )
        graph = build_portfolio_ontology(
            [position],
            portfolio_summary([position], fx_rates={"KRW": 1}),
        )

        coverage_gaps = [item for item in graph.entities if item.kind == "coverage-gap"]
        coverage_relations = [item for item in graph.relations if item.relation_type == "HAS_COVERAGE_GAP"]

        self.assertTrue(coverage_gaps)
        self.assertIn("externalEvidence", coverage_gaps[0].properties["missingCategories"])
        self.assertTrue(coverage_relations)
        self.assertEqual("CoverageGap", coverage_gaps[0].properties["tboxClass"])
        self.assertEqual("valid", validate_ontology(graph).status)

    def test_portfolio_ontology_materializes_crypto_macro_and_valuation_contexts(self):
        position = Position(
            symbol="MSTR",
            name="Strategy",
            market="NASDAQ",
            currency="USD",
            market_value=5000,
            quantity=5,
            average_price=900,
            current_price=980,
            profit_loss_rate=8.9,
            ma20=940,
            ma60=870,
            volume=1200000,
            volume_ratio=1.4,
            trading_value=900000000,
            sector="디지털자산",
        )
        graph = build_portfolio_ontology(
            [position],
            portfolio_summary([position], fx_rates={"USD": 1400, "KRW": 1}),
            external_signals={
                "cryptoMarkets": {
                    "bitcoin": {
                        "provider": "CoinGecko",
                        "symbol": "BTC",
                        "name": "Bitcoin",
                        "price": 120000,
                        "marketCap": 2200000000000,
                        "volume24h": 45000000000,
                        "change1h": 1.2,
                        "change24h": -5.5,
                        "change7d": -11.0,
                    }
                },
                "macro": {
                    "series": {
                        "DGS10": {"provider": "FRED", "value": 4.75},
                        "DGS2": {"provider": "FRED", "value": 4.55},
                    },
                    "yieldSpread10y2y": 0.20,
                },
                "companyOverviews": {
                    "MSTR": {
                        "provider": "Alpha Vantage",
                        "name": "Strategy",
                        "sector": "Technology",
                        "industry": "Software",
                        "marketCapitalization": 10000000000,
                        "revenueTTM": 500000000,
                        "peRatio": 65,
                        "beta": 1.8,
                    }
                },
            },
        )

        kinds = {item.kind for item in graph.entities}
        relation_types = {item.relation_type for item in graph.relations}
        inference_relations = [
            item
            for item in graph.relations
            if (item.properties or {}).get("ontologyBox") == "InferenceBox"
        ]

        self.assertIn("crypto-asset", kinds)
        self.assertIn("price-path", kinds)
        self.assertIn("macro-regime", kinds)
        self.assertIn("crypto-exposure", kinds)
        self.assertIn("valuation-assumption", kinds)
        self.assertIn("valuation-model", kinds)
        self.assertNotIn("active-valuation", kinds)
        self.assertNotIn("valuation-review", kinds)
        self.assertIn("HAS_CRYPTO_EXPOSURE", relation_types)
        self.assertIn("HAS_MACRO_REGIME", relation_types)
        self.assertIn("HAS_VALUATION", relation_types)
        self.assertNotIn("HAS_ACTIVE_VALUATION", relation_types)
        self.assertNotIn("AWAITS_USER_REVIEW", relation_types)
        self.assertEqual([], inference_relations)
        self.assertEqual("abox-facts-only-typedb-native-rules", graph.worldview["runtimeProjectionMode"])
        self.assertEqual("valid", validate_ontology(graph).status)

    def test_runtime_valuation_assumption_materializes_fair_value_and_margin(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            market_value=800000,
            quantity=10,
            current_price=80000,
            ma20=79000,
            ma20_distance=1.3,
            volume=1200000,
            volume_ratio=1.1,
            sector="반도체",
            source="watchlist",
        )
        graph = build_portfolio_ontology(
            [position],
            portfolio_summary([], account_cash=1000000, fx_rates={"KRW": 1}),
            runtime_context={
                "settings": {
                    "valuationAssumptions": {
                        "005930": {
                            "expectedEPS": 9000,
                            "targetPER": 11,
                            "minimumMarginOfSafetyPct": 20,
                            "formula": "적정가 = 예상 EPS x 목표 PER",
                        }
                    }
                }
            },
        )

        kinds = {item.kind for item in graph.entities}
        relation_types = {item.relation_type for item in graph.relations}
        margin = next(item for item in graph.entities if item.kind == "margin-of-safety")
        prompt = prompt_payload(graph)

        self.assertIn("valuation-metric", kinds)
        self.assertIn("fair-value-estimate", kinds)
        self.assertIn("margin-of-safety", kinds)
        self.assertIn("HAS_VALUATION_METRIC", relation_types)
        self.assertIn("HAS_FAIR_VALUE_ESTIMATE", relation_types)
        self.assertIn("HAS_MARGIN_OF_SAFETY", relation_types)
        self.assertEqual(23.75, margin.properties["marginOfSafetyPct"])
        self.assertTrue(prompt["valuationContext"])
        self.assertEqual("valid", validate_ontology(graph).status)

    def test_ai_preferred_income_valuation_materializes_as_proposal(self):
        position = Position(
            symbol="STRC",
            name="스트래티지 스트레치 우선주(9.00%)",
            market="NASDAQ",
            currency="USD",
            market_value=2106,
            quantity=24,
            current_price=87.76,
            ma20=85.7,
            ma60=94.1,
            sector="디지털자산",
        )
        graph = build_portfolio_ontology(
            [position],
            portfolio_summary([position], fx_rates={"USD": 1400, "KRW": 1}),
            external_signals={
                "macro": {
                    "series": {
                        "DGS10": {"provider": "FRED", "value": 4.5},
                    },
                },
            },
        )

        relation_types = {item.relation_type for item in graph.relations}
        ai_assumptions = [
            item
            for item in graph.entities
            if item.kind == "valuation-assumption" and "AIValuationProposal" in (item.properties.get("tboxClasses") or [])
        ]

        self.assertTrue(ai_assumptions)
        self.assertIn("HAS_AI_VALUATION_PROPOSAL", relation_types)
        self.assertIn("HAS_FAIR_VALUE_ESTIMATE", relation_types)
        self.assertIn("HAS_MARGIN_OF_SAFETY", relation_types)
        self.assertEqual("AIValuationProposal", ai_assumptions[0].properties["tboxClass"])
        self.assertEqual("valid", validate_ontology(graph).status)

    def test_tbox_contains_valuation_and_ai_decision_contracts(self):
        for class_name in [
            "DecisionStage",
            "InvestmentOpinion",
            "PeerContext",
            "AIValuationProposal",
            "ValuationMetric",
            "FairValueEstimate",
            "MarginOfSafety",
            "ValuationRisk",
            "UndervaluationOpportunity",
        ]:
            self.assertIsNotNone(tbox_class_def(class_name), class_name)

        for relation_type in [
            "PRODUCES_AI_DECISION",
            "HAS_AI_VALUATION_PROPOSAL",
            "HAS_VALUATION_METRIC",
            "HAS_FAIR_VALUE_ESTIMATE",
            "HAS_MARGIN_OF_SAFETY",
            "HAS_VALUATION_RISK",
            "HAS_VALUATION_OPPORTUNITY",
        ]:
            self.assertIsNotNone(tbox_relation_def(relation_type), relation_type)

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
