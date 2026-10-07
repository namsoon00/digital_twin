"""One audited entry point for model execution, including existing domain workflows."""
import hashlib
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
        diagnostic = exception_diagnostic(error)
        if diagnostic:
            metrics["failure"] = diagnostic
        try:
            finish(error.code if isinstance(error, AIExecutionError) else type(error).__name__)
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
        diagnostic = process_diagnostic(result)
        if diagnostic:
            raise AIExecutionError(diagnostic)
        return result
