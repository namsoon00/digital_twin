"""Execution limits are scheduling state, not failed investment analysis."""
from datetime import datetime, timedelta, timezone

from digital_twin.modules.ai_orchestration.domain.planning import bounded


class AIControlBudgetWait(RuntimeError):
    def __init__(self, kind, reset_at):
        self.code = "ai-" + kind + "-budget-exhausted"
        self.reset_at = reset_at
        super().__init__(self.code)

    def result(self):
        return {"status": "budget-wait", "reason": self.code, "nextCheckAt": self.reset_at}


def budgets_enabled(settings):
    return str(settings.get("aiControlBudgetEnabled", "true")).lower() not in {"false", "0", "off"}


def budget_state(settings, tasks_used, calls_used, now=None):
    now = now or datetime.now(timezone.utc)
    reset = (now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
             + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    task_limit = bounded(settings.get("aiControlDailyTaskBudget"), 48, 0, 200)
    call_limit = bounded(settings.get("aiControlDailyCallBudget"), 24, 0, 300)
    limited = budgets_enabled(settings)
    return {"budgetEnabled": limited, "tasksStartedToday": int(tasks_used), "modelCallsUsedToday": int(calls_used),
            "dailyTaskBudget": task_limit, "dailyCallBudget": call_limit,
            "tasksRemainingToday": max(0, task_limit - int(tasks_used)) if limited else None,
            "modelCallsRemainingToday": max(0, call_limit - int(calls_used)) if limited else None,
            "budgetResetAt": reset}


def admission_wait(state, capability="observe"):
    if not state["budgetEnabled"]:
        return None
    if not state["tasksRemainingToday"]:
        return AIControlBudgetWait("task", state["budgetResetAt"])
    # Leave capacity for the independent critique before starting a draft.
    # begin_call still enforces the hard limit when workers race for capacity.
    if state["modelCallsRemainingToday"] < (2 if capability == "observe" else 1):
        return AIControlBudgetWait("call", state["budgetResetAt"])
    return None
