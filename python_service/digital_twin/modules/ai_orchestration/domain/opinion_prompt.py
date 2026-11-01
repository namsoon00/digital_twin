"""Versioned opinion semantics, with historical author contracts preserved."""
from copy import deepcopy
import hashlib
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from . import opinion_memory, abstention_prompt, question_purpose_prompt

PROMPT_VERSION = "independent-observation-v35-reviewed-opinion"
REPAIR_VERSION = "independent-observation-repair-v31-reviewed-opinion"
REVIEW_VERSION = "independent-observation-review-v24-reviewed-opinion"
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION}
enabled = opinion_memory.enabled


def base(packet):
    return abstention_prompt if abstention_prompt.enabled(packet) else question_purpose_prompt


def prompt(packet, history, research):
    return opinion_memory.INSTRUCTIONS + base(packet).prompt(packet, history, research)


def schema(packet, research):
    return base(packet).schema(packet, research)


def review_prompt(packet, draft):
    return opinion_memory.INSTRUCTIONS + base(packet).review_prompt(packet, draft)


def review_schema(packet):
    return base(packet).review_schema(packet)


def validate_input(envelope):
    from .execution_input import validate_execution_input, prompt_budget
    from .insight_repair import repair_prompt
    packet, version = envelope["current"], envelope["promptVersion"]
    if not enabled(packet):
        raise EvidenceContractError("reviewed opinion policy missing")
    reviewing = version == REVIEW_VERSION
    expected = review_prompt(packet, envelope["draft"]) if reviewing else prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        expected = repair_prompt(expected, envelope["repair"])
    if (expected != envelope["prompt"] or hashlib.sha256(expected.encode()).hexdigest() != envelope["promptHash"]
            or envelope["outputSchema"] != (review_schema(packet) if reviewing else schema(packet, envelope["researchResults"]))
            or len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes"))):
        raise EvidenceContractError("reviewed opinion input changed")
    previous, old = base(packet), deepcopy(envelope)
    old["promptVersion"] = {PROMPT_VERSION: previous.PROMPT_VERSION, REPAIR_VERSION: previous.REPAIR_VERSION,
                            REVIEW_VERSION: previous.REVIEW_VERSION}[version]
    old["prompt"] = previous.review_prompt(packet, envelope["draft"]) if reviewing else previous.prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        old["prompt"] = repair_prompt(old["prompt"], envelope["repair"])
    old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
    validate_execution_input(old)
    return content_hash(envelope)
