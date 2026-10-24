"""Bounded repair under the original case lock; old completed records are immutable."""
import json

from ..domain.planning import identity, stamp
from ..domain.research_execution_repair import VERSION, replacement_execution
from .mysql_brain_agenda import MySQLBrainAgendaStore
from .mysql_control import MySQLAIControlStore


def repair_source_execution(connection, task_id, read_run, *, apply=False):
    task = connection.execute("SELECT status,payload_json,result_json FROM ai_control_tasks "
        "WHERE task_id=%s FOR UPDATE", (task_id,)).fetchone()
    if not task or task["status"] != "completed":
        return {"status": "skipped", "reason": "source-task-not-completed"}
    replacement_id = identity(VERSION, task_id)
    if connection.execute("SELECT task_id FROM ai_control_tasks WHERE task_id=%s", (replacement_id,)).fetchone():
        return {"status": "already-repaired", "taskId": replacement_id}
    job, result = json.loads(task["payload_json"]), json.loads(task["result_json"])
    if job.get("capability") != "research" or job.get("taskId") != task_id:
        return {"status": "skipped", "reason": "source-task-scope-mismatch"}
    case = MySQLBrainAgendaStore.read(connection, job.get("brainCaseId", ""), job["accountId"], job["symbol"], lock=True)
    if not case:
        return {"status": "skipped", "reason": "source-case-missing"}
    run = read_run(connection, "ai-control-" + task_id[:40])
    try:
        child, updated = replacement_execution(job, result, case, run, stamp())
    except ValueError as error:
        return {"status": "skipped", "reason": str(error)}
    if apply:
        MySQLAIControlStore.insert(connection, child)
        # No progress publisher: execution repair is an audit event, not a new
        # source finding or another customer scheduling message.
        MySQLBrainAgendaStore.save_with_connection(connection, updated, child["taskId"],
            "source-execution-repaired", child["executionRepair"])
    return {"status": "repaired" if apply else "eligible", "taskId": child["taskId"],
            "symbol": job["symbol"], "originalTaskId": task_id, "researchAttempts": case.get("researchAttempts")}
