"""Retain source pointers, and admit only exact documents read from the frozen graph."""
from copy import deepcopy
from .question_purpose import enabled

VERSION = "question-source-handoff-v1"
MAX_SOURCES = 8


def source_references(case, limit=MAX_SOURCES):
    answer = case.get("latestSourceAnswer") or case.get("lastResearch", {}).get("result", {}).get("documentaryAnswer") or {}
    history = [source for row in reversed(case.get("answerMemory", [])[-3:])
               for source in row.get("answer", {}).get("sources", [])]
    rows = answer.get("sources", []) + case.get("questionSourceReferences", []) + history
    seen, result = set(), []
    for row in rows:
        key = row.get("evidenceId")
        if isinstance(key, str) and key and key not in seen:
            seen.add(key)
            result.append(deepcopy(row))
    return result if limit is None else result[:limit]


def retain(case, answer):
    # Keep references, not past answers asserted as current truth. A new source
    # revision wins over its previous pointer; same-question bounds stay fixed.
    case["questionSourceReferences"] = source_references({
        "latestSourceAnswer": answer,
        "questionSourceReferences": source_references(case)})


def build(packet, research, facts):
    if not enabled(packet):
        return {}
    scopes = {r["caseId"] for r in packet.get("researchScope", {}).get("questions", [])}
    rows = []
    for case in packet.get("questionEvidenceIndex", []):
        if case.get("caseId") not in scopes:
            continue
        # Only original, question-owned source IDs already in the frozen graph.
        refs = {r["evidenceId"] for r in case.get("sourceAnswer", {}).get("sources", [])}
        ids = sorted(f["id"] for f in facts if
            (f.get("historicalDocumentReference") or f.get("questionSourceReference") or {}).get("evidenceId") in refs)
        rows.append({"caseId": case["caseId"], "factIds": ids[:MAX_SOURCES]})
    return {"version": VERSION, "questions": sorted(rows, key=lambda r: r["caseId"]),
            "authority": "read-source-link-only"}


def validate(packet, research):
    if enabled(packet) and packet.get("questionSourceHandoff") != build(packet, research, packet["facts"]):
        raise ValueError("question source handoff changed after captured memory")


def assessment_scope(packet, case_id):
    scope = deepcopy(next((r for r in packet.get("researchScope", {}).get("questions", []) if r["caseId"] == case_id), {}))
    if enabled(packet) and scope:
        handoff = packet.get("questionSourceHandoff", {})
        if handoff.get("version") == VERSION:
            ids = next((r["factIds"] for r in handoff["questions"] if r["caseId"] == case_id), [])
            scope["selectedFactIds"] = sorted(set(scope["selectedFactIds"]) | set(ids))
    return scope
