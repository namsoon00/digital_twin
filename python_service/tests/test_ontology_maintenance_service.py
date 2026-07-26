import unittest
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.ontology_maintenance_service import OntologyMaintenanceRunner


class FakeStateStore:
    def __init__(self, payload=None):
        self.payload = dict(payload or {})

    def load(self):
        return dict(self.payload)

    def replace(self, payload):
        self.payload = dict(payload or {})


class FakeOntologyRepository:
    def __init__(self):
        self.calls = []

    def list_ontology_worlds(self):
        return [
            {"worldId": "portfolio:local:main", "worldType": "portfolio"},
            {"worldId": "market:shared:kr", "worldType": "market"},
            {"worldId": "knowledge:shared:news", "worldType": "knowledge"},
        ]

    def run_deferred_maintenance(self, payload):
        self.calls.append(dict(payload or {}))
        return {
            "status": "ok",
            "worldId": payload.get("worldId"),
            "abox": {
                "completedInactiveManifestCount": 12,
                "remainingInactiveManifestCount": 4,
                "removedManifestIds": ["manifest:one", "manifest:two"],
                "deletedBatchCount": 3,
            },
        }


class OntologyMaintenanceRunnerTests(unittest.TestCase):
    def test_round_robin_uses_bounded_policy_and_persists_cursor(self):
        repository = FakeOntologyRepository()
        store = FakeStateStore()
        runner = OntologyMaintenanceRunner(
            repository,
            state_store=store,
            settings={
                "ontologyAboxMaintenanceWorldTypes": "portfolio,market",
                "ontologyAboxMaintenanceMaxManifestsPerRun": "7",
                "ontologyAboxMaintenanceKeepInactiveManifestCount": "0",
            },
        )

        first = runner.run_once()
        second = runner.run_once()

        self.assertEqual("market:shared:kr", first["worldId"])
        self.assertEqual("portfolio:local:main", second["worldId"])
        self.assertEqual(7, repository.calls[0]["maxInactiveManifests"])
        self.assertEqual(2, repository.calls[0]["maxAboxDeleteBatches"])
        self.assertEqual(50, repository.calls[0]["aboxDeleteBatchSize"])
        self.assertEqual(0, repository.calls[0]["keepInactiveManifests"])
        self.assertEqual(2, first["maintenance"]["removedManifestCount"])
        self.assertEqual("draining", first["maintenance"]["health"]["state"])
        self.assertEqual("market:shared:kr", store.payload["nextWorldId"])

    def test_status_exposes_configured_retention_policy(self):
        runner = OntologyMaintenanceRunner(
            FakeOntologyRepository(),
            state_store=FakeStateStore({"lastRunAt": "2026-07-26T00:00:00+00:00"}),
            settings={
                "ontologyAboxMaintenanceWarningInactiveManifestCount": "30",
                "ontologyAboxMaintenanceCriticalInactiveManifestCount": "100",
            },
        )

        status = runner.status()

        self.assertTrue(status["enabled"])
        self.assertEqual(3, status["worldCount"])
        self.assertEqual(100, status["policy"]["criticalInactiveManifestCount"])
        self.assertEqual(2, status["policy"]["maxDeleteBatchesPerRun"])
        self.assertEqual(50, status["policy"]["deleteBatchSize"])

    def test_deferred_writer_lease_does_not_claim_an_empty_inventory(self):
        class BusyRepository(FakeOntologyRepository):
            def run_deferred_maintenance(self, payload):
                return {
                    "status": "deferred-write-lease",
                    "worldId": payload.get("worldId"),
                    "reason": "A live ABox activation owns the graph writer lease.",
                }

        result = OntologyMaintenanceRunner(BusyRepository(), state_store=FakeStateStore()).run_once()

        self.assertEqual("deferred-write-lease", result["status"])
        self.assertFalse(result["maintenance"]["inventoryAvailable"])
        self.assertEqual("deferred", result["maintenance"]["health"]["state"])
        self.assertIsNone(result["maintenance"]["health"]["inactiveManifestCount"])

    def test_maintenance_yields_to_pending_reasoning_work(self):
        repository = FakeOntologyRepository()
        runner = OntologyMaintenanceRunner(
            repository,
            state_store=FakeStateStore(),
            reasoning_queue_probe=lambda: {
                "status": "healthy",
                "effectivePendingCount": 2,
                "pendingSymbolCount": 1,
            },
        )

        result = runner.run_once()

        self.assertEqual("deferred-reasoning-queue", result["status"])
        self.assertEqual(2, result["reasoningQueue"]["effectivePendingCount"])
        self.assertEqual([], repository.calls)

    def test_maintenance_yields_when_another_world_owns_typedb_writer(self):
        class BusyCoordinatorRepository(FakeOntologyRepository):
            def acquire_projection_coordinator_lease(self, _owner, world_id=""):
                return {
                    "acquired": False,
                    "status": "held",
                    "requestedWorldId": world_id,
                    "recommendedRetryAfterSeconds": 9,
                    "reason": "another TypeDB projection is active",
                }

        repository = BusyCoordinatorRepository()
        result = OntologyMaintenanceRunner(repository, state_store=FakeStateStore()).run_once()

        self.assertEqual("deferred-projection-coordinator", result["status"])
        self.assertEqual(9, result["retryAfterSeconds"])
        self.assertEqual([], repository.calls)

    def test_persistent_critical_backlog_gradually_increases_only_delete_batches(self):
        class CriticalRepository(FakeOntologyRepository):
            def list_ontology_worlds(self):
                return [{"worldId": "market:shared:kr", "worldType": "market"}]

            def run_deferred_maintenance(self, payload):
                self.calls.append(dict(payload or {}))
                return {
                    "status": "partial",
                    "worldId": payload.get("worldId"),
                    "abox": {
                        "completedInactiveManifestCount": 180,
                        "remainingInactiveManifestCount": 180,
                        "removedManifestIds": [],
                        "deletedBatchCount": payload.get("maxAboxDeleteBatches"),
                    },
                }

        repository = CriticalRepository()
        runner = OntologyMaintenanceRunner(
            repository,
            state_store=FakeStateStore(),
            settings={
                "ontologyAboxMaintenanceMaxDeleteBatchesPerRun": "2",
                "ontologyAboxMaintenanceAdaptiveDrainMaxDeleteBatchesPerRun": "4",
                "ontologyAboxMaintenanceAdaptiveDrainCriticalRunsBeforeIncrease": "2",
            },
        )

        first = runner.run_once()
        second = runner.run_once()
        third = runner.run_once()

        self.assertEqual([2, 2, 3], [call["maxAboxDeleteBatches"] for call in repository.calls])
        self.assertEqual("adaptive-drain", third["maintenance"]["adaptiveDrain"]["mode"])
        self.assertEqual(3, third["maintenance"]["adaptiveDrain"]["criticalDrainRuns"])


if __name__ == "__main__":
    unittest.main()
