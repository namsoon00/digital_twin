"""Validate exact current-source witnesses before testing a flow hypothesis."""
from datetime import datetime, timedelta, timezone


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (ValueError, TypeError):
        return None


def current_flow_source_clock(subject, conditions, index, cutoff):
    """Missing, conflicting or old observations are unknown, never reversal."""
    now = _stamp(cutoff)
    stamps, deadlines, reasons = [], [], []
    for condition in conditions:
        filters = condition.target_property_filters or {}
        is_flow = filters.get("dataScope") == "verified-current-flow-v1"
        is_quote = condition.condition_id == "current-flow-price-source"
        is_quality = condition.condition_id == "market-evidence-profile-eligible"
        if not is_flow and not is_quote and not is_quality:
            continue
        witnesses = []
        for edge in index["relations"].get((subject.entity_id, "out", condition.relation_type), ()):
            target = index["entities"].get(edge.target)
            if not target or target.kind != condition.target_kind:
                continue
            p = target.properties or {}
            if is_flow and (p.get("field") != filters.get("field") or p.get("dataScope") != filters.get("dataScope")):
                continue
            if is_quality and p.get("field") != filters.get("field"):
                continue
            witnesses.append(p)
        if len(witnesses) != 1:
            reasons.append(condition.condition_id + ":source-missing-or-ambiguous")
            continue
        p = witnesses[0]
        if is_quality:
            if p.get("dataState") != "sufficient":
                reasons.append(condition.condition_id + ":source-not-usable")
            continue
        at, received = _stamp(p.get("sourceAsOf")), _stamp(p.get("sourceFetchedAt"))
        try:
            max_age = min(600, float(p.get("maxAgeMinutes", 10)) * 60)
        except (ValueError, TypeError):
            max_age = 0
        usable = (now and at and received and at <= received <= now
                  and max_age > 0 and 0 <= (now - at).total_seconds() <= max_age
                  and p.get("judgementEvidenceUsable") is True
                  and (not filters.get("priceChangeUsable") or p.get("priceChangeUsable") is True)
                  and (p.get("marketSession") == "regular" if is_flow else p.get("marketSessionStatus") == "open"))
        expiry = at + timedelta(seconds=max_age) if at and max_age > 0 else None
        if p.get("validUntil"):
            supplied = _stamp(p["validUntil"])
            usable = usable and supplied is not None and now < supplied
            if supplied and expiry:
                expiry = min(expiry, supplied)
        if usable:
            stamps.append(at)
            deadlines.append(expiry)
        else:
            reasons.append(condition.condition_id + ":source-not-usable")
    if not stamps:
        reasons.append("current-flow-source-clock-unavailable")
    return {"validUntil": min(deadlines).isoformat() if deadlines else "",
            "maxAgeMinutes": 10, "reasons": sorted(set(reasons)), "observedAt": min(stamps).isoformat() if stamps else "",
            "marketSession": "regular" if not reasons else "unknown",
            "sourceAgeSeconds": (now - min(stamps)).total_seconds() if now and stamps else None}
