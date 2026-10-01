"""Freeze the exact model input before execution, including bounded memories."""
import copy
import hashlib

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash, validate_evidence_packet
from digital_twin.modules.ai_orchestration.domain.planning import planning_prompt
from digital_twin.modules.ai_orchestration.domain.insight_schema import planning_schema, review_schema


EXECUTION_INPUT_PROTOCOL = "ai-observation-execution-v1"
PROMPT_VERSION = "independent-observation-v3-insight-contract"
REVIEW_PROMPT_VERSION = "independent-observation-review-v1"


def freeze_execution_input(packet, history, research):
    validate_evidence_packet(packet)
    current, previous, results = copy.deepcopy((packet, history, research))
    prompt = planning_prompt(current, previous, results)
    if len(prompt.encode()) > 120000:
        previous, results = previous[:1], results[:1]
        prompt = planning_prompt(current, previous, results)
    if len(prompt.encode()) > 120000:
        raise EvidenceContractError("observation execution exceeds context budget")
    return {"protocolVersion": EXECUTION_INPUT_PROTOCOL, "promptVersion": PROMPT_VERSION,
        "current": current, "previousAnalyses": previous, "researchResults": results,
        "memoryCoverage": {"analysesAvailable": len(history), "analysesIncluded": len(previous),
                           "researchAvailable": len(research), "researchIncluded": len(results)},
        "prompt": prompt, "promptHash": hashlib.sha256(prompt.encode()).hexdigest(), "outputSchema": planning_schema(current)}


def validate_execution_input(envelope):
    if envelope.get("protocolVersion") != EXECUTION_INPUT_PROTOCOL or envelope.get("promptVersion") not in {PROMPT_VERSION, REVIEW_PROMPT_VERSION}:
        raise EvidenceContractError("unsupported AI execution input")
    validate_evidence_packet(envelope["current"])
    if envelope["promptVersion"] == REVIEW_PROMPT_VERSION:
        from digital_twin.modules.ai_orchestration.domain.insight_quality import review_prompt
        prompt = review_prompt(envelope["current"], envelope["draft"])
        schema = review_schema()
    else:
        prompt = planning_prompt(envelope["current"], envelope["previousAnalyses"], envelope["researchResults"])
        schema = planning_schema(envelope["current"])
    if prompt != envelope["prompt"] or hashlib.sha256(prompt.encode()).hexdigest() != envelope["promptHash"]:
        raise EvidenceContractError("frozen AI prompt does not match input")
    if envelope.get("outputSchema") != schema:
        raise EvidenceContractError("frozen AI schema does not match input")
    return content_hash(envelope)


def freeze_review_input(result):
    from digital_twin.modules.ai_orchestration.domain.insight_quality import review_prompt
    draft = copy.deepcopy({key: value for key, value in result.items() if key not in {"input", "quality", "comparisonFacts"}})
    packet = copy.deepcopy(result["input"])
    prompt = review_prompt(packet, draft)
    if len(prompt.encode()) > 120000:
        raise EvidenceContractError("observation review exceeds context budget")
    return {"protocolVersion": EXECUTION_INPUT_PROTOCOL, "promptVersion": REVIEW_PROMPT_VERSION,
            "current": packet, "draft": draft, "prompt": prompt, "promptHash": hashlib.sha256(prompt.encode()).hexdigest(), "outputSchema": review_schema()}
