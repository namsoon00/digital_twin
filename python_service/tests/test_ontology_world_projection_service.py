import unittest
from datetime import datetime, timedelta, timezone

from digital_twin.application.ontology_world_projection_service import OntologyWorldProjectionRunner
from digital_twin.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.domain.ontology_projection_payload import serialize_portfolio_ontology
from digital_twin.domain.ontology_worlds import market_world


class FakeOutbox:
    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.completed = []
        self.retries = []
        self.pruned = 0
        self.rebuild_requeue_result = None
        self.rebuild_requeue_limits = []
        self.summary_payload = None

    def claim(self, worker_id, limit, lease_seconds):
        claimed = self.jobs[:limit]
        self.jobs = self.jobs[limit:]
        return claimed

    def complete(self, job_id, worker_id, result):
        self.completed.append((job_id, worker_id, dict(result or {})))
        return True

    def retry(self, job_id, worker_id, reason, max_attempts):
        payload = {
            "status": "pending",
            "jobId": job_id,
            "reason": reason,
            "maxAttempts": max_attempts,
        }
        self.retries.append((job_id, worker_id, payload))
        return payload

    def prune_completed(self, _hours):
        return self.pruned

    def summary(self):
        if self.summary_payload is not None:
            return dict(self.summary_payload)
        return {"enabled": True, "pendingCount": len(self.jobs)}

    def max_payload_bytes(self):
        return 5 * 1024 * 1024

    def requeue_latest_completed(self, limit):
        self.rebuild_requeue_limits.append(limit)
        return dict(self.rebuild_requeue_result or {
            "status": "ok",
            "requeuedCount": 0,
            "requeuedJobIds": [],
        })


class FakeRecorder:
    def __init__(self, result, repository=None):
        self.result = dict(result or {})
        self.calls = []
        self.repository = repository

    def project_shared_world_update(self, graph, world, projection_kind="market"):
        self.calls.append((graph, world, projection_kind))
        return dict(self.result)


class FakeStateStore:
    def __init__(self, payload=None):
        self.payload = dict(payload or {})

    def load(self):
        return dict(self.payload)

    def replace(self, payload):
        self.payload = dict(payload or {})


class FakeMaintenanceRepository:
    def __init__(self):
        self.calls = []

    def run_deferred_maintenance(self, payload):
        self.calls.append(dict(payload or {}))
        return {
            "status": "ok",
            "worldId": payload.get("worldId"),
            "deletedBatchCount": 3,
            "abox": {"status": "ok"},
        }


def projection_job(kind="market"):
    graph = PortfolioOntology(
        "portfolio:local:test",
        entities=[OntologyEntity(
            "stock:TEST",
            "Test",
            "stock",
            {"ontologyBox": "ABox", "tboxClass": "Stock", "symbol": "TEST"},
        )],
        worldview={"sharedWorldProjection": kind},
    )
    world = market_world("us")
    return {
        "jobId": "world-projection:test-" + kind,
        "projectionKind": kind,
        **world.to_dict(),
        "payload": serialize_portfolio_ontology(graph),
    }


class OntologyWorldProjectionRunnerTests(unittest.TestCase):
    def test_default_worker_claims_one_shared_world_per_isolated_run(self):
        runner = OntologyWorldProjectionRunner(FakeOutbox([]), FakeRecorder({"status": "ok"}))

        self.assertEqual(1, runner.batch_size())
        self.assertEqual(300, runner.fairness_cooldown_seconds())

    def test_low_disk_guard_defers_before_claiming_a_shared_world_write(self):
        outbox = FakeOutbox([projection_job()])
        recorder = FakeRecorder({"status": "ok", "saved": True})
        runner = OntologyWorldProjectionRunner(
            outbox,
            recorder,
            storage_guard=lambda: {"ready": False, "status": "blocked-low-disk"},
        )

        result = runner.run_once()

        self.assertEqual("deferred-low-disk", result["status"])
        self.assertEqual(1, len(outbox.jobs))
        self.assertEqual([], recorder.calls)

    def test_completed_shared_projection_acknowledges_the_durable_job(self):
        outbox = FakeOutbox([projection_job()])
        recorder = FakeRecorder({"status": "ok", "saved": True})
        runner = OntologyWorldProjectionRunner(outbox, recorder, settings={"ontologyWorldProjectionBatchSize": "1"})

        result = runner.run_once()

        self.assertEqual(1, result["claimedCount"])
        self.assertEqual(1, result["completedCount"])
        self.assertEqual(0, result["retryCount"])
        self.assertEqual(1, len(outbox.completed))
        graph, world, kind = recorder.calls[0]
        self.assertEqual("stock:TEST", graph.entities[0].entity_id)
        self.assertEqual("market:shared:us", world.world_id)
        self.assertEqual("market", kind)

    def test_pending_live_reasoning_yields_before_claiming_shared_world_work(self):
        outbox = FakeOutbox([projection_job()])
        recorder = FakeRecorder({"status": "ok", "saved": True})
        runner = OntologyWorldProjectionRunner(
            outbox,
            recorder,
            reasoning_queue_probe=lambda: {
                "status": "healthy",
                "effectivePendingCount": 2,
                "runningEntryCount": 1,
                "pendingSymbolCount": 2,
                "queueMode": "priority-selected",
            },
        )

        result = runner.run_once(limit=1)

        self.assertEqual("deferred-reasoning-queue", result["status"])
        self.assertEqual(0, result["claimedCount"])
        self.assertEqual(1, len(outbox.jobs))
        self.assertEqual([], recorder.calls)
        self.assertEqual(2, result["reasoningQueue"]["effectivePendingCount"])
        self.assertEqual("active-reasoning-lease", result["backgroundFairness"]["reasonCode"])

    def test_aged_shared_projection_gets_one_fairness_turn_between_reasoning_leases(self):
        outbox = FakeOutbox([projection_job()])
        outbox.summary_payload = {
            "enabled": True,
            "pendingCount": 1,
            "states": {
                "pending": {
                    "count": 1,
                    "oldestAt": (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
                },
            },
        }
        state = FakeStateStore()
        recorder = FakeRecorder({"status": "ok", "saved": True})
        runner = OntologyWorldProjectionRunner(
            outbox,
            recorder,
            settings={"ontologyWorldProjectionMaxReasoningDeferralSeconds": "30"},
            reasoning_queue_probe=lambda: {"effectivePendingCount": 2, "runningEntryCount": 0},
            fairness_state_store=state,
        )

        result = runner.run_once(limit=1)

        self.assertEqual(1, result["completedCount"])
        self.assertTrue(result["backgroundFairness"]["fairnessGranted"])
        self.assertTrue(state.payload["lastFairnessAttemptAt"])
        self.assertEqual(1, len(recorder.calls))

    def test_reset_rebuild_replays_latest_durable_job_before_live_reasoning(self):
        job = projection_job()
        outbox = FakeOutbox([job])
        outbox.rebuild_requeue_result = {
            "status": "ok",
            "requeuedCount": 1,
            "requeuedJobIds": [job["jobId"]],
            "selection": "latest-completed-per-dedupe-key",
        }
        recorder = FakeRecorder({"status": "ok", "saved": True})
        runner = OntologyWorldProjectionRunner(
            outbox,
            recorder,
            reasoning_queue_probe=lambda: {"effectivePendingCount": 3},
        )

        result = runner.rebuild_after_typedb_reset(limit=10)

        self.assertEqual("ok", result["status"])
        self.assertEqual([10], outbox.rebuild_requeue_limits)
        self.assertEqual([job["jobId"]], result["replayedJobIds"])
        self.assertEqual([], result["remainingJobIds"])
        self.assertTrue(result["reasoningQueueBypassed"])
        self.assertEqual(1, len(recorder.calls))

    def test_saved_shared_projection_defers_routine_maintenance(self):
        outbox = FakeOutbox([projection_job()])
        repository = FakeMaintenanceRepository()
        recorder = FakeRecorder({"status": "ok", "save": {"saved": True}}, repository=repository)
        runner = OntologyWorldProjectionRunner(outbox, recorder)

        result = runner.run_once(limit=1)

        self.assertEqual(1, result["completedCount"])
        self.assertEqual([], repository.calls)
        stored = outbox.completed[0][2]
        self.assertNotIn("postRebuildMaintenance", stored)

    def test_contract_rebuild_prunes_legacy_shared_manifest(self):
        outbox = FakeOutbox([projection_job()])
        repository = FakeMaintenanceRepository()
        recorder = FakeRecorder({
            "status": "ok",
            "save": {"saved": True},
            "fullRebuild": True,
        }, repository=repository)
        runner = OntologyWorldProjectionRunner(outbox, recorder)

        runner.run_once(limit=1)

        self.assertEqual([{
            "worldId": "market:shared:us",
            "keepInactiveManifests": 0,
            "maxInactiveManifests": 10,
        }], repository.calls)
        stored = outbox.completed[0][2]
        self.assertEqual("ok", stored["postRebuildMaintenance"]["status"])

    def test_deferred_shared_projection_is_retried_and_not_acknowledged(self):
        outbox = FakeOutbox([projection_job()])
        recorder = FakeRecorder({
            "status": "deferred-market-world-write-lease",
            "reason": "another worker owns the Manifest lease",
        })
        runner = OntologyWorldProjectionRunner(outbox, recorder)

        result = runner.run_once(limit=1)

        self.assertEqual(0, result["completedCount"])
        self.assertEqual(1, result["retryCount"])
        self.assertEqual([], outbox.completed)
        self.assertIn("deferred-market-world-write-lease", outbox.retries[0][2]["reason"])

    def test_unknown_projection_kind_is_retried_without_touching_the_recorder(self):
        outbox = FakeOutbox([projection_job("unsupported")])
        recorder = FakeRecorder({"status": "ok"})
        runner = OntologyWorldProjectionRunner(outbox, recorder)

        result = runner.run_once(limit=1)

        self.assertEqual(1, result["retryCount"])
        self.assertEqual([], recorder.calls)


if __name__ == "__main__":
    unittest.main()
