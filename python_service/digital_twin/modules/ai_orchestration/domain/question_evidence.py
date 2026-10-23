"""Bounded question work pointers; read progress has no answer authority."""
from copy import deepcopy

from digital_twin.modules.reasoning.contracts import content_hash, evidence_change_identity

VERSION = "question-evidence-work-v1"
INSTRUCTIONS = """질문별 자료 연결 계약:
questionIndex.sourceAnswer는 이미 검토한 부분 답변과 원문 위치입니다. 해당 원문은 questionDocumentIndex/read_fact로 실제 읽은 뒤 판단하세요.
missingRequirements와 evidenceProgress.missingRequirements는 원래 questionContract의 부족분입니다. 기존 답변을 반복하지 말고 이 항목에 필요한 기간·지표·원문을 우선 조회하세요.
sourceAnswer.status=answered도 현재 선택 근거의 충분성을 대신하지 않습니다. 부분 답변이나 과거 ready 표시는 답변 완료·가설 검증이 아닙니다.
이번 questionScopes에 ready 질문이 하나라도 있으면 finish로 답변 검토에 넘기세요. waiting 질문의 부족분 때문에 다시 내부 research로 돌아가지 마세요.
새 원문이 없거나 미래 관측·접근 복구를 기다려야 하면 부족 항목을 구체적으로 남기세요. 문구 변경이나 대기 시간 경과는 새 근거가 아닙니다.
"""


def source_answer(case):
    return case.get("latestSourceAnswer") or case.get("lastResearch", {}).get("result", {}).get("documentaryAnswer") or {}


def source_identity(case):
    """Source content and reviewed coverage matter; model prose and timers do not."""
    answer = source_answer(case)
    sources = [{"materialFingerprint": row["materialFingerprint"]} if row.get("materialFingerprint") else
        {key: row[key] for key in ("evidenceId", "inputFingerprint", "periodEnd", "reportDate", "publishedAt") if key in row}
        for row in answer.get("sources", [])]
    return {"status": (case.get("answerResolution") or {}).get("status", answer.get("status", "")),
        "sources": sorted({content_hash(row): row for row in sources}.values(), key=content_hash)}


def progress_memory(progress):
    return {key: deepcopy(progress[key]) for key in ("version", "status", "missingRequirements", "selectedFactIds",
        "resumeOn", "sourceSnapshotId", "executionInputId", "authority") if key in progress}


def question_index(research):
    result = []
    for case in research:
        if case.get("kind") != "brain-case" or case.get("capability") != "research":
            continue
        answer = source_answer(case)
        resolution = case.get("answerResolution") or {}
        row = {key: deepcopy(case[key]) for key in ("caseId", "question", "questionContract", "revision") if key in case}
        if case.get("questionWorkKey"):
            row["questionWorkKey"] = case["questionWorkKey"]
        sources = answer.get("sources", [])
        row["sourceAnswer"] = {"status": resolution.get("status", answer.get("status", "unavailable")),
            "sources": [{key: deepcopy(source[key]) for key in ("evidenceId", "periodEnd", "reportDate", "publishedAt")
                         if key in source} for source in sources[:8]], "omittedSourceCount": max(0, len(sources)-8),
            "sourceIdentity": content_hash(source_identity(case)), "authority": "historical-source-answer-only"}
        row["missingRequirements"] = deepcopy(resolution.get("missingRequirements", answer.get("missingRequirements", [])))[:8]
        row["evidenceProgress"] = progress_memory(case.get("evidenceProgress") or {})
        row["sourceBlockers"] = deepcopy(answer.get("blockers", []))[:4]
        result.append(row)
    return result[:5]


def captured_index(index):
    """Only ownership/replay pointers belong in the final author's packet."""
    return [{**{key: deepcopy(row[key]) for key in ("caseId", "revision", "questionContract", "questionWorkKey") if key in row},
             "sourceAnswer": {"sourceIdentity": row.get("sourceAnswer", {}).get("sourceIdentity", "")}}
            for row in index]


def checkpoints(packet, trace):
    """Derive operational checkpoints only from the frozen, final read decision."""
    if packet.get("questionEvidencePolicy") != VERSION or not trace or not trace[-1].get("decision"):
        return []
    if packet.get("retrieval", {}).get("status") == "context-budget":
        return []  # An unadmitted selection is not completed question progress.
    from . import scoped_retrieval, indexed_retrieval, question_retrieval, lane_retrieval, bounded_retrieval, purpose_retrieval, compact_retrieval
    contract = compact_retrieval if packet["retrieval"]["version"] == compact_retrieval.VERSION else purpose_retrieval if packet["retrieval"]["version"] == purpose_retrieval.VERSION else bounded_retrieval if packet["retrieval"]["version"] == bounded_retrieval.VERSION else lane_retrieval if packet["retrieval"]["version"] == lane_retrieval.VERSION else question_retrieval if packet["retrieval"]["version"] == question_retrieval.VERSION else indexed_retrieval if packet["retrieval"]["version"] == indexed_retrieval.VERSION else scoped_retrieval
    decision = contract.validate_decision(trace[-1]["decision"])
    if decision["action"] == "read":
        return []  # An exhausted read budget is not a completed per-question assessment.
    index = packet.get("questionEvidenceIndex", [])
    facts = {row["id"]: row for row in packet["facts"]}
    contract.validate_scope(decision, {"questionIndex": index, "questionEvidencePolicy": VERSION}, facts)
    if packet.get("researchScope") != contract.research_scope(trace, packet["retrieval"]["status"]):
        raise ValueError("question progress must match the frozen read scope")
    captured = {row["caseId"]: row for row in index}
    result = []
    for scope in decision["questionScopes"]:
        original = captured[scope["caseId"]]
        # Legacy cases without a contract remain untouched until source planning
        # establishes their original period/criterion through the existing path.
        if not original.get("questionContract") or type(original.get("revision")) is not int:
            continue
        refs = [{"factId": key, "contentHash": content_hash(facts[key]),
                 "materialHash": evidence_change_identity({"facts": [facts[key]]}, [])}
                for key in sorted(scope["selectedFactIds"])]
        identity = content_hash({"version": VERSION, "caseId": scope["caseId"], "contract": original["questionContract"],
            "sourceAnswer": original.get("sourceAnswer", {}).get("sourceIdentity"), "status": scope["status"],
            "references": [{key: row[key] for key in ("factId", "materialHash")} for row in refs]})
        result.append({"caseId": scope["caseId"], "expectedRevision": original["revision"],
            "questionContract": deepcopy(original["questionContract"]), "progress": {"version": VERSION,
                "progressKey": identity, "status": scope["status"], "missingRequirements": deepcopy(scope["missingEvidence"]),
                "selectedFactIds": sorted(scope["selectedFactIds"]), "references": refs,
                "sourceSnapshotId": packet["sourceSnapshotId"], "executionInputId": trace[-1]["inputId"],
                **({"workKey": original["questionWorkKey"]} if original.get("questionWorkKey") else {}),
                "resumeOn": ["changed-source-evidence", "changed-question-contract"],
                "authority": "read-progress-only", "qualification": "not-an-answer-or-thesis"}})
    return result
