import unittest

from digital_twin.modules.reasoning.domain.ontology_contracts import (
    OntologyEntity,
    OntologyRelation,
    PortfolioOntology,
)
from digital_twin.infrastructure.ontology_projection import (
    PORTFOLIO_GRAPH_ASSEMBLY_CACHE_CONTRACT_VERSION,
    PortfolioOntologyProjectionRecorder,
)
from digital_twin.infrastructure.typedb_ontology import (
    TypeDBOntologyGraphRepository,
)


class ProjectionHypothesisCalibrationLineageTests(unittest.TestCase):
    def assert_compact_frozen_catalog_retains_crypto_rule_subjects(self):
        frozen_catalog = {
            "configured": True,
            "status": "ok",
            "ruleCount": 1,
            "frozenReleaseVerified": True,
            "rules": [{
                "rule_id": "graph.crypto.market.7d.up.watch.v1",
                "enabled": True,
                "source_kind": "crypto-asset",
                "conditions": [{
                    "kind": "relation",
                    "relation_type": "HAS_OBSERVATION",
                    "direction": "out",
                }],
                "derivations": [],
            }],
        }
        recorder = PortfolioOntologyProjectionRecorder(
            object(),
            frozen_rulebox_catalog=frozen_catalog,
        )
        compact_catalog = recorder.ensure_rulebox_ready()
        graph = PortfolioOntology(
            "crypto-rule-subject-test",
            entities=[
                OntologyEntity(
                    "crypto-asset:ETH",
                    "Ethereum",
                    "crypto-asset",
                    {"ontologyBox": "ABox", "symbol": "ETH"},
                ),
                OntologyEntity(
                    "market-event:ETH:crypto-threshold:7d:up:watch",
                    "Ethereum 7d threshold",
                    "market-event",
                    {"ontologyBox": "ABox", "eventType": "crypto-market-7d-up-watch"},
                ),
            ],
            relations=[
                OntologyRelation(
                    "crypto-asset:ETH",
                    "market-event:ETH:crypto-threshold:7d:up:watch",
                    "HAS_OBSERVATION",
                    properties={"ontologyBox": "ABox"},
                ),
            ],
        )

        self.assertNotIn("rules", compact_catalog)
        persisted = recorder.graph_for_graph_store_persistence(
            graph,
            compact_catalog,
        )

        self.assertEqual(
            {
                "crypto-asset:ETH",
                "market-event:ETH:crypto-threshold:7d:up:watch",
            },
            {item.entity_id for item in persisted.entities},
        )
        self.assertEqual(
            {"HAS_OBSERVATION"},
            {item.relation_type for item in persisted.relations},
        )

    def test_native_result_reads_calibration_from_active_scope_not_matched_fact_slice(self):
        class CalibrationRepository(TypeDBOntologyGraphRepository):
            def __init__(self):
                super().__init__("127.0.0.1:1729")
                self.read_arguments = None

            def hypothesis_calibration_snapshot(
                self,
                symbols=None,
                limit=40,
                world_id="",
                source_abox_snapshot_id="",
                generation_aligned=False,
            ):
                self.read_arguments = {
                    "symbols": list(symbols or []),
                    "limit": limit,
                    "worldId": world_id,
                    "sourceAboxSnapshotId": source_abox_snapshot_id,
                    "generationAligned": generation_aligned,
                }
                return {
                    "status": "ok",
                    "calibrationCount": 1,
                    "calibrations": [{"claimContractId": "rule-claim:risk"}],
                }

        repository = CalibrationRepository()
        matched_graph = PortfolioOntology(
            "matched-rule-premises",
            entities=[
                OntologyEntity(
                    "stock:000660",
                    "SK hynix",
                    "stock",
                    {"ontologyBox": "ABox", "symbol": "000660"},
                ),
            ],
        )

        snapshot = repository.hypothesis_calibration_snapshot_for_native_result(
            matched_graph,
            ["000660"],
            "abox-manifest:current",
            True,
            True,
            world_id="portfolio:local:default",
        )

        self.assertEqual("ok", snapshot["status"])
        self.assertEqual(1, snapshot["calibrationCount"])
        self.assertEqual(
            {
                "symbols": ["000660"],
                "limit": 40,
                "worldId": "portfolio:local:default",
                "sourceAboxSnapshotId": "abox-manifest:current",
                "generationAligned": True,
            },
            repository.read_arguments,
        )

    def test_persistence_graph_keeps_complete_hypothesis_calibration_lineage(self):
        self.assert_compact_frozen_catalog_retains_crypto_rule_subjects()
        graph = PortfolioOntology(
            "default",
            entities=[
                OntologyEntity(
                    "stock:000660",
                    "SK하이닉스",
                    "stock",
                    {"ontologyBox": "ABox", "symbol": "000660"},
                ),
                OntologyEntity(
                    "hypothesis-template:recovery",
                    "회복 가설",
                    "hypothesis-template",
                    {"ontologyBox": "ABox"},
                ),
                OntologyEntity(
                    "hypothesis-calibration:000660-recovery",
                    "SK하이닉스 회복 가설 결과 보정",
                    "hypothesis-calibration",
                    {
                        "ontologyBox": "ABox",
                        "tboxClass": "HypothesisCalibration",
                        "symbol": "000660",
                        "templateId": "hypothesis-template:recovery",
                        "independentEpisodeCount": 5,
                        "decisiveOutcomeCount": 4,
                    },
                ),
            ],
            relations=[
                OntologyRelation(
                    "stock:000660",
                    "hypothesis-calibration:000660-recovery",
                    "HAS_HYPOTHESIS_CALIBRATION",
                    properties={"ontologyBox": "ABox"},
                ),
                OntologyRelation(
                    "hypothesis-template:recovery",
                    "hypothesis-calibration:000660-recovery",
                    "CALIBRATED_BY_OUTCOME",
                    properties={"ontologyBox": "ABox"},
                ),
            ],
        )
        recorder = PortfolioOntologyProjectionRecorder(object())

        persisted = recorder.graph_for_graph_store_persistence(
            graph,
            {"inputRelationTypes": ["HAS_PRICE"]},
        )

        self.assertIn(
            "v16-frozen-rule-subjects",
            PORTFOLIO_GRAPH_ASSEMBLY_CACHE_CONTRACT_VERSION,
        )
        self.assertEqual(
            {
                "hypothesis-calibration:000660-recovery",
                "hypothesis-template:recovery",
                "stock:000660",
            },
            {item.entity_id for item in persisted.entities},
        )
        self.assertEqual(
            {"CALIBRATED_BY_OUTCOME", "HAS_HYPOTHESIS_CALIBRATION"},
            {item.relation_type for item in persisted.relations},
        )


if __name__ == "__main__":
    unittest.main()
