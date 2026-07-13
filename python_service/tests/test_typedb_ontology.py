import json
import unittest
from pathlib import Path
from unittest.mock import patch

from digital_twin import service_manager
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from digital_twin.domain.repositories import (
    ONTOLOGY_GRAPH_REPOSITORY_CONTRACT,
    ontology_graph_repository_contract_errors,
)
from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules
from digital_twin.infrastructure.typedb_ontology import (
    NullTypeDBOntologyGraphRepository,
    TypeDBOntologyGraphRepository,
    relation_row_id,
    typedb_native_reasoning_profile,
)


class TypeDBOntologyRepositoryTests(unittest.TestCase):
    def test_typedb_schema_defines_nodes_assertions_and_keys(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        schema = repository.schema_query()

        self.assertIn("entity ontology-node @abstract", schema)
        self.assertIn("relation ontology-assertion", schema)
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
        self.assertTrue(any("insert $r isa ontology-assertion, links (source: $source, target: $target)" in query for query in queries))
        self.assertTrue(any('has ontology-relation-type "HAS_RISK_SIGNAL"' in query for query in queries))
        self.assertTrue(any('has ontology-relation-type "HAS_EVIDENCE"' in query for query in queries))
        self.assertEqual(relation_row_id(repository.rows_for_relations(graph)[0]), relation_row_id(repository.rows_for_relations(graph)[0]))

    def test_typedb_insert_queries_promote_reasoning_fields_to_attributes(self):
        graph = PortfolioOntology("portfolio:typed-fields")
        graph.entities.append(OntologyEntity("stock:005930", "삼성전자", "stock", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "source": "holding",
            "profitLossRate": -12.5,
            "tboxClass": "Stock",
        }))
        graph.entities.append(OntologyEntity("level:005930:ma20", "20일선", "key-level", {
            "ontologyBox": "ABox",
            "symbol": "005930",
            "levelType": "ma20",
            "field": "movingAverage",
            "value": 70000,
        }))
        graph.relations.append(OntologyRelation("stock:005930", "level:005930:ma20", "BREAKS_LEVEL", 0.8, properties={
            "ontologyBox": "ABox",
            "riskImpact": 3.2,
            "polarity": "risk",
            "field": "ma20Distance",
        }))
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        queries = repository.insert_queries(graph)

        self.assertTrue(any('has ontology-source-value "holding"' in query for query in queries))
        self.assertTrue(any("has ontology-profit-loss-rate -12.5" in query for query in queries))
        self.assertTrue(any('has ontology-level-type "ma20"' in query for query in queries))
        self.assertTrue(any("has ontology-risk-impact 3.2" in query for query in queries))

    def test_typedb_null_repository_is_explicitly_disabled(self):
        result = NullTypeDBOntologyGraphRepository().save_graph(PortfolioOntology("empty"))

        self.assertFalse(result["saved"])
        self.assertEqual("disabled", result["status"])
        self.assertEqual("typedb", result["graphStore"])

    def test_repository_factory_builds_typedb_graph_store(self):
        repository = ontology_repository_from_settings({
            "ontologyTypeDbEnabled": "1",
            "typedbAddress": "127.0.0.1:1729",
            "typedbUser": "admin",
            "typedbPassword": "password",
            "typedbDatabase": "orbit_alpha_ontology",
            "typedbTlsEnabled": "0",
            "typedbTimeoutSeconds": "20",
        })

        self.assertIsInstance(repository, TypeDBOntologyGraphRepository)
        self.assertEqual("typedb", repository.store_key)

    def test_runtime_composition_uses_generic_graph_store_factory(self):
        root = Path(__file__).resolve().parents[2]
        for relative in [
            "python_service/digital_twin/infrastructure/service_factory.py",
            "python_service/digital_twin/infrastructure/web_server.py",
            "python_service/digital_twin/cli.py",
        ]:
            source = (root / relative).read_text(encoding="utf-8")
            self.assertIn("ontology_graph_store", source)
            self.assertNotIn("from .typedb_ontology import ontology_repository_from_settings", source)
            self.assertNotIn("from ..infrastructure.typedb_ontology import ontology_repository_from_settings", source)

    def test_service_manager_adds_typedb_only_when_graph_store_requests_it(self):
        with patch.object(service_manager, "runtime_settings", return_value={
            "ontologyTypeDbEnabled": "0",
        }):
            self.assertNotIn("typedb", service_manager.worker_specs())

        with patch.object(service_manager, "runtime_settings", return_value={
            "ontologyTypeDbEnabled": "1",
            "typedbAddress": "127.0.0.1:1729",
        }), patch.object(service_manager, "typedb_executable", return_value="/tmp/typedb"):
            workers = service_manager.worker_specs()

        self.assertIn("typedb", workers)
        command = workers["typedb"]["command"]
        self.assertIn("server", command)
        self.assertIn("--server.listen-address", command)
        self.assertIn("--storage.data-directory", command)

    def test_graph_store_contract_is_shared_by_all_adapters(self):
        repositories = [
            NullTypeDBOntologyGraphRepository(),
            TypeDBOntologyGraphRepository(""),
            TypeDBOntologyGraphRepository(""),
        ]

        for repository in repositories:
            self.assertEqual([], ontology_graph_repository_contract_errors(repository), repository.__class__.__name__)

    def test_graph_store_contract_catches_partial_implementations(self):
        class PartialRepository:
            def save_graph(self, graph):
                return {}

        errors = ontology_graph_repository_contract_errors(PartialRepository())

        self.assertGreaterEqual(len(errors), len(ONTOLOGY_GRAPH_REPOSITORY_CONTRACT) - 1)
        self.assertTrue(any("active_tbox_metadata" in error for error in errors))

    def test_typedb_rulebox_snapshot_reads_persisted_typeql_rows(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        graph = rulebox_graph_from_rules(default_graph_inference_rules()[:2])
        entity_rows = repository.rows_for_entities(graph)
        relation_rows = repository.rows_for_relations(graph)

        with patch.object(repository, "read_entity_rows", return_value=entity_rows), patch.object(repository, "read_relation_rows", return_value=relation_rows):
            snapshot = repository.rulebox_snapshot()

        self.assertEqual("ok", snapshot["status"])
        self.assertEqual("typedb-typeql", snapshot["source"])
        self.assertEqual("typedb", snapshot["graphStore"])
        self.assertGreaterEqual(snapshot["ruleCount"], 1)
        self.assertFalse(snapshot["defaultsFallbackUsed"])
        self.assertEqual("typedb-functions", snapshot["nativeReasoningProfile"]["reasoningModel"])

    def test_typedb_rule_condition_rows_keep_node_kind_separate_from_condition_kind(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        rows = repository.entity_rows_from_typeql([
            {
                "id": "rule-condition:test",
                "label": "보유 종목",
                "kind": "rule-condition",
                "updatedAt": "2026-07-12T00:00:00Z",
                "json": json.dumps({
                    "ontologyBox": "RuleBox",
                    "ruleId": "graph.test",
                    "condition": {
                        "condition_id": "holding-source",
                        "kind": "subject_property",
                        "field": "source",
                        "operator": "==",
                        "value": "holding",
                    },
                }),
            }
        ], "RuleBox")

        self.assertEqual("rule-condition", rows[0]["nodeKind"])
        self.assertEqual("subject_property", rows[0]["kind"])

    def test_typedb_inferencebox_snapshot_reads_persisted_typeql_rows(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        entity_rows = [
            {
                "id": "risk:005930:loss",
                "label": "삼성전자 손실 방어",
                "kind": "risk-signal",
                "ontologyBox": "InferenceBox",
                "symbol": "005930",
                "ruleId": "graph.loss_guard.breakdown.v1",
                "tboxClass": "RiskSignal",
                "confidence": 0.86,
                "nativeTypeDbReasoned": True,
                "propertiesJson": json.dumps({
                    "ontologyBox": "InferenceBox",
                    "symbol": "005930",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "confidence": 0.86,
                    "decisionStage": "LOSS_REDUCE",
                    "stagePriority": 90,
                    "nativeTypeDbReasoned": True,
                }),
            },
            {
                "id": "inference-trace:005930:graph.loss_guard.breakdown.v1",
                "label": "삼성전자 · 손실 방어 추론",
                "kind": "inference-trace",
                "ontologyBox": "InferenceBox",
                "symbol": "005930",
                "ruleId": "graph.loss_guard.breakdown.v1",
                "confidence": 0.86,
                "nativeTypeDbReasoned": True,
                "propertiesJson": json.dumps({
                    "ontologyBox": "InferenceBox",
                    "symbol": "005930",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "confidence": 0.86,
                    "matchedConditions": [{"conditionId": "holding-loss"}],
                    "nativeTypeDbReasoned": True,
                }),
            },
        ]
        relation_rows = [
            {
                "source": "stock:005930",
                "sourceLabel": "삼성전자",
                "target": "risk:005930:loss",
                "targetLabel": "삼성전자 손실 방어",
                "type": "HAS_INFERRED_RISK",
                "ontologyBox": "InferenceBox",
                "symbol": "005930",
                "ruleId": "graph.loss_guard.breakdown.v1",
                "weight": 0.86,
                "nativeTypeDbReasoned": True,
                "propertiesJson": json.dumps({
                    "ontologyBox": "InferenceBox",
                    "symbol": "005930",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "riskImpact": 13,
                    "decisionStage": "LOSS_REDUCE",
                    "stagePriority": 90,
                    "aiInfluenceLabel": "손실 방어 추론",
                    "inferenceTraceId": "inference-trace:005930:graph.loss_guard.breakdown.v1",
                    "nativeTypeDbReasoned": True,
                }),
            },
        ]

        with patch.object(repository, "read_entity_rows", return_value=entity_rows), patch.object(repository, "read_relation_rows", return_value=relation_rows):
            snapshot = repository.inferencebox_snapshot(symbols=["005930"])

        self.assertEqual("ok", snapshot["status"])
        self.assertEqual("typedbInferenceBox", snapshot["source"])
        self.assertEqual("typedb", snapshot["graphStore"])
        self.assertEqual("typedb-native-inferencebox", snapshot["reasoningMode"])
        self.assertEqual("typedb-typeql", snapshot["querySource"])
        self.assertEqual("ok", snapshot["typedbReadStatus"])
        self.assertEqual(2, snapshot["entityCount"])
        self.assertEqual(1, snapshot["relationCount"])
        self.assertTrue(snapshot["nativeTypeDbReasoningUsed"])
        self.assertFalse(snapshot["typedbBootstrapReasoningUsed"])
        self.assertEqual(0, snapshot["ignoredNonNativeRelationCount"])
        self.assertEqual(["holding-loss"], snapshot["traces"][0]["matchedConditionIds"])

    def test_typedb_inferencebox_snapshot_exposes_typeql_read_errors(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")

        with patch.object(repository, "read_entity_rows", side_effect=RuntimeError("schema unavailable")):
            snapshot = repository.inferencebox_snapshot(symbols=["005930"])

        self.assertEqual("error", snapshot["status"])
        self.assertEqual("typedbInferenceBox", snapshot["source"])
        self.assertEqual("typedb", snapshot["graphStore"])
        self.assertEqual("typedb-typeql-read", snapshot["reasoningMode"])
        self.assertEqual("typedb-typeql", snapshot["querySource"])
        self.assertEqual("error", snapshot["typedbReadStatus"])
        self.assertIn("schema unavailable", snapshot["typedbReadReason"])
        self.assertIn("TypeDB InferenceBox 조회 실패", snapshot["reason"])
        self.assertFalse(snapshot["typedbBootstrapReasoningUsed"])

    def test_typedb_rulebox_execution_requires_native_inference_and_does_not_materialize_python_bootstrap(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        rule_snapshot = {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "rules": [default_graph_inference_rules()[0].to_dict()],
            "ruleCount": 1,
        }

        with patch.object(repository, "read_entity_rows", return_value=[{"id": "stock:005930", "ontologyBox": "ABox"}]), patch.object(repository, "rulebox_snapshot", return_value=rule_snapshot), patch.object(repository, "clear_inferencebox", return_value={"status": "ok", "graphStore": "typedb"}), patch.object(repository, "save_graph") as save_graph:
            result = repository.run_rulebox({"clearInference": True})

        self.assertEqual("native-reasoning-required", result["status"])
        self.assertEqual("typedb-native-required", result["reasoningMode"])
        self.assertEqual(0, result["statementCount"])
        self.assertFalse(result["typedbBootstrapReasoningUsed"])
        self.assertTrue(result["pythonBootstrapDisabled"])
        self.assertIn("nativeReasoningProfile", result)
        self.assertFalse(result["typedbNativeFunctionReasoningUsed"])
        save_graph.assert_not_called()

    def test_typedb_native_reasoning_profile_identifies_function_ready_rules(self):
        profile = typedb_native_reasoning_profile([rule.to_dict() for rule in default_graph_inference_rules()])

        self.assertEqual("typedb-functions", profile["reasoningModel"])
        self.assertEqual("typedb-function-readiness-v1", profile["version"])
        self.assertGreater(profile["readyRuleCount"] + profile["partialRuleCount"], 0)
        self.assertTrue(profile["materializationRequired"])


if __name__ == "__main__":
    unittest.main()
