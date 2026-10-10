"""Narrow, immutable observation ingress to the existing hypothesis queue."""
from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.infrastructure.operational_common import json_dumps
from digital_twin.modules.decisions.contracts import utc_now_iso


class MySQLObservationDevelopmentStore(MySQLOperationalConnection):
    def enqueue_observation_development_with_connection(self, connection, payload):
        """The central observation and this immutable handoff commit together."""
        from digital_twin.modules.model_registry.contracts import validate_observation_development_context
        validate_observation_development_context(payload["observationContext"], payload["accountId"], payload["symbol"])
        if payload.get("source") != "ai-control-observation" or not payload.get("gapFingerprint") or not payload.get("requestId"):
            raise ValueError("invalid observation development handoff")
        stamp = utc_now_iso()
        item = {**payload, "status": "pending", "queuedAt": stamp}
        connection.execute(
            "INSERT IGNORE INTO investment_hypothesis_proposal_requests "
            "(request_id,account_id,symbol,gap_fingerprint,status,attempts,available_at,lease_owner,lease_expires_at,"
            "last_error,payload_json,result_json,created_at,updated_at,completed_at) "
            "VALUES (%s,%s,%s,%s,'pending',0,%s,'','','',%s,'{}',%s,%s,'')",
            (item["requestId"], item["accountId"], item["symbol"], item["gapFingerprint"], stamp, json_dumps(item), stamp, stamp))
        row = connection.execute("SELECT request_id,status,payload_json FROM investment_hypothesis_proposal_requests "
            "WHERE account_id=%s AND symbol=%s AND gap_fingerprint=%s FOR UPDATE",
            (item["accountId"], item["symbol"], item["gapFingerprint"])).fetchone()
        if not row:
            raise ValueError("observation development handoff was not persisted")
        stored = _json_loads(row["payload_json"], {})
        original = stored["observationContext"]
        return {"requestId": row["request_id"], "status": row["status"], "sourceTaskId": original["taskId"],
                "requestedQuestionMatched": stored["question"]["text"] == payload["question"]["text"],
                "sourceQuestionIds": [item["caseId"] for item in original.get("sourceQuestions", [])],
                "reused": original["taskId"] != item["observationContext"]["taskId"], "authority": "proposal-only"}

    def observation_development_record(self, request_id, account_id, symbol, world):
        with self.connect() as connection:
            row = connection.execute("SELECT request_id,status,payload_json,result_json FROM investment_hypothesis_proposal_requests "
                "WHERE request_id=%s AND account_id=%s AND symbol=%s", (request_id, account_id, symbol)).fetchone()
        if not row:
            return None
        request = _json_loads(row["payload_json"], {})
        if (request.get("source") != "ai-control-observation"
                or request.get("observationContext", {}).get("packet", {}).get("worldId") != world):
            raise ValueError("development result scope mismatch")
        return {"requestId": row["request_id"], "status": row["status"], "request": request,
                "result": _json_loads(row["result_json"], {})}

    def observation_development_records(self, account_id, symbol, limit=3):
        with self.connect() as connection:
            rows = connection.execute("SELECT request_id,status,payload_json,result_json FROM investment_hypothesis_proposal_requests "
                "WHERE account_id=%s AND symbol=%s AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.source'))='ai-control-observation' "
                "ORDER BY created_at DESC,request_id DESC LIMIT %s", (account_id, symbol, max(1, min(3, int(limit))))).fetchall()
        return [{"requestId": row["request_id"], "status": row["status"],
                 "request": _json_loads(row["payload_json"], {}), "result": _json_loads(row["result_json"], {})} for row in rows]
