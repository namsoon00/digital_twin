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

# Only developer-authored messages are admitted; arbitrary exception text must
# never become a diagnostic or a model correction instruction.
COMPONENT_REASONS = {
    "businessResearch": (
        "invalid business research contract", "business research requires bounded actionless explanation",
        "business research requires captured evidence", "business research exceeds work budget",
        "incomplete business thesis", "business thesis cannot be based on prices alone",
        "invalid business horizon", "invalid business checkpoints", "invalid report checkpoint",
        "checkpoint must bind an exact reported metric", "same-season checkpoint requires report duration and annual horizon",
        "checkpoint baseline must be latest comparable report", "unmeasurable thesis must state missing evidence",
        "invalid business review", "business review requires captured thesis", "business review scope mismatch",
        "revision requires a replacement thesis", "at most two active business contracts; review existing contracts first",
        "changed business thesis requires explicit review",
    ),
    "caseReviews": (
        "brain management requires captured evidence", "reference-only facts cannot answer a case",
        "brain management exceeds its bounded work budget", "invalid case review contract",
        "case review must own one captured scoped case", "brain memory scope mismatch",
        "only documentary questions may repeat source research", "due brain cases require an explicit review",
        "invalid service feedback contract",
    ),
    "questionResolutions": (
        "invalid question resolutions", "invalid question resolution fields", "resolution requires one reviewed question",
        "question resolution scope mismatch", "question resolution requires a reason",
        "question resolution requires current evidence", "reference-only evidence cannot originate a hypothesis",
        "question resolution target missing", "unresolved work cannot simultaneously advance to a hypothesis",
        "deferred question cannot name a target", "reviewed research questions require a hypothesis disposition",
    ),
    "research": (
        "invalid research request fields", "invalid research request types", "non-documentary work cannot request source research",
        "research request exceeds bounds", "unsupported research source", "invalid public research query",
    ),
}
COMPONENT_FIELDS = {message: field for field, messages in COMPONENT_REASONS.items() for message in messages}
PLAN_REASONS.update({message: message.replace(";", "").replace(" ", "-") for message in COMPONENT_FIELDS})
for _key in ("reason", "problem", "proposal", "verification"):
    _message = "brain management requires bounded " + _key
    PLAN_REASONS[_message] = "management-text-" + _key
    COMPONENT_FIELDS[_message] = "caseReviews.reason" if _key == "reason" else "serviceFeedback." + _key


class PlanValidationError(ValueError):
    """Typed business validation details, containing no rejected source text."""
    def __init__(self, code, field, expected):
        super().__init__(code)
        self.diagnostic = {"reasonCode": code, "field": field, "expected": expected}


def correction_issue(error):
    if isinstance(error, PlanValidationError):
        return dict(error.diagnostic)
    message = str(error)
    if isinstance(error, ValueError) and message in PLAN_REASONS and message != "current verified graph facts unavailable":
        return {"reasonCode": PLAN_REASONS[message], "field": COMPONENT_FIELDS.get(message, "$"),
                "expected": message}
    return None


def task_failure(stage, error):
    execution = exception_diagnostic(error)
    if execution:
        return "ai-execution:" + execution["category"], {"stage": stage, "category": "execution", "execution": execution}
    known = PLAN_REASONS.get(str(error)) if isinstance(error, ValueError) else None
    issue = correction_issue(error)
    if issue:
        return "validation:" + issue["reasonCode"], {"version": "ai-task-failure-v2", "stage": stage,
            "category": "validation", **issue, "rawErrorRetained": False}
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
