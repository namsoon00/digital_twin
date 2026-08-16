"""Independent V2 reasoning execution without the monitoring runner."""

import inspect
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Dict, Iterable, Mapping

from ..domain.events import DomainEvent
from ..domain.independent_reasoning import (
    IndependentReasoningRequest,
    IndependentReasoningResult,
    independent_reasoning_request,
)
from ..domain.message_types import PORTFOLIO_ONTOLOGY_SIGNAL
from ..domain.ontology_projection_input import compact_monitor_state_for_ontology


VERIFIED_PROJECTION_STATUSES = {
    "ok",
    "partial",
    "unchanged-material-facts",
    "unchanged-material-facts-reasoning-retry",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _int_setting(settings: Mapping[str, object], key: str, fallback: int, minimum: int, maximum: int) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(minimum, min(maximum, value))


def projection_inference_identity(projection: object) -> Dict[str, object]:
    values = dict(projection or {}) if isinstance(projection, Mapping) else {}
    inference = values.get("inferenceBox") if isinstance(values.get("inferenceBox"), Mapping) else {}
    native_completed = bool(
        inference.get("nativeTypeDbReasoningCompleted")
        or inference.get("typedbNativeRuleEvaluationCompleted")
    )
    verified = bool(
        str(values.get("status") or "").lower() in VERIFIED_PROJECTION_STATUSES
        and native_completed
        and inference.get("generationAligned")
        and str(inference.get("sourceAboxSnapshotId") or "").strip()
    )
    return {
        "verified": verified,
        "status": str(values.get("status") or "missing"),
        "sourceAboxSnapshotId": str(inference.get("sourceAboxSnapshotId") or ""),
        "inferenceGenerationId": str(inference.get("inferenceGenerationId") or ""),
        "nativeTypeDbReasoningCompleted": native_completed,
        "generationAligned": bool(inference.get("generationAligned")),
        "relationCount": int(inference.get("relationCount") or len(inference.get("relations") or [])),
        "traceCount": int(inference.get("traceCount") or len(inference.get("traces") or [])),
    }


def alert_event_payload(event: object) -> Dict[str, object]:
    source_metadata = deepcopy(getattr(event, "metadata", {}) or {})
    metadata = {
        key: source_metadata.get(key)
        for key in [
            "decisionEpisodeId",
            "decisionKey",
            "inferenceGenerationId",
            "sourceAboxSnapshotId",
            "reasoningEngineDeploymentId",
            "reasoningEngineVersion",
            "selectedRuleId",
            "matchedRuleIds",
            "relationCount",
            "traceCount",
            "reviewLevel",
            "dataState",
            "validationState",
        ]
        if source_metadata.get(key) not in (None, "", [], {})
    }
    return {
        "accountId": str(getattr(event, "account_id", "") or ""),
        "accountLabel": str(getattr(event, "account_label", "") or ""),
        "severity": str(getattr(event, "severity", "") or ""),
        "rule": str(getattr(event, "rule", "") or ""),
        "key": str(getattr(event, "key", "") or ""),
        "title": str(getattr(event, "title", "") or ""),
        "lines": list(getattr(event, "lines", []) or []),
        "symbol": str(getattr(event, "symbol", "") or ""),
        "criteria": list(getattr(event, "criteria", []) or []),
        "metadata": metadata,
        "generatedAt": str(getattr(event, "generated_at", "") or ""),
    }


def compact_projection_result(projection: object) -> Dict[str, object]:
    values = dict(projection or {}) if isinstance(projection, Mapping) else {}
    identity = projection_inference_identity(values)
    stages = (
        values.get("runtimeStages")
        if isinstance(values.get("runtimeStages"), Mapping)
        else values.get("stages")
        if isinstance(values.get("stages"), Mapping)
        else {}
    )
    slo = values.get("slo") if isinstance(values.get("slo"), Mapping) else {}
    audit = values.get("projectionAudit") if isinstance(values.get("projectionAudit"), Mapping) else {}
    return {
        "configured": bool(values.get("configured")),
        "saved": bool(values.get("saved")),
        "status": str(values.get("status") or ""),
        "reason": str(values.get("reason") or "")[:500],
        "graphStore": str(values.get("graphStore") or ""),
        "worldId": str(values.get("worldId") or ""),
        "accountId": str(values.get("accountId") or ""),
        "entityCount": int(values.get("entityCount") or 0),
        "relationCount": int(values.get("relationCount") or 0),
        "sourceAboxSnapshotId": identity["sourceAboxSnapshotId"],
        "inferenceGenerationId": identity["inferenceGenerationId"],
        "nativeTypeDbReasoningCompleted": identity["nativeTypeDbReasoningCompleted"],
        "generationAligned": identity["generationAligned"],
        "inferenceRelationCount": identity["relationCount"],
        "inferenceTraceCount": identity["traceCount"],
        "stages": {
            str(key): int(value)
            for key, value in stages.items()
            if isinstance(value, (int, float))
        },
        "slo": {
            "state": str(slo.get("state") or ""),
            "violations": [
                {
                    "code": str(item.get("code") or ""),
                    "severity": str(item.get("severity") or ""),
                }
                for item in slo.get("violations") or []
                if isinstance(item, Mapping)
            ][:20],
        },
        "projectionAudit": {
            "status": str(audit.get("status") or ""),
            "runId": str(audit.get("runId") or ""),
            "activeAboxSnapshotId": str(audit.get("activeAboxSnapshotId") or ""),
        },
    }


class IndependentReasoningInputAssembler:
    """Build point-in-time snapshots from the durable monitor source."""

    def __init__(self, account_repository, snapshot_source, monitor_store, settings=None):
        self.account_repository = account_repository
        self.snapshot_source = snapshot_source
        self.monitor_store = monitor_store
        self.settings = dict(settings or {})

    def temporal_history_limit(self) -> int:
        return _int_setting(self.settings, "temporalWindowHistoryLimit", 96, 6, 500)

    def selected_accounts(self, request: IndependentReasoningRequest):
        requested = set(request.account_ids)
        accounts = list(self.account_repository.load() or [])
        selected = [
            account for account in accounts
            if not requested or str(getattr(account, "account_id", "") or "") in requested
        ]
        if requested or not request.symbols:
            return selected
        target_symbols = set(request.symbols)
        previous = getattr(self.monitor_store, "previous", {}) or {}
        scoped = []
        for account in selected:
            account_id = str(getattr(account, "account_id", "") or "")
            state = previous.get(account_id) if isinstance(previous, dict) else {}
            state = state if isinstance(state, dict) else {}
            symbols = {
                str(symbol or "").upper().strip()
                for collection_name in ["positions", "watchlist"]
                for symbol in (
                    (state.get(collection_name) or {}).keys()
                    if isinstance(state.get(collection_name), dict)
                    else [
                        item.get("symbol")
                        for item in state.get(collection_name) or []
                        if isinstance(item, dict)
                    ]
                )
                if str(symbol or "").strip()
            }
            symbols.update(
                str(symbol or "").upper().strip()
                for symbol in getattr(account, "watchlist_symbols", []) or []
                if str(symbol or "").strip()
            )
            if symbols.intersection(target_symbols):
                scoped.append(account)
        return scoped

    def preflight(self, request: IndependentReasoningRequest, accounts) -> Dict[str, object]:
        if request.account_ids:
            selected = {
                str(getattr(account, "account_id", "") or "")
                for account in accounts or []
            }
            missing = sorted(set(request.account_ids) - selected)
            if missing:
                return {
                    "ready": False,
                    "permanent": True,
                    "status": "rejected-source-account",
                    "reason": "The requested account is not registered.",
                    "missingAccountIds": missing,
                }
        return dict(self.snapshot_source.preflight(accounts, dict(request.context)) or {})

    def previous_state(self, account_id: str, snapshot) -> Dict[str, object]:
        metadata = snapshot.metadata if isinstance(getattr(snapshot, "metadata", None), dict) else {}
        persisted = metadata.get("previousMonitorState")
        if isinstance(persisted, dict) and persisted:
            return deepcopy(persisted)
        previous = getattr(self.monitor_store, "previous", {}) or {}
        value = previous.get(str(account_id or "")) if isinstance(previous, dict) else {}
        return deepcopy(value) if isinstance(value, dict) else {}

    def history(self, account_id: str):
        reader = getattr(self.monitor_store, "load_history", None)
        if not callable(reader):
            return []
        values = list(reader(account_id, limit=self.temporal_history_limit()) or [])
        return [
            compact_monitor_state_for_ontology(value, settings=self.settings)
            for value in values
            if isinstance(value, dict) and value
        ][-self.temporal_history_limit():]

    def assemble(self, request: IndependentReasoningRequest) -> Dict[str, object]:
        accounts = self.selected_accounts(request)
        if not accounts:
            return {
                "status": "rejected",
                "accounts": [],
                "preflight": {
                    "ready": False,
                    "permanent": True,
                    "status": "no-affected-account",
                    "reason": "No account holds or watches the requested symbol scope.",
                },
                "snapshots": [],
            }
        preflight = self.preflight(request, accounts)
        if not preflight.get("ready"):
            return {"status": "deferred", "accounts": accounts, "preflight": preflight, "snapshots": []}
        snapshots = []
        previous_by_account = {}
        for account in accounts:
            account_id = str(getattr(account, "account_id", "") or "")
            snapshot = self.snapshot_source(account, reasoning_context=dict(request.context))
            previous = self.previous_state(account_id, snapshot)
            metadata = deepcopy(snapshot.metadata or {})
            if previous and not isinstance(metadata.get("previousMonitorState"), dict):
                metadata["previousMonitorState"] = compact_monitor_state_for_ontology(
                    previous,
                    settings=self.settings,
                )
            if not isinstance(metadata.get("monitorStateHistory"), list):
                metadata["monitorStateHistory"] = self.history(account_id)
            metadata["independentReasoningRequest"] = {
                "requestId": request.request_id,
                "scopeId": request.scope_id,
                "inputFingerprint": request.input_fingerprint,
                "deploymentId": request.deployment_id,
                "sourceObservedAt": request.source_observed_at,
            }
            snapshot.metadata = metadata
            snapshots.append(snapshot)
            previous_by_account[account_id] = previous
        return {
            "status": "ready",
            "accounts": accounts,
            "preflight": preflight,
            "snapshots": snapshots,
            "previousByAccount": previous_by_account,
        }


class ScopedTypeDBInferenceExecutor:
    """Project only the requested scope and read the resulting InferenceBox."""

    def __init__(self, projection_recorder):
        self.projection_recorder = projection_recorder

    def execute(self, request: IndependentReasoningRequest, snapshots, progress_callback=None):
        results = {}
        for snapshot in snapshots or []:
            account_id = str(getattr(snapshot, "account_id", "") or "")

            def progress(stage: str, payload: Dict[str, object] = None) -> None:
                if callable(progress_callback):
                    progress_callback(stage, {"accountId": account_id, **dict(payload or {})})

            recorder = self.projection_recorder.record_snapshot
            parameters = inspect.signature(recorder).parameters
            kwargs = {
                "target_symbols": list(request.symbols),
                "reasoning_context": dict(request.context),
            }
            if "progress_callback" in parameters:
                kwargs["progress_callback"] = progress
            try:
                result = dict(recorder(snapshot, **kwargs) or {})
            except Exception as error:  # noqa: BLE001 - the worker applies bounded retry policy.
                result = {
                    "saved": False,
                    "status": "error",
                    "reason": str(error)[:240],
                    "retryable": True,
                    "recommendedRetryAfterSeconds": 15,
                }
                snapshot.metadata.setdefault("ontology", {})["projection"] = result
            results[account_id] = result
        return results


class GraphDecisionCandidateBuilder:
    """Translate verified InferenceBox output into graph-backed alert candidates."""

    def __init__(self, monitor, monitor_store):
        self.monitor = monitor
        self.monitor_store = monitor_store

    @staticmethod
    def filter_events(events, request: IndependentReasoningRequest, account_id: str):
        if not request.symbols:
            return list(events or [])
        allowed = set(request.symbols)
        portfolio_scope = bool(
            "PORTFOLIO" in set(request.context.get("subjectKinds") or [])
            and (not request.account_ids or account_id in request.account_ids)
        )
        return [
            event for event in events or []
            if str(getattr(event, "symbol", "") or "").upper().strip() in allowed
            or (
                portfolio_scope
                and not str(getattr(event, "symbol", "") or "").strip()
                and str(getattr(event, "rule", "") or "") in {PORTFOLIO_ONTOLOGY_SIGNAL, "investmentInsight"}
            )
        ]

    def build(self, request, snapshots, previous_by_account, projection_results, force=False):
        detected = []
        ready = []
        for snapshot in snapshots or []:
            account_id = str(getattr(snapshot, "account_id", "") or "")
            identity = projection_inference_identity(projection_results.get(account_id) or {})
            if not identity["verified"]:
                continue
            events = self.monitor.events_for_snapshot(
                snapshot,
                previous_by_account.get(account_id) or {},
                reasoning_context=dict(request.context),
            )
            events = self.filter_events(events, request, account_id)
            detected.extend(events)
            ready.extend(self.monitor.apply_cadence(events, self.monitor_store, force=force))
        return {"detected": detected, "ready": ready}


class V2ReasoningEngine:
    """Concrete independent engine implementation for the V2 deployment."""

    def __init__(
        self,
        descriptor,
        input_assembler: IndependentReasoningInputAssembler,
        inference_executor: ScopedTypeDBInferenceExecutor,
        candidate_builder: GraphDecisionCandidateBuilder,
        cycle_recorder=None,
        delivery_authorized_provider=None,
        settings=None,
        release_identity=None,
    ):
        self._descriptor = descriptor
        self.input_assembler = input_assembler
        self.inference_executor = inference_executor
        self.candidate_builder = candidate_builder
        self.cycle_recorder = cycle_recorder
        self.delivery_authorized_provider = delivery_authorized_provider or (lambda: False)
        self.settings = dict(settings or {})
        self._release_identity = dict(release_identity or {})
        self.last_result = None
        self.results_by_request_id = {}

    def descriptor(self):
        return self._descriptor

    def release_manifest(self) -> Dict[str, object]:
        values = self._descriptor.to_dict()
        return {
            "engineId": values.get("engineId"),
            "engineVersion": values.get("engineVersion"),
            "deploymentId": values.get("deploymentId"),
            "graphStoreBinding": values.get("graphStoreBinding"),
            "timeSeriesBackendId": values.get("timeSeriesBackendId"),
            "releaseBundle": values.get("releaseBundle"),
            "releaseIdentity": dict(self._release_identity),
        }

    def consume(self, source_events: Iterable[Mapping[str, object]]) -> Dict[str, object]:
        request = independent_reasoning_request(
            self._descriptor.deployment_id,
            source_events,
            self.release_manifest(),
        )
        return self.execute(request).to_dict()

    def execute(self, request: IndependentReasoningRequest, force: bool = False) -> IndependentReasoningResult:
        started_at = utc_now_iso()
        started = time.perf_counter()
        stages = {}
        input_started = time.perf_counter()
        assembled = self.input_assembler.assemble(request)
        stages["inputAssemblyMs"] = int((time.perf_counter() - input_started) * 1000)
        preflight = dict(assembled.get("preflight") or {})
        if assembled.get("status") != "ready":
            result = IndependentReasoningResult(
                request_id=request.request_id,
                deployment_id=request.deployment_id,
                status="rejected" if preflight.get("permanent") else "deferred",
                started_at=started_at,
                completed_at=utc_now_iso(),
                duration_ms=int((time.perf_counter() - started) * 1000),
                account_ids=request.account_ids,
                symbols=request.symbols,
                retryable=not bool(preflight.get("permanent")),
                retry_after_seconds=int(preflight.get("retryAfterSeconds") or 15),
                reason=str(preflight.get("reason") or "Point-in-time input is not ready."),
                stage_durations_ms=stages,
            )
            return self.remember(result)

        snapshots = list(assembled.get("snapshots") or [])
        projection_started = time.perf_counter()
        projection_results = self.inference_executor.execute(request, snapshots)
        stages["projectionAndInferenceMs"] = int((time.perf_counter() - projection_started) * 1000)
        identities = {
            account_id: projection_inference_identity(value)
            for account_id, value in projection_results.items()
        }
        verified_accounts = [account_id for account_id, value in identities.items() if value["verified"]]
        failed_accounts = [account_id for account_id, value in identities.items() if not value["verified"]]
        candidate_started = time.perf_counter()
        candidates = self.candidate_builder.build(
            request,
            snapshots,
            assembled.get("previousByAccount") or {},
            projection_results,
            force=force,
        )
        stages["candidateBuildMs"] = int((time.perf_counter() - candidate_started) * 1000)
        detected_events = list(candidates.get("detected") or [])
        ready_events = list(candidates.get("ready") or [])
        delivery_authorized = bool(self.delivery_authorized_provider())
        delivery_events = []
        ai_handoff_status = "shadow-delivery-blocked"
        if delivery_authorized and self.cycle_recorder is not None:
            delivery_started = time.perf_counter()
            cycle = self.cycle_recorder.record_cycle(
                [str(getattr(snapshot, "account_id", "") or "") for snapshot in snapshots],
                snapshots,
                ready_events,
                dry_run=False,
                source_snapshot_replay=True,
            )
            delivery_events = list(getattr(cycle, "delivered_events", None) or ready_events)
            ai_handoff_status = "notification-ai-queue-enqueued" if int(getattr(cycle, "queued", 0) or 0) else "no-delivery-candidate"
            stages["deliveryHandoffMs"] = int((time.perf_counter() - delivery_started) * 1000)
        elif delivery_authorized:
            ai_handoff_status = "delivery-recorder-unavailable"

        retryable = any(bool((projection_results.get(account_id) or {}).get("retryable")) for account_id in failed_accounts)
        retry_after = max([
            int((projection_results.get(account_id) or {}).get("recommendedRetryAfterSeconds") or 0)
            for account_id in failed_accounts
        ] or [0])
        status = "ok" if verified_accounts and not failed_accounts else "partial" if verified_accounts else "deferred" if retryable else "blocked"
        if status == "ok":
            reason = "Independent V2 TypeDB inference completed."
        elif status == "partial":
            reason = "Independent V2 completed only part of the requested account scope."
        else:
            reason = str(next(
                (
                    (projection_results.get(account_id) or {}).get("reason")
                    for account_id in failed_accounts
                    if (projection_results.get(account_id) or {}).get("reason")
                ),
                "Independent V2 TypeDB inference is not ready.",
            ))
        source_ids = tuple(
            value["sourceAboxSnapshotId"] for value in identities.values()
            if value["sourceAboxSnapshotId"]
        )
        generation_ids = tuple(
            value["inferenceGenerationId"] for value in identities.values()
            if value["inferenceGenerationId"]
        )
        result = IndependentReasoningResult(
            request_id=request.request_id,
            deployment_id=request.deployment_id,
            status=status,
            started_at=started_at,
            completed_at=utc_now_iso(),
            duration_ms=int((time.perf_counter() - started) * 1000),
            account_ids=tuple(str(getattr(snapshot, "account_id", "") or "") for snapshot in snapshots),
            symbols=request.symbols,
            source_abox_snapshot_ids=source_ids,
            inference_generation_ids=generation_ids,
            projection_results={
                account_id: compact_projection_result(value)
                for account_id, value in projection_results.items()
            },
            candidate_events=tuple(alert_event_payload(event) for event in detected_events),
            delivery_events=tuple(alert_event_payload(event) for event in delivery_events),
            delivery_authorized=delivery_authorized,
            ai_handoff_status=ai_handoff_status,
            trace_complete=bool(identities) and all(
                value["verified"] and value["sourceAboxSnapshotId"] and value["inferenceGenerationId"]
                for value in identities.values()
            ),
            retryable=retryable,
            retry_after_seconds=max(1, retry_after) if retryable else 0,
            reason=reason,
            stage_durations_ms=stages,
        )
        return self.remember(result)

    def remember(self, result: IndependentReasoningResult) -> IndependentReasoningResult:
        self.last_result = result
        self.results_by_request_id[result.request_id] = result.to_dict()
        while len(self.results_by_request_id) > 100:
            self.results_by_request_id.pop(next(iter(self.results_by_request_id)))
        return result

    def health(self) -> Dict[str, object]:
        result = self.last_result.to_dict() if self.last_result else {}
        return {
            "status": "ready" if not result or result.get("status") in {"ok", "partial"} else "degraded",
            "engineVersion": "v2",
            "independentExecution": True,
            "directSourceEvents": True,
            "monitorRunnerUsed": False,
            "lastResult": result,
        }

    def explain(self, decision_id: str) -> Dict[str, object]:
        return dict(self.results_by_request_id.get(str(decision_id or "")) or {})


class IndependentReasoningJobRunner:
    """Lease direct V2 requests and execute them independently of V1."""

    def __init__(
        self,
        queue,
        engine: V2ReasoningEngine,
        registry,
        settings=None,
        worker_id: str = "",
        event_reader=None,
    ):
        import os
        import socket
        import uuid

        self.queue = queue
        self.engine = engine
        self.registry = registry
        self.settings = dict(settings or {})
        self.event_reader = event_reader
        self.worker_id = worker_id or (socket.gethostname() + ":" + str(os.getpid()) + ":v2-" + uuid.uuid4().hex[:8])

    def enabled(self) -> bool:
        return str(self.settings.get("reasoningEngineV2IndependentEnabled") or "1").strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def run_once(self) -> Dict[str, object]:
        if not self.enabled():
            return {"status": "disabled", "processedCount": 0}
        descriptor = self.engine.descriptor()
        repaired = self.repair_ingress(descriptor.deployment_id)
        jobs = self.queue.claim(
            descriptor.deployment_id,
            self.worker_id,
            limit=_int_setting(self.settings, "reasoningEngineV2BatchSize", 6, 1, 20),
            lease_seconds=_int_setting(self.settings, "reasoningEngineV2LeaseSeconds", 600, 60, 3600),
        )
        if not jobs:
            return {
                "status": "idle",
                "processedCount": 0,
                "repairedIngressCount": repaired,
                "queue": self.queue.summary(descriptor.deployment_id),
            }
        batch_key = self.batch_compatibility_key(jobs[0])
        selected_jobs = [job for job in jobs if self.batch_compatibility_key(job) == batch_key]
        deferred_jobs = [job for job in jobs if job not in selected_jobs]
        for job in deferred_jobs:
            self.queue.defer(
                job["jobId"],
                "A different point-in-time boundary owns the current V2 batch.",
                1,
            )
        job_ids = [job["jobId"] for job in selected_jobs]
        try:
            events = [
                DomainEvent.from_dict(dict(job.get("sourceEvent") or {}))
                for job in selected_jobs
            ]
            result = self.engine.consume(events)
            result["batch_job_count"] = len(selected_jobs)
            result["batch_job_ids"] = job_ids
            status = str(result.get("status") or "")
            if status == "deferred" and result.get("retryable"):
                for job_id in job_ids:
                    self.queue.defer(
                        job_id,
                        str(result.get("reason") or "V2 input is not ready."),
                        int(result.get("retry_after_seconds") or result.get("retryAfterSeconds") or 15),
                    )
                outcome = "deferred"
            else:
                for job_id in job_ids:
                    self.queue.complete(job_id, result)
                outcome = "completed"
            health = dict((self.registry.get(descriptor.deployment_id) or {}).get("health") or {})
            health.update(self.engine.health())
            health.update({
                "lastJobId": job_ids[-1],
                "lastJobIds": job_ids,
                "lastRunAt": utc_now_iso(),
                "queue": self.queue.summary(descriptor.deployment_id),
            })
            self.registry.update_health(descriptor.deployment_id, health)
            return {
                "status": outcome,
                "processedCount": len(selected_jobs),
                "repairedIngressCount": repaired,
                "jobId": job_ids[-1],
                "jobIds": job_ids,
                "result": result,
                "queue": health["queue"],
            }
        except Exception as error:  # noqa: BLE001 - durable retry owns recovery.
            retries = [
                self.queue.retry(
                    job_id,
                    str(error),
                    max_attempts=_int_setting(self.settings, "reasoningEngineV2MaxAttempts", 3, 1, 8),
                )
                for job_id in job_ids
            ]
            terminal = bool(retries) and all(retry.get("terminal") for retry in retries)
            health = dict((self.registry.get(descriptor.deployment_id) or {}).get("health") or {})
            health.update({
                "status": "blocked" if terminal else "degraded",
                "independentExecution": True,
                "directSourceEvents": True,
                "monitorRunnerUsed": False,
                "lastJobId": job_ids[-1],
                "lastJobIds": job_ids,
                "lastError": str(error)[:300],
                "lastRunAt": utc_now_iso(),
                "queue": self.queue.summary(descriptor.deployment_id),
            })
            self.registry.update_health(descriptor.deployment_id, health)
            return {
                "status": "failed" if terminal else "retry",
                "processedCount": len(selected_jobs),
                "repairedIngressCount": repaired,
                "jobId": job_ids[-1],
                "jobIds": job_ids,
                "reason": str(error)[:300],
                "retries": retries,
            }

    @staticmethod
    def batch_compatibility_key(job: Mapping[str, object]):
        event = dict(job.get("sourceEvent") or {})
        payload = dict(event.get("payload") or {})
        raw_account_ids = payload.get("accountIds") or []
        if isinstance(raw_account_ids, str):
            raw_account_ids = [raw_account_ids]
        account_ids = tuple(sorted({
            str(value or "").strip()
            for value in [*raw_account_ids, payload.get("accountId")]
            if str(value or "").strip()
        }))
        boundary = payload.get("verifiedSourceSnapshot")
        boundary = dict(boundary or {}) if isinstance(boundary, Mapping) else {}
        return (
            account_ids,
            str(boundary.get("accountId") or ""),
            str(boundary.get("generatedAt") or ""),
        )

    def repair_ingress(self, deployment_id: str) -> int:
        reader = getattr(self.event_reader, "unmaterialized_reasoning_engine_events", None)
        ingress = getattr(self.queue, "ingress_event", None)
        if not callable(reader) or not callable(ingress):
            return 0
        hours = _int_setting(
            self.settings,
            "reasoningEngineV2IngressRepairLookbackHours",
            6,
            1,
            168,
        )
        limit = _int_setting(
            self.settings,
            "reasoningEngineV2IngressRepairBatchSize",
            20,
            1,
            200,
        )
        cutoff = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - (hours * 60 * 60),
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
        events = reader(deployment_id, after_occurred_at=cutoff, limit=limit)
        return sum(1 for event in events or [] if ingress(event).get("saved"))

    def watch(self) -> None:
        interval = _int_setting(self.settings, "reasoningEngineV2IntervalSeconds", 5, 1, 300)
        while True:
            result = self.run_once()
            if result.get("status") == "idle":
                time.sleep(interval)
            elif result.get("status") in {"disabled", "deferred"}:
                time.sleep(interval)
