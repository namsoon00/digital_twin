"""V38 makes a reviewed economic argument explicit without changing legacy prompts."""
from copy import deepcopy
import hashlib

from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from . import investment_meaning as policy, analyst_prompt as previous
from .insight_schema import obj, evidence_references

PROMPT_VERSION = "independent-observation-v38-investment-meaning"
REPAIR_VERSION = "independent-observation-repair-v34-investment-meaning"
REVIEW_VERSION = "independent-observation-review-v27-investment-meaning"
RETRIEVAL_VERSION = "independent-observation-retrieval-v24-investment-meaning"
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION, RETRIEVAL_VERSION}
enabled = policy.enabled
INSTRUCTIONS = """투자 의미 연결 계약:
investmentMeaning에 하나의 핵심 question, 수익·비용·현금창출·주당 가치 또는 구체적인 보유 위험으로 이어지는 mechanism과 현재 원천 evidence를 기록한다.
status=analyzed는 실제 인용한 자료로 판단을 진전시킨 경우다. insufficient는 자료 부족, not-applicable은 기업 의미를 논할 대상이 아닌 경우다. 새 가설이나 범위를 만들어 알림을 채우지 않는다.
explanations는 가장 유력한 설명과 경쟁 설명 두 개를 비교한다. 각 explanation에 support·against의 정확한 원천 필드, assessment=preferred/possible/weakened/unresolved와 reason을 쓴다.
회계상 현금 변화의 분해와 수요·비용·회수 지연의 인과 해석을 구분한다. 원문 없는 설명은 unresolved이며 미확인을 반증으로 쓰지 않는다.
expectation은 실제와 회사 가이던스·출처가 있는 전망을 같은 기간·범위·단위로 비교할 수 있을 때만 status=compared, actual과 expected에 서로 다른 근거 필드를 쓴다.
기업 개선이 가격에 얼마나 반영됐는지는 별도 근거가 필요하다. AI 가정·과거 답변·가격 위치를 시장 기대나 회사 전망으로 승격하지 않는다. 기대 근거가 없으면 unavailable, 비교가 불필요하면 not-required로 쓰고 expected=[]로 둔다.
원래 연구 질문이 부분 답변이어도 현재 원문으로 확인한 별도 사실의 의미를 평가할 수 있다. 원래 질문을 닫거나 완료 기준을 바꾸지 않는다. 오래된 자료를 오늘 가격의 원인이나 최신 실적으로 쓰지 않는다.
nextCheck에는 question, 확인할 source, 가설을 뒷받침하는 supports와 약화하는 weakens를 구체적으로 쓴다. analyzed이면 그 question을 기존 questions에 capability=observe로 정확히 연결한다.
수치 범위를 정당화할 근거가 있으면 기존 businessResearch의 정량 checkpoint 계약을 사용한다. 근거가 없으면 범위를 만들지 말고 원문으로 판별할 조건을 쓴다.
기존 analystAssessment·judgmentChange와 일치시킨다. mechanism의 evidence는 summary/hypothesis/portfolioImpact에도 인용하고 모든 새 참조의 factId를 evidenceIds에 포함한다.
previousAnalyses의 investmentMeaning은 당시의 질문·설명·지지/약화 조건이다. 이번 원천으로 어느 조건을 확인했는지 comparison과 judgmentChange에 기록한다. 같은 자료의 재검토는 강화가 아니며, 자료 없음·아직 오지 않은 보고기간은 반증이 아니다.
모든 새 문장은 숫자 없는 간결한 한국어이며 매매 지시를 하지 않는다. 설명이 바뀌지 않았거나 가격·평균선 설명뿐이면 notification.send=false다.
"""
READ_INSTRUCTIONS = """기업 판단을 위한 자료 선택:
기존 질문과 새 원문으로 수익·비용·현금창출·가치 가정 중 무엇을 판단할 수 있는지 먼저 정한다.
현재 읽을 수 있는 원문과 비교기간 보고서, 경쟁 설명을 판별할 자료, 출처가 있는 가이던스·전망을 선택한다.
승인된 부분 답변은 원문을 찾는 기억이며 질문 해결의 증거가 아니다. 읽은 원문을 반복 조회하지 않는다.
현재 가격의 원인을 밝히는 질문과 날짜가 명시된 기업 판단을 구분한다. 해당 기업 설명에 불필요한 시세·다른 질문의 미완료를 보류 조건으로 추가하지 않는다.
자료가 없으면 현재 발급된 목록에서 부족한 정확한 원천을 기록하고 기존 회차·입력 한도 안에서 종료한다.
"""


def prompt(packet, history, research):
    return INSTRUCTIONS + previous.prompt(packet, history, research)


def schema(packet, research):
    value = previous.schema(packet, research)
    value.setdefault("$defs", {})["investmentMeaningEvidence"] = evidence_references(packet, usable=True, current_only=True)
    value["properties"]["investmentMeaning"] = policy.schema()
    value["required"].append("investmentMeaning")
    return value


def review_prompt(packet, draft):
    return (INSTRUCTIONS + "\n독립 검토 investmentMeaning={supported,discriminating,expectationsSupported,followUpLinked,reason}를 작성한다. "
        "단순 원문 인용이 아니라 경제적 연결과 두 설명의 판별력을 확인한다. 근거 없는 기대 비교·서로 다른 기준의 비교·확인 불가를 반증으로 쓰면 거부한다. "
        "후속 질문이 실제 observe 과제로 연결되고 지지·약화 조건이 서로 다른지 검토한다. 분석 불가를 정직하게 남긴 경우에는 그 한계와 무발송 판단의 타당성을 검토한다.\n"
        + previous.review_prompt(packet, draft))


def review_schema(packet):
    value = previous.review_schema(packet)
    value["properties"]["investmentMeaning"] = obj({**{k: {"type": "boolean"} for k in
        ("supported", "discriminating", "expectationsSupported", "followUpLinked")},
        "reason": {"type": "string", "minLength": 8, "maxLength": 500}})
    value["required"].append("investmentMeaning")
    return value


def freeze_retrieval(current, context, max_bytes):
    value = previous.freeze_retrieval(current, context, max_bytes)
    value.update(promptVersion=RETRIEVAL_VERSION, prompt=READ_INSTRUCTIONS + value["prompt"])
    value["promptHash"] = hashlib.sha256(value["prompt"].encode()).hexdigest()
    if len(value["prompt"].encode()) > value["promptBudgetBytes"]:
        raise EvidenceContractError("investment meaning retrieval exceeds context budget")
    return value


def validate_input(envelope):
    from .execution_input import prompt_budget
    from .insight_repair import repair_prompt
    packet, version = envelope["current"], envelope["promptVersion"]
    if not enabled(packet) or not previous.enabled(packet):
        raise EvidenceContractError("investment meaning policy missing")
    if version == RETRIEVAL_VERSION:
        if freeze_retrieval(packet, envelope["retrievalContext"], envelope["promptBudgetBytes"]) != envelope:
            raise EvidenceContractError("investment meaning retrieval changed")
        old = previous.freeze_retrieval(packet, envelope["retrievalContext"], envelope["promptBudgetBytes"])
        previous.validate_input(old)
        return content_hash(envelope)
    reviewing = version == REVIEW_VERSION
    expected = review_prompt(packet, envelope["draft"]) if reviewing else prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        expected = repair_prompt(expected, envelope["repair"])
    if (expected != envelope["prompt"] or hashlib.sha256(expected.encode()).hexdigest() != envelope["promptHash"]
            or envelope["outputSchema"] != (review_schema(packet) if reviewing else schema(packet, envelope["researchResults"]))
            or len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes"))):
        raise EvidenceContractError("investment meaning frozen input changed")
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
