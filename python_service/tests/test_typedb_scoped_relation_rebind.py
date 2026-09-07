import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from digital_twin.domain.ontology_contracts import PortfolioOntology
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

    def test_incremental_save_rejects_unvalidated_manifest_patch_contract(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        graph = PortfolioOntology(
            "invalid-patch-contract",
            worldview={
                "worldId": "portfolio:local:main",
                "worldviewManifestId": "abox-manifest:test",
                "persistenceMode": "immutable-scoped-manifest",
                "scopePlan": [{
                    "scopeId": "symbol:MSTR:state",
                    "scopeType": "symbol",
                    "scopeFamily": "state",
                    "generationId": "abox-scope:test",
                    "fingerprint": "test",
                    "dependencyScopeIds": [],
                    "entityCount": 1,
                    "nodeInventoryVersion": "scope-node-inventory-v1",
                    "nodeIds": ["stock:MSTR"],
                    "relationEndpointBindingVersion": "relation-endpoint-binding-v1",
                    "relationEndpointNodeIdsByScope": {
                        "symbol:MSTR:state": ["stock:MSTR"],
                    },
                }],
                "targetScopedManifestPatch": {
                    "status": "applied",
                    "mode": "incremental-target-scoped-manifest-patch",
                    "targetSymbols": ["MSTR"],
                    "manifestPatchContract": {
                        "version": "abox-manifest-patch-boundary-v1",
                        "validation": {
                            "status": "invalid",
                            "valid": False,
                        },
                    },
                },
            },
        )

        result = repository.save_scoped_abox_graph(graph)

        self.assertEqual("invalid-manifest-patch-contract", result["status"])
        self.assertFalse(result["saved"])
        self.assertTrue(result["preservedActiveGeneration"])
        normalized_scope = repository.scoped_abox_plan(graph)[0]
        self.assertEqual(
            "scope-node-inventory-v1",
            normalized_scope["nodeInventoryVersion"],
        )
        self.assertEqual(["stock:MSTR"], normalized_scope["nodeIds"])
        self.assertEqual(
            {"symbol:MSTR:state": ["stock:MSTR"]},
            normalized_scope["relationEndpointNodeIdsByScope"],
        )

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

        changed_plan = [dict(item) for item in self.physical_scope_plan]
        for item in changed_plan:
            if item["scopeId"] == self.evidence_scope:
                item.update({
                    "generationId": "abox-current-cow:evidence-new",
                    "logicalGenerationId": "logical-evidence-new",
                    "physicalGenerationChanged": True,
                })
        replacement_news = self._node(
            "research:MSTR:news:new",
            "news-article",
            self.evidence_scope,
            "abox-current-cow:evidence-new",
            title="Deferred new article",
            symbol="MSTR",
        )
        replacement_relation = self._relation(
            "stock:MSTR",
            replacement_news["id"],
            self.new_link_generation,
            title="Deferred new article",
        )
        replacement_relation.update({
            "sourceStorageId": ontology_storage_id(
                self.current_stock,
                self.current_stock["id"],
                "node",
            ),
            "targetStorageId": ontology_storage_id(
                replacement_news,
                replacement_news["id"],
                "node",
            ),
        })
        result = TypeDBOntologyGraphRepository.scoped_abox_candidate_persistence_rows(
            [self.current_stock, replacement_news],
            [replacement_relation],
            self._active_context(),
            changed_plan,
            [self.state_scope, self.evidence_scope],
            [self.state_scope, self.evidence_scope, self.link_scope],
            [self.link_scope],
            self.manifest_id,
        )

        self.assertEqual("ok", result["status"])
        self.assertEqual(
            [self.link_scope],
            result["currentFallbackRelationScopeIds"],
        )
        self.assertEqual(
            ["research:MSTR:news:new"],
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

    def test_current_fallback_rejects_endpoint_outside_candidate_manifest(self):
        changed_plan = [dict(item) for item in self.physical_scope_plan]
        for item in changed_plan:
            if item["scopeId"] == self.evidence_scope:
                item.update({
                    "generationId": "abox-current-cow:evidence-new",
                    "logicalGenerationId": "logical-evidence-new",
                    "physicalGenerationChanged": True,
                })
        result = TypeDBOntologyGraphRepository.scoped_abox_candidate_persistence_rows(
            [self.current_stock, self.current_news],
            [self.current_relation],
            self._active_context(),
            changed_plan,
            [self.state_scope],
            [self.state_scope, self.evidence_scope, self.link_scope],
            [self.evidence_scope, self.link_scope],
            self.manifest_id,
        )

        # The current relation points at a new evidence node while that
        # evidence scope is explicitly deferred. It cannot be used as a
        # physical fallback until recovery expands the exact candidate scope.
        self.assertEqual("active-rebind-endpoint-invalid", result["status"])
        self.assertEqual(self.link_scope, result["scopeId"])
        self.assertIn("absent from the exact candidate graph", result["reason"])

    def test_candidate_relation_storage_ids_are_rebound_to_candidate_nodes(self):
        current_relation = self._relation(
            "stock:MSTR",
            "research:MSTR:news:active",
            self.new_link_generation,
            title="Published active article",
        )
        current_relation.update({
            "sourceStorageId": "ontology-storage:stale-source",
            "targetStorageId": "ontology-storage:stale-target",
        })

        result = TypeDBOntologyGraphRepository.scoped_abox_candidate_persistence_rows(
            [self.current_stock, self.current_news],
            [current_relation],
            self._active_context(),
            self.physical_scope_plan,
            [self.state_scope, self.link_scope],
            [self.state_scope, self.link_scope],
            [self.evidence_scope],
            self.manifest_id,
        )

        self.assertEqual("ok", result["status"])
        relation = result["candidateRelationRows"][0]
        self.assertEqual(
            ontology_storage_id(self.current_stock, "stock:MSTR", "node"),
            relation["sourceStorageId"],
        )
        self.assertEqual(
            ontology_storage_id(
                self.active_news,
                "research:MSTR:news:active",
                "node",
            ),
            relation["targetStorageId"],
        )

    def test_current_only_node_from_unchanged_scope_is_not_candidate_endpoint(self):
        result = TypeDBOntologyGraphRepository.scoped_abox_candidate_persistence_rows(
            [self.current_stock, self.current_news],
            [self.current_relation],
            self._active_context(),
            self.physical_scope_plan,
            [self.state_scope, self.link_scope],
            [self.state_scope, self.link_scope],
            [],
            self.manifest_id,
        )

        self.assertEqual("candidate-relation-endpoint-missing", result["status"])
        self.assertEqual("research:MSTR:news:new", result["endpointId"])
        self.assertEqual("target", result["endpointRole"])
        self.assertEqual([self.evidence_scope], result["knownEndpointScopeIds"])

    def test_selected_relation_can_reuse_unchanged_active_endpoint(self):
        current_relation = self._relation(
            "stock:MSTR",
            "research:MSTR:news:active",
            self.new_link_generation,
            title="Published active article",
        )

        result = TypeDBOntologyGraphRepository.scoped_abox_candidate_persistence_rows(
            [self.current_stock],
            [current_relation],
            self._active_context(),
            self.physical_scope_plan,
            [self.state_scope, self.link_scope],
            [self.state_scope, self.link_scope],
            [],
            self.manifest_id,
        )

        self.assertEqual("ok", result["status"])
        self.assertEqual(
            "research:MSTR:news:active",
            result["candidateRelationRows"][0]["target"],
        )

    def test_unselected_current_relation_does_not_enter_candidate(self):
        result = TypeDBOntologyGraphRepository.scoped_abox_candidate_persistence_rows(
            [self.current_stock, self.current_news],
            [self.current_relation],
            self._active_context(),
            self.physical_scope_plan,
            [self.state_scope],
            [self.state_scope],
            [self.evidence_scope],
            self.manifest_id,
        )

        self.assertEqual("ok", result["status"])
        self.assertEqual([], result["candidateRelationRows"])

    def test_candidate_relation_fails_before_write_for_retired_endpoint_generation(self):
        changed_plan = [dict(item) for item in self.physical_scope_plan]
        for item in changed_plan:
            if item["scopeId"] == self.evidence_scope:
                item.update({
                    "generationId": "abox-current-cow:evidence-new",
                    "logicalGenerationId": "logical-evidence-new",
                    "physicalGenerationChanged": True,
                })
        relation = self._relation(
            "stock:MSTR",
            "research:MSTR:news:active",
            self.new_link_generation,
            title="Retired article",
        )
        relation.update({
            "sourceStorageId": "ontology-storage:stale-source",
            "targetStorageId": "ontology-storage:stale-target",
        })

        result = TypeDBOntologyGraphRepository.scoped_abox_candidate_persistence_rows(
            [self.current_stock, self.current_news],
            [relation],
            self._active_context(),
            changed_plan,
            [self.state_scope, self.evidence_scope, self.link_scope],
            [self.state_scope, self.evidence_scope, self.link_scope],
            [],
            self.manifest_id,
        )

        self.assertEqual("candidate-relation-endpoint-missing", result["status"])
        self.assertEqual(self.link_scope, result["scopeId"])
        self.assertEqual("target", result["endpointRole"])
        self.assertEqual("research:MSTR:news:active", result["endpointId"])
        self.assertEqual(
            [self.evidence_generation],
            result["knownEndpointGenerationIds"],
        )

    def test_initial_candidate_counts_evidence_as_a_physical_node(self):
        evidence_row = self._node(
            "evidence:MSTR:price",
            "evidence:price",
            self.evidence_scope,
            self.evidence_generation,
            symbol="MSTR",
        )
        evidence_row["nodeType"] = "ontology-evidence"
        plan = [{
            "scopeId": self.evidence_scope,
            "scopeType": "symbol",
            "generationId": self.evidence_generation,
            "logicalGenerationId": "logical-evidence",
            "physicalGenerationChanged": True,
            "entityCount": 0,
            "evidenceCount": 1,
            "relationCount": 0,
        }]

        result = TypeDBOntologyGraphRepository.scoped_abox_candidate_persistence_rows(
            [evidence_row],
            [],
            {},
            plan,
            [self.evidence_scope],
            [self.evidence_scope],
            [],
            self.manifest_id,
        )

        self.assertEqual("ok", result["status"])
        self.assertEqual(["evidence:MSTR:price"], [row["id"] for row in result["nodeRows"]])

    def test_retention_protects_nodes_used_by_another_relation_generation(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        driver = MagicMock()
        tx = driver.transaction.return_value.__enter__.return_value
        imported = ((None, None, None, None, SimpleNamespace(READ="read", WRITE="write")), "")
        external_reference = {
            "nodeStorageId": "ontology-storage:state-old",
            "relationBox": "ABox",
            "relationSnapshotId": self.old_link_generation,
            "relationStorageId": "ontology-storage:link-old",
        }

        with patch.object(
            repository,
            "read_rows_in_transaction",
            return_value=[external_reference],
        ) as read_rows, patch.object(
            repository,
            "box_snapshot_instance_exists",
        ) as instance_exists:
            result = repository.delete_box_snapshot_rows_in_batches(
                driver,
                imported,
                "ABox",
                self.old_state_generation,
            )

        self.assertEqual("protected-external-relation-reference", result["status"])
        self.assertEqual(0, result["deletedBatchCount"])
        self.assertTrue(result["resumeRequired"])
        self.assertEqual([external_reference], result["externalRelationReferences"])
        instance_exists.assert_not_called()
        driver.transaction.assert_called_once_with(repository.database, "read")
        query = read_rows.call_args.args[1]
        self.assertIn("links (source: $n)", query)
        self.assertIn("links (target: $n)", query)
        self.assertIn("$relationSnapshotId !=", query)
        tx.query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
