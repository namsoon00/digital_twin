"""Bounded source selection for external events and company exposure."""
import hashlib
from . import efficient_retrieval as previous, external_influence
from digital_twin.modules.reasoning.contracts import EvidenceContractError

VERSION = "independent-observation-retrieval-v17-external-influence"
POLICY, AUTHOR_SCOPE_POLICY = previous.POLICY, previous.AUTHOR_SCOPE_POLICY
context_view = previous.context_view
question_index, research_window, research_scope = previous.question_index, previous.research_window, previous.research_scope
price_only_finish, validate_scope = previous.price_only_finish, previous.validate_scope
validate_decision, validate_trace, schema = previous.validate_decision, previous.validate_trace, previous.schema


def trace_summary(trace, status):
    return {**previous.trace_summary(trace, status), "version": VERSION}


def prompt(current, context):
    return external_influence.READ_INSTRUCTIONS + previous.prompt(current, context)


def freeze(current, context, max_bytes):
    if not external_influence.enabled(current):
        raise EvidenceContractError("external influence routing policy missing")
    result = previous.freeze(current, context, max_bytes)
    result["promptVersion"] = VERSION
    result["prompt"] = prompt(current, result["retrievalContext"])
    result["promptHash"] = hashlib.sha256(result["prompt"].encode()).hexdigest()
    if len(result["prompt"].encode()) > result["promptBudgetBytes"]:
        raise EvidenceContractError("external influence routing exceeds context budget")
    return result
