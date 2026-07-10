import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_quality import build_ontology_quality_sample
from digital_twin.domain.ontology_schema import abox_properties, abox_relation_properties
from digital_twin.domain.ontology_validator import validate_ontology
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
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
