"""AI proposes bounded research work; it cannot grant itself execution authority."""
import hashlib
import json
from datetime import datetime, timezone
from digital_twin.modules.reasoning.contracts import evidence_change_identity


CAPABILITIES = {
    "observe": "현재 근거와 이전 분석을 비교하고 다음 조사와 확인 시점을 정합니다.",
    "research": "검증 가능한 출처를 수집하고 기존 근거 검증·그래프 반영 절차로 보냅니다.",
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


def validate_plan(value, packet):
    if not isinstance(value, dict):
        raise ValueError("AI plan must be an object")
    # No model-authored account, symbol, command, source URL or action is executable.
    allowed = {"summary", "hypothesis", "counterEvidence", "comparison", "evidenceIds", "questions", "nextCheckMinutes", "notification",
               "insightVersion", "portfolioImpact", "claimEvidence", "observations", "followUpConditions"}
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
    for item in questions:
        if not isinstance(item, dict) or set(item) != {"question", "capability"}:
            raise ValueError("research questions must name an allowed capability")
        question = item["question"]
        if item["capability"] not in CAPABILITIES or not isinstance(question, str) or not 8 <= len(question.strip()) <= 500:
            raise ValueError("invalid research question")
        result["questions"].append(question.strip())
        if item["capability"] == "research":
            result["researchQuestions"].append(question.strip())
    result.update(evidenceIds=list(dict.fromkeys(evidence))[:20],
                  nextCheckMinutes=bounded(value.get("nextCheckMinutes"), 180, 60, 1440),
                  authority="research-only", publicationStatus="internal-research")
    if "insightVersion" in value:
        from digital_twin.modules.outcomes.contracts import prepare_observation_conditions
        result.update({key: value.get(key) for key in ("insightVersion", "portfolioImpact", "claimEvidence", "observations")})
        try:
            result["followUpConditions"] = prepare_observation_conditions(value.get("followUpConditions"), packet)
        except (ValueError, KeyError, TypeError):
            result["followUpConditions"] = []
    return result


def planning_prompt(packet, history, research):
    instructions = """근거 계약 보완 규칙:
참고용(judgementEvidenceUsable=false 또는 valuationDecisionEligible=false) 자료는 counterEvidence에서 한계를 설명할 때만 인용하세요.
과거 가격과 비교하려면 양쪽 facts에 같은 currency와 관측 시점이 보존되어 있어야 합니다. 과거 통화를 현재 값으로 추정하지 마세요.
누락된 과거 가격/통화가 있으면 현재의 검증 가능한 관계만 비교하고, 이전 수치 비교가 제한됨을 명시하세요.
observations는 참인 비교만 포함하고, 가설의 미래 확인 조건은 followUpConditions에 분리하세요.
관심 종목도 검증된 변화가 관찰 이유를 바꾸면 알릴 수 있습니다. 미보유 자체는 보류 사유가 아닙니다.
기존 분석은 검증 거절 여부를 확인하세요. 거절된 문장이나 발송되지 않은 분석을 고객에게 전달한 설명으로 취급하지 마세요.
"""
    return instructions + legacy_planning_prompt(packet, history, research)


def legacy_planning_prompt(packet, history, research):
    from digital_twin.modules.ai_orchestration.domain.insight_prompt import insight_prompt
    return insight_prompt(packet, history, research)
