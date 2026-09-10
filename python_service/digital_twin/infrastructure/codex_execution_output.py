"""Read Codex JSONL output without retaining tools, prompts or reasoning text."""

from collections import Counter
import json


def codex_execution_output(stdout):
    text = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else str(stdout or "")
    events = Counter()
    usage = {}
    final_message = ""
    tool_calls = 0
    error_codes = set()
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = str(event.get("type") or "")
        events[kind] += 1
        if kind == "turn.completed":
            source = event.get("usage") or {}
            source = source if isinstance(source, dict) else {}
            usage = {
                key: source[key] for key in (
                    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
                ) if isinstance(source.get(key), (int, float))
            }
        item = event.get("item") or {}
        if kind == "item.completed" and isinstance(item, dict):
            if item.get("type") == "agent_message":
                final_message = str(item.get("text") or "")
            elif item.get("type") in {"command_execution", "mcp_tool_call", "web_search", "file_change"}:
                tool_calls += 1
        # A bounded allowlist avoids copying vendor text or echoed prompts.
        if kind in {"error", "turn.failed"}:
            error = event.get("error") or event
            if isinstance(error, dict):
                for code in ("usage_limit_reached", "rate_limit_exceeded", "invalid_api_key", "context_length_exceeded", "invalid_json_schema", "stream_disconnected"):
                    if code in str(error.get("code") or error.get("message") or ""):
                        error_codes.add(code)
    diagnostics = {
        "protocol": "codex-jsonl-v1",
        "eventCount": sum(events.values()),
        "eventTypes": dict(events),
        "turnCompleted": bool(events["turn.completed"]),
        "turnFailed": bool(events["turn.failed"]),
        "toolCallCount": tool_calls,
        "usage": usage,
        "errorCodes": sorted(error_codes),
    }
    return final_message, diagnostics
