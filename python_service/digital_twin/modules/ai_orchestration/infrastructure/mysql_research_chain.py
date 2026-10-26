"""Commit the parent answer, child question and source handoff atomically."""
from ..domain.research_review_parts import approved
from copy import deepcopy
import json

from ..domain import research_chain
from ..domain.brain_management import new_case
from ..domain.research_work import contract_key


def record_continuation(store, connection, job, result, item, continuation, children, now, receipt):
    children[:] = [row for row in children if not (row["capability"] == "research" and row.get("question") == item["question"])]

    def defer(reason):
        receipt["deferred"].append({"question": item["question"], "parentCaseId": continuation["parentCaseId"], "reason": reason})

    if not approved(result):
        defer("next-question-independent-review-required")
        return
    parent = store.read(connection, continuation["parentCaseId"], job["accountId"], job["symbol"], lock=True)
    review = next(r for r in result["caseReviews"] if r["caseId"] == continuation["parentCaseId"])
    if (not parent or parent.get("worldId") != job["worldId"]
            or parent["revision"] != review["expectedRevision"] + 1
            or parent.get("questionAssessment", {}).get("executionInputId") != result["executionInputId"]
            or parent["questionAssessment"].get("researchReviewInputId") != result["researchQuality"]["reviewInputId"]):
        raise ValueError("next question requires its committed independently reviewed parent answer")
    if parent.get("inquiryContinuation"):
        defer("parent-already-continued")
        return
    child_key = research_chain.child_id(job, item["question"])
    if store.read(connection, child_key, job["accountId"], job["symbol"], lock=True):
        defer("next-question-already-exists")
        return
    active = [json.loads(row["payload_json"]) for row in connection.execute(
        "SELECT payload_json FROM ai_brain_cases WHERE account_id=%s AND symbol=%s AND kind='question' "
        "AND status IN ('open','waiting','review-needed','blocked') "
        "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.worldId'))=%s FOR UPDATE",
        (job["accountId"], job["symbol"], job["worldId"])).fetchall()]
    if len(active) >= 12:
        defer("active-case-limit")
        return
    if any(row.get("questionContract") and contract_key(row["questionContract"]) == contract_key(continuation["contract"]) for row in active):
        defer("next-question-contract-already-active")
        return
    child = new_case(job, result, item["question"], "research", now)
    child["questionContract"] = deepcopy(continuation["contract"])
    child["completionCriterion"] = child["questionContract"]["answerCriterion"]
    child["inquiryLineage"] = research_chain.lineage(parent, continuation, result)
    child["reason"] = continuation["rationale"]
    # This is a reviewed distinct question, not a source retry. It receives its
    # own unchanged collection limits and never resets the parent's attempts.
    store.schedule_research(connection, child, job, result, children, now)
    store.save(connection, child, job["taskId"], "created", {"parentCaseId": parent["caseId"],
        "executionInputId": result["executionInputId"], "researchReviewInputId": result["researchQuality"]["reviewInputId"]})
    parent["inquiryContinuation"] = {"version": research_chain.VERSION, "caseId": child_key,
        "question": item["question"], "relation": continuation["relation"],
        "rationale": continuation["rationale"], "expectedInsight": continuation["expectedInsight"],
        "at": now, "executionInputId": result["executionInputId"],
        "researchReviewInputId": result["researchQuality"]["reviewInputId"]}
    store.save(connection, parent, job["taskId"], "question-continued", parent["inquiryContinuation"])
    receipt["caseIds"].append(child_key)
    receipt.setdefault("continuations", []).append({"parentCaseId": parent["caseId"], "caseId": child_key})
