"""Bounded question-specific research status, never current market evidence."""
from copy import deepcopy


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
    return result
