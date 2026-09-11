"""Point-in-time contract for event evidence used by ontology rules."""

from datetime import datetime, timezone
from typing import Dict, Mapping, Optional


EVENT_TIME_CONTRACT_VERSION = "event-time-contract-v1"

EVENT_MAX_AGE_MINUTES = {
    "earnings": 60 * 24 * 14,
    "disclosure": 60 * 24 * 30,
    "corporate-action": 60 * 24 * 45,
    "regulatory": 60 * 24 * 45,
}

EVENT_MAX_AGE_SETTING_KEYS = {
    "earnings": "eventEvidenceEarningsMaxAgeMinutes",
    "disclosure": "eventEvidenceDisclosureMaxAgeMinutes",
    "corporate-action": "eventEvidenceCorporateActionMaxAgeMinutes",
    "regulatory": "eventEvidenceRegulatoryMaxAgeMinutes",
}


def configured_event_max_age_minutes(settings: Mapping[str, object], event_kind: str) -> int:
    kind = str(event_kind or "event").strip().lower()
    fallback = EVENT_MAX_AGE_MINUTES.get(kind, 60 * 24 * 30)
    key = EVENT_MAX_AGE_SETTING_KEYS.get(kind, "eventEvidenceDefaultMaxAgeMinutes")
    try:
        value = int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(1, value)


def parse_event_timestamp(value: object) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit() and len(text) == 8:
        text = text[:4] + "-" + text[4:6] + "-" + text[6:]
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def event_time_contract(
    *,
    event_kind: str,
    effective_at: object,
    retrieved_at: object,
    evaluated_at: object,
    max_age_minutes: int = 0,
) -> Dict[str, object]:
    """Classify an event against the snapshot clock, never the process clock."""
    kind = str(event_kind or "event").strip().lower()
    maximum_age = int(max_age_minutes or EVENT_MAX_AGE_MINUTES.get(kind, 60 * 24 * 30))
    effective = parse_event_timestamp(effective_at)
    evaluated = parse_event_timestamp(evaluated_at)
    retrieved = parse_event_timestamp(retrieved_at)

    state = "invalid"
    freshness = "unknown"
    reason = "사건 발생시각 또는 판단 기준시각이 없습니다."
    eligible = False
    age_minutes = None
    valid_until = ""
    if effective and evaluated:
        age_minutes = (evaluated - effective).total_seconds() / 60.0
        valid_until_dt = effective.timestamp() + maximum_age * 60
        valid_until = datetime.fromtimestamp(valid_until_dt, tz=timezone.utc).isoformat()
        if age_minutes < -5:
            state = "future"
            freshness = "invalid"
            reason = "판단 기준시각 이후에 발생한 사건이라 현재 판단에 사용할 수 없습니다."
        elif age_minutes <= maximum_age:
            state = "active"
            freshness = "fresh"
            eligible = True
            reason = "사건 발생시각이 현재 판단의 유효기간 안에 있습니다."
        else:
            state = "expired"
            freshness = "stale"
            reason = "사건 발생 후 판단 유효기간이 지났습니다. 감사·조회에만 사용합니다."

    return {
        "eventTimeContractVersion": EVENT_TIME_CONTRACT_VERSION,
        "effectiveAt": effective.isoformat() if effective else str(effective_at or ""),
        "retrievedAt": retrieved.isoformat() if retrieved else str(retrieved_at or ""),
        "evaluatedAt": evaluated.isoformat() if evaluated else str(evaluated_at or ""),
        "validFrom": effective.isoformat() if effective else "",
        "validUntil": valid_until,
        "eventAgeMinutes": round(age_minutes, 2) if age_minutes is not None else None,
        "eventMaxAgeMinutes": maximum_age,
        "eventLifecycleState": state,
        "eventFreshnessClass": freshness,
        "eventDecisionEligible": eligible,
        "eventDecisionReason": reason,
        "freshnessRequired": True,
        "freshnessStatus": freshness,
        "freshnessAgeMinutes": round(age_minutes, 2) if age_minutes is not None else None,
        "maxAgeMinutes": maximum_age,
        "sourceAsOf": effective.isoformat() if effective else str(effective_at or ""),
        "sourceFetchedAt": retrieved.isoformat() if retrieved else str(retrieved_at or ""),
        "sourceTimestampPresent": bool(effective),
        "judgementEvidenceUsable": eligible,
        "freshnessGateReason": reason,
    }
