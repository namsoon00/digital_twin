"""V19 requires a bounded source survey before closing the price question."""
import hashlib
from . import market_explanation_retrieval as previous, external_influence_retrieval
from . import price_investigation as policy
from .retrieval import ReadRequestError, issue
from digital_twin.modules.reasoning.contracts import EvidenceContractError

VERSION = "independent-observation-retrieval-v19-price-investigation"
POLICY, AUTHOR_SCOPE_POLICY = previous.POLICY, previous.AUTHOR_SCOPE_POLICY
context_view = previous.context_view
question_index, research_window, research_scope = previous.question_index, previous.research_window, previous.research_scope
price_only_finish, validate_decision, validate_trace = previous.price_only_finish, previous.validate_decision, previous.validate_trace
schema = previous.schema


def validate_scope(decision, context, seen):
    previous.validate_scope(decision, context, seen)
    if decision["action"] == "finish":
        gaps = policy.completion_gaps(context["priceInvestigationScope"], seen, decision["selectedFactIds"])
        dated = any(row["datedReferenceEligible"] for row in context["priceMoveAnchor"]["quotes"])
        if gaps or not dated:
            raise ReadRequestError([issue("priceInvestigation", "원천 시각이 유효한 시세와 후보 원문 조회·선택 필요: " + ",".join(gaps))])


def trace_summary(trace, status):
    return {**previous.trace_summary(trace, status), "version": VERSION}


def prompt(current, context):
    return policy.READ_INSTRUCTIONS + external_influence_retrieval.prompt(current, context)


def freeze(current, context, max_bytes):
    policy.validate_packet(current)
    for key in ("priceMoveAnchor", "priceInvestigationScope"):
        if context.get(key) != current[key]:
            raise EvidenceContractError("price investigation routing scope changed")
    if context.get("eventIndex", {}).get("candidateGroups") != current["priceInvestigationScope"]["candidateGroups"]:
        raise EvidenceContractError("price investigation index changed")
    result = previous.freeze(current, context, max_bytes)
    result.update(promptVersion=VERSION, prompt=prompt(current, result["retrievalContext"]))
    result["promptHash"] = hashlib.sha256(result["prompt"].encode()).hexdigest()
    if len(result["prompt"].encode()) > result["promptBudgetBytes"]:
        raise EvidenceContractError("price investigation routing exceeds context budget")
    return result
