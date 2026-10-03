"""Evidence-bound work memory and service feedback, without investment authority."""
import copy
import re
from datetime import datetime, timedelta

from digital_twin.modules.ai_orchestration.domain.planning import identity, bounded


ACTIVE_CASE_STATES = ("open", "waiting", "review-needed", "blocked")
CASE_ACTIONS = ("wait", "research", "answered", "blocked", "dismissed")
FEEDBACK_CATEGORIES = ("analysis", "data", "experience", "operations")
GOALS = (
    "중요한 반증과 확인 시점을 놓치지 않습니다.",
    "미해결 질문을 근거로 재검토하고, 조사 실행과 질문 해결을 구분합니다.",
    "확인된 서비스 문제에 개선안과 확인 기준을 제안합니다.",
)


def case_identity(account, symbol, world, capability, question):
    normalized = re.sub(r"\s+", " ", question.strip()).casefold()
    return identity("brain-case-v1", account, symbol, world, capability, normalized)


def later(now, minutes):
    return (datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def case_memories(records):
    return {item["caseId"]: item for item in records if item.get("kind") == "brain-case"}


def due_memory(records):
    return any(item.get("reviewDue") for item in case_memories(records).values())


def required_case_memory(record):
    return record.get("kind") == "brain-case" and record.get("reviewDue") is True


def management_prompt(packet, history, research):
    from digital_twin.modules.ai_orchestration.domain.planning import planning_prompt
    return """지속 과제와 서비스 개선 계약:
목표는 중요한 반증 확인, 미해결 질문 해결, 근거 있는 서비스 개선입니다. 호출·알림·제안 개수는 성과가 아닙니다.
researchResults의 kind=brain-case는 같은 계정·종목의 지속 과제입니다. 최근 관찰보다 오래된 원래 질문과 근거를 포함할 수 있습니다.
이 기록은 당시 해석과 작업 이력이며 현재 시장 사실이 아닙니다. 현재 설명의 근거는 current.facts에서 다시 확인하세요.
reviewDue=true인 과제는 caseReviews에서 빠짐없이 검토하세요. 기존 과제를 다른 문장으로 새로 만들지 마세요.
action=wait는 다음 관찰 대기, research는 같은 질문의 추가 원문 조사, answered는 현재 근거로 질문에 답함,
blocked는 지원 자료/기능 부족, dismissed는 더 이상 다룰 필요가 없음을 뜻합니다. reason에 답이나 판단 이유를 적으세요.
조사 완료·실험 준비만으로 answered를 선택하지 마세요. 가격 관측을 예측 성공이나 인과성 입증으로 취급하지 마세요.
answered에는 질문에 답하는 현재의 사용 가능한 evidenceIds가 필요합니다. blocked/dismissed도 사유를 구체적으로 기록하세요.
새 질문은 기존 questions에 넣으세요. 기존 과제에 연결된 조사는 변화한 근거, 재시도 간격과 횟수 제한을 따릅니다.
serviceFeedback은 최대 한 개의 개선 제안입니다. category는 analysis/data/experience/operations,
problem은 입력에서 확인한 한계, proposal은 구체적 개선안, verification은 개선 후 확인할 관측 가능한 기준입니다.
화면이나 운영 지표를 보지 않았다면 실제 화면 오류·속도·사용자 불만을 관측했다고 주장하지 마세요.
제안의 근거 evidenceIds는 현재 입력의 사실을 인용하세요. 개선 제안은 검증 전 의견이고 코드 변경·규칙 채택 권한이 아닙니다.
기존 kind=service-feedback의 같은 문제를 반복 제안하지 마세요. 자료 부족 자체를 사용자 알림으로 반복 발송하지 마세요.
응답에는 caseReviews와 serviceFeedback 배열을 반드시 포함합니다. 필요한 작업이나 제안이 없으면 빈 배열입니다.
caseReviews 항목: {caseId,action,reason,evidenceIds,nextCheckMinutes}; nextCheckMinutes는 60~1440입니다.
serviceFeedback 항목: {category,problem,proposal,verification,evidenceIds}.
""" + planning_prompt(packet, history, research)


def management_schema(packet, research):
    from digital_twin.modules.ai_orchestration.domain.insight_schema import planning_schema, obj, choice, array
    result = planning_schema(packet)
    string = {"type": "string"}
    evidence = array(choice([row["id"] for row in packet.get("facts", [])] or [""]))
    result["properties"].update(
        caseReviews=array(obj({"caseId": choice(list(case_memories(research)) or [""]),
            "action": choice(CASE_ACTIONS), "reason": string, "evidenceIds": evidence,
            "nextCheckMinutes": {"type": "integer"}})),
        serviceFeedback=array(obj({"category": choice(FEEDBACK_CATEGORIES), "problem": string,
            "proposal": string, "verification": string, "evidenceIds": evidence})))
    result["required"] += ["caseReviews", "serviceFeedback"]
    return result


def validate_management(value, packet, research, require_research=False):
    known = {row["id"]: row for row in packet.get("facts", [])}
    memories = case_memories(research)

    def text(item, key):
        result = item.get(key)
        if not isinstance(result, str) or not 8 <= len(result.strip()) <= 600:
            raise ValueError("brain management requires bounded " + key)
        return result.strip()

    def evidence(item, usable=False):
        ids = item.get("evidenceIds")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 8 or any(not isinstance(key, str) or key not in known for key in ids):
            raise ValueError("brain management requires captured evidence")
        if usable and any(known[key].get("judgementEvidenceUsable") is False or known[key].get("valuationDecisionEligible") is False for key in ids):
            raise ValueError("reference-only facts cannot answer a case")
        return list(dict.fromkeys(ids))

    reviews, feedback = value.get("caseReviews", []), value.get("serviceFeedback", [])
    if not isinstance(reviews, list) or len(reviews) > 5 or not isinstance(feedback, list) or len(feedback) > 1:
        raise ValueError("brain management exceeds its bounded work budget")
    seen, accepted = set(), []
    for item in reviews:
        fields = {"caseId", "action", "reason", "evidenceIds", "nextCheckMinutes"}
        if (not isinstance(item, dict) or set(item) not in (fields, fields | {"research"})
                or (require_research and "research" not in item)):
            raise ValueError("invalid case review contract")
        key, action = item["caseId"], item["action"]
        if key not in memories or key in seen or action not in CASE_ACTIONS:
            raise ValueError("case review must own one captured scoped case")
        if any(memories[key].get(field) != packet.get(field) for field in ("accountId", "symbol", "worldId")):
            raise ValueError("brain memory scope mismatch")
        if action == "research" and memories[key]["capability"] != "research":
            raise ValueError("only documentary questions may repeat source research")
        seen.add(key)
        accepted.append({"caseId": key, "action": action, "reason": text(item, "reason"),
            "evidenceIds": evidence(item, usable=action == "answered"),
            "nextCheckMinutes": bounded(item["nextCheckMinutes"], 180, 60, 1440),
            "expectedRevision": memories[key]["revision"]})
        if "research" in item:
            from .research_request import validate_research_request
            validate_research_request(item["research"], action == "research", packet.get("accountId", ""))
            accepted[-1]["research"] = copy.deepcopy(item["research"])
    if {key for key, item in memories.items() if item.get("reviewDue")} - seen:
        raise ValueError("due brain cases require an explicit review")
    proposals = []
    for item in feedback:
        if not isinstance(item, dict) or set(item) != {"category", "problem", "proposal", "verification", "evidenceIds"} or item["category"] not in FEEDBACK_CATEGORIES:
            raise ValueError("invalid service feedback contract")
        proposals.append({"category": item["category"], **{key: text(item, key) for key in ("problem", "proposal", "verification")},
                          "evidenceIds": evidence(item), "qualification": "unverified-proposal"})
    return {"caseReviews": accepted, "serviceFeedback": proposals}


def source_memory(job, result):
    packet = result["input"]
    selected = set(result.get("evidenceIds", []))
    for item in result.get("caseReviews", []) + result.get("serviceFeedback", []):
        selected.update(item.get("evidenceIds", []))
    return {"taskId": job["taskId"], "executionInputId": result["executionInputId"],
        "capturedAt": packet["capturedAt"], "sourceSnapshots": packet.get("sourceSnapshots", {}),
        "summary": result["summary"], "hypothesis": result["hypothesis"],
        "counterEvidence": result["counterEvidence"], "quality": result.get("quality", {}).get("status"),
        "evidence": [copy.deepcopy(row) for row in packet["facts"] if row["id"] in selected],
        "authority": "historical-observation-only"}


def new_case(job, result, question, capability, now):
    request = next((item.get("researchRequest", {}) for item in result.get("workQuestions", [])
                    if item["question"] == question and item["capability"] == capability), {})
    return {"caseId": case_identity(job["accountId"], job["symbol"], job["worldId"], capability, question),
        "accountId": job["accountId"], "symbol": job["symbol"], "worldId": job["worldId"],
        "question": question, "capability": capability, "status": "open", "revision": 0,
        "createdAt": now, "updatedAt": now, "nextCheckAt": later(now, result["nextCheckMinutes"]),
        "completionCriterion": "검증 가능한 근거로 원래 질문에 답하고 남은 불확실성을 설명합니다.",
        "reason": "새 관찰에서 확인할 질문을 등록했습니다.", "researchAttempts": 0,
        "origin": source_memory(job, result), "researchRequest": copy.deepcopy(request), "authority": "research-only"}


def review_transition(case, review, source, now):
    value = copy.deepcopy(case)
    if review["action"] == "research" and review.get("research"):
        from .research_request import validate_research_request
        value["researchRequest"] = validate_research_request(review["research"], True, case["accountId"])
    if value["revision"] != review["expectedRevision"] or value["status"] not in ACTIVE_CASE_STATES:
        raise ValueError("brain case changed after input capture")
    action = review["action"]
    value.update(status={"wait": "waiting", "research": "open"}.get(action, action),
        reason=review["reason"], nextCheckAt=later(now, review["nextCheckMinutes"]),
        lastAssessment={"taskId": source["taskId"], "executionInputId": source["executionInputId"],
            "evidenceIds": review["evidenceIds"], "reason": review["reason"], "qualification": "ai-assessment"})
    return value
