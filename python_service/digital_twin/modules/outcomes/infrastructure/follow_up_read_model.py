"""Hydrate current watch state without rewriting the immutable AI interpretation."""

from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.modules.outcomes.domain.follow_up_tracking import follow_up_is_registered


def hydrate_insight_followups(connection, episodes):
    ids = sorted({str(condition.get("conditionId")) for episode in episodes
                  for condition in (episode.get("insight") or {}).get("followUpConditions") or []
                  if isinstance(condition, dict) and condition.get("conditionId")})
    persisted = {}
    # Bounded batches also cover full read-model pages without an N+1 query.
    for start in range(0, len(ids), 500):
        batch = ids[start:start + 500]
        rows = connection.execute(
            "SELECT condition_id, account_id, symbol, payload_json FROM investment_decision_follow_ups "
            "WHERE condition_id IN (" + ",".join(["%s"] * len(batch)) + ")",
            tuple(batch),
        ).fetchall()
        for row in rows or []:
            persisted[(row["account_id"], row["symbol"], row["condition_id"])] = _json_loads(row.get("payload_json"), {})
    for episode in episodes:
        insight = episode.get("insight") or {}
        hydrated = []
        for condition in insight.get("followUpConditions") or []:
            if not isinstance(condition, dict):
                continue
            saved = persisted.get((episode.get("accountId"), episode.get("symbol"), condition.get("conditionId")))
            if (saved and follow_up_is_registered(saved)
                    and saved.get("accountId") == episode.get("accountId")
                    and saved.get("symbol") == episode.get("symbol")):
                hydrated.append(saved)
            else:
                hydrated.append({**condition, "registration": {}, "trackingStatus": "unregistered",
                                 "trackingOwner": "none", "notificationOnTransition": False})
        if hydrated:
            insight["followUpConditions"] = hydrated
