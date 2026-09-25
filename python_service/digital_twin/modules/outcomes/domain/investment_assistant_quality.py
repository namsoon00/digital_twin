"""Point-in-time quality evaluation for investment-assistant episodes."""

from __future__ import annotations

import hashlib
import json
import statistics
from typing import Dict, Mapping, Sequence


INVESTMENT_ASSISTANT_QUALITY_VERSION = "investment-assistant-quality-v1"


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _rate(numerator: int, denominator: int):
    return round(numerator / denominator, 6) if denominator else None


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate_investment_assistant_quality(
    observations: Sequence[Mapping[str, object]],
    *,
    minimum_independent_episodes: int,
    maximum_unsupported_claim_rate: float,
    maximum_duplicate_delivery_rate: float,
    minimum_reproducibility_rate: float,
) -> Dict[str, object]:
    """Evaluate independent frozen episodes without using future-repaired inputs."""

    latest_by_episode: Dict[str, Dict[str, object]] = {}
    excluded = []
    for raw in observations or []:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        episode_id = _text(row.get("episodeId") or row.get("eventId"))
        if not episode_id:
            excluded.append({"reason": "episode-identity-missing"})
            continue
        if bool(row.get("futureInformationUsed")):
            excluded.append({"episodeId": episode_id, "reason": "future-information-leakage"})
            continue
        if _text(row.get("replayState")) in {"partial", "audit-only", "missing-source-revision"}:
            excluded.append({"episodeId": episode_id, "reason": "legacy-or-partial-replay"})
            continue
        existing = latest_by_episode.get(episode_id)
        if existing is None or _text(row.get("recordedAt")) > _text(existing.get("recordedAt")):
            latest_by_episode[episode_id] = row

    episodes = list(latest_by_episode.values())
    count = len(episodes)
    traceable = sum(bool(item.get("sourceTraceComplete")) for item in episodes)
    reproducible = sum(bool(item.get("calculationReproducible")) for item in episodes)
    unsupported_claims = sum(int(item.get("unsupportedClaimCount") or 0) for item in episodes)
    verifiable_claims = sum(int(item.get("verifiableClaimCount") or 0) for item in episodes)
    duplicate_deliveries = sum(int(item.get("duplicateDeliveryCount") or 0) for item in episodes)
    successful_deliveries = sum(int(item.get("successfulDeliveryCount") or 0) for item in episodes)
    missed_material_events = sum(int(item.get("missedMaterialEventCount") or 0) for item in episodes)
    labelled_material_events = sum(int(item.get("labelledMaterialEventCount") or 0) for item in episodes)
    ai_failures = sum(int(item.get("aiFailureCount") or 0) for item in episodes)
    ai_attempts = sum(int(item.get("aiAttemptCount") or 0) for item in episodes)
    useful = sum(bool(item.get("userMarkedUseful")) for item in episodes if item.get("userMarkedUseful") is not None)
    feedback_count = sum(item.get("userMarkedUseful") is not None for item in episodes)
    latencies = [float(item.get("endToEndLatencyMs")) for item in episodes if item.get("endToEndLatencyMs") is not None]
    token_cost = sum(int(item.get("aiInputTokens") or 0) + int(item.get("aiOutputTokens") or 0) for item in episodes)

    metrics = {
        "independentEpisodeCount": count,
        "sourceTraceRate": _rate(traceable, count),
        "calculationReproducibilityRate": _rate(reproducible, count),
        "unsupportedClaimRate": _rate(unsupported_claims, verifiable_claims),
        "duplicateDeliveryRate": _rate(duplicate_deliveries, successful_deliveries),
        "materialEventCaptureRate": _rate(labelled_material_events - missed_material_events, labelled_material_events),
        "aiFailureRate": _rate(ai_failures, ai_attempts),
        "userUsefulRate": _rate(useful, feedback_count),
        "endToEndLatencyMedianMs": round(statistics.median(latencies), 3) if latencies else None,
        "totalAiTokens": token_cost,
    }
    enough_samples = count >= max(1, int(minimum_independent_episodes))
    gates = {
        "sampleSize": enough_samples,
        "unsupportedClaims": metrics["unsupportedClaimRate"] is not None and metrics["unsupportedClaimRate"] <= maximum_unsupported_claim_rate,
        "duplicateDelivery": metrics["duplicateDeliveryRate"] is not None and metrics["duplicateDeliveryRate"] <= maximum_duplicate_delivery_rate,
        "reproducibility": metrics["calculationReproducibilityRate"] is not None and metrics["calculationReproducibilityRate"] >= minimum_reproducibility_rate,
        "futureLeakage": not any(item.get("reason") == "future-information-leakage" for item in excluded),
    }
    ready = all(gates.values())
    material = {
        "contractVersion": INVESTMENT_ASSISTANT_QUALITY_VERSION,
        "status": "eligible-for-expansion-review" if ready else "observation-incomplete" if not enough_samples else "quality-gates-failed",
        "metrics": metrics,
        "gates": gates,
        "excludedObservations": excluded,
        "developmentCompleteDoesNotImplyEmpiricalQualification": True,
        "expansionDecision": "manual-review-required" if ready else "remain-limited-or-reference",
    }
    digest = _digest(material)
    return {**material, "reportId": "investment-assistant-quality:" + digest[:32]}


__all__ = ["INVESTMENT_ASSISTANT_QUALITY_VERSION", "evaluate_investment_assistant_quality"]
