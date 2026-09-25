import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.portfolio.domain.valuation.snapshot import bind_valuation_snapshot, valuation_snapshot_delta
from digital_twin.modules.portfolio.domain.valuation.projection import add_valuation_row_concepts
from digital_twin.modules.portfolio.domain.portfolio import Position
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology


class ValuationSnapshotTests(unittest.TestCase):
    def row(self):
        reference = {
            "datasetId": "analyst.consensus",
            "providerId": "licensed-provider",
            "subjectKey": "TEST",
            "revisionId": "r1",
            "payloadHash": "hash-r1",
            "fetchedAt": "2026-09-25T01:10:00Z",
        }
        return {
            "valuationModelId": "earnings-multiple",
            "valuationMethod": "eps-per",
            "modelVersion": "v1",
            "valuationCurrency": "USD",
            "valuationAsOf": "2026-09-25T01:00:00Z",
            "valuationInputState": "sufficient",
            "approvalStatus": "approved",
            "valuationDecisionEligible": True,
            "fairValueLow": 36.0,
            "fairValue": 40.0,
            "fairValueHigh": 44.0,
            "inputObservations": [{
                "observationId": "eps:fy1:r1",
                "metric": "earnings-per-share",
                "period": "fy1",
                "base": 2.0,
                "currency": "USD",
                "validationState": "verified",
                "sourceReferences": [reference],
            }],
            "sourceReferences": [reference],
            "multipleBand": {"low": 18, "base": 20, "high": 22, "evidenceBacked": True, "basis": "peer"},
            "formulaTrace": {"formula": "EPS * P/E", "usedObservationIds": ["eps:fy1:r1"]},
        }

    def bind(self, row=None, source_snapshot_id="portfolio-snapshot:r1"):
        return bind_valuation_snapshot(
            row or self.row(),
            symbol="TEST",
            security_line="TEST",
            valuation_currency="USD",
            quote_value=42,
            quote_as_of="2026-09-25T01:05:00Z",
            valuation_at="2026-09-25T01:05:00Z",
            knowledge_cutoff_at="2026-09-25T01:00:00Z",
            model_release_id="earnings-multiple",
            source_snapshot_id=source_snapshot_id,
        )

    def test_same_frozen_inputs_have_stable_bundle_and_assessment(self):
        first = self.bind()
        second = self.bind(copy.deepcopy(self.row()))

        self.assertEqual(first["valuationBundleId"], second["valuationBundleId"])
        self.assertEqual(first["valuationAssessmentId"], second["valuationAssessmentId"])
        self.assertEqual(first["valuationMaterialFingerprint"], second["valuationMaterialFingerprint"])
        self.assertEqual("reproducible", first["valuationReproducibilityState"])
        self.assertTrue(first["valuationDecisionEligible"])
        self.assertEqual("portfolio-snapshot:r1", first["valuationBundle"]["sourceSnapshotId"])

    def test_lineage_only_change_does_not_create_material_change(self):
        first = self.bind()
        changed = self.row()
        changed["sourceReferences"][0]["fetchedAt"] = "2026-09-25T02:10:00Z"
        changed["inputObservations"][0]["sourceReferences"][0]["fetchedAt"] = "2026-09-25T02:10:00Z"
        second = self.bind(changed)
        delta = valuation_snapshot_delta(first, second)

        self.assertEqual(first["valuationMaterialFingerprint"], second["valuationMaterialFingerprint"])
        self.assertNotEqual(first["valuationAuditFingerprint"], second["valuationAuditFingerprint"])
        self.assertEqual("lineage-only-change", delta["state"])
        self.assertFalse(delta["materialChange"])

    def test_source_snapshot_rotation_is_audit_only_when_inputs_are_identical(self):
        first = self.bind(source_snapshot_id="portfolio-snapshot:r1")
        second = self.bind(source_snapshot_id="portfolio-snapshot:r2")
        delta = valuation_snapshot_delta(first, second)

        self.assertEqual(first["valuationBundleId"], second["valuationBundleId"])
        self.assertEqual(first["valuationMaterialFingerprint"], second["valuationMaterialFingerprint"])
        self.assertNotEqual(first["valuationAuditFingerprint"], second["valuationAuditFingerprint"])
        self.assertEqual("lineage-only-change", delta["state"])

    def test_missing_exact_revision_is_reference_only(self):
        row = self.row()
        row["sourceReferences"] = []
        row["inputObservations"][0]["sourceReferences"] = []
        result = self.bind(row)

        self.assertEqual("partial", result["valuationReproducibilityState"])
        self.assertIn("exact-source-revisions-missing", result["valuationReproducibilityGaps"])
        self.assertFalse(result["valuationDecisionEligible"])

    def test_graph_projects_the_same_bundle_and_assessment_identity(self):
        row = self.bind()
        graph = PortfolioOntology("valuation-identity-test")
        add_valuation_row_concepts(
            graph,
            "stock:TEST",
            Position(symbol="TEST", name="Test", market="US", currency="USD", current_price=42),
            row,
        )

        bundle = next(item for item in graph.entities if item.kind == "valuation-input-bundle")
        assessment = next(item for item in graph.entities if item.kind == "valuation-assessment")
        self.assertEqual(row["valuationBundleId"], bundle.properties["valuationBundleId"])
        self.assertEqual(row["valuationAssessmentId"], assessment.properties["valuationAssessmentId"])
        self.assertEqual(row["valuationBundleId"], assessment.properties["valuationBundleId"])
        self.assertTrue(any(
            relation.relation_type == "USES_VALUATION_INPUT" and relation.target == bundle.entity_id
            for relation in graph.relations
        ))


if __name__ == "__main__":
    unittest.main()
