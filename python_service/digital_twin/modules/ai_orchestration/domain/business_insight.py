"""V26 requires explicit judgment progress and comparable business evidence."""
from copy import deepcopy
import hashlib

from digital_twin.modules.reasoning.contracts import content_hash, EvidenceContractError
from . import evidence_completion as previous, evidence_readiness, business_progress
from .insight_schema import obj, choice, array, evidence_references, review_schema as old_review_schema

VERSION = business_progress.VERSION
enabled = business_progress.enabled
PROMPT_VERSION = "independent-observation-v26-business-progress"
REPAIR_VERSION = "independent-observation-repair-v22-business-progress"
REVIEW_VERSION = "independent-observation-review-v15-business-progress"
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION}

INSTRUCTIONS = """판단 진전 계약:
businessEvidence.reportRequirements는 같은 동결 재고에서 확인된 최신 보고기간입니다. 현재 기업 설명에 과거 재무를 쓰려면 해당 최신 보고서도 읽으세요. 보고기간과 수집 시각을 구분하고 read_report로 필요한 지표만 읽어 비교 자료를 함께 확보하세요. original-question은 원래 대상 기간을 유지합니다. 무관한 시세·뉴스 설명에 재무자료를 강제로 요구하지 마세요.
기업 실적은 같은 기간·회계 범위·통화·분기/누적 기준끼리 비교하세요. 이익과 잉여현금흐름이 엇갈리면 영업현금흐름, 설비투자, 운전자본을 대조해 가능한 설명을 구분하세요. 분해 근거가 없으면 현금 전환 악화가 확인됐다고 하지 마세요. 서로 다른 기간의 IR 설명으로 빈 현금흐름 근거를 메우지 마세요.
judgmentChange={kind,impact,reason,evidence}를 작성하세요. kind=business는 새 원천이 사업 판단을 처음 세우거나 강화·약화·폐기한 경우, market은 검증된 시세·시장 맥락 변화, none은 판단 진전이 없는 연구 기록입니다. impact는 new/supports/weakens/invalidates/unchanged/insufficient입니다. reason에 기존 판단과 새 근거가 바꾼 판단을 설명하고 evidence에는 핵심 설명·가설에 인용한 현재 원천 필드만 연결하세요. 출처 시계·검토 상태만 바뀐 것은 새 사업 근거가 아닙니다.
summary와 comparison에 새 사실, 이전 판단의 변화, 보유·관심 상황의 의미를 먼저 쓰세요. 새 사업 근거 없이 근거 부족으로 기존 가설을 유지하면 unchanged/insufficient이며 notification.send=false입니다. 새 가설을 만들어 발송 조건을 채우지 마세요. 부족한 비교 자료는 기존 questions 조사 흐름에 구체적인 기간과 지표를 남기세요.
market 알림은 시세 변화와 의미에 집중하고 summary/hypothesis에 미완료 사업 설명을 붙이지 마세요. 각 설명은 짧게 쓰고 평가 입력 연결 오류·검토 대기 체크리스트를 본문에 나열하지 마세요. 사업 근거 부족과 평가 승인 대기는 내부 기록에서 유지합니다.
"""


def prompt(packet, history, research):
    return INSTRUCTIONS + previous.prompt(packet, history, research)


def review_prompt(packet, draft):
    return (INSTRUCTIONS + "\n독립 검토의 judgmentChange={supported,reason}에서 실제 판단 진전을 별도로 심사하세요. "
            "사업 판단이 근거 부족인데 가격 위치·보유 표시 변경만으로 business를 승인하면 안 됩니다. "
            "선택된 문장과 근거의 일치뿐 아니라 최신 관련 자료·동일 기준 비교·현금흐름 경쟁 설명이 충분한지 검토하세요. "
            "새 원문이 기존 판단을 왜 바꾸는지 설명되지 않으면 supported=false입니다.\n"
            + previous.review_prompt(packet, draft))


def schema(packet, research):
    value = evidence_readiness.schema(packet, research)
    value["properties"]["judgmentChange"] = obj({"kind": choice(["business", "market", "none"]),
        "impact": choice(business_progress.IMPACTS), "reason": {"type": "string"},
        "evidence": {**array(evidence_references(packet, usable=True, current_only=True)), "maxItems": 8}})
    value["required"].append("judgmentChange")
    return value


def review_schema():
    value = old_review_schema()
    value["properties"]["judgmentChange"] = obj({"supported": {"type": "boolean"}, "reason": {"type": "string"}})
    value["required"].append("judgmentChange")
    return value


def validate_input(envelope):
    from .execution_input import validate_execution_input, prompt_budget
    from .insight_repair import repair_prompt
    packet, version = envelope["current"], envelope["promptVersion"]
    if not enabled(packet) or not previous.enabled(packet):
        raise EvidenceContractError("business insight policy missing")
    reviewing = version == REVIEW_VERSION
    expected = review_prompt(packet, envelope["draft"]) if reviewing else prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        expected = repair_prompt(expected, envelope["repair"])
    expected_schema = review_schema() if reviewing else schema(packet, envelope["researchResults"])
    if (expected != envelope["prompt"] or hashlib.sha256(expected.encode()).hexdigest() != envelope["promptHash"]
            or expected_schema != envelope["outputSchema"]
            or len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes"))):
        raise EvidenceContractError("business insight input changed")
    old = deepcopy(envelope)
    old["promptVersion"] = {PROMPT_VERSION: previous.PROMPT_VERSION, REPAIR_VERSION: previous.REPAIR_VERSION,
                            REVIEW_VERSION: previous.REVIEW_VERSION}[version]
    old["prompt"] = previous.review_prompt(packet, envelope["draft"]) if reviewing else previous.prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        old["prompt"] = repair_prompt(old["prompt"], envelope["repair"])
    old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
    old["outputSchema"] = old_review_schema() if reviewing else evidence_readiness.schema(packet, envelope["researchResults"])
    validate_execution_input(old)
    return content_hash(envelope)
