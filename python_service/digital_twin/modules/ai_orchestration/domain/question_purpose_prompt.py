"""Versioned author contract; previous immutable prompts remain replayable."""
from copy import deepcopy
import hashlib
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from . import question_purpose, research_chain_prompt, delivery_insight, company_research_cycle

PROMPT_VERSION = "independent-observation-v30-question-purpose"
REPAIR_VERSION = "independent-observation-repair-v26-question-purpose"
REVIEW_VERSION = "independent-observation-review-v19-question-purpose"
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION}
enabled = question_purpose.enabled


def base(packet):
    return (research_chain_prompt if research_chain_prompt.enabled(packet) else
            delivery_insight if delivery_insight.enabled(packet) else company_research_cycle)


def prompt(packet, history, research):
    return question_purpose.INSTRUCTIONS + base(packet).prompt(packet, history, research)


def schema(packet, research):
    result = base(packet).schema(packet, research)
    result["properties"]["questionPurposes"] = question_purpose.schema(packet)
    result["required"].append("questionPurposes")
    return result


def review_prompt(packet, draft):
    return question_purpose.INSTRUCTIONS + base(packet).review_prompt(packet, draft)


def review_schema(packet):
    from .insight_schema import review_schema as legacy
    previous = base(packet)
    if previous in {research_chain_prompt, delivery_insight}:
        return previous.review_schema(packet)
    return previous.review_schema() if hasattr(previous, "review_schema") else legacy()


def validate_input(envelope):
    from .execution_input import validate_execution_input, prompt_budget
    from .insight_repair import repair_prompt
    packet, version = envelope["current"], envelope["promptVersion"]
    if not enabled(packet):
        raise EvidenceContractError("question purpose policy missing")
    from .question_source_handoff import validate as validate_handoff
    validate_handoff(packet, ())
    reviewing = version == REVIEW_VERSION
    expected = review_prompt(packet, envelope["draft"]) if reviewing else prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        expected = repair_prompt(expected, envelope["repair"])
    if (expected != envelope["prompt"] or hashlib.sha256(expected.encode()).hexdigest() != envelope["promptHash"]
            or envelope["outputSchema"] != (review_schema(packet) if reviewing else schema(packet, envelope["researchResults"]))
            or len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes"))):
        raise EvidenceContractError("question purpose input changed")
    previous, old = base(packet), deepcopy(envelope)
    old["promptVersion"] = {PROMPT_VERSION: previous.PROMPT_VERSION, REPAIR_VERSION: previous.REPAIR_VERSION,
                            REVIEW_VERSION: previous.REVIEW_VERSION}[version]
    old["prompt"] = previous.review_prompt(packet, envelope["draft"]) if reviewing else previous.prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        old["prompt"] = repair_prompt(old["prompt"], envelope["repair"])
    old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
    old["outputSchema"] = review_schema(packet) if reviewing else previous.schema(packet, envelope["researchResults"])
    validate_execution_input(old)
    return content_hash(envelope)
