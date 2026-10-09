"""AI proposes bounded research work; it cannot grant itself execution authority."""
import hashlib
import json
from datetime import datetime, timezone
from digital_twin.modules.reasoning.contracts import evidence_change_identity


CAPABILITIES = {
    "observe": "현재 근거와 이전 분석을 비교하고 다음 조사와 확인 시점을 정합니다.",
    "research": "검증 가능한 출처를 수집하고 기존 근거 검증·그래프 반영 절차로 보냅니다.",
    "develop-hypothesis": "관찰 근거에서 검증할 가설 개선 질문을 기존 격리 실험 절차로 보냅니다.",
}


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def identity(*values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def bounded(value, default, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def enabled(settings):
    return str(settings.get("aiControlEnabled", "true")).lower() not in {"false", "0", "off"}


def observation_fingerprint(packet, research):
    return evidence_change_identity(packet, research, packet.get("questionsToCheck", []))


def validate_plan(value, packet, research=(), require_research=False):
    if not isinstance(value, dict):
        raise ValueError("AI plan must be an object")
    # No model-authored account, symbol, command, source URL or action is executable.
    allowed = {"summary", "hypothesis", "counterEvidence", "comparison", "evidenceIds", "questions", "nextCheckMinutes", "notification",
               "insightVersion", "portfolioImpact", "claimEvidence", "observations", "followUpConditions", "caseReviews", "serviceFeedback", "businessResearch"}
    if set(value) - allowed:
        raise ValueError("AI plan contains unsupported fields")
    known = {str(row["id"]) for row in packet.get("facts", []) if row.get("id")}
    evidence = value.get("evidenceIds")
    if not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or item not in known for item in evidence):
        raise ValueError("AI plan must cite current packet facts")
    result = {}
    notification = value.get("notification")
    if not isinstance(notification, dict) or set(notification) != {"send", "reason"} or type(notification["send"]) is not bool:
        raise ValueError("AI must explicitly decide whether a useful notification is warranted")
    if not isinstance(notification["reason"], str) or not 8 <= len(notification["reason"].strip()) <= 500:
        raise ValueError("AI must explain notification novelty or silence")
    result["notification"] = {"send": notification["send"], "reason": notification["reason"].strip()}
    for key in ("summary", "hypothesis", "counterEvidence", "comparison"):
        if not isinstance(value.get(key), str) or not value[key].strip() or len(value[key]) > 2000:
            raise ValueError("AI plan requires bounded " + key)
        result[key] = value[key].strip()
    questions = value.get("questions", [])
    if not isinstance(questions, list) or len(questions) > 2:
        raise ValueError("at most two research questions are allowed")
    result["questions"] = []
    result["researchQuestions"] = []
    result["developmentQuestions"] = []
    result["workQuestions"] = []
    for item in questions:
        if (not isinstance(item, dict) or set(item) not in ({"question", "capability"}, {"question", "capability", "research"})
                or (require_research and "research" not in item)):
            raise ValueError("research questions must name an allowed capability")
        question = item["question"]
        if item["capability"] not in CAPABILITIES or not isinstance(question, str) or not 8 <= len(question.strip()) <= 500:
            raise ValueError("invalid research question")
        work = {"question": question.strip(), "capability": item["capability"]}
        if "research" in item:
            from .research_request import validate_research_request
            request = validate_research_request(item["research"], item["capability"] == "research", packet.get("accountId", ""))
            if request:
                work["researchRequest"] = request
        result["workQuestions"].append(work)
        if item["capability"] == "develop-hypothesis":
            result["developmentQuestions"].append(question.strip())
        else:
            result["questions"].append(question.strip())
            if item["capability"] == "research":
                result["researchQuestions"].append(question.strip())
    if len(result["developmentQuestions"]) > 1:
        raise ValueError("at most one hypothesis development question is allowed")
    result.update(evidenceIds=list(dict.fromkeys(evidence))[:20],
                  nextCheckMinutes=bounded(value.get("nextCheckMinutes"), 180, 60, 1440),
                  authority="research-only", publicationStatus="internal-research")
    if "insightVersion" in value:
        from digital_twin.modules.outcomes.contracts import prepare_observation_conditions, ObservationConditionError
        result.update({key: value.get(key) for key in ("insightVersion", "portfolioImpact", "claimEvidence", "observations")})
        try:
            business = value.get("businessResearch") or {}
            result["followUpConditions"] = [] if value.get("followUpConditions") == [] and (business.get("theses") or business.get("reviews")) else prepare_observation_conditions(value.get("followUpConditions"), packet)
        except (ValueError, KeyError, TypeError) as error:
            result["followUpConditions"] = []
            result["conditionValidation"] = error.diagnostic if isinstance(error, ObservationConditionError) else {
                "stage": "followup-validation", "category": "data-or-reference", "reasonCode": "condition-reference-invalid"}
    from digital_twin.modules.ai_orchestration.domain.brain_management import validate_management
    result.update(validate_management(value, packet, research, require_research=require_research))
    if "businessResearch" in value:
        from .business_research import validate_business
        result["businessResearch"] = validate_business(value["businessResearch"], packet, research)
    return result


def planning_prompt(packet, history, research):
    return """온톨로지 개선 계약:
questions의 capability에는 observe, research 외에 develop-hypothesis를 사용할 수 있습니다. 한 관찰에서 최대 한 개입니다.
확인된 근거의 충돌이나 이전 설명의 반증으로 기존 설명을 개선할 필요가 있을 때, 검증 가능한 가설 개발 질문을 작성하세요.
단순 가격 변화·자료 누락은 관찰이나 원문 조사 대상입니다. 근거 없는 원인이나 거래 행동을 개선 요청에 넣지 마세요.
researchResults 중 kind=ontology-development는 이전 개선 요청과 실험의 진행 기록입니다. 동일한 진행·자료 대기 요청을 반복하지 마세요.
실패·차단 이유는 다음 질문에 반영하되, 실험 진행·검토 통과·관찰 조건 전환을 미래 예측의 적중이나 인과성 입증으로 해석하지 마세요.
개발 요청은 기존 모델·어휘를 이용한 격리 후보 실험으로 이어집니다. 운영 규칙·검증 기준·서비스 코드를 직접 변경할 권한은 없습니다.
이 기능은 알림 발송 여부와 별개이며 계정·종목별 UTC 하루 한 요청으로 병합됩니다.
""" + bounded_planning_prompt(packet, history, research, development=True)


def bounded_planning_prompt(packet, history, research, development=False):
    instructions = """근거 계약 보완 규칙:
참고용(judgementEvidenceUsable=false 또는 valuationDecisionEligible=false) 자료는 counterEvidence에서 한계를 설명할 때만 인용하세요.
과거 가격과 비교하려면 양쪽 facts에 같은 currency와 관측 시점이 보존되어 있어야 합니다. 과거 통화를 현재 값으로 추정하지 마세요.
누락된 과거 가격/통화가 있으면 현재의 검증 가능한 관계만 비교하고, 이전 수치 비교가 제한됨을 명시하세요.
observations는 참인 비교만 포함하고, 가설의 미래 확인 조건은 followUpConditions에 분리하세요.
관심 종목도 검증된 변화가 관찰 이유를 바꾸면 알릴 수 있습니다. 미보유 자체는 보류 사유가 아닙니다.
기존 분석은 검증 거절 여부를 확인하세요. 거절된 문장이나 발송되지 않은 분석을 고객에게 전달한 설명으로 취급하지 마세요.
"""
    from digital_twin.modules.ai_orchestration.domain.insight_prompt import insight_prompt
    return instructions + insight_prompt(packet, history, research, development=development)


def legacy_planning_prompt(packet, history, research):
    from digital_twin.modules.ai_orchestration.domain.insight_prompt import insight_prompt
    return insight_prompt(packet, history, research)
