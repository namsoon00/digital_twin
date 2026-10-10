"""A cited source review is an answer artifact, not a market hypothesis verdict."""
from copy import deepcopy
from urllib.parse import urlsplit


BLOCKERS = {
    "planner-disabled": "자료 답변 검토 기능이 설정에서 꺼져 있습니다.",
    "planner-unavailable": "자료 답변 검토기가 연결되지 않았습니다.",
    "planner-failed": "자료 답변 검토 실행에 실패했습니다.",
    "awaiting-evidence": "질문에 사용할 수 있는 출처 자료가 없습니다.",
    "configuration-required": "공식 원문 접근 설정이 없어 보고서를 읽지 못했습니다.",
    "document-unavailable": "공식 원문을 가져오지 못했습니다.",
    "no-matching-passages": "가져온 원문에서 질문에 해당하는 내용을 찾지 못했습니다.",
    "deferred": "자료 제공처의 요청 제한으로 수집을 보류했습니다.",
    "cooldown": "자료 수집 재시도 간격이 아직 지나지 않았습니다.",
}


def documentary_answer(run, task_id):
    rows = [row for row in run.get("taskAssessments", []) if row.get("taskId") == task_id]
    row = rows[0] if len(rows) == 1 else {}
    history = run.get("roundHistory") or []
    latest = history[-1] if history else {}
    recorded = [item for item in latest.get("taskAssessments", []) if item.get("taskId") == task_id]
    packets = {item["evidenceId"]: item for item in latest.get("evidencePackets", [])}
    cited = list(dict.fromkeys((row.get("resultEvidenceIds") or []) + (row.get("counterEvidenceIds") or [])))
    bound = bool(row and recorded == [row] and row.get("assessmentFingerprint") and cited
                 and all(key in packets for key in cited) and row.get("reason"))
    reviewed = bound and row.get("semanticReviewState") in {"addressed", "partial", "unresolved"}
    complete = (reviewed and row.get("status") == "addressed" and row.get("semanticReviewState") == "addressed"
                and row.get("coverageState") == "complete" and not row.get("missingRequirements"))
    status = "answered" if complete else "partial" if reviewed and row.get("semanticReviewState") != "unresolved" else "unavailable"
    blockers = list(dict.fromkeys(BLOCKERS[item.get("status")] for item in
        [latest.get("planningAudit") or {}, *(run.get("providerStatuses") or [])]
        if item.get("status") in BLOCKERS))[:4]
    excluded = {reason for item in row.get("excludedEvidence", []) for reason in item.get("reasons", [])}
    rejected = {reason for item in run.get("rejectedClaims", []) for reason in item.get("reasons", [])}
    if "stale-for-task" in excluded or "evidence-stale" in rejected:
        blockers.append("보고서 발행일이 질문에 설정된 조회 기간 밖입니다. 비교 기간을 포함하도록 조사 조건을 보완해야 합니다.")
    if not reviewed:
        if row.get("reviewRejected"):
            blockers.insert(0, "검토의 인용 또는 자료 버전이 맞지 않아 답변으로 채택하지 않았습니다.")
        elif not blockers:
            blockers.append("수집 자료에 대한 유효한 인용 답변을 확보하지 못했습니다.")
    sources = []
    if reviewed:
        for key in cited[:8]:
            packet = packets[key]
            url = str(packet.get("sourceUrl") or "")
            try:
                parsed = urlsplit(url)
                safe = parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
            except ValueError:
                safe = False
            sources.append({"evidenceId": key, "sourceUrl": url if safe else "",
                **{name: str(packet.get(name) or "")[:160] for name in (
                    "title", "publishedAt", "periodEnd", "reportDate", "inputFingerprint")}})
    return {"version": "documentary-answer-v1", "taskId": task_id, "status": status,
        "authority": "source-review-only", "assessmentFingerprint": str(row.get("assessmentFingerprint") or ""),
        "text": str(row.get("reason") or "")[:600] if reviewed else " ".join(blockers),
        "missingRequirements": deepcopy(row.get("missingRequirements") or [])[:8],
        "blockers": blockers, "sources": sources, "assessedAt": str(row.get("assessedAt") or "")}
