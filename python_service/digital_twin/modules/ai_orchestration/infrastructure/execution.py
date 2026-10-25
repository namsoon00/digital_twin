"""One audited entry point for model execution, including existing domain workflows."""
import hashlib
import json
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import lru_cache
import time

from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
from digital_twin.modules.ai_orchestration.domain.execution_resilience import AIExecutionError, process_diagnostic, exception_diagnostic


CURRENT_TASK = ContextVar("ai_control_task", default="")
CURRENT_INPUT = ContextVar("ai_control_input", default="")
CURRENT_CALL_METRICS = ContextVar("ai_control_call_metrics", default=None)


def current_execution_metrics():
    return CURRENT_CALL_METRICS.get()


def record_execution_usage(stdout):
    """Read only numeric terminal usage; never persist prompt/model output."""
    from ..domain.token_budget import usage_counts
    metrics = current_execution_metrics()
    if metrics is None:
        return
    text = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else str(stdout or "")
    totals, turns, open_turns, failed_turns, complete = {}, 0, 0, 0, True
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            continue
        if event.get("type") == "turn.started":
            open_turns += 1
        if event.get("type") not in {"turn.completed", "turn.failed"}:
            continue
        turns += 1
        open_turns = max(0, open_turns - 1)
        failed_turns += event["type"] == "turn.failed"
        counts = usage_counts(event.get("usage"))
        if counts is None:
            complete = False
        else:
            for key, value in counts.items():
                totals[key] = totals.get(key, 0) + value
    complete = bool(turns and complete and not open_turns)
    metrics["usage"] = totals if complete else None
    # Explicit terminal zero usage is evidence. A quota error string is not.
    # One unmeasured failed turn must not disappear behind earlier successes.
    metrics["usageEvidence"] = {"version": "terminal-usage-v2", "terminalTurns": turns,
        "failedTurns": failed_turns, "complete": complete}


@lru_cache(maxsize=4)
def _store(settings_items):
    return MySQLAIControlStore(dict(settings_items))


@contextmanager
def ai_execution(workload, prompt="", settings=None, store=None):
    configured = settings or {}
    if store is None:
        from digital_twin.infrastructure.settings import runtime_settings
        configured = settings if settings is not None else runtime_settings()
        store = _store(tuple(sorted((key, str(value)) for key, value in configured.items())))
    ticket = store.acquire_execution()
    try:
        call_id = store.begin_call(workload, hashlib.sha256(prompt.encode()).hexdigest(), CURRENT_TASK.get(), CURRENT_INPUT.get())
    except BaseException:
        store.finish_execution(ticket, "cancelled")
        raise
    metrics = {}
    token = CURRENT_CALL_METRICS.set(metrics)
    started = time.monotonic()
    def finish(error_kind=""):
        if metrics:
            metrics["totalMs"] = round((time.monotonic() - started) * 1000, 3)
            if metrics.get("stage") == "capacity-wait":
                metrics["capacityWaitMs"] = metrics["totalMs"]
            store.finish_call(call_id, error_kind, metrics=metrics)
        else:
            store.finish_call(call_id, error_kind) if error_kind else store.finish_call(call_id)
    try:
        capacity = nullcontext()
        if workload == "interactive-chat":
            from digital_twin.infrastructure.local_ai_process_guard import local_ai_capacity_lease
            from digital_twin.infrastructure.settings import data_dir
            from digital_twin.modules.ai_orchestration.domain.planning import bounded
            capacity = local_ai_capacity_lease(data_dir() / "local-ai-capacity",
                max_concurrent=bounded(configured.get("localAiMaxConcurrentProcesses"), 2, 1, 8),
                wait_seconds=30, lane="background", reserved_priority_slots=1)
        with capacity:
            yield call_id
    except BaseException as error:
        from digital_twin.infrastructure.local_ai_process_guard import LocalAICapacityUnavailable
        if isinstance(error, LocalAICapacityUnavailable):
            metrics["processNotStarted"] = True
        diagnostic = exception_diagnostic(error)
        if diagnostic:
            metrics["failure"] = diagnostic
        try:
            finish("ai-execution:" + diagnostic["category"] if diagnostic else type(error).__name__)
        finally:
            store.finish_execution(ticket, "failure", diagnostic)
        raise
    else:
        try:
            finish()
        finally:
            store.finish_execution(ticket, "success")
    finally:
        CURRENT_CALL_METRICS.reset(token)


def execute_ai_work(workload, prompt, invoke, settings=None):
    with ai_execution(workload, prompt, settings):
        result = invoke()
        record_execution_usage(getattr(result, "stdout", ""))
        diagnostic = process_diagnostic(result)
        if diagnostic:
            raise AIExecutionError(diagnostic)
        return result
