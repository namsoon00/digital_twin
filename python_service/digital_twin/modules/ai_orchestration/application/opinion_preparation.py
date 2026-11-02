"""Cheap source checks and finite gap reads before paid opinion work."""
import re
from copy import deepcopy

from ..domain import opinion_memory, execution_lanes, evidence_purpose, reliable_retrieval
from digital_twin.modules.reasoning.contracts import content_hash


def memory_reads(session, packet, documents, reports, already_read):
    """Reopen at most two issued originals linked to captured research memory."""
    from ..domain import analyst_memory
    if not analyst_memory.enabled(packet):
        return {}, []
    documents = {r["factId"]: r for r in documents}
    reports = {r["factId"]: r for r in reports}
    linked = list(dict.fromkeys(key for row in packet.get("analystMemory", {}).get("items", [])
                                for key in row["sourceFactIds"]))
    views, receipts = {}, []
    for key in linked:
        if key in already_read or len(receipts) >= 2:
            continue
        if key in documents:
            request = {"tool": "read_document", "factId": key}
            result = session.read_document(key)
        elif key in reports and reports[key].get("metrics"):
            metrics = reports[key]["metrics"][:12]
            request = {"tool": "read_report", "factId": key, "metrics": metrics}
            result = session.read_report(key, metrics)
        else:
            continue  # An old pointer never grants access outside this capture.
        views.update({f["id"]: f for f in result.get("facts", [])})
        receipts.append({"request": request, "factIds": [f["id"] for f in result.get("facts", [])],
            "memoryIds": [r["memoryId"] for r in packet["analystMemory"]["items"] if key in r["sourceFactIds"]],
            "resultHash": content_hash(result), "status": "read-needs-assessment"})
    return views, receipts


def quote_blocker(packet):
    if (not opinion_memory.enabled(packet) or not execution_lanes.market(packet)
            or not packet.get("priceInvestigationPolicy")):
        return {}
    # This lane's price-investigation contract requires a dated quote even for
    # a dated explanation. The separate original-question lane remains usable.
    quotes = packet.get("evidenceUses", {}).get("quotes", [])
    if not any(q.get("currentStateEligible") or q.get("datedReferenceEligible") for q in quotes):
        return {"reasonCode": "current-quote", "stage": "pre-retrieval-source-check",
            "reason": "가격 변동 설명에 필요한 원천 시세 시각이 확인되지 않아 새 시세를 기다립니다.",
            "quoteWaitKey": evidence_purpose.quote_wait_key(packet, [])}
    return {}


def gap_reads(session, packet, research, documents, reports, already_read):
    """Read at most two issued, not-yet-selected documents/reports. No inference."""
    if not opinion_memory.enabled(packet) or execution_lanes.market(packet):
        return {}, {}
    requirements, case_ids, periods = [], set(), set()
    for case in research:
        assessment = case.get("questionAssessment") or {}
        parts = assessment.get("answerParts") or {}
        missing = assessment.get("missingRequirements", [])
        unread = [gap for gap in parts.get("gaps", []) if gap.get("cause") == "unread-source"
                  and gap.get("role") == "essential"]
        for gap in unread:
            index = gap.get("requirementIndex", -1)
            if type(index) is int and 0 <= index < len(missing):
                requirement = {"caseId": case["caseId"], "requirement": missing[index], "nextAction": gap.get("nextAction", "")}
                if reliable_retrieval.enabled(packet):
                    requirement.update(requirementId=content_hash({"caseId": case["caseId"],
                        "contract": case.get("questionContract"), "requirement": missing[index]}), requirementIndex=index)
                requirements.append(requirement)
                case_ids.add(case["caseId"])
        if unread:
            periods.update(re.findall(r"20\d{2}(?:-\d{2}(?:-\d{2})?)?", str(case.get("questionContract", {}).get("period", ""))))
    if not requirements:
        return {}, {}
    plans = []
    for row in documents:
        if case_ids.intersection(row.get("caseIds", [])) and row["factId"] not in already_read:
            plans.append({"tool": "read_document", "factId": row["factId"]})
    # Report values are partial evidence only. Reading them cannot claim that
    # the filing's notes, accounting perimeter or attribution were verified.
    terms = " ".join(r["requirement"] + " " + r["nextAction"] for r in requirements).casefold()
    aliases = [(('현금', 'cash'), ('cash', 'capex', 'capitalexpenditure')),
               (('매출', '손익', '이익', 'income', 'profit'), ('revenue', 'income', 'profit', 'tax')),
               (('재고', '운전자본', 'inventory'), ('inventor', 'receivable', 'payable', 'workingcapital'))]
    wanted = {metric for keys, metrics in aliases if any(k in terms for k in keys) for metric in metrics}
    for row in reports:
        date = str(row.get("periodEnd", ""))
        if row["factId"] in already_read or not periods or not any(date.startswith(p) for p in periods):
            continue
        metrics = [m for m in row.get("metrics", []) if any(w in m.casefold().replace('_', '') for w in wanted)][:12]
        if metrics:
            plans.append({"tool": "read_report", "factId": row["factId"], "metrics": metrics})
    views, receipts = {}, []
    for request in plans[:2]:
        value = session.read_document(request["factId"]) if request["tool"] == "read_document" else session.read_report(request["factId"], request["metrics"])
        for fact in value.get("facts", []):
            views[fact["id"]] = fact
        receipt = {"request": request, "factIds": [r["id"] for r in value.get("facts", [])], "status": "read-needs-assessment"}
        if reliable_retrieval.enabled(packet):
            linked_cases = next((set(row.get("caseIds", [])) for row in documents if row["factId"] == request["factId"]), case_ids)
            receipt["requirementIds"] = [r["requirementId"] for r in requirements[:8] if r["caseId"] in linked_cases]
        receipts.append(receipt)
    return views, {"version": "bounded-question-gap-reads-v2" if reliable_retrieval.enabled(packet) else "bounded-question-gap-reads-v1", "requirements": deepcopy(requirements[:8]),
        "reads": receipts, "status": "read-needs-assessment" if receipts else "no-additional-issued-source",
        "authority": "source-read-only-not-answer-completion"}
