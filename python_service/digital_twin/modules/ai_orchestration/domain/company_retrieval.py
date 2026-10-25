"""V15 reads source-derived calculations with the existing bounded read tools."""
import hashlib
from . import business_retrieval as previous, company_research_cycle
from digital_twin.modules.reasoning.contracts import EvidenceContractError

VERSION = "independent-observation-retrieval-v15-company-research"
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
    return company_research_cycle.INSTRUCTIONS + previous.prompt(current, context)


def freeze(current, context, max_bytes):
    from .execution_input import prompt_budget
    if not company_research_cycle.enabled(current):
        raise EvidenceContractError("company research policy missing")
    value = previous.freeze(current, context, max_bytes)
    text = prompt(current, value["retrievalContext"])
    if len(text.encode()) > prompt_budget(max_bytes):
        raise EvidenceContractError("retrieval input exceeds context budget")
    value.update(promptVersion=VERSION, prompt=text, promptHash=hashlib.sha256(text.encode()).hexdigest())
    return value
