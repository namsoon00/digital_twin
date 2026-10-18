"""Attach verified read progress to its agenda owner on the completion transaction."""
from ..domain.brain_management import ACTIVE_CASE_STATES
from ..domain.question_evidence import checkpoints


def record_question_evidence(store, connection, job, packet, trace):
    if job.get("capability") != "observe" or any(packet.get(key) != job.get(key)
            for key in ("accountId", "symbol", "worldId", "taskId")):
        raise ValueError("question progress subject mismatch")
    receipt = {"caseIds": [], "deferred": [], "authority": "read-progress-only"}
    for item in checkpoints(packet, trace):
        case = store.read(connection, item["caseId"], job["accountId"], job["symbol"], lock=True)
        if (not case or case["worldId"] != job["worldId"] or case.get("capability") != "research"
                or case["status"] not in ACTIVE_CASE_STATES or case["revision"] != item["expectedRevision"]
                or case.get("questionContract") != item["questionContract"]):
            receipt["deferred"].append({"caseId": item["caseId"], "reason": "case-changed-after-capture"})
            continue
        if (case.get("evidenceProgress") or {}).get("progressKey") == item["progress"]["progressKey"]:
            continue
        # This is neither an answer nor a retry authorization. Preserve the
        # original contract, answer receipts, attempts, status and wait deadline.
        case["evidenceProgress"] = item["progress"]
        store.save(connection, case, job["taskId"], "research-evidence-progress", item["progress"])
        receipt["caseIds"].append(case["caseId"])
    return receipt
