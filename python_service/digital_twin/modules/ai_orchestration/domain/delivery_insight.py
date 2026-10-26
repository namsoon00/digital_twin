"""Versioned author/review contract for added meaning across delivered routes."""
from copy import deepcopy
import hashlib
from digital_twin.modules.reasoning.contracts import EvidenceContractError, content_hash
from . import notification_contribution as contribution, business_insight, company_research_cycle, partial_judgment
from .insight_schema import obj, choice, array, evidence_references

PROMPT_VERSION = "independent-observation-v28-delivery-context"
REPAIR_VERSION = "independent-observation-repair-v24-delivery-context"
REVIEW_VERSION = "independent-observation-review-v17-delivery-context"
VERSIONS = {PROMPT_VERSION, REPAIR_VERSION, REVIEW_VERSION}
enabled = contribution.enabled


def base(packet):
    if partial_judgment.enabled(packet):
        return partial_judgment
    return company_research_cycle if company_research_cycle.enabled(packet) else business_insight


def contextual_text(text):
    # Only V28 relaxes source novelty; frozen earlier prompts stay byte-exact.
    return text.replace("kind=business는 새 원천이 사업 판단을 처음 세우거나 강화·약화·폐기한 경우",
                        "kind=business는 사용 가능한 원천의 새 분석이 사업 판단을 세우거나 강화·약화·수정한 경우").replace(
        "summary와 comparison에 새 사실, 이전 판단의 변화, 보유·관심 상황의 의미를 먼저 쓰세요.",
        "summary와 comparison에 기존 알림에 더하는 해석, 판단·근거의 변화, 보유·관심 상황의 의미를 먼저 쓰세요.").replace(
        "첫 관찰/이전 발송 없음은 current.lastDeliveredNotification이 비어 있는지로 검증하는 메타데이터 진술입니다.",
        "첫 AI 관찰 여부는 current.lastDeliveredNotification으로 확인하되, 전체 이전 발송 여부는 deliveredNotificationContext의 범위와 누락을 함께 확인하세요. 첫 AI 관찰을 사용자에게 전달한 적 없는 사실로 혼동하지 마세요.").replace(
        "새 원문이 기존 판단을 왜 바꾸는지 설명되지 않으면 supported=false입니다.",
        "사용 가능한 원천에 근거한 추가 해석이나 판단·위험 변화가 설명되지 않으면 supported=false입니다. 같은 원천의 재해석도 검토하세요.")


def prompt(packet, history, research):
    contribution.validate_context(packet)
    return contribution.INSTRUCTIONS + contextual_text(base(packet).prompt(packet, history, research))


def ids_schema(packet):
    ids = contribution.receipt_ids(packet)
    return {**array(choice(ids or [""])), "minItems": len(ids), "maxItems": len(ids)}


def schema(packet, research):
    result = base(packet).schema(packet, research)
    result["properties"]["notificationContribution"] = obj({
        "kind": choice(contribution.KINDS), "comparedReceiptIds": ids_schema(packet),
        "section": choice(contribution.SECTIONS), "reason": {"type": "string", "minLength": 8, "maxLength": 500},
        "evidence": {**array(evidence_references(packet, usable=True, current_only=True)), "maxItems": 8}})
    result["required"].append("notificationContribution")
    return result


def review_prompt(packet, draft):
    contribution.validate_context(packet)
    return (contribution.INSTRUCTIONS + "\n독립 검토에서 notificationContribution={supported,kind,comparedReceiptIds,reason}를 별도로 판정하세요. "
            "제공된 모든 전달 항목과 실제 전달할 문단을 비교하세요. 작성자가 추가 가치라고 명명한 것만으로 승인하지 마세요. "
            "조건 문장을 풀어쓴 설명에는 supported=false를 반환하세요. 같은 근거의 새 분석·이전 오류 수정·같은 결론의 중요한 "
            "근거/위험 변화는 내용과 원천이 뒷받침되면 승인할 수 있습니다. 검증은 예측 정확성 인증이 아닙니다.\n"
            + contextual_text(base(packet).review_prompt(packet, draft)))


def review_schema(packet):
    result = base(packet).review_schema()
    result["properties"]["notificationContribution"] = obj({
        "supported": {"type": "boolean"}, "kind": choice(contribution.KINDS),
        "comparedReceiptIds": ids_schema(packet), "reason": {"type": "string", "minLength": 8, "maxLength": 500}})
    result["required"].append("notificationContribution")
    return result


def validate_input(envelope):
    from .execution_input import validate_execution_input, prompt_budget
    from .insight_repair import repair_prompt
    packet, version = envelope["current"], envelope["promptVersion"]
    if not enabled(packet) or not business_insight.enabled(packet):
        raise EvidenceContractError("delivery insight policy missing")
    reviewing = version == REVIEW_VERSION
    expected = review_prompt(packet, envelope["draft"]) if reviewing else prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        expected = repair_prompt(expected, envelope["repair"])
    if (expected != envelope["prompt"] or hashlib.sha256(expected.encode()).hexdigest() != envelope["promptHash"]
            or envelope["outputSchema"] != (review_schema(packet) if reviewing else schema(packet, envelope["researchResults"]))
            or len(expected.encode()) > prompt_budget(envelope.get("promptBudgetBytes"))):
        raise EvidenceContractError("delivery insight input changed")
    previous, old = base(packet), deepcopy(envelope)
    old["promptVersion"] = {PROMPT_VERSION: previous.PROMPT_VERSION, REPAIR_VERSION: previous.REPAIR_VERSION,
                            REVIEW_VERSION: previous.REVIEW_VERSION}[version]
    old["prompt"] = previous.review_prompt(packet, envelope["draft"]) if reviewing else previous.prompt(packet, envelope["previousAnalyses"], envelope["researchResults"])
    if version == REPAIR_VERSION:
        old["prompt"] = repair_prompt(old["prompt"], envelope["repair"])
    old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
    old["outputSchema"] = previous.review_schema() if reviewing else previous.schema(packet, envelope["researchResults"])
    validate_execution_input(old)
    return content_hash(envelope)
