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
        from digital_twin.modules.outcomes.contracts import RESEARCH_EXPECTATION_TERMINAL
        category, state = "business", [status, observations]
        label = {"superseded": "사업 가설 수정", "retired": "사업 가설 종료"}.get(status, "사업 지표 확인 상태 변경")
        if status not in {"superseded", "retired"} and not any(
                value in {"direction-observed", "direction-not-observed"} | RESEARCH_EXPECTATION_TERMINAL for value in observations):
            return None
    elif stage == "created" and case.get("capability") in {"research", "develop-hypothesis"}:
        category, state, label = "question", status, "답변에서 이어진 후속 질문" if case.get("inquiryLineage") else "연구 질문 등록"
    elif stage == "assessment" and status in {"answered", "blocked", "dismissed"}:
        category, state = "question", status
        label = {"answered": "연구 답변 검토", "blocked": "연구 진행 보류", "dismissed": "연구 질문 종료"}[status]
    elif stage == "research-planned":
        plan = event.get("details") or {}
        task_id = plan.get("scheduledTaskId")
        if (plan.get("action") != "research" or not task_id
                or task_id != (case.get("lastResearch") or {}).get("taskId")):
            return None
        from .research_repeat import materials
        # A new planner/task/route is execution bookkeeping, not new research.
        # Keep the last announced material set across plans and source outages.
        markers = case.setdefault("researchProgressMarkers", {})
        scope = identity(case.get("questionContract") or case.get("question", ""))
        current = set(materials(case))
        previous = markers.get("followupMaterial")
        markers["followupMaterial"] = {"contract": scope,
            "materials": sorted(current | set((previous or {}).get("materials", [])))[:256]}
        if previous and previous["contract"] == scope and not current - set(previous["materials"]):
            return None
        if not previous and markers.get("source-followup"):
            return None  # Seed legacy announcements without a migration notice.
        category, state, label = "source-followup", [scope, sorted(current)], "부족한 자료 추가 조사 예약"
    elif stage == "research-returned":
        result = case.get("lastResearch", {}).get("result", {})
        answer = result.get("documentaryAnswer") or {}
        if answer:
            # Text, requirement regrouping and immutable fetch IDs do not prove
            # progress. A newly completed answer is meaningful even on old facts.
            material = sorted({row["materialFingerprint"] for row in answer.get("sources", [])
                               if row.get("materialFingerprint")})
            answer_status = answer.get("status")
            markers = case.setdefault("researchProgressMarkers", {})
            previous = markers.get("answerMaterial")
            if previous:
                if answer_status == previous["status"] and not set(material) - set(previous["materials"]):
                    return None
                if answer_status == "unavailable" and previous["status"] in {"partial", "answered"}:
                    return None  # Collection failure is not loss of known facts.
            elif markers.get("answer") and answer_status != "answered":
                # Seed legacy state without replaying its last customer update.
                markers["answerMaterial"] = {"status": answer_status, "materials": material}
                return None
            markers["answerMaterial"] = {"status": answer_status,
                "materials": sorted(set(material) | set((previous or {}).get("materials", [])))[:256]}
            category, state = "answer", [answer_status, material]
            label = {"answered": "연구 질문 답변", "partial": "연구 질문 부분 답변"}.get(answer.get("status"), "연구 답변 미확보 · 원인 확인")
        elif result.get("status") in {"failed", "research-cooldown"}:
            return None  # Transport/cooldown churn is not research progress.
        elif not result.get("changedEvidenceCount", 0):
            return None
        else:
            category, state, label = "collection", "returned", "연구 자료 갱신 · 답변 검토 대기"
    elif stage == "review-deferred" and case.get("reviewBlocker"):
        # Draft validation is an operational incident, not a new research
        # finding. Keep it in the case/event audit without repeated customer pushes.
        return None
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
    quantitative_checks = [{**{key: check[key] for key in ("contractVersion", "meaning", "targetPeriodStart",
        "targetPeriodEnd", "expectedLow", "expectedHigh")}, "baseline": {"basis": check["baseline"]["basis"]}}
        for check in contract.get("checkpoints", [])[:3]
        if check.get("contractVersion") == "quantitative-business-checkpoint-v1"]
    quantitative_actuals = [{"metric": row["metric"], "actual": row["current"]["value"],
        "currency": row["current"]["basis"]["currency"], "rangeDeviation": row["rangeDeviation"],
        "targetPeriodEnd": row["targetPeriodEnd"]}
        for row in case.get("observations", [])[:3]
        if row.get("contractVersion") == "quantitative-business-checkpoint-v1" and row.get("current")]
    return {
        "version": "research-progress-v1", "eventId": event["eventId"],
        **{key: case[key] for key in ("caseId", "accountId", "symbol", "worldId")},
        "name": case.get("name", ""),
        "taskId": event["taskId"], "revision": event["revision"], "at": event["at"],
        "stage": stage, "status": status, "label": label, "question": case.get("question", ""),
        "reason": case.get("reason", ""), "nextCheckAt": case.get("nextCheckAt", ""),
        "contract": deepcopy({key: contract[key] for key in (
            "mechanism", "assumption", "alternative", "invalidation", "missingEvidence", "horizonDays") if key in contract}
            | ({"checkpoints": quantitative_checks} if quantitative_checks else {})),
        "evidenceIds": list(review.get("evidenceIds") or contract.get("evidenceIds") or [])[:12],
        "sourceQuestionIds": [row["caseId"] for row in case.get("origin", {}).get("sourceQuestions", [])],
        "observations": observations, "authority": "research-status-only",
        **({"inquiryLineage": deepcopy({key: case["inquiryLineage"][key] for key in
            ("parentCaseId", "parentAnswer", "depth", "relation", "rationale", "expectedInsight")})}
           if case.get("inquiryLineage") else {}),
        **({"quantitativeActuals": quantitative_actuals} if quantitative_actuals else {}),
        "qualification": "not-empirically-qualified",
        "documentaryAnswer": deepcopy(case.get("lastResearch", {}).get("result", {}).get("documentaryAnswer") or {}) if stage == "research-returned" else {},
        "reviewBlocker": deepcopy(case.get("reviewBlocker") or {}) if stage == "review-deferred" else {},
        **({"sourceFollowUp": {"reason": (event.get("details") or {}).get("reason", ""),
            "missingRequirements": (event.get("details") or {}).get("missingRequirements", []),
            "resumeWhen": (event.get("details") or {}).get("resumeWhen", "")}}
           if stage == "research-planned" else {}),
    }
