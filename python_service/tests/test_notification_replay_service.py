import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.notification_replay_service import NotificationReplayService
from digital_twin.domain.notifications import NotificationJob, notification_debug_number


class FakeNotificationQueue:
    def __init__(self, jobs=None):
        self.items = list(jobs or [])

    def recent(self, limit=40):
        return list(reversed(self.items))[:limit]

    def enqueue(self, job):
        self.items.append(job)
        return True

    def upsert_job(self, job):
        for index, item in enumerate(self.items):
            if item.job_id == job.job_id:
                self.items[index] = job
                return
        self.items.append(job)

    def mark_done(self, job):
        job.status = "done"
        job.last_error = ""
        self.upsert_job(job)

    def mark_failed(self, job, error):
        job.status = "failed"
        job.last_error = error
        self.upsert_job(job)


class FakeAccountRepository:
    def load_all(self):
        return [SimpleNamespace(account_id="main", message_delivery_context=lambda: {"deliveryProfile": "unit"})]


class FakeRunner:
    def __init__(self):
        self.deliveries = []

    def apply_account_delivery_context(self, job, account):
        if account:
            context = dict(job.context or {})
            context.update(account.message_delivery_context())
            job.context = context

    def render(self, job):
        context = dict(job.context or {})
        context["rendered"] = True
        job.context = context
        return "렌더링된 본문"

    def deliver(self, job, accounts, message):
        self.deliveries.append((job.job_id, message, sorted(accounts.keys())))


class NotificationReplayServiceTests(unittest.TestCase):
    def service_with(self, source):
        queue = FakeNotificationQueue([source])
        runner = FakeRunner()
        service = NotificationReplayService(
            queue=queue,
            account_repository=FakeAccountRepository(),
            runner_factory=lambda dry_run: runner,
        )
        return service, queue, runner

    def test_replay_dry_run_resolves_tracking_number_and_marks_message(self):
        source = NotificationJob.create(
            "원본 본문",
            account_id="main",
            account_label="메인",
            message_type="ontologyInferenceMissing",
            context={"relations": 0},
        )
        service, _queue, _runner = self.service_with(source)

        result = service.replay(notification_debug_number(source.job_id), dry_run=True)

        self.assertEqual(result.status, "dry-run")
        self.assertEqual(result.source_job_id, source.job_id)
        self.assertIn("[재발송] 원본 알림 " + notification_debug_number(source.job_id), result.message)
        self.assertEqual(result.message_type, "ontologyInferenceMissing")

    def test_replay_direct_delivers_and_records_replay_metadata(self):
        source = NotificationJob.create(
            "원본 본문",
            account_id="main",
            account_label="메인",
            message_type="ontologyInferenceMissing",
            context={"relations": 0},
        )
        service, queue, runner = self.service_with(source)

        result = service.replay(source.job_id, direct=True)

        self.assertEqual(result.status, "done")
        self.assertTrue(result.delivered)
        self.assertEqual(len(runner.deliveries), 1)
        replay = next(job for job in queue.items if job.job_id == result.replay_job_id)
        self.assertEqual(replay.status, "done")
        self.assertEqual(replay.attempts, 1)
        self.assertTrue(replay.context["notificationReplay"])
        self.assertTrue(replay.context["notificationTestBypassPolicy"])
        self.assertEqual(replay.context["replaySourceJobId"], source.job_id)
        self.assertEqual(replay.context["deliveryProfile"], "unit")

    def test_replay_queue_uses_new_job_without_dedupe_key(self):
        source = NotificationJob.create(
            "원본 본문",
            account_id="main",
            account_label="메인",
            message_type="investmentInsight",
            dedupe_key="original-dedupe",
            context={},
        )
        service, queue, _runner = self.service_with(source)

        result = service.replay(source.job_id)

        self.assertEqual(result.status, "queued")
        self.assertTrue(result.queued)
        replay = next(job for job in queue.items if job.job_id == result.replay_job_id)
        self.assertNotEqual(replay.job_id, source.job_id)
        self.assertEqual(replay.dedupe_key, "")
        self.assertEqual(replay.context["notificationReplayMode"], "queue")


if __name__ == "__main__":
    unittest.main()

