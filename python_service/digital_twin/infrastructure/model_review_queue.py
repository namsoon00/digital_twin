from typing import Dict, Iterable, List

from ..domain.events import DomainEvent, MONITORING_ALERTS_DETECTED
from ..domain.model_review import ModelReviewJob
from .settings import data_dir, read_json, utc_now, write_private_json


def model_review_payloads_from_event(event: DomainEvent) -> List[Dict[str, object]]:
    if event.name != MONITORING_ALERTS_DETECTED:
        return []
    payloads: List[Dict[str, object]] = []
    for item in event.payload.get("events") or []:
        if not isinstance(item, dict):
            continue
        if item.get("rule") == "monitorDecisionChange":
            payloads.append(item)
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        for source in metadata.get("sourceAlertEvents") or []:
            if not isinstance(source, dict) or source.get("rule") != "monitorDecisionChange":
                continue
            source_payload = {
                "accountId": item.get("accountId"),
                "accountLabel": item.get("accountLabel"),
                "severity": source.get("severity") or item.get("severity"),
                "rule": source.get("rule"),
                "key": source.get("key") or item.get("key"),
                "title": source.get("title") or item.get("title"),
                "symbol": source.get("symbol") or item.get("symbol"),
                "lines": list(source.get("lines") or []),
                "criteria": list(source.get("criteria") or []),
                "metadata": dict(source.get("metadata") or {}),
                "generatedAt": item.get("generatedAt"),
            }
            source_payload["metadata"].setdefault("parentInsight", {
                "key": item.get("key"),
                "rule": item.get("rule"),
                "ontologyInsight": metadata.get("ontologyInsight"),
            })
            payloads.append(source_payload)
    return payloads


class ModelReviewJobStore:
    def __init__(self, path=None):
        self.path = path or data_dir() / "model-review-queue.json"
        self.payload = read_json(self.path, {"jobs": []})
        if not isinstance(self.payload, dict):
            self.payload = {"jobs": []}
        self.payload.setdefault("jobs", [])

    def jobs(self) -> List[ModelReviewJob]:
        return [ModelReviewJob.from_dict(item) for item in self.payload.get("jobs") or [] if isinstance(item, dict)]

    def write_jobs(self, jobs: Iterable[ModelReviewJob]) -> None:
        self.payload["jobs"] = [job.to_dict() for job in jobs]
        write_private_json(self.path, self.payload)

    def enqueue(self, job: ModelReviewJob) -> bool:
        jobs = self.jobs()
        for existing in jobs:
            if existing.job_id == job.job_id:
                return False
        jobs.append(job)
        self.write_jobs(jobs)
        return True

    def enqueue_from_event(self, event: DomainEvent) -> int:
        count = 0
        for item in model_review_payloads_from_event(event):
            if self.enqueue(ModelReviewJob.create(item)):
                count += 1
        return count

    def pending(self, limit: int = 1) -> List[ModelReviewJob]:
        selected: List[ModelReviewJob] = []
        for job in self.jobs():
            if job.status in {"pending", "failed"}:
                selected.append(job)
            if len(selected) >= int(limit or 1):
                break
        return selected

    def update(self, updated: ModelReviewJob) -> None:
        jobs = self.jobs()
        replaced = False
        for index, job in enumerate(jobs):
            if job.job_id == updated.job_id:
                jobs[index] = updated
                replaced = True
                break
        if not replaced:
            jobs.append(updated)
        self.write_jobs(jobs)

    def mark_processing(self, job: ModelReviewJob) -> ModelReviewJob:
        job.status = "processing"
        job.attempts += 1
        job.updated_at = utc_now()
        self.update(job)
        return job

    def mark_done(self, job: ModelReviewJob, result: str) -> None:
        job.status = "done"
        job.result = result
        job.last_error = ""
        job.updated_at = utc_now()
        self.update(job)

    def mark_failed(self, job: ModelReviewJob, error: str) -> None:
        job.status = "failed"
        job.last_error = error
        job.updated_at = utc_now()
        self.update(job)

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for job in self.jobs():
            counts[job.status] = counts.get(job.status, 0) + 1
        return counts


class ModelReviewEnqueuer:
    def __init__(self, store: ModelReviewJobStore = None):
        if store:
            self.store = store
        else:
            from .operational_store import model_review_job_store

            self.store = model_review_job_store()

    def handle(self, event: DomainEvent) -> None:
        self.store.enqueue_from_event(event)
