"""A bounded read model of existing answers, gaps and resumption conditions."""
from .research_repeat import admission
from .research_work import source_answer_complete


def closure(case, answer, assessment, assessed, active, wait):
    source = assessment if assessed else answer
    plan = case.get("researchPlan") or {}
    gaps = source.get("missingRequirements")
    if not isinstance(gaps, list):
        gaps = (case.get("evidenceProgress") or {}).get("missingRequirements") or []
    complete = assessed and assessment.get("status") == "answered"
    stop = admission(case, "planned") if case.get("researchRepeat") else ""
    text = source.get("answer") if assessed else source.get("text")
    stopped = stop in {"research-attempt-limit", "research-material-limit"}
    next_step = ("답변 완료 · 새 근거가 생기면 재검토" if complete else
        "질문 검토 종료" if case.get("status") == "dismissed" else
        "기존 작업에서 이어서 처리" if active else
        "자동 조사 한도 도달 · 조사 범위 검토 필요" if stopped else
        "확보한 답변을 원래 질문 기준으로 최종 검토" if source_answer_complete(case) else
        "새 자료 또는 지원 경로를 기다림" if stop else "남은 항목의 조사 계획 확인")
    return {"version": "question-closure-v1", "answerStatus": source.get("status", "pending"),
        "recordedAnswer": str(text or "")[:2400], "answerAuthority": "reviewed-answer" if assessed else "source-answer",
        "missingRequirements": [str(x)[:500] for x in gaps[:8]] if not complete else [],
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
        "partialAnswersRetained": sum(w.get("closure", {}).get("answerStatus") == "partial" for w in rows),
        "receiptVerifiedNotifications": len(delivered), "investmentAccuracy": None,
        "qualification": "sample-operational-progress-not-causal-impact-or-investment-accuracy"}
