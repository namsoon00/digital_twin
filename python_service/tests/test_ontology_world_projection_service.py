import unittest

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
        return {"enabled": True, "pendingCount": len(self.jobs)}

    def max_payload_bytes(self):
        return 5 * 1024 * 1024


class FakeRecorder:
    def __init__(self, result, repository=None):
        self.result = dict(result or {})
        self.calls = []
        self.repository = repository

    def project_shared_world_update(self, graph, world, projection_kind="market"):
        self.calls.append((graph, world, projection_kind))
        return dict(self.result)


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

    def test_saved_shared_projection_prunes_the_replaced_manifest(self):
        outbox = FakeOutbox([projection_job()])
        repository = FakeMaintenanceRepository()
        recorder = FakeRecorder({"status": "ok", "save": {"saved": True}}, repository=repository)
        runner = OntologyWorldProjectionRunner(outbox, recorder)

        result = runner.run_once(limit=1)

        self.assertEqual(1, result["completedCount"])
        self.assertEqual([{
            "worldId": "market:shared:us",
            "keepInactiveManifests": 0,
            "maxInactiveManifests": 10,
        }], repository.calls)
        stored = outbox.completed[0][2]
        self.assertEqual("ok", stored["postProjectionMaintenance"]["status"])
        self.assertEqual(3, stored["postProjectionMaintenance"]["deletedBatchCount"])

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
