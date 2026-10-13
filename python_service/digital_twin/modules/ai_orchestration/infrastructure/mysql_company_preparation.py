"""Existing central queue and real review receipts for company preparation."""
from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from ..domain.planning import enabled, identity, stamp


class MySQLPreparationAI(MySQLOperationalConnection):
    def progress(self, subject):
        scope = tuple(subject[key] for key in ("accountId", "symbol", "worldId"))
        if not enabled(self.runtime_settings):
            return {"status": "blocked", "detail": "중앙 AI가 일시 중지돼 있습니다."}
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT task_id,status,available_at,last_error,updated_at,"
                "JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.quality.status')) AS quality,"
                "JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.input.sourceSnapshotId')) AS snapshot_id,"
                "JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.observedAt')) AS observed_at "
                "FROM ai_control_tasks WHERE account_id=%s AND symbol=%s AND capability='observe' "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s "
                "ORDER BY updated_at DESC,task_id DESC LIMIT 20", scope).fetchall()
        accepted = next((row for row in rows if row["status"] == "completed"
                         and row["quality"] in {"accepted", "observation-only"} and row["snapshot_id"] not in (None, "", "null")), None)
        latest = rows[0] if rows else {}
        failed = latest.get("status") == "failed" or latest.get("quality") == "rejected"
        if accepted:
            return {"status": "complete", "taskId": accepted["task_id"], "sourceSnapshotId": accepted["snapshot_id"],
                    "observedAt": accepted["observed_at"] or accepted["updated_at"],
                    "latestTaskStatus": latest.get("status", ""),
                    "detail": "근거가 연결된 분석 확인" + (" · 최근 후속 검토는 미통과하여 보완 필요" if failed else "")}
        return {"status": "blocked" if failed else "waiting", "taskId": latest.get("task_id", ""),
                "latestTaskStatus": latest.get("status", ""),
                "nextCheckAt": latest.get("available_at", ""), "errorKind": latest.get("last_error", ""),
                "detail": "AI 검토 미통과; 다음 처리와 근거 보완 필요" if failed else "중앙 AI 최초 검토 대기"}

    def request_preparation(self, subject, gaps, basis):
        if not enabled(self.runtime_settings):
            return {"status": "paused"}
        from .mysql_control import MySQLAIControlStore
        job = {key: subject[key] for key in ("accountId", "symbol", "worldId", "name")}
        bounded_gaps = list(gaps) if len(gaps) <= 4 else list(gaps)[:3] + [" / ".join(gaps[3:])]
        job.update(capability="research-plan", priority=4, availableAt=stamp(), researchGaps=bounded_gaps,
                   taskId=identity("company-preparation-v1", *[subject[key] for key in ("accountId", "symbol", "worldId")], basis))
        with self.transaction() as connection:
            MySQLAIControlStore.insert(connection, job)
            row = connection.execute("SELECT status,available_at,last_error FROM ai_control_tasks WHERE task_id=%s", (job["taskId"],)).fetchone()
        return {"taskId": job["taskId"], "status": row["status"], "nextCheckAt": row["available_at"], "errorKind": row["last_error"]}
