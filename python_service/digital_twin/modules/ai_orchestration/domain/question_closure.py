"""A bounded read model of existing answers, gaps and resumption conditions."""
from collections import Counter
from copy import deepcopy
from .research_repeat import admission
from .research_work import source_answer_complete
from .question_answer import VERSION as ANSWER_VERSION, CAUSE_LABELS


def execution_state(case, active, wait, stop, complete):
    """Collection/queue status is independent of how much was answered."""
    plan = case.get("researchPlan") or {}
    if complete or case.get("status") in {"answered", "dismissed"}:
        code, label = "closed", "질문 처리 종료"
    elif active:
        code = wait.get("status") or ("running" if active.get("status") == "processing" else "scheduled")
        label = wait.get("label") or ("기존 작업 처리 중" if code == "running" else "기존 작업 예약 대기")
    elif stop in {"research-attempt-limit", "research-material-limit"}:
        code, label = "limit-reached", "자동 조사 한도 도달"
    elif stop == "source-state-unavailable":
        code, label = "source-unavailable", "자료 조회 경로 확인 필요"
    elif case.get("status") == "review-needed" or source_answer_complete(case):
        code, label = "review-pending", "확보한 답변의 최종 검토 대기"
    elif case.get("status") == "blocked" or plan.get("action") == "blocked":
        code, label = "blocked", "조사 진행 불가 · 원인 확인 필요"
    elif stop == "unchanged-research-material":
        code, label = "unchanged-material", "새 근거 대기 · 같은 자료 재조사 중지"
    else:
        code, label = "waiting", "추가 근거 또는 조사 계획 대기"
    return {"code": code, "label": label, "reason": stop,
            "activeTaskId": active.get("taskId", ""), "nextCheckAt": active.get("nextCheckAt", "")}


def closure(case, answer, assessment, assessed, active, wait):
    source = assessment if assessed else answer
    plan = case.get("researchPlan") or {}
    gaps = source.get("missingRequirements")
    if not isinstance(gaps, list):
        gaps = (case.get("evidenceProgress") or {}).get("missingRequirements") or []
    complete = assessed and assessment.get("status") == "answered"
    stop = admission(case, "planned") if case.get("researchRepeat") else ""
    text = source.get("answer") if assessed else source.get("text")
    parts = (assessment.get("answerParts") or {}) if assessed and assessment.get("answerPartsVersion") == ANSWER_VERSION else {}
    confirmed = deepcopy(parts.get("confirmed", []))
    has_answer = bool(text and source.get("sources") and source.get("status") in {"answered", "partial"})
    answer_state, answer_label = (
        ("complete", "원래 질문의 답변 완료") if complete else
        ("dismissed", "질문 검토 종료") if case.get("status") == "dismissed" else
        ("confirmed-parts", "확인된 범위의 답변 · 전체 질문 미완료") if confirmed else
        ("recorded-partial", "일부 답변 기록 · 전체 질문 미완료") if assessed and has_answer and not parts else
        ("source-answer", "자료 조사 답변 확보 · 최종 검토 대기") if not assessed and has_answer else
        ("unavailable", "답변 미확보") if source.get("status") in {"unavailable", "partial"} else
        ("pending", "답변 작성 대기"))
    execution = execution_state(case, active, wait, stop, complete)
    stopped = stop in {"research-attempt-limit", "research-material-limit"}
    next_step = ("답변 완료 · 새 근거가 생기면 재검토" if complete else
        "질문 검토 종료" if case.get("status") == "dismissed" else
        "기존 작업에서 이어서 처리" if active else
        "자동 조사 한도 도달 · 조사 범위 검토 필요" if stopped else
        "확보한 답변을 원래 질문 기준으로 최종 검토" if source_answer_complete(case) else
        "새 자료 또는 지원 경로를 기다림" if stop else "남은 항목의 조사 계획 확인")
    return {"version": "question-closure-v2", "answerStatus": source.get("status", "pending"),
        "answerState": answer_state, "answerLabel": answer_label, "execution": execution,
        "confirmedParts": confirmed, "gapDetails": [{**deepcopy(gap), "requirement": gaps[gap["requirementIndex"]],
            "causeLabel": CAUSE_LABELS.get(gap["cause"], "검증 필요")}
            for gap in parts.get("gaps", []) if type(gap.get("requirementIndex")) is int and 0 <= gap["requirementIndex"] < len(gaps)],
        "recordedAnswer": str(text or "")[:2400] if has_answer and (not parts or confirmed or complete) else "",
        "answerAuthority": "reviewed-answer" if assessed else "source-answer",
        "missingRequirements": list(dict.fromkeys(str(x)[:600] for x in gaps[:8])) if not complete else [],
        "nextStep": next_step, "resumeWhen": str(("자동 재시도 한도를 소진했습니다. 새 자료만으로 같은 조사가 다시 실행되지는 않습니다." if stopped else plan.get("resumeWhen")) or
            ("원래 질문에 필요한 새 원문·수치 또는 지원 경로가 확인되면 재검토" if stop else ""))[:500],
        "stopReason": stop, "activeTaskId": active.get("taskId", ""), "executionWait": wait.get("label", ""),
        "judgmentEffect": (assessment.get("judgmentImpact") or {}).get("effect", "unknown") if assessed else "unknown",
        "qualification": "recorded-evidence-progress-not-investment-accuracy"}


def summarize(cases):
    rows = [c["workflow"] for c in cases if c.get("workflow")]
    assessed = [w for w in rows if w.get("judgmentTaskId")]
    effects = [w.get("closure", {}).get("judgmentEffect", "unknown") for w in assessed]
    delivered = {w["publication"]["jobId"] for w in rows if w.get("publication", {}).get("jobId")
        and w["publication"].get("receipt", {}).get("source") == "transport-receipt"
        and w["publication"]["receipt"].get("deliveredAt")}
    return {"version": "question-performance-v1", "scope": "visible-question-sample", "sampleCount": len(rows),
        "finalAnswers": sum(w.get("answerComplete") is True for w in rows),
        "reviewedJudgmentLinks": len(assessed), "judgmentChanged": effects.count("weakens"),
        "judgmentSupported": effects.count("supports"), "judgmentUnchanged": effects.count("unchanged"),
        "judgmentInsufficient": effects.count("insufficient"),
        "providerWaiting": sum(w.get("wait", {}).get("scope") == "model-provider" for w in rows),
        "confirmedPartialAnswers": sum(w.get("closure", {}).get("answerState") == "confirmed-parts" for w in rows),
        "executionStates": dict(Counter(w.get("closure", {}).get("execution", {}).get("code", "unknown") for w in rows)),
        "partialAnswersRetained": sum(w.get("closure", {}).get("answerStatus") == "partial" for w in rows),
        "receiptVerifiedNotifications": len(delivered), "investmentAccuracy": None,
        "qualification": "sample-operational-progress-not-causal-impact-or-investment-accuracy"}
