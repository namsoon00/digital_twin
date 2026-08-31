import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from digital_twin.infrastructure.typedb_ontology import (
    TypeDBOntologyGraphRepository,
    ontology_row_content_fingerprint,
    ontology_storage_id,
    relation_row_id,
)


class TypeDBCurrentStateDeltaContractTests(unittest.TestCase):
    def test_current_state_delta_preserves_semantic_rows_and_bounded_reads(self):
        self._assert_polling_lifecycle_is_not_material()
        self._assert_only_adjacent_relations_are_rebound()
        self._assert_inventory_keeps_legacy_rows_visible()
        self._assert_post_write_verification_reads_exact_storage_ids()

    def _assert_polling_lifecycle_is_not_material(self):
        base = {
            "id": "price:005930",
            "kind": "price-observation",
            "ontologyBox": "ABox",
            "scopeId": "scope:market:005930",
            "snapshotId": "abox-current:scope-market:a",
            "observedAt": "2026-08-31T00:00:00Z",
            "propertiesJson": json.dumps({
                "currentPrice": 100,
                "sourceFetchedAt": "2026-08-31T00:00:00Z",
                "marketSessionElapsedPct": 10.0,
                "freshnessStatus": "fresh",
            }),
        }
        lifecycle_only = {
            **base,
            "observedAt": "2026-08-31T00:01:00Z",
            "propertiesJson": json.dumps({
                "currentPrice": 100,
                "sourceFetchedAt": "2026-08-31T00:01:00Z",
                "marketSessionElapsedPct": 12.0,
                "freshnessStatus": "fresh",
            }),
        }
        material_change = {
            **lifecycle_only,
            "propertiesJson": json.dumps({
                "currentPrice": 101,
                "sourceFetchedAt": "2026-08-31T00:01:00Z",
                "marketSessionElapsedPct": 12.0,
                "freshnessStatus": "fresh",
            }),
        }

        self.assertEqual(
            ontology_row_content_fingerprint(base, "node"),
            ontology_row_content_fingerprint(lifecycle_only, "node"),
        )
        self.assertNotEqual(
            ontology_row_content_fingerprint(base, "node"),
            ontology_row_content_fingerprint(material_change, "node"),
        )

    def _assert_only_adjacent_relations_are_rebound(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        snapshot_id = "abox-current:scope-market:a"
        scope_id = "scope:market:005930"
        nodes = [
            {
                "id": entity_key,
                "kind": "stock" if entity_key.startswith("stock:") else "price-observation",
                "ontologyBox": "ABox",
                "scopeId": scope_id,
                "snapshotId": snapshot_id,
                "propertiesJson": json.dumps({"currentPrice": price}),
            }
            for entity_key, price in [
                ("stock:005930", 100),
                ("price:005930", 101),
                ("stock:000660", 200),
                ("price:000660", 200),
            ]
        ]
        relations = []
        for symbol in ["005930", "000660"]:
            relation = {
                "source": "stock:" + symbol,
                "target": "price:" + symbol,
                "type": "HAS_PRICE",
                "ontologyBox": "ABox",
                "scopeId": scope_id,
                "snapshotId": snapshot_id,
                "propertiesJson": "{}",
            }
            relation.update({
                "sourceStorageId": ontology_storage_id(
                    relation,
                    relation["source"],
                    "node",
                ),
                "targetStorageId": ontology_storage_id(
                    relation,
                    relation["target"],
                    "node",
                ),
            })
            relations.append(relation)

        inventory = {"nodes": {}, "relations": {}}
        for node in nodes:
            storage_id = ontology_storage_id(node, node["id"], "node")
            inventory["nodes"][storage_id] = {
                "storageId": storage_id,
                "scopeId": scope_id,
                "contentFingerprint": (
                    "old-price"
                    if node["id"] == "price:005930"
                    else ontology_row_content_fingerprint(node, "node")
                ),
            }
        for relation in relations:
            storage_id = ontology_storage_id(
                relation,
                relation_row_id(relation),
                "relation",
            )
            inventory["relations"][storage_id] = {
                "storageId": storage_id,
                "scopeId": scope_id,
                "contentFingerprint": ontology_row_content_fingerprint(
                    relation,
                    "relation",
                ),
            }

        delta = repository.current_state_delta_plan(nodes, relations, inventory)

        self.assertEqual(1, len(delta["nodeRowsToInsert"]))
        self.assertEqual(1, len(delta["relationRowsToInsert"]))
        self.assertEqual(
            "stock:005930",
            delta["relationRowsToInsert"][0]["source"],
        )
        self.assertEqual(1, len(delta["reusedRelationRows"]))
        self.assertEqual(
            "stock:000660",
            delta["reusedRelationRows"][0]["source"],
        )

    def _assert_inventory_keeps_legacy_rows_visible(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        transaction = MagicMock()
        transaction.__enter__.return_value = transaction
        transaction.__exit__.return_value = False
        driver = MagicMock()
        driver.transaction.return_value = transaction
        imported = ((None, None, None, None, SimpleNamespace(READ="read")), None)

        def rows(_tx, _query, _columns, label=""):
            suffix = "node" if rows.calls < 2 else "relation"
            rows.calls += 1
            if label == "typedb.current-state-slot-inventory":
                return [{
                    "storageId": "storage:" + suffix,
                    "scopeId": "scope:market:005930",
                    "snapshotId": "abox-current:scope-market:a",
                }]
            if label == "typedb.current-state-slot-content":
                return [{
                    "storageId": "storage:" + suffix,
                    "scopeId": "scope:market:005930",
                    "snapshotId": "abox-current:scope-market:a",
                    "contentFingerprint": "fingerprint:" + suffix,
                }]
            self.fail("Unexpected current-state inventory query")

        rows.calls = 0
        with patch.object(
            repository,
            "read_rows_in_transaction",
            side_effect=rows,
        ) as reader:
            inventory = repository.current_state_slot_inventory(
                driver,
                imported,
                ["abox-current:scope-market:a"],
            )

        self.assertEqual(4, reader.call_count)
        self.assertTrue(all(
            " try {" not in invocation.args[1]
            for invocation in reader.call_args_list
        ))
        self.assertEqual(
            "fingerprint:node",
            inventory["nodes"]["storage:node"]["contentFingerprint"],
        )
        self.assertEqual(
            "fingerprint:relation",
            inventory["relations"]["storage:relation"]["contentFingerprint"],
        )

    def _assert_post_write_verification_reads_exact_storage_ids(self):
        repository = TypeDBOntologyGraphRepository("127.0.0.1:1729")
        transaction = MagicMock()
        transaction.__enter__.return_value = transaction
        transaction.__exit__.return_value = False
        driver = MagicMock()
        driver.transaction.return_value = transaction
        imported = ((None, None, None, None, SimpleNamespace(READ="read")), None)

        def rows(_tx, query, _columns, label=""):
            self.assertEqual(
                "typedb.current-state-storage-verification",
                label,
            )
            if "isa ontology-node" in query:
                return [{
                    "storageId": "node-storage:new",
                    "scopeId": "scope:market:005930",
                    "snapshotId": "abox-current:scope-market:a",
                    "contentFingerprint": "node-fingerprint:new",
                }]
            return [{
                "storageId": "relation-storage:new",
                "scopeId": "scope:market:005930",
                "snapshotId": "abox-current:scope-market:a",
                "contentFingerprint": "relation-fingerprint:new",
            }]

        with patch.object(
            repository,
            "read_rows_in_transaction",
            side_effect=rows,
        ) as reader, patch(
            "digital_twin.infrastructure.typedb_ontology.runtime_settings",
            return_value={"typedbABoxCurrentStateInventoryBatchSize": "32"},
        ):
            inventory = repository.current_state_storage_inventory(
                driver,
                imported,
                ["node-storage:new"],
                ["relation-storage:new"],
            )

        self.assertEqual(2, reader.call_count)
        self.assertEqual(
            "node-fingerprint:new",
            inventory["nodes"]["node-storage:new"]["contentFingerprint"],
        )
        self.assertEqual(
            "relation-fingerprint:new",
            inventory["relations"]["relation-storage:new"]["contentFingerprint"],
        )
        for invocation in reader.call_args_list:
            query = invocation.args[1]
            self.assertIn("ontology-storage-id", query)
            self.assertIn("ontology-content-fingerprint", query)
