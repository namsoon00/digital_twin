import unittest

from digital_twin.application.historical_replay_job_service import HistoricalReplayJobService
from digital_twin.domain.historical_replay import HistoricalReplayJob


class FakeReplayStore:
    def __init__(self):
        self.jobs = {}

    def enqueue(self, job):
        self.jobs[job.job_id] = job
        return True

    def get(self, job_id):
        return self.jobs.get(job_id)

    def list(self, replay_kind="", limit=20):
        rows = list(self.jobs.values())
        if replay_kind:
            rows = [row for row in rows if row.replay_kind == replay_kind]
        return rows[:limit]

    def summary(self):
        summary = {}
        for job in self.jobs.values():
            summary[job.status] = summary.get(job.status, 0) + 1
        return summary

    def claim_pending(self, limit=1):
        selected = [job for job in self.jobs.values() if job.status == "pending"][:limit]
        for job in selected:
            job.status = "processing"
            job.attempts += 1
        return selected

    def mark_completed(self, job, result):
        job.status = "completed"
        job.result = dict(result)

    def mark_failed(self, job, error):
        job.status = "failed"
        job.last_error = str(error)


class ReplayService:
    def __init__(self, result=None):
        self.result = result or {"status": "completed"}
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.result)


class HistoricalReplayJobServiceTests(unittest.TestCase):
    def test_enqueue_returns_durable_pending_contract(self):
        store = FakeReplayStore()
        service = HistoricalReplayJobService(store, ReplayService(), ReplayService())

        payload = service.enqueue("decision", {"symbol": "NVDA", "limit": 500})

        self.assertEqual("pending", payload["status"])
        self.assertEqual("decision", payload["replayKind"])
        self.assertEqual("NVDA", store.get(payload["jobId"]).request["symbol"])

    def test_worker_executes_decision_replay_without_delivery_or_abox_writes(self):
        store = FakeReplayStore()
        decision = ReplayService({"status": "completed", "episodeCount": 12})
        service = HistoricalReplayJobService(store, decision, ReplayService())
        job = HistoricalReplayJob.create("decision", {
            "accountId": "account-1",
            "symbol": "TSLA",
            "limit": 20,
            "includeCases": True,
        })
        store.enqueue(job)

        processed = service.run_once()

        self.assertEqual(1, processed)
        self.assertEqual("completed", job.status)
        self.assertEqual(12, job.result["episodeCount"])
        self.assertTrue(job.result["executionIsolation"]["readOnly"])
        self.assertFalse(job.result["executionIsolation"]["notificationDeliveryEnabled"])
        self.assertFalse(job.result["executionIsolation"]["operationalAboxWriteEnabled"])
        self.assertEqual("TSLA", decision.calls[0]["symbol"])

    def test_worker_records_failure_without_raising_to_scheduler(self):
        class BrokenReplay:
            def run(self, **_kwargs):
                raise RuntimeError("broken archive")

        store = FakeReplayStore()
        service = HistoricalReplayJobService(store, BrokenReplay(), ReplayService())
        job = HistoricalReplayJob.create("decision", {})
        store.enqueue(job)

        self.assertEqual(1, service.run_once())
        self.assertEqual("failed", job.status)
        self.assertIn("broken archive", job.last_error)


if __name__ == "__main__":
    unittest.main()
