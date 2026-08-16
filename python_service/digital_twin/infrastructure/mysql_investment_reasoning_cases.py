"""MySQL repository for the InvestmentReasoning aggregate."""

from typing import Dict, List, Optional

from ..domain.investment_reasoning import ReasoningCase
from .mysql_operational_connection import MySQLOperationalConnection
from .mysql_operational_helpers import _json_loads
from .operational_common import json_dumps
from .settings import utc_now


class MySQLInvestmentReasoningCaseStore(MySQLOperationalConnection):
    def save(self, reasoning_case: ReasoningCase) -> ReasoningCase:
        payload = reasoning_case.to_dict()
        symbols = list(reasoning_case.fact_delta.symbols)
        accounts = list(reasoning_case.fact_delta.account_ids)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO investment_reasoning_cases (
                    case_id, request_id, deployment_id, release_fingerprint,
                    validation_cohort_id, reasoning_lane, stage, primary_symbol,
                    account_ids_json, symbols_json, payload_json, case_version,
                    created_at, updated_at, completed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    deployment_id = VALUES(deployment_id),
                    release_fingerprint = VALUES(release_fingerprint),
                    validation_cohort_id = VALUES(validation_cohort_id),
                    reasoning_lane = VALUES(reasoning_lane),
                    stage = VALUES(stage),
                    primary_symbol = VALUES(primary_symbol),
                    account_ids_json = VALUES(account_ids_json),
                    symbols_json = VALUES(symbols_json),
                    payload_json = VALUES(payload_json),
                    case_version = VALUES(case_version),
                    updated_at = VALUES(updated_at),
                    completed_at = VALUES(completed_at)
                """,
                (
                    reasoning_case.case_id,
                    reasoning_case.request_id,
                    reasoning_case.deployment_id,
                    reasoning_case.release_fingerprint,
                    reasoning_case.validation_cohort_id,
                    reasoning_case.fact_delta.lane,
                    reasoning_case.stage,
                    symbols[0] if symbols else "",
                    json_dumps(accounts),
                    json_dumps(symbols),
                    json_dumps(payload),
                    reasoning_case.version,
                    reasoning_case.created_at,
                    reasoning_case.updated_at or utc_now(),
                    reasoning_case.completed_at,
                ),
            )
        return reasoning_case

    def get(self, case_id: str) -> Optional[ReasoningCase]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM investment_reasoning_cases WHERE case_id = %s",
                (str(case_id or ""),),
            ).fetchone()
        return self.case_from_row(row)

    def get_by_request(self, request_id: str) -> Optional[ReasoningCase]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM investment_reasoning_cases WHERE request_id = %s",
                (str(request_id or ""),),
            ).fetchone()
        return self.case_from_row(row)

    def latest(self, deployment_id: str = "", symbol: str = "", limit: int = 20) -> List[ReasoningCase]:
        conditions = []
        params = []
        if str(deployment_id or "").strip():
            conditions.append("deployment_id = %s")
            params.append(str(deployment_id or "").strip())
        if str(symbol or "").strip():
            conditions.append("JSON_CONTAINS(CAST(symbols_json AS JSON), JSON_QUOTE(%s))")
            params.append(str(symbol or "").upper().strip())
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM investment_reasoning_cases"
                + where
                + " ORDER BY updated_at DESC, case_id DESC LIMIT %s",
                (*params, max(1, min(200, int(limit or 20)))),
            ).fetchall()
        return [case for case in (self.case_from_row(row) for row in rows or []) if case]

    def summary(self, deployment_id: str = "", release_fingerprint: str = "") -> Dict[str, object]:
        conditions = []
        params = []
        if str(deployment_id or "").strip():
            conditions.append("deployment_id = %s")
            params.append(str(deployment_id or "").strip())
        if str(release_fingerprint or "").strip():
            conditions.append("release_fingerprint = %s")
            params.append(str(release_fingerprint or "").strip())
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT stage, COUNT(*) AS row_count, MIN(created_at) AS oldest, "
                "MAX(updated_at) AS latest FROM investment_reasoning_cases"
                + where
                + " GROUP BY stage",
                tuple(params),
            ).fetchall()
        return {
            "deploymentId": str(deployment_id or ""),
            "releaseFingerprint": str(release_fingerprint or ""),
            "counts": {
                str(row.get("stage") or ""): int(row.get("row_count") or 0)
                for row in rows or []
            },
            "oldest": {
                str(row.get("stage") or ""): str(row.get("oldest") or "")
                for row in rows or []
            },
            "latest": {
                str(row.get("stage") or ""): str(row.get("latest") or "")
                for row in rows or []
            },
        }

    @staticmethod
    def case_from_row(row) -> Optional[ReasoningCase]:
        if not row:
            return None
        payload = _json_loads(row.get("payload_json"), {})
        return ReasoningCase.from_dict(payload) if payload else None
