import time
from typing import Callable, Dict

from ..domain.model_review import ModelReviewJob, normalize_model_review_result, should_deliver_model_review


class ModelReviewRunner:
    def __init__(self, queue, reviewer, account_repository, notifier_factory: Callable, dry_run: bool = False, settings: Dict[str, object] = None):
        self.queue = queue
        self.reviewer = reviewer
        self.account_repository = account_repository
        self.notifier_factory = notifier_factory
        self.dry_run = dry_run
        self.settings = settings or {}

    def account_map(self) -> Dict[str, object]:
        return {account.account_id: account for account in self.account_repository.load_all()}

    def run_once(self, limit: int = 1) -> int:
        processed = 0
        accounts = self.account_map()
        jobs = self.queue.claim_pending(limit) if hasattr(self.queue, "claim_pending") else self.queue.pending(limit)
        for job in jobs:
            if not hasattr(self.queue, "claim_pending"):
                self.queue.mark_processing(job)
            try:
                result = normalize_model_review_result(job, self.reviewer.review(job))
                if should_deliver_model_review(job, result, self.settings):
                    self.deliver(job, result, accounts)
                self.queue.mark_done(job, result)
                processed += 1
            except Exception as error:  # noqa: BLE001 - one model review failure must not block remaining jobs.
                self.queue.mark_failed(job, str(error))
        return processed

    def deliver(self, job: ModelReviewJob, result: str, accounts: Dict[str, object]) -> None:
        message = result.strip()
        if not message:
            raise RuntimeError("empty model review result")
        if self.dry_run:
            print(message)
            return
        notifier = self.notifier_factory(accounts.get(job.account_id))
        delivery = notifier.send(message)
        if not delivery.delivered:
            raise RuntimeError(delivery.reason or "model review delivery failed")


class ModelReviewScheduler:
    def __init__(self, runner: ModelReviewRunner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(60, int(interval_seconds or 300))
        self.running = True

    def stop(self) -> None:
        self.running = False

    def run_forever(self, limit: int = 1) -> None:
        print("Python model review worker started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                self.runner.run_once(limit=limit)
            except Exception as error:  # noqa: BLE001 - worker must continue after a cycle failure.
                print("Python model review worker error: " + str(error))
            elapsed = time.monotonic() - started
            sleep_seconds = max(1.0, self.interval_seconds - elapsed)
            end_at = time.monotonic() + sleep_seconds
            while self.running and time.monotonic() < end_at:
                time.sleep(min(1.0, end_at - time.monotonic()))
