"""Independent admission of a question assessment and separate knowledge edits."""
from copy import deepcopy
from .insight_schema import obj, choice
from .question_purpose import enabled

VERSION = "research-assessment-review-v8-separate-knowledge"
INSTRUCTIONS = """독립 검토를 assessment와 knowledge 두 부분으로 반환하세요. 각 부분은 {accepted,reason}입니다.
assessment는 원래 질문의 답·부족 항목·judgmentImpact, businessResearch, 새 질문의 목적·자료 작업 및 questionContinuations를 함께 검토합니다. 이 안에서 서로 의존하는 변경은 묶어서 승인하거나 거부하세요.
knowledge는 knowledgeResearch만 검토합니다. 관련 없는 지식 메모리의 오류만으로 타당한 질문 답변을 거부하지 마세요. 반대로 질문 승인이 잘못된 지식 변경을 승인하지도 않습니다. 각 부분에 이유를 쓰고, 해당 제안이 없으면 변경 없음으로 승인하세요.
knowledge가 이번 businessResearch.theses의 인덱스를 참조하는데 assessment를 거부했다면 knowledge도 거부하세요. 원래 미확인 조건·기간·반대 근거를 누락한 변경은 해당 부분에서 거부하세요. 부분 답을 질문 완료로 올리거나 source answer만으로 사실을 인정하지 마세요.
JSON만 반환하세요: {version,assessment:{accepted,reason},knowledge:{accepted,reason}}.
"""


def schema():
    part = obj({"accepted": {"type": "boolean"}, "reason": {"type": "string", "minLength": 8, "maxLength": 600}})
    return obj({"version": choice([VERSION]), "assessment": deepcopy(part), "knowledge": deepcopy(part)})


def admissions(result, review):
    if (not isinstance(review, dict) or set(review) != {"version", "assessment", "knowledge"}
            or review.get("version") != VERSION or not enabled(result.get("input", {}))):
        return {"assessment": False, "knowledge": False}
    for key in ("assessment", "knowledge"):
        row = review[key]
        if (not isinstance(row, dict) or set(row) != {"accepted", "reason"} or type(row["accepted"]) is not bool
                or not isinstance(row["reason"], str) or not 8 <= len(row["reason"].strip()) <= 600):
            return {"assessment": False, "knowledge": False}
    accepted = {key: review[key]["accepted"] for key in ("assessment", "knowledge")}
    if any(row.get("disposition") == "experiment" for row in result.get("questionResolutions", [])):
        accepted["assessment"] = False
    if not accepted["assessment"] and any(row.get("businessThesisIndex", -1) >= 0
                                           for row in (result.get("knowledgeResearch") or {}).get("claims", [])):
        accepted["knowledge"] = False
    return accepted


def approved(result, section="assessment"):
    quality = result.get("researchQuality") or {}
    if quality.get("version") == VERSION:
        return (quality.get("status") in {"accepted", "partially-accepted"}
                and quality.get("admissions", {}).get(section) is True)
    return quality.get("status") == "accepted"


def reason(quality, section):
    review = quality.get("review") or {}
    return review.get(section, {}).get("reason", "") if quality.get("version") == VERSION else review.get("reason", "")
