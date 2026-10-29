"""V18 distinguishes optional context from evidence required by the chosen scope."""
from copy import deepcopy
import hashlib
from . import external_influence_retrieval as previous, market_explanation as policy
from .retrieval import ReadRequestError, issue
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash

VERSION = "independent-observation-retrieval-v18-price-context"
POLICY, AUTHOR_SCOPE_POLICY = previous.POLICY, previous.AUTHOR_SCOPE_POLICY
context_view = previous.context_view
question_index, research_window, research_scope = previous.question_index, previous.research_window, previous.research_scope
price_only_finish = previous.price_only_finish


def legacy(value):
    return {k: v for k, v in value.items() if k != "optionalEvidence"}


def validate_decision(raw):
    value = raw.get("optionalEvidence") if isinstance(raw, dict) else None
    if (not isinstance(value, list) or len(value) > 4
            or any(not isinstance(x, str) or not 1 <= len(x.strip()) <= 600 for x in value)
            or len(set(value)) != len(value)):
        raise ReadRequestError([issue("optionalEvidence", "최대 네 개의 구체적인 보충 자료 부족")])
    result = previous.validate_decision(legacy(raw))
    if set(value).intersection(result["missingEvidence"]):
        raise ReadRequestError([issue("optionalEvidence", "같은 부족분을 필수와 보충에 중복 분류할 수 없습니다")])
    return {**result, "optionalEvidence": deepcopy(value)}


def validate_scope(decision, context, seen):
    previous.validate_scope(legacy(decision), context, seen)


def stripped(trace):
    result = deepcopy(trace)
    for row in result:
        if row.get("decision"):
            row["decision"] = legacy(row["decision"])
    return result


def validate_trace(trace):
    for row in trace:
        if row.get("decision"):
            validate_decision(row["decision"])
    previous.validate_trace(stripped(trace))


def trace_summary(trace, status):
    result = previous.trace_summary(stripped(trace), status)
    result.update(version=VERSION, traceHash=content_hash(trace))
    for target, source in zip(result["steps"], trace):
        target["optionalEvidence"] = (source.get("decision") or {}).get("optionalEvidence", [])
    return result


def schema(context=None):
    result = previous.schema(context)
    result["properties"]["optionalEvidence"] = {"type": "array", "maxItems": 4,
        "items": {"type": "string", "minLength": 1, "maxLength": 600}}
    result["required"].append("optionalEvidence")
    return result


def prompt(current, context):
    return policy.READ_INSTRUCTIONS + previous.prompt(current, context)


def freeze(current, context, max_bytes):
    if not policy.enabled(current):
        raise EvidenceContractError("market explanation policy missing")
    result = previous.freeze(current, context, max_bytes)
    result.update(promptVersion=VERSION, prompt=prompt(current, result["retrievalContext"]), outputSchema=schema(result["retrievalContext"]))
    result["promptHash"] = hashlib.sha256(result["prompt"].encode()).hexdigest()
    if len(result["prompt"].encode()) > result["promptBudgetBytes"]:
        raise EvidenceContractError("market explanation routing exceeds context budget")
    return result
