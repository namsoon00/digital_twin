"""Audit-preserving repairs of legacy decision and follow-up rows."""

from __future__ import annotations

from typing import Callable, Dict
from digital_twin.modules.read_models.domain.investment_flow import investment_flow_id
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.infrastructure.operational_common import json_dumps
from .ports import ConnectionFactory


def supersede_noncurrent_follow_ups(
    limit: int = 5000,
    *,
    _transaction: ConnectionFactory,
    utc_now_iso: Callable[[], str],
) -> Dict[str, object]:
    """One-time repair for conditions created before single-owner tracking."""

    maximum = max(1, min(50000, int(limit or 5000)))
    stamp = utc_now_iso()
    with _transaction() as connection:
        rows = connection.execute(
            "SELECT follow_up.condition_id, follow_up.episode_id, follow_up.payload_json, "
            "current_flow.decision_episode_id AS current_episode_id "
            "FROM investment_decision_follow_ups follow_up "
            "JOIN investment_flow_current current_flow "
            "ON current_flow.account_id = follow_up.account_id "
            "AND current_flow.symbol = follow_up.symbol "
            "WHERE follow_up.status = 'pending' "
            "AND follow_up.episode_id <> current_flow.decision_episode_id "
            "ORDER BY follow_up.updated_at, follow_up.condition_id LIMIT %s",
            (maximum,),
        ).fetchall()
        changed = 0
        for row in rows or []:
            payload = _json_loads(row.get("payload_json"), {})
            payload.update(
                {
                    "status": "superseded",
                    "trackingStatus": "stopped-newer-decision",
                    "supersededByEpisodeId": str(row.get("current_episode_id") or ""),
                    "supersededAt": stamp,
                }
            )
            cursor = connection.execute(
                "UPDATE investment_decision_follow_ups SET status = 'superseded', "
                "payload_json = %s, updated_at = %s WHERE condition_id = %s "
                "AND status = 'pending'",
                (json_dumps(payload), stamp, str(row.get("condition_id") or "")),
            )
            changed += max(0, int(getattr(cursor, "rowcount", 0) or 0))
    return {
        "status": "repaired",
        "candidateCount": len(rows or []),
        "supersededCount": changed,
    }


def quarantine_invalid_legacy_outcomes(
    limit: int = 5000,
    *,
    _transaction: ConnectionFactory,
    utc_now_iso: Callable[[], str],
) -> Dict[str, object]:
    """Remove operational observations from active decision continuity.

    Rows are retained for audit, but they can no longer become the current
    investment decision, produce follow-up work, or open an action plan.
    """

    maximum = max(1, min(50000, int(limit or 5000)))
    quarantine_status = "invalid-legacy-outcome"
    stamp = utc_now_iso()
    with _transaction() as connection:
        rows = connection.execute(
            "SELECT episode_id, account_id, symbol, payload_json FROM investment_decision_episodes "
            "WHERE status <> %s AND (action = 'NO_ACTION' OR "
            "(source = 'v2-reasoning-case' AND selected_hypothesis_id = '')) "
            "ORDER BY decided_at, episode_id LIMIT %s",
            (quarantine_status, maximum),
        ).fetchall()
        episode_ids = []
        affected_scopes = set()
        for row in rows or []:
            episode_id = str(row.get("episode_id") or "")
            if not episode_id:
                continue
            affected_scopes.add(
                (
                    str(row.get("account_id") or ""),
                    str(row.get("symbol") or "").upper(),
                )
            )
            payload = _json_loads(row.get("payload_json"), {})
            payload["status"] = quarantine_status
            payload["validationState"] = "invalid"
            facts = payload.get("factsAtDecision")
            facts = dict(facts or {}) if isinstance(facts, dict) else {}
            facts["legacyOutcomeQuarantine"] = {
                "reason": "Operational observation is not a final investment decision.",
                "quarantinedAt": stamp,
            }
            payload["factsAtDecision"] = facts
            connection.execute(
                "UPDATE investment_decision_episodes SET status = %s, "
                "validation_state = 'invalid', payload_json = %s, updated_at = %s "
                "WHERE episode_id = %s",
                (quarantine_status, json_dumps(payload), stamp, episode_id),
            )
            episode_ids.append(episode_id)
        if not episode_ids:
            return {
                "status": "unchanged",
                "quarantinedCount": 0,
                "currentPointersRemoved": 0,
                "currentPointersRepaired": 0,
                "followUpsCanceled": 0,
                "outcomeTargetsExcluded": 0,
                "actionPlansCanceled": 0,
            }
        placeholders = ", ".join(["%s"] * len(episode_ids))
        cursor = connection.execute(
            "DELETE FROM investment_flow_current WHERE decision_episode_id IN ("
            + placeholders
            + ")",
            tuple(episode_ids),
        )
        current_removed = max(0, int(getattr(cursor, "rowcount", 0) or 0))
        current_repaired = 0
        for account_id, symbol in sorted(affected_scopes):
            replacement = connection.execute(
                "SELECT episode_id, selected_hypothesis_id, action, data_state, "
                "validation_state, inference_generation_id, decided_at, payload_json, updated_at "
                "FROM investment_decision_episodes WHERE account_id = %s AND symbol = %s "
                "AND action IN ('BUY', 'ADD', 'HOLD', 'TRIM', 'SELL', 'AVOID', 'WATCH') "
                "AND selected_hypothesis_id <> '' "
                "AND status NOT IN ('blocked', 'failed', 'expired', 'suppressed', 'superseded', "
                "'reference-only', 'invalid-legacy-outcome') "
                "AND validation_state NOT IN ('blocked', 'invalid', 'failed', 'error') "
                "ORDER BY decided_at DESC, episode_id DESC LIMIT 1",
                (account_id, symbol),
            ).fetchone()
            if not replacement:
                continue
            replacement_payload = _json_loads(replacement.get("payload_json"), {})
            replacement_id = str(replacement.get("episode_id") or "")
            connection.execute(
                """
                    INSERT INTO investment_flow_current (
                        account_id, symbol, flow_id, decision_episode_id,
                        source_abox_snapshot_id, inference_generation_id,
                        selected_hypothesis_id, action, data_state,
                        validation_state, decided_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        flow_id = VALUES(flow_id), decision_episode_id = VALUES(decision_episode_id),
                        source_abox_snapshot_id = VALUES(source_abox_snapshot_id),
                        inference_generation_id = VALUES(inference_generation_id),
                        selected_hypothesis_id = VALUES(selected_hypothesis_id), action = VALUES(action),
                        data_state = VALUES(data_state), validation_state = VALUES(validation_state),
                        decided_at = VALUES(decided_at), updated_at = VALUES(updated_at)
                    """,
                (
                    account_id,
                    symbol,
                    investment_flow_id(account_id, symbol, replacement_id),
                    replacement_id,
                    str(replacement_payload.get("sourceAboxSnapshotId") or ""),
                    str(replacement.get("inference_generation_id") or ""),
                    str(replacement.get("selected_hypothesis_id") or ""),
                    str(replacement.get("action") or ""),
                    str(replacement.get("data_state") or ""),
                    str(replacement.get("validation_state") or ""),
                    str(replacement.get("decided_at") or ""),
                    str(replacement.get("updated_at") or stamp),
                ),
            )
            current_repaired += 1
        cursor = connection.execute(
            "UPDATE investment_decision_follow_ups SET status = 'canceled', "
            "updated_at = %s, transitioned_at = %s WHERE episode_id IN ("
            + placeholders
            + ") AND status IN ('pending', 'ready')",
            (stamp, stamp, *episode_ids),
        )
        follow_ups = max(0, int(getattr(cursor, "rowcount", 0) or 0))
        cursor = connection.execute(
            "UPDATE investment_decision_outcome_targets SET status = 'excluded', "
            "exclusion_reason = 'invalid-legacy-outcome', updated_at = %s "
            "WHERE episode_id IN (" + placeholders + ") AND status <> 'observed'",
            (stamp, *episode_ids),
        )
        targets = max(0, int(getattr(cursor, "rowcount", 0) or 0))
        cursor = connection.execute(
            "UPDATE investment_action_plans SET status = 'canceled', updated_at = %s "
            "WHERE decision_episode_id IN (" + placeholders + ") "
            "AND NOT EXISTS (SELECT 1 FROM trade_execution_episodes execution "
            "WHERE execution.action_plan_id = investment_action_plans.plan_id)",
            (stamp, *episode_ids),
        )
        plans = max(0, int(getattr(cursor, "rowcount", 0) or 0))
    return {
        "status": "quarantined",
        "quarantinedCount": len(episode_ids),
        "currentPointersRemoved": current_removed,
        "currentPointersRepaired": current_repaired,
        "followUpsCanceled": follow_ups,
        "outcomeTargetsExcluded": targets,
        "actionPlansCanceled": plans,
        "episodeIds": episode_ids,
    }


def quarantine_unverified_legacy_follow_up_transitions(
    limit: int = 5000,
    *,
    _transaction: ConnectionFactory,
    utc_now_iso: Callable[[], str],
) -> Dict[str, object]:
    """Keep legacy terminal rows for audit without treating them as edges."""

    maximum = max(1, min(50000, int(limit or 5000)))
    stamp = utc_now_iso()
    quarantined = []
    with _transaction() as connection:
        rows = connection.execute(
            "SELECT condition_id, payload_json FROM investment_decision_follow_ups "
            "WHERE status IN ('satisfied', 'invalidated', 'expired') "
            "ORDER BY updated_at, condition_id LIMIT %s",
            (maximum,),
        ).fetchall()
        for row in rows or []:
            payload = _json_loads(row.get("payload_json"), {})
            if bool(payload.get("transitionVerified")):
                continue
            condition_id = str(row.get("condition_id") or "")
            payload.update(
                {
                    "status": "legacy-unverified",
                    "transitionVerified": False,
                    "legacyTransitionState": "unverified",
                    "legacyTransitionQuarantinedAt": stamp,
                }
            )
            cursor = connection.execute(
                "UPDATE investment_decision_follow_ups SET status = 'legacy-unverified', "
                "payload_json = %s, updated_at = %s WHERE condition_id = %s "
                "AND status IN ('satisfied', 'invalidated', 'expired')",
                (json_dumps(payload), stamp, condition_id),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) > 0:
                quarantined.append(condition_id)
    return {
        "status": "quarantined" if quarantined else "unchanged",
        "quarantinedCount": len(quarantined),
        "conditionIds": quarantined,
    }
