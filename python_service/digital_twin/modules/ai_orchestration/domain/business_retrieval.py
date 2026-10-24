"""V14 reads reporting periods before declaring a business explanation ready."""
import hashlib
from . import completion_retrieval as previous, business_insight
from digital_twin.modules.reasoning.contracts import EvidenceContractError

VERSION = "independent-observation-retrieval-v14-business-progress"
POLICY = previous.POLICY
AUTHOR_SCOPE_POLICY = previous.AUTHOR_SCOPE_POLICY
question_index = previous.question_index
research_window = previous.research_window
research_scope = previous.research_scope
price_only_finish = previous.price_only_finish
validate_scope = previous.validate_scope
validate_decision = previous.validate_decision
validate_trace = previous.validate_trace
schema = previous.schema


def trace_summary(trace, status):
    return {**previous.trace_summary(trace, status), "version": VERSION}


def prompt(current, context):
    return business_insight.INSTRUCTIONS + previous.prompt(current, context)


def freeze(current, context, max_bytes):
    from .execution_input import prompt_budget
    if not business_insight.enabled(current):
        raise EvidenceContractError("business insight policy missing")
    value = previous.freeze(current, context, max_bytes)
    text = prompt(current, value["retrievalContext"])
    if len(text.encode()) > prompt_budget(max_bytes):
        raise EvidenceContractError("retrieval input exceeds context budget")
    value.update(promptVersion=VERSION, prompt=text, promptHash=hashlib.sha256(text.encode()).hexdigest())
    return value
