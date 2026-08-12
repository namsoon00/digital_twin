import unittest
from datetime import datetime, timedelta, timezone
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


class FakeEventPublisher:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class FakeScopeRepairOutbox:
    def __init__(self):
        self.calls = []

    def enqueue_scope_repair(self, **payload):
        self.calls.append(dict(payload))
        return {
            "status": "queued-durable-scope-repair",
            "jobIds": ["scope-repair-job:1"],
            "queuedSymbolCount": len(payload.get("repair_requests_by_symbol") or {}),
            "missingSymbols": [],
        }


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


class ManifestInventoryRepository(FakeOntologyRepository):
    def __init__(self):
        super().__init__()
        self.inventories = {
            "portfolio:local:main": {
                "status": "ok",
                "storedManifestCount": 14,
                "inactiveManifestCount": 13,
            },
            "market:shared:kr": {
                "status": "ok",
                "storedManifestCount": 2,
                "inactiveManifestCount": 1,
            },
            "knowledge:shared:news": {
                "status": "ok",
                "storedManifestCount": 1,
                "inactiveManifestCount": 0,
            },
        }

    def scoped_abox_manifest_inventory(self, world_id):
        return dict(self.inventories[world_id])


class IntegrityAuditRepository(FakeOntologyRepository):
    def __init__(self):
        super().__init__()
        self.audit_calls = []

    def scoped_abox_integrity_audit(self, world_id="", cursor=0, limit=20):
        self.audit_calls.append({"worldId": world_id, "cursor": cursor, "limit": limit})
        return {
            "status": "repair-required",
            "worldId": world_id,
            "activeScopeCount": 44,
            "checkedScopeCount": 7,
            "mismatchCount": 1,
            "mismatches": [{
                "scopeId": "symbol:005930:flow",
                "symbol": "005930",
                "scopeFamily": "flow",
            }],
            "nextCursor": 7,
            "cycleCompleted": False,
            "readOnly": True,
            "automaticFullProjectionUsed": False,
        }


class OntologyMaintenanceRunnerTests(unittest.TestCase):
    def test_maintenance_rotates_read_only_scope_integrity_audit_without_full_projection(self):
        repository = IntegrityAuditRepository()
        store = FakeStateStore()
        runner = OntologyMaintenanceRunner(
            repository,
            state_store=store,
            settings={
                "ontologyAboxMaintenanceWorldTypes": "portfolio",
                "ontologyScopeIntegrityAuditIntervalMinutes": "30",
                "ontologyScopeIntegrityAuditBatchSize": "7",
            },
        )

        result = runner.run_once()

        audit = result["maintenance"]["scopeIntegrityAudit"]
        self.assertEqual("repair-required", audit["status"])
        self.assertEqual(1, audit["mismatchCount"])
        self.assertEqual(7, audit["checkedScopeCount"])
        self.assertTrue(audit["readOnly"])
        self.assertFalse(audit["automaticFullProjectionUsed"])
        self.assertEqual([{
            "worldId": "portfolio:local:main",
            "cursor": 0,
            "limit": 7,
        }], repository.audit_calls)
        stored = store.payload["scopeIntegrityAuditByWorld"]["portfolio:local:main"]
        self.assertEqual(7, stored["nextCursor"])

    def test_integrity_mismatch_queues_one_bounded_maintenance_repair(self):
        repository = IntegrityAuditRepository()
        store = FakeStateStore()
        publisher = FakeEventPublisher()
        outbox = FakeScopeRepairOutbox()
        runner = OntologyMaintenanceRunner(
            repository,
            state_store=store,
            event_publisher=publisher,
            scope_repair_outbox=outbox,
            settings={
                "ontologyAboxMaintenanceWorldTypes": "portfolio",
                "ontologyScopeIntegrityAuditIntervalMinutes": "30",
            },
        )

        result = runner.run_once()

        repair = result["maintenance"]["scopeRepair"]
        self.assertEqual("queued-durable-scope-repair", repair["status"])
        self.assertEqual(["005930"], repair["symbols"])
        self.assertEqual("ontology-world-projection-outbox", repair["workBoundary"])
        self.assertEqual(1, len(outbox.calls))
        self.assertEqual(1, len(publisher.events))
        event = publisher.events[0]
        self.assertEqual("ontology.scope-integrity-repair-requested", event.name)
        self.assertEqual(
            ["symbol:005930:flow"],
            event.payload["scopeRepairRequestsBySymbol"]["005930"]["scopeIds"],
        )
        self.assertFalse(repair["automaticFullProjectionUsed"])

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
        self.assertEqual(6, repository.calls[0]["maxAboxDeleteBatches"])
        self.assertEqual(150, repository.calls[0]["aboxDeleteBatchSize"])
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
        self.assertEqual(0, status["worldCount"])
        self.assertEqual("durable-maintenance-state", status["worldInventorySource"])
        self.assertEqual([], status["knownWorldIds"])
        self.assertEqual(100, status["policy"]["criticalInactiveManifestCount"])
        self.assertEqual(8, status["policy"]["maxDeleteBatchesPerRun"])
        self.assertEqual(150, status["policy"]["deleteBatchSize"])

    def test_live_manifest_inventory_prioritizes_backlogged_world_and_replaces_stale_state(self):
        repository = ManifestInventoryRepository()
        store = FakeStateStore({
            "nextWorldId": "market:shared:kr",
            "backlogByWorld": {
                "portfolio:local:main": {
                    "lastInactiveManifestCount": 485,
                    "inventoryAvailable": False,
                },
            },
        })
        runner = OntologyMaintenanceRunner(
            repository,
            state_store=store,
            settings={"ontologyAboxMaintenancePriorityInactiveManifestCount": "8"},
        )

        result = runner.run_once()

        self.assertEqual("portfolio:local:main", result["worldId"])
        self.assertEqual("inactive-manifest-priority", result["maintenance"]["worldSelection"]["mode"])
        self.assertEqual(13, result["maintenance"]["manifestInventory"]["inactiveManifestCount"])
        self.assertEqual(4, store.payload["backlogByWorld"]["portfolio:local:main"]["lastInactiveManifestCount"])
        self.assertEqual(1, store.payload["backlogByWorld"]["market:shared:kr"]["lastInactiveManifestCount"])
        self.assertEqual(0, store.payload["backlogByWorld"]["knowledge:shared:news"]["lastInactiveManifestCount"])

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

    def test_pending_native_activation_preserves_verified_manifest_backlog(self):
        class PendingActivationRepository(ManifestInventoryRepository):
            def run_deferred_maintenance(self, payload):
                self.calls.append(dict(payload or {}))
                return {
                    "status": "ok",
                    "worldId": payload.get("worldId"),
                    "abox": {
                        "status": "skipped",
                        "reason": "Scoped ABox activation is pending native inference.",
                    },
                }

        repository = PendingActivationRepository()
        store = FakeStateStore()
        runner = OntologyMaintenanceRunner(repository, state_store=store)

        result = runner.run_once()

        self.assertEqual("deferred-pending-abox-activation", result["status"])
        self.assertEqual(10, result["retryAfterSeconds"])
        self.assertFalse(result["maintenance"]["inventoryAvailable"])
        self.assertEqual("deferred", result["maintenance"]["health"]["state"])
        self.assertEqual(
            13,
            store.payload["backlogByWorld"]["portfolio:local:main"]["lastInactiveManifestCount"],
        )

    def test_maintenance_yields_to_pending_reasoning_work(self):
        repository = FakeOntologyRepository()
        runner = OntologyMaintenanceRunner(
            repository,
            state_store=FakeStateStore(),
            reasoning_queue_probe=lambda: {
                "status": "healthy",
                "effectivePendingCount": 2,
                "runningEntryCount": 1,
                "pendingSymbolCount": 1,
            },
        )

        result = runner.run_once()

        self.assertEqual("deferred-reasoning-queue", result["status"])
        self.assertEqual(2, result["reasoningQueue"]["effectivePendingCount"])
        self.assertEqual([], repository.calls)
        self.assertEqual("active-reasoning-lease", result["backgroundFairness"]["reasonCode"])
        self.assertEqual(10, result["retryAfterSeconds"])

    def test_aged_verified_priority_backlog_requests_a_bounded_reasoning_yield(self):
        now = datetime.now(timezone.utc)
        store = FakeStateStore({
            "reasoningQueueDeferredSinceAt": (
                now - timedelta(minutes=10)
            ).isoformat().replace("+00:00", "Z"),
            "backlogByWorld": {
                "portfolio:local:main": {
                    "inventoryAvailable": True,
                    "lastInactiveManifestCount": 20,
                    "lastInventoryObservedAt": now.isoformat().replace("+00:00", "Z"),
                },
            },
        })
        runner = OntologyMaintenanceRunner(
            FakeOntologyRepository(),
            state_store=store,
            settings={
                "ontologyAboxMaintenanceYieldEnabled": "1",
                "ontologyAboxMaintenanceYieldAfterSeconds": "30",
                "ontologyAboxMaintenanceYieldWindowSeconds": "30",
                "ontologyAboxMaintenanceYieldRequestTtlSeconds": "90",
            },
            reasoning_queue_probe=lambda: {
                "effectivePendingCount": 2,
                "runningEntryCount": 1,
            },
        )

        result = runner.reasoning_queue_deferral()
        maintenance_yield = runner.maintenance_yield_status(store.payload)

        self.assertEqual({}, result)
        self.assertTrue(maintenance_yield["active"])
        self.assertEqual("portfolio:local:main", maintenance_yield["worldId"])
        self.assertEqual(20, maintenance_yield["inactiveManifestCount"])
        self.assertEqual(30, maintenance_yield["retryAfterSeconds"])
        self.assertGreaterEqual(maintenance_yield["requestRemainingSeconds"], 80)
        self.assertTrue(store.payload["maintenanceYieldRequest"]["expiresAt"])

    def test_successful_maintenance_consumes_an_active_yield_request(self):
        now = datetime.now(timezone.utc)
        store = FakeStateStore({
            "maintenanceYieldRequest": {
                "requestedAt": now.isoformat().replace("+00:00", "Z"),
                "expiresAt": (now + timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
                "worldId": "portfolio:local:main",
                "inactiveManifestCount": 20,
            },
            "maintenanceYieldLastRequestedAt": now.isoformat().replace("+00:00", "Z"),
        })
        runner = OntologyMaintenanceRunner(
            FakeOntologyRepository(),
            state_store=store,
            settings={"ontologyAboxMaintenanceYieldEnabled": "1"},
            reasoning_queue_probe=lambda: {"effectivePendingCount": 0, "runningEntryCount": 0},
        )

        result = runner.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual("consumed", result["maintenance"]["maintenanceYield"]["status"])
        self.assertEqual({}, store.payload["maintenanceYieldRequest"])
        self.assertTrue(store.payload["maintenanceYieldLastGrantedAt"])
        self.assertEqual(1, runner.ontology_repository.calls[0]["maxInactiveManifests"])
        self.assertEqual(2, runner.ontology_repository.calls[0]["maxAboxDeleteBatches"])
        self.assertEqual(150, runner.ontology_repository.calls[0]["aboxDeleteBatchSize"])
        self.assertTrue(result["maintenance"]["capacityBudget"]["yieldBounded"])

    def test_pending_activation_releases_active_yield_for_reasoning_recovery(self):
        class PendingActivationRepository(ManifestInventoryRepository):
            def run_deferred_maintenance(self, payload):
                self.calls.append(dict(payload or {}))
                return {
                    "status": "ok",
                    "worldId": payload.get("worldId"),
                    "abox": {
                        "status": "skipped",
                        "reason": "Scoped ABox activation is pending native inference.",
                    },
                }

        now = datetime.now(timezone.utc)
        store = FakeStateStore({
            "maintenanceYieldRequest": {
                "requestedAt": now.isoformat().replace("+00:00", "Z"),
                "expiresAt": (now + timedelta(seconds=120)).isoformat().replace("+00:00", "Z"),
                "worldId": "portfolio:local:main",
                "inactiveManifestCount": 13,
            },
            "maintenanceYieldLastRequestedAt": now.isoformat().replace("+00:00", "Z"),
        })
        runner = OntologyMaintenanceRunner(
            PendingActivationRepository(),
            state_store=store,
            settings={"ontologyAboxMaintenanceYieldEnabled": "1"},
            reasoning_queue_probe=lambda: {"effectivePendingCount": 0, "runningEntryCount": 0},
        )

        result = runner.run_once()

        self.assertEqual("deferred-pending-abox-activation", result["status"])
        self.assertEqual(
            "released-pending-abox-activation",
            result["maintenance"]["maintenanceYield"]["status"],
        )
        self.assertEqual({}, store.payload["maintenanceYieldRequest"])
        self.assertTrue(store.payload["maintenanceYieldLastReleasedAt"])
        self.assertNotIn("maintenanceYieldLastGrantedAt", store.payload)

    def test_aged_maintenance_gets_a_bounded_turn_when_no_reasoning_lease_is_active(self):
        repository = FakeOntologyRepository()
        store = FakeStateStore({
            "reasoningQueueDeferredSinceAt": (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            ).isoformat().replace("+00:00", "Z"),
        })
        runner = OntologyMaintenanceRunner(
            repository,
            state_store=store,
            settings={"ontologyAboxMaintenanceMaxReasoningDeferralSeconds": "30"},
            reasoning_queue_probe=lambda: {"effectivePendingCount": 2, "runningEntryCount": 0},
        )

        result = runner.run_once()

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["backgroundFairness"]["fairnessGranted"])
        self.assertEqual("aged-background-turn", result["backgroundFairness"]["reasonCode"])
        self.assertIn("lastFairnessCompletedAt", store.payload)
        self.assertEqual(1, len(repository.calls))

    def test_timeout_recovery_clears_only_repository_verified_dead_leases(self):
        class RecoveringRepository(FakeOntologyRepository):
            def recover_all_dead_local_scoped_abox_write_leases(self):
                return {
                    "status": "cleared",
                    "clearedCount": 2,
                    "clearedWorldIds": [
                        "portfolio:local:main",
                        "system:typedb-projection-coordinator",
                    ],
                    "worldCount": 3,
                }

        result = OntologyMaintenanceRunner(
            RecoveringRepository(),
            state_store=FakeStateStore(),
        ).recover_dead_projection_leases()

        self.assertEqual("cleared", result["status"])
        self.assertEqual(2, result["clearedCount"])
        self.assertEqual(3, result["worldCount"])

    def test_aged_queue_attempts_coordinator_without_starting_cooldown_when_busy(self):
        class BusyCoordinatorRepository(FakeOntologyRepository):
            def acquire_projection_coordinator_lease(self, _owner, world_id=""):
                return {
                    "acquired": False,
                    "status": "held",
                    "requestedWorldId": world_id,
                    "recommendedRetryAfterSeconds": 10,
                    "reason": "another TypeDB projection is active",
                }

        store = FakeStateStore({
            "reasoningQueueDeferredSinceAt": (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            ).isoformat().replace("+00:00", "Z"),
        })
        repository = BusyCoordinatorRepository()
        runner = OntologyMaintenanceRunner(
            repository,
            state_store=store,
            settings={"ontologyAboxMaintenanceMaxReasoningDeferralSeconds": "30"},
            reasoning_queue_probe=lambda: {"effectivePendingCount": 2, "runningEntryCount": 0},
        )

        result = runner.run_once()

        self.assertEqual("deferred-projection-coordinator", result["status"])
        self.assertEqual([], repository.calls)
        self.assertNotIn("lastFairnessCompletedAt", store.payload)

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

    def test_runtime_budget_caps_adaptive_delete_batches_before_process_timeout(self):
        runner = OntologyMaintenanceRunner(
            FakeOntologyRepository(),
            state_store=FakeStateStore(),
            settings={
                "ontologyAboxMaintenanceExecutionTimeoutSeconds": "180",
                "ontologyAboxMaintenanceExecutionReserveSeconds": "60",
                "ontologyAboxMaintenanceEstimatedDeleteBatchSeconds": "20",
            },
        )

        budget = runner.capacity_maintenance_budget(
            runner.policy(),
            {"effectiveMaxDeleteBatches": 16},
            {"capacityPriority": False},
        )

        self.assertEqual(16, budget["requestedAboxDeleteBatches"])
        self.assertEqual(6, budget["runtimeSafeDeleteBatchCap"])
        self.assertEqual(6, budget["maxAboxDeleteBatches"])

    def test_capacity_pressure_prioritizes_bounded_cleanup_over_pending_reasoning(self):
        repository = FakeOntologyRepository()
        runner = OntologyMaintenanceRunner(
            repository,
            state_store=FakeStateStore(),
            settings={
                "typedbCapacityMaintenanceMaxManifests": "10",
                "typedbCapacityMaintenanceMaxDeleteBatches": "12",
                "typedbCapacityMaintenanceDeleteBatchSize": "250",
            },
            reasoning_queue_probe=lambda: {"effectivePendingCount": 4, "runningEntryCount": 0},
            capacity_guard=lambda: {
                "ready": True,
                "capacityPriority": True,
                "bypassReasoningDeferral": True,
                "mode": "write-throttled",
            },
        )

        result = runner.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual(10, repository.calls[0]["maxInactiveManifests"])
        self.assertEqual(6, repository.calls[0]["maxAboxDeleteBatches"])
        self.assertEqual(250, repository.calls[0]["aboxDeleteBatchSize"])
        self.assertEqual(1, result["maintenance"]["capacityBudget"]["capacityPriority"])
        self.assertEqual(12, result["maintenance"]["capacityBudget"]["requestedAboxDeleteBatches"])

    def test_capacity_rotation_wait_blocks_cleanup_writes(self):
        repository = FakeOntologyRepository()
        result = OntologyMaintenanceRunner(
            repository,
            state_store=FakeStateStore(),
            capacity_guard=lambda: {
                "ready": False,
                "mode": "rotation-required",
                "reason": "rotation required",
            },
        ).run_once()

        self.assertEqual("deferred-capacity", result["status"])
        self.assertEqual([], repository.calls)


if __name__ == "__main__":
    unittest.main()
