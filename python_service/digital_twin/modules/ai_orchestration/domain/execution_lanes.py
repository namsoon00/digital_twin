"""Operational isolation of market observations and original-question work."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from digital_twin.modules.reasoning.contracts import content_hash, evidence_change_identity

VERSION = "independent-observation-lanes-v1"
MARKET = "market-observe"
OBSERVATIONS = {"observe", MARKET}


def enabled(settings):
    return settings.get("aiObservationLanePolicy") == VERSION


def market(value):
    return value.get("capability") == MARKET or value.get("executionLane") == MARKET


def identified(packet):
    return packet.get("executionLanePolicy") == VERSION and packet.get("executionLane") in {MARKET, "question-research"}


def annotate(packet, job):
    return {**packet, "executionLanePolicy": VERSION,
            "executionLane": MARKET if market(job) else "question-research"}


def compact_knowledge(context):
    # The owning contract explicitly excludes this duplicate graph projection
    # from contextHash. All original methods, claims and provenance stay exact.
    return {key: deepcopy(value) for key, value in context.items() if key != "ontology"}


def scoped_memories(rows, market_lane):
    result = []
    for row in rows:
        readonly = row.get("capability") == "research" if market_lane else row.get("capability") != "research"
        if row.get("kind") == "brain-case" and readonly:
            assessment = row.get("questionAssessment") or {}
            result.append({"kind": "research-context", "caseId": row["caseId"],
                "question": row.get("question", ""), "status": assessment.get("status", "unresolved"),
                "reviewedAssessment": {key: deepcopy(assessment[key]) for key in
                    ("status", "judgmentImpact", "missingRequirements", "researchReviewInputId", "workKey") if key in assessment}
                    if assessment.get("researchReviewInputId") else {},
                "authority": "historical-context-only", "body": deepcopy(row)})
        else:
            result.append(deepcopy(row))
    return result


def market_memories(rows):
    return scoped_memories(rows, True)


def material_key(packet, research, *, evidence_identity=None):
    from .execution_input import LANE_PROMPT_VERSION
    common = {"policy": VERSION, "lane": packet["executionLane"],
              "authorPolicy": LANE_PROMPT_VERSION,
              "account": packet["accountId"], "symbol": packet["symbol"], "world": packet["worldId"],
              "binding": packet.get("reasoningBinding", {}).get("deploymentId")}
    if packet.get("evidenceReadPolicy"):
        common["readPolicy"] = packet["evidenceReadPolicy"]
    if packet.get("evidenceReadCompletionPolicy"):
        common["readCompletionPolicy"] = packet["evidenceReadCompletionPolicy"]
    if packet.get("evidencePurposePolicy"):
        common["purposePolicy"] = packet["evidencePurposePolicy"]
    if packet.get("companyValuePolicy"):
        common["companyValuePolicy"] = packet["companyValuePolicy"]
    if packet.get("evidenceCompletionPolicy"):
        common["evidenceCompletionPolicy"] = packet["evidenceCompletionPolicy"]
    if packet.get("businessInsightPolicy"):
        common["businessInsightPolicy"] = packet["businessInsightPolicy"]
    if packet.get("companyResearchPolicy"):
        common["companyResearchPolicy"] = packet["companyResearchPolicy"]
    if packet.get("judgmentQualityPolicy"):
        common["judgmentQualityPolicy"] = packet["judgmentQualityPolicy"]
    if packet.get("researchChainPolicy"):
        common["researchChainPolicy"] = packet["researchChainPolicy"]
    if packet.get("externalInfluencePolicy"):
        common["externalInfluencePolicy"] = packet["externalInfluencePolicy"]
    if packet.get("priceInvestigationPolicy"):
        common["priceInvestigationPolicy"] = packet["priceInvestigationPolicy"]
    if packet.get("marketExplanationPolicy"):
        common["marketExplanationPolicy"] = packet["marketExplanationPolicy"]
    if packet.get("notificationContributionPolicy"):
        common["notificationContributionPolicy"] = packet["notificationContributionPolicy"]
        if market(packet):
            common["deliveredContext"] = packet["deliveredNotificationContext"]["fingerprint"]
    if market(packet):
        common["evidence"] = evidence_identity if evidence_identity is not None else evidence_change_identity(packet, [], [])
        common["observationCases"] = sorted([{key: row[key] for key in ("caseId", "capability", "question", "completionCriterion") if key in row}
            for row in research if row.get("kind") == "brain-case"], key=content_hash)
        common["reviewedQuestionReceipts"] = sorted([{"caseId": row["caseId"],
            "workKey": row["body"]["questionAssessment"].get("workKey"),
            "reviewInputId": row["body"]["questionAssessment"]["researchReviewInputId"]}
            for row in research if row.get("kind") == "research-context"
            and (row.get("body", {}).get("questionAssessment") or {}).get("researchReviewInputId")], key=content_hash)
    else:
        # A quote tick, retry clock or self-authored prose cannot renew a
        # documentary question. Only its original contract/source inventory can.
        common["questions"] = sorted(row["questionWorkKey"] for row in research if row.get("questionWorkKey"))
        common["pending"] = packet.get("questionWorkPending", [])
    return content_hash(common)


def wait_until(packet, now=None):
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(minutes=30 if market(packet) else 360)).isoformat().replace("+00:00", "Z")


def instructions(packet):
    if not identified(packet):
        return ""
    if market(packet):
        return """시장 관찰 전용 실행 계약:
현재 시세·보유 상황·새 근거가 마지막 실제 발송의 설명을 바꾸는지 판단하세요.
research-context와 연구 기억은 과거 맥락입니다. 필요하면 read_memory로 읽고 현재 facts로 확인하세요.
미완료 연구 질문이 있어도 확인된 현재 사실의 범위에서 시장 관찰을 수행하세요. 해당 설명에 필수인 자료가 없으면 그 설명을 보류하고 한계를 명시하세요.
질문 완료를 기다린다는 이유만으로 시장 관찰 전체를 잠정 연구로 바꾸지 마세요.
caseReviews는 현재 입력의 observe/develop-hypothesis 과제만 검토합니다. 원문 연구 질문 답변은 별도 실행이 맡습니다. questionResolutions는 develop-hypothesis만 연결하고 knowledgeResearch.claims는 빈 배열입니다. 새로운 조사 필요는 questions에 제한적으로 제안하세요.
알림은 새로운 의미·보유/관심 상황의 영향·근거·반대 근거가 있을 때만 제안합니다. 알림을 만들기 위해 결론을 과장하지 마세요.
"""
    return """원래 질문 연구 전용 실행 계약:
이번에는 선택된 한 질문만 평가합니다. 원래 기간·완료 기준과 부족 항목을 보존하세요.
부분 답변과 연구 검토는 고객 알림의 승인이 아닙니다. 시장 관찰은 별도 작업에서 계속됩니다.
"""
