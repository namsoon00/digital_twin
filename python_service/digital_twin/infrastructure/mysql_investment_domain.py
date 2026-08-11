"""MySQL adapters for portfolio policy, ledger, execution, and review domains."""

from decimal import Decimal
import hashlib
from typing import Dict, Iterable, List, Optional

from ..domain.investment_mandate import InvestmentMandate
from ..domain.investment_outcomes import DecisionReview, PerformanceAttribution
from ..domain.portfolio_ledger import PortfolioLedgerEntry
from ..domain.portfolio_rebalancing import RebalanceProposal
from ..domain.trade_execution import ActionPlan, ExecutionEpisode
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps
from .settings import utc_now


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
        return {
            "status": "ready" if decision else "unavailable",
            "decisionEpisodeId": episode_id,
            "decisionEpisode": _json_loads(decision.get("payload_json"), {}) if decision else {},
            "actionPlans": [_json_loads(item.get("payload_json"), {}) for item in plans or []],
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
            for entry in rows:
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

    def save_execution_episode(self, episode: ExecutionEpisode) -> ExecutionEpisode:
        stamp = utc_now()
        with self.transaction() as connection:
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
        return episode

    def save_decision_review(self, review: DecisionReview) -> DecisionReview:
        stamp = utc_now()
        with self.transaction() as connection:
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
        return review

    def save_performance_attribution(self, attribution: PerformanceAttribution) -> PerformanceAttribution:
        stamp = utc_now()
        with self.transaction() as connection:
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
        return attribution
