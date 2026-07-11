import json
from datetime import datetime, timezone

from ..domain.investment_research import ResearchEvidence
from ..domain.notification_rules import NotificationRuleConfig
from ..domain.notification_templates import NotificationTemplate


IN_FLIGHT_NOTIFICATION_HISTORY_MINUTES = 30
MAX_NOTIFICATION_DELIVERY_ATTEMPTS = 5
NOTIFICATION_HISTORY_LOOKBACK_LIMIT = 25


def json_dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def parse_utc_datetime(value: str):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def age_minutes_since(value: str, now=None) -> int:
    parsed = parse_utc_datetime(value)
    if not parsed:
        return 0
    current = now or datetime.now(timezone.utc)
    return max(0, int((current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() // 60))


def notification_history_row_age_minutes(row) -> int:
    return age_minutes_since(str(row["created_at"] or ""))


def notification_history_is_recent_in_flight(row) -> bool:
    status = str(row["status"] or "").strip()
    if status not in {"pending", "processing"}:
        return False
    return notification_history_row_age_minutes(row) <= IN_FLIGHT_NOTIFICATION_HISTORY_MINUTES


def rule_from_row(row) -> NotificationRuleConfig:
    row_keys = set(row.keys())
    try:
        conditions = json.loads(row["conditions_json"] or "[]")
    except json.JSONDecodeError:
        conditions = []
    try:
        similarity_fields = json.loads(row["similarity_fields_json"] or "[]")
    except json.JSONDecodeError:
        similarity_fields = []
    if "similarity_bypass_conditions_json" in row_keys:
        try:
            similarity_bypass_conditions = json.loads(row["similarity_bypass_conditions_json"] or "[]")
        except json.JSONDecodeError:
            similarity_bypass_conditions = []
    else:
        similarity_bypass_conditions = []
    if "market_hours_markets_json" in row_keys:
        try:
            market_hours_markets = json.loads(row["market_hours_markets_json"] or "[]")
        except json.JSONDecodeError:
            market_hours_markets = []
    else:
        market_hours_markets = []
    return NotificationRuleConfig.from_dict({
        "messageType": row["message_type"],
        "enabled": bool(row["enabled"]),
        "threshold": row["threshold"],
        "baseScore": row["base_score"],
        "lowScoreAction": row["low_score_action"],
        "conditions": conditions if isinstance(conditions, list) else [],
        "similarityEnabled": bool(row["similarity_enabled"]),
        "similarityWindowMinutes": row["similarity_window_minutes"],
        "similarityPenalty": row["similarity_penalty"],
        "similarityBypassScoreDelta": row["similarity_bypass_score_delta"],
        "similarityBypassConditions": similarity_bypass_conditions if isinstance(similarity_bypass_conditions, list) else [],
        "similarityFields": similarity_fields if isinstance(similarity_fields, list) else [],
        "stateCooldownEnabled": bool(row["state_cooldown_enabled"]) if "state_cooldown_enabled" in row_keys else None,
        "stateCooldownMinutes": row["state_cooldown_minutes"] if "state_cooldown_minutes" in row_keys else None,
        "marketHoursEnabled": bool(row["market_hours_enabled"]) if "market_hours_enabled" in row_keys else None,
        "marketHoursMarkets": market_hours_markets if isinstance(market_hours_markets, list) else [],
        "updatedAt": row["updated_at"],
    })


def template_from_row(row) -> NotificationTemplate:
    return NotificationTemplate(
        message_type=row["message_type"],
        template=row["template"],
        description=row["description"],
        enabled=bool(row["enabled"]),
        updated_at=row["updated_at"],
    )


def research_evidence_from_row(row) -> ResearchEvidence:
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    return ResearchEvidence(
        evidence_id=row["evidence_id"],
        symbol=row["symbol"],
        kind=row["kind"],
        source=row["source"],
        title=row["title"],
        summary=row["summary"],
        url=row["url"],
        observed_at=row["observed_at"],
        polarity=row["polarity"],
        impact_score=row["impact_score"],
        confidence=row["confidence"],
        published_at=row["published_at"],
        raw_payload=payload if isinstance(payload, dict) else {},
    )
