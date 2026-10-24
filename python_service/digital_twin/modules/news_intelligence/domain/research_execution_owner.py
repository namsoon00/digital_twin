"""Execution ownership survives budget waits and legacy checkpoints."""


def central_research_run(run):
    value = run.to_dict() if hasattr(run, "to_dict") else run
    context = value.get("requestContext") or {}
    return (str(value.get("runId") or "").startswith("ai-control-")
            or context.get("source") == "ai-control" or bool(context.get("aiControlTaskId")))


def require_queue_owned_run(run):
    if central_research_run(run):
        raise ValueError("research-execution-owner-mismatch")
    if not isinstance((run.request_context or {}).get("brain"), dict):
        raise ValueError("research-queued-context-missing")
