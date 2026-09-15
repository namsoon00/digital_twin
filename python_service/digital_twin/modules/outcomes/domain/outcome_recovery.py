"""Observation repair never substitutes a later quote for the original outcome."""

from datetime import datetime, timedelta
from math import isfinite
from typing import Mapping


DATA_GAP_ELIGIBILITIES = frozenset({"excluded-contract-data-gap", "excluded-criterion-data-gap"})


def benchmark_observation_window(target: Mapping) -> dict:
    previous = dict(target.get("previousOutcome") or {})
    payload = dict(previous.get("payload") or {})
    return {
        "symbol": str(target.get("benchmarkSymbol") or "").upper(),
        "baselineAt": target.get("baselineAt") or target.get("decidedAt"),
        "targetAt": payload.get("targetAt") or target.get("targetAt"),
        "observedAt": previous.get("observedAt"),
        "maximumObservationDelayMinutes": target.get("maximumObservationDelayMinutes"),
    }


def outcome_recovery_state(previous: Mapping, incoming: Mapping, stamp: str) -> dict:
    if not outcome_needs_data(incoming):
        return {"state": "complete", "attemptCount": 0, "automaticRetryStopped": False}
    prior = dict((previous.get("payload") or {}).get("evaluationRecovery") or {})
    attempt = min(8, int(prior.get("attemptCount") or 0) + 1)
    stopped = attempt >= 8
    retry_at = datetime.fromisoformat(stamp.replace("Z", "+00:00")) + timedelta(minutes=min(360, 15 * 2 ** (attempt - 1)))
    return {
        "state": "unavailable" if stopped else "waiting-data",
        "reason": "automatic-data-recovery-exhausted" if stopped else "original-observation-data-missing",
        "attemptCount": attempt, "automaticRetryStopped": stopped,
        "nextRetryAt": "" if stopped else retry_at.isoformat().replace("+00:00", "Z"),
    }


def outcome_needs_data(value: Mapping) -> bool:
    payload = value.get("payload") if isinstance(value.get("payload"), Mapping) else value
    return str(payload.get("calibrationEligibility") or "") in DATA_GAP_ELIGIBILITIES


def frozen_outcome_facts(value: Mapping) -> dict:
    payload = dict(value.get("payload") or {})
    facts = dict(payload.get("observationFacts") or {})
    facts.update({
        "currentPrice": value.get("price"),
        "decisionPrice": payload.get("decisionPrice"),
        "decisionPriceSourceAsOf": payload.get("decisionPriceSourceAsOf"),
        "sourceAsOf": value.get("observedAt") or payload.get("sourceAsOf"),
        "dataQuality": payload.get("dataQuality") or "unknown",
        "observationSource": payload.get("observationSource") or "",
    })
    return {key: value for key, value in facts.items() if value is not None}


def observation_facts(value: Mapping) -> dict:
    # Source adapters already return a bounded, secret-free observation packet.
    return {key: item for key, item in value.items() if isinstance(item, (str, int, float, bool)) or item is None}


def validate_outcome_repair(previous: Mapping, incoming: Mapping) -> None:
    before = dict(previous.get("payload") or {})
    after = dict(incoming.get("payload") or {})
    if any(previous.get(key) != incoming.get(key) for key in ("episodeId", "observedAt", "price")) or any(
        before.get(key) != after.get(key) for key in ("contractFingerprint", "horizonMinutes")
    ):
        raise ValueError("Outcome repair must preserve the original observation and evaluation contract.")
    if before.get("decisionPrice") != after.get("decisionPrice"):
        # Legacy gaps may acquire their first baseline, never replace a known
        # price. The source clock must precede the original frozen decision.
        contract = dict(before.get("hypothesisOutcomeContract") or {})
        try:
            price = float(after.get("decisionPrice"))
            source_at = datetime.fromisoformat(str(after.get("decisionPriceSourceAsOf") or "").replace("Z", "+00:00"))
            effective_at = datetime.fromisoformat(str(contract.get("effectiveAt") or "").replace("Z", "+00:00"))
            recoverable = (before.get("decisionPrice") is None and not isinstance(after.get("decisionPrice"), bool)
                           and isfinite(price) and price > 0
                           and source_at.tzinfo is not None and effective_at.tzinfo is not None
                           and source_at <= effective_at)
        except (TypeError, ValueError):
            recoverable = False
        if not recoverable:
            raise ValueError("Outcome repair requires the original point-in-time baseline.")


def outcome_evaluation_history(previous: Mapping, stamp: str) -> list:
    payload = dict(previous.get("payload") or {})
    return (list(payload.get("evaluationHistory") or []) + [{
        "replacedAt": stamp,
        "calibrationEligibility": payload.get("calibrationEligibility"),
        "missingRequiredMetricIds": payload.get("missingRequiredMetricIds") or [],
        "missingObservationDomains": payload.get("missingObservationDomains") or [],
    }])[-5:]
