"""Bounded PK reads for visible questions; no initializer, mutation or model call."""
from datetime import datetime, timezone
import json

from ..domain.question_progress import question_progress


def attach_question_progress(connection, cases, now=None):
    now = now or datetime.now(timezone.utc)
    # Historical question payloads omitted kind; the owning table/save contract
    # has always defaulted these rows to question.
    questions = [row for row in cases[:40] if row.get("kind", "question") == "question"]
    if not questions:
        return
    ids = sorted({(case.get(key) or {}).get("taskId") for case in questions
                  for key in ("lastResearch", "researchPlan", "questionAssessment")} - {None, ""})
    tasks = {}
    for offset in range(0, len(ids), 20):
        batch = ids[offset:offset + 20]
        rows = connection.execute("SELECT task_id,account_id,symbol,capability,status,last_error,available_at,"
            "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId')) AS world_id,"
            "JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.brainCaseId')) AS case_id,"
            "JSON_EXTRACT(result_json,'$.publication') AS publication,"
            "(JSON_TYPE(JSON_EXTRACT(result_json,'$.summary'))='STRING' AND "
            "COALESCE(CHAR_LENGTH(JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.summary'))),0)>0) AS has_summary "
            "FROM ai_control_tasks FORCE INDEX(PRIMARY) WHERE task_id IN (" + ",".join(["%s"] * len(batch)) + ") LIMIT 20", tuple(batch)).fetchall()
        for row in rows:
            tasks[row["task_id"]] = {"taskId": row["task_id"], "accountId": row["account_id"],
                "symbol": row["symbol"], "worldId": row["world_id"], "brainCaseId": row["case_id"],
                "status": row["status"], "capability": row["capability"], "lastError": row["last_error"],
                "nextCheckAt": row["available_at"], "hasSummary": bool(row["has_summary"]),
                "publication": json.loads(row["publication"] or "null") or {}}
    row = connection.execute("SELECT state_json FROM ai_control_execution_state FORCE INDEX(PRIMARY) WHERE scope_key=%s LIMIT 1",
                             ("shared-local-model",)).fetchone()
    execution = json.loads(row["state_json"]) if row else {}
    for case in questions:
        case["workflow"] = question_progress(case, tasks, execution, now)
