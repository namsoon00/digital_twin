"""Application service for isolated, asynchronous historical replay."""

import time
from typing import Dict

from digital_twin.modules.outcomes.domain.historical_replay import HistoricalReplayJob
from digital_twin.modules.outcomes.domain.price_recovery_paper import KIND, study_identity


class HistoricalReplayJobService:
    def __init__(self, store, decision_replay_service, hypothesis_replay_service, paper_service=None):
        self.store = store
        self.decision_replay_service = decision_replay_service
        self.hypothesis_replay_service = hypothesis_replay_service
        self.paper_service = paper_service

    def enqueue(self, replay_kind: str, request: Dict[str, object]) -> Dict[str, object]:
        if replay_kind == KIND:
            study = self.paper_service.prepare(request)
            job = HistoricalReplayJob.create(replay_kind, study)
            job.job_id = study_identity(study)
            return self.store.enqueue_paper_study(job).to_dict()
        job = HistoricalReplayJob.create(replay_kind, request)
        self.store.enqueue(job)
        return job.to_dict()

    def get(self, job_id: str) -> Dict[str, object]:
        job = self.store.get(job_id)
        return job.to_dict() if job else {}

    def list(self, replay_kind: str = "", limit: int = 20) -> Dict[str, object]:
        jobs = self.store.list(replay_kind=replay_kind, limit=limit)
        return {
            "status": "ok",
            "jobs": [job.to_dict() for job in jobs],
            "summary": self.store.summary(),
        }

    def run_once(self, limit: int = 1) -> int:
        processed = 0
        for job in self.store.claim_pending(limit=max(1, min(5, int(limit or 1)))):
            started = time.monotonic()
            try:
                result = self.execute(job)
                result["executionDurationMs"] = int((time.monotonic() - started) * 1000)
                result["executionIsolation"] = {
                    "notificationDeliveryEnabled": False,
                    "operationalAboxWriteEnabled": False,
                    "readOnly": True,
                }
                self.store.mark_completed(job, result)
            except Exception as error:  # noqa: BLE001 - one replay must not poison the queue.
                self.store.mark_failed(job, str(error)[:1000])
            processed += 1
        return processed

    def execute(self, job: HistoricalReplayJob) -> Dict[str, object]:
        request = dict(job.request or {})
        if job.replay_kind == KIND:
            return self.paper_service.run(request, job.created_at)
        if job.replay_kind == "decision":
            if not self.decision_replay_service:
                raise RuntimeError("decision replay executor is not configured")
            return self.decision_replay_service.run(
                account_id=str(request.get("accountId") or ""),
                symbol=str(request.get("symbol") or ""),
                limit=int(request.get("limit") or 500),
                include_cases=bool(request.get("includeCases")),
                case_limit=int(request.get("caseLimit") or 30),
                replay_mode=str(request.get("replayMode") or "strict-replay"),
            )
        if job.replay_kind == "hypothesis":
            if not self.hypothesis_replay_service:
                raise RuntimeError("hypothesis replay executor is not configured")
            return self.hypothesis_replay_service.run(
                account_id=str(request.get("accountId") or ""),
                symbol=str(request.get("symbol") or ""),
                limit=int(request.get("limit") or 500),
            )
        raise ValueError("unsupported historical replay kind: " + job.replay_kind)
