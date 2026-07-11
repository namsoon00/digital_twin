import unittest

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from digital_twin.infrastructure.neo4j_ontology import ontology_repository_from_settings
from digital_twin.infrastructure.typedb_ontology import (
    CompositeOntologyGraphRepository,
    NullTypeDBOntologyGraphRepository,
    TypeDBOntologyGraphRepository,
    relation_row_id,
)


class TypeDBOntologyRepositoryTests(unittest.TestCase):
    def test_typedb_schema_defines_nodes_assertions_and_keys(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        schema = repository.schema_query()

        self.assertIn("ontology-node sub entity", schema)
        self.assertIn("ontology-assertion sub relation", schema)
        self.assertIn("owns ontology-id @key", schema)
        self.assertIn("plays ontology-assertion:source", schema)
        self.assertIn("plays ontology-assertion:target", schema)

    def test_typedb_insert_queries_project_same_ontology_graph_shape(self):
        graph = PortfolioOntology("portfolio:test")
        graph.entities.append(OntologyEntity("stock:005930", "삼성전자", "stock", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "tboxClass": "Stock",
        }))
        graph.entities.append(OntologyEntity("signal:005930:risk", "리스크 신호", "risk-signal", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "tboxClass": "RiskSignal",
        }))
        graph.relations.append(OntologyRelation("stock:005930", "signal:005930:risk", "HAS_RISK_SIGNAL", 0.84, properties={
            "ontologyBox": "ABox",
            "ruleId": "risk.test",
        }))
        graph.evidence.append(OntologyEvidence(
            "evidence:005930:risk",
            "stock:005930",
            "market-observation",
            "test",
            "위험 관찰",
            {"ontologyBox": "ABox"},
            0.8,
        ))
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        queries = repository.insert_queries(graph)

        self.assertTrue(any("insert $n isa ontology-entity" in query for query in queries))
        self.assertTrue(any("insert $r (source: $source, target: $target) isa ontology-assertion" in query for query in queries))
        self.assertTrue(any('has ontology-relation-type "HAS_RISK_SIGNAL"' in query for query in queries))
        self.assertTrue(any('has ontology-relation-type "HAS_EVIDENCE"' in query for query in queries))
        self.assertEqual(relation_row_id(repository.rows_for_relations(graph)[0]), relation_row_id(repository.rows_for_relations(graph)[0]))

    def test_typedb_null_repository_is_explicitly_disabled(self):
        result = NullTypeDBOntologyGraphRepository().save_graph(PortfolioOntology("empty"))

        self.assertFalse(result["saved"])
        self.assertEqual("disabled", result["status"])
        self.assertEqual("typedb", result["graphStore"])

    def test_repository_factory_can_build_dual_graph_store_without_disabling_neo4j(self):
        repository = ontology_repository_from_settings({
            "ontologyGraphStoreMode": "dual",
            "ontologyNeo4jEnabled": "1",
            "neo4jUri": "http://127.0.0.1:7474",
            "neo4jUser": "neo4j",
            "neo4jDatabase": "neo4j",
            "neo4jTimeoutSeconds": "8",
            "ontologyTypeDbEnabled": "1",
            "typedbAddress": "127.0.0.1:1729",
            "typedbUser": "admin",
            "typedbPassword": "password",
            "typedbDatabase": "orbit_alpha_ontology",
            "typedbTlsEnabled": "0",
            "typedbTimeoutSeconds": "20",
        })

        self.assertIsInstance(repository, CompositeOntologyGraphRepository)
        self.assertEqual("neo4j", repository.primary.store_key)
        self.assertEqual(["typedb"], [item.store_key for item in repository.mirrors])


if __name__ == "__main__":
    unittest.main()
