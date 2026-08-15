"""MySQL adapters for portfolio policy, ledger, execution, and review domains."""

from decimal import Decimal
import hashlib
from typing import Dict, Iterable, List, Optional

from ..domain.investment_mandate import InvestmentMandate
from ..domain.investment_outcomes import DecisionReview, PerformanceAttribution, decision_quality_summary
from ..domain.portfolio_activity_episode import (
    DecisionActionObservation,
    PortfolioActivityEpisode,
    PortfolioSnapshotCheckpoint,
    PortfolioStateSnapshot,
)
from ..domain.portfolio_decision_cycle import PortfolioDecisionCycle
from ..domain.portfolio_ledger import (
    INFERRED_SNAPSHOT_ENTRY_TYPES,
    PortfolioLedgerEntry,
    PortfolioReconciliation,
    execution_ledger_entries,
)
from ..domain.portfolio_analytics import PortfolioRiskSnapshot
from ..domain.events import PORTFOLIO_RISK_OBSERVED
from ..domain.snapshot_portfolio_activity import activity_payload
from ..domain.portfolio_rebalancing import (
    RebalanceProposal,
    RebalanceState,
    RebalanceTransition,
    rebalance_transition,
)
from ..domain.risk_exposure import ExposureSnapshot
from ..domain.trade_execution import ActionPlan, ActionPlanReview, ExecutionEpisode
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_events import insert_domain_event_with_connection
from .mysql_operational_helpers import _json_loads
from .mysql_notification_jobs import MySQLNotificationJobStore
from .operational_common import json_dumps
from .settings import utc_now


def ledger_entry_from_payload(payload: Dict[str, object]) -> PortfolioLedgerEntry:
    values = dict(payload or {})
    portfolio_id = values.pop("portfolio_id", "")
    account_id = values.pop("account_id", "")
    entry_type = values.pop("entry_type", "")
    occurred_at = values.pop("occurred_at", "")
    return PortfolioLedgerEntry.create(portfolio_id, account_id, entry_type, occurred_at, **values)


def save_mandate_with_connection(connection, mandate: InvestmentMandate, stamp: str = "") -> None:
    stamp = str(stamp or utc_now())
    version_record_id = "mandate-version:" + hashlib.sha256(
        (mandate.mandate_id + "|" + mandate.policy_version).encode("utf-8")
    ).hexdigest()[:32]
    connection.execute(
        """
        INSERT IGNORE INTO investment_mandate_versions (
            mandate_version_id, mandate_id, portfolio_id, account_id,
            policy_version, profile, effective_at, payload_json, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            version_record_id,
            mandate.mandate_id,
            mandate.portfolio_id,
            mandate.account_id,
            mandate.policy_version,
            mandate.profile,
            mandate.effective_at,
            json_dumps(mandate.to_dict()),
            stamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO investment_mandates (
            mandate_id, portfolio_id, account_id, policy_version, profile,
            status, effective_at, payload_json, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, 'active', %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE policy_version = VALUES(policy_version),
            profile = VALUES(profile), status = 'active',
            effective_at = VALUES(effective_at), payload_json = VALUES(payload_json),
            updated_at = VALUES(updated_at)
        """,
        (
            mandate.mandate_id,
            mandate.portfolio_id,
            mandate.account_id,
            mandate.policy_version,
            mandate.profile,
            mandate.effective_at,
            json_dumps(mandate.to_dict()),
            stamp,
            stamp,
        ),
    )


class MySQLInvestmentDomainStore(MySQLOperationalConnection):
    def record_rebalance_review_window(
        self,
        portfolio_id: str,
        review_window: str,
        observed_at: str,
        domain_event,
        reasoning_event,
    ) -> bool:
        """Record one scheduled review and its mailbox ingress atomically."""
        clean_portfolio_id = str(portfolio_id or "").strip()[:191]
        clean_window = str(review_window or "").strip()[:80]
        if not clean_portfolio_id or not clean_window or not domain_event or not reasoning_event:
            return False
        stamp = utc_now()

        def operation(connection):
            cursor = connection.execute(
                """
                INSERT IGNORE INTO portfolio_rebalance_review_windows (
                    portfolio_id, review_window, observed_at, source_event_id,
                    reasoning_event_id, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    clean_portfolio_id,
                    clean_window,
                    str(observed_at or "")[:40],
                    str(domain_event.event_id or "")[:191],
                    str(reasoning_event.event_id or "")[:191],
                    stamp,
                ),
            )
            inserted = max(0, int(getattr(cursor, "rowcount", 0) or 0)) > 0
            if not inserted:
                return False
            insert_domain_event_with_connection(connection, domain_event)
            insert_domain_event_with_connection(connection, reasoning_event)
            from .mysql_reasoning_mailbox import MySQLOntologyReasoningMailboxStore

            MySQLOntologyReasoningMailboxStore.ingress_event_with_connection(
                connection,
                reasoning_event,
            )
            return True

        return bool(self.transaction_with_deadlock_retry("rebalance-review-window", operation))

    def record_rebalance_state_with_connection(
        self,
        connection,
        current_state: Optional[RebalanceState],
        transition: Optional[RebalanceTransition],
        domain_event=None,
        reasoning_event=None,
        stamp: str = "",
    ) -> bool:
        """CAS the emitted baseline and its mailbox ingress under one row lock."""
        if not current_state:
            return False
        stamp = str(stamp or utc_now())
        row = connection.execute(
            "SELECT revision, event_payload_json FROM portfolio_rebalance_states "
            "WHERE portfolio_id = %s FOR UPDATE",
            (current_state.portfolio_id,),
        ).fetchone()
        previous_payload = _json_loads((row or {}).get("event_payload_json"), {})
        previous = RebalanceState.from_dict(previous_payload) if previous_payload else None
        verified = rebalance_transition(previous, current_state)
        recorded_revision = str((row or {}).get("revision") or "")
        accepted = bool(
            transition
            and verified
            and domain_event
            and reasoning_event
            and transition.revision == verified.revision
            and transition.revision != recorded_revision
        )
        current_payload = json_dumps(current_state.to_dict())
        if row:
            if accepted:
                connection.execute(
                    "UPDATE portfolio_rebalance_states SET policy_version = %s, status = %s, "
                    "semantic_fingerprint = %s, revision = %s, transition_type = %s, observed_at = %s, "
                    "current_payload_json = %s, event_payload_json = %s, updated_at = %s WHERE portfolio_id = %s",
                    (
                        current_state.policy_version, current_state.status, current_state.semantic_fingerprint,
                        transition.revision, transition.transition_type, current_state.observed_at,
                        current_payload, current_payload, stamp, current_state.portfolio_id,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE portfolio_rebalance_states SET policy_version = %s, status = %s, "
                    "semantic_fingerprint = %s, observed_at = %s, current_payload_json = %s, "
                    "updated_at = %s WHERE portfolio_id = %s",
                    (
                        current_state.policy_version, current_state.status, current_state.semantic_fingerprint,
                        current_state.observed_at, current_payload, stamp, current_state.portfolio_id,
                    ),
                )
        else:
            connection.execute(
                "INSERT INTO portfolio_rebalance_states "
                "(portfolio_id, policy_version, status, semantic_fingerprint, revision, transition_type, "
                "observed_at, current_payload_json, event_payload_json, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    current_state.portfolio_id, current_state.policy_version, current_state.status,
                    current_state.semantic_fingerprint, transition.revision if accepted else "",
                    transition.transition_type if accepted else "", current_state.observed_at,
                    current_payload, current_payload if accepted else None, stamp, stamp,
                ),
            )
        if accepted and domain_event:
            insert_domain_event_with_connection(connection, domain_event)
        if accepted and reasoning_event:
            insert_domain_event_with_connection(connection, reasoning_event)
            from .mysql_reasoning_mailbox import MySQLOntologyReasoningMailboxStore

            MySQLOntologyReasoningMailboxStore.ingress_event_with_connection(connection, reasoning_event)
        return accepted

    def lifecycle_trace(self, decision_episode_id: str) -> Dict[str, object]:
        episode_id = str(decision_episode_id or "").strip()
        if not episode_id:
            return {"status": "unavailable", "decisionEpisodeId": ""}
        with self.connect() as connection:
            decision = connection.execute(
                "SELECT payload_json FROM investment_decision_episodes WHERE episode_id = %s",
                (episode_id,),
            ).fetchone()
            plans = connection.execute(
                "SELECT plan_id, payload_json FROM investment_action_plans "
                "WHERE decision_episode_id = %s ORDER BY created_at ASC, plan_id ASC",
                (episode_id,),
            ).fetchall()
            plan_ids = [str(item.get("plan_id") or "") for item in plans or [] if str(item.get("plan_id") or "")]
            executions = []
            fills = []
            if plan_ids:
                placeholders = ",".join(["%s"] * len(plan_ids))
                executions = connection.execute(
                    "SELECT execution_episode_id, payload_json FROM trade_execution_episodes "
                    "WHERE action_plan_id IN (" + placeholders + ") "
                    "ORDER BY created_at ASC, execution_episode_id ASC",
                    tuple(plan_ids),
                ).fetchall()
                execution_ids = [
                    str(item.get("execution_episode_id") or "")
                    for item in executions or []
                    if str(item.get("execution_episode_id") or "")
                ]
                if execution_ids:
                    execution_placeholders = ",".join(["%s"] * len(execution_ids))
                    fills = connection.execute(
                        "SELECT payload_json FROM trade_execution_fills "
                        "WHERE execution_episode_id IN (" + execution_placeholders + ") "
                        "ORDER BY executed_at ASC, fill_id ASC",
                        tuple(execution_ids),
                    ).fetchall()
            reviews = connection.execute(
                "SELECT payload_json FROM investment_decision_reviews "
                "WHERE decision_episode_id = %s ORDER BY reviewed_at ASC, review_id ASC",
                (episode_id,),
            ).fetchall()
            attributions = connection.execute(
                "SELECT payload_json FROM investment_performance_attributions "
                "WHERE decision_episode_id = %s ORDER BY observed_at ASC, attribution_id ASC",
                (episode_id,),
            ).fetchall()
            plan_reviews = []
            if plan_ids:
                placeholders = ",".join(["%s"] * len(plan_ids))
                plan_reviews = connection.execute(
                    "SELECT payload_json FROM investment_action_plan_reviews "
                    "WHERE plan_id IN (" + placeholders + ") "
                    "ORDER BY reviewed_at ASC, review_id ASC",
                    tuple(plan_ids),
                ).fetchall()
        return {
            "status": "ready" if decision else "unavailable",
            "decisionEpisodeId": episode_id,
            "decisionEpisode": _json_loads(decision.get("payload_json"), {}) if decision else {},
            "actionPlans": [_json_loads(item.get("payload_json"), {}) for item in plans or []],
            "actionPlanReviews": [_json_loads(item.get("payload_json"), {}) for item in plan_reviews or []],
            "executionEpisodes": [_json_loads(item.get("payload_json"), {}) for item in executions or []],
            "fills": [_json_loads(item.get("payload_json"), {}) for item in fills or []],
            "decisionReviews": [_json_loads(item.get("payload_json"), {}) for item in reviews or []],
            "performanceAttributions": [_json_loads(item.get("payload_json"), {}) for item in attributions or []],
        }

    def save_mandate(self, mandate: InvestmentMandate) -> InvestmentMandate:
        stamp = utc_now()
        with self.transaction() as connection:
            self.save_mandate_with_connection(connection, mandate, stamp)
        return mandate

    def save_mandate_with_connection(self, connection, mandate: InvestmentMandate, stamp: str = "") -> None:
        save_mandate_with_connection(connection, mandate, stamp)

    def active_mandate(self, portfolio_id: str) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM investment_mandates "
                "WHERE portfolio_id = %s AND status = 'active' "
                "ORDER BY updated_at DESC, mandate_id DESC LIMIT 1",
                (str(portfolio_id or ""),),
            ).fetchone()
        return _json_loads(row.get("payload_json"), {}) if row else {}

    def mandate_history(self, portfolio_id: str, limit: int = 100) -> List[Dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM investment_mandate_versions "
                "WHERE portfolio_id = %s ORDER BY created_at DESC, mandate_version_id DESC LIMIT %s",
                (str(portfolio_id or ""), max(1, min(1000, int(limit or 100)))),
            ).fetchall()
        return [_json_loads(row.get("payload_json"), {}) for row in rows or []]

    def append_ledger_entries(self, entries: Iterable[PortfolioLedgerEntry]) -> int:
        rows = list(entries or [])
        if not rows:
            return 0
        stamp = utc_now()
        inserted = 0
        with self.transaction() as connection:
            inserted = self.append_ledger_entries_with_connection(connection, rows, stamp)
        return inserted

    def append_ledger_entries_with_connection(self, connection, entries, stamp: str) -> int:
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

    def snapshot_checkpoint(self, portfolio_id: str) -> Optional[PortfolioSnapshotCheckpoint]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json, checkpoint_version FROM portfolio_snapshot_checkpoints WHERE portfolio_id = %s",
                (str(portfolio_id or ""),),
            ).fetchone()
        if not row:
            return None
        payload = _json_loads(row.get("payload_json"), {})
        payload["checkpointVersion"] = int(row.get("checkpoint_version") or payload.get("checkpointVersion") or 0)
        return PortfolioSnapshotCheckpoint.from_dict(payload)

    def latest_decision_before(self, account_id: str, symbol: str, observed_at: str) -> Dict[str, object]:
        if not str(account_id or "").strip() or not str(symbol or "").strip():
            return {}
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM investment_decision_episodes "
                "WHERE account_id = %s AND symbol = %s AND decided_at < %s "
                "ORDER BY decided_at DESC, episode_id DESC LIMIT 1",
                (str(account_id), str(symbol).upper(), str(observed_at or "")),
            ).fetchone()
        return _json_loads(row.get("payload_json"), {}) if row else {}

    def record_snapshot_quarantine(
        self,
        checkpoint: PortfolioSnapshotCheckpoint,
        reason: str,
        previous_checkpoint: Optional[PortfolioSnapshotCheckpoint] = None,
    ) -> Dict[str, object]:
        quarantine_id = "snapshot-quarantine:" + hashlib.sha256(
            "|".join([
                checkpoint.portfolio_id,
                checkpoint.balance_fingerprint,
                checkpoint.observed_at,
                str(reason or ""),
            ]).encode("utf-8")
        ).hexdigest()[:24]
        payload = {
            **checkpoint.to_dict(),
            "quarantineId": quarantine_id,
            "reason": str(reason or ""),
            "previousCheckpoint": previous_checkpoint.to_dict() if previous_checkpoint else {},
        }
        stamp = utc_now()
        with self.connect() as connection:
            connection.execute(
                "INSERT IGNORE INTO portfolio_snapshot_quarantines "
                "(quarantine_id, portfolio_id, account_id, reason, observed_at, balance_fingerprint, payload_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    quarantine_id,
                    checkpoint.portfolio_id,
                    checkpoint.account_id,
                    str(reason or ""),
                    checkpoint.observed_at,
                    checkpoint.balance_fingerprint,
                    json_dumps(payload),
                    stamp,
                ),
            )
        return payload

    def advance_snapshot_checkpoint(
        self,
        expected_checkpoint_version: int,
        checkpoint: PortfolioSnapshotCheckpoint,
    ) -> Dict[str, object]:
        expected = max(0, int(expected_checkpoint_version or 0))

        def commit(connection):
            current = connection.execute(
                "SELECT checkpoint_version, observed_at, balance_fingerprint "
                "FROM portfolio_snapshot_checkpoints WHERE portfolio_id = %s FOR UPDATE",
                (checkpoint.portfolio_id,),
            ).fetchone()
            actual = int(current.get("checkpoint_version") or 0) if current else 0
            if actual != expected:
                return {"status": "checkpoint-conflict", "actualCheckpointVersion": actual}
            if current and str(current.get("observed_at") or "") >= checkpoint.observed_at:
                same = str(current.get("balance_fingerprint") or "") == checkpoint.balance_fingerprint
                return {"status": "duplicate" if same else "stale", "actualCheckpointVersion": actual}
            next_version = expected + 1
            stamp = utc_now()
            payload = {**checkpoint.to_dict(), "checkpointVersion": next_version}
            connection.execute(
                "UPDATE portfolio_snapshot_checkpoints SET observed_at = %s, balance_fingerprint = %s, "
                "checkpoint_version = %s, position_count = %s, status = 'accepted', payload_json = %s, updated_at = %s "
                "WHERE portfolio_id = %s AND checkpoint_version = %s",
                (
                    checkpoint.observed_at, checkpoint.balance_fingerprint, next_version,
                    checkpoint.position_count, json_dumps(payload), stamp,
                    checkpoint.portfolio_id, expected,
                ),
            )
            return {"status": "unchanged", "actualCheckpointVersion": next_version}

        return self.transaction_with_deadlock_retry("portfolio-snapshot-checkpoint-advance", commit)

    def commit_snapshot_observation(
        self,
        expected_checkpoint_version: int,
        checkpoint: PortfolioSnapshotCheckpoint,
        ledger_entries: Iterable[PortfolioLedgerEntry],
        activity_episode: Optional[PortfolioActivityEpisode],
        state_snapshot: PortfolioStateSnapshot,
        reconciliation: PortfolioReconciliation,
        exposure: ExposureSnapshot,
        rebalance_proposal: Optional[RebalanceProposal],
        decision_cycle: PortfolioDecisionCycle,
        decision_action_observations: Iterable[DecisionActionObservation] = None,
        domain_event=None,
        notification_job=None,
        reasoning_event=None,
        risk_snapshot: Optional[PortfolioRiskSnapshot] = None,
        rebalance_state: Optional[RebalanceState] = None,
        rebalance_transition: Optional[RebalanceTransition] = None,
        rebalance_event=None,
        rebalance_reasoning_event=None,
    ) -> Dict[str, object]:
        """CAS one complete account observation and its durable side effects."""
        rows = list(ledger_entries or [])
        action_rows = list(decision_action_observations or [])
        expected = max(0, int(expected_checkpoint_version or 0))
        notification_store = MySQLNotificationJobStore(self.runtime_settings) if notification_job else None

        def commit(connection):
            current = connection.execute(
                "SELECT checkpoint_version, observed_at, balance_fingerprint "
                "FROM portfolio_snapshot_checkpoints WHERE portfolio_id = %s FOR UPDATE",
                (checkpoint.portfolio_id,),
            ).fetchone()
            current_version = int(current.get("checkpoint_version") or 0) if current else 0
            if current_version != expected:
                return {
                    "status": "checkpoint-conflict",
                    "expectedCheckpointVersion": expected,
                    "actualCheckpointVersion": current_version,
                    "insertedCount": 0,
                    "notificationQueued": False,
                }
            if current and str(current.get("observed_at") or "") >= checkpoint.observed_at:
                same = str(current.get("balance_fingerprint") or "") == checkpoint.balance_fingerprint
                return {
                    "status": "duplicate" if same else "stale",
                    "expectedCheckpointVersion": expected,
                    "actualCheckpointVersion": current_version,
                    "insertedCount": 0,
                    "notificationQueued": False,
                }
            stamp = utc_now()
            inserted = self.append_ledger_entries_with_connection(connection, rows, stamp)
            if activity_episode:
                payload = activity_episode.to_dict()
                connection.execute(
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
            state_payload = state_snapshot.to_dict()
            connection.execute(
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
            reconciliation_payload = reconciliation.to_dict()
            connection.execute(
                "INSERT IGNORE INTO portfolio_reconciliations "
                "(reconciliation_id, portfolio_id, account_id, balance_fingerprint, status, difference_count, "
                "source_snapshot_at, payload_json, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    reconciliation.reconciliation_id, reconciliation.portfolio_id, reconciliation.account_id,
                    reconciliation.balance_fingerprint, reconciliation.status,
                    int(reconciliation_payload.get("differenceCount") or 0), reconciliation.source_snapshot_at,
                    json_dumps(reconciliation_payload), reconciliation.created_at or stamp, stamp,
                ),
            )
            connection.execute(
                "INSERT IGNORE INTO portfolio_exposure_snapshots "
                "(exposure_snapshot_id, portfolio_id, observed_at, over_policy_count, payload_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (exposure.snapshot_id, exposure.portfolio_id, exposure.observed_at, len(exposure.over_policy_metrics()), json_dumps(exposure.to_dict()), stamp),
            )
            if risk_snapshot:
                self.save_risk_snapshot_with_connection(connection, risk_snapshot, stamp)
            if rebalance_proposal:
                connection.execute(
                    "INSERT INTO portfolio_rebalance_proposals "
                    "(proposal_id, portfolio_id, mandate_version, exposure_snapshot_id, status, payload_json, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE status = VALUES(status), payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)",
                    (
                        rebalance_proposal.proposal_id, rebalance_proposal.portfolio_id,
                        rebalance_proposal.mandate_version, rebalance_proposal.exposure_snapshot_id,
                        rebalance_proposal.status, json_dumps(rebalance_proposal.to_dict()),
                        rebalance_proposal.created_at or stamp, stamp,
                    ),
                )
            cycle_payload = decision_cycle.to_dict()
            connection.execute(
                "INSERT INTO portfolio_decision_cycles "
                "(cycle_id, portfolio_id, account_id, policy_version, source_snapshot_id, candidate_fingerprint, "
                "data_state, candidate_count, payload_json, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE candidate_fingerprint = VALUES(candidate_fingerprint), "
                "data_state = VALUES(data_state), candidate_count = VALUES(candidate_count), "
                "payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)",
                (
                    decision_cycle.cycle_id, decision_cycle.portfolio_id, decision_cycle.account_id,
                    decision_cycle.policy_version, decision_cycle.source_snapshot_id, decision_cycle.fingerprint,
                    decision_cycle.data_state, len(decision_cycle.candidates), json_dumps(cycle_payload),
                    decision_cycle.created_at or stamp, stamp,
                ),
            )
            for observation in action_rows:
                payload = observation.to_dict()
                connection.execute(
                    "INSERT IGNORE INTO portfolio_decision_action_observations "
                    "(observation_id, portfolio_id, account_id, symbol, activity_episode_id, "
                    "prior_decision_episode_id, correspondence, observed_at, payload_json, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        observation.observation_id, observation.portfolio_id, observation.account_id,
                        observation.symbol, observation.activity_episode_id, observation.prior_decision_episode_id,
                        observation.correspondence, observation.observed_at, json_dumps(payload), stamp,
                    ),
                )
            next_version = expected + 1
            checkpoint_payload = {**checkpoint.to_dict(), "checkpointVersion": next_version}
            connection.execute(
                "INSERT INTO portfolio_snapshot_checkpoints "
                "(portfolio_id, account_id, account_fingerprint, observed_at, balance_fingerprint, checkpoint_version, "
                "position_count, status, payload_json, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'accepted', %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE account_id = VALUES(account_id), account_fingerprint = VALUES(account_fingerprint), "
                "observed_at = VALUES(observed_at), balance_fingerprint = VALUES(balance_fingerprint), "
                "checkpoint_version = VALUES(checkpoint_version), position_count = VALUES(position_count), "
                "status = 'accepted', payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)",
                (
                    checkpoint.portfolio_id, checkpoint.account_id, checkpoint.account_fingerprint,
                    checkpoint.observed_at, checkpoint.balance_fingerprint, next_version,
                    checkpoint.position_count, json_dumps(checkpoint_payload), stamp, stamp,
                ),
            )
            if domain_event:
                insert_domain_event_with_connection(connection, domain_event)
            if reasoning_event:
                insert_domain_event_with_connection(connection, reasoning_event)
                from .mysql_reasoning_mailbox import MySQLOntologyReasoningMailboxStore

                MySQLOntologyReasoningMailboxStore.ingress_event_with_connection(connection, reasoning_event)
            rebalance_recorded = self.record_rebalance_state_with_connection(
                connection,
                rebalance_state,
                rebalance_transition,
                rebalance_event,
                rebalance_reasoning_event,
                stamp,
            )
            queued = bool(notification_store.enqueue_with_connection(connection, notification_job)) if notification_store and notification_job else False
            return {
                "status": "committed",
                "expectedCheckpointVersion": expected,
                "actualCheckpointVersion": next_version,
                "insertedCount": inserted,
                "notificationQueued": queued,
                "rebalanceTransitionRecorded": rebalance_recorded,
            }

        return self.transaction_with_deadlock_retry("portfolio-snapshot-observation", commit)

    def ledger_entries(self, portfolio_id: str, limit: int = 10000) -> List[PortfolioLedgerEntry]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM portfolio_ledger_entries WHERE portfolio_id = %s "
                "ORDER BY occurred_at ASC, entry_id ASC LIMIT %s",
                (str(portfolio_id or ""), max(1, min(100000, int(limit or 10000)))),
            ).fetchall()
        return [
            PortfolioLedgerEntry(
                entry_id=str(row.get("entry_id") or ""),
                portfolio_id=str(row.get("portfolio_id") or ""),
                account_id=str(row.get("account_id") or ""),
                entry_type=str(row.get("entry_type") or ""),
                occurred_at=str(row.get("occurred_at") or ""),
                source_reference=str(row.get("source_reference") or ""),
                symbol=str(row.get("symbol") or ""),
                currency=str(row.get("currency") or "KRW"),
                quantity=Decimal(str(row.get("quantity") or "0")),
                unit_price=Decimal(str(row.get("unit_price") or "0")),
                amount=Decimal(str(row.get("amount") or "0")),
                fee=Decimal(str(row.get("fee") or "0")),
                payload=dict(_json_loads(row.get("payload_json"), {}).get("payload") or {}),
            )
            for row in rows or []
        ]

    def save_reconciliation(self, reconciliation: PortfolioReconciliation) -> PortfolioReconciliation:
        stamp = utc_now()
        payload = reconciliation.to_dict()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT IGNORE INTO portfolio_reconciliations (
                    reconciliation_id, portfolio_id, account_id, balance_fingerprint,
                    status, difference_count, source_snapshot_at, payload_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    reconciliation.reconciliation_id,
                    reconciliation.portfolio_id,
                    reconciliation.account_id,
                    reconciliation.balance_fingerprint,
                    reconciliation.status,
                    int(payload.get("differenceCount") or 0),
                    reconciliation.source_snapshot_at,
                    json_dumps(payload),
                    reconciliation.created_at or stamp,
                    stamp,
                ),
            )
        return reconciliation

    def save_exposure_snapshot(self, snapshot: ExposureSnapshot) -> ExposureSnapshot:
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT IGNORE INTO portfolio_exposure_snapshots (
                    exposure_snapshot_id, portfolio_id, observed_at, over_policy_count,
                    payload_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.portfolio_id,
                    snapshot.observed_at,
                    len(snapshot.over_policy_metrics()),
                    json_dumps(snapshot.to_dict()),
                    stamp,
                ),
            )
        return snapshot

    def save_risk_snapshot_with_connection(
        self,
        connection,
        snapshot: PortfolioRiskSnapshot,
        stamp: str,
    ) -> None:
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

    def save_risk_snapshot(self, snapshot: PortfolioRiskSnapshot) -> PortfolioRiskSnapshot:
        stamp = utc_now()
        with self.transaction() as connection:
            self.save_risk_snapshot_with_connection(connection, snapshot, stamp)
        return snapshot

    def latest_portfolio_risk_event(self, portfolio_id: str) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM domain_events WHERE name = %s AND aggregate_id = %s "
                "ORDER BY occurred_at DESC, event_id DESC LIMIT 1",
                (PORTFOLIO_RISK_OBSERVED, str(portfolio_id or "")),
            ).fetchone()
        return _json_loads(row.get("payload_json"), {}) if row else {}

    def latest_rebalance_state(self, portfolio_id: str) -> Dict[str, object]:
        """Return the last event-worthy baseline, not every sampled state."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT event_payload_json, revision, transition_type FROM portfolio_rebalance_states "
                "WHERE portfolio_id = %s LIMIT 1",
                (str(portfolio_id or ""),),
            ).fetchone()
        payload = _json_loads((row or {}).get("event_payload_json"), {})
        if payload:
            payload["revision"] = str((row or {}).get("revision") or "")
            payload["lastTransitionType"] = str((row or {}).get("transition_type") or "")
        return payload

    def latest_rebalance_current_state(self, portfolio_id: str) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT current_payload_json FROM portfolio_rebalance_states WHERE portfolio_id = %s LIMIT 1",
                (str(portfolio_id or ""),),
            ).fetchone()
        return _json_loads((row or {}).get("current_payload_json"), {})

    def save_portfolio_analysis_bundle(
        self,
        risk_snapshot: PortfolioRiskSnapshot,
        exposure: ExposureSnapshot,
        rebalance_proposal: Optional[RebalanceProposal],
        decision_cycle: PortfolioDecisionCycle,
        domain_event=None,
        reasoning_event=None,
        rebalance_state: Optional[RebalanceState] = None,
        rebalance_transition: Optional[RebalanceTransition] = None,
        rebalance_event=None,
        rebalance_reasoning_event=None,
    ) -> Dict[str, object]:
        """Persist one derived analysis bundle without holding a source lock."""
        stamp = utc_now()
        with self.transaction() as connection:
            previous_risk = connection.execute(
                "SELECT risk_snapshot_id FROM portfolio_risk_snapshots WHERE portfolio_id = %s "
                "ORDER BY observed_at DESC, risk_snapshot_id DESC LIMIT 1",
                (risk_snapshot.portfolio_id,),
            ).fetchone()
            risk_changed = str((previous_risk or {}).get("risk_snapshot_id") or "") != risk_snapshot.risk_snapshot_id
            connection.execute(
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
            self.save_risk_snapshot_with_connection(connection, risk_snapshot, stamp)
            if rebalance_proposal:
                connection.execute(
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
            cycle_payload = decision_cycle.to_dict()
            connection.execute(
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
            if risk_changed and domain_event:
                insert_domain_event_with_connection(connection, domain_event)
            if risk_changed and reasoning_event:
                insert_domain_event_with_connection(connection, reasoning_event)
                from .mysql_reasoning_mailbox import MySQLOntologyReasoningMailboxStore

                MySQLOntologyReasoningMailboxStore.ingress_event_with_connection(connection, reasoning_event)
            rebalance_recorded = self.record_rebalance_state_with_connection(
                connection,
                rebalance_state,
                rebalance_transition,
                rebalance_event,
                rebalance_reasoning_event,
                stamp,
            )
        return {
            "status": "saved",
            "riskChanged": risk_changed,
            "riskSnapshotId": risk_snapshot.risk_snapshot_id,
            "exposureSnapshotId": exposure.snapshot_id,
            "rebalanceProposalId": rebalance_proposal.proposal_id if rebalance_proposal else "",
            "decisionCycleId": decision_cycle.cycle_id,
            "rebalanceTransitionRecorded": rebalance_recorded,
        }

    def save_portfolio_decision_cycle(self, cycle: PortfolioDecisionCycle) -> PortfolioDecisionCycle:
        stamp = utc_now()
        payload = cycle.to_dict()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO portfolio_decision_cycles (
                    cycle_id, portfolio_id, account_id, policy_version, source_snapshot_id,
                    candidate_fingerprint, data_state, candidate_count, payload_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE candidate_fingerprint = VALUES(candidate_fingerprint),
                    data_state = VALUES(data_state), candidate_count = VALUES(candidate_count),
                    payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
                (
                    cycle.cycle_id,
                    cycle.portfolio_id,
                    cycle.account_id,
                    cycle.policy_version,
                    cycle.source_snapshot_id,
                    cycle.fingerprint,
                    cycle.data_state,
                    len(cycle.candidates),
                    json_dumps(payload),
                    cycle.created_at or stamp,
                    stamp,
                ),
            )
        return cycle

    def decision_continuity_context(
        self,
        portfolio_id: str,
        account_id: str,
        symbol: str,
        decision_episode_id: str,
    ) -> Dict[str, object]:
        """Read only the position and account observations linked to one decision."""

        portfolio_key = str(portfolio_id or "")
        account_key = str(account_id or "")
        symbol_key = str(symbol or "").upper().strip()
        decision_key = str(decision_episode_id or "").strip()
        with self.connect() as connection:
            action_observations = connection.execute(
                "SELECT activity_episode_id, payload_json "
                "FROM portfolio_decision_action_observations "
                "WHERE account_id = %s AND symbol = %s AND prior_decision_episode_id = %s "
                "ORDER BY observed_at DESC, observation_id DESC LIMIT 4",
                (account_key, symbol_key, decision_key),
            ).fetchall()
            state = connection.execute(
                "SELECT payload_json FROM portfolio_state_snapshots WHERE portfolio_id = %s "
                "ORDER BY observed_at DESC, state_id DESC LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            activity_ids = [
                str(item.get("activity_episode_id") or "")
                for item in action_observations or []
                if str(item.get("activity_episode_id") or "")
            ]
            activities = []
            if activity_ids:
                placeholders = ",".join(["%s"] * len(activity_ids))
                activities = connection.execute(
                    "SELECT episode_id, payload_json FROM portfolio_activity_episodes "
                    "WHERE episode_id IN (" + placeholders + ")",
                    tuple(activity_ids),
                ).fetchall()

        activity_by_id = {
            str(item.get("episode_id") or ""): _json_loads(item.get("payload_json"), {})
            for item in activities or []
        }
        observations = []
        for item in action_observations or []:
            observation = _json_loads(item.get("payload_json"), {})
            activity = activity_by_id.get(str(item.get("activity_episode_id") or ""), {})
            instrument = next((
                dict(row)
                for row in activity.get("instrumentChanges") or []
                if isinstance(row, dict) and str(row.get("symbol") or "").upper().strip() == symbol_key
            ), {})
            for key in (
                "previousQuantity", "observedQuantity", "quantityDelta", "confidence",
            ):
                if instrument.get(key) not in (None, ""):
                    observation[key] = instrument.get(key)
            observations.append(observation)

        state_payload = _json_loads((state or {}).get("payload_json"), {})
        position = next((
            dict(item)
            for item in state_payload.get("positions") or []
            if isinstance(item, dict) and str(item.get("symbol") or "").upper().strip() == symbol_key
        ), {})
        if position:
            position["observedAt"] = state_payload.get("observedAt") or ""
            position["observationState"] = "observed"
        else:
            position = {
                "symbol": symbol_key,
                "observedAt": state_payload.get("observedAt") or "",
                "observationState": "not-held" if state_payload else "unavailable",
            }
        return {
            "actionObservations": observations,
            "currentPosition": position,
        }

    def ontology_portfolio_lifecycle_context(self, portfolio_id: str) -> Dict[str, object]:
        portfolio_key = str(portfolio_id or "")
        with self.connect() as connection:
            reconciliation = connection.execute(
                "SELECT payload_json FROM portfolio_reconciliations WHERE portfolio_id = %s "
                "ORDER BY source_snapshot_at DESC, reconciliation_id DESC LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            inferred_rows = connection.execute(
                "SELECT payload_json FROM portfolio_ledger_entries WHERE portfolio_id = %s "
                "AND entry_type IN (" + ",".join(["%s"] * len(INFERRED_SNAPSHOT_ENTRY_TYPES)) + ") "
                "ORDER BY occurred_at DESC, entry_id DESC LIMIT 20",
                (portfolio_key, *sorted(INFERRED_SNAPSHOT_ENTRY_TYPES)),
            ).fetchall()
            decision_cycle = connection.execute(
                "SELECT payload_json FROM portfolio_decision_cycles WHERE portfolio_id = %s "
                "ORDER BY created_at DESC, cycle_id DESC LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            risk_snapshot = connection.execute(
                "SELECT payload_json FROM portfolio_risk_snapshots WHERE portfolio_id = %s "
                "ORDER BY observed_at DESC, risk_snapshot_id DESC LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            exposure = connection.execute(
                "SELECT payload_json FROM portfolio_exposure_snapshots WHERE portfolio_id = %s "
                "ORDER BY observed_at DESC, exposure_snapshot_id DESC LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            rebalance = connection.execute(
                "SELECT payload_json FROM portfolio_rebalance_proposals WHERE portfolio_id = %s "
                "ORDER BY created_at DESC, proposal_id DESC LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            rebalance_state = connection.execute(
                "SELECT current_payload_json, revision, transition_type FROM portfolio_rebalance_states "
                "WHERE portfolio_id = %s LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            checkpoint = connection.execute(
                "SELECT payload_json FROM portfolio_snapshot_checkpoints WHERE portfolio_id = %s",
                (portfolio_key,),
            ).fetchone()
            episodes = connection.execute(
                "SELECT payload_json FROM portfolio_activity_episodes WHERE portfolio_id = %s "
                "ORDER BY observed_at DESC, episode_id DESC LIMIT 8",
                (portfolio_key,),
            ).fetchall()
            state = connection.execute(
                "SELECT payload_json FROM portfolio_state_snapshots WHERE portfolio_id = %s "
                "ORDER BY observed_at DESC, state_id DESC LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            action_observations = connection.execute(
                "SELECT payload_json FROM portfolio_decision_action_observations WHERE portfolio_id = %s "
                "ORDER BY observed_at DESC, observation_id DESC LIMIT 8",
                (portfolio_key,),
            ).fetchall()
            quarantines = connection.execute(
                "SELECT payload_json FROM portfolio_snapshot_quarantines WHERE portfolio_id = %s "
                "ORDER BY observed_at DESC, quarantine_id DESC LIMIT 8",
                (portfolio_key,),
            ).fetchall()
        rebalance_state_payload = _json_loads((rebalance_state or {}).get("current_payload_json"), {})
        if rebalance_state_payload:
            rebalance_state_payload["revision"] = str((rebalance_state or {}).get("revision") or "")
            rebalance_state_payload["lastTransitionType"] = str((rebalance_state or {}).get("transition_type") or "")
        payload = {
            "portfolioId": portfolio_key,
            "snapshotCheckpoint": _json_loads(checkpoint.get("payload_json"), {}) if checkpoint else {},
            "reconciliation": _json_loads(reconciliation.get("payload_json"), {}) if reconciliation else {},
            "recentInferredActivities": [
                activity_payload(ledger_entry_from_payload(payload))
                for payload in [_json_loads(item.get("payload_json"), {}) for item in inferred_rows or []]
                if payload
            ],
            "recentActivityEpisodes": [_json_loads(item.get("payload_json"), {}) for item in episodes or []],
            "portfolioState": _json_loads(state.get("payload_json"), {}) if state else {},
            "decisionActionObservations": [
                _json_loads(item.get("payload_json"), {}) for item in action_observations or []
            ],
            "recentSnapshotQuarantines": [
                _json_loads(item.get("payload_json"), {}) for item in quarantines or []
            ],
            "exposureSnapshot": _json_loads(exposure.get("payload_json"), {}) if exposure else {},
            "portfolioDecisionCycle": _json_loads(decision_cycle.get("payload_json"), {}) if decision_cycle else {},
            "portfolioRiskSnapshot": _json_loads(risk_snapshot.get("payload_json"), {}) if risk_snapshot else {},
            "rebalanceProposal": _json_loads(rebalance.get("payload_json"), {}) if rebalance else {},
            "rebalanceState": rebalance_state_payload,
        }
        payload["status"] = "ready" if any(
            value for key, value in payload.items() if key not in {"portfolioId", "status"}
        ) else "unavailable"
        return payload

    def latest_portfolio_lifecycle(self, portfolio_id: str) -> Dict[str, object]:
        portfolio_key = str(portfolio_id or "")
        account_id = portfolio_key[len("portfolio:"):] if portfolio_key.startswith("portfolio:") else portfolio_key
        with self.connect() as connection:
            reconciliation = connection.execute(
                "SELECT payload_json FROM portfolio_reconciliations WHERE portfolio_id = %s "
                "ORDER BY source_snapshot_at DESC, reconciliation_id DESC LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            exposure = connection.execute(
                "SELECT payload_json FROM portfolio_exposure_snapshots WHERE portfolio_id = %s "
                "ORDER BY observed_at DESC, exposure_snapshot_id DESC LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            risk_snapshot = connection.execute(
                "SELECT payload_json FROM portfolio_risk_snapshots WHERE portfolio_id = %s "
                "ORDER BY observed_at DESC, risk_snapshot_id DESC LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            rebalance = connection.execute(
                "SELECT payload_json FROM portfolio_rebalance_proposals WHERE portfolio_id = %s "
                "ORDER BY created_at DESC, proposal_id DESC LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            rebalance_state = connection.execute(
                "SELECT current_payload_json, revision, transition_type FROM portfolio_rebalance_states "
                "WHERE portfolio_id = %s LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            plans = connection.execute(
                "SELECT plan_id, payload_json FROM investment_action_plans WHERE portfolio_id = %s "
                "ORDER BY created_at DESC, plan_id DESC LIMIT 20",
                (portfolio_key,),
            ).fetchall()
            decision_cycle = connection.execute(
                "SELECT payload_json FROM portfolio_decision_cycles WHERE portfolio_id = %s "
                "ORDER BY created_at DESC, cycle_id DESC LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            ledger_rows = connection.execute(
                "SELECT payload_json FROM portfolio_ledger_entries WHERE portfolio_id = %s "
                "ORDER BY occurred_at DESC, entry_id DESC LIMIT 100",
                (portfolio_key,),
            ).fetchall()
            inferred_rows = connection.execute(
                "SELECT payload_json FROM portfolio_ledger_entries WHERE portfolio_id = %s "
                "AND entry_type IN (" + ",".join(["%s"] * len(INFERRED_SNAPSHOT_ENTRY_TYPES)) + ") "
                "ORDER BY occurred_at DESC, entry_id DESC LIMIT 20",
                (portfolio_key, *sorted(INFERRED_SNAPSHOT_ENTRY_TYPES)),
            ).fetchall()
            ledger_summary = connection.execute(
                "SELECT COUNT(*) AS entry_count, "
                "SUM(CASE WHEN entry_type IN ('OPENING_POSITION','OPENING_CASH') THEN 1 ELSE 0 END) AS opening_count, "
                "SUM(CASE WHEN entry_type NOT IN ('OPENING_POSITION','OPENING_CASH') THEN 1 ELSE 0 END) AS activity_count, "
                "MAX(occurred_at) AS last_entry_at FROM portfolio_ledger_entries WHERE portfolio_id = %s",
                (portfolio_key,),
            ).fetchone() or {}
            checkpoint = connection.execute(
                "SELECT payload_json FROM portfolio_snapshot_checkpoints WHERE portfolio_id = %s",
                (portfolio_key,),
            ).fetchone()
            activity_episodes = connection.execute(
                "SELECT payload_json FROM portfolio_activity_episodes WHERE portfolio_id = %s "
                "ORDER BY observed_at DESC, episode_id DESC LIMIT 20",
                (portfolio_key,),
            ).fetchall()
            state_snapshot = connection.execute(
                "SELECT payload_json FROM portfolio_state_snapshots WHERE portfolio_id = %s "
                "ORDER BY observed_at DESC, state_id DESC LIMIT 1",
                (portfolio_key,),
            ).fetchone()
            action_observations = connection.execute(
                "SELECT payload_json FROM portfolio_decision_action_observations WHERE portfolio_id = %s "
                "ORDER BY observed_at DESC, observation_id DESC LIMIT 20",
                (portfolio_key,),
            ).fetchall()
            snapshot_quarantines = connection.execute(
                "SELECT payload_json FROM portfolio_snapshot_quarantines WHERE portfolio_id = %s "
                "ORDER BY observed_at DESC, quarantine_id DESC LIMIT 20",
                (portfolio_key,),
            ).fetchall()
            plan_ids = [str(item.get("plan_id") or "") for item in plans or [] if str(item.get("plan_id") or "")]
            plan_reviews = []
            executions = []
            fills = []
            if plan_ids:
                placeholders = ",".join(["%s"] * len(plan_ids))
                plan_reviews = connection.execute(
                    "SELECT payload_json FROM investment_action_plan_reviews WHERE plan_id IN (" + placeholders + ") "
                    "ORDER BY reviewed_at DESC, review_id DESC LIMIT 100",
                    tuple(plan_ids),
                ).fetchall()
                executions = connection.execute(
                    "SELECT execution_episode_id, payload_json FROM trade_execution_episodes "
                    "WHERE action_plan_id IN (" + placeholders + ") "
                    "ORDER BY created_at DESC, execution_episode_id DESC LIMIT 100",
                    tuple(plan_ids),
                ).fetchall()
                execution_ids = [
                    str(item.get("execution_episode_id") or "")
                    for item in executions or [] if str(item.get("execution_episode_id") or "")
                ]
                if execution_ids:
                    execution_placeholders = ",".join(["%s"] * len(execution_ids))
                    fills = connection.execute(
                        "SELECT payload_json FROM trade_execution_fills WHERE execution_episode_id IN ("
                        + execution_placeholders + ") ORDER BY executed_at DESC, fill_id DESC LIMIT 200",
                        tuple(execution_ids),
                    ).fetchall()
            attributions = connection.execute(
                "SELECT a.payload_json FROM investment_performance_attributions a "
                "JOIN investment_decision_episodes d ON d.episode_id = a.decision_episode_id "
                "WHERE d.account_id = %s ORDER BY a.observed_at DESC, a.attribution_id DESC LIMIT 100",
                (account_id,),
            ).fetchall()
            decision_reviews = connection.execute(
                "SELECT r.payload_json FROM investment_decision_reviews r "
                "JOIN investment_decision_episodes d ON d.episode_id = r.decision_episode_id "
                "WHERE d.account_id = %s ORDER BY r.reviewed_at DESC, r.review_id DESC LIMIT 100",
                (account_id,),
            ).fetchall()
        mandate = self.active_mandate(portfolio_key)
        attribution_payloads = [_json_loads(item.get("payload_json"), {}) for item in attributions or []]
        decision_review_payloads = [_json_loads(item.get("payload_json"), {}) for item in decision_reviews or []]
        rebalance_state_payload = _json_loads((rebalance_state or {}).get("current_payload_json"), {})
        if rebalance_state_payload:
            rebalance_state_payload["revision"] = str((rebalance_state or {}).get("revision") or "")
            rebalance_state_payload["lastTransitionType"] = str((rebalance_state or {}).get("transition_type") or "")
        return {
            "status": "ready" if any([mandate, reconciliation, exposure, risk_snapshot, rebalance, plans, ledger_rows]) else "unavailable",
            "portfolioId": portfolio_key,
            "mandate": mandate,
            "snapshotCheckpoint": _json_loads(checkpoint.get("payload_json"), {}) if checkpoint else {},
            "reconciliation": _json_loads(reconciliation.get("payload_json"), {}) if reconciliation else {},
            "exposureSnapshot": _json_loads(exposure.get("payload_json"), {}) if exposure else {},
            "portfolioRiskSnapshot": _json_loads(risk_snapshot.get("payload_json"), {}) if risk_snapshot else {},
            "rebalanceProposal": _json_loads(rebalance.get("payload_json"), {}) if rebalance else {},
            "rebalanceState": rebalance_state_payload,
            "portfolioDecisionCycle": _json_loads(decision_cycle.get("payload_json"), {}) if decision_cycle else {},
            "ledgerSummary": {
                "entryCount": int(ledger_summary.get("entry_count") or 0),
                "openingCount": int(ledger_summary.get("opening_count") or 0),
                "activityCount": int(ledger_summary.get("activity_count") or 0),
                "lastEntryAt": str(ledger_summary.get("last_entry_at") or ""),
            },
            "ledgerEntries": [_json_loads(item.get("payload_json"), {}) for item in ledger_rows or []],
            "recentInferredActivities": [
                activity_payload(ledger_entry_from_payload(payload))
                for payload in [_json_loads(item.get("payload_json"), {}) for item in inferred_rows or []]
                if payload
            ],
            "recentActivityEpisodes": [
                _json_loads(item.get("payload_json"), {}) for item in activity_episodes or []
            ],
            "portfolioState": _json_loads(state_snapshot.get("payload_json"), {}) if state_snapshot else {},
            "decisionActionObservations": [
                _json_loads(item.get("payload_json"), {}) for item in action_observations or []
            ],
            "recentSnapshotQuarantines": [
                _json_loads(item.get("payload_json"), {}) for item in snapshot_quarantines or []
            ],
            "actionPlans": [_json_loads(item.get("payload_json"), {}) for item in plans or []],
            "actionPlanReviews": [_json_loads(item.get("payload_json"), {}) for item in plan_reviews or []],
            "executionEpisodes": [_json_loads(item.get("payload_json"), {}) for item in executions or []],
            "fills": [_json_loads(item.get("payload_json"), {}) for item in fills or []],
            "performanceAttributions": attribution_payloads,
            "decisionReviews": decision_review_payloads,
            "decisionQualitySummary": decision_quality_summary(attribution_payloads, decision_review_payloads),
        }

    def lifecycle_feedback_for_decisions(self, decision_episode_ids: Iterable[str]) -> Dict[str, Dict[str, object]]:
        episode_ids = list(dict.fromkeys(
            str(item or "").strip()
            for item in decision_episode_ids or []
            if str(item or "").strip()
        ))
        if not episode_ids:
            return {}
        placeholders = ",".join(["%s"] * len(episode_ids))
        with self.connect() as connection:
            reviews = connection.execute(
                "SELECT decision_episode_id, payload_json FROM investment_decision_reviews "
                "WHERE decision_episode_id IN (" + placeholders + ") "
                "ORDER BY reviewed_at ASC, review_id ASC",
                tuple(episode_ids),
            ).fetchall()
            attributions = connection.execute(
                "SELECT decision_episode_id, payload_json FROM investment_performance_attributions "
                "WHERE decision_episode_id IN (" + placeholders + ") "
                "ORDER BY observed_at ASC, attribution_id ASC",
                tuple(episode_ids),
            ).fetchall()
        result = {item: {"decisionReviews": [], "performanceAttributions": []} for item in episode_ids}
        for row in reviews or []:
            result.setdefault(str(row.get("decision_episode_id") or ""), {}).setdefault("decisionReviews", []).append(
                _json_loads(row.get("payload_json"), {})
            )
        for row in attributions or []:
            result.setdefault(str(row.get("decision_episode_id") or ""), {}).setdefault("performanceAttributions", []).append(
                _json_loads(row.get("payload_json"), {})
            )
        return result

    def execution_feedback_for_decisions(self, decision_episode_ids: Iterable[str]) -> Dict[str, Dict[str, object]]:
        episode_ids = list(dict.fromkeys(
            str(item or "").strip()
            for item in decision_episode_ids or []
            if str(item or "").strip()
        ))
        if not episode_ids:
            return {}
        result = {
            item: {"actionPlans": [], "executionEpisodes": [], "fills": []}
            for item in episode_ids
        }
        episode_placeholders = ",".join(["%s"] * len(episode_ids))
        with self.connect() as connection:
            plans = connection.execute(
                "SELECT plan_id, decision_episode_id, payload_json FROM investment_action_plans "
                "WHERE decision_episode_id IN (" + episode_placeholders + ") "
                "ORDER BY created_at ASC, plan_id ASC",
                tuple(episode_ids),
            ).fetchall()
            plan_to_episode = {
                str(item.get("plan_id") or ""): str(item.get("decision_episode_id") or "")
                for item in plans or []
                if str(item.get("plan_id") or "")
            }
            for item in plans or []:
                episode_id = str(item.get("decision_episode_id") or "")
                result.setdefault(episode_id, {"actionPlans": [], "executionEpisodes": [], "fills": []})[
                    "actionPlans"
                ].append(_json_loads(item.get("payload_json"), {}))
            executions = []
            if plan_to_episode:
                plan_ids = list(plan_to_episode)
                plan_placeholders = ",".join(["%s"] * len(plan_ids))
                executions = connection.execute(
                    "SELECT execution_episode_id, action_plan_id, payload_json FROM trade_execution_episodes "
                    "WHERE action_plan_id IN (" + plan_placeholders + ") "
                    "ORDER BY created_at ASC, execution_episode_id ASC",
                    tuple(plan_ids),
                ).fetchall()
            execution_to_episode = {}
            for item in executions or []:
                episode_id = plan_to_episode.get(str(item.get("action_plan_id") or ""), "")
                execution_id = str(item.get("execution_episode_id") or "")
                if execution_id:
                    execution_to_episode[execution_id] = episode_id
                result.setdefault(episode_id, {"actionPlans": [], "executionEpisodes": [], "fills": []})[
                    "executionEpisodes"
                ].append(_json_loads(item.get("payload_json"), {}))
            if execution_to_episode:
                execution_ids = list(execution_to_episode)
                execution_placeholders = ",".join(["%s"] * len(execution_ids))
                fills = connection.execute(
                    "SELECT execution_episode_id, payload_json FROM trade_execution_fills "
                    "WHERE execution_episode_id IN (" + execution_placeholders + ") "
                    "ORDER BY executed_at ASC, fill_id ASC",
                    tuple(execution_ids),
                ).fetchall()
                for item in fills or []:
                    episode_id = execution_to_episode.get(str(item.get("execution_episode_id") or ""), "")
                    result.setdefault(episode_id, {"actionPlans": [], "executionEpisodes": [], "fills": []})[
                        "fills"
                    ].append(_json_loads(item.get("payload_json"), {}))
        return result

    def save_rebalance_proposal(self, proposal: RebalanceProposal) -> RebalanceProposal:
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO portfolio_rebalance_proposals (
                    proposal_id, portfolio_id, mandate_version, exposure_snapshot_id,
                    status, payload_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE status = VALUES(status),
                    payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
                (
                    proposal.proposal_id,
                    proposal.portfolio_id,
                    proposal.mandate_version,
                    proposal.exposure_snapshot_id,
                    proposal.status,
                    json_dumps(proposal.to_dict()),
                    proposal.created_at or stamp,
                    stamp,
                ),
            )
        return proposal

    def save_action_plan(self, plan: ActionPlan) -> ActionPlan:
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
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
        return plan

    def action_plan(self, plan_id: str) -> Optional[ActionPlan]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM investment_action_plans WHERE plan_id = %s",
                (str(plan_id or ""),),
            ).fetchone()
        return ActionPlan.from_dict(_json_loads(row.get("payload_json"), {})) if row else None

    def latest_active_action_plan(self, portfolio_id: str, symbol: str, action: str) -> Optional[ActionPlan]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM investment_action_plans WHERE portfolio_id = %s AND action = %s "
                "AND status IN ('review-required','approved') ORDER BY created_at DESC, plan_id DESC LIMIT 25",
                (str(portfolio_id or ""), str(action or "").upper()),
            ).fetchall()
        expected_symbol = str(symbol or "").upper()
        for row in rows or []:
            plan = ActionPlan.from_dict(_json_loads(row.get("payload_json"), {}))
            if plan.envelope and str(plan.envelope.symbol or "").upper() == expected_symbol:
                return plan
        return None

    def save_action_plan_review(self, review: ActionPlanReview) -> ActionPlanReview:
        stamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT IGNORE INTO investment_action_plan_reviews (
                    review_id, plan_id, decision, reviewer, policy_version,
                    reason, payload_json, reviewed_at, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    review.review_id,
                    review.plan_id,
                    review.decision,
                    review.reviewer,
                    review.policy_version,
                    review.reason,
                    json_dumps(review.to_dict()),
                    review.reviewed_at,
                    stamp,
                ),
            )
        return review

    def save_execution_episode(self, episode: ExecutionEpisode) -> ExecutionEpisode:
        stamp = utc_now()
        with self.transaction() as connection:
            self.save_execution_episode_with_connection(connection, episode, stamp)
        return episode

    def execution_episode_for_plan(self, plan_id: str) -> Optional[ExecutionEpisode]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM trade_execution_episodes WHERE action_plan_id = %s "
                "ORDER BY updated_at DESC, execution_episode_id DESC LIMIT 1",
                (str(plan_id or ""),),
            ).fetchone()
        return ExecutionEpisode.from_dict(_json_loads(row.get("payload_json"), {})) if row else None

    def save_execution_episode_with_connection(self, connection, episode: ExecutionEpisode, stamp: str) -> None:
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

    def save_execution_with_ledger(self, episode: ExecutionEpisode, plan: ActionPlan, domain_event=None) -> Dict[str, object]:
        existing = self.ledger_entries(plan.portfolio_id, limit=100000)
        actual_entries = execution_ledger_entries(episode, plan, existing)
        stamp = utc_now()
        inserted = 0
        with self.transaction() as connection:
            self.save_execution_episode_with_connection(connection, episode, stamp)
            inserted = self.append_ledger_entries_with_connection(connection, actual_entries, stamp)
            if domain_event:
                insert_domain_event_with_connection(connection, domain_event)
        return {
            "status": episode.status,
            "executionEpisode": episode.to_dict(),
            "actualLedgerEntryCount": inserted,
            "supersededInferredEntryCount": sum(
                len(list((item.payload or {}).get("supersedesEntryIds") or [])) for item in actual_entries
            ),
        }

    def save_decision_review(self, review: DecisionReview) -> DecisionReview:
        stamp = utc_now()
        with self.transaction() as connection:
            self.save_decision_review_with_connection(connection, review, stamp)
        return review

    def save_decision_review_with_connection(self, connection, review: DecisionReview, stamp: str) -> None:
        connection.execute(
            """
            INSERT INTO investment_decision_reviews (
                review_id, decision_episode_id, selected_hypothesis_status,
                policy_compliant, execution_compliant, evidence_still_valid,
                payload_json, reviewed_at, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE selected_hypothesis_status = VALUES(selected_hypothesis_status),
                policy_compliant = VALUES(policy_compliant),
                execution_compliant = VALUES(execution_compliant),
                evidence_still_valid = VALUES(evidence_still_valid),
                payload_json = VALUES(payload_json), reviewed_at = VALUES(reviewed_at),
                updated_at = VALUES(updated_at)
            """,
            (
                review.review_id,
                review.decision_episode_id,
                review.selected_hypothesis_status,
                1 if review.policy_compliant else 0,
                1 if review.execution_compliant else 0,
                1 if review.evidence_still_valid else 0,
                json_dumps(review.to_dict()),
                review.reviewed_at,
                stamp,
                stamp,
            ),
        )

    def save_performance_attribution(self, attribution: PerformanceAttribution) -> PerformanceAttribution:
        stamp = utc_now()
        with self.transaction() as connection:
            self.save_performance_attribution_with_connection(connection, attribution, stamp)
        return attribution

    def save_performance_attribution_with_connection(
        self,
        connection,
        attribution: PerformanceAttribution,
        stamp: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO investment_performance_attributions (
                attribution_id, decision_episode_id, action_plan_id,
                execution_episode_id, market_return_pct, instrument_return_pct,
                active_return_pct, execution_cost, realized_profit_loss,
                currency_effect_pct, payload_json, observed_at, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE market_return_pct = VALUES(market_return_pct),
                instrument_return_pct = VALUES(instrument_return_pct),
                active_return_pct = VALUES(active_return_pct),
                execution_cost = VALUES(execution_cost),
                realized_profit_loss = VALUES(realized_profit_loss),
                currency_effect_pct = VALUES(currency_effect_pct),
                payload_json = VALUES(payload_json), observed_at = VALUES(observed_at),
                updated_at = VALUES(updated_at)
            """,
            (
                attribution.attribution_id,
                attribution.decision_episode_id,
                attribution.action_plan_id,
                attribution.execution_episode_id,
                attribution.market_return_pct,
                attribution.instrument_return_pct,
                attribution.active_return_pct,
                attribution.execution_cost,
                attribution.realized_profit_loss,
                attribution.currency_effect_pct,
                json_dumps(attribution.to_dict()),
                attribution.observed_at,
                stamp,
                stamp,
            ),
        )

    def save_outcome_reviews(
        self,
        attributions: Iterable[PerformanceAttribution],
        reviews: Iterable[DecisionReview],
    ) -> Dict[str, int]:
        attribution_rows = list(attributions or [])
        review_rows = list(reviews or [])
        if not attribution_rows and not review_rows:
            return {"attributionCount": 0, "reviewCount": 0}
        stamp = utc_now()
        with self.transaction() as connection:
            for attribution in attribution_rows:
                self.save_performance_attribution_with_connection(connection, attribution, stamp)
            for review in review_rows:
                self.save_decision_review_with_connection(connection, review, stamp)
        return {"attributionCount": len(attribution_rows), "reviewCount": len(review_rows)}
