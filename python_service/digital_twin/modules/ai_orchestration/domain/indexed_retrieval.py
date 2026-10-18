"""V5 adds precise memory reads while retaining earlier frozen contracts."""
from copy import deepcopy
import hashlib
import json

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from . import scoped_retrieval as scoped, memory_reference
from .insight_schema import obj, choice
from .retrieval import ReadRequestError, issue

VERSION = "independent-observation-retrieval-v5-indexed-memory"
POLICY = "question-working-set-v3-indexed-memory"
AUTHOR_SCOPE_POLICY = scoped.AUTHOR_SCOPE_POLICY
question_index = scoped.question_index
research_window = scoped.research_window
research_scope = scoped.research_scope
validate_scope = scoped.validate_scope


def schema(context=None):
    result = scoped.schema(context)
    text = {"type": "string", "minLength": 1, "maxLength": 600}
    result["properties"]["requests"]["items"]["anyOf"].append(obj({"tool": choice(["read_memory"]),
        "category": choice(["analyses", "memories"]),
        "memoryId": choice([row["memoryId"] for row in context.get("memoryIndex", [])] or [""]) if context is not None else text,
        "path": text}))
    return result


def normalized(raw):
    value = deepcopy(raw)
    if isinstance(value, dict) and isinstance(value.get("requests"), list):
        for i, request in enumerate(value["requests"]):
            if isinstance(request, dict) and request.get("tool") == "read_memory":
                if (set(request) != {"tool", "category", "memoryId", "path"}
                        or not isinstance(request["category"], str) or request["category"] not in {"analyses", "memories"}
                        or any(not isinstance(request[key], str) or not 1 <= len(request[key]) <= 600
                               for key in ("memoryId", "path")) or not request["path"].startswith("/")):
                    raise ReadRequestError([issue("requests[" + str(i) + "]", "memoryIndex의 ID와 발급된 section path")])
                value["requests"][i] = {"tool": "recall_memory", "category": request["category"], "cursor": ""}
    return value


def validate_decision(raw):
    scoped.validate_decision(normalized(raw))
    return deepcopy(raw)


def validate_trace(trace):
    value = deepcopy(trace)
    for step in value:
        if step.get("decision") is not None:
            step["decision"] = normalized(step["decision"])
    scoped.validate_trace(value)


def trace_summary(trace, status):
    normalized_trace = deepcopy(trace)
    for step in normalized_trace:
        if step.get("decision") is not None:
            step["decision"] = normalized(step["decision"])
    result = scoped.trace_summary(normalized_trace, status)
    result.update(version=VERSION, traceHash=content_hash(trace))
    return result


def prompt(current, context):
    return memory_reference.INSTRUCTIONS + scoped.prompt(current, context)


def freeze(current, context, max_bytes):
    from .execution_input import EXECUTION_INPUT_PROTOCOL, prompt_budget
    value = prompt(current, context)
    limit = prompt_budget(max_bytes)
    if len(value.encode()) > limit:
        raise EvidenceContractError("retrieval input exceeds context budget")
    return {"protocolVersion": EXECUTION_INPUT_PROTOCOL, "promptVersion": VERSION, "promptBudgetBytes": limit,
        "current": deepcopy(current), "retrievalContext": deepcopy(context), "prompt": value,
        "promptHash": hashlib.sha256(value.encode()).hexdigest(), "outputSchema": schema(context)}
