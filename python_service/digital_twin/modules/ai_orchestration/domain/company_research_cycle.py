"""V27 connects source calculations, research revisions and forward checks."""
from copy import deepcopy
import hashlib
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from . import business_insight as previous, quantitative_research

VERSION = quantitative_research.POLICY
enabled = quantitative_research.enabled
PROMPT_VERSION = "independent-observation-v27-company-research"
REPAIR_VERSION = "independent-observation-repair-v23-company-research"
REVIEW_VERSION = "independent-observation-review-v16-company-research"
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION}
INSTRUCTIONS = quantitative_research.INSTRUCTIONS


def prompt(packet, history, research):
    # Replace only the obsolete checkpoint instruction in this new version.
    text = previous.prompt(packet, history, research)
    text = text.replace("checkpoint={factId,metric,direction:increase|decrease,meaning}",
                        "checkpoint는 기업 연구 정량 계약의 구조")
    text = text.replace("동일 보고 기준의 다음 해 같은 기간 수치를 비교하므로 horizonDays>=365입니다.",
                        "정량 계약의 목표 보고기간은 horizonDays 이내여야 합니다.")
    return INSTRUCTIONS + text


def review_prompt(packet, draft):
    return INSTRUCTIONS + "\n예상 범위의 근거, 현재 결론과 반론, 계산과 인과 해석의 구분을 독립 검토하세요.\n" + previous.review_prompt(packet, draft)


schema = previous.schema
review_schema = previous.review_schema


def validate_input(envelope):
    from .execution_input import validate_execution_input, prompt_budget
    from .insight_repair import repair_prompt
    packet, version = envelope["current"], envelope["promptVersion"]
    if not enabled(packet) or not previous.enabled(packet):
        raise EvidenceContractError("company research policy missing")
    reviewing = version == REVIEW_VERSION
    expected = review_prompt(packet, envelope["draft"]) if reviewing else prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        expected = repair_prompt(expected, envelope["repair"])
    if (expected != envelope["prompt"] or hashlib.sha256(expected.encode()).hexdigest() != envelope["promptHash"]
            or envelope["outputSchema"] != (review_schema() if reviewing else schema(packet, envelope["researchResults"]))
            or len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes"))):
        raise EvidenceContractError("company research input changed")
    old = deepcopy(envelope)
    old["promptVersion"] = {PROMPT_VERSION: previous.PROMPT_VERSION, REPAIR_VERSION: previous.REPAIR_VERSION,
                            REVIEW_VERSION: previous.REVIEW_VERSION}[version]
    old["prompt"] = previous.review_prompt(packet, envelope["draft"]) if reviewing else previous.prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        old["prompt"] = repair_prompt(old["prompt"], envelope["repair"])
    old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
    validate_execution_input(old)
    return content_hash(envelope)
