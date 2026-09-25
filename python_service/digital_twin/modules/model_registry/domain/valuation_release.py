"""Immutable release, paired-comparison and rollback contracts for valuation work."""

from __future__ import annotations

import hashlib
import json
from typing import Dict, Mapping, Sequence


VALUATION_RELEASE_MANIFEST_VERSION = "valuation-release-manifest-v1"
VALUATION_PAIRED_COMPARISON_VERSION = "valuation-paired-comparison-v1"


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_valuation_release_manifest(
    *,
    release_id: object,
    git_revision: object,
    target_symbols: Sequence[object],
    model_versions: Mapping[str, object],
    source_bundle_ids: Sequence[object],
    candidate_runtime: Mapping[str, object],
    active_runtime: Mapping[str, object],
    resource_budgets: Mapping[str, object],
    rollback_artifact_id: object,
) -> Dict[str, object]:
    """Build a fail-closed release manifest without changing control pointers."""

    candidate = dict(candidate_runtime or {})
    active = dict(active_runtime or {})
    budgets = dict(resource_budgets or {})
    reasons = []
    if not _text(release_id):
        reasons.append("release-id-missing")
    if not _text(git_revision):
        reasons.append("git-revision-missing")
    symbols = sorted({_text(item).upper() for item in target_symbols or [] if _text(item)})
    if not symbols:
        reasons.append("target-symbols-missing")
    bundles = sorted({_text(item) for item in source_bundle_ids or [] if _text(item)})
    if not bundles:
        reasons.append("immutable-source-bundles-missing")
    required_budgets = {
        "maxQueueDepth", "maxDbWriteMs", "maxMemoryMb", "maxCpuPct",
        "maxCollectionRequests", "maxAiInputTokens", "maxAiOutputTokens",
    }
    for key in sorted(required_budgets):
        try:
            valid = float(budgets.get(key)) > 0
        except (TypeError, ValueError):
            valid = False
        if not valid:
            reasons.append("resource-budget-missing:" + key)
    for capability in ("notificationCreate", "notificationEnqueue", "notificationTransport"):
        if bool(candidate.get(capability)):
            reasons.append("candidate-delivery-capability-forbidden:" + capability)
    for namespace in ("graphDatabase", "queueNamespace", "cacheNamespace"):
        if not _text(candidate.get(namespace)):
            reasons.append("candidate-runtime-missing:" + namespace)
        elif _text(candidate.get(namespace)) == _text(active.get(namespace)):
            reasons.append("candidate-runtime-not-isolated:" + namespace)
    if not _text(rollback_artifact_id):
        reasons.append("rollback-artifact-missing")

    material = {
        "contractVersion": VALUATION_RELEASE_MANIFEST_VERSION,
        "releaseId": _text(release_id),
        "gitRevision": _text(git_revision),
        "targetSymbols": symbols,
        "modelVersions": dict(sorted((str(key), value) for key, value in (model_versions or {}).items())),
        "sourceBundleIds": bundles,
        "candidateRuntime": candidate,
        "activeRuntime": active,
        "resourceBudgets": budgets,
        "rollbackArtifactId": _text(rollback_artifact_id),
        "deliveryAuthorization": False,
        "status": "ready-for-isolated-comparison" if not reasons else "blocked",
        "blockedReasons": sorted(set(reasons)),
    }
    digest = _digest(material)
    return {
        **material,
        "manifestId": "valuation-release-manifest:" + digest[:32],
        "releaseFingerprint": "valuation-release:" + digest,
    }


def compare_valuation_release_pair(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    *,
    allowed_difference_fields: Sequence[str] = (),
) -> Dict[str, object]:
    """Compare two frozen executions and reject input or isolation drift."""

    left = dict(baseline or {})
    right = dict(candidate or {})
    forbidden = []
    for key in ("sourceBundleId", "observationClock", "accountId", "symbol", "targetSecurityLine"):
        if left.get(key) != right.get(key):
            forbidden.append({"field": key, "baseline": left.get(key), "candidate": right.get(key)})
    if right.get("notificationCreated") or right.get("notificationEnqueued") or right.get("transportAttempted"):
        forbidden.append({"field": "candidate-delivery-side-effect", "baseline": False, "candidate": True})
    if left.get("activePointerAfter") != right.get("activePointerAfter") or left.get("activePointerBefore") != left.get("activePointerAfter"):
        forbidden.append({"field": "active-pointer-mutation", "baseline": left.get("activePointerAfter"), "candidate": right.get("activePointerAfter")})

    allowed = set(str(item) for item in allowed_difference_fields or [])
    values_left = left.get("assessment") if isinstance(left.get("assessment"), Mapping) else {}
    values_right = right.get("assessment") if isinstance(right.get("assessment"), Mapping) else {}
    differences = []
    for key in sorted(set(values_left) | set(values_right)):
        if values_left.get(key) != values_right.get(key):
            differences.append({
                "field": key,
                "baseline": values_left.get(key),
                "candidate": values_right.get(key),
                "allowed": key in allowed,
            })
    unexplained = [item for item in differences if not item["allowed"]]
    material = {
        "contractVersion": VALUATION_PAIRED_COMPARISON_VERSION,
        "status": "passed" if not forbidden and not unexplained else "failed",
        "forbiddenDifferences": forbidden,
        "assessmentDifferences": differences,
        "unexplainedDifferenceCount": len(unexplained),
        "candidateDeliveryCount": sum(bool(right.get(key)) for key in ("notificationCreated", "notificationEnqueued", "transportAttempted")),
        "sameFrozenInput": not any(item["field"] in {"sourceBundleId", "observationClock", "accountId", "symbol", "targetSecurityLine"} for item in forbidden),
    }
    digest = _digest(material)
    return {**material, "comparisonId": "valuation-paired-comparison:" + digest[:32]}


def rollback_receipt(
    *,
    failed_release_id: object,
    restored_release_id: object,
    restored_artifact_id: object,
    pointers: Mapping[str, object],
    pending_jobs: Sequence[Mapping[str, object]],
) -> Dict[str, object]:
    """Describe a rollback result; the caller still owns actual pointer writes."""

    pointer_values = dict(pointers or {})
    reasons = []
    if not _text(restored_artifact_id):
        reasons.append("immutable-restored-artifact-missing")
    if _text(pointer_values.get("activeReleaseId")) != _text(restored_release_id):
        reasons.append("active-pointer-not-restored")
    if _text(pointer_values.get("readReleaseId")) != _text(restored_release_id):
        reasons.append("read-pointer-not-restored")
    unsafe_jobs = [
        dict(item) for item in pending_jobs or []
        if isinstance(item, Mapping)
        and _text(item.get("releaseId")) == _text(failed_release_id)
        and _text(item.get("state")) not in {"superseded", "excluded", "completed"}
    ]
    if unsafe_jobs:
        reasons.append("failed-release-jobs-still-active")
    material = {
        "contractVersion": "valuation-rollback-receipt-v1",
        "failedReleaseId": _text(failed_release_id),
        "restoredReleaseId": _text(restored_release_id),
        "restoredArtifactId": _text(restored_artifact_id),
        "pointers": pointer_values,
        "unsafePendingJobs": unsafe_jobs,
        "status": "restored" if not reasons else "incomplete",
        "blockedReasons": reasons,
    }
    return {**material, "receiptId": "valuation-rollback:" + _digest(material)[:32]}


__all__ = [
    "VALUATION_PAIRED_COMPARISON_VERSION",
    "VALUATION_RELEASE_MANIFEST_VERSION",
    "build_valuation_release_manifest",
    "compare_valuation_release_pair",
    "rollback_receipt",
]
