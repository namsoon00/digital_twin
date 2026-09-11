"""Immutable decision facts and separate mutable streams for replay."""

from __future__ import annotations

from typing import Dict, List
from digital_twin.modules.decisions.domain.investment_brain import canonical_investment_timestamp
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from .ports import ConnectionFactory


def list_replay_records(
    account_id: str = "",
    symbol: str = "",
    limit: int = 500,
    *,
    _connect: ConnectionFactory,
) -> List[Dict[str, object]]:
    """Read immutable decision payloads separately from later observations.

    The normal list view intentionally hydrates current follow-up state and
    outcomes. Historical replay needs the original payload plus those
    mutable records as separate streams so an application service can apply
    an explicit point-in-time cutoff.
    """

    where = []
    params: List[object] = []
    if account_id:
        where.append("account_id = %s")
        params.append(str(account_id))
    if symbol:
        where.append("symbol = %s")
        params.append(str(symbol).upper())
    params.append(max(1, min(2000, int(limit or 500))))
    sql = "SELECT episode_id, payload_json, status, decided_at, created_at FROM investment_decision_episodes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY decided_at DESC, episode_id DESC LIMIT %s"
    with _connect() as connection:
        episode_rows = connection.execute(sql, tuple(params)).fetchall()
        episode_ids = [
            str(row.get("episode_id") or "")
            for row in episode_rows or []
            if str(row.get("episode_id") or "")
        ]
        outcome_rows = []
        follow_up_rows = []
        if episode_ids:
            placeholders = ",".join(["%s"] * len(episode_ids))
            outcome_rows = connection.execute(
                "SELECT episode_id, observed_at, payload_json FROM investment_decision_outcomes "
                "WHERE episode_id IN (" + placeholders + ") "
                "ORDER BY observed_at ASC, outcome_id ASC",
                tuple(episode_ids),
            ).fetchall()
            follow_up_rows = connection.execute(
                "SELECT episode_id, observable, transitioned_at, payload_json "
                "FROM investment_decision_follow_ups WHERE episode_id IN (" + placeholders + ") "
                "ORDER BY created_at ASC, condition_id ASC",
                tuple(episode_ids),
            ).fetchall()

    outcomes: Dict[str, List[Dict[str, object]]] = {}
    for row in outcome_rows or []:
        payload = _json_loads(row.get("payload_json"), {})
        if not payload:
            continue
        if not payload.get("observedAt"):
            payload["observedAt"] = canonical_investment_timestamp(row.get("observed_at"))
        outcomes.setdefault(str(row.get("episode_id") or ""), []).append(payload)

    follow_ups: Dict[str, Dict[str, List[Dict[str, object]]]] = {}
    for row in follow_up_rows or []:
        payload = _json_loads(row.get("payload_json"), {})
        if not payload:
            continue
        if not payload.get("transitionAt") and row.get("transitioned_at"):
            payload["transitionAt"] = canonical_investment_timestamp(row.get("transitioned_at"))
        buckets = follow_ups.setdefault(
            str(row.get("episode_id") or ""),
            {"tracked": [], "unsupported": []},
        )
        buckets["tracked" if bool(row.get("observable")) else "unsupported"].append(payload)

    result = []
    for row in episode_rows or []:
        episode_id = str(row.get("episode_id") or "")
        snapshot = _json_loads(row.get("payload_json"), {})
        stored_decided_at = canonical_investment_timestamp(row.get("decided_at"))
        if stored_decided_at:
            snapshot["decidedAt"] = stored_decided_at
        snapshot["recordedAt"] = canonical_investment_timestamp(row.get("created_at"))
        buckets = follow_ups.get(episode_id, {"tracked": [], "unsupported": []})
        result.append(
            {
                "episodeSnapshot": snapshot,
                "persistedStatus": str(row.get("status") or ""),
                "outcomes": list(outcomes.get(episode_id, [])),
                "followUps": list(buckets["tracked"]),
                "unsupportedFollowUps": list(buckets["unsupported"]),
            }
        )
    return result
