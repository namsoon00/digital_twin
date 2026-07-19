from typing import Dict, List

from ..domain.investment_brain import NovelHypothesisProposal, utc_now_iso
from ..domain.investment_evidence_governance import ResearchRun
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps


class MySQLInvestmentResearchStore(MySQLOperationalConnection):
    def save_run(self, run: ResearchRun) -> ResearchRun:
        stamp = utc_now_iso()
        payload = run.to_dict()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO investment_research_runs (
                    run_id, question_id, account_id, symbol, status, started_at,
                    completed_at, changed_evidence_count, reasoning_refreshed,
                    payload_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE status = VALUES(status),
                    completed_at = VALUES(completed_at),
                    changed_evidence_count = VALUES(changed_evidence_count),
                    reasoning_refreshed = VALUES(reasoning_refreshed),
                    payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
                (
                    run.run_id,
                    run.question_id,
                    run.account_id,
                    run.symbol,
                    run.status,
                    run.started_at,
                    run.completed_at,
                    run.changed_evidence_count,
                    1 if run.reasoning_refreshed else 0,
                    json_dumps(payload),
                    stamp,
                    stamp,
                ),
            )
        return run

    def list_runs(self, account_id: str = "", symbol: str = "", limit: int = 50) -> List[Dict[str, object]]:
        where = []
        params: List[object] = []
        if account_id:
            where.append("account_id = %s")
            params.append(str(account_id))
        if symbol:
            where.append("symbol = %s")
            params.append(str(symbol).upper())
        params.append(max(1, min(500, int(limit or 50))))
        sql = "SELECT payload_json FROM investment_research_runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY started_at DESC, run_id DESC LIMIT %s"
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [_json_loads(row.get("payload_json"), {}) for row in rows or []]

    def save_hypothesis_proposal(self, proposal: NovelHypothesisProposal) -> NovelHypothesisProposal:
        stamp = utc_now_iso()
        payload = proposal.to_dict()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO investment_hypothesis_proposals (
                    proposal_id, account_id, symbol, status, title,
                    source_question_id, source, payload_json, created_at, updated_at,
                    reviewed_at, review_note
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '', '')
                ON DUPLICATE KEY UPDATE title = VALUES(title), source = VALUES(source),
                    payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
                """,
                (
                    proposal.proposal_id,
                    proposal.account_id,
                    proposal.symbol,
                    proposal.status,
                    proposal.title,
                    proposal.source_question_id,
                    proposal.source,
                    json_dumps(payload),
                    proposal.created_at or stamp,
                    stamp,
                ),
            )
        return proposal

    def list_hypothesis_proposals(self, status: str = "", symbol: str = "", limit: int = 50) -> List[Dict[str, object]]:
        where = []
        params: List[object] = []
        if status:
            where.append("status = %s")
            params.append(str(status))
        if symbol:
            where.append("symbol = %s")
            params.append(str(symbol).upper())
        params.append(max(1, min(500, int(limit or 50))))
        sql = "SELECT payload_json, status, reviewed_at, review_note FROM investment_hypothesis_proposals"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC, proposal_id DESC LIMIT %s"
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        result = []
        for row in rows or []:
            payload = _json_loads(row.get("payload_json"), {})
            payload.update({
                "status": str(row.get("status") or payload.get("status") or ""),
                "reviewedAt": str(row.get("reviewed_at") or ""),
                "reviewNote": str(row.get("review_note") or ""),
            })
            result.append(payload)
        return result

    def review_hypothesis_proposal(self, proposal_id: str, status: str, note: str = "") -> Dict[str, object]:
        allowed = {"review-required", "researching", "approved", "rejected", "needs-revision"}
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in allowed:
            raise ValueError("지원하지 않는 가설 제안 상태입니다: " + normalized_status)
        stamp = utc_now_iso()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM investment_hypothesis_proposals WHERE proposal_id = %s",
                (str(proposal_id or ""),),
            ).fetchone()
            if not row:
                raise KeyError("가설 제안을 찾지 못했습니다: " + str(proposal_id or ""))
            payload = _json_loads(row.get("payload_json"), {})
            payload["status"] = normalized_status
            payload["reviewedAt"] = stamp
            payload["reviewNote"] = str(note or "")
            connection.execute(
                """
                UPDATE investment_hypothesis_proposals
                SET status = %s, payload_json = %s, updated_at = %s,
                    reviewed_at = %s, review_note = %s
                WHERE proposal_id = %s
                """,
                (normalized_status, json_dumps(payload), stamp, stamp, str(note or ""), str(proposal_id or "")),
            )
        return payload
