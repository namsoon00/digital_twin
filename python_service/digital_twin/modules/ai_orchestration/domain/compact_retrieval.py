"""V11 separates routing text from immutable source/audit and author evidence."""
from copy import deepcopy
import hashlib
import json

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from . import purpose_retrieval as previous, retrieval_view, company_value, evidence_purpose
from .insight_schema import obj, choice
from .retrieval import ReadRequestError, issue

VERSION = "independent-observation-retrieval-v11-compact-routing"
POLICY = previous.POLICY
AUTHOR_SCOPE_POLICY = previous.AUTHOR_SCOPE_POLICY
question_index = previous.question_index
research_window = previous.research_window
research_scope = previous.research_scope
price_only_finish = previous.price_only_finish
validate_scope = previous.validate_scope


def normalized(raw):
    value = deepcopy(raw)
    if isinstance(value, dict) and isinstance(value.get("requests"), list):
        for index, request in enumerate(value["requests"]):
            if isinstance(request, dict) and request.get("tool") == "read_context":
                if (set(request) != {"tool", "path"} or not isinstance(request["path"], str)
                        or not 1 <= len(request["path"]) <= 600 or not request["path"].startswith("/")):
                    raise ReadRequestError([issue("read_context", "발급된 동결 문맥 path")])
                # Reuse the historical decision shape validator; this surrogate
                # is never sent to a source reader or persisted as the decision.
                value["requests"][index] = {"tool": "read_fact", "category": "company", "factId": request["path"]}
    return value


def validate_decision(raw):
    result = previous.validate_decision(normalized(raw))
    result["requests"] = deepcopy(raw["requests"])
    return result


def _trace(trace):
    value = deepcopy(trace)
    for step in value:
        if step.get("decision") is not None:
            step["decision"] = normalized(step["decision"])
    return value


def validate_trace(trace):
    previous.validate_trace(_trace(trace))


def trace_summary(trace, status):
    result = previous.trace_summary(_trace(trace), status)
    result.update(version=VERSION, traceHash=content_hash(trace))
    return result


def schema(context=None):
    result = previous.schema(context)
    paths = (context or {}).get("readableContextPaths", [])
    if paths:
        requests = result["properties"]["requests"]
        if requests.get("maxItems") == 0:
            requests["items"] = {"anyOf": []}
        requests["maxItems"] = 2
        requests["items"]["anyOf"].append(obj({"tool": choice(["read_context"]), "path": choice(paths)}))
        if "read" not in result["properties"]["action"]["enum"]:
            result["properties"]["action"]["enum"].insert(0, "read")
    return result


def prompt(current, context):
    packet, routing = retrieval_view.project(current, context)
    # Retain the existing authority, purpose, question and counter-evidence
    # instructions exactly, with compact JSON for the new version only.
    base = previous.prompt(packet, routing)
    data = json.dumps({"current": packet, "retrieval": routing}, ensure_ascii=False, allow_nan=False)
    instructions = ("조회용 요약입니다. detailNotLoaded는 자료 부재·반증이 아닙니다. "
                    "상세 내용이 결정에 필요하면 read_context(path)로 발급된 경로를 읽으세요. "
                    "readResults는 이미 읽은 결과이며 같은 읽기를 반복하지 마세요. "
                    "이전 알림·기억은 과거 문맥이며 새로운 현재 사실이 아닙니다. "
                    "memoryIndex의 상세 경로는 readableMemorySections에 있습니다. "
                    "최종 작성에는 선택된 원본 근거와 필수 기억이 별도로 전달됩니다.\n")
    return instructions + base[:-len(data)] + json.dumps({"current": packet, "retrieval": routing},
        ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def freeze(current, context, max_bytes):
    from .execution_input import EXECUTION_INPUT_PROTOCOL, prompt_budget
    value = deepcopy(context)
    value["readableContextPaths"] = retrieval_view.paths(current, value)
    evidence_purpose.validate(current)
    if company_value.enabled(current):
        company_value.validate(current)
    text = prompt(current, value)
    limit = prompt_budget(max_bytes)
    if len(text.encode()) > limit:
        raise EvidenceContractError("retrieval input exceeds context budget")
    return {"protocolVersion": EXECUTION_INPUT_PROTOCOL, "promptVersion": VERSION, "promptBudgetBytes": limit,
            "current": deepcopy(current), "retrievalContext": value, "prompt": text,
            "promptHash": hashlib.sha256(text.encode()).hexdigest(), "outputSchema": schema(value)}
