import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.ontology import build_portfolio_ontology
from digital_twin.domain.ontology_prompting import prompt_payload
from digital_twin.domain.portfolio import Position
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.infrastructure.neo4j_ontology import Neo4jOntologyGraphRepository


class OntologyRuleBoxTests(unittest.TestCase):
    def loss_guard_graph(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            quantity=10,
            sellable_quantity=10,
            average_price=80000,
            current_price=69000,
            market_value=690000,
            profit_loss=-110000,
            profit_loss_rate=-12.4,
            ma20=76000,
            ma60=73000,
            ma20_distance=-9.2,
            ma60_distance=-5.5,
            volume_ratio=1.4,
            trading_value=5000000000,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-test")

    def test_rulebox_materializes_rules_and_inference_relations(self):
        graph = self.loss_guard_graph()

        rule_entities = [
            item
            for item in graph.entities
            if item.kind == "rule" and (item.properties or {}).get("ontologyBox") == "RuleBox"
        ]
        inference_relations = [
            item
            for item in graph.relations
            if (item.properties or {}).get("ontologyBox") == "InferenceBox"
        ]
        loss_guard_relations = [
            item
            for item in inference_relations
            if item.source == "stock:005930" and item.relation_type == "HAS_INFERRED_RISK"
        ]
        opinion = graph.opinion_for_symbol("005930")

        self.assertTrue(any((item.properties or {}).get("ruleId") == "graph.loss_guard.breakdown.v1" for item in rule_entities))
        self.assertTrue(loss_guard_relations)
        self.assertTrue(any(item.kind == "inference-trace" for item in graph.entities))
        self.assertTrue(any(item.kind == "inference-trace" for item in graph.evidence))
        self.assertIsNotNone(opinion)
        self.assertTrue(any("손실 방어 추론" in str(item.get("label") or "") for item in opinion.relation_influences))

    def test_prompt_payload_exposes_rulebox_and_inferencebox(self):
        graph = self.loss_guard_graph()
        payload = prompt_payload(graph)

        self.assertGreater(payload["ruleBox"]["ruleCount"], 0)
        self.assertGreater(payload["inferenceBox"]["traceCount"], 0)
        self.assertTrue(any(item["type"] == "HAS_INFERRED_RISK" for item in payload["derivedRelations"]))
        self.assertIn("ruleBox", payload["aiInferencePacket"]["inputOrder"])
        self.assertGreater(payload["aiInferencePacket"]["graphInputs"]["inferenceBoxRelationCount"], 0)

    def test_neo4j_projection_promotes_rule_and_inference_query_keys(self):
        graph = self.loss_guard_graph()
        repository = Neo4jOntologyGraphRepository("http://neo4j.example.test")

        rule_row = next(item for item in repository.rows_for_entities(graph) if item["id"] == "rule:graph.loss_guard.breakdown.v1")
        condition_row = next(item for item in repository.rows_for_entities(graph) if item["id"] == "rule-condition:graph.loss_guard.breakdown.v1:ma-break")
        template_row = next(item for item in repository.rows_for_entities(graph) if item["id"] == "relation-template:graph.loss_guard.breakdown.v1:0")
        inference_row = next(item for item in repository.rows_for_entities(graph) if item["kind"] == "inference-trace")
        risk_relation = next(item for item in repository.rows_for_relations(graph) if item["type"] == "HAS_INFERRED_RISK")
        inference_evidence = next(item for item in repository.rows_for_evidence(graph) if item["kind"] == "inference-trace")
        schema_text = "\n".join(item["statement"] for item in repository.schema_statements())

        self.assertEqual("RuleBox", rule_row["ontologyBox"])
        self.assertEqual("graph.loss_guard.breakdown.v1", rule_row["ruleId"])
        self.assertEqual("relation", condition_row["conditionKind"])
        self.assertEqual("BREAKS_LEVEL", condition_row["conditionRelationType"])
        self.assertEqual(["ma20", "ma60"], condition_row["conditionTargetLevelTypes"])
        self.assertEqual("HAS_INFERRED_RISK", template_row["derivationRelationType"])
        self.assertEqual("risk", template_row["derivationTargetKind"])
        self.assertEqual("InferenceBox", inference_row["ontologyBox"])
        self.assertEqual("InferenceBox", risk_relation["ontologyBox"])
        self.assertEqual("graph.loss_guard.breakdown.v1", risk_relation["ruleId"])
        self.assertEqual("InferenceBox", inference_evidence["ontologyBox"])
        self.assertIn("ontology_entity_rule_id", schema_text)
        self.assertIn("ontology_entity_condition_kind", schema_text)

    def test_neo4j_native_reasoning_cypher_uses_rulebox_as_execution_source(self):
        graph = self.loss_guard_graph()
        repository = Neo4jOntologyGraphRepository("http://neo4j.example.test")

        statements = repository.native_reasoning_statements(graph)
        cypher = "\n".join(item["statement"] for item in statements)
        relation_types = [item["parameters"]["relationType"] for item in statements]

        self.assertIn("HAS_INFERRED_RISK", relation_types)
        self.assertIn("MATCH (rule:OntologyEntity {kind: 'rule', ontologyBox: 'RuleBox'})", cypher)
        self.assertIn("MATCH (rule)-[:HAS_CONDITION]->(condition:OntologyEntity", cypher)
        self.assertIn("MATCH (rule)-[:DERIVES_RELATION]->(template:OntologyEntity", cypher)
        self.assertIn("MERGE (stock)-[inferred:HAS_INFERRED_RISK]->(target)", cypher)
        self.assertIn("nativeNeo4jReasoned = true", cypher)


if __name__ == "__main__":
    unittest.main()
