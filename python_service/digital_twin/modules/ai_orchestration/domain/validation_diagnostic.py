"""Stable, bounded failure codes; exception text never enters the work ledger."""
from .execution_resilience import exception_diagnostic


PLAN_REASONS = {
    "AI plan must be an object": "plan-shape",
    "AI plan contains unsupported fields": "plan-fields",
    "AI plan must cite current packet facts": "evidence-reference",
    "AI must explicitly decide whether a useful notification is warranted": "notification-shape",
    "AI must explain notification novelty or silence": "notification-reason",
    "at most two research questions are allowed": "question-count",
    "research questions must name an allowed capability": "question-capability",
    "invalid research question": "question-shape",
    "at most one hypothesis development question is allowed": "development-count",
    "current verified graph facts unavailable": "current-facts-unavailable",
    **{"AI plan requires bounded " + key: "narrative-shape" for key in
       ("summary", "hypothesis", "counterEvidence", "comparison")},
}


def task_failure(stage, error):
    execution = exception_diagnostic(error)
    if execution:
        return "ai-execution:" + execution["category"], {"stage": stage, "category": "execution", "execution": execution}
    known = PLAN_REASONS.get(str(error)) if isinstance(error, ValueError) else None
    code = "validation:" + known if known else type(error).__name__
    return code, {"version": "ai-task-failure-v1", "stage": stage,
                  "category": "data" if known == "current-facts-unavailable" else "validation" if isinstance(error, ValueError) else "application",
                  "reasonCode": known or "unclassified", "rawErrorRetained": False}


def quality_diagnostics(result, errors, stage="local-validation"):
    diagnostics = []
    condition = result.get("conditionValidation")
    if condition:
        diagnostics.append(condition)
    for error in errors:
        category, code = "validation", "grounding-check"
        if "내부 조회" in error:
            category, code = "data", "retrieval-incomplete"
        elif "사용 가능한 근거" in error:
            category, code = "data-or-reference", "evidence-not-usable"
        elif "기울기 근거" in error:
            category, code = "data", "slope-evidence-missing"
        elif "확인 조건과 기간" in error:
            category, code = "condition", "followup-unavailable"
        elif "새로운 의미" in error or "다른 설명 근거" in error:
            category, code = "novelty", "no-new-meaning"
        elif "설명 또는 근거" in error or "계약" in error or "완전하지" in error:
            category, code = "output", "incomplete-output"
        item = {"stage": stage, "category": category, "reasonCode": code}
        if item not in diagnostics:
            diagnostics.append(item)
    return diagnostics
