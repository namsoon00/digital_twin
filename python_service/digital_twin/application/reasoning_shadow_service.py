"""Asynchronous V2 execution and comparison against the delivery engine."""

from __future__ import annotations

import os
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, Mapping

from ..domain.reasoning_shadow import (
    compare_engine_outcomes,
    engine_outcome_packet,
    frozen_projection_runtime_context,
    pack_projection_runtime_contexts,
    payload_hash,
)
from ..domain.reasoning_engine_versions import reasoning_release_identity


DISABLED_VALUES = {"", "0", "false", "no", "off", "disabled"}


def truthy(value: object, default: bool = False) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def integer(value: object, fallback: int, lower: int = 0, upper: int = 100000) -> int:
    try:
        parsed = int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, min(upper, parsed))


def iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parsed_utc(value: object):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def outcome_rulebox_fingerprint(outcome: Mapping[str, object]) -> str:
    fingerprints = sorted({
        str(item.get("ruleboxFingerprint") or "").strip()
        for item in dict(outcome or {}).get("projections") or []
        if isinstance(item, Mapping) and str(item.get("ruleboxFingerprint") or "").strip()
    })
    if not fingerprints:
        return ""
    return fingerprints[0] if len(fingerprints) == 1 else payload_hash(fingerprints)


class ReasoningShadowScheduler:
    """Persist one immutable V1 input/output handoff without delaying V1."""

    def __init__(self, queue, registry, settings=None):
        self.queue = queue
        self.registry = registry
        self.settings = dict(settings or {})

    def enabled(self) -> bool:
        return truthy(self.settings.get("reasoningEngineShadowEnabled"), False)

    def schedule(
        self,
        monitor_runner,
        source_events: Iterable[object],
        symbols: Iterable[str],
        reasoning_context: Mapping[str, object],
        baseline_duration_ms: int,
    ) -> Dict[str, object]:
        if not self.enabled():
            return {"status": "disabled", "saved": False}
        control = self.registry.control()
        baseline_id = str(control.active_deployment_id or "")
        delivery_id = str(control.delivery_deployment_id or "")
        candidate_id = str(control.candidate_deployment_id or "")
        if not candidate_id or candidate_id in {baseline_id, delivery_id}:
            return {"status": "no-shadow-candidate", "saved": False}
        source_states = {
            str(account_id or ""): dict(state)
            for account_id, state in dict(
                getattr(monitor_runner, "last_reasoning_source_states", {}) or {}
            ).items()
            if str(account_id or "") and isinstance(state, Mapping)
        }
        if not source_states:
            return {"status": "missing-immutable-source", "saved": False}
        recorder = getattr(monitor_runner, "ontology_projection_recorder", None)
        captured_runtime_contexts = getattr(
            monitor_runner,
            "last_projection_runtime_contexts",
            {},
        ) or {}
        recorder_runtime_contexts = getattr(
            recorder,
            "last_runtime_contexts",
            {},
        ) or {}
        runtime_contexts = {
            str(account_id or ""): frozen_projection_runtime_context(context)
            for account_id, context in dict(
                captured_runtime_contexts or recorder_runtime_contexts
            ).items()
            if str(account_id or "") and isinstance(context, Mapping)
        }
        missing_context_accounts = sorted(set(source_states) - set(runtime_contexts))
        if missing_context_accounts:
            return {
                "status": "missing-immutable-projection-context",
                "saved": False,
                "accountIds": missing_context_accounts,
            }
        runtime_context_packet = pack_projection_runtime_contexts(runtime_contexts)
        source_snapshot_ids = {
            account_id: str(state.get("generatedAt") or "")
            for account_id, state in source_states.items()
        }
        baseline_outcome = engine_outcome_packet(
            baseline_id,
            getattr(monitor_runner, "last_detected_alert_events", []) or [],
            getattr(monitor_runner, "last_ontology_projection_results", {}) or {},
            baseline_duration_ms,
            source_snapshot_ids=source_snapshot_ids,
            delivery_count=0,
        )
        baseline_projections = list(baseline_outcome.get("projections") or [])
        baseline_verifiable = bool(baseline_projections) and all(
            isinstance(item, Mapping)
            and bool(item.get("nativeTypeDbReasoningCompleted"))
            and bool(item.get("generationAligned"))
            and bool(item.get("comparisonScopeFingerprint"))
            for item in baseline_projections
        )
        if not baseline_verifiable or len(baseline_projections) != len(source_states):
            return {"status": "baseline-not-verifiable", "saved": False}
        baseline_deployment = self.registry.get(baseline_id)
        candidate_deployment = self.registry.get(candidate_id)
        baseline_health = dict(baseline_deployment.get("health") or {})
        rulebox_fingerprint = (
            outcome_rulebox_fingerprint(baseline_outcome)
            or str(baseline_health.get("ruleboxFingerprint") or "")
        )
        if not rulebox_fingerprint:
            return {"status": "missing-rulebox-release-fingerprint", "saved": False}
        baseline_release = reasoning_release_identity(
            baseline_deployment,
            rulebox_fingerprint,
        )
        candidate_release = reasoning_release_identity(
            candidate_deployment,
            rulebox_fingerprint,
        )
        projection_results = dict(
            getattr(monitor_runner, "last_ontology_projection_results", {}) or {}
        )
        projection_target_symbols_by_account = {
            str(account_id or ""): sorted({
                str(symbol or "").upper().strip()
                for symbol in dict(projection.get("graphInput") or {}).get(
                    "targetSymbols"
                ) or []
                if str(symbol or "").strip()
            })
            for account_id, projection in projection_results.items()
            if str(account_id or "") and isinstance(projection, Mapping)
        }
        if set(projection_target_symbols_by_account) != set(source_states):
            return {"status": "missing-projection-target-contract", "saved": False}
        projected_symbols = {
            str(symbol or "").upper().strip()
            for projection in dict(
                getattr(monitor_runner, "last_ontology_projection_results", {}) or {}
            ).values()
            if isinstance(projection, Mapping)
            for symbol in dict(projection.get("inferenceBox") or {}).get("targetSymbols") or []
            if str(symbol or "").strip()
        }
        clean_symbols = sorted({
            str(symbol or "").upper().strip()
            for symbol in symbols or []
            if str(symbol or "").strip()
        }.union(projected_symbols))
        source_event_ids = []
        for event in source_events or []:
            event_id = (
                event.get("eventId") or event.get("event_id") or ""
                if isinstance(event, Mapping)
                else getattr(event, "event_id", "")
            )
            if str(event_id or "").strip():
                source_event_ids.append(str(event_id).strip())
        scope_key = payload_hash({
            "accounts": sorted(source_states),
        })[:40]
        payload = {
            "contractVersion": "reasoning-shadow-job-v2",
            "queuedAt": iso_utc(),
            "baselineDeploymentId": baseline_id,
            "candidateDeploymentId": candidate_id,
            "baselineReleaseIdentity": baseline_release,
            "candidateReleaseIdentity": candidate_release,
            "baselineReleaseId": str(baseline_release.get("releaseId") or ""),
            "candidateReleaseId": str(candidate_release.get("releaseId") or ""),
            "candidateReleaseFingerprint": str(candidate_release.get("releaseFingerprint") or ""),
            "validationCohortId": str(candidate_release.get("validationCohortId") or ""),
            "candidateRuntimeRevision": str(candidate_release.get("runtimeRevision") or ""),
            "sourceEventIds": source_event_ids,
            "accountIds": sorted(source_states),
            "symbols": clean_symbols,
            "sourceSnapshotIds": source_snapshot_ids,
            "sourceStates": source_states,
            "projectionTargetSymbolsByAccount": projection_target_symbols_by_account,
            "projectionRuntimeContextPacket": runtime_context_packet,
            "projectionRuntimeContextHashes": {
                account_id: payload_hash(context)
                for account_id, context in sorted(runtime_contexts.items())
            },
            "reasoningContext": dict(reasoning_context or {}),
            "baselineOutcome": baseline_outcome,
        }
        dedupe_key = payload_hash({
            "candidate": candidate_id,
            "candidateReleaseFingerprint": candidate_release.get("releaseFingerprint"),
            "sourceSnapshots": source_snapshot_ids,
            "symbols": clean_symbols,
            "baselineOutcomeHash": baseline_outcome.get("outcomeHash"),
        })
        result = self.queue.enqueue(
            baseline_deployment_id=baseline_id,
            candidate_deployment_id=candidate_id,
            source_event_id=source_event_ids[0] if source_event_ids else "",
            scope_key=scope_key,
            dedupe_key=dedupe_key,
            payload=payload,
        )
        return {
            "status": str(result.get("status") or "queued"),
            "saved": bool(result.get("saved")),
            "jobId": str(result.get("jobId") or ""),
            "candidateDeploymentId": candidate_id,
            "candidateReleaseId": str(candidate_release.get("releaseId") or ""),
            "validationCohortId": str(candidate_release.get("validationCohortId") or ""),
            "sourceSnapshotIds": source_snapshot_ids,
            "symbolCount": len(clean_symbols),
            "projectionRuntimeContextBytes": int(
                runtime_context_packet.get("uncompressedBytes") or 0
            ),
            "projectionRuntimeContextCompressedBytes": int(
                runtime_context_packet.get("compressedBytes") or 0
            ),
        }


class ReasoningEngineShadowRunner:
    """Execute candidate TypeDB inference with a hard zero-delivery boundary."""

    def __init__(
        self,
        queue,
        comparison_store,
        registry,
        candidate_runner_factory: Callable,
        temporal_snapshot_service,
        temporal_definitions,
        settings=None,
        active_queue_probe: Callable = None,
        worker_id: str = "",
    ):
        self.queue = queue
        self.comparison_store = comparison_store
        self.registry = registry
        self.candidate_runner_factory = candidate_runner_factory
        self.temporal_snapshot_service = temporal_snapshot_service
        self.temporal_definitions = list(temporal_definitions or [])
        self.settings = dict(settings or {})
        self.active_queue_probe = active_queue_probe
        self.worker_id = str(worker_id or "").strip() or (
            socket.gethostname() + ":" + str(os.getpid()) + ":shadow-" + uuid.uuid4().hex[:8]
        )

    def enabled(self) -> bool:
        return truthy(self.settings.get("reasoningEngineShadowEnabled"), False)

    def candidate_deployment_id(self) -> str:
        control = self.registry.control()
        return str(control.candidate_deployment_id or "")

    def active_queue_guard(self) -> Dict[str, object]:
        if not truthy(self.settings.get("reasoningEngineShadowYieldToActiveQueue"), True):
            return {"ready": True, "status": "disabled"}
        if not callable(self.active_queue_probe):
            return {"ready": True, "status": "unavailable"}
        try:
            state = dict(self.active_queue_probe() or {})
        except Exception as error:  # noqa: BLE001 - fail safe for a low-priority shadow worker.
            return {"ready": False, "status": "probe-error", "reason": str(error)[:180]}
        pending = integer(state.get("effectivePendingCount"), 0)
        maximum = integer(
            self.settings.get("reasoningEngineShadowActiveQueueMaxPending"),
            0,
            0,
            100000,
        )
        health = str((state.get("queueHealth") or {}).get("status") or state.get("status") or "").lower()
        ready = pending <= maximum and health not in {"critical", "error", "blocked"}
        return {
            "ready": ready,
            "status": "ready" if ready else "active-queue-pressure",
            "effectivePendingCount": pending,
            "maximumPendingCount": maximum,
            "activeQueueStatus": health,
        }

    def temporal_comparisons(self, payload: Mapping[str, object]) -> list:
        symbols = list(payload.get("symbols") or [])
        rows = []
        if not symbols:
            return rows
        for account_id in payload.get("accountIds") or []:
            as_of = str((payload.get("sourceSnapshotIds") or {}).get(account_id) or "")
            comparison = self.temporal_snapshot_service.compare(
                "mysql-primary",
                str(self.settings.get("timeSeriesShadowBackendId") or "questdb-shadow"),
                str(account_id or ""),
                symbols,
                self.temporal_definitions,
                as_of,
            )
            rows.append({"accountId": str(account_id or ""), **dict(comparison or {})})
        return rows

    def run_once(self) -> Dict[str, object]:
        if not self.enabled():
            return {"status": "disabled", "processedCount": 0}
        candidate_id = self.candidate_deployment_id()
        if not candidate_id:
            return {"status": "no-shadow-candidate", "processedCount": 0}
        guard = self.active_queue_guard()
        if not guard.get("ready"):
            return {"status": "deferred-active-queue", "processedCount": 0, "activeQueueGuard": guard}
        current_deployment = self.registry.get(candidate_id)
        current_bundle = dict(current_deployment.get("releaseBundle") or {})
        current_release_id = str(
            current_bundle.get("release_id")
            or current_bundle.get("releaseId")
            or candidate_id
        )
        current_runtime_revision = str(
            current_bundle.get("runtime_revision")
            or current_bundle.get("runtimeRevision")
            or "unknown"
        )
        job = self.queue.claim(
            candidate_id,
            self.worker_id,
            lease_seconds=integer(
                self.settings.get("reasoningEngineShadowLeaseSeconds"),
                3600,
                60,
                7200,
            ),
            candidate_release_id=current_release_id,
            candidate_runtime_revision=current_runtime_revision,
        )
        if not job:
            return {"status": "idle", "processedCount": 0, "activeQueueGuard": guard}
        payload = dict(job.get("payload") or {})
        required_contract_fields = {
            "baselineOutcome": Mapping,
            "sourceStates": Mapping,
            "projectionTargetSymbolsByAccount": Mapping,
            "projectionRuntimeContextPacket": Mapping,
            "baselineReleaseIdentity": Mapping,
            "candidateReleaseIdentity": Mapping,
        }
        missing_contract_fields = [
            key
            for key, expected_type in required_contract_fields.items()
            if not isinstance(payload.get(key), expected_type)
        ]
        baseline_projections = list(
            dict(payload.get("baselineOutcome") or {}).get("projections") or []
        )
        baseline_verifiable = bool(baseline_projections) and all(
            isinstance(item, Mapping)
            and bool(item.get("nativeTypeDbReasoningCompleted"))
            and bool(item.get("generationAligned"))
            and bool(item.get("comparisonScopeFingerprint"))
            for item in baseline_projections
        )
        if not baseline_verifiable:
            missing_contract_fields.append("verifiableBaselineProjection")
        if missing_contract_fields:
            reason = (
                "Immutable V2 shadow input is missing required fields: "
                + ", ".join(sorted(missing_contract_fields))
            )
            discard = getattr(self.queue, "discard", None)
            if callable(discard):
                discard(str(job.get("jobId") or ""), reason)
            else:
                self.queue.retry(str(job.get("jobId") or ""), reason, max_attempts=1)
            return {
                "status": "invalid-input",
                "processedCount": 0,
                "jobId": str(job.get("jobId") or ""),
                "reason": reason,
            }
        expected_release = dict(payload.get("candidateReleaseIdentity") or {})
        current_release = reasoning_release_identity(
            current_deployment,
            expected_release.get("ruleboxFingerprint") or "",
        )
        if (
            str(current_release.get("releaseFingerprint") or "")
            != str(expected_release.get("releaseFingerprint") or "")
        ):
            reason = "Shadow job belongs to a stale candidate release and was discarded."
            self.queue.discard(str(job.get("jobId") or ""), reason)
            return {
                "status": "stale-release-input",
                "processedCount": 0,
                "jobId": str(job.get("jobId") or ""),
                "reason": reason,
            }
        deployment_before_run = self.registry.get(candidate_id)
        started = time.perf_counter()
        try:
            runner = self.candidate_runner_factory(payload)
            runner_release = dict(getattr(runner, "shadow_release_identity", {}) or {})
            if str(runner_release.get("releaseFingerprint") or "") != str(
                expected_release.get("releaseFingerprint") or ""
            ):
                raise RuntimeError("V2 runtime release does not match the immutable shadow job release")
            candidate_events = runner.run_once(
                dry_run=True,
                force=False,
                symbol_filter=payload.get("symbols") or [],
                reasoning_context=payload.get("reasoningContext") or {},
            )
            del candidate_events  # Candidate comparison uses pre-cadence graph candidates.
            duration_ms = max(0, int((time.perf_counter() - started) * 1000))
            candidate_outcome = engine_outcome_packet(
                str(payload.get("candidateDeploymentId") or candidate_id),
                getattr(runner, "last_detected_alert_events", []) or [],
                getattr(runner, "last_ontology_projection_results", {}) or {},
                duration_ms,
                source_snapshot_ids=payload.get("sourceSnapshotIds") or {},
                delivery_count=max(
                    int(getattr(runner, "shadow_delivery_count", 0) or 0),
                    int(getattr(
                        getattr(runner, "shadow_notification_sink", None),
                        "attempt_count",
                        0,
                    ) or 0),
                ),
            )
            release_fingerprint = str(runner_release.get("releaseFingerprint") or "")
            temporal = self.temporal_comparisons(payload)
            comparison = compare_engine_outcomes(
                payload.get("baselineOutcome") or {},
                candidate_outcome,
                temporal,
            ).to_dict()
            comparison["candidateReleaseFingerprint"] = release_fingerprint
            comparison["baselineReleaseId"] = str(payload.get("baselineReleaseId") or "")
            comparison["candidateReleaseId"] = str(payload.get("candidateReleaseId") or "")
            comparison["validationCohortId"] = str(payload.get("validationCohortId") or "")
            comparison["candidateRuntimeRevision"] = str(payload.get("candidateRuntimeRevision") or "")
            comparison["ruleboxFingerprint"] = str(runner_release.get("ruleboxFingerprint") or "")
            queued_at = parsed_utc(payload.get("queuedAt"))
            comparison["queueWaitMs"] = max(
                0,
                int((datetime.now(timezone.utc) - queued_at).total_seconds() * 1000),
            ) if queued_at else 0
            comparison["candidateWarmup"] = (
                str(deployment_before_run.get("status") or "") == "provisioning"
            )
            recorded = self.comparison_store.record(
                str(payload.get("baselineDeploymentId") or job.get("baselineDeploymentId") or ""),
                str(payload.get("candidateDeploymentId") or candidate_id),
                str(job.get("sourceEventId") or ""),
                comparison,
            )
            self.queue.complete(str(job.get("jobId") or ""))
            deployment = self.registry.get(candidate_id)
            comparison_status = str(comparison.get("status") or "")
            candidate_usable = comparison_status not in {
                "candidate-failed",
                "delivery-violation",
            }
            if candidate_usable and str(deployment.get("status") or "") == "provisioning":
                self.registry.transition(candidate_id, "shadow")
            self.registry.update_health(candidate_id, {
                "status": (
                    "ready"
                    if candidate_usable
                    else "blocked" if comparison_status == "delivery-violation" else "degraded"
                ),
                "lastComparisonStatus": comparison_status,
                "lastComparisonAt": recorded.get("createdAt"),
                "durationMs": duration_ms,
                "shadowDeliveryCount": comparison.get("shadowDeliveryCount"),
                "ruleboxFingerprint": str(runner_release.get("ruleboxFingerprint") or ""),
                "candidateReleaseId": str(runner_release.get("releaseId") or ""),
                "candidateReleaseFingerprint": release_fingerprint if candidate_usable else "",
                "releaseFingerprint": release_fingerprint if candidate_usable else "",
                "candidateRuntimeRevision": str(runner_release.get("runtimeRevision") or ""),
                "validationCohortId": str(runner_release.get("validationCohortId") or ""),
            })
            return {
                "status": "completed",
                "processedCount": 1,
                "jobId": str(job.get("jobId") or ""),
                "comparisonId": str(recorded.get("comparisonId") or ""),
                "comparisonStatus": str(comparison.get("status") or ""),
                "factParityPct": comparison.get("factParityPct"),
                "ruleSlotCoveragePct": comparison.get("ruleSlotCoveragePct"),
                "decisionDifferenceCount": comparison.get("decisionDifferenceCount"),
                "shadowDeliveryCount": comparison.get("shadowDeliveryCount"),
                "durationMs": duration_ms,
                "activeQueueGuard": guard,
            }
        except Exception as error:
            retry = self.queue.retry(
                str(job.get("jobId") or ""),
                str(error),
                max_attempts=integer(
                    self.settings.get("reasoningEngineShadowMaxAttempts"),
                    5,
                    1,
                    20,
                ),
            )
            self.registry.update_health(candidate_id, {
                "status": "degraded" if not retry.get("terminal") else "blocked",
                "lastError": str(error)[:240],
                "lastFailureAt": iso_utc(),
                "terminal": bool(retry.get("terminal")),
            })
            return {
                "status": "failed" if retry.get("terminal") else "retry",
                "processedCount": 0,
                "jobId": str(job.get("jobId") or ""),
                "reason": str(error)[:240],
                **retry,
            }

    def watch(self) -> None:
        interval = integer(
            self.settings.get("reasoningEngineShadowIntervalSeconds"),
            15,
            2,
            300,
        )
        while True:
            result = self.run_once()
            print(
                "Reasoning shadow "
                + str(result.get("status") or "unknown")
                + " processed=" + str(result.get("processedCount") or 0)
                + (
                    " comparison=" + str(result.get("comparisonStatus") or "")
                    if result.get("comparisonStatus")
                    else ""
                ),
                flush=True,
            )
            if int(result.get("processedCount") or 0) == 0:
                time.sleep(interval)
