import unittest

from digital_twin.domain.ontology_contracts import OntologyEvidence, PortfolioOntology
from digital_twin.domain.ontology_projection_fingerprint import material_graph_fingerprint


class OntologyProjectionFingerprintContractTests(unittest.TestCase):
    def test_fingerprint_uses_evidence_role_and_data_state(self):
        graph = PortfolioOntology("fingerprint-contract")
        evidence = OntologyEvidence(
            "evidence:contract",
            "stock:005930",
            "data-quality",
            "test",
            "시장 데이터 수집 상태",
            {"ontologyBox": "ABox"},
        )
        evidence.evidence_role = "risk"
        evidence.data_state = "partial"
        graph.evidence.append(evidence)

        fingerprint = material_graph_fingerprint(graph)

        self.assertEqual(64, len(fingerprint))


if __name__ == "__main__":
    unittest.main()
