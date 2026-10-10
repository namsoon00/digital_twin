"""Operational research milestones, never investment or empirical qualification."""
from copy import deepcopy

from .planning import identity


def research_progress(case, event):
    """Return a bounded event snapshot and remember material milestones in the case.

    Called only inside the owning case/event transaction. Clocks, retries and
    reworded reasons do not create a new milestone.
    """
    stage, status = event["stage"], case["status"]
    category, state, label = "", "", ""
    contract = case.get("contract", {})
    observations = [row.get("status", "") for row in case.get("observations", [])]
    if stage == "business-registered":
        category, state, label = "business", [status, observations], "사업 가설 등록"
    elif stage == "business-reviewed":
        category, state = "business", [status, observations]
        label = {"superseded": "사업 가설 수정", "retired": "사업 가설 종료"}.get(status, "사업 지표 확인 상태 변경")
        if status not in {"superseded", "retired"} and not any(
                value in {"direction-observed", "direction-not-observed"} for value in observations):
            return None
    elif stage == "created" and case.get("capability") in {"research", "develop-hypothesis"}:
        category, state, label = "question", status, "연구 질문 등록"
    elif stage == "assessment" and status in {"answered", "blocked", "dismissed"}:
        category, state = "question", status
        label = {"answered": "연구 답변 검토", "blocked": "연구 진행 보류", "dismissed": "연구 질문 종료"}[status]
    elif stage == "research-returned":
        result = case.get("lastResearch", {}).get("result", {})
        answer = result.get("documentaryAnswer") or {}
        if answer:
            category, state = "answer", [answer.get("status"), answer.get("text"),
                answer.get("missingRequirements"), answer.get("blockers"),
                [[row.get("evidenceId"), row.get("sourceUrl"), row.get("periodEnd"), row.get("reportDate")]
                 for row in answer.get("sources", [])]]
            label = {"answered": "연구 질문 답변", "partial": "연구 질문 부분 답변"}.get(answer.get("status"), "연구 답변 미확보 · 원인 확인")
        elif result.get("status") in {"failed", "research-cooldown"}:
            return None  # Transport/cooldown churn is not research progress.
        elif not result.get("changedEvidenceCount", 0):
            return None
        else:
            category, state, label = "collection", "returned", "연구 자료 갱신 · 답변 검토 대기"
    elif stage == "review-deferred" and case.get("reviewBlocker"):
        category, state, label = "review-blocker", case["reviewBlocker"], "가설 판단 보류 · 원인 확인"
    elif stage == "development-returned":
        progress = case.get("developmentProgress", {})
        category, state, label = "development", [progress.get("status"), [
            [row.get("caseId"), row.get("status"), row.get("stage")]
            for row in progress.get("cases", [])]], "가설 개발 진행 상태 변경"
    else:
        return None
    signature = identity(category, state)
    markers = case.setdefault("researchProgressMarkers", {})
    if markers.get(category) == signature:
        return None
    markers[category] = signature
    review = case.get("lastReview") or case.get("lastAssessment") or {}
    return {
        "version": "research-progress-v1", "eventId": event["eventId"],
        **{key: case[key] for key in ("caseId", "accountId", "symbol", "worldId")},
        "taskId": event["taskId"], "revision": event["revision"], "at": event["at"],
        "stage": stage, "status": status, "label": label, "question": case.get("question", ""),
        "reason": case.get("reason", ""), "nextCheckAt": case.get("nextCheckAt", ""),
        "contract": deepcopy({key: contract[key] for key in (
            "mechanism", "assumption", "alternative", "invalidation", "missingEvidence", "horizonDays") if key in contract}),
        "evidenceIds": list(review.get("evidenceIds") or contract.get("evidenceIds") or [])[:12],
        "sourceQuestionIds": [row["caseId"] for row in case.get("origin", {}).get("sourceQuestions", [])],
        "observations": observations, "authority": "research-status-only",
        "qualification": "not-empirically-qualified",
        "documentaryAnswer": deepcopy(case.get("lastResearch", {}).get("result", {}).get("documentaryAnswer") or {}) if stage == "research-returned" else {},
        "reviewBlocker": deepcopy(case.get("reviewBlocker") or {}) if stage == "review-deferred" else {},
    }
