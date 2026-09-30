"""Reference protection and progress across small, repeated cleanup turns."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from digital_twin.modules.reasoning.infrastructure.graph_maintenance.manifests import (
    prune_inactive_scoped_abox_manifests_in_driver as prune,
)
from digital_twin.modules.reasoning.application.ontology_maintenance_service import OntologyMaintenanceRunner
from digital_twin.modules.reasoning.infrastructure.graph_maintenance.runner import run_deferred_maintenance


class ScopedRetentionSafetyTests(unittest.TestCase):
    def setUp(self):
        def manifest(name, generations):
            return {"status": "ok", "worldviewManifestId": name,
                    "scopeGenerationIds": {str(i): value for i, value in enumerate(generations)}}

        self.metadata = {
            "active": manifest("active", ["live", "shared"]),
            "rollback": manifest("rollback", ["rollback-only", "shared"]),
            "old-2": manifest("old-2", ["old-b", "live", "rollback-only"]),
            "old-1": manifest("old-1", ["old-a", "shared", "rollback-only"]),
        }
        self.rows = {"old-a": 1, "old-b": 2, "live": 1, "shared": 1, "rollback-only": 1}
        self.markers = ["old-1", "old-2", "rollback", "active"]
        self.driver = MagicMock()
        self.imported = ((None, None, None, None, SimpleNamespace(READ="read")), None)
        self.store = SimpleNamespace(
            database="isolated-contract",
            read_rows_in_transaction=Mock(side_effect=self.read_presence),
            active_abox_metadata=Mock(side_effect=lambda world: self.metadata["active"]),
            pending_abox_activation=Mock(return_value={"status": "empty"}),
            worldview_manifest_marker_identity_rows=Mock(side_effect=lambda world: [
                {"worldviewManifestId": name, "updatedAt": str(i)}
                for i, name in enumerate(self.markers)
            ]),
            scoped_manifest_metadata=Mock(side_effect=lambda name, world: self.metadata.get(name, {})),
            delete_box_snapshot_rows_in_batches=Mock(side_effect=self.delete_generation),
            delete_worldview_manifest_markers_batch=Mock(side_effect=self.delete_markers),
        )

    def read_presence(self, tx, query, columns, **options):
        return [
            {"snapshotId": name, "count": count}
            for name, count in self.rows.items()
            if count and json.dumps(name) in query
        ]

    def delete_generation(self, driver, imported, box, generation, **options):
        remaining = self.rows[generation]
        deleted = min(remaining, options["max_batches"])
        self.rows[generation] -= deleted
        return {"status": "partial" if self.rows[generation] else "ok", "deletedBatchCount": deleted}

    def delete_markers(self, driver, imported, names, **options):
        self.markers = [name for name in self.markers if name not in names]
        return {"status": "ok", "deletedBatchCount": 1, "removedManifestIds": names}

    def run_cleanup(self, budget=2):
        return prune(self.store, self.driver, self.imported, world_id="world", active_manifest_id="active", keep_inactive_count=1,
                     max_manifests=10, max_delete_batches=budget, delete_batch_size=50,
                     max_duration_seconds=45)

    def test_small_turns_finish_markers_and_preserve_live_and_rollback(self):
        first = self.run_cleanup()
        self.assertEqual(["old-1"], first["removedManifestIds"])
        self.assertEqual(2, first["deletedBatchCount"])
        self.assertEqual(1, first["removedRetiredScopeGenerationCount"])
        second = self.run_cleanup()
        self.assertEqual([], second["removedManifestIds"])
        third = self.run_cleanup(budget=1)
        self.assertEqual(["old-2"], third["removedManifestIds"])
        self.assertEqual(0, third["removedRetiredScopeGenerationCount"])
        self.assertEqual(1, third["alreadyEmptyRetiredScopeGenerationCount"])
        self.assertEqual(["rollback", "active"], self.markers)
        self.assertEqual({"live": 1, "shared": 1, "rollback-only": 1},
                         {key: self.rows[key] for key in ["live", "shared", "rollback-only"]})

    def test_repeated_empty_confirmation_is_not_a_new_deletion(self):
        self.rows["old-a"] = 0
        self.store.delete_worldview_manifest_markers_batch.side_effect = None
        self.store.delete_worldview_manifest_markers_batch.return_value = {"status": "error", "deletedBatchCount": 0}
        for _ in range(2):
            result = self.run_cleanup(budget=1)
            self.assertEqual(0, result["removedRetiredScopeGenerationCount"])
            self.assertEqual(1, result["clearedRetiredScopeGenerationCount"])
            self.assertEqual(1, result["alreadyEmptyRetiredScopeGenerationCount"])
            self.assertTrue(result["resumeRequired"])
        self.assertEqual(2, self.rows["old-b"])
        # Empty proof must come from fresh reads on each retry, never a cached
        # confirmation carried across graph epochs or write turns.
        self.assertEqual(4, self.store.read_rows_in_transaction.call_count)

    def test_large_later_generation_cannot_spend_reserved_marker_batch(self):
        self.rows["old-a"] = 0
        self.rows["old-b"] = 20
        result = self.run_cleanup()
        self.assertEqual(["old-1"], result["removedManifestIds"])
        self.assertEqual(2, result["deletedBatchCount"])
        self.assertEqual(19, self.rows["old-b"])

    def test_missing_or_mismatched_metadata_never_deletes(self):
        for name in ["active", "rollback", "old-1"]:
            original = self.metadata[name]
            for broken in [{}, {**original, "worldviewManifestId": "wrong"},
                           {"status": "ok", "worldviewManifestId": name}]:
                with self.subTest(name=name, broken=broken):
                    self.metadata[name] = broken
                    result = self.run_cleanup()
                    self.assertEqual("blocked-protection-metadata", result["status"])
                    self.store.delete_box_snapshot_rows_in_batches.assert_not_called()
                    self.store.delete_worldview_manifest_markers_batch.assert_not_called()
            self.metadata[name] = original
        # A failed second query must invalidate the first empty result too.
        self.store.read_rows_in_transaction.side_effect = [[], RuntimeError("read interrupted")]
        with self.assertRaises(RuntimeError):
            self.run_cleanup()
        self.store.delete_box_snapshot_rows_in_batches.assert_not_called()
        self.store.delete_worldview_manifest_markers_batch.assert_not_called()

    def test_pending_or_unknown_activation_never_deletes(self):
        for state in [{"status": "pending"}, {"status": "invalid"}, {}]:
            self.store.pending_abox_activation.return_value = state
            result = self.run_cleanup()
            self.assertEqual(0, result["deletedBatchCount"])
            self.store.delete_box_snapshot_rows_in_batches.assert_not_called()

    def test_shared_retired_generation_is_deleted_once(self):
        empty = ["empty-" + str(i) for i in range(130)]
        self.rows.update({name: 0 for name in empty})
        scopes = {str(i): name for i, name in enumerate(empty + ["old-a"])}
        self.metadata["old-1"]["scopeGenerationIds"] = scopes
        self.metadata["old-2"]["scopeGenerationIds"] = scopes
        result = self.run_cleanup()
        self.assertEqual(["old-1", "old-2"], result["removedManifestIds"])
        self.assertEqual(1, result["removedRetiredScopeGenerationCount"])
        self.assertEqual(130, result["alreadyEmptyRetiredScopeGenerationCount"])
        self.assertEqual(3, result["generationPresenceProbeCount"])
        self.assertEqual(131, self.store.read_rows_in_transaction.call_count)
        self.assertEqual(3, self.driver.transaction.call_count)
        self.store.delete_box_snapshot_rows_in_batches.assert_called_once()

    def test_external_reference_and_timeout_preserve_incomplete_marker(self):
        for result in [
            {"status": "protected-external-relation-reference", "deletedBatchCount": 0},
            {"status": "partial", "deletedBatchCount": 1, "timeBudgetExhausted": True},
        ]:
            self.store.delete_box_snapshot_rows_in_batches.side_effect = None
            self.store.delete_box_snapshot_rows_in_batches.return_value = result
            cleaned = self.run_cleanup()
            self.assertTrue(cleaned["resumeRequired"])
            self.assertEqual([], cleaned["removedManifestIds"])
            self.assertEqual(0, cleaned["removedRetiredScopeGenerationCount"])
            self.store.delete_worldview_manifest_markers_batch.assert_not_called()

        # A relation-only result still enters the protected delete path.
        for call in self.store.read_rows_in_transaction.call_args_list:
            self.assertNotIn("isa ontology-node", call.args[1])
            self.assertIn("limit 1;", call.args[1])
        self.store.delete_box_snapshot_rows_in_batches.reset_mock()
        with patch("digital_twin.modules.reasoning.infrastructure.graph_maintenance.manifests.time.monotonic",
                   side_effect=[0, 0, 0, 46]):
            with self.assertRaises(TimeoutError):
                self.run_cleanup()
        self.store.delete_box_snapshot_rows_in_batches.assert_not_called()

    def test_legacy_totals_are_preserved_separately_and_backlog_uses_cleared(self):
        runner = OntologyMaintenanceRunner(SimpleNamespace())
        state = {"backlogByWorld": {"world": {"removedRetiredScopeGenerationCountTotal": 999}}}
        for removed, expected in [(1, 1), (0, 1)]:
            rows = runner.updated_backlog_by_world(
                state, "world", "partial", True, {}, 4, 4, 0, removed,
                8, removed, 0, ["world"], cleared_retired_generation_count=5,
                generation_counter_version="physical-delete-v2",
            )
            self.assertEqual(expected, rows["world"]["removedRetiredScopeGenerationCountTotal"])
            self.assertEqual(999, rows["world"]["legacyGenerationClearConfirmationCountTotal"])
            self.assertEqual(3, rows["world"]["retiredScopeGenerationBacklogCount"])
            state = {"backlogByWorld": rows}
        summary = runner.progress_summary(rows, {})
        self.assertEqual(1, summary["removedRetiredScopeGenerationCountTotal"])
        self.assertEqual(999, summary["legacyGenerationClearConfirmationCountTotal"])

    def test_blocked_cleanup_preserves_previous_backlog_in_worker_status(self):
        state = {"knownWorlds": [{"worldId": "world", "worldType": "portfolio"}],
                 "backlogByWorld": {"world": {"retiredScopeGenerationBacklogCount": 12, "lastProgress": True}}}
        repository = SimpleNamespace(
            acquire_projection_coordinator_lease=Mock(return_value={"acquired": True}),
            release_projection_coordinator_lease=Mock(),
            run_deferred_maintenance=Mock(return_value={
                "status": "partial", "abox": {
                    "status": "blocked-protection-metadata", "completedInactiveManifestCount": 4,
                    "remainingInactiveManifestCount": 4, "deletedBatchCount": 0,
                    "resumeRequired": True,
                },
            }),
        )
        saved = []
        runner = OntologyMaintenanceRunner(
            repository, state_store=SimpleNamespace(load=lambda: state, replace=saved.append),
            reasoning_queue_probe=lambda: {"effectivePendingCount": 0, "processingCount": 0},
        )
        result = runner.run_once()
        self.assertEqual("blocked-protection-metadata", result["status"])
        self.assertFalse(saved[-1]["lastResult"]["inventoryAvailable"])
        self.assertEqual(12, saved[-1]["backlogByWorld"]["world"]["retiredScopeGenerationBacklogCount"])
        self.assertFalse(saved[-1]["backlogByWorld"]["world"]["lastProgress"])

    def test_blocked_cleanup_propagates_without_downstream_deletion(self):
        store = SimpleNamespace(
            address="fixture.invalid",
            acquire_scoped_abox_write_lease=Mock(return_value={"acquired": True}),
            release_scoped_abox_write_lease=Mock(),
            prune_inactive_scoped_abox_manifests=Mock(return_value={
                "status": "blocked-protection-metadata", "resumeRequired": True,
            }),
            read_inference_generation_records=Mock(),
            prune_inferencebox_generations=Mock(),
        )
        result = run_deferred_maintenance(store, {
            "worldId": "world", "maxInactiveManifests": 10,
            "maxAboxDeleteBatches": 2, "aboxDeleteBatchSize": 50,
        }, _bindings=SimpleNamespace(typedb_error_code=lambda error: type(error).__name__))
        self.assertEqual("partial", result["status"])
        self.assertEqual("blocked-protection-metadata", result["abox"]["status"])
        store.read_inference_generation_records.assert_not_called()
        store.prune_inferencebox_generations.assert_not_called()
        store.release_scoped_abox_write_lease.assert_called_once()
