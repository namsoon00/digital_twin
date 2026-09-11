"""Carry an attempt identity to every durable job transition."""

import inspect
from typing import Mapping

from ..domain.job_claim import ReasoningJobLeaseLost


class ClaimedJobTransitions:
    METHODS = frozenset(
        {
            "complete",
            "defer",
            "retry",
            "fail",
            "exclude",
            "supersede",
            "await_world_projection",
            "await_target_scope_repair",
            "reshard_claimed_job",
        }
    )

    def __init__(self, queue, worker_id: str):
        self.queue = queue
        self.worker_id = worker_id
        self.tokens = {}
        self.settled = set()

    def remember(self, jobs) -> None:
        self.tokens = {
            str(job["jobId"]): str(job.get("claimedAt") or "") for job in jobs
        }
        self.settled.clear()

    def method(self, name):
        if name not in self.METHODS:
            raise ValueError("Unsupported reasoning job transition.")
        callback = getattr(self.queue, name, None)
        if not callable(callback):
            return None
        parameters = inspect.signature(callback).parameters

        def invoke(job_id, *args, **kwargs):
            if job_id in self.settled:
                raise ReasoningJobLeaseLost(job_id, "attempt-already-settled")
            if "worker_id" in parameters:
                kwargs["worker_id"] = self.worker_id
            if "claimed_at" in parameters:
                kwargs["claimed_at"] = self.tokens.get(job_id, "")
            result = callback(job_id, *args, **kwargs)
            if (
                name != "reshard_claimed_job"
                or str((result or {}).get("status")) != "unchanged"
            ):
                self.settled.add(job_id)
            return result

        return invoke

    def call(self, name, job_id, *args, **kwargs):
        callback = self.method(name)
        if callback is None:
            raise TypeError("Reasoning queue lacks transition: " + name)
        return callback(job_id, *args, **kwargs)

    def retry(self, job_id, error, max_attempts):
        try:
            return self.call("retry", job_id, error, max_attempts=max_attempts)
        except ReasoningJobLeaseLost as lost:
            return {
                "jobId": job_id,
                "status": "lease-lost",
                "reasonCode": lost.reason,
                "terminal": False,
            }

    def bind_release(self, job_ids, release: Mapping[str, object], lane: str):
        callback = getattr(self.queue, "bind_release", None)
        if not callable(callback):
            return
        parameters = inspect.signature(callback).parameters
        kwargs = {}
        if "worker_id" in parameters:
            kwargs["worker_id"] = self.worker_id
        if "claimed_tokens" in parameters:
            kwargs["claimed_tokens"] = dict(self.tokens)
        callback(job_ids, release, lane, **kwargs)
