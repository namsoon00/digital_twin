import json
import unittest

from digital_twin.infrastructure.typedb_ontology import (
    TypeDBOntologyGraphRepository,
    ontology_storage_id,
)


class TypeDBScopedRelationRebindTest(unittest.TestCase):
    def setUp(self):
        self.state_scope = "symbol:MSTR:state"
        self.evidence_scope = "symbol:MSTR:evidence:bucket:47"
        self.link_scope = "link:symbol:MSTR:evidence:bucket:47"
        self.old_state_generation = "abox-current-cow:state-old"
        self.new_state_generation = "abox-current-cow:state-new"
        self.evidence_generation = "abox-current-cow:evidence-old"
        self.old_link_generation = "abox-current-cow:link-old"
        self.new_link_generation = "abox-current-cow:link-new"
        self.manifest_id = "abox-manifest:candidate"
        self.world_id = "portfolio:local:default"

        self.current_stock = self._node(
            "stock:MSTR",
            "stock",
            self.state_scope,
            self.new_state_generation,
            currentPrice=125.0,
            symbol="MSTR",
        )
        self.current_news = self._node(
            "research:MSTR:news:new",
            "news-article",
            self.evidence_scope,
            self.evidence_generation,
            title="Deferred new article",
            symbol="MSTR",
        )
        self.current_relation = self._relation(
            "stock:MSTR",
            "research:MSTR:news:new",
            self.new_link_generation,
            title="Deferred new article",
        )

        self.active_stock = self._node(
            "stock:MSTR",
            "stock",
            self.state_scope,
            self.old_state_generation,
            currentPrice=124.0,
            symbol="MSTR",
        )
        self.active_news = self._node(
            "research:MSTR:news:active",
            "news-article",
            self.evidence_scope,
            self.evidence_generation,
            title="Published active article",
            symbol="MSTR",
        )
        self.active_relation = self._relation(
            "stock:MSTR",
            "research:MSTR:news:active",
            self.old_link_generation,
            title="Published active article",
        )
        self.active_relation.update({
            "sourceStorageId": ontology_storage_id(
                self.active_stock,
                self.active_stock["id"],
                "node",
            ),
            "targetStorageId": ontology_storage_id(
                self.active_news,
                self.active_news["id"],
                "node",
            ),
        })
        self.physical_scope_plan = [
            {
                "scopeId": self.state_scope,
                "scopeType": "symbol",
                "generationId": self.new_state_generation,
                "logicalGenerationId": "logical-state-new",
                "physicalGenerationChanged": True,
                "entityCount": 1,
                "relationCount": 0,
            },
            {
                "scopeId": self.evidence_scope,
                "scopeType": "symbol",
                "generationId": self.evidence_generation,
                "logicalGenerationId": "logical-evidence-active",
                "physicalGenerationChanged": False,
                "entityCount": 1,
                "relationCount": 0,
            },
            {
                "scopeId": self.link_scope,
                "scopeType": "link",
                "generationId": self.new_link_generation,
                "logicalGenerationId": "logical-link-active",
                "physicalGenerationChanged": True,
                "entityCount": 0,
                "relationCount": 1,
            },
        ]

    def _node(self, node_id, kind, scope_id, generation_id, **properties):
        values = {
            "ontologyBox": "ABox",
            "worldId": self.world_id,
            "aboxScopeId": scope_id,
            "aboxScopeType": "link" if scope_id.startswith("link:") else "symbol",
            "scopeGenerationId": generation_id,
            "snapshotId": generation_id,
            "aboxSnapshotId": generation_id,
            **properties,
        }
        return {
            "id": node_id,
            "label": properties.get("title") or node_id,
            "kind": kind,
            "ontologyBox": "ABox",
            "worldId": self.world_id,
            "scopeId": scope_id,
            "scopeType": values["aboxScopeType"],
            "snapshotId": generation_id,
            "aboxSnapshotId": generation_id,
            "scopeGenerationId": generation_id,
            "propertiesJson": json.dumps(values, sort_keys=True),
            **properties,
        }

    def _relation(self, source, target, generation_id, **properties):
        values = {
            "ontologyBox": "ABox",
            "worldId": self.world_id,
            "symbol": "MSTR",
            "aboxScopeId": self.link_scope,
            "aboxScopeType": "link",
            "scopeGenerationId": generation_id,
            "snapshotId": generation_id,
            "aboxSnapshotId": generation_id,
            **properties,
        }
        return {
            "source": source,
            "target": target,
            "type": "HAS_EVIDENCE",
            "weight": 1.0,
            "symbol": "MSTR",
            "ontologyBox": "ABox",
            "worldId": self.world_id,
            "scopeId": self.link_scope,
            "scopeType": "link",
            "snapshotId": generation_id,
            "aboxSnapshotId": generation_id,
            "scopeGenerationId": generation_id,
            "propertiesJson": json.dumps(values, sort_keys=True),
        }

    def _active_context(self):
        return {
            "status": "ok",
            "scopeIds": [self.evidence_scope, self.link_scope],
            "nodeRows": [self.active_news],
            "endpointNodeRows": [self.active_stock, self.active_news],
            "relationRows": [self.active_relation],
        }

    def test_rebind_preserves_active_relation_semantics(self):
        result = TypeDBOntologyGraphRepository.scoped_abox_candidate_persistence_rows(
            [self.current_stock, self.current_news],
            [self.current_relation],
            self._active_context(),
            self.physical_scope_plan,
            [self.state_scope],
            [self.state_scope, self.link_scope],
            [self.evidence_scope, self.link_scope],
            self.manifest_id,
        )

        self.assertEqual("ok", result["status"])
        self.assertEqual([self.link_scope], result["rebindOnlyRelationScopeIds"])
        self.assertEqual(1, result["reboundRelationCount"])
        self.assertEqual(["stock:MSTR"], [row["id"] for row in result["nodeRows"]])
        self.assertEqual(1, len(result["relationRows"]))
        rebound = result["relationRows"][0]
        self.assertEqual("research:MSTR:news:active", rebound["target"])
        self.assertNotEqual("research:MSTR:news:new", rebound["target"])
        self.assertEqual(self.new_link_generation, rebound["snapshotId"])
        self.assertEqual(
            ontology_storage_id(self.current_stock, "stock:MSTR", "node"),
            rebound["sourceStorageId"],
        )
        self.assertEqual(
            ontology_storage_id(
                self.active_news,
                "research:MSTR:news:active",
                "node",
            ),
            rebound["targetStorageId"],
        )
        candidate_node_ids = {row["id"] for row in result["candidateNodeRows"]}
        self.assertIn("research:MSTR:news:active", candidate_node_ids)
        self.assertNotIn("research:MSTR:news:new", candidate_node_ids)
        self.assertEqual(
            ["research:MSTR:news:active"],
            [row["target"] for row in result["candidateRelationRows"]],
        )

    def test_rebind_fails_closed_when_active_relation_is_missing(self):
        active_context = self._active_context()
        active_context["relationRows"] = []

        result = TypeDBOntologyGraphRepository.scoped_abox_candidate_persistence_rows(
            [self.current_stock, self.current_news],
            [self.current_relation],
            active_context,
            self.physical_scope_plan,
            [self.state_scope],
            [self.state_scope, self.link_scope],
            [self.evidence_scope, self.link_scope],
            self.manifest_id,
        )

        self.assertEqual("active-rebind-relation-count-mismatch", result["status"])
        self.assertEqual(self.link_scope, result["scopeId"])


if __name__ == "__main__":
    unittest.main()
