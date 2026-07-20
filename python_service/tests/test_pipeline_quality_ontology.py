import unittest

from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.portfolio import Position
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.infrastructure.graph_store_rulebox import rulebox_rules_to_payload
from digital_twin.infrastructure.typedb_ontology import typedb_native_rule_profile


class PipelineQualityOntologyTests(unittest.TestCase):
    def test_failed_market_snapshot_becomes_per_symbol_blocking_abox_fact(self):
        position = Position(
            "005930",
            "삼성전자",
            market="KR",
            currency="KRW",
            quantity=1,
            current_price=80000,
            market_value=80000,
            source="holding",
            quote_source="KIS Open API",
            source_as_of="2026-07-21T01:00:00Z",
            source_fetched_at="2026-07-21T01:00:10Z",
            updated_at="2026-07-21T01:00:10Z",
            data_quality="actual",
        )
        graph = build_portfolio_ontology(
            [position],
            portfolio_summary([position]),
            portfolio_id="pipeline-quality-test",
            runtime_context={
                "asOf": "2026-07-21T01:01:00Z",
                "dataPipelineHealth": {
                    "pipelines": {
                        "marketSnapshot": {
                            "state": "failed",
                            "reasonCode": "quote-coverage-empty",
                            "reason": "수집 대상 종목의 현재가를 확보하지 못했습니다.",
                            "checkedAt": "2026-07-21T01:01:00Z",
                            "targetCount": 1,
                            "fetchedCount": 0,
                            "savedCount": 0,
                            "providerFailureCount": 1,
                        }
                    }
                },
            },
        )

        quality = next(
            item for item in graph.entities
            if item.kind == "missing-data" and item.properties.get("dataScope") == "market-snapshot"
        )
        relation = next(
            item for item in graph.relations
            if item.source == "stock:005930" and item.target == quality.entity_id and item.relation_type == "HAS_DATA_QUALITY"
        )

        self.assertEqual("failed", quality.properties["pipelineState"])
        self.assertEqual("unavailable", quality.properties["dataState"])
        self.assertEqual("blocking", relation.properties["evidenceRole"])
        rules = {item.rule_id for item in default_graph_inference_rules()}
        self.assertIn("graph.data_quality.market_snapshot_failure_block.v1", rules)
        self.assertIn("graph.data_quality.market_snapshot_degraded.v1", rules)

    def test_market_snapshot_quality_rules_are_typedb_native_ready(self):
        profiles = {
            str(item["rule_id"]): typedb_native_rule_profile(item)
            for item in rulebox_rules_to_payload(default_graph_inference_rules())
            if str(item["rule_id"]).startswith("graph.data_quality.market_snapshot_")
        }

        self.assertEqual(
            {"graph.data_quality.market_snapshot_degraded.v1", "graph.data_quality.market_snapshot_failure_block.v1"},
            set(profiles),
        )
        self.assertTrue(all(item["status"] == "ready" for item in profiles.values()))


if __name__ == "__main__":
    unittest.main()
