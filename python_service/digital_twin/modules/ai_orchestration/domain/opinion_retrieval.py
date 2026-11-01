"""Scoped archive search and concrete gap reads in the frozen retrieval protocol."""
from copy import deepcopy
import hashlib

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from . import question_gap_retrieval, price_investigation_retrieval, market_explanation_retrieval, efficient_retrieval, opinion_memory
from .insight_schema import obj, choice
from .retrieval import ReadRequestError, issue

VERSION = "independent-observation-retrieval-v21-opinion-memory"
POLICY, AUTHOR_SCOPE_POLICY = question_gap_retrieval.POLICY, question_gap_retrieval.AUTHOR_SCOPE_POLICY
context_view = efficient_retrieval.context_view
question_index, research_window, research_scope = question_gap_retrieval.question_index, question_gap_retrieval.research_window, question_gap_retrieval.research_scope
price_only_finish = question_gap_retrieval.price_only_finish
INSTRUCTIONS = """승인 의견 검색과 남은 자료 작업:
search_memory(category=memories,query,cursor)는 같은 계정·종목·세계의 승인된 과거 답변·의견을 찾습니다. query에는 주제와 필요한 보고 기간을 구체적으로 적고, 전체 목록을 반복 검색하지 마세요.
검색 요약은 과거 기억입니다. 필요한 상세는 발급된 read_memory 경로로 읽고 현재 사실·근거로 승격하지 마세요. 검색 범위가 제한되거나 다음 페이지가 있으면 결과 0건이 전체 기억 부재를 뜻하지 않습니다.
gapReadWork는 남은 항목 때문에 먼저 읽은 원문·지표의 기록입니다. 읽은 자료를 원래 부족 항목과 대조하고, 더 필요하면 발급된 경로로 읽으세요. 읽기만으로 답변이 완료되지는 않습니다.
원문을 읽어도 부족분이 해소되지 않으면 확인한 문서·범위와 남은 항목을 구분하세요. 목록에서 찾지 못한 것은 미확인이고, 접근 실패나 비공개라는 뜻이 아닙니다.
"""


def base(context):
    return price_investigation_retrieval if context.get("priceInvestigationScope") else question_gap_retrieval if context.get("executionLane") == "question-research" else efficient_retrieval


def schema(context=None):
    value = base(context or {}).schema(context)
    if context and context.get("opinionMemorySearch"):
        branches = value["properties"]["requests"]["items"].setdefault("anyOf", [])
        cursors = [r["cursor"] for r in context.get("availableCursors", []) if r.get("tool") == "search_memory"]
        branches.append(obj({"tool": choice(["search_memory"]), "category": choice(["memories"]),
            "query": {"type": "string", "minLength": 1, "maxLength": 160}, "cursor": choice(["", *cursors])}))
        value["properties"]["requests"]["maxItems"] = 2
        if "read" not in value["properties"]["action"]["enum"]:
            value["properties"]["action"]["enum"].append("read")
    return value


def normalized(raw):
    value = deepcopy(raw)
    for i, request in enumerate(value.get("requests", []) if isinstance(value, dict) else []):
        if isinstance(request, dict) and request.get("tool") == "search_memory":
            if (set(request) != {"tool", "category", "query", "cursor"} or request["category"] != "memories"
                    or not isinstance(request["query"], str) or not 1 <= len(request["query"].strip()) <= 160
                    or not isinstance(request["cursor"], str)):
                raise ReadRequestError([issue("requests["+str(i)+"]", "bounded scoped memory search")])
            value["requests"][i] = {"tool": "recall_memory", "category": "memories", "cursor": ""}
    return value


def validate_decision(raw):
    contract = market_explanation_retrieval if isinstance(raw, dict) and "optionalEvidence" in raw else question_gap_retrieval
    value = contract.validate_decision(normalized(raw))
    value["requests"] = deepcopy(raw["requests"])
    return value


def validate_trace(trace):
    value = deepcopy(trace)
    for step in value:
        if step.get("decision"):
            step["decision"] = normalized(validate_decision(step["decision"]))
            step["decision"].pop("optionalEvidence", None)
    question_gap_retrieval.validate_trace(value)


def validate_scope(decision, context, seen):
    if bool(context.get("priceInvestigationScope")) != ("optionalEvidence" in decision):
        raise ReadRequestError([issue("optionalEvidence", "시장 관찰의 보충 자료와 질문의 필수 부족분을 구분하세요")])
    base(context).validate_scope(decision, context, seen)


def trace_summary(trace, status):
    value = deepcopy(trace)
    for step in value:
        if step.get("decision"):
            step["decision"] = normalized(step["decision"])
            step["decision"].pop("optionalEvidence", None)
    summary = {**question_gap_retrieval.trace_summary(value, status), "version": VERSION, "traceHash": content_hash(trace)}
    for target, source in zip(summary["steps"], trace):
        if "optionalEvidence" in (source.get("decision") or {}):
            target["optionalEvidence"] = deepcopy(source["decision"]["optionalEvidence"])
    return summary


def prompt(current, context):
    return INSTRUCTIONS + base(context).prompt(current, context)


def freeze(current, context, max_bytes):
    if not opinion_memory.enabled(current):
        raise EvidenceContractError("opinion memory policy missing")
    value = base(context).freeze(current, context, max_bytes)
    value.update(promptVersion=VERSION, prompt=prompt(current, value["retrievalContext"]), outputSchema=schema(value["retrievalContext"]))
    value["promptHash"] = hashlib.sha256(value["prompt"].encode()).hexdigest()
    if len(value["prompt"].encode()) > value["promptBudgetBytes"]:
        raise EvidenceContractError("opinion retrieval exceeds context budget")
    return value


def validate_input(envelope):
    expected = freeze(envelope["current"], envelope["retrievalContext"], envelope["promptBudgetBytes"])
    if expected != envelope:
        raise EvidenceContractError("opinion retrieval input changed")
    validate_trace(envelope["retrievalContext"].get("trace", []))
    return content_hash(envelope)
