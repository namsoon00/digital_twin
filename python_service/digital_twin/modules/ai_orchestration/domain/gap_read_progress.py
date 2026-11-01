"""Source receipts reach authored inputs; only approved answers judge completion."""
from copy import deepcopy


def coverage(packet):
    work = packet.get("gapReadWork") or {}
    if work.get("version") != "bounded-question-gap-reads-v2":
        return {}
    selected = {row["id"] for row in packet.get("facts", [])}
    rows = []
    for requirement in work["requirements"]:
        reads = [r for r in work["reads"] if requirement["requirementId"] in r.get("requirementIds", [])]
        ids = list(dict.fromkeys(key for r in reads for key in r["factIds"]))
        rows.append({**deepcopy(requirement), "readFactIds": ids,
            "selectedFactIds": [key for key in ids if key in selected],
            "status": "selected-needs-assessment" if selected.intersection(ids) else "read-not-selected" if ids else "no-additional-issued-source"})
    return {"version": "gap-read-coverage-v1", "requirements": rows, "authority": "source-coverage-not-answer-completion"}


def reviewed(packet, assessment):
    rows = [row for row in coverage(packet).get("requirements", []) if row["caseId"] == assessment.get("caseId")]
    if not rows:
        return {}
    return {"version": "reviewed-gap-read-progress-v1", "requirements": rows,
        "answerStatus": assessment.get("answerStatus", assessment.get("status")),
        "remainingRequirements": deepcopy(assessment.get("missingRequirements", [])),
        "reviewInputId": assessment.get("researchReviewInputId"),
        "authority": "approved-question-assessment-only"}
