"""V12 routes by question, with optional macro reads and purpose-specific output."""
import hashlib
from . import compact_retrieval as previous, judgment_policy
from digital_twin.modules.reasoning.contracts import EvidenceContractError

VERSION = "independent-observation-retrieval-v12-purpose-quality"
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
    return judgment_policy.INSTRUCTIONS + previous.prompt(current, context)


def freeze(current, context, max_bytes):
    from .execution_input import prompt_budget
    value = previous.freeze(current, context, max_bytes)
    text = prompt(current, value["retrievalContext"])
    if not judgment_policy.enabled(current) or len(text.encode()) > prompt_budget(max_bytes):
        raise EvidenceContractError("quality retrieval policy or context budget invalid")
    value.update(promptVersion=VERSION, prompt=text, promptHash=hashlib.sha256(text.encode()).hexdigest())
    return value
