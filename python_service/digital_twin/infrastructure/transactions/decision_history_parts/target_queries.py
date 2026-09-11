"""Durable decision and shadow observation target reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List

from digital_twin.modules.decisions.domain.investment_brain import canonical_investment_timestamp
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from .outcome_policy import number
from .ports import ConnectionFactory


def outcome_target_summary(
    account_id: str = "",
    symbol: str = "",
    *,
    _connect: ConnectionFactory,
    utc_now_iso: Callable[[], str],
) -> Dict[str, object]:
    clauses = []
    params: List[object] = []
    if account_id:
        clauses.append("account_id = %s")
        params.append(str(account_id))
    if symbol:
        clauses.append("symbol = %s")
        params.append(str(symbol).upper())
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    due_clause = (" AND " + " AND ".join(clauses)) if clauses else ""
    now = utc_now_iso()
    with _connect() as connection:
        rows = connection.execute(
            "SELECT status, COUNT(*) AS count, MIN(target_at) AS oldest_target_at, "
            "MAX(updated_at) AS latest_updated_at "
            "FROM investment_decision_outcome_targets" + where + " GROUP BY status",
            tuple(params),
        ).fetchall()
        due = connection.execute(
            "SELECT COUNT(*) AS count, MIN(target_at) AS oldest_target_at "
            "FROM investment_decision_outcome_targets WHERE status = 'pending' "
            "AND target_at <= %s" + due_clause,
            tuple([now] + params),
        ).fetchone()
        latest = connection.execute(
            "SELECT MAX(observed_at) AS latest_observed_at FROM investment_decision_outcomes"
            + where,
            tuple(params),
        ).fetchone()
        shadow_rows = connection.execute(
            "SELECT status, COUNT(*) AS count, MIN(target_at) AS oldest_target_at, "
            "MAX(updated_at) AS latest_updated_at "
            "FROM investment_hypothesis_observation_targets" + where + " GROUP BY status",
            tuple(params),
        ).fetchall()
        shadow_due = connection.execute(
            "SELECT COUNT(*) AS count, MIN(target_at) AS oldest_target_at "
            "FROM investment_hypothesis_observation_targets WHERE status = 'pending' "
            "AND target_at <= %s" + due_clause,
            tuple([now] + params),
        ).fetchone()
        shadow_latest = connection.execute(
            "SELECT MAX(observed_at) AS latest_observed_at "
            "FROM investment_hypothesis_observation_outcomes" + where,
            tuple(params),
        ).fetchone()
    decision_states = {
        str(row.get("status") or "unknown"): {
            "count": int(row.get("count") or 0),
            "oldestTargetAt": str(row.get("oldest_target_at") or ""),
            "latestUpdatedAt": str(row.get("latest_updated_at") or ""),
        }
        for row in rows or []
    }
    shadow_states = {
        str(row.get("status") or "unknown"): {
            "count": int(row.get("count") or 0),
            "oldestTargetAt": str(row.get("oldest_target_at") or ""),
            "latestUpdatedAt": str(row.get("latest_updated_at") or ""),
        }
        for row in shadow_rows or []
    }
    states = {}
    for state in sorted(set(decision_states) | set(shadow_states)):
        decision_state = decision_states.get(state) or {}
        shadow_state = shadow_states.get(state) or {}
        states[state] = {
            "count": int(decision_state.get("count") or 0) + int(shadow_state.get("count") or 0),
            "oldestTargetAt": min(
                [
                    value
                    for value in (
                        str(decision_state.get("oldestTargetAt") or ""),
                        str(shadow_state.get("oldestTargetAt") or ""),
                    )
                    if value
                ],
                default="",
            ),
            "latestUpdatedAt": max(
                str(decision_state.get("latestUpdatedAt") or ""),
                str(shadow_state.get("latestUpdatedAt") or ""),
            ),
        }
    due_count = int((due or {}).get("count") or 0) + int((shadow_due or {}).get("count") or 0)
    oldest_due = min(
        [
            value
            for value in (
                str((due or {}).get("oldest_target_at") or ""),
                str((shadow_due or {}).get("oldest_target_at") or ""),
            )
            if value
        ],
        default="",
    )
    latest_observed = max(
        str((latest or {}).get("latest_observed_at") or ""),
        str((shadow_latest or {}).get("latest_observed_at") or ""),
    )
    return {
        "status": "warning" if due_count or (states.get("needs-data") or {}).get("count") else "ok",
        "checkedAt": now,
        "accountId": str(account_id or ""),
        "symbol": str(symbol or "").upper(),
        "pendingTargetCount": int((states.get("pending") or {}).get("count") or 0),
        "dueTargetCount": due_count,
        "oldestDueTargetAt": oldest_due,
        "observedTargetCount": int((states.get("observed") or {}).get("count") or 0),
        "dataGapTargetCount": int((states.get("needs-data") or {}).get("count") or 0),
        "excludedTargetCount": int((states.get("excluded") or {}).get("count") or 0),
        "latestOutcomeObservedAt": latest_observed,
        "states": states,
        "decisionTargetCount": sum(
            int(item.get("count") or 0) for item in decision_states.values()
        ),
        "shadowHypothesisTargetCount": sum(
            int(item.get("count") or 0) for item in shadow_states.values()
        ),
        "shadowDueTargetCount": int((shadow_due or {}).get("count") or 0),
        "contract": "durable-decision-and-shadow-hypothesis-outcome-target-v3",
    }


@dataclass(frozen=True)
class PendingTargetRead:
    account_id: str
    observed_at: str
    limit: int
    include_future: bool = False


def pending_outcome_targets(
    request: PendingTargetRead, *, _connect: ConnectionFactory
) -> List[Dict[str, object]]:
    normalized_account_id = request.account_id
    observed_stamp = request.observed_at
    target_limit = request.limit
    cutoff = canonical_investment_timestamp((datetime.fromisoformat(observed_stamp.replace("Z", "+00:00")) - timedelta(minutes=15)).isoformat())
    time_clause = "" if request.include_future else " AND targets.target_at <= %s"
    state_clause = """ AND (targets.status = 'pending' OR (
        targets.status IN ('needs-data', 'observed') AND targets.updated_at <= %s
        AND JSON_UNQUOTE(JSON_EXTRACT(outcomes.payload_json, '$.payload.calibrationEligibility'))
          IN ('excluded-contract-data-gap', 'excluded-criterion-data-gap')
    ))"""
    params = (normalized_account_id, cutoff, *(() if request.include_future else (observed_stamp,)), target_limit)
    with _connect() as connection:
        decision_rows = connection.execute(
            """
                SELECT targets.payload_json, outcomes.payload_json AS outcome_json,
                       episodes.payload_json AS episode_json
                FROM investment_decision_outcome_targets AS targets
                JOIN investment_decision_episodes AS episodes
                  ON episodes.episode_id = targets.episode_id
                LEFT JOIN investment_decision_outcomes AS outcomes ON outcomes.outcome_id = targets.outcome_id
                WHERE targets.account_id = %s
                """ + state_clause + time_clause + """
                ORDER BY targets.target_at ASC, targets.target_id ASC
                LIMIT %s
                """,
            params,
        ).fetchall()
        shadow_rows = connection.execute(
            """
                SELECT targets.payload_json, outcomes.payload_json AS outcome_json
                FROM investment_hypothesis_observation_targets AS targets
                LEFT JOIN investment_hypothesis_observation_outcomes AS outcomes ON outcomes.outcome_id = targets.outcome_id
                WHERE targets.account_id = %s
                """ + state_clause + time_clause + """
                ORDER BY targets.target_at ASC, targets.target_id ASC
                LIMIT %s
                """,
            params,
        ).fetchall()
    targets: List[Dict[str, object]] = []
    for row in decision_rows or []:
        payload = _json_loads(row.get("payload_json"), {})
        if payload:
            previous_outcome = _json_loads(row.get("outcome_json"), {})
            if previous_outcome:
                payload["previousOutcome"] = previous_outcome
            # Older pending decision targets predate the explicit baseline
            # flag. They still need the quote at decidedAt because V2
            # decision episodes intentionally keep large market facts out
            # of their compact immutable payload.
            payload.setdefault("episodeKind", "decision")
            payload.setdefault("requiresInstrumentBaseline", True)
            episode_payload = _json_loads(row.get("episode_json"), {})
            facts = (
                episode_payload.get("factsAtDecision")
                if isinstance(episode_payload.get("factsAtDecision"), dict)
                else {}
            )
            fact_delta = facts.get("factDelta") if isinstance(facts.get("factDelta"), dict) else {}
            baseline_at = canonical_investment_timestamp(
                fact_delta.get("source_observed_at")
                or fact_delta.get("sourceObservedAt")
                or facts.get("sourceAsOf")
            )
            if baseline_at:
                payload.setdefault("baselineAt", baseline_at)
            decision_price = number(facts.get("currentPrice"))
            if decision_price > 0:
                payload.setdefault("decisionPrice", decision_price)
                if facts.get("sourceAsOf"):
                    payload.setdefault(
                        "decisionPriceSourceAsOf",
                        facts.get("sourceAsOf"),
                    )
            targets.append(payload)
    for row in shadow_rows or []:
        payload = _json_loads(row.get("payload_json"), {})
        if payload:
            previous_outcome = _json_loads(row.get("outcome_json"), {})
            if previous_outcome:
                payload["previousOutcome"] = previous_outcome
            payload.setdefault("episodeKind", "shadow-hypothesis")
            payload.setdefault("requiresInstrumentBaseline", True)
            targets.append(payload)
    return sorted(
        targets,
        key=lambda item: (
            str(item.get("targetAt") or ""),
            str(item.get("requestId") or ""),
        ),
    )[:target_limit]
