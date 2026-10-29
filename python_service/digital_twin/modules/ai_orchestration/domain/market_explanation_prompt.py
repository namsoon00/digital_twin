"""Versioned price context author/reviewer, using the existing evidence schema."""
from copy import deepcopy
import hashlib
from . import external_influence_prompt as previous, market_explanation as policy
from .insight_schema import obj
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash

PROMPT_VERSION = "independent-observation-v32-price-context"
REPAIR_VERSION = "independent-observation-repair-v28-price-context"
REVIEW_VERSION = "independent-observation-review-v21-price-context"
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION}
enabled, schema = policy.enabled, previous.schema


def review_schema(packet):
    value = previous.review_schema(packet)
    value["properties"]["marketExplanation"] = obj({"supported": {"type": "boolean"},
        "reason": {"type": "string", "minLength": 8, "maxLength": 600}})
    value["required"].append("marketExplanation")
    return value


def prompt(packet, history, research):
    return policy.INSTRUCTIONS + previous.prompt(packet, history, research)


def review_prompt(packet, draft):
    return policy.INSTRUCTIONS + "\nmarketExplanation={supported,reason}로 가격 비교 기간과 공개 시각, 시장 비교의 동일 기준, 필수·보충 부족의 분류 및 본문 한계를 확인하세요. 자료 부족을 숨기거나 가격 변화와 사업 영향을 인과관계로 단정한 설명은 거부하세요.\n" + previous.review_prompt(packet, draft)


def validate_input(envelope):
    from .execution_input import validate_execution_input, prompt_budget
    from .insight_repair import repair_prompt
    packet, version = envelope["current"], envelope["promptVersion"]
    if not enabled(packet):
        raise EvidenceContractError("market explanation policy missing")
    reviewing = version == REVIEW_VERSION
    expected = review_prompt(packet, envelope["draft"]) if reviewing else prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        expected = repair_prompt(expected, envelope["repair"])
    if (expected != envelope["prompt"] or hashlib.sha256(expected.encode()).hexdigest() != envelope["promptHash"]
            or envelope["outputSchema"] != (review_schema(packet) if reviewing else schema(packet, envelope["researchResults"]))
            or len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes"))):
        raise EvidenceContractError("market explanation input changed")
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
