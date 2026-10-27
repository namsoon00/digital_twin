"""Versioned company impact author and reviewer over immutable graph sources."""
from copy import deepcopy
import hashlib
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from . import external_influence as policy, question_purpose_prompt as previous
from .insight_schema import obj

PROMPT_VERSION = "independent-observation-v31-external-influence"
REPAIR_VERSION = "independent-observation-repair-v27-external-influence"
REVIEW_VERSION = "independent-observation-review-v20-external-influence"
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION}
enabled = policy.enabled


def prompt(packet, history, research):
    return policy.INSTRUCTIONS + previous.prompt(packet, history, research)


def schema(packet, research):
    result = previous.schema(packet, research)
    result["properties"]["externalInfluences"] = policy.schema(packet)
    result["required"].append("externalInfluences")
    return result


def review_prompt(packet, draft):
    return policy.INSTRUCTIONS + "\nexternalInfluences={supported,reason}로 모든 외부 영향 경로의 사건 원문·기업 노출·전제·반론·기간과 실제 전달 문단을 독립 검토하세요. 기업별 노출 근거 없이 일반론을 확정 영향으로 쓴 경우 거부하세요. 빈 배열은 관련된 분석 제안 없음으로 검토합니다. 검토 승인은 인과성이나 예측 정확성 인증이 아닙니다.\n" + previous.review_prompt(packet, draft)


def review_schema(packet):
    result = previous.review_schema(packet)
    result["properties"]["externalInfluences"] = obj({"supported": {"type": "boolean"},
        "reason": {"type": "string", "minLength": 8, "maxLength": 600}})
    result["required"].append("externalInfluences")
    return result


def validate_input(envelope):
    from .execution_input import validate_execution_input, prompt_budget
    from .insight_repair import repair_prompt
    packet, version = envelope["current"], envelope["promptVersion"]
    if not enabled(packet) or not previous.enabled(packet):
        raise EvidenceContractError("external influence policy missing")
    reviewing = version == REVIEW_VERSION
    expected = review_prompt(packet, envelope["draft"]) if reviewing else prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        expected = repair_prompt(expected, envelope["repair"])
    if (expected != envelope["prompt"] or hashlib.sha256(expected.encode()).hexdigest() != envelope["promptHash"]
            or envelope["outputSchema"] != (review_schema(packet) if reviewing else schema(packet, envelope["researchResults"]))
            or len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes"))):
        raise EvidenceContractError("external influence input changed")
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
