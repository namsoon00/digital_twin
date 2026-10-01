"""One audited entry point for model execution, including existing domain workflows."""
import hashlib
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import lru_cache

from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore


CURRENT_TASK = ContextVar("ai_control_task", default="")
CURRENT_INPUT = ContextVar("ai_control_input", default="")


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
    call_id = store.begin_call(workload, hashlib.sha256(prompt.encode()).hexdigest(), CURRENT_TASK.get(), CURRENT_INPUT.get())
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
        store.finish_call(call_id, type(error).__name__)
        raise
    else:
        store.finish_call(call_id)


def execute_ai_work(workload, prompt, invoke, settings=None):
    with ai_execution(workload, prompt, settings):
        result = invoke()
        if getattr(result, "returncode", 0):
            raise RuntimeError("AI process failed")
        return result
