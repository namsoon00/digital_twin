"""Secret-free operational usage counters for local AI executions."""

from typing import Any, Dict


def ai_execution_usage(execution_audit: Any) -> Dict[str, int]:
    totals = {
        "model_call_count": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    spans = execution_audit.get("executionSpans") if isinstance(execution_audit, dict) else {}
    attempts = spans.get("modelAttempts") if isinstance(spans, dict) else []
    for attempt in attempts or []:
        model_output = attempt.get("modelOutput") if isinstance(attempt, dict) else {}
        usage = model_output.get("usage") if isinstance(model_output, dict) else {}
        if not isinstance(usage, dict):
            continue
        totals["model_call_count"] += 1
        for key in (
            "input_tokens", "cached_input_tokens", "output_tokens",
            "reasoning_output_tokens",
        ):
            try:
                totals[key] += max(0, int(usage.get(key) or 0))
            except (TypeError, ValueError):
                continue
    return totals
