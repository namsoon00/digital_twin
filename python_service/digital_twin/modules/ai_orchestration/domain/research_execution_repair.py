"""One replacement execution for a proven lost source plan, never a new question."""
from copy import deepcopy

from .planning import identity
from .brain_management import ACTIVE_CASE_STATES, later

VERSION = "source-owner-repair-v1"


def lost_source_plan(job, run):
    context = (run or {}).get("requestContext") or {}
    return bool(run and run.get("runId") == "ai-control-" + job["taskId"][:40]
        and context.get("source") == "ai-control" and context.get("aiControlTaskId") == job["taskId"]
        and run.get("accountId") == job["accountId"] and run.get("symbol") == job["symbol"]
        and run.get("status") == "not-required" and run.get("stopReason") == "not-required"
        and not run.get("taskIds") and not (run.get("executedPlan") or {}).get("tasks")
        and not run.get("taskAssessments") and not run.get("roundHistory"))


def replacement_execution(job, result, case, run, now):
    if not lost_source_plan(job, run) or job.get("executionRepair"):
        raise ValueError("source-repair-not-applicable")
    if (result.get("runId") != run["runId"] or result.get("status") != "not-required"
            or (result.get("documentaryAnswer") or {}).get("status") != "unavailable"):
        raise ValueError("source-repair-result-changed")
    if (case.get("caseId") != job.get("brainCaseId")
            or any(case.get(key) != job.get(key) for key in ("accountId", "symbol", "worldId"))
            or case.get("status") not in ACTIVE_CASE_STATES
            or (case.get("lastResearch") or {}).get("taskId") != job["taskId"]
            or (case.get("latestSourceAnswer") or {}).get("status") == "answered"
            or case.get("question") != job.get("question")
            or case.get("questionContract") != job.get("questionContract")):
        raise ValueError("source-repair-case-changed")
    if job["taskId"] in (case.get("executionRepairs") or {}):
        raise ValueError("source-repair-already-recorded")
    task_id = identity(VERSION, job["taskId"])
    repair = {"version": VERSION, "originalTaskId": job["taskId"], "originalRunId": run["runId"],
              "reason": "source-plan-lost", "at": now, "taskId": task_id}
    task = {key: deepcopy(job[key]) for key in ("accountId", "symbol", "worldId", "name", "question",
        "brainCaseId", "researchRequest", "questionContract", "evidenceRequirements", "previousSourceRecovery",
        "previousSourceRun") if key in job}
    task.update(taskId=task_id, capability="research", priority=5, availableAt=now, executionRepair=repair)
    updated = deepcopy(case)
    updated.setdefault("executionRepairs", {})[job["taskId"]] = {
        **repair, "previousResearch": deepcopy(case["lastResearch"]),
        "previousAnswerResolution": deepcopy(case.get("answerResolution") or {})}
    updated.pop("answerResolution", None)
    updated["lastResearch"] = {**{key: value for key, value in case["lastResearch"].items() if key != "result"},
                               "taskId": task_id, "executionRepair": repair}
    updated.setdefault("researchRepeat", {})["authorizedTaskId"] = task_id
    updated["researchRepeat"].pop("waitReason", None)
    updated.update(status="waiting", nextCheckAt=later(now, 360),
                   reason="조사 실행의 질문 연결 오류를 복구했습니다. 기존 답변을 보존하고 같은 질문의 자료를 다시 확인합니다.")
    return task, updated
