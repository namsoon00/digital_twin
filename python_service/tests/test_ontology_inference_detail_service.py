import unittest

from digital_twin.application.ontology_inference_detail_service import OntologyInferenceDetailRunner
from digital_twin.infrastructure.mysql_ontology_inference_detail_outbox import inference_detail_dedupe_key


def inference_job(generation="inference-generation:test", source_abox="abox-manifest:test"):
    return {
        "jobId": "inference-detail:test",
        "worldId": "portfolio:local:main",
        "accountId": "main",
        "inferenceGenerationId": generation,
        "sourceAboxSnapshotId": source_abox,
        "targetSymbols": ["AAPL"],
        "projectionRunId": "ontology-projection:test",
        "detailLimit": 80,
    }


def complete_snapshot(generation="inference-generation:test", source_abox="abox-manifest:test"):
    return {
        "status": "ok",
        "inferenceGenerationId": generation,
        "sourceAboxSnapshotId": source_abox,
        "targetSymbols": ["AAPL"],
        "relationCount": 1,
        "relations": [{"id": "native-relation:1"}],
        "traces": [{"id": "native-trace:1"}],
        "nativeTypeDbReasoningUsed": True,
        "nativeTypeDbReasoningCompleted": True,
        "generationAligned": True,
    }


class FakeOutbox:
    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.completed = []
        self.retries = []
        self.pruned = 0

    def claim(self, _worker_id, limit, _lease_seconds):
        claimed = self.jobs[:limit]
        self.jobs = self.jobs[limit:]
        return claimed

    def complete(self, job_id, worker_id, result, terminal_status="completed"):
        self.completed.append((job_id, worker_id, dict(result or {}), terminal_status))
        return True

    def retry(self, job_id, worker_id, reason, max_attempts):
        value = {
            "status": "pending",
            "jobId": job_id,
            "reason": str(reason),
            "maxAttempts": max_attempts,
        }
        self.retries.append((job_id, worker_id, value))
        return value

    def prune_completed(self, _hours):
        return self.pruned

    def summary(self):
        return {"enabled": True, "pendingCount": len(self.jobs)}


class FakeRepository:
    def __init__(self, snapshot):
        self.snapshot = dict(snapshot or {})
        self.calls = []

    def inferencebox_snapshot(self, symbols=None, limit=80, world_id=""):
        self.calls.append({"symbols": list(symbols or []), "limit": limit, "worldId": world_id})
        return dict(self.snapshot)


class OntologyInferenceDetailRunnerTests(unittest.TestCase):
    def test_detail_dedupe_key_preserves_independent_target_scopes(self):
        aapl = inference_detail_dedupe_key("portfolio:local:main", ["AAPL"])

        self.assertEqual(aapl, inference_detail_dedupe_key("portfolio:local:main", ["aapl"]))
        self.assertNotEqual(aapl, inference_detail_dedupe_key("portfolio:local:main", ["MSFT"]))
        self.assertNotEqual(aapl, inference_detail_dedupe_key("portfolio:local:main", ["AAPL", "MSFT"]))

    def test_pending_live_reasoning_defers_before_claiming_detail_readback(self):
        outbox = FakeOutbox([inference_job()])
        repository = FakeRepository(complete_snapshot())
        runner = OntologyInferenceDetailRunner(
            outbox,
            repository,
            reasoning_queue_probe=lambda: {"effectivePendingCount": 2},
        )

        result = runner.run_once()

        self.assertEqual("deferred-reasoning-queue", result["status"])
        self.assertEqual(0, result["claimedCount"])
        self.assertEqual(1, len(outbox.jobs))
        self.assertEqual([], repository.calls)

    def test_matching_durable_detail_is_completed_after_idle_readback(self):
        outbox = FakeOutbox([inference_job()])
        repository = FakeRepository(complete_snapshot())
        runner = OntologyInferenceDetailRunner(outbox, repository)

        result = runner.run_once()

        self.assertEqual(1, result["claimedCount"])
        self.assertEqual(1, result["completedCount"])
        self.assertEqual(0, result["retryCount"])
        self.assertEqual(1, len(outbox.completed))
        stored = outbox.completed[0][2]["inferenceBox"]
        self.assertTrue(stored["durableReadback"])
        self.assertTrue(stored["durableDetailReadback"])
        self.assertEqual("portfolio:local:main", repository.calls[0]["worldId"])

    def test_newer_active_generation_supersedes_stale_detail_job(self):
        outbox = FakeOutbox([inference_job()])
        repository = FakeRepository(complete_snapshot(generation="inference-generation:new"))
        runner = OntologyInferenceDetailRunner(outbox, repository)

        result = runner.run_once()

        self.assertEqual(1, result["supersededCount"])
        self.assertEqual(0, result["completedCount"])
        self.assertEqual([], outbox.retries)
        self.assertEqual("superseded", outbox.completed[0][3])
        self.assertEqual("inference-generation:new", outbox.completed[0][2]["actualInferenceGenerationId"])

    def test_unaligned_detail_is_retried_without_acknowledgement(self):
        snapshot = complete_snapshot()
        snapshot["generationAligned"] = False
        outbox = FakeOutbox([inference_job()])
        repository = FakeRepository(snapshot)
        runner = OntologyInferenceDetailRunner(outbox, repository)

        result = runner.run_once()

        self.assertEqual(1, result["retryCount"])
        self.assertEqual([], outbox.completed)
        self.assertIn("generation-misaligned", outbox.retries[0][2]["reason"])


if __name__ == "__main__":
    unittest.main()
