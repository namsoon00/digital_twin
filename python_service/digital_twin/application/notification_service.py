import time
from datetime import datetime
from typing import Callable, Dict
from zoneinfo import ZoneInfo

from ..domain.notifications import NotificationJob


class NotificationQueueRunner:
    def __init__(
        self,
        queue,
        account_repository,
        notifier_factory: Callable,
        dry_run: bool = False,
        send_gap_seconds: float = 0.0,
        template_renderer: Callable = None,
        now_provider: Callable = None,
    ):
        self.queue = queue
        self.account_repository = account_repository
        self.notifier_factory = notifier_factory
        self.dry_run = dry_run
        self.send_gap_seconds = max(0.0, float(send_gap_seconds or 0))
        self.template_renderer = template_renderer
        self.now_provider = now_provider or (lambda: datetime.now(ZoneInfo("UTC")))

    def account_map(self) -> Dict[str, object]:
        return {account.account_id: account for account in self.account_repository.load_all()}

    def run_once(self, limit: int = 10) -> int:
        jobs = self.queue.pending(limit=limit)
        if not jobs:
            print("No pending notification jobs.")
            return 0
        accounts = self.account_map()
        processed = 0
        for job in jobs:
            if not job.text.strip():
                self.queue.mark_failed(job, "empty notification text")
                continue
            message = self.render(job)
            if not message:
                self.queue.mark_failed(job, "empty rendered notification text")
                continue
            if self.dry_run:
                print(message)
                processed += 1
                continue
            account = accounts.get(job.account_id)
            if account and account.quiet_hours_active(self.now_provider(), job.message_type):
                self.mark_quiet_hours_suppressed(job, account)
                processed += 1
                continue
            self.queue.mark_processing(job)
            try:
                self.deliver(job, accounts, message)
                self.queue.mark_done(job)
                processed += 1
            except Exception as error:  # noqa: BLE001 - one failed delivery must not stop the queue.
                self.queue.mark_failed(job, str(error))
            if self.send_gap_seconds and processed < len(jobs):
                time.sleep(self.send_gap_seconds)
        return processed

    def render(self, job: NotificationJob) -> str:
        if self.template_renderer:
            return str(self.template_renderer(job) or "").strip()
        return job.text.strip()

    def deliver(self, job: NotificationJob, accounts: Dict[str, object], message: str) -> None:
        notifier = self.notifier_factory(accounts.get(job.account_id))
        delivery = notifier.send(message)
        if not delivery.delivered:
            raise RuntimeError(delivery.reason or "notification delivery failed")

    def mark_quiet_hours_suppressed(self, job: NotificationJob, account) -> None:
        reason = account.quiet_hours_reason()
        context = dict(job.context or {})
        context.update({
            "quietHoursSuppressed": True,
            "quietHoursReason": reason,
            "quietHoursStart": account.quiet_hours_start,
            "quietHoursEnd": account.quiet_hours_end,
            "quietHoursTimezone": account.quiet_hours_timezone,
        })
        job.context = context
        if hasattr(self.queue, "mark_suppressed"):
            self.queue.mark_suppressed(job, reason)
        else:
            self.queue.mark_failed(job, reason)


class NotificationQueueScheduler:
    def __init__(self, runner: NotificationQueueRunner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(5, int(interval_seconds or 30))
        self.running = True

    def stop(self) -> None:
        self.running = False

    def run_forever(self, limit: int = 10) -> None:
        print("Python notification worker started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                processed = self.runner.run_once(limit=limit)
                if processed:
                    print("Processed notification jobs: " + str(processed))
            except Exception as error:  # noqa: BLE001 - worker must continue after a cycle failure.
                print("Python notification worker error: " + str(error))
            elapsed = time.monotonic() - started
            sleep_seconds = max(1.0, self.interval_seconds - elapsed)
            end_at = time.monotonic() + sleep_seconds
            while self.running and time.monotonic() < end_at:
                time.sleep(min(1.0, end_at - time.monotonic()))
