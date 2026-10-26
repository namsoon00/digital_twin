"""Bounded question-specific research status, never current market evidence."""
from copy import deepcopy
from digital_twin.modules.news_intelligence.contracts import source_recovery_summary
from .documentary_answer import documentary_answer


def research_return(run, run_id, source_task_id, request):
    result = {"runId": run_id, "status": run["status"], "researchRequest": deepcopy(request),
        "searchScope": "question-specific" if request else "legacy-subject-refresh", "authority": "research-only"}
    # Absent historical measurements stay absent, rather than becoming zero.
    for key in ("changedEvidenceCount", "stopReason", "completedAt"):
        if key in run:
            result[key] = deepcopy(run[key])
    rows = [row for row in run.get("taskAssessments", [])
            if isinstance(row, dict) and row.get("taskId") == source_task_id]
    assessment = {"version": "question-research-return-v1", "taskId": source_task_id,
                  "status": "unavailable", "authority": "historical-work-status-only"}
    if len(rows) == 1:
        row = rows[0]
        truncated = []
        for key in ("status", "coverageState", "semanticReviewState", "assessmentFingerprint", "assessedAt", "reason"):
            if key in row and isinstance(row[key], str):
                assessment[key] = row[key][:600]
                if len(row[key]) > 600:
                    truncated.append(key)
        missing = row.get("missingRequirements", [])
        assessment["missingRequirementCount"] = len(missing)
        assessment["missingRequirements"] = [str(item)[:160] for item in missing[:8]]
        if len(missing) > 8 or any(len(str(item)) > 160 for item in missing[:8]):
            truncated.append("missingRequirements")
        for key in ("candidateEvidenceIds", "resultEvidenceIds", "counterEvidenceIds"):
            assessment[key.removesuffix("Ids") + "Count"] = len(row.get(key, []))
        assessment["truncatedFields"] = truncated
    result["questionAssessment"] = assessment
    result["documentaryAnswer"] = documentary_answer(run, source_task_id)
    reuse = (run.get("executedPlan") or {}).get("documentaryReviewReuse")
    if isinstance(reuse, dict):
        result["planningReuse"] = {key: deepcopy(reuse[key]) for key in ("version", "skippedPlanningCalls") if key in reuse}
    exploration = (run.get("executedPlan") or {}).get("sourceExploration")
    if isinstance(exploration, dict):
        result["sourceExploration"] = {key: deepcopy(exploration[key]) for key in (
            "version", "taskIds", "stopReason", "searchCount", "readCount", "extractionCount", "evidenceIds", "missingRequirements", "elapsedSeconds")
            if key in exploration}
    scoped_exploration = exploration if exploration and source_task_id in exploration.get("taskIds", []) else {}
    providers = [row for row in run.get("providerStatuses", [])
                 if not row.get("taskIds") or source_task_id in row["taskIds"]]
    result["sourceRecovery"] = source_recovery_summary(scoped_exploration, providers)
    return result
