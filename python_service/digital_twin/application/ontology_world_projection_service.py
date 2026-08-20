"""Durable projection of verified PortfolioWorld facts into shared worlds."""

from __future__ import annotations

import time
import uuid
from typing import Dict

from ..domain.ontology_projection_payload import deserialize_portfolio_ontology
from ..domain.ontology_runtime_operations import bounded_background_work_fairness
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
        storage_guard=None,
        fairness_state_store=None,
    ):
        self.outbox = outbox
        self.projection_recorder = projection_recorder
        self.settings = dict(settings or {})
        self.worker_id = str(worker_id or "ontology-world-" + uuid.uuid4().hex[:12])
        self.reasoning_queue_probe = reasoning_queue_probe
        self.storage_guard = storage_guard
        self.fairness_state_store = fairness_state_store
        self.last_run_details = []
        self.last_background_fairness = {}

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

    def background_fairness_enabled(self) -> bool:
        value = str(
            self.settings.get("ontologyBackgroundWorkFairnessEnabled") or "1"
        ).strip().lower()
        return value not in {"0", "false", "no", "off", "disabled"}

    def max_reasoning_deferral_seconds(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyWorldProjectionMaxReasoningDeferralSeconds",
            600,
            30,
            24 * 60 * 60,
        )

    def fairness_cooldown_seconds(self) -> int:
        return _integer_setting(
            self.settings,
            "ontologyBackgroundWorkFairnessCooldownSeconds",
            300,
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
        except Exception:  # noqa: BLE001 - a missing audit state cannot block shared-world recovery.
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
    def active_reasoning_count(state: Dict[str, object]):
        values = dict(state or {}) if isinstance(state, dict) else {}
        mailbox = values.get("mailbox") if isinstance(values.get("mailbox"), dict) else {}
        counts = []
        for source in (values, mailbox):
            if "runningEntryCount" not in source:
                continue
            try:
                counts.append(max(0, int(float(source.get("runningEntryCount") or 0))))
            except (TypeError, ValueError):
                continue
        return max(counts) if counts else None

    def pending_background_work(self, summary: Dict[str, object]) -> Dict[str, object]:
        values = dict(summary or {}) if isinstance(summary, dict) else {}
        states = values.get("states") if isinstance(values.get("states"), dict) else {}
        pending_state = states.get("pending") if isinstance(states.get("pending"), dict) else {}
        pending_count = self.nonnegative(values.get("pendingCount") or pending_state.get("count"))
        return {
            "pendingCount": pending_count,
            "oldestAt": str(pending_state.get("oldestAt") or values.get("oldestPendingAt") or "").strip(),
            "summaryStatus": str(values.get("status") or "ok").strip() or "ok",
        }

    def background_fairness_decision(
        self,
        reasoning_queue: Dict[str, object] = None,
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
            "worker": "ontology-world-projection",
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

    def reasoning_queue_deferral(self, commit_fairness: bool = False) -> Dict[str, object]:
        """Return a no-write preflight result while live reasoning is pending."""
        reasoning_queue = self.reasoning_queue_state()
        reasoning_pending = self.reasoning_pending_count(reasoning_queue)
        if not self.defer_while_reasoning_pending() or reasoning_pending <= 0:
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
                "retryCount": 0,
                "reasoningQueue": reasoning_queue,
                "backgroundFairness": fairness,
                "reason": "라이브 추론 대기 중이지만 처리할 공유 월드 투영 작업이 없습니다.",
            }
        return {
            "status": "deferred-reasoning-queue",
            "workerId": self.worker_id,
            "claimedCount": 0,
            "completedCount": 0,
            "retryCount": 0,
            "reasoningQueue": reasoning_queue,
            "backgroundFairness": fairness,
            "reason": (
                "투자 판단을 위한 라이브 TypeDB 추론 요청이 "
                + str(reasoning_pending)
                + "건 남아 있어 공유 MarketWorld/KnowledgeWorld 투영을 양보합니다."
            ),
        }

    def status(self) -> Dict[str, object]:
        reasoning_queue = self.reasoning_queue_state()
        fairness = self.background_fairness_decision(reasoning_queue)
        summary = self.outbox_summary()
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

    @staticmethod
    def deferred_result(result: Dict[str, object]) -> bool:
        status = str((result or {}).get("status") or "").strip().lower()
        return status.startswith("deferred-") or status == "staged-scoped-manifest"

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

    def rebuild_after_typedb_reset(self, limit: int = 0) -> Dict[str, object]:
        """Restore shared worlds before live reasoning resumes after a data reset.

        Live reasoning normally takes precedence over shared-world materialized
        views.  That ordering becomes a deadlock after TypeDB is rebuilt: the
        reasoning queue needs the shared worlds, while the lower-priority
        worker waits for that queue to drain.  This explicit recovery path
        replays only the newest completed packet for each source boundary and
        bypasses that one scheduling preference until the worlds exist again.
        """
        requeue = getattr(self.outbox, "requeue_latest_replayable", None)
        if not callable(requeue):
            requeue = getattr(self.outbox, "requeue_latest_completed", None)
        if not callable(requeue):
            return {
                "status": "unsupported",
                "reason": "The shared-world outbox cannot replay its latest valid projections.",
            }
        bounded = max(1, min(5000, int(limit or 100)))
        try:
            replay = dict(requeue(limit=bounded) or {})
        except Exception as error:  # noqa: BLE001 - expose the durable replay failure to startup.
            return {"status": "error", "reason": str(error)[:180]}
        requeued_ids = {
            str(job_id or "").strip()
            for job_id in replay.get("requeuedJobIds") or []
            if str(job_id or "").strip()
        }
        if not requeued_ids:
            return {
                "status": "empty",
                "replay": replay,
                "reason": "No valid shared-world projection packet is available to replay.",
            }

        completed_ids = set()
        retry_count = 0
        runs = []
        # A reset is performed before dependent workers start, so these jobs
        # are normally the only pending work. The loop remains bounded in
        # case a manual operator recovery races with a normal outbox update.
        for _index in range(len(requeued_ids) + 4):
            if requeued_ids.issubset(completed_ids):
                break
            run = self.run_once(limit=1, bypass_reasoning_queue=True)
            runs.append(run)
            completed_ids.update(
                str(job_id or "").strip()
                for job_id in run.get("completedJobIds") or []
                if str(job_id or "").strip()
            )
            retry_count += int(run.get("retryCount") or 0)
            if int(run.get("claimedCount") or 0) <= 0 or int(run.get("retryCount") or 0) > 0:
                break
        restored_ids = sorted(requeued_ids.intersection(completed_ids))
        missing_ids = sorted(requeued_ids.difference(completed_ids))
        return {
            "status": "ok" if not missing_ids and retry_count == 0 else "retry-required",
            "replay": replay,
            "replayedCount": len(restored_ids),
            "replayedJobIds": restored_ids,
            "remainingJobIds": missing_ids,
            "retryCount": retry_count,
            "runs": runs,
            "reasoningQueueBypassed": True,
        }

    def rebuild_candidate_from_completed(self, limit: int = 0) -> Dict[str, object]:
        """Populate an isolated TypeDB candidate without mutating the live outbox."""
        reader = getattr(self.outbox, "latest_replayable", None)
        if not callable(reader):
            reader = getattr(self.outbox, "latest_completed", None)
        if not callable(reader):
            return {
                "status": "unsupported",
                "reason": "The shared-world outbox has no read-only replay contract.",
            }
        bounded = max(1, min(5000, int(limit or 100)))
        try:
            jobs = list(reader(limit=bounded) or [])
        except Exception as error:  # noqa: BLE001 - the active outbox remains unchanged.
            return {"status": "error", "reason": str(error)[:180], "readOnlySourceReplay": True}
        if not jobs:
            return {
                "status": "empty",
                "replayedCount": 0,
                "readOnlySourceReplay": True,
                "sourceQueueMutated": False,
            }
        replayed = []
        failures = []
        for job in jobs:
            job_id = str(job.get("jobId") or "")
            kind = str(job.get("projectionKind") or "").strip().lower()
            try:
                if kind not in {"market", "knowledge"}:
                    raise ValueError("unsupported read-only shared-world projection kind: " + kind)
                graph = deserialize_portfolio_ontology(job.get("payload") or {})
                world = world_from_metadata(job)
                result = dict(self.projection_recorder.project_shared_world_update(
                    graph,
                    world,
                    projection_kind=kind,
                ) or {})
                if not self.successful(result):
                    failures.append({"jobId": job_id, "reason": self.retry_reason(result)})
                    continue
                replayed.append(job_id)
            except Exception as error:  # noqa: BLE001 - candidate failure cannot affect the active store.
                failures.append({"jobId": job_id, "reason": str(error)[:180]})
        return {
            "status": "ok" if not failures else "retry-required",
            "replayedCount": len(replayed),
            "replayedJobIds": replayed,
            "remainingJobIds": [item.get("jobId") for item in failures],
            "failures": failures,
            "readOnlySourceReplay": True,
            "sourceQueueMutated": False,
        }

    def run_once(self, limit: int = 0, bypass_reasoning_queue: bool = False) -> Dict[str, object]:
        started = time.monotonic()
        if callable(self.storage_guard):
            try:
                storage = dict(self.storage_guard() or {})
            except Exception as error:  # noqa: BLE001 - an unknown disk state must not start a graph write.
                storage = {"ready": False, "status": "unavailable", "reason": str(error)[:180]}
            if not bool(storage.get("ready", True)):
                self.last_run_details = ["deferred-low-disk"]
                return {
                    "status": "deferred-low-disk",
                    "processedCount": 0,
                    "completedCount": 0,
                    "retriedCount": 0,
                    "storage": storage,
                    "durationMs": int((time.monotonic() - started) * 1000),
                }
        deferred = {} if bypass_reasoning_queue else self.reasoning_queue_deferral(commit_fairness=True)
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
        post_claim_reasoning_queue = self.reasoning_queue_state() if jobs else reasoning_queue
        post_claim_pending = self.reasoning_pending_count(post_claim_reasoning_queue)
        fairness_granted = bool(self.last_background_fairness.get("fairnessGranted"))
        if (
            jobs
            and not bypass_reasoning_queue
            and self.defer_while_reasoning_pending()
            and post_claim_pending > 0
            and not fairness_granted
        ):
            yielded = []
            yield_claimed = getattr(self.outbox, "yield_claimed", None)
            for job in jobs:
                job_id = str(job.get("jobId") or "")
                if callable(yield_claimed) and yield_claimed(
                    job_id,
                    self.worker_id,
                    "live reasoning arrived after shared-world claim",
                ):
                    yielded.append(job_id)
                    continue
                # Compatibility stores without the admission handoff retain
                # the job through their normal retry contract.
                self.outbox.retry(
                    job_id,
                    self.worker_id,
                    "live reasoning arrived after shared-world claim",
                    max_attempts=self.max_attempts(),
                )
            self.last_run_details = ["deferred-reasoning-queue-after-claim"]
            return {
                "status": "deferred-reasoning-queue-after-claim",
                "workerId": self.worker_id,
                "claimedCount": len(jobs),
                "yieldedCount": len(yielded),
                "yieldedJobIds": yielded,
                "completedCount": 0,
                "retryCount": 0,
                "reasoningQueue": post_claim_reasoning_queue,
                "reason": (
                    "공유 월드 작업 점유 뒤 라이브 추론 "
                    + str(post_claim_pending)
                    + "건이 도착해 TypeDB 쓰기 전에 작업을 양보했습니다."
                ),
                "durationMs": int((time.monotonic() - started) * 1000),
            }
        completed, retried, postponed = [], [], []
        details = []
        for job in jobs:
            job_id = str(job.get("jobId") or "")
            kind = str(job.get("projectionKind") or "market").strip().lower()
            try:
                if kind not in {"market", "knowledge", "scope-repair"}:
                    raise ValueError("unsupported shared-world projection kind: " + kind)
                graph = deserialize_portfolio_ontology(job.get("payload") or {})
                world = world_from_metadata(job)
                projection_kind = kind
                if kind == "scope-repair":
                    projection_kind = str(
                        (graph.worldview or {}).get("scopeRepairSourceProjectionKind")
                        or ("knowledge" if str(world.world_type or "") == "knowledge" else "market")
                    ).strip().lower()
                result = self.projection_recorder.project_shared_world_update(
                    graph,
                    world,
                    projection_kind=projection_kind,
                )
                result = dict(result or {})
                if kind == "scope-repair":
                    result["workKind"] = "scope-repair"
                    result["scopeRepairRequestId"] = str(
                        (graph.worldview or {}).get("scopeRepairRequestId") or ""
                    )
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
                if self.deferred_result(result) and callable(getattr(self.outbox, "defer", None)):
                    deferred_result = self.outbox.defer(
                        job_id,
                        self.worker_id,
                        self.retry_reason(result),
                        retry_after_seconds=int(result.get("recommendedRetryAfterSeconds") or 10),
                    )
                    postponed.append({"jobId": job_id, **dict(deferred_result or {})})
                    details.append(kind + ":" + job_id[-10:] + "=deferred")
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
        result = {
            "status": "ok",
            "workerId": self.worker_id,
            "claimedCount": len(jobs),
            "completedCount": len(completed),
            "retryCount": len(retried),
            "deferredCount": len(postponed),
            "completedJobIds": completed,
            "retries": retried,
            "deferredJobs": postponed,
            "reasoningQueue": reasoning_queue,
            "reasoningQueueBypassed": bool(bypass_reasoning_queue),
            "prunedCompletedCount": pruned,
            "supersededOversizedPendingCount": superseded_oversized,
            "purgedOversizedSupersededCount": purged_oversized,
            "durationMs": int((time.monotonic() - started) * 1000),
        }
        if bool(self.last_background_fairness.get("fairnessGranted")):
            result["backgroundFairness"] = dict(self.last_background_fairness)
        return result
