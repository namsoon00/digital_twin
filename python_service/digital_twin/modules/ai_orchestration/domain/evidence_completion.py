"""V25 keeps completion admissible and citation eligibility consistent with review."""
from copy import deepcopy
import hashlib
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from . import judgment_policy as previous

VERSION = 'evidence-completion-v1'
PROMPT_VERSION = 'independent-observation-v25-evidence-completion'
REPAIR_VERSION = 'independent-observation-repair-v21-evidence-completion'
REVIEW_VERSION = 'independent-observation-review-v14-evidence-completion'
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION}
INSTRUCTIONS = '''근거 연결과 작성 완료 계약:
출력 스키마의 evidence는 해당 필드의 사용 검증을 통과한 인용만 포함합니다. limitation은 한계 설명 전용입니다. 이전 알림도 원문 검증 정보가 보존된 항목만 비교 근거로 인용하세요.
businessEvidence.omittedByBudget와 companyValueEvidence.omitted는 이번 입력에서 미조회한 자료이며 자료 부재가 아닙니다. 필요하면 해당 원문이나 보고서 지표를 읽고 관련 없는 선택을 해제하세요.
companyValueReadiness는 평가 보류의 실제 단계입니다. 계산 완료·모델 승인 대기·입력 연결 미확인을 구분하고, 다른 후보 자료의 제외 사유를 채택한 자료의 결함으로 바꾸지 마세요. 승인 대기에 같은 자료를 반복 조사하거나 스스로 승인하지 마세요.
'''


def enabled(packet):
    return packet.get('evidenceCompletionPolicy') == VERSION


def prompt(packet, history, research):
    return INSTRUCTIONS + previous.prompt(packet, history, research)


def review_prompt(packet, draft):
    return INSTRUCTIONS + previous.review_prompt(packet, draft)


def validate_input(envelope):
    from .execution_input import validate_execution_input, prompt_budget
    from .insight_repair import repair_prompt
    packet, version = envelope['current'], envelope['promptVersion']
    if not enabled(packet) or not previous.enabled(packet):
        raise EvidenceContractError('evidence completion policy missing')
    reviewing = version == REVIEW_VERSION
    expected = review_prompt(packet, envelope['draft']) if reviewing else prompt(packet, envelope['previousAnalyses'], envelope['researchResults'])
    if version == REPAIR_VERSION:
        expected = repair_prompt(expected, envelope['repair'])
    if (expected != envelope['prompt'] or hashlib.sha256(expected.encode()).hexdigest() != envelope['promptHash']
            or len(expected.encode()) > prompt_budget(envelope.get('promptBudgetBytes'))):
        raise EvidenceContractError('evidence completion input changed')
    old = deepcopy(envelope)
    old['promptVersion'] = {PROMPT_VERSION: previous.PROMPT_VERSION, REPAIR_VERSION: previous.REPAIR_VERSION,
                            REVIEW_VERSION: previous.REVIEW_VERSION}[version]
    old['prompt'] = previous.review_prompt(packet, envelope['draft']) if reviewing else previous.prompt(packet, envelope['previousAnalyses'], envelope['researchResults'])
    if version == REPAIR_VERSION:
        old['prompt'] = repair_prompt(old['prompt'], envelope['repair'])
    old['promptHash'] = hashlib.sha256(old['prompt'].encode()).hexdigest()
    validate_execution_input(old)
    return content_hash(envelope)
