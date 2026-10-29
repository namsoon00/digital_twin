"""Versioned source-delivery and silence contract; historical prompts stay frozen."""
from copy import deepcopy
import hashlib
from . import price_investigation_prompt as previous, observation_abstention as policy
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash

PROMPT_VERSION = "independent-observation-v34-abstention-bounds"
REPAIR_VERSION = "independent-observation-repair-v30-abstention-bounds"
REVIEW_VERSION = "independent-observation-review-v23-abstention-bounds"
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION}
enabled = policy.enabled


schema = previous.schema
review_schema = previous.review_schema


def prompt(packet, history, research):
    return policy.INSTRUCTIONS + previous.prompt(packet, history, research)


def review_prompt(packet, draft):
    return policy.INSTRUCTIONS + previous.review_prompt(packet, draft)


def validate_input(envelope):
    from .execution_input import validate_execution_input, prompt_budget
    from .insight_repair import repair_prompt
    packet, version = envelope["current"], envelope["promptVersion"]
    policy.validate_packet(packet)
    reviewing = version == REVIEW_VERSION
    expected = review_prompt(packet, envelope["draft"]) if reviewing else prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        expected = repair_prompt(expected, envelope["repair"])
    if (expected != envelope["prompt"] or hashlib.sha256(expected.encode()).hexdigest() != envelope["promptHash"]
            or envelope["outputSchema"] != (review_schema(packet) if reviewing else schema(packet, envelope["researchResults"]))
            or len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes"))):
        raise EvidenceContractError("abstention input changed")
    old = deepcopy(envelope)
    old["promptVersion"] = {PROMPT_VERSION: previous.PROMPT_VERSION, REPAIR_VERSION: previous.REPAIR_VERSION,
                            REVIEW_VERSION: previous.REVIEW_VERSION}[version]
    old["prompt"] = previous.review_prompt(packet, envelope["draft"]) if reviewing else previous.prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        old["prompt"] = repair_prompt(old["prompt"], envelope["repair"])
    old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
    old["outputSchema"] = previous.review_schema(packet) if reviewing else previous.schema(packet, envelope["researchResults"])
    validate_execution_input(old)
    return content_hash(envelope)
