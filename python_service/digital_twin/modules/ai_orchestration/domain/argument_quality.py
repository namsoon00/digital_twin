"""A bounded writing check and an audit of feedback use, never market evidence."""
from .insight_contract import narrative_digest
from digital_twin.modules.reasoning.contracts import content_hash

VERSION = "grounded-economic-argument-v1"
FIELD = "argumentQualityPolicy"
VERSIONS = {
    "author": "independent-observation-v39-grounded-argument",
    "repair": "independent-observation-repair-v35-grounded-argument",
    "review": "independent-observation-review-v28-grounded-argument",
}
INSTRUCTIONS = """작성 전 근거와 설명을 대조한다. 추가 응답 항목이나 별도 호출은 만들지 않는다.
한 문장에 여러 사실을 쓰면 claimEvidence의 해당 문단에 각 사실의 정확한 필드를 인용한다. 영업현금흐름을 인용해 영업이익 증가까지 주장하거나, 연결 범위 필드로 투자지출 구성·정정 이력의 미확인을 입증하지 않는다. 읽지 않은 원문·없는 필드는 확인 한계로 쓰며, 자료가 없다는 확정 사실로 바꾸지 않는다.
investmentMeaning.explanations는 같은 결과·보고기간·범위를 설명하는 두 대안이어야 한다. 현금 증가분과 투자지출의 회계상 분해, 반복 가능한 영업 개선의 가능성은 동시에 성립할 수 있으므로 그 자체로 경쟁 설명이 아니다. 수요·마진·회수 시점·일회성 요인 중 실제 근거가 가리키는 대안을 비교하고, 모르는 대안은 unresolved로 둔다. 판별할 수 없으면 insufficient이며 알림을 채우기 위해 원인이나 경쟁 관계를 만들지 않는다.
nextCheck.supports와 weakens는 같은 관측에서 함께 성립하지 않도록 결과·비교 기준·상쇄 범위를 명시한다. 일부 상쇄와 개선분 전체 소멸을 같은 약화 조건으로 쓰지 않는다. 근거 없는 숫자 경계는 추가하지 않는다.
researchResults의 review-feedback은 과거 작성 실수다. 지적된 문단의 인용과 설명을 현재 원천으로 점검한다. 지적 문장·과거 초안은 사실이나 현재 반증이 아니며, 같은 자료의 재확인을 의견 강화로 쓰지 않는다. 미해결이면 관련 주장을 줄이거나 보류한다.
"""
REVIEW_INSTRUCTIONS = """현재 초안만 독립 검토한다. 한 문단의 모든 사실이 정확한 인용 필드로 뒷받침되는지 대조한다.
경제적 대안은 같은 결과를 설명하는지, 둘이 함께 성립하는 회계 분해와 사업 개선을 억지로 경쟁시켰는지 확인한다. 지지·약화 조건이 같은 관측에서 동시에 성립하면 discriminating=false다. 한계의 정직한 인정은 보존하되 분석 충분성이나 발송 승인으로 대신하지 않는다.
"""


def enabled(packet):
    return packet.get(FIELD) == VERSION


def feedback_use(result, envelope, original_research):
    """Record input inclusion and observed checks, never infer semantic resolution."""
    from . import rejection_feedback as feedback
    from .insight_quality import local_quality, accept_review
    packet = envelope["current"]
    scope = tuple(packet.get(k) for k in ("accountId", "symbol", "worldId"))
    def eligible(row):
        return (row.get("kind") == feedback.KIND and row.get("version") == feedback.VERSION
                and row.get("authority") == "rejected-draft-feedback-only"
                and tuple(row.get(k) for k in ("accountId", "symbol", "worldId")) == scope)
    original = [row for row in original_research if eligible(row)][:feedback.MAX_ROWS]
    if not original:
        return None
    if result.get("input") != packet:
        raise ValueError("feedback audit requires the final author's exact source packet")
    def fingerprint(row):
        return content_hash({key: row[key] for key in feedback.FIELDS if key in row})
    included = {fingerprint(row) for row in envelope["researchResults"] if eligible(row)}
    quality = result.get("quality", {})
    review = quality.get("review") or {}
    verified = False
    if quality.get("reviewInputId") and quality.get("draftHash") == narrative_digest(result):
        checked = accept_review(result, review, quality["reviewInputId"])
        verified = checked.get("status") == quality.get("status") and checked.get("errors") == quality.get("errors")
    local = local_quality(result)
    rows = []
    for memory in original:
        present = fingerprint(memory) in included
        issues = []
        for issue in memory["issues"][:4]:
            section = issue["section"]
            state = "not-reviewed"
            if not present:
                state = "not-in-final-input"
            elif memory["stage"] == "local-validation":
                if issue["reason"] in local.get("errors", []):
                    state = "repeated"
                elif local.get("status") != "rejected":
                    state = "local-check-passed"
            elif verified:
                fields = {"investmentMeaning": ("supported", "discriminating", "expectationsSupported", "followUpLinked"),
                          "analystAssessment": ("supported", "substantive")}.get(section, ("supported",))
                check = review.get("sections", {}).get(section, review.get(section, {}))
                if isinstance(check, dict) and all(type(check.get(k)) is bool for k in fields):
                    state = "check-passed" if all(check[k] for k in fields) else "repeated"
            issues.append({"section": section, "issueHash": content_hash(issue), "checkStatus": state})
        rows.append({"taskId": memory["taskId"], "draftHash": memory["draftHash"],
                     "includedInFinalInput": present, "issues": issues})
    return {"version": VERSION, "authority": "execution-audit-only", "authorInputId": result["executionInputId"],
            "authorInputHash": content_hash(envelope), "authorPromptVersion": envelope["promptVersion"],
            "draftHash": narrative_digest(result), "reviewInputId": quality.get("reviewInputId", "") if verified else "",
            "finalQualityStatus": quality.get("status", "unknown"), "feedback": rows,
            "qualification": "check-results-only-not-semantic-resolution-or-investment-performance"}
