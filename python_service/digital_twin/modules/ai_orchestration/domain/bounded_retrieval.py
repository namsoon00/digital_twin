"""V8 issues exact read arguments and conservatively parks contradictory finishes."""
from copy import deepcopy
import hashlib
import json

from . import lane_retrieval as previous, execution_lanes
from .insight_schema import obj, choice, array
from .retrieval import CATEGORIES
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash

VERSION = "independent-observation-retrieval-v8-bounded-evidence"
POLICY = "bounded-issued-reads-v1"
AUTHOR_SCOPE_POLICY = previous.AUTHOR_SCOPE_POLICY
question_index = previous.question_index
research_window = previous.research_window
research_scope = previous.research_scope
validate_scope = previous.validate_scope


def schema(context=None):
    value = previous.schema(context)
    if context is None:
        return value
    branches = []
    cursors = context.get("availableCursors", [])
    for category in CATEGORIES:
        coverage = context.get("catalog", {}).get("coverage", {}).get(category, {})
        if not coverage.get("available"):
            continue
        for kind in ["", *sorted(coverage.get("kinds", {}))]:
            issued = [row["cursor"] for row in cursors
                      if row.get("tool") == "query_facts" and row.get("category") == category
                      and row.get("kind") == kind]
            branches.append(obj({"tool": choice(["query_facts"]), "category": choice([category]),
                                 "kind": choice([kind]), "cursor": choice(["", *issued])}))
    readable = context.get("readableFacts", context.get("knownFacts", []))
    for category in CATEGORIES:
        ids = sorted({row["factId"] for row in readable if row["category"] == category})
        if ids:
            branches.append(obj({"tool": choice(["read_fact"]), "category": choice([category]), "factId": choice(ids)}))
    for report in context.get("reportIndex", []):
        if report.get("metrics"):
            branches.append(obj({"tool": choice(["read_report"]), "factId": choice([report["factId"]]),
                "metrics": {**array(choice(report["metrics"])), "minItems": 1, "maxItems": 12}}))
    documents = [row["factId"] for row in context.get("questionDocumentIndex", [])]
    if documents:
        branches.append(obj({"tool": choice(["read_document"]), "factId": choice(documents)}))
    for memory in context.get("readableMemorySections", []):
        branches.append(obj({"tool": choice(["read_memory"]), "category": choice([memory["category"]]),
                             "memoryId": choice([memory["memoryId"]]), "path": choice(memory["paths"])}))
    # Detailed recall is already addressable. Do not offer a full-memory page
    # tool which encourages repeated broad reads or invented section paths.
    if branches:
        value["properties"]["requests"]["items"] = {"anyOf": branches}
    else:
        value["properties"]["requests"]["maxItems"] = 0
        value["properties"]["action"]["enum"] = [a for a in value["properties"]["action"]["enum"] if a != "read"]
    return value


def validate_decision(raw):
    value = deepcopy(raw)
    # Never pay to ask the model to remove a declared required gap. Retain the
    # raw response in its audit and move only toward the less permissive state.
    if (isinstance(value, dict) and value.get("action") == "finish"
            and value.get("missingEvidence") and value.get("questionScopes") == []):
        value["action"] = "defer"
    return previous.validate_decision(value)


def price_only_finish(decision, known):
    """The existing source-selection floor also owns the persisted receipt."""
    return decision.get("action") == "finish" and not any(
        known[key]["kind"] != "stock" for key in decision["selectedFactIds"])


def validate_trace(trace):
    previous.validate_trace(trace)


def trace_summary(trace, status):
    value = previous.trace_summary(trace, status)
    value.update(version=VERSION, traceHash=content_hash(trace))
    return value


def prompt(current, context):
    data = json.dumps({"current": current, "retrieval": context}, ensure_ascii=False, allow_nan=False)
    base = previous.prompt(current, context)
    return ("출력 스키마의 발급된 도구·ID·분류·지표·경로만 사용하세요. readableFacts는 읽기 허가이고 "
            "knownFacts는 이미 읽어 선택 가능한 근거입니다. 보고서 지표는 read_report로, 원문은 read_document로 읽으세요. "
            "성공한 읽기를 반복하지 마세요. 부족한 필수 자료를 적었다면 시장 관찰은 defer입니다. "
            "일반적인 불확실성과 검증되지 않은 가설은 필수 자료 부족과 구분하세요. "
            "시세 갱신 요청은 원래 동결 자료를 바꾸지 않습니다. 원래 공시 질문에는 현재 시세를 요구하지 마세요.\n"
            + base[:-len(data)] + data)


def freeze(current, context, max_bytes):
    from .execution_input import EXECUTION_INPUT_PROTOCOL, prompt_budget
    value = prompt(current, context)
    limit = prompt_budget(max_bytes)
    if len(value.encode()) > limit:
        raise EvidenceContractError("retrieval input exceeds context budget")
    return {"protocolVersion": EXECUTION_INPUT_PROTOCOL, "promptVersion": VERSION, "promptBudgetBytes": limit,
            "current": deepcopy(current), "retrievalContext": deepcopy(context), "prompt": value,
            "promptHash": hashlib.sha256(value.encode()).hexdigest(), "outputSchema": schema(context)}
