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
