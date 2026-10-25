"""Read-only question milestones; reservations and retries are never answers."""
from copy import deepcopy

from .execution_resilience import recovery_wait, FAILURE_MESSAGES
from .question_assessment import VERSION as ASSESSMENT_VERSION


def task_wait(task, execution, now):
    if task.get("status") not in {"pending", "processing"}:
        return {}
    shared = recovery_wait({**execution, "lastFailureCategory": execution.get("lastFailureCategory")
                            or (execution.get("lastFailure") or {}).get("category")}, now)
    if shared:
        return {**shared.result(), "label": "AI 제공처 사용 한도 대기" if shared.category == "quota" else "AI 제공처 실행 대기"}
    error = task.get("lastError", "")
    if error in {"ai-token-budget-exhausted", "ai-token-allocation-budget-exhausted"}:
        return {"status": "budget-wait", "scope": "project-budget", "reason": error,
            "label": "서비스 내부 토큰 예산 대기", "explanation": "내부 사용 예산이 배정되면 같은 작업을 이어갑니다.",
            "nextCheckAt": task.get("nextCheckAt", ""), "retryIsReset": False}
    if error == "ai-execution:recovery-wait":
        return {"status": "retry-scheduled", "scope": "model-provider", "reason": error,
            "label": "AI 실행 재확인 예정", "explanation": "앞선 실행 오류 후 같은 작업의 재시도를 기다립니다. 현재 한도 회복은 아직 확인하지 못했습니다.",
            "nextCheckAt": task.get("nextCheckAt", ""), "retryIsReset": False}
    if error.startswith("ai-execution:"):
        category = error.partition(":")[2]
        return {"status": "execution-error", "scope": "model-provider", "reason": error,
            "label": "AI 실행 재시도 대기", "explanation": FAILURE_MESSAGES.get(category, FAILURE_MESSAGES["process-failed"]),
            "nextCheckAt": task.get("nextCheckAt", ""), "retryIsReset": False}
    return {}


def question_progress(case, tasks, execution, now):
    def linked(key, research=False):
        reference = (case.get(key) or {}).get("taskId")
        task = tasks.get(reference) or {}
        if (not task or any(task.get(k) != case.get(k) for k in ("accountId", "symbol", "worldId"))
                or research and (task.get("brainCaseId") != case["caseId"] or task.get("capability") != "research")):
            return {}
        return task

    source = linked("lastResearch", True)
    planner = linked("researchPlan")
    judgment = linked("questionAssessment")
    assessment = case.get("questionAssessment") or {}
    assessed = bool(judgment.get("status") == "completed" and judgment.get("hasSummary")
        and assessment.get("version") == ASSESSMENT_VERSION and assessment.get("researchReviewInputId")
        and assessment.get("questionContract") == case.get("questionContract"))
    answer = case.get("latestSourceAnswer") or (case.get("lastResearch") or {}).get("result", {}).get("documentaryAnswer") or {}
    known = len((assessment.get("sources") if assessed else None) or answer.get("sources") or [])
    active = next((t for t in (source, planner) if t.get("status") in {"pending", "processing"}), {})
    wait = task_wait(active, execution, now)
    if not wait and case.get("status") in {"open", "waiting", "review-needed", "blocked"}:
        # The shared gate concerns the next model call, not non-model collection.
        wait = task_wait({"status": "pending"}, execution, now)
    answer_status = assessment.get("status") if assessed else answer.get("status", "pending")
    disposition = assessment.get("hypothesisDisposition") if assessed else ""
    stages = [
        {"key": "sources", "label": "자료 확보", "status": "recorded" if known else "pending",
         "detail": f"질문에 연결된 근거 {known}건" if known else "질문에 연결된 답변 근거가 아직 없습니다."},
        {"key": "answer", "label": "질문 답변", "status": answer_status,
         "detail": {"answered": "최종 AI 답변 기록" if assessed else "자료 조사 답변 확보 · 최종 검토 대기",
                    "partial": "부분 답변 보존 · 남은 항목 확인 필요", "unavailable": "앞선 조사에서 답변 미확보",
                    "dismissed": "질문 검토 종료"}.get(answer_status, "답변 작성 대기")},
        {"key": "hypothesis", "label": "가설 평가", "status": "recorded" if disposition else "pending",
         "detail": {"thesis": "사업 가설에 연결", "experiment": "검증 실험 제안", "defer": "평가 보류",
                    "answer": "질문 답변으로 정리", "dismiss": "검토 종료"}.get(disposition, "평가 기록 확인 필요")},
        {"key": "judgment", "label": "AI 판단 반영", "status": "recorded" if assessed else "pending",
         "detail": (assessment.get("judgmentImpact") or {}).get("reason", "") if assessed else "검토를 통과한 판단 연결이 아직 없습니다."},
    ]
    return {"version": "question-progress-v1", "checkedAt": now.isoformat(), "stages": stages,
        "activeTaskId": active.get("taskId", ""), "wait": wait,
        "publication": deepcopy(judgment.get("publication") or {}) if assessed else {},
        "judgmentTaskId": judgment.get("taskId", "") if assessed else "",
        "answerComplete": assessed and assessment.get("status") == "answered",
        "qualification": "operational-progress-not-empirical-validation"}
