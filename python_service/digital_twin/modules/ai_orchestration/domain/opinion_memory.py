"""Reviewed opinion read models; source support is separate from opinion change."""
from copy import deepcopy

from digital_twin.modules.reasoning.contracts import content_hash, evidence_change_identity

VERSION = "reviewed-opinion-memory-v1"
RELATIONS = ("supports", "contradicts", "mixed", "insufficient")
CHANGES = ("new", "unchanged", "strengthened", "weakened", "withdrawn")
INSTRUCTIONS = """승인된 의견과 변화 계약 (이전 judgmentImpact 설명보다 우선):
questionResolutions.judgmentImpact에 evidenceRelation과 opinionChange를 별도로 기록하세요.
evidenceRelation=supports/contradicts/mixed/insufficient는 현재 답변 근거와 이번 설명의 관계입니다.
opinionChange=new/unchanged/strengthened/weakened/withdrawn는 승인된 이전 의견 대비 변화입니다. 같은 자료가 설명을 계속 지지해도 새 판단이 없으면 unchanged입니다.
호환 effect는 unchanged이면 unchanged, strengthened이면 supports, weakened/withdrawn이면 weakens입니다. new는 이전 승인 의견이 없을 때만 사용하고, 이때 effect는 evidenceRelation=supports이면 supports, contradicts이면 weakens, mixed/insufficient이면 insufficient입니다.
reason에는 확인한 답, 그 답이 뒷받침하거나 반박하는 범위, 이전과 달라진 점 및 남은 한계를 적으세요. 새 근거나 질문 조건 변화 없이 강화·약화·철회를 만들지 마세요.
approvedOpinion은 검토를 통과한 과거 답변·의견입니다. rejected candidate나 완료 상태만 있는 실행은 채택된 의견이 아닙니다.
search_memory로 찾은 과거 의견도 당시 자료와 적용 기간의 기억입니다. 현재 facts를 대신하지 않으며 최신 사실로 인용할 수 없습니다.
"""


def enabled(packet):
    return packet.get("opinionMemoryPolicy") == VERSION


def approved_result(result):
    if not result.get("summary"):
        return False
    if (result.get("quality", {}).get("status") in {"accepted", "observation-only"}
            and not result.get("quality", {}).get("errors")):
        return True
    from .research_review_parts import approved
    return approved(result) and bool(result.get("researchQuality", {}).get("reviewInputId"))


def current_question(case):
    """Read the accepted assessment, never the latest attempted resolution."""
    row = case.get("questionAssessment") or {}
    if (row.get("version") != "original-question-assessment-v1" or row.get("status") not in {"answered", "partial", "dismissed"}
            or not row.get("researchReviewInputId") or not row.get("executionInputId")
            or row.get("questionContract") != case.get("questionContract")):
        return {"status": "unavailable", "reason": "no-reviewed-answer-for-current-contract"}
    return {"version": VERSION, "status": "reviewed", "caseId": case["caseId"],
        "question": case.get("question", ""), "questionContract": deepcopy(case["questionContract"]),
        **{k: deepcopy(row[k]) for k in ("answer", "answerParts", "missingRequirements", "judgmentImpact",
            "judgment", "taskId", "executionInputId", "researchReviewInputId", "at", "evidenceIds", "sources", "workKey", "basis") if k in row},
        "answerStatus": row.get("status", "partial"), "authority": "historical-reviewed-answer-only"}


def impact_schema():
    from .insight_schema import obj, choice
    return obj({"effect": choice(["supports", "weakens", "unchanged", "insufficient"]),
        "reason": {"type": "string", "minLength": 8, "maxLength": 500},
        "evidenceRelation": choice(RELATIONS), "opinionChange": choice(CHANGES)})


def validate_impact(value, case, packet):
    if set(value) != set(impact_schema()["properties"]):
        raise ValueError("opinion impact requires separate evidence relation and change")
    if value["evidenceRelation"] not in RELATIONS or value["opinionChange"] not in CHANGES:
        raise ValueError("invalid opinion impact")
    expected = {"unchanged": "unchanged", "strengthened": "supports", "weakened": "weakens", "withdrawn": "weakens"}
    if value["opinionChange"] in expected and value["effect"] != expected[value["opinionChange"]]:
        raise ValueError("opinion change and compatibility effect disagree")
    if value["opinionChange"] == "new" and value["effect"] != {
            "supports": "supports", "contradicts": "weakens", "mixed": "insufficient", "insufficient": "insufficient"}[value["evidenceRelation"]]:
        raise ValueError("new opinion effect must match its evidence relation")
    previous = case.get("approvedOpinion") or current_question(case)
    if previous.get("status") == "reviewed":
        if value["opinionChange"] == "new":
            raise ValueError("reviewed prior opinion cannot be described as new")
        if (case.get("questionWorkUnchanged") and value["opinionChange"] != "unchanged"):
            raise ValueError("unchanged question evidence cannot strengthen or weaken its opinion")
    elif value["opinionChange"] != "new":
        raise ValueError("opinion comparison requires a reviewed baseline")


def basis(packet, ids):
    return [{"factId": f["id"], "materialHash": evidence_change_identity({"facts": [f]}, []),
             "contentHash": content_hash(f)} for f in packet.get("facts", []) if f["id"] in ids]


def contribution(result):
    """Bounded, reviewed answer contribution for display; no delivery authority."""
    from .research_review_parts import approved
    packet = result.get("input", {})
    if not enabled(packet):
        return []
    reviews = {r["caseId"]: r for r in result.get("caseReviews", [])}
    values = ([{"caseId": r["caseId"], "kind": "current-answer", "question": r.get("sourceQuestion", {}).get("question", ""),
               "answer": reviews.get(r["caseId"], {}).get("reason", ""),
               "impact": deepcopy(r["judgmentImpact"]), "evidenceIds": list(r["evidenceIds"]),
               "missingRequirements": list(r.get("missingRequirements", []))}
              for r in result.get("questionResolutions", []) if r.get("judgmentImpact")]
              if approved(result) and result.get("researchQuality", {}).get("reviewInputId") else [])
    facts = {f["id"]: f for f in packet.get("facts", [])}
    cited = {ref["factId"] for section in ("summary", "hypothesis", "portfolioImpact")
             for ref in result.get("claimEvidence", {}).get(section, []) if ref.get("period") == "current"}
    for memory in packet.get("reviewedQuestionContext", []):
        matched = set()
        for ref in memory.get("sources", []):
            fact = facts.get(ref["factId"], {})
            current_hash = (fact.get("historicalDocumentReference") or {}).get("documentHash")
            material = next((b["materialHash"] for b in memory.get("basis", []) if b["factId"] == ref["factId"]), "")
            if ref["factId"] in cited and ((ref.get("documentHash") and ref["documentHash"] == current_hash)
                    or material and material == evidence_change_identity({"facts": [fact]}, [])):
                matched.add(ref["factId"])
        # A prior answer can mention several sources. Display only confirmed
        # parts whose whole basis is also cited in this observation.
        confirmed = [part["text"] for part in memory.get("confirmed", [])
                     if part.get("evidenceIds") and set(part["evidenceIds"]) <= matched]
        if confirmed and memory.get("researchReviewInputId"):
            values.append({"caseId": memory["caseId"], "kind": "prior-answer-context", "question": memory["question"],
                "answer": " ".join(confirmed[:2]), "at": memory.get("at", ""), "impact": deepcopy(memory.get("judgmentImpact", {})),
                "evidenceIds": sorted(matched), "missingRequirements": list(memory.get("missingRequirements", []))})
    return values[:3]


def question_context(research):
    """Small approved research references frozen with a market observation."""
    result = []
    for row in research:
        if row.get("kind") != "research-context":
            continue
        current = current_question(row.get("body", {}))
        if current.get("status") != "reviewed":
            continue
        result.append({k: deepcopy(current[k]) for k in
            ("caseId", "question", "judgmentImpact", "researchReviewInputId", "at", "sources", "basis", "missingRequirements") if k in current})
        result[-1]["confirmed"] = deepcopy(current.get("answerParts", {}).get("confirmed", []))
    return result[:4]
