"""V20 preserves both gap lists without paying to repair their wording."""
from copy import deepcopy
import hashlib

from . import efficient_retrieval as previous
from .retrieval import ReadRequestError, issue
from digital_twin.modules.reasoning.contracts import content_hash, EvidenceContractError

VERSION = "independent-observation-retrieval-v20-question-gaps"
POLICY, AUTHOR_SCOPE_POLICY = previous.POLICY, previous.AUTHOR_SCOPE_POLICY
MARKER = "question-gap-union-v1"
context_view = previous.context_view
question_index, research_window, research_scope = previous.question_index, previous.research_window, previous.research_scope
price_only_finish, validate_scope, schema = previous.price_only_finish, previous.validate_scope, previous.schema
INSTRUCTIONS = """단일 질문의 부족 항목은 questionScopes[0].missingEvidence에 한 번만 기록하고 전체 missingEvidence=[]로 두세요.
서로 다르게 적은 두 부족 항목 목록은 보수적으로 합쳐 보존합니다. 부족 항목이 하나라도 있으면 ready가 될 수 없습니다.
"""


def bounded_gaps(scoped, global_gaps):
    if len(scoped) > 4 or len(global_gaps) > 4:
        raise ReadRequestError([issue("missingEvidence", "각 목록 최대 네 개")])
    gaps = scoped + global_gaps
    if any(not isinstance(gap, str) or not 1 <= len(gap) <= 600 for gap in gaps):
        raise ReadRequestError([issue("missingEvidence", "bounded strings")])
    gaps = list(dict.fromkeys(gaps))
    if len(gaps) <= 4:
        return gaps
    ordered = sorted(range(len(gaps)), key=lambda index: -len(gaps[index]))
    groups, sizes = [[], [], [], []], [0, 0, 0, 0]
    def fit(position):
        if position == len(ordered):
            return True
        item = ordered[position]
        visited = set()
        for index in sorted(range(4), key=lambda index: sizes[index]):
            if sizes[index] in visited:
                continue
            visited.add(sizes[index])
            extra = len(gaps[item]) + bool(groups[index])
            if sizes[index] + extra > 600:
                continue
            groups[index].append(item); sizes[index] += extra
            if fit(position + 1):
                return True
            groups[index].pop(); sizes[index] -= extra
        return False
    # At most eight original strings in four bins. Newlines separate intact,
    # independent requirements; no paraphrase, truncation or semantic merge.
    if sum(map(len, gaps)) > 2400 or not fit(0):
        raise ReadRequestError([issue("missingEvidence", "모든 부족 항목을 보존할 수 있는 입력 한도")])
    return ["\n".join(gaps[index] for index in sorted(group))
            for group in sorted((group for group in groups if group), key=min)]


def canonical(raw):
    value = deepcopy(raw)
    if (isinstance(value, dict) and value.get("action") == "assess"
            and isinstance(value.get("questionScopes"), list) and len(value["questionScopes"]) == 1):
        scope = value["questionScopes"][0]
        if isinstance(scope, dict) and isinstance(scope.get("missingEvidence"), list) and isinstance(value.get("missingEvidence"), list):
            gaps = bounded_gaps(scope["missingEvidence"], value["missingEvidence"])
            scope["missingEvidence"] = gaps
            value["missingEvidence"] = gaps
            if gaps:
                scope["status"] = "waiting"
                value["blocker"] = "required-source"
    return value


def validate_decision(raw):
    return previous.validate_decision(canonical(raw))


def validate_trace(trace):
    normalized = deepcopy(trace)
    for step in normalized:
        if step.get("decision"):
            step["decision"] = canonical(step["decision"])
    previous.validate_trace(normalized)


def trace_summary(trace, status):
    return {**previous.trace_summary(trace, status), "version": VERSION, "traceHash": content_hash(trace)}


def prompt(current, context):
    return INSTRUCTIONS + previous.prompt(current, context)


def freeze(current, context, max_bytes):
    if current.get("questionGapPolicy") != MARKER or current.get("executionLane") != "question-research":
        raise EvidenceContractError("question gap policy missing")
    result = previous.freeze(current, context, max_bytes)
    result.update(promptVersion=VERSION, prompt=prompt(current, result["retrievalContext"]))
    result["promptHash"] = hashlib.sha256(result["prompt"].encode()).hexdigest()
    if len(result["prompt"].encode()) > result["promptBudgetBytes"]:
        raise EvidenceContractError("question gap routing exceeds context budget")
    return result
