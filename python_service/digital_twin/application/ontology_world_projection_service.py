"""Durable projection of verified PortfolioWorld facts into shared worlds."""

from __future__ import annotations

import time
import uuid
from typing import Dict

from ..domain.ontology_projection_payload import deserialize_portfolio_ontology
from ..domain.ontology_worlds import world_from_metadata


SUCCESS_STATUSES = {
    "ok",
    "unchanged-material-facts",
    "already-projected-material",
    "skipped-empty-market-world-patch",
    "skipped-empty-knowledge-world-patch",
}


def _integer_setting(settings: Dict[str, object], key: str, fallback: int, lower: int, upper: int) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(lower, min(upper, value))


class OntologyWorldProjectionRunner:
    """Consumes outbox jobs without putting shared-world writes on alert latency.

    The source PortfolioWorld has already passed ABox validation and native
    RuleBox inference before a job is queued.  This runner only materializes
    the account-independent MarketWorld or KnowledgeWorld view and cannot
    change the originating investment judgement.
    """

    def __init__(
        self,
        outbox,
        projection_recorder,
        settings: Dict[str, object] = None,
        worker_id: str = "",
        reasoning_queue_probe=None,
    ):
        self.outbox = outbox
        self.projection_recorder = projection_recorder
        self.settings = dict(settings or {})
        self.worker_id = str(worker_id or "ontology-world-" + uuid.uuid4().hex[:12])
        self.reasoning_queue_probe = reasoning_queue_probe
        self.last_run_details = []

    def batch_size(self) -> int:
        # TypeDB serializes writes at the database boundary. A MarketWorld
        # and KnowledgeWorld can each require a substantial scoped Manifest
        # update, so combining them in one isolated process can exceed the
        # worker budget and delay the next account inference. The outbox
        # coalesces each world independently; one job per run is therefore
        # the stable default rather than a throughput loss.
        return _integer_setting(self.settings, "ontologyWorldProjectionBatchSize", 1, 1, 50)

    def lease_seconds(self) -> int:
        return _integer_setting(self.settings, "ontologyWorldProjectionLeaseSeconds", 300, 30, 3600)

    def max_attempts(self) -> int:
        return _integer_setting(self.settings, "ontologyWorldProjectionMaxAttempts", 12, 1, 32)

    def completed_retention_hours(self) -> int:
        return _integer_setting(self.settings, "ontologyWorldProjectionCompletedRetentionHours", 168, 24, 24 * 365)

    def execution_timeout_seconds(self) -> int:
        return _integer_setting(self.settings, "ontologyWorldProjectionExecutionTimeoutSeconds", 150, 15, 900)

    def execution_timeout_grace_seconds(self) -> int:
        return _integer_setting(self.settings, "ontologyWorldProjectionExecutionTimeoutGraceSeconds", 10, 1, 60)

    def defer_while_reasoning_pending(self) -> bool:
        value = str(
            self.settings.get("ontologyWorldProjectionDeferWhenReasoningPending") or "1"
        ).strip().lower()
        return value not in {"0", "false", "no", "off", "disabled"}

    def reasoning_queue_state(self) -> Dict[str, object]:
        if not callable(self.reasoning_queue_probe):
            return {"status": "not-configured", "effectivePendingCount": 0}
        try:
            value = self.reasoning_queue_probe()
        except Exception as error:  # noqa: BLE001 - a probe fault must not stall the shared read model forever.
            return {
                "status": "error",
                "effectivePendingCount": 0,
                "reason": str(error)[:180],
            }
        return dict(value or {}) if isinstance(value, dict) else {
            "status": "invalid",
            "effectivePendingCount": 0,
        }

    @staticmethod
    def reasoning_pending_count(state: Dict[str, object]) -> int:
        for key in ["effectivePendingCount", "pendingEntryCount", "pendingCount"]:
            try:
                value = int(float((state or {}).get(key) or 0))
            except (TypeError, ValueError):
                continue
            if value >= 0:
                return value
        return 0

    def reasoning_queue_deferral(self) -> Dict[str, object]:
        """Return a no-write preflight result while live reasoning is pending."""
        reasoning_queue = self.reasoning_queue_state()
        reasoning_pending = self.reasoning_pending_count(reasoning_queue)
        if not self.defer_while_reasoning_pending() or reasoning_pending <= 0:
            return {}
        return {
            "status": "deferred-reasoning-queue",
            "workerId": self.worker_id,
            "claimedCount": 0,
            "completedCount": 0,
            "retryCount": 0,
            "reasoningQueue": reasoning_queue,
            "reason": (
                "투자 판단을 위한 라이브 TypeDB 추론 요청이 "
                + str(reasoning_pending)
                + "건 남아 있어 공유 MarketWorld/KnowledgeWorld 투영을 양보합니다."
            ),
        }

    def status(self) -> Dict[str, object]:
        summary = dict(self.outbox.summary() or {})
        return {
            "workerId": self.worker_id,
            "batchSize": self.batch_size(),
            "leaseSeconds": self.lease_seconds(),
            "maxAttempts": self.max_attempts(),
            "completedRetentionHours": self.completed_retention_hours(),
            "deferWhenReasoningPending": self.defer_while_reasoning_pending(),
            "reasoningQueue": self.reasoning_queue_state(),
            "maxPayloadBytes": int(getattr(self.outbox, "max_payload_bytes", lambda: 0)() or 0),
            "outbox": summary,
        }

    @staticmethod
    def successful(result: Dict[str, object]) -> bool:
        return str((result or {}).get("status") or "").strip().lower() in SUCCESS_STATUSES

    @staticmethod
    def retry_reason(result: Dict[str, object]) -> str:
        status = str((result or {}).get("status") or "unknown").strip()
        reason = str((result or {}).get("reason") or "").strip()
        return (status + (": " + reason if reason else ""))[:1000]

    def full_rebuild_maintenance(self, world_id: str) -> Dict[str, object]:
        """Prune legacy generations after an explicit projection-contract rebuild."""
        repository = getattr(self.projection_recorder, "repository", None)
        maintenance = getattr(repository, "run_deferred_maintenance", None)
        if not callable(maintenance):
            return {"status": "unavailable"}
        try:
            result = dict(maintenance({
                "worldId": str(world_id or ""),
                "keepInactiveManifests": 0,
                "maxInactiveManifests": 10,
            }) or {})
            return {
                "status": str(result.get("status") or "unknown"),
                "worldId": str(result.get("worldId") or world_id or ""),
                "deletedBatchCount": int(result.get("deletedBatchCount") or 0),
                "aboxStatus": str((result.get("abox") or {}).get("status") or ""),
            }
        except Exception as error:  # noqa: BLE001 - a rebuilt active world remains valid if cleanup is delayed.
            return {"status": "error", "reason": str(error)[:180]}

    def run_once(self, limit: int = 0) -> Dict[str, object]:
        started = time.monotonic()
        deferred = self.reasoning_queue_deferral()
        if deferred:
            self.last_run_details = ["deferred-reasoning-queue"]
            return {
                **deferred,
                "supersededOversizedPendingCount": 0,
                "purgedOversizedSupersededCount": 0,
                "durationMs": int((time.monotonic() - started) * 1000),
            }
        bounded = int(limit or self.batch_size())
        superseded_oversized = 0
        supersede = getattr(self.outbox, "supersede_oversized_pending", None)
        if callable(supersede):
            superseded_oversized = int(supersede() or 0)
        purged_oversized = 0
        purge = getattr(self.outbox, "purge_oversized_superseded", None)
        if callable(purge):
            purged_oversized = int(purge() or 0)
        reasoning_queue = self.reasoning_queue_state()
        jobs = list(self.outbox.claim(self.worker_id, bounded, self.lease_seconds()) or [])
        completed, retried = [], []
        details = []
        for job in jobs:
            job_id = str(job.get("jobId") or "")
            kind = str(job.get("projectionKind") or "market").strip().lower()
            try:
                if kind not in {"market", "knowledge"}:
                    raise ValueError("unsupported shared-world projection kind: " + kind)
                graph = deserialize_portfolio_ontology(job.get("payload") or {})
                world = world_from_metadata(job)
                result = self.projection_recorder.project_shared_world_update(
                    graph,
                    world,
                    projection_kind=kind,
                )
                result = dict(result or {})
                if self.successful(result):
                    if bool(result.get("fullRebuild")):
                        # Normal updates must return the shared-world writer
                        # lease promptly. Routine generation retention runs
                        # through the existing bounded deferred-maintenance
                        # cadence; only a contract migration needs immediate
                        # legacy cleanup before it can accumulate again.
                        maintenance = self.full_rebuild_maintenance(world.world_id)
                        result["postRebuildMaintenance"] = maintenance
                    self.outbox.complete(job_id, self.worker_id, result)
                    completed.append(job_id)
                    details.append(kind + ":" + job_id[-10:] + "=" + str(result.get("status") or "ok"))
                    continue
                retry = self.outbox.retry(
                    job_id,
                    self.worker_id,
                    self.retry_reason(result),
                    max_attempts=self.max_attempts(),
                )
                retried.append({"jobId": job_id, **dict(retry or {})})
                details.append(kind + ":" + job_id[-10:] + "=" + str((retry or {}).get("status") or "retry"))
            except Exception as error:  # noqa: BLE001 - one corrupt job must not stop the worker.
                retry = self.outbox.retry(
                    job_id,
                    self.worker_id,
                    str(error),
                    max_attempts=self.max_attempts(),
                )
                retried.append({"jobId": job_id, **dict(retry or {})})
                details.append(kind + ":" + job_id[-10:] + "=" + str((retry or {}).get("status") or "error"))

        pruned = 0
        prune = getattr(self.outbox, "prune_completed", None)
        if callable(prune):
            try:
                pruned = int(prune(self.completed_retention_hours()) or 0)
            except Exception:  # noqa: BLE001 - history retention must never block queued projection work.
                pruned = 0
        self.last_run_details = details
        return {
            "status": "ok",
            "workerId": self.worker_id,
            "claimedCount": len(jobs),
            "completedCount": len(completed),
            "retryCount": len(retried),
            "completedJobIds": completed,
            "retries": retried,
            "reasoningQueue": reasoning_queue,
            "prunedCompletedCount": pruned,
            "supersededOversizedPendingCount": superseded_oversized,
            "purgedOversizedSupersededCount": purged_oversized,
            "durationMs": int((time.monotonic() - started) * 1000),
        }
