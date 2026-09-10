"""As-of company evidence from immutable monitor history, never current APIs."""

import json
from datetime import datetime, timedelta, timezone
from typing import Mapping

from .mysql_operational_connection import MySQLOperationalConnection


class MySQLOutcomeEvidenceSource(MySQLOperationalConnection):
    def load_outcome_evidence(self, account_id, requests):
        targets = []
        for request in list(requests or [])[:1000]:
            try:
                cutoff = datetime.fromisoformat(str(request.get("observedAt") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=timezone.utc)
            cutoff = cutoff.astimezone(timezone.utc)
            if not request.get("requestId") or not request.get("symbol"):
                continue
            targets.append((
                str(request["requestId"]),
                cutoff.isoformat().replace("+00:00", "Z"),
                (cutoff - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                "$.externalSignals.companyKnowledge." + json.dumps(str(request["symbol"]).upper()),
            ))
        result = {}
        for offset in range(0, len(targets), 100):
            batch = targets[offset:offset + 100]
            request_sql = " UNION ALL ".join("SELECT %s AS request_id, %s AS cutoff_at, %s AS floor_at, %s AS json_path" for _ in batch)
            with self.connect() as connection:
                rows = connection.execute(
                    "SELECT request.request_id, history.generated_at, "
                    "COALESCE(JSON_EXTRACT(NULLIF(history.projection_payload_json, ''), request.json_path), "
                    "JSON_EXTRACT(history.payload_json, request.json_path)) AS company_json "
                    "FROM (" + request_sql + ") request "
                    "JOIN monitor_snapshot_history history ON history.account_id = %s "
                    "AND history.generated_at = (SELECT MAX(candidate.generated_at) FROM monitor_snapshot_history candidate "
                    "WHERE candidate.account_id = history.account_id AND candidate.generated_at <= request.cutoff_at "
                    "AND candidate.generated_at >= request.floor_at "
                    "AND CAST(REPLACE(REPLACE(candidate.generated_at, 'T', ' '), 'Z', '') AS DATETIME(6)) "
                    "<= CAST(REPLACE(REPLACE(request.cutoff_at, 'T', ' '), 'Z', '') AS DATETIME(6)))",
                    tuple(value for target in batch for value in target) + (str(account_id),),
                ).fetchall()
            for row in rows:
                raw = row.get("company_json")
                try:
                    company = raw if isinstance(raw, Mapping) else json.loads(raw or "{}")
                except (TypeError, ValueError):
                    continue
                if isinstance(company, Mapping) and company:
                    result[str(row["request_id"])] = {
                        "companyContext": dict(company),
                        "evidenceSnapshotAt": str(row["generated_at"]),
                    }
        return result
