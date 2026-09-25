"""Evidence-bounded interpretation of an event and a market reaction.

This module validates whether a causal *hypothesis* may be stated.  It never
turns correlation into a confirmed cause and it keeps a valuation mechanism
separate from the observed market-price reaction.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Dict, Mapping


CAUSAL_ATTRIBUTION_VERSION = "causal-attribution-v1"
CLAIM_STRENGTHS = frozenset({"observed-event", "supported-mechanism", "causal-hypothesis", "unresolved"})


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _number(value: object):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _clock(value: object):
    text = _text(value)
    if not text or len(text) <= 10:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _fingerprint(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _independent_evidence(event: Mapping[str, object]) -> tuple[int, int, list[str]]:
    references = [item for item in event.get("sourceReferences") or [] if isinstance(item, Mapping)]
    documents = {
        (
            _text(item.get("upstreamOrigin") or item.get("providerId") or item.get("datasetId")),
            _text(item.get("providerRevision") or item.get("revisionId")),
        )
        for item in references
        if _text(item.get("datasetId") or item.get("providerId"))
    }
    independent = {
        _text(item.get("upstreamOrigin") or item.get("providerId") or item.get("datasetId"))
        for item in references
        if _text(item.get("upstreamOrigin") or item.get("providerId") or item.get("datasetId"))
    }
    return len(documents), len(independent), sorted(independent)


def evaluate_causal_attribution(
    event: Mapping[str, object],
    price_reaction: Mapping[str, object] = None,
    *,
    driver_map: Mapping[str, object] = None,
    alternative_explanations: list[Mapping[str, object]] = None,
) -> Dict[str, object]:
    """Classify one event interpretation and preserve every reason to abstain."""

    event = dict(event or {})
    reaction = dict(price_reaction or {})
    drivers = dict(driver_map or {})
    alternatives = [dict(item) for item in alternative_explanations or [] if isinstance(item, Mapping)]
    event_contract = event.get("companyEventContract") if isinstance(event.get("companyEventContract"), Mapping) else event
    event_id = _text(event_contract.get("eventId") or event_contract.get("observationId"))
    symbol = _text(event_contract.get("symbol") or event.get("symbol")).upper()
    announced_at = _clock(event_contract.get("announcedAt") or event_contract.get("publishedAt"))
    reaction_start = _clock(reaction.get("windowStart") or reaction.get("observedAt"))
    reaction_end = _clock(reaction.get("windowEnd") or reaction.get("observedAt"))
    adjusted = bool(reaction.get("corporateActionAdjusted"))
    session = _text(reaction.get("session"))
    benchmark_return = _number(reaction.get("benchmarkReturnPct"))
    sector_return = _number(reaction.get("sectorReturnPct"))
    stock_return = _number(reaction.get("stockReturnPct"))
    abnormal_return = _number(reaction.get("abnormalReturnPct"))
    if abnormal_return is None and stock_return is not None and benchmark_return is not None:
        abnormal_return = stock_return - benchmark_return
    source_count, independent_count, source_families = _independent_evidence(event_contract)

    observed_event = bool(event_id and symbol and event_contract.get("sourceReferences"))
    mechanism_links = [
        dict(item) for item in drivers.get("drivers") or []
        if isinstance(item, Mapping) and item.get("validationState") == "verified"
    ]
    supported_mechanism = bool(observed_event and mechanism_links)
    chronology_valid = bool(announced_at and reaction_start and announced_at <= reaction_start)
    chronology_invalid = bool(announced_at and reaction_start and announced_at > reaction_start)
    reaction_window_complete = bool(reaction_start and reaction_end and reaction_start <= reaction_end and session and adjusted)
    benchmark_complete = benchmark_return is not None and sector_return is not None
    material_reaction = abnormal_return is not None and abs(abnormal_return) >= float(reaction.get("materialThresholdPct") or 1.0)
    alternatives_checked = any(_text(item.get("state")) in {"checked", "observed", "ruled-out", "supported"} for item in alternatives)

    blocking_reasons = []
    limitations = []
    if not observed_event:
        blocking_reasons.append("verified-event-source-missing")
    if not announced_at:
        blocking_reasons.append("precise-event-clock-missing")
    if chronology_invalid:
        blocking_reasons.append("event-after-price-reaction")
    if not reaction_window_complete:
        blocking_reasons.append("adjusted-session-price-window-missing")
    if not benchmark_complete:
        blocking_reasons.append("market-sector-benchmarks-missing")
    if abnormal_return is None:
        blocking_reasons.append("abnormal-return-unavailable")
    elif not material_reaction:
        limitations.append("abnormal-return-below-material-threshold")
    if not alternatives_checked:
        blocking_reasons.append("alternative-explanations-not-checked")
    if independent_count == 0:
        blocking_reasons.append("independent-source-family-missing")
    if source_count > independent_count and independent_count:
        limitations.append("correlated-source-documents-deduplicated")

    causal_hypothesis_supported = bool(
        observed_event
        and chronology_valid
        and reaction_window_complete
        and benchmark_complete
        and material_reaction
        and alternatives_checked
        and independent_count >= 1
    )
    if causal_hypothesis_supported:
        strength = "causal-hypothesis"
    elif supported_mechanism:
        strength = "supported-mechanism"
    elif observed_event:
        strength = "observed-event"
    else:
        strength = "unresolved"

    material = {
        "contractVersion": CAUSAL_ATTRIBUTION_VERSION,
        "symbol": symbol,
        "eventId": event_id,
        "eventObserved": observed_event,
        "mechanismSupported": supported_mechanism,
        "priceCausalHypothesisSupported": causal_hypothesis_supported,
        "claimStrength": strength,
        "chronology": {
            "eventAt": announced_at.isoformat().replace("+00:00", "Z") if announced_at else "",
            "reactionStart": reaction_start.isoformat().replace("+00:00", "Z") if reaction_start else "",
            "reactionEnd": reaction_end.isoformat().replace("+00:00", "Z") if reaction_end else "",
            "state": "valid" if chronology_valid else "invalid" if chronology_invalid else "unknown",
        },
        "marketReaction": {
            "session": session,
            "corporateActionAdjusted": adjusted,
            "stockReturnPct": stock_return,
            "benchmarkReturnPct": benchmark_return,
            "sectorReturnPct": sector_return,
            "abnormalReturnPct": abnormal_return,
            "material": material_reaction,
        },
        "mechanismDriverIds": [_text(item.get("driverId")) for item in mechanism_links],
        "sourceCount": source_count,
        "independentEvidenceCount": independent_count,
        "independentSourceFamilies": source_families,
        "alternativeExplanations": alternatives,
        "blockingReasons": sorted(set(blocking_reasons)),
        "limitations": sorted(set(limitations)),
        "valuationImpactSeparateFromPriceCause": True,
    }
    digest = _fingerprint(material)
    return {
        **material,
        "attributionId": "causal-attribution:" + digest[:32],
        "materialFingerprint": "causal-attribution-material:" + digest,
        "customerClaimEligible": strength in {"observed-event", "supported-mechanism", "causal-hypothesis"},
        "priceCauseClaimEligible": causal_hypothesis_supported,
    }


__all__ = ["CAUSAL_ATTRIBUTION_VERSION", "CLAIM_STRENGTHS", "evaluate_causal_attribution"]
