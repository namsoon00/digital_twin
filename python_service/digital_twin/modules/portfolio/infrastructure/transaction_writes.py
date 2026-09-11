"""Connection-bound writes; the caller owns commit, rollback and ordering."""

from __future__ import annotations
from digital_twin.infrastructure.transaction_port import BoundWriteConnection
from typing import Any
from digital_twin.modules.portfolio.domain.portfolio_analytics import PortfolioRiskSnapshot
from digital_twin.modules.portfolio.domain.trade_execution import ExecutionEpisode
from digital_twin.infrastructure.operational_common import json_dumps


def append_ledger_entries(
    connection: BoundWriteConnection,
    entries: Any,
    stamp: str,
):
    inserted = 0
    for entry in entries or []:
        cursor = connection.execute(
            """
                INSERT IGNORE INTO portfolio_ledger_entries (
                    entry_id, idempotency_key, portfolio_id, account_id, entry_type,
                    symbol, currency, quantity, unit_price, amount, fee, occurred_at,
                    source_reference, payload_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
            (
                entry.entry_id,
                entry.source_reference or entry.entry_id,
                entry.portfolio_id,
                entry.account_id,
                entry.entry_type,
                entry.symbol,
                entry.currency,
                str(entry.quantity),
                str(entry.unit_price),
                str(entry.amount),
                str(entry.fee),
                entry.occurred_at,
                entry.source_reference,
                json_dumps(entry.to_dict()),
                stamp,
            ),
        )
        inserted += max(0, int(cursor.rowcount or 0))
    return inserted


def insert_activity_episode(
    connection: BoundWriteConnection,
    activity_episode: Any,
    payload: Any,
    stamp: Any,
):
    return connection.execute(
        "INSERT IGNORE INTO portfolio_activity_episodes "
        "(episode_id, portfolio_id, account_id, classification, confidence, observed_at, "
        "observation_fingerprint, payload_json, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            activity_episode.episode_id,
            activity_episode.portfolio_id,
            activity_episode.account_id,
            activity_episode.classification,
            activity_episode.confidence,
            activity_episode.observed_at,
            activity_episode.observation_fingerprint,
            json_dumps(payload),
            stamp,
        ),
    )


def insert_state_snapshot(
    connection: BoundWriteConnection,
    stamp: Any,
    state_payload: Any,
    state_snapshot: Any,
):
    return connection.execute(
        "INSERT IGNORE INTO portfolio_state_snapshots "
        "(state_id, portfolio_id, account_id, observed_at, source_checkpoint_version, position_count, payload_json, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            state_snapshot.state_id,
            state_snapshot.portfolio_id,
            state_snapshot.account_id,
            state_snapshot.observed_at,
            state_snapshot.source_checkpoint_version,
            state_snapshot.position_count,
            json_dumps(state_payload),
            stamp,
        ),
    )


def insert_observation_reconciliation(
    connection: BoundWriteConnection,
    reconciliation: Any,
    reconciliation_payload: Any,
    stamp: Any,
):
    return connection.execute(
        "INSERT IGNORE INTO portfolio_reconciliations "
        "(reconciliation_id, portfolio_id, account_id, balance_fingerprint, status, difference_count, "
        "source_snapshot_at, payload_json, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            reconciliation.reconciliation_id,
            reconciliation.portfolio_id,
            reconciliation.account_id,
            reconciliation.balance_fingerprint,
            reconciliation.status,
            int(reconciliation_payload.get("differenceCount") or 0),
            reconciliation.source_snapshot_at,
            json_dumps(reconciliation_payload),
            reconciliation.created_at or stamp,
            stamp,
        ),
    )


def insert_observation_exposure(
    connection: BoundWriteConnection,
    exposure: Any,
    stamp: Any,
):
    return connection.execute(
        "INSERT IGNORE INTO portfolio_exposure_snapshots "
        "(exposure_snapshot_id, portfolio_id, observed_at, over_policy_count, payload_json, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            exposure.snapshot_id,
            exposure.portfolio_id,
            exposure.observed_at,
            len(exposure.over_policy_metrics()),
            json_dumps(exposure.to_dict()),
            stamp,
        ),
    )


def upsert_observation_rebalance(
    connection: BoundWriteConnection,
    rebalance_proposal: Any,
    stamp: Any,
):
    return connection.execute(
        "INSERT INTO portfolio_rebalance_proposals "
        "(proposal_id, portfolio_id, mandate_version, exposure_snapshot_id, status, payload_json, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE status = VALUES(status), payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)",
        (
            rebalance_proposal.proposal_id,
            rebalance_proposal.portfolio_id,
            rebalance_proposal.mandate_version,
            rebalance_proposal.exposure_snapshot_id,
            rebalance_proposal.status,
            json_dumps(rebalance_proposal.to_dict()),
            rebalance_proposal.created_at or stamp,
            stamp,
        ),
    )


def upsert_observation_cycle(
    connection: BoundWriteConnection,
    cycle_payload: Any,
    decision_cycle: Any,
    stamp: Any,
):
    return connection.execute(
        "INSERT INTO portfolio_decision_cycles "
        "(cycle_id, portfolio_id, account_id, policy_version, source_snapshot_id, candidate_fingerprint, "
        "data_state, candidate_count, payload_json, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE candidate_fingerprint = VALUES(candidate_fingerprint), "
        "data_state = VALUES(data_state), candidate_count = VALUES(candidate_count), "
        "payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)",
        (
            decision_cycle.cycle_id,
            decision_cycle.portfolio_id,
            decision_cycle.account_id,
            decision_cycle.policy_version,
            decision_cycle.source_snapshot_id,
            decision_cycle.fingerprint,
            decision_cycle.data_state,
            len(decision_cycle.candidates),
            json_dumps(cycle_payload),
            decision_cycle.created_at or stamp,
            stamp,
        ),
    )


def insert_action_observation(
    connection: BoundWriteConnection,
    observation: Any,
    payload: Any,
    stamp: Any,
):
    return connection.execute(
        "INSERT IGNORE INTO portfolio_decision_action_observations "
        "(observation_id, portfolio_id, account_id, symbol, activity_episode_id, "
        "prior_decision_episode_id, correspondence, observed_at, payload_json, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            observation.observation_id,
            observation.portfolio_id,
            observation.account_id,
            observation.symbol,
            observation.activity_episode_id,
            observation.prior_decision_episode_id,
            observation.correspondence,
            observation.observed_at,
            json_dumps(payload),
            stamp,
        ),
    )


def advance_observation_checkpoint(
    connection: BoundWriteConnection,
    checkpoint: Any,
    checkpoint_payload: Any,
    next_version: Any,
    stamp: Any,
):
    return connection.execute(
        "INSERT INTO portfolio_snapshot_checkpoints "
        "(portfolio_id, account_id, account_fingerprint, observed_at, balance_fingerprint, checkpoint_version, "
        "position_count, status, payload_json, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'accepted', %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE account_id = VALUES(account_id), account_fingerprint = VALUES(account_fingerprint), "
        "observed_at = VALUES(observed_at), balance_fingerprint = VALUES(balance_fingerprint), "
        "checkpoint_version = VALUES(checkpoint_version), position_count = VALUES(position_count), "
        "status = 'accepted', payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)",
        (
            checkpoint.portfolio_id,
            checkpoint.account_id,
            checkpoint.account_fingerprint,
            checkpoint.observed_at,
            checkpoint.balance_fingerprint,
            next_version,
            checkpoint.position_count,
            json_dumps(checkpoint_payload),
            stamp,
            stamp,
        ),
    )


def save_risk_snapshot(
    connection: BoundWriteConnection,
    snapshot: PortfolioRiskSnapshot,
    stamp: str,
):
    connection.execute(
        """
            INSERT INTO portfolio_risk_snapshots (
                risk_snapshot_id, portfolio_id, observed_at, data_state, sample_count,
                annualized_volatility_pct, maximum_drawdown_pct,
                maximum_pairwise_correlation, payload_json, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE observed_at = VALUES(observed_at),
                data_state = VALUES(data_state), sample_count = VALUES(sample_count),
                annualized_volatility_pct = VALUES(annualized_volatility_pct),
                maximum_drawdown_pct = VALUES(maximum_drawdown_pct),
                maximum_pairwise_correlation = VALUES(maximum_pairwise_correlation),
                payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
            """,
        (
            snapshot.risk_snapshot_id,
            snapshot.portfolio_id,
            snapshot.observed_at,
            snapshot.data_state,
            snapshot.sample_count,
            snapshot.annualized_volatility_pct,
            snapshot.maximum_drawdown_pct,
            snapshot.maximum_pairwise_correlation,
            json_dumps(snapshot.to_dict()),
            stamp,
            stamp,
        ),
    )


def save_execution_episode(
    connection: BoundWriteConnection,
    episode: ExecutionEpisode,
    stamp: str,
):
    connection.execute(
        """
                INSERT INTO trade_execution_episodes (
                    execution_episode_id, action_plan_id, portfolio_id, status,
                    payload_json, started_at, completed_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE status = VALUES(status),
                    payload_json = VALUES(payload_json), completed_at = VALUES(completed_at),
                    updated_at = VALUES(updated_at)
                """,
        (
            episode.execution_episode_id,
            episode.action_plan_id,
            episode.portfolio_id,
            episode.status,
            json_dumps(episode.to_dict()),
            episode.started_at,
            episode.completed_at,
            stamp,
            stamp,
        ),
    )
    for fill in episode.fills:
        connection.execute(
            """
                    INSERT IGNORE INTO trade_execution_fills (
                        fill_id, provider_execution_id, execution_episode_id, order_intent_id,
                        symbol, side, quantity, price, fee, currency, executed_at,
                        payload_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
            (
                fill.fill_id,
                fill.provider_execution_id,
                episode.execution_episode_id,
                fill.order_intent_id,
                fill.symbol,
                fill.side,
                fill.quantity,
                fill.price,
                fill.fee,
                fill.currency,
                fill.executed_at,
                json_dumps(fill.to_dict()),
                stamp,
            ),
        )


def upsert_decision_action_plan(
    connection: BoundWriteConnection,
    plan: Any,
    stamp: Any,
):
    return connection.execute(
        """
                    INSERT INTO investment_action_plans (
                        plan_id, portfolio_id, decision_episode_id, policy_version,
                        inference_generation_id, action, status, payload_json, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE status = VALUES(status),
                        payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                    """,
        (
            plan.plan_id,
            plan.portfolio_id,
            plan.decision_episode_id,
            plan.policy_version,
            plan.inference_generation_id,
            plan.action,
            plan.status,
            json_dumps(plan.to_dict()),
            plan.created_at or stamp,
            stamp,
        ),
    )
