"""Focus a bounded collection on outstanding units without editing its question."""
from copy import deepcopy

from .research_progress import fingerprint, task_contract
from .source_focus import source_research_focus

VERSION = "outstanding-requirement-collection-v1"


def focus_collection(tasks, assessments):
    by_id = {row["taskId"]: row for row in assessments}
    focused, units = [], []
    for original in tasks:
        task = deepcopy(original)
        assessment = by_id.get(task.get("taskId"), {})
        # Only the review of this exact task/evidence may refine collection.
        # Coverage-only codes also remain in the full assessment and contract.
        missing = assessment.get("missingRequirements") or []
        gaps = [value for value in missing if isinstance(value, str) and 1 <= len(value) <= 1000]
        if gaps and assessment.get("semanticReviewState") in {"partial", "unresolved", "addressed"}:
            task["sourceFocus"] = source_research_focus(gaps[:8])
            units.append({"taskId": task["taskId"], "originalTaskHash": fingerprint(task_contract(original)),
                "assessmentFingerprint": assessment["assessmentFingerprint"],
                "requirements": [{"requirementId": fingerprint([task_contract(original), text]),
                                  "text": text} for text in gaps[:8]],
                "omittedRequirementCount": max(0, len(missing)-8), "authority": "collection-only"})
        focused.append(task)
    return focused, {"version": VERSION, "tasks": units}
