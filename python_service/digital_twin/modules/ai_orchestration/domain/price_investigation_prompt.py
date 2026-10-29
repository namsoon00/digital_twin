"""Versioned price investigation outcome and independent explanation review."""
from copy import deepcopy
import hashlib
from . import market_explanation_prompt as previous, price_investigation as policy
from .insight_schema import obj
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash

PROMPT_VERSION = "independent-observation-v33-price-investigation"
REPAIR_VERSION = "independent-observation-repair-v29-price-investigation"
REVIEW_VERSION = "independent-observation-review-v22-price-investigation"
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION}
enabled = policy.enabled


def schema(packet, research):
    value = previous.schema(packet, research)
    value["properties"]["priceExplanation"] = policy.schema(packet)
    value["required"].append("priceExplanation")
    return value


def review_schema(packet):
    value = previous.review_schema(packet)
    value["properties"]["priceInvestigation"] = obj({"supported": {"type": "boolean"},
        "reason": {"type": "string", "minLength": 8, "maxLength": 600}})
    value["required"].append("priceInvestigation")
    return value


def prompt(packet, history, research):
    return policy.INSTRUCTIONS + previous.prompt(packet, history, research)


def review_prompt(packet, draft):
    return policy.INSTRUCTIONS + "\npriceInvestigation={supported,reason}로 고정된 가격 시점의 변동 질문에 답했는지 검토하세요. 과거 재무 설명으로 목표를 바꿨거나, 조회만 하고 원인을 확인했다고 주장하거나, 이후 보도를 당시 원인으로 소급하거나, 미확인 원인을 숨겼으면 거부하세요. 실제 문단의 대안과 반대 근거도 대조하세요.\n" + previous.review_prompt(packet, draft)


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
        raise EvidenceContractError("price investigation input changed")
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
