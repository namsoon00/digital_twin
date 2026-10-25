"""Source-clocked current flow measurements, without investment judgements.

The legacy mixed price/flow history is deliberately not an input. Explicitly
observed zero is retained; missing/default zero never becomes an observation.
"""
from datetime import datetime, timezone
import math
from digital_twin.modules.portfolio.contracts import expects_kr_microstructure_signals
from .data_freshness import max_age_minutes_for_kis_stage

VERSION = "verified-current-flow-v1"
FIELDS = {
    "foreignNetVolume": ("investor", "foreign_net_volume"),
    "institutionNetVolume": ("investor", "institution_net_volume"),
    "individualNetVolume": ("investor", "individual_net_volume"),
    "tradeStrength": ("ccnl", "trade_strength"),
    "bidAskImbalance": ("orderbook", "bid_ask_imbalance"),
}


def source_time(value):
    try:
        stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (ValueError, TypeError):
        return None


def verified_flow_measurements(position, cutoff, market_session, settings=None):
    """Return observations with their own provider clock and explicit validity."""
    if not expects_kr_microstructure_signals(position.market, position.currency, position.symbol):
        return []
    now = source_time(cutoff)
    coverage = position.market_signal_coverage or {}
    if not now or not isinstance(coverage, dict):
        return []
    result = []
    for field, (stage, attribute) in FIELDS.items():
        item = coverage.get(stage) or {}
        if not isinstance(item, dict):
            continue
        observed = item.get("observedFields", item.get("fields")) or []
        if field not in observed:
            continue
        try:
            value = float(getattr(position, attribute))
        except (TypeError, ValueError, AttributeError):
            continue
        if not math.isfinite(value):
            continue
        at = source_time(item.get("sourceAsOf"))
        received = source_time(item.get("fetchedAt") or item.get("sourceFetchedAt"))
        age = (now - at).total_seconds() if at else None
        max_age = min(10, max_age_minutes_for_kis_stage(stage, settings))
        freshness_states = {"fresh", "live", "realtime", "available", "near-live"}
        if stage == "investor":
            freshness_states.add("intraday-estimate")
        usable = bool(at and received and at <= received <= now and 0 <= age <= max_age * 60
                      and item.get("status") in {"available", "fresh"}
                      and item.get("freshnessStatus", "fresh") in freshness_states
                      and item.get("marketSession", "regular") in {"regular", "open"}
                      and item.get("judgementEvidenceUsable") is not False
                      and item.get("sourceTimestampState") not in {"missing", "unknown", "inferred", "fallback"}
                      and market_session == "open")
        if stage == "investor":
            valid_until = source_time(item.get("validUntil"))
            party = {"foreignNetVolume": "foreign", "institutionNetVolume": "institution", "individualNetVolume": "individual"}[field]
            party_status = (item.get("participantStatus") or {}).get(party)
            usable = (usable and item.get("measurementType") == "intraday-estimate"
                      and party_status in (None, "available")
                      and item.get("providerUpdateCurrent") is not False
                      and (not item.get("validUntil") or bool(valid_until and now < valid_until)))
        if field == "tradeStrength":
            usable = usable and item.get("tradeStrengthDecisionUsable") is True
        result.append({"field": field, "value": value, "dataScope": VERSION,
                       "sourceAsOf": str(item.get("sourceAsOf") or ""),
                       "sourceFetchedAt": str(item.get("fetchedAt") or item.get("sourceFetchedAt") or ""),
                       "provider": str(item.get("provider") or item.get("source") or "KIS " + stage),
                       "measurementType": str(item.get("measurementType") or "current-" + stage),
                       "marketSession": "regular" if market_session == "open" else market_session,
                       "judgementEvidenceUsable": bool(usable),
                       "freshnessStatus": "fresh" if usable else "unavailable",
                       "sourceTimestampPresent": bool(at),
                       "sourceTimestampState": str(item.get("sourceTimestampState") or "unspecified"),
                       "maxAgeMinutes": max_age, "validUntil": str(item.get("validUntil") or "")})
    by_field = {r["field"]: r for r in result}
    foreign, institution = (by_field.get(f) for f in ("foreignNetVolume", "institutionNetVolume"))
    if foreign and institution:
        same_clock = foreign["sourceAsOf"] == institution["sourceAsOf"]
        result.append({**foreign, "field": "smartMoneyNetVolume",
                       "value": foreign["value"] + institution["value"],
                       "judgementEvidenceUsable": bool(same_clock and foreign["judgementEvidenceUsable"]
                                                       and institution["judgementEvidenceUsable"])})
    return result
