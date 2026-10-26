"""Versioned author contract; previous immutable prompts remain replayable."""
from copy import deepcopy
import hashlib
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from . import research_chain, partial_judgment, company_research_cycle, business_insight, evidence_purpose
from . import evidence_completion, judgment_policy, company_value

PROMPT_VERSION = "independent-observation-v29-answer-led-research"
REPAIR_VERSION = "independent-observation-repair-v25-answer-led-research"
REVIEW_VERSION = "independent-observation-review-v18-answer-led-research"
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION}
enabled = research_chain.enabled


def base(packet):
    return (partial_judgment if partial_judgment.enabled(packet) else
            company_research_cycle if company_research_cycle.enabled(packet) else
            business_insight if business_insight.enabled(packet) else
            evidence_completion if evidence_completion.enabled(packet) else
            judgment_policy if judgment_policy.enabled(packet) else evidence_purpose)


def prompt(packet, history, research):
    return research_chain.INSTRUCTIONS + base(packet).prompt(packet, history, research)


def schema(packet, research):
    result = base(packet).schema(packet, research)
    result["properties"]["questionContinuations"] = research_chain.schema(research)
    result["required"].append("questionContinuations")
    return result


def review_prompt(packet, draft):
    return research_chain.REVIEW_INSTRUCTIONS + base(packet).review_prompt(packet, draft)


def review_schema(packet):
    from .insight_schema import review_schema as legacy
    return base(packet).review_schema() if hasattr(base(packet), "review_schema") else legacy()


def validate_input(envelope):
    from .execution_input import validate_execution_input, prompt_budget
    from .insight_repair import repair_prompt
    packet, version = envelope["current"], envelope["promptVersion"]
    if not enabled(packet):
        raise EvidenceContractError("answer-led research policy missing")
    reviewing = version == REVIEW_VERSION
    expected = review_prompt(packet, envelope["draft"]) if reviewing else prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        expected = repair_prompt(expected, envelope["repair"])
    if (expected != envelope["prompt"] or hashlib.sha256(expected.encode()).hexdigest() != envelope["promptHash"]
            or envelope["outputSchema"] != (review_schema(packet) if reviewing else schema(packet, envelope["researchResults"]))
            or len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes"))):
        raise EvidenceContractError("answer-led research input changed")
    previous, old = base(packet), deepcopy(envelope)
    previous_version = company_value if previous is evidence_purpose and company_value.enabled(packet) else previous
    old["promptVersion"] = {PROMPT_VERSION: previous_version.PROMPT_VERSION, REPAIR_VERSION: previous_version.REPAIR_VERSION,
                            REVIEW_VERSION: previous_version.REVIEW_VERSION}[version]
    old["prompt"] = previous.review_prompt(packet, envelope["draft"]) if reviewing else previous.prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        old["prompt"] = repair_prompt(old["prompt"], envelope["repair"])
    old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
    old["outputSchema"] = review_schema(packet) if reviewing else previous.schema(packet, envelope["researchResults"])
    validate_execution_input(old)
    return content_hash(envelope)
