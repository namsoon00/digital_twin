"""Observation repair never substitutes a later quote for the original outcome."""

from typing import Mapping


DATA_GAP_ELIGIBILITIES = frozenset({"excluded-contract-data-gap", "excluded-criterion-data-gap"})


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
        before.get(key) != after.get(key) for key in ("contractFingerprint", "horizonMinutes", "decisionPrice")
    ):
        raise ValueError("Outcome repair must preserve the original observation and evaluation contract.")


def outcome_evaluation_history(previous: Mapping, stamp: str) -> list:
    payload = dict(previous.get("payload") or {})
    return (list(payload.get("evaluationHistory") or []) + [{
        "replacedAt": stamp,
        "calibrationEligibility": payload.get("calibrationEligibility"),
        "missingRequiredMetricIds": payload.get("missingRequiredMetricIds") or [],
        "missingObservationDomains": payload.get("missingObservationDomains") or [],
    }])[-5:]
