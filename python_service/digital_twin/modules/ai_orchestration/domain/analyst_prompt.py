"""Versioned memory-to-thesis review over the existing bounded observation lane."""
from copy import deepcopy
import hashlib

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from . import analyst_memory as policy, compact_opinion_prompt as previous
from .insight_schema import obj, choice, array, evidence_references

PROMPT_VERSION = "independent-observation-v37-analyst-memory"
REPAIR_VERSION = "independent-observation-repair-v33-analyst-memory"
REVIEW_VERSION = "independent-observation-review-v26-analyst-memory"
RETRIEVAL_VERSION = "independent-observation-retrieval-v23-analyst-memory"
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION, RETRIEVAL_VERSION}
enabled = policy.enabled
TEXT = {"type": "string", "minLength": 8, "maxLength": 500}
READ_INSTRUCTIONS = """기업 판단을 위한 조회 목표 (뒤의 가격 조사 목표보다 우선):
시세 기준·공개 시각·원인 확인의 제약은 유지하되, 조사 충분성은 가격 상태 설명만으로 판단하지 않는다.
analystMemory.items가 가리키는 기존 질문·확인된 답변·기업 가설을 먼저 대조하고 사업·현금창출·가치 가정·보유 위험에 더할 의미를 찾는다.
자동으로 읽은 원문과 businessEvidence는 이미 읽은 자료다. 원문을 다시 읽지 말고 부족한 비교 기간·기업 노출·반론만 발급된 목록에서 선택한다.
읽을 수 있는 기업 노출·공시 원문이 있는데 가격과 평균선만 선택하고 충분하다고 끝내지 않는다. 실제 미해결 판단에 필요한 자료가 있을 때 기존 회차·요청 한도에서 읽는다.
이번 변동의 원인을 모르는 것과 기업 가치의 의미를 모르는 것은 다르다. 기간별 현금흐름·희석·부채·수익성의 확인 가능한 사실은 날짜를 유지해 검토할 수 있다.
과거 사업 수치를 이번 가격 변동의 원인으로 소급하지 않는다. 의미 있는 설명을 만들 근거가 없으면 그 한계를 남긴다.
최종 작성에 보호된 기업 근거가 남을 수 있으므로 authorBudget을 확인한다. 필수 원문이 읽혔거나 더 읽을 적합한 자료가 없으면 finish/defer하고, 같은 조회·질문을 반복하지 않는다.
출력은 기존 조회 JSON 형식만 사용한다. 이 단계는 analystAssessment 작성이나 투자 행동 결정 단계가 아니다.
"""
INSTRUCTIONS = """기업 판단과 기억 연결 계약:
analystMemory.items의 각 기억을 지정된 path의 과거 답변·기업 가설과 대조한다. 기억은 당시 해석이며 새 사실이나 매매 권한이 아니다.
이번 자료가 사업의 수익·비용·현금흐름, 가치 가정, 기업 노출이나 보유 위험의 이해를 어떻게 바꾸는지 먼저 검토한다.
analystAssessment={memories,insightKind,meaning,counterEvidence}를 작성한다. memories는 모든 memoryId에 대해
{memoryId,effect,reason,evidence,nextEvidence}를 하나씩 쓴다. effect는 strengthened/weakened/unchanged/unresolved/not-relevant.
reason은 원래 답·가설의 어느 부분과 이번 근거를 대조했는지, nextEvidence는 남은 판별 자료 또는 재검토할 구체적인 변화다.
강화·약화는 이번 facts의 정확한 필드를 evidence로 인용해야 한다. 같은 자료의 반복은 unchanged이며 새 발전이라고 쓰지 않는다.
원문이 선택되지 않았거나 비교 기간·노출 경로가 확인되지 않으면 unresolved다. 근거 부재는 반증이 아니다. 무관하면 not-relevant와 이유를 쓴다.
이미 확인된 답을 다시 질문하지 않는다. 추가 자료가 실제로 필요하면 기존 질문을 유지하고 기존 questions 경로의 완료 요건을 구체화한다.
insightKind는 business/valuation/portfolio/market-context/correction/none. meaning은 고객이 새롭게 이해할 내용, counterEvidence는 그 설명의 반론이다.
추가 가치가 있으면 summary/hypothesis/portfolioImpact에 그 내용을 쓰고 같은 원천을 claimEvidence에도 인용한다.
평균선 위치·기울기·손익·계속된 비중 초과의 설명 수정만으로는 인사이트가 아니다. 이 경우 notification.send=false.
새로운 실제 비중 한도 초과처럼 구체적인 보유 위험 변화는 허용하지만, 같은 위험의 반복이나 시세 시각 수정은 제외한다.
원인이나 적정가를 만들지 않는다. 기업별 노출·비교 기준이 없는 거시 일반론과 뉴스의 단순 첨부는 추가 가치가 아니다.
기억을 모두 고려했다는 것과 가설의 검증·질문 해결은 다르다. 이 기록으로 질문을 닫거나 가설 상태를 변경하지 않는다.
"""


def prompt(packet, history, research):
    return INSTRUCTIONS + previous.prompt(packet, history, research)


def schema(packet, research):
    result = previous.schema(packet, research)
    ids = [r["memoryId"] for r in packet.get("analystMemory", {}).get("items", [])]
    result["properties"]["analystAssessment"] = obj({
        "memories": {**array(obj({"memoryId": choice(ids or [""]), "effect": choice(policy.EFFECTS),
            "reason": TEXT, "evidence": {**array(evidence_references(packet, usable=True, current_only=True)), "maxItems": 4},
            "nextEvidence": TEXT})), "minItems": len(ids), "maxItems": len(ids)},
        "insightKind": choice(policy.KINDS), "meaning": TEXT, "counterEvidence": TEXT})
    result["required"].append("analystAssessment")
    return result


def review_prompt(packet, draft):
    return (INSTRUCTIONS + "\n독립 검토의 analystAssessment={supported,substantive,memoryIds,reason}를 작성한다. "
        "모든 기억의 실제 사용·누락·반론을 대조하고 memoryIds에 검토한 모든 ID를 쓴다. "
        "substantive는 핵심 문장에 연결된 기업/가치/시장 전달 경로/구체적인 위험의 추가 의미가 있을 때만 true다. "
        "작성자의 추가 가치 선언, 반대 근거에만 등장한 뉴스, 평균선 설명의 수정만으로 승인하지 않는다. "
        "새 근거가 원래 답·가설의 어떤 부분을 바꾸는지 설명하지 못하거나 미확인을 반증으로 썼으면 supported=false다.\n"
        + previous.review_prompt(packet, draft))


def review_schema(packet):
    result = previous.review_schema(packet)
    ids = [r["memoryId"] for r in packet.get("analystMemory", {}).get("items", [])]
    result["properties"]["analystAssessment"] = obj({"supported": {"type": "boolean"},
        "substantive": {"type": "boolean"}, "memoryIds": {**array(choice(ids or [""])), "minItems": len(ids), "maxItems": len(ids)}, "reason": TEXT})
    result["required"].append("analystAssessment")
    return result


def freeze_retrieval(current, context, max_bytes):
    from . import reliable_retrieval
    if not enabled(current):
        raise EvidenceContractError("analyst retrieval policy missing")
    value = reliable_retrieval.freeze(current, context, max_bytes)
    value.update(promptVersion=RETRIEVAL_VERSION, prompt=READ_INSTRUCTIONS + value["prompt"])
    value["promptHash"] = hashlib.sha256(value["prompt"].encode()).hexdigest()
    if len(value["prompt"].encode()) > value["promptBudgetBytes"]:
        raise EvidenceContractError("analyst retrieval exceeds context budget")
    return value


def validate_input(envelope):
    from .execution_input import prompt_budget
    from .insight_repair import repair_prompt
    packet, version = envelope["current"], envelope["promptVersion"]
    if version == RETRIEVAL_VERSION:
        from . import reliable_retrieval
        if freeze_retrieval(packet, envelope["retrievalContext"], envelope["promptBudgetBytes"]) != envelope:
            raise EvidenceContractError("analyst retrieval frozen input changed")
        reliable_retrieval.validate_trace(envelope["retrievalContext"].get("trace", []))
        return content_hash(envelope)
    if not enabled(packet) or not previous.enabled(packet):
        raise EvidenceContractError("analyst memory policy missing")
    reviewing = version == REVIEW_VERSION
    expected = review_prompt(packet, envelope["draft"]) if reviewing else prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        expected = repair_prompt(expected, envelope["repair"])
    if (expected != envelope["prompt"] or hashlib.sha256(expected.encode()).hexdigest() != envelope["promptHash"]
            or envelope["outputSchema"] != (review_schema(packet) if reviewing else schema(packet, envelope["researchResults"]))
            or len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes"))):
        raise EvidenceContractError("analyst memory frozen input changed")
    old = deepcopy(envelope)
    old["promptVersion"] = {PROMPT_VERSION: previous.PROMPT_VERSION, REPAIR_VERSION: previous.REPAIR_VERSION,
                            REVIEW_VERSION: previous.REVIEW_VERSION}[version]
    old["prompt"] = previous.review_prompt(packet, envelope["draft"]) if reviewing else previous.prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        old["prompt"] = repair_prompt(old["prompt"], envelope["repair"])
    old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
    old["outputSchema"] = previous.review_schema(packet) if reviewing else previous.schema(packet, envelope["researchResults"])
    previous.validate_input(old)
    return content_hash(envelope)
