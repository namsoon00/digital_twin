"""Freeze the exact model input before execution, including bounded memories."""
import copy
import hashlib

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash, validate_evidence_packet
from digital_twin.modules.ai_orchestration.domain.planning import planning_prompt


EXECUTION_INPUT_PROTOCOL = "ai-observation-execution-v1"
PROMPT_VERSION = "independent-observation-v2-evidence-contract"


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
        "prompt": prompt, "promptHash": hashlib.sha256(prompt.encode()).hexdigest()}


def validate_execution_input(envelope):
    if envelope.get("protocolVersion") != EXECUTION_INPUT_PROTOCOL or envelope.get("promptVersion") != PROMPT_VERSION:
        raise EvidenceContractError("unsupported AI execution input")
    validate_evidence_packet(envelope["current"])
    prompt = planning_prompt(envelope["current"], envelope["previousAnalyses"], envelope["researchResults"])
    if prompt != envelope["prompt"] or hashlib.sha256(prompt.encode()).hexdigest() != envelope["promptHash"]:
        raise EvidenceContractError("frozen AI prompt does not match input")
    return content_hash(envelope)
