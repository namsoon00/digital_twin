"""V13 admits complete working sets before paid reads and final writing."""
import hashlib
from . import quality_retrieval as previous, compact_retrieval, evidence_completion, judgment_policy
from digital_twin.modules.reasoning.contracts import EvidenceContractError

VERSION = "independent-observation-retrieval-v13-evidence-completion"
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
    return evidence_completion.INSTRUCTIONS + previous.prompt(current, context)


def freeze(current, context, max_bytes):
    from .execution_input import prompt_budget
    if not evidence_completion.enabled(current) or not judgment_policy.enabled(current):
        raise EvidenceContractError("evidence completion policy missing")
    value = compact_retrieval.freeze(current, context, max_bytes)
    text = prompt(current, value["retrievalContext"])
    if len(text.encode()) > prompt_budget(max_bytes):
        raise EvidenceContractError("retrieval input exceeds context budget")
    value.update(promptVersion=VERSION, prompt=text, promptHash=hashlib.sha256(text.encode()).hexdigest())
    return value
