"""Reopen at most two currently issued originals; historical decisions are not reused."""
from copy import deepcopy
from ..domain import read_continuation as policy
from ..domain.retrieval import MAX_RESULT_BYTES
from digital_twin.modules.reasoning.contracts import content_hash


def reopen(session, plans, documents, reports, question_index, already_read):
    issued_docs = {r["factId"] for r in documents}
    issued_reports = {r["factId"]: set(r.get("metrics", [])) for r in reports}
    scope = policy.question_scope(question_index)
    receipts, omitted, seen, used = [], [], set(), 0
    import json
    for plan in plans[:policy.MAX_PLANS]:
        request = plan["request"]
        tool = request.get("tool")
        expected = {"tool", "factId", "metrics"} if tool == "read_report" else {"tool", "factId"}
        if tool not in {"read_document", "read_report"} or set(request) != expected:
            omitted.append({"fromInputId": plan["fromInputId"], "reason": "unsupported-read-plan"})
            continue
        key = content_hash(request)
        if key in seen or request.get("factId") in already_read:
            continue
        seen.add(key)
        reason = ""
        if plan["questionScope"] != scope:
            reason = "question-contract-changed"
        elif request["tool"] == "read_document" and request["factId"] not in issued_docs:
            reason = "source-not-issued"
        elif request["tool"] == "read_report" and (request["factId"] not in issued_reports
                or not request.get("metrics") or not set(request["metrics"]).issubset(issued_reports[request["factId"]])):
            reason = "source-or-metrics-not-issued"
        if reason:
            omitted.append({"fromInputId": plan["fromInputId"], "reason": reason})
            continue
        if len(receipts) >= policy.MAX_READS:
            omitted.append({"fromInputId": plan["fromInputId"], "reason": "read-limit"})
            continue
        result = session.read_document(request["factId"]) if request["tool"] == "read_document" else session.read_report(request["factId"], request["metrics"])
        size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())
        if size > MAX_RESULT_BYTES or used + size > policy.MAX_BYTES:
            omitted.append({"fromInputId": plan["fromInputId"], "reason": "context-budget"})
            continue
        facts = result.get("facts", [])
        if not facts:
            omitted.append({"fromInputId": plan["fromInputId"], "reason": "no-current-source-facts"})
            continue
        receipts.append({"request": deepcopy(request), "result": result, "fromInputId": plan["fromInputId"],
            "fromAttempt": plan["fromAttempt"], "resultHash": content_hash(result),
            "sourceChanged": facts[0].get("evidenceView", {}).get("sourceFactHash") != plan.get("sourceFactHash"),
            "status": "current-source-read-needs-assessment"})
        used += size
    return {"version": policy.VERSION, "reads": receipts, "omitted": omitted,
            "bytes": used, "authority": "current-source-read-only-not-answer-completion"}
