"""Pending-condition observation and compare-and-set transitions."""

from __future__ import annotations

from typing import Callable, Dict, List
from digital_twin.modules.outcomes.domain.decision_follow_up import evaluate_follow_up_conditions
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.infrastructure.operational_common import json_dumps
from .ports import ConnectionFactory


def evaluate_follow_up_observation(
    account_id: str,
    symbol: str,
    facts: Dict[str, object],
    observed_at: str,
    *,
    _connect: ConnectionFactory,
    utc_now_iso: Callable[[], str],
) -> List[Dict[str, object]]:
    """Advance only pending, observable conditions for one scoped subject."""

    with _connect() as connection:
        rows = connection.execute(
            """
                SELECT follow_up.condition_id, follow_up.episode_id,
                       follow_up.account_id, follow_up.symbol, follow_up.payload_json
                FROM investment_decision_follow_ups follow_up
                LEFT JOIN investment_flow_current current_flow
                  ON current_flow.account_id = follow_up.account_id
                 AND current_flow.symbol = follow_up.symbol
                 AND current_flow.decision_episode_id = follow_up.episode_id
                LEFT JOIN investment_ai_insight_episodes insight
                  ON insight.episode_id = follow_up.episode_id
                 AND insight.account_id = follow_up.account_id AND insight.symbol = follow_up.symbol
                WHERE follow_up.account_id = %s AND follow_up.symbol = %s
                  AND follow_up.observable = 1
                  AND ((follow_up.status = 'pending' AND (current_flow.decision_episode_id IS NOT NULL
                        OR (insight.episode_id IS NOT NULL
                            AND JSON_UNQUOTE(JSON_EXTRACT(follow_up.payload_json, '$.ownerKind')) = 'ai-insight'
                            AND JSON_EXTRACT(insight.payload_json, '$.aiAuthored') = true
                            AND JSON_EXTRACT(insight.payload_json, '$.publicationContractPassed') = true)))
                       OR (follow_up.status IN ('satisfied', 'invalidated')
                           AND JSON_UNQUOTE(JSON_EXTRACT(follow_up.payload_json, '$.reasoningDispatchStatus')) = 'pending'))
                ORDER BY follow_up.updated_at ASC, follow_up.condition_id ASC
                LIMIT 80
                """,
            (str(account_id or ""), str(symbol or "").upper()),
        ).fetchall()
    transitions: List[Dict[str, object]] = []
    for row in rows or []:
        payload = _json_loads(row.get("payload_json"), {})
        if payload.get("reasoningDispatchStatus") == "pending" and payload.get("transitionVerified"):
            transitions.append(payload)
            continue
        payload.update(
            {
                "episodeId": str(row.get("episode_id") or ""),
                "accountId": str(row.get("account_id") or account_id or ""),
                "symbol": str(row.get("symbol") or symbol or "").upper(),
                "trackingOwner": "system",
                "trackingCadence": "each-live-snapshot",
                "trackingStatus": "active",
                "notificationOnTransition": True,
            }
        )
        updated, material = evaluate_follow_up_conditions([payload], facts, observed_at)
        if not updated or updated[0] == payload:
            continue
        condition = updated[0]
        if material and condition.get("transitionId"):
            condition.update({"reasoningDispatchStatus": "pending", "sourceSnapshotObservedAt": observed_at})
        stamp = utc_now_iso()
        transition_at = str(condition.get("transitionAt") or "") if material else ""
        with _connect() as connection:
            cursor = connection.execute(
                """
                    UPDATE investment_decision_follow_ups
                    SET status = %s, payload_json = %s, updated_at = %s,
                        transitioned_at = CASE WHEN %s <> '' THEN %s ELSE transitioned_at END
                    WHERE condition_id = %s AND status = 'pending' AND BINARY payload_json = BINARY %s
                    """,
                (
                    str(condition.get("status") or "pending"),
                    json_dumps(condition),
                    stamp,
                    transition_at,
                    transition_at,
                    str(row.get("condition_id") or ""),
                    row.get("payload_json"),
                ),
            )
        if material and int(getattr(cursor, "rowcount", 0) or 0) > 0:
            transitions.append(condition)
    return transitions


def acknowledge_follow_up_reasoning(account_id, condition_id, transition_id, *, _connect):
    with _connect() as connection:
        connection.execute(
            "UPDATE investment_decision_follow_ups SET payload_json = JSON_SET(payload_json, '$.reasoningDispatchStatus', 'published') "
            "WHERE account_id = %s AND condition_id = %s "
            "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.transitionId')) = %s "
            "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.reasoningDispatchStatus')) = 'pending'",
            (account_id, condition_id, transition_id),
        )
