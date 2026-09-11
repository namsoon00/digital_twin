"""Pair and compare completed independent reasoning-engine executions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, Mapping

from digital_twin.domain.reasoning_shadow import compare_engine_outcomes, independent_reasoning_outcome_packet
from digital_twin.domain.reasoning_comparison_identity import comparison_input_contract


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _timestamp(value: object):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _control_value(control: object, snake: str, camel: str) -> str:
    values = control if isinstance(control, Mapping) else {}
    return str(
        getattr(control, snake, "")
        or values.get(snake)
        or values.get(camel)
        or ""
    ).strip()


def _release_value(job: Mapping[str, object], *names: str) -> str:
    values = _mapping(job)
    result = _mapping(values.get("result"))
    release = _mapping(result.get("release_identity") or result.get("releaseIdentity"))
    for name in names:
        if str(release.get(name) or "").strip():
            return str(release.get(name) or "").strip()
        if str(values.get(name) or "").strip():
            return str(values.get(name) or "").strip()
    return ""


def _job_id(job: Mapping[str, object]) -> str:
    values = _mapping(job)
    return str(values.get("jobId") or values.get("job_id") or "").strip()


def _canonical_pairs(pairs: Iterable[Mapping[str, object]]):
    """Keep one comparison per durable baseline/candidate execution pair."""

    selected = {}
    for pair in pairs or []:
        values = _mapping(pair)
        baseline_job = _mapping(values.get("baseline"))
        candidate_job = _mapping(values.get("candidate"))
        baseline_job_id = _job_id(baseline_job)
        candidate_job_id = _job_id(candidate_job)
        source_event_id = str(
            values.get("sourceEventId")
            or candidate_job.get("sourceEventId")
            or ""
        ).strip()
        if not baseline_job_id or not candidate_job_id or not source_event_id:
            continue
        identity = (baseline_job_id, candidate_job_id)
        current = selected.get(identity)
        direct_source_event_id = str(candidate_job.get("sourceEventId") or "").strip()
        preference = (
            0 if source_event_id == direct_source_event_id else 1,
            source_event_id,
        )
        if current is None or preference < current[0]:
            selected[identity] = (preference, values)
    return [item[1] for item in selected.values()]


class IndependentReasoningComparisonService:
    """Create immutable active/candidate comparisons from durable V2 jobs."""

    def __init__(self, job_store, comparison_store, registry):
        self.job_store = job_store
        self.comparison_store = comparison_store
        self.registry = registry

    def reconcile(
        self,
        source_event_ids: Iterable[str] = None,
        limit: int = 20,
    ) -> Dict[str, object]:
        control = self.registry.control()
        baseline_id = (
            _control_value(control, "delivery_deployment_id", "deliveryDeploymentId")
            or _control_value(control, "active_deployment_id", "activeDeploymentId")
        )
        candidate_id = _control_value(
            control,
            "candidate_deployment_id",
            "candidateDeploymentId",
        )
        if not baseline_id or not candidate_id or baseline_id == candidate_id:
            return {
                "status": "not-applicable",
                "recordedCount": 0,
                "baselineDeploymentId": baseline_id,
                "candidateDeploymentId": candidate_id,
            }

        pair_reader = getattr(self.job_store, "completed_comparison_pairs", None)
        if not callable(pair_reader):
            return {
                "status": "not-configured",
                "recordedCount": 0,
                "baselineDeploymentId": baseline_id,
                "candidateDeploymentId": candidate_id,
            }
        pairs = _canonical_pairs(pair_reader(
            baseline_id,
            candidate_id,
            source_event_ids=source_event_ids,
            limit=max(1, min(200, int(limit or 20))),
        ) or [])
        candidate_health = _mapping(
            _mapping(self.registry.get(candidate_id)).get("health")
        )
        validation_started_at = _timestamp(candidate_health.get("validationStartedAt"))
        recorded = []
        rejected = []
        latest_candidate_release = {}
        for pair in pairs:
            baseline_job = _mapping(_mapping(pair).get("baseline"))
            candidate_job = _mapping(_mapping(pair).get("candidate"))
            source_event_id = str(
                _mapping(pair).get("sourceEventId")
                or candidate_job.get("sourceEventId")
                or ""
            ).strip()
            if not baseline_job or not candidate_job or not source_event_id:
                continue
            input_contract = comparison_input_contract(baseline_job, candidate_job)
            if not input_contract["eligible"]:
                rejected.append(input_contract)
                continue
            baseline_job["comparisonSourceEventId"] = source_event_id
            candidate_job["comparisonSourceEventId"] = source_event_id

            source_checks = {
                "sourceEventId": source_event_id,
                "sourceSnapshotId": bool(
                    str(baseline_job.get("sourceSnapshotId") or "")
                    and str(baseline_job.get("sourceSnapshotId") or "")
                    == str(candidate_job.get("sourceSnapshotId") or "")
                ),
                "sourceSnapshotAt": bool(
                    str(baseline_job.get("sourceSnapshotAt") or "")
                    and str(baseline_job.get("sourceSnapshotAt") or "")
                    == str(candidate_job.get("sourceSnapshotAt") or "")
                ),
                "sourcePayloadHash": bool(
                    str(baseline_job.get("sourcePayloadHash") or "")
                    and str(baseline_job.get("sourcePayloadHash") or "")
                    == str(candidate_job.get("sourcePayloadHash") or "")
                ),
            }
            source_input_equal = all(
                bool(value)
                for key, value in source_checks.items()
                if key != "sourceEventId"
            )
            source_input_comparison = {
                "status": "equivalent" if source_input_equal else "different",
                **source_checks,
                "baselineJobId": str(baseline_job.get("jobId") or ""),
                "candidateJobId": str(candidate_job.get("jobId") or ""),
                "baseline": {
                    "sourceSnapshotId": str(baseline_job.get("sourceSnapshotId") or ""),
                    "sourceSnapshotAt": str(baseline_job.get("sourceSnapshotAt") or ""),
                    "sourcePayloadHash": str(baseline_job.get("sourcePayloadHash") or ""),
                },
                "candidate": {
                    "sourceSnapshotId": str(candidate_job.get("sourceSnapshotId") or ""),
                    "sourceSnapshotAt": str(candidate_job.get("sourceSnapshotAt") or ""),
                    "sourcePayloadHash": str(candidate_job.get("sourcePayloadHash") or ""),
                },
            }
            source_input_comparison.update(input_contract)
            comparison = compare_engine_outcomes(
                independent_reasoning_outcome_packet(baseline_job),
                independent_reasoning_outcome_packet(candidate_job),
                [source_input_comparison],
            ).to_dict()

            candidate_started_at = _timestamp(
                candidate_job.get("claimedAt") or candidate_job.get("createdAt")
            )
            candidate_warmup = bool(
                validation_started_at is None
                or candidate_started_at is None
                or candidate_started_at < validation_started_at
            )
            candidate_source_payload = _mapping(
                _mapping(candidate_job.get("sourceEvent")).get("payload")
            )
            candidate_result = _mapping(candidate_job.get("result"))
            latest_candidate_release = _mapping(
                candidate_result.get("release_identity")
                or candidate_result.get("releaseIdentity")
            )
            replay = bool(candidate_source_payload.get("candidateValidationReplay")) or (
                "replay" in str(candidate_source_payload.get("trigger") or "").lower()
            )
            comparison.update({
                "sourceEventId": source_event_id,
                "baselineReleaseId": _release_value(
                    baseline_job,
                    "releaseId",
                    "release_id",
                ),
                "candidateReleaseId": _release_value(
                    candidate_job,
                    "releaseId",
                    "release_id",
                ),
                "candidateReleaseFingerprint": str(
                    candidate_job.get("releaseFingerprint")
                    or _release_value(
                        candidate_job,
                        "releaseFingerprint",
                        "release_fingerprint",
                    )
                    or ""
                ),
                "validationCohortId": str(
                    candidate_job.get("validationCohortId")
                    or _release_value(
                        candidate_job,
                        "validationCohortId",
                        "validation_cohort_id",
                    )
                    or ""
                ),
                "candidateRuntimeRevision": str(
                    candidate_job.get("runtimeRevision")
                    or _release_value(
                        candidate_job,
                        "runtimeRevision",
                        "runtime_revision",
                    )
                    or ""
                ),
                "queueWaitMs": int(candidate_job.get("queueWaitMs") or 0),
                "candidateExecutionMs": int(candidate_job.get("durationMs") or 0),
                "candidateStartedAt": str(candidate_job.get("claimedAt") or ""),
                "candidateWarmup": candidate_warmup,
                "sourceMode": "historical-point-in-time" if replay else "live",
                "sourceInput": source_input_comparison,
            })
            recorded.append(self.comparison_store.record(
                baseline_id,
                candidate_id,
                source_event_id,
                comparison,
            ))

        result = {
            "status": "recorded" if recorded else "idle",
            "recordedCount": len(recorded),
            "rejectedPairCount": len(rejected),
            "rejectedPairs": rejected[:20],
            "baselineDeploymentId": baseline_id,
            "candidateDeploymentId": candidate_id,
            "sourceEventIds": [
                str(item.get("sourceEventId") or "")
                for item in recorded
                if str(item.get("sourceEventId") or "")
            ],
            "comparisons": [
                {
                    "comparisonId": str(item.get("comparisonId") or ""),
                    "sourceEventId": str(item.get("sourceEventId") or ""),
                    "status": str(item.get("status") or ""),
                    "factParityPct": float(item.get("factParityPct") or 0.0),
                    "decisionDifferenceCount": int(
                        item.get("decisionDifferenceCount") or 0
                    ),
                }
                for item in recorded
            ],
        }
        if recorded:
            health_patch = {
                "independentComparisonReconciliation": {
                    **result,
                    "comparisons": result["comparisons"][-5:],
                },
                "lastComparisonAt": str(recorded[-1].get("createdAt") or ""),
                "lastComparisonStatus": str(recorded[-1].get("status") or ""),
            }
            if latest_candidate_release:
                health_patch.update({
                    "candidateReleaseId": str(
                        latest_candidate_release.get("releaseId") or ""
                    ),
                    "candidateBaseReleaseId": str(
                        latest_candidate_release.get("baseReleaseId") or ""
                    ),
                    "candidateRuntimeRevision": str(
                        latest_candidate_release.get("runtimeRevision") or ""
                    ),
                    "candidateReleaseFingerprint": str(
                        latest_candidate_release.get("releaseFingerprint") or ""
                    ),
                    "releaseFingerprint": str(
                        latest_candidate_release.get("releaseFingerprint") or ""
                    ),
                    "validationCohortId": str(
                        latest_candidate_release.get("validationCohortId") or ""
                    ),
                    "ruleboxFingerprint": str(
                        latest_candidate_release.get("ruleboxFingerprint") or ""
                    ),
                    "tboxFingerprint": str(
                        latest_candidate_release.get("tboxFingerprint") or ""
                    ),
                    "tboxReleaseId": str(
                        latest_candidate_release.get("tboxReleaseId") or ""
                    ),
                    "ruleboxReleaseId": str(
                        latest_candidate_release.get("ruleboxReleaseId") or ""
                    ),
                    "promptReleaseId": str(
                        latest_candidate_release.get("promptReleaseId") or ""
                    ),
                    "modelSignalReleaseId": str(
                        latest_candidate_release.get("modelSignalReleaseId") or ""
                    ),
                })
            patch_health = getattr(self.registry, "patch_health", None)
            if callable(patch_health):
                patch_health(candidate_id, health_patch)
            else:
                updated_health = dict(candidate_health)
                updated_health.update(health_patch)
                self.registry.update_health(candidate_id, updated_health)
        return result
