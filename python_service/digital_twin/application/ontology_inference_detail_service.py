"""Asynchronous durable InferenceBox detail readback.

This worker never participates in an investment judgement.  The live path has
already committed a native TypeDB generation and verified its active marker;
the worker only stores a detailed, bounded readback for diagnostics after live
reasoning work has yielded.
"""

from __future__ import annotations

from datetime import datetime, timezone
import time
import uuid
from typing import Dict, List, Mapping, Tuple

from ..domain.ontology_runtime_operations import bounded_background_work_fairness


SUCCESS_STATUSES = {"ok", "empty"}


def _integer_setting(settings: Dict[str, object], key: str, fallback: int, lower: int, upper: int) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(lower, min(upper, value))


def _symbols(values: object) -> List[str]:
    rows = values if isinstance(values, (list, tuple, set)) else [values]
    return sorted({
        str(value or "").upper().strip()
        for value in rows
        if str(value or "").strip()
    })


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class OntologyInferenceDetailRunner:
    """Consume detailed-readback jobs only while live reasoning is idle."""

    def __init__(
        self,
        outbox,
        ontology_repository,
        settings: Dict[str, object] = None,
        worker_id: str = "",
        reasoning_queue_probe=None,
        fairness_state_store=None,
    ):
        self.outbox = outbox
        self.ontology_repository = ontology_repository
        self.settings = dict(settings or {})
        self.worker_id = str(worker_id or "ontology-inference-detail-" + uuid.uuid4().hex[:12])
        self.reasoning_queue_probe = reasoning_queue_probe
        self.fairness_state_store = fairness_state_store
        self.last_run_details: List[str] = []
        self.last_background_fairness: Dict[str, object] = {}

    def batch_size(self) -> int:
        # Full snapshots can involve a sizable TypeDB traversal. One idle job
        # per isolated process keeps a newly-arrived live request responsive.
        return _integer_setting(self.settings, "ontologyInferenceDetailBatchSize", 1, 1, 10)

    def lease_seconds(self) -> int:
        return _integer_setting(self.settings, "ontologyInferenceDetailLeaseSeconds", 300, 30, 3600)

    def max_attempts(self) -> int:
        return _integer_setting(self.settings, "ontologyInferenceDetailMaxAttempts", 8, 1, 32)

    def completed_retention_hours(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyInferenceDetailCompletedRetentionHours",
            168,
            24,
            24 * 365,
        )

    def execution_timeout_seconds(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyInferenceDetailExecutionTimeoutSeconds",
            150,
            15,
            900,
        )

    def execution_timeout_grace_seconds(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyInferenceDetailExecutionTimeoutGraceSeconds",
            10,
            1,
            60,
        )

    def defer_while_reasoning_pending(self) -> bool:
        value = str(
            self.settings.get("ontologyInferenceDetailDeferWhenReasoningPending") or "1"
        ).strip().lower()
        return value not in {"0", "false", "no", "off", "disabled"}

    def background_fairness_enabled(self) -> bool:
        value = str(
            self.settings.get("ontologyBackgroundWorkFairnessEnabled") or "1"
        ).strip().lower()
        return value not in {"0", "false", "no", "off", "disabled"}

    def max_reasoning_deferral_seconds(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyInferenceDetailMaxReasoningDeferralSeconds",
            900,
            30,
            24 * 60 * 60,
        )

    def fairness_cooldown_seconds(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyBackgroundWorkFairnessCooldownSeconds",
            60,
            10,
            60 * 60,
        )

    def fairness_state(self) -> Dict[str, object]:
        store = self.fairness_state_store
        reader = getattr(store, "load", None)
        if not callable(reader):
            return {}
        try:
            value = reader()
        except Exception:  # noqa: BLE001 - diagnostics may run without their optional audit checkpoint.
            return {}
        return dict(value or {}) if isinstance(value, dict) else {}

    def save_fairness_state(self, payload: Dict[str, object]) -> None:
        store = self.fairness_state_store
        writer = getattr(store, "replace", None)
        if not callable(writer):
            writer = getattr(store, "save", None)
        if not callable(writer):
            return
        try:
            writer(dict(payload or {}))
        except Exception:
            return

    def outbox_summary(self) -> Dict[str, object]:
        try:
            value = self.outbox.summary()
        except Exception:  # noqa: BLE001 - an unavailable audit summary must preserve live priority.
            return {"status": "unavailable"}
        return dict(value or {}) if isinstance(value, dict) else {"status": "invalid"}

    @staticmethod
    def nonnegative(value: object) -> int:
        try:
            return max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def active_reasoning_count(state: Mapping[str, object]):
        values = dict(state or {}) if isinstance(state, Mapping) else {}
        mailbox = values.get("mailbox") if isinstance(values.get("mailbox"), Mapping) else {}
        counts = []
        for source in (values, mailbox):
            if "runningEntryCount" not in source:
                continue
            try:
                counts.append(max(0, int(float(source.get("runningEntryCount") or 0))))
            except (TypeError, ValueError):
                continue
        return max(counts) if counts else None

    def pending_background_work(self, summary: Mapping[str, object]) -> Dict[str, object]:
        values = dict(summary or {}) if isinstance(summary, Mapping) else {}
        states = values.get("states") if isinstance(values.get("states"), Mapping) else {}
        pending_state = states.get("pending") if isinstance(states.get("pending"), Mapping) else {}
        pending_count = self.nonnegative(values.get("pendingCount") or pending_state.get("count"))
        return {
            "pendingCount": pending_count,
            "oldestAt": str(pending_state.get("oldestAt") or values.get("oldestPendingAt") or "").strip(),
            "summaryStatus": str(values.get("status") or "ok").strip() or "ok",
        }

    def background_fairness_decision(
        self,
        reasoning_queue: Mapping[str, object] = None,
        commit_fairness: bool = False,
    ) -> Dict[str, object]:
        queue = dict(reasoning_queue or self.reasoning_queue_state())
        pending = self.reasoning_pending_count(queue)
        active = self.active_reasoning_count(queue)
        state = self.fairness_state()
        fairness_enabled = self.background_fairness_enabled()
        backlog = {"pendingCount": None, "oldestAt": "", "summaryStatus": "not-read"}
        if pending > 0 and active == 0 and fairness_enabled:
            backlog = self.pending_background_work(self.outbox_summary())
        background_pending = bool(backlog.get("pendingCount")) if backlog.get("pendingCount") is not None else pending > 0
        decision = bounded_background_work_fairness(
            reasoning_pending_count=pending,
            active_reasoning_count=active,
            background_work_pending=background_pending,
            oldest_background_work_at=backlog.get("oldestAt"),
            last_fairness_at=state.get("lastFairnessAttemptAt"),
            max_deferral_seconds=self.max_reasoning_deferral_seconds(),
            fairness_cooldown_seconds=self.fairness_cooldown_seconds(),
        )
        if not fairness_enabled and pending > 0:
            decision.update({
                "deferred": True,
                "fairnessGranted": False,
                "reasonCode": "fairness-disabled",
                "reason": "공정 실행이 비활성화되어 라이브 추론 우선 정책을 유지합니다.",
            })
        decision.update({
            "worker": "ontology-inference-detail",
            "enabled": fairness_enabled,
            "outbox": backlog,
        })
        if bool(decision.get("fairnessGranted")) and commit_fairness:
            self.save_fairness_state({
                **state,
                "lastFairnessAttemptAt": str(decision.get("checkedAt") or ""),
                "lastFairness": {
                    key: decision.get(key)
                    for key in [
                        "version", "checkedAt", "reasonCode", "backgroundWaitSeconds",
                        "maxDeferralSeconds", "fairnessCooldownSeconds", "outbox",
                    ]
                },
            })
        self.last_background_fairness = dict(decision)
        return decision

    def reasoning_queue_state(self) -> Dict[str, object]:
        if not callable(self.reasoning_queue_probe):
            return {"status": "not-configured", "effectivePendingCount": 0}
        try:
            value = self.reasoning_queue_probe()
        except Exception as error:  # noqa: BLE001 - a degraded probe must not permanently block audit detail.
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
    def reasoning_pending_count(state: Mapping[str, object]) -> int:
        for key in ["effectivePendingCount", "pendingEntryCount", "pendingCount"]:
            try:
                value = int(float((state or {}).get(key) or 0))
            except (TypeError, ValueError):
                continue
            if value >= 0:
                return value
        return 0

    def reasoning_queue_deferral(self, commit_fairness: bool = False) -> Dict[str, object]:
        reasoning_queue = self.reasoning_queue_state()
        pending = self.reasoning_pending_count(reasoning_queue)
        if not self.defer_while_reasoning_pending() or pending <= 0:
            self.last_background_fairness = {}
            return {}
        fairness = self.background_fairness_decision(reasoning_queue, commit_fairness=commit_fairness)
        if bool(fairness.get("fairnessGranted")):
            return {}
        if not bool(fairness.get("backgroundWorkPending")):
            return {
                "status": "idle-no-background-work",
                "workerId": self.worker_id,
                "claimedCount": 0,
                "completedCount": 0,
                "supersededCount": 0,
                "retryCount": 0,
                "reasoningQueue": reasoning_queue,
                "backgroundFairness": fairness,
                "reason": "라이브 추론 대기 중이지만 처리할 상세 InferenceBox 읽기 작업이 없습니다.",
            }
        return {
            "status": "deferred-reasoning-queue",
            "workerId": self.worker_id,
            "claimedCount": 0,
            "completedCount": 0,
            "supersededCount": 0,
            "retryCount": 0,
            "reasoningQueue": reasoning_queue,
            "backgroundFairness": fairness,
            "reason": (
                "라이브 TypeDB 추론 요청이 " + str(pending)
                + "건 남아 있어 상세 InferenceBox 읽기를 유예합니다."
            ),
        }

    def status(self) -> Dict[str, object]:
        reasoning_queue = self.reasoning_queue_state()
        fairness = self.background_fairness_decision(reasoning_queue)
        return {
            "workerId": self.worker_id,
            "batchSize": self.batch_size(),
            "leaseSeconds": self.lease_seconds(),
            "maxAttempts": self.max_attempts(),
            "completedRetentionHours": self.completed_retention_hours(),
            "deferWhenReasoningPending": self.defer_while_reasoning_pending(),
            "reasoningQueue": reasoning_queue,
            "backgroundFairness": fairness,
            "fairnessState": self.fairness_state(),
            "outbox": self.outbox_summary(),
        }

    def inferencebox_snapshot(self, job: Mapping[str, object]) -> Dict[str, object]:
        method = getattr(self.ontology_repository, "inferencebox_snapshot", None)
        if not callable(method):
            raise AttributeError("ontology repository has no inferencebox_snapshot")
        targets = _symbols(job.get("targetSymbols") or [])
        limit = _integer_setting(
            {"limit": job.get("detailLimit")},
            "limit",
            80,
            1,
            500,
        )
        world_id = str(job.get("worldId") or "").strip()
        try:
            value = method(symbols=targets, limit=limit, world_id=world_id)
        except TypeError as error:
            message = str(error)
            if "world_id" not in message and "unexpected keyword" not in message:
                raise
            value = method(symbols=targets, limit=limit)
        if not isinstance(value, dict):
            raise ValueError("inferencebox_snapshot returned non-dict")
        return dict(value)

    @staticmethod
    def detail_validation(snapshot: Mapping[str, object], job: Mapping[str, object]) -> Tuple[str, str]:
        """Classify one durable read without ever accepting stale detail."""
        values = dict(snapshot or {})
        expected_generation = str(job.get("inferenceGenerationId") or "").strip()
        expected_source = str(job.get("sourceAboxSnapshotId") or "").strip()
        actual_generation = str(values.get("inferenceGenerationId") or "").strip()
        actual_source = str(values.get("sourceAboxSnapshotId") or "").strip()
        status = str(values.get("status") or "").strip().lower()
        if actual_generation and expected_generation and actual_generation != expected_generation:
            return "superseded", "active InferenceBox generation changed before deferred detail readback"
        if status not in SUCCESS_STATUSES:
            return "retry", "TypeDB detailed InferenceBox read returned " + (status or "unknown")
        if not actual_generation:
            return "retry", "detailed InferenceBox read has no active generation identity"
        if actual_source != expected_source:
            return "retry", "detailed InferenceBox source ABox does not match the queued generation"
        native_completed = bool(
            values.get("nativeTypeDbReasoningCompleted")
            or values.get("typedbNativeRuleEvaluationCompleted")
            or values.get("nativeTypeDbReasoningUsed")
        )
        native_used = bool(values.get("nativeTypeDbReasoningUsed"))
        if not native_completed:
            return "retry", "detailed InferenceBox does not prove native evaluation completion"
        if status == "ok" and not native_used:
            return "retry", "matched detailed InferenceBox does not prove native materialization"
        if status == "empty" and native_used:
            return "retry", "empty detailed InferenceBox contradicts native materialization"
        if values.get("generationAligned") is False:
            return "retry", "detailed InferenceBox is marked generation-misaligned"
        expected_targets = set(_symbols(job.get("targetSymbols") or []))
        actual_targets = set(_symbols(values.get("targetSymbols") or []))
        missing_targets = sorted(expected_targets.difference(actual_targets))
        if missing_targets:
            return "retry", "detailed InferenceBox misses target symbols: " + ", ".join(missing_targets)
        return "complete", ""

    def complete(self, job_id: str, result: Mapping[str, object], terminal_status: str = "completed") -> bool:
        try:
            return bool(self.outbox.complete(
                job_id,
                self.worker_id,
                result,
                terminal_status=terminal_status,
            ))
        except TypeError:
            # Small legacy/focused test doubles may not yet expose a terminal
            # status argument. Their result still retains the superseded flag.
            return bool(self.outbox.complete(job_id, self.worker_id, result))

    def run_once(self, limit: int = 0) -> Dict[str, object]:
        started = time.monotonic()
        deferred = self.reasoning_queue_deferral(commit_fairness=True)
        if deferred:
            self.last_run_details = ["deferred-reasoning-queue"]
            return {
                **deferred,
                "prunedCompletedCount": 0,
                "durationMs": int((time.monotonic() - started) * 1000),
            }
        bounded = int(limit or self.batch_size())
        reasoning_queue = self.reasoning_queue_state()
        jobs = list(self.outbox.claim(self.worker_id, bounded, self.lease_seconds()) or [])
        completed, superseded, retried, details = [], [], [], []
        for job in jobs:
            job_id = str(job.get("jobId") or "")
            try:
                snapshot = self.inferencebox_snapshot(job)
                action, reason = self.detail_validation(snapshot, job)
                if action == "superseded":
                    receipt = {
                        "status": "superseded",
                        "reason": reason,
                        "expectedInferenceGenerationId": str(job.get("inferenceGenerationId") or ""),
                        "actualInferenceGenerationId": str(snapshot.get("inferenceGenerationId") or ""),
                        "sourceAboxSnapshotId": str(snapshot.get("sourceAboxSnapshotId") or ""),
                        "recordedAt": _stamp(),
                    }
                    self.complete(job_id, receipt, terminal_status="superseded")
                    superseded.append(job_id)
                    details.append("superseded:" + job_id[-10:])
                    continue
                if action != "complete":
                    retry = self.outbox.retry(
                        job_id,
                        self.worker_id,
                        reason,
                        max_attempts=self.max_attempts(),
                    )
                    retried.append({"jobId": job_id, **dict(retry or {})})
                    details.append("retry:" + job_id[-10:])
                    continue
                snapshot["durableReadback"] = True
                snapshot["durableDetailReadback"] = True
                snapshot["detailReadbackAt"] = _stamp()
                snapshot["detailOutboxJobId"] = job_id
                snapshot["detailProjectionRunId"] = str(job.get("projectionRunId") or "")
                if self.complete(job_id, {"status": "ok", "inferenceBox": snapshot}):
                    completed.append(job_id)
                    details.append("completed:" + job_id[-10:])
                else:
                    details.append("lease-lost:" + job_id[-10:])
            except Exception as error:  # noqa: BLE001 - one stale audit row must not stop the worker.
                retry = self.outbox.retry(
                    job_id,
                    self.worker_id,
                    str(error),
                    max_attempts=self.max_attempts(),
                )
                retried.append({"jobId": job_id, **dict(retry or {})})
                details.append("error:" + job_id[-10:])

        pruned = 0
        prune = getattr(self.outbox, "prune_completed", None)
        if callable(prune):
            try:
                pruned = int(prune(self.completed_retention_hours()) or 0)
            except Exception:  # noqa: BLE001 - retention must not block future audit reads.
                pruned = 0
        self.last_run_details = details
        result = {
            "status": "ok",
            "workerId": self.worker_id,
            "claimedCount": len(jobs),
            "completedCount": len(completed),
            "supersededCount": len(superseded),
            "retryCount": len(retried),
            "completedJobIds": completed,
            "supersededJobIds": superseded,
            "retries": retried,
            "reasoningQueue": reasoning_queue,
            "prunedCompletedCount": pruned,
            "durationMs": int((time.monotonic() - started) * 1000),
        }
        if bool(self.last_background_fairness.get("fairnessGranted")):
            result["backgroundFairness"] = dict(self.last_background_fairness)
        return result
