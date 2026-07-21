from typing import List

from ..domain.investment_strategy_proposals import (
    InvestmentStrategyProposal,
    without_retired_strategy_score_fields,
)
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps
from .settings import utc_now


def strategy_proposal_payload_from_row(row):
    """Return the public payload and whether a legacy score field was removed."""
    raw_payload = _json_loads(row["payload_json"], {})
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    sanitized = without_retired_strategy_score_fields(payload)
    sanitized = dict(sanitized) if isinstance(sanitized, dict) else {}
    return sanitized, sanitized != payload


def strategy_proposal_from_row(row) -> InvestmentStrategyProposal:
    payload, _changed = strategy_proposal_payload_from_row(row)
    payload.update({
        "id": row["proposal_id"],
        "status": row["status"],
        "title": row["title"],
        "sourceTrigger": row["source_trigger"],
        "sourceExperimentId": row["source_experiment_id"],
        "symbols": _json_loads(row["symbols_json"], []),
        "ruleIds": _json_loads(row["rule_ids_json"], []),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "approvedAt": row["approved_at"],
        "deployedAt": row["deployed_at"],
    })
    return InvestmentStrategyProposal.from_dict(payload)


class MySQLInvestmentStrategyProposalStore(MySQLOperationalConnection):
    @staticmethod
    def rewrite_retired_score_fields(connection, rows) -> None:
        """Physically remove retired proposal scores without changing lifecycle dates."""
        for row in rows or []:
            payload, changed = strategy_proposal_payload_from_row(row)
            if not changed:
                continue
            connection.execute(
                "UPDATE investment_strategy_proposals SET payload_json = %s WHERE proposal_id = %s",
                (json_dumps(payload), str(row["proposal_id"])),
            )

    def list(self, status: str = "", limit: int = 500) -> List[InvestmentStrategyProposal]:
        clauses = []
        params = []
        if status:
            clauses.append("status = %s")
            params.append(str(status))
        params.append(max(1, min(1000, int(limit or 500))))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM investment_strategy_proposals" + where + " ORDER BY updated_at DESC, proposal_id LIMIT %s",
                params,
            ).fetchall()
            self.rewrite_retired_score_fields(connection, rows)
        return [strategy_proposal_from_row(row) for row in rows]

    def get(self, proposal_id: str):
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM investment_strategy_proposals WHERE proposal_id = %s",
                (str(proposal_id or ""),),
            ).fetchone()
            if row:
                self.rewrite_retired_score_fields(connection, [row])
        return strategy_proposal_from_row(row) if row else None

    def save(self, proposal: InvestmentStrategyProposal) -> None:
        stamp = utc_now()
        if not proposal.created_at:
            proposal.created_at = stamp
        if not proposal.updated_at:
            proposal.updated_at = stamp
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO investment_strategy_proposals (
                    proposal_id, status, title, source_trigger, source_experiment_id,
                    symbols_json, rule_ids_json, payload_json, created_at, updated_at,
                    approved_at, deployed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    status = VALUES(status),
                    title = VALUES(title),
                    source_trigger = VALUES(source_trigger),
                    source_experiment_id = VALUES(source_experiment_id),
                    symbols_json = VALUES(symbols_json),
                    rule_ids_json = VALUES(rule_ids_json),
                    payload_json = VALUES(payload_json),
                    updated_at = VALUES(updated_at),
                    approved_at = VALUES(approved_at),
                    deployed_at = VALUES(deployed_at)
                """,
                (
                    proposal.proposal_id,
                    proposal.status,
                    proposal.title[:255],
                    proposal.source_trigger,
                    proposal.source_experiment_id,
                    json_dumps(proposal.symbols),
                    json_dumps(proposal.rule_ids),
                    json_dumps(proposal.to_dict()),
                    proposal.created_at,
                    proposal.updated_at,
                    proposal.approved_at,
                    proposal.deployed_at,
                ),
            )
