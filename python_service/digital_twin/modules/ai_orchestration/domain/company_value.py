"""Company value explanations over existing, governed graph calculations."""
from copy import deepcopy
from digital_twin.modules.reasoning.contracts import company_value_context, EvidenceContractError

VERSION = "company-value-observation-v1"
PROMPT_VERSION = "independent-observation-v23-company-value"
REPAIR_VERSION = "independent-observation-repair-v19-company-value"
REVIEW_VERSION = "independent-observation-review-v12-company-value"
RETRIEVAL_VERSION = "independent-observation-retrieval-v10-company-value"

INSTRUCTIONS = """기업 가치 검토 계약:
시장의 단기 움직임보다 사업이 이익·현금으로 연결되는 과정과 현재 가격에 필요한 가정을 먼저 검토하세요.
companyValueContext는 기존 계산 모듈의 평가를 원천 근거에 연결한 읽기 전용 자료입니다. AI가 적정가 숫자를 만들거나 가정·승인·모델을 자동 변경하지 않습니다.
status=available인 모델의 주당 가치 범위만 평가 근거로 사용하세요. 서로 다른 모델을 임의로 평균하거나 기업 전체 가치와 주당 가치를 섞지 마세요.
summary는 사업 가치에 대한 핵심 판단, hypothesis는 가치의 핵심 가정·경쟁 설명·평가를 바꿀 구체적인 실적/사업 조건, comparison은 이전 평가와 달라진 근거를 설명합니다.
portfolioImpact는 가격과 가치의 차이가 보유·관심 상황에 갖는 의미입니다. 사업 개선 없이 가격만 하락한 것을 가치 상승으로 표현하지 마세요.
평가가 unchanged이면 기존 가정을 유지하고 새 가설·동일 조사·가치 변화 알림을 만들지 마세요. 가격 비교나 별도의 실제 새 근거는 구분해서 설명할 수 있습니다.
평가가 blocked/unavailable이면 계산값을 적정가로 제시하지 마세요. 부족한 원문·기간·주당 기준·모델/가정 검증 중 무엇이 필요한지 counterEvidence와 기존 questions에 구체적으로 남깁니다. 미조회는 자료 부재가 아닙니다.
회사·재무 근거가 있으면 사업 설명과 기존 businessResearch를 활용하세요. 관계 없는 거시 통계로 가치 상승의 원인을 만들지 마세요.
가치 근거는 current.facts의 정확한 항목으로 인용합니다. 유효한 estimateFactId를 summary 또는 hypothesis에서 인용한 가치 설명은 observations와 followUpConditions를 빈 배열로 둘 수 있습니다. 가격 조건을 억지로 추가하지 마세요.
수치·통화·평가 시점은 코드가 표시하므로 설명 문장에 숫자를 직접 쓰지 않습니다. 검토할 조건은 자동 추적 성공이나 매매 지시가 아닙니다.
"""


def enabled(packet):
    return packet.get("companyValuePolicy") == VERSION


def available(packet):
    return enabled(packet) and packet.get("companyValueContext", {}).get("status") == "available"


def annotate(packet, previous=()):
    from .execution_lanes import market
    from .evidence_purpose import enabled as purpose_enabled
    if not market(packet) or not purpose_enabled(packet) or any(f.get("kind") == "crypto-asset" for f in packet.get("facts", [])):
        return packet
    packet = {**packet, "companyValuePolicy": VERSION}
    if previous:
        prior = next((r.get("companyValue") for r in previous if r.get("companyValue")
                      and r.get("quality", {}).get("status") in {"accepted", "observation-only"}), None)
        if prior:
            packet["previousCompanyValue"] = deepcopy(prior)
    return refresh(packet)


def context(packet):
    value = company_value_context(packet)
    previous = {m["modelId"]: m for m in packet.get("previousCompanyValue", {}).get("models", [])}
    for model in value["models"]:
        old = previous.get(model["modelId"])
        model["change"] = ("initial" if not old else "unchanged" if old.get("basisFingerprint") == model["basisFingerprint"]
                           else "basis-changed")
    return value


def refresh(packet):
    return {**packet, "companyValueContext": context(packet)} if enabled(packet) else packet


def validate(packet):
    if not enabled(packet) or packet.get("companyValueContext") != context(packet):
        raise EvidenceContractError("company value context differs from frozen graph evidence")


def explains_value(result, packet=None):
    packet = packet or result.get("input", {})
    if not enabled(packet):
        return False
    ids = {m["estimateFactId"] for m in packet.get("companyValueContext", {}).get("models", []) if m["status"] == "available"}
    cited = {r.get("factId") for section in ("summary", "hypothesis")
             for r in result.get("claimEvidence", {}).get(section, []) if r.get("period") == "current"}
    return bool(ids.intersection(cited).intersection(result.get("evidenceIds", [])))


def quality_errors(result):
    packet = result["input"]
    if not enabled(packet):
        return []
    from digital_twin.modules.notifications.contracts import COMPANY_VALUE_PRESENTATION_VERSION
    if result.get("companyValuePresentationVersion") not in {None, COMPANY_VALUE_PRESENTATION_VERSION}:
        return ["지원하지 않는 기업 가치 상태 표시 계약입니다."]
    try:
        validate(packet)
    except EvidenceContractError:
        return ["기업 가치 평가와 동결된 계산 근거가 일치하지 않습니다."]
    if result.get("companyValue") != packet["companyValueContext"]:
        return ["기업 가치 평가 기록이 원래 계산과 달라졌습니다."]
    blocked = {m[key] for m in packet["companyValueContext"]["models"] if m["status"] != "available"
               for key in ("estimateFactId", "assessmentFactId", "inputFactId") if m[key]}
    if any(ref.get("factId") in blocked and ref.get("period") == "current"
           for section, refs in result.get("claimEvidence", {}).items() if section != "counterEvidence" for ref in refs):
        return ["검증되지 않은 가치평가를 결론의 근거로 사용했습니다."]
    if result.get("notification", {}).get("send") and packet["companyValueContext"]["status"] == "available" and not explains_value(result):
        return ["사용 가능한 기업 가치 평가를 핵심 설명에 연결하지 않았습니다."]
    return []
