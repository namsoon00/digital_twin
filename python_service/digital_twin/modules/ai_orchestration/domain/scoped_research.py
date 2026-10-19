"""Extra guards for the versioned partial-research route; legacy inputs unchanged."""
from .scoped_retrieval import SCOPE_VERSION, AUTHOR_SCOPE_POLICY
from .validation_diagnostic import PlanValidationError

INSTRUCTIONS = """진행 범위 검증 계약:
current.researchScope는 조회 단계의 질문별 근거 선택과 미확인 조건입니다. ready는 질문 답변의 승인이나 가설 검증이 아닙니다.
waiting 질문은 answered/dismissed로 닫지 마세요. 필요한 근거가 있는 질문만 평가하고 나머지 질문의 기간·완료 기준은 유지하세요.
mode=research-only이면 notification.send=false입니다. 부분 근거에 기반한 잠정 knowledgeResearch와 반론·부족 항목을 내부 연구로 작성하세요.
증거 없는 설명을 만들지 말고 조사 질문으로 남기세요. 연구 가설에는 어떤 근거와 가정으로 연결했는지, 무엇이 반증하는지를 보존하세요.
이전 researchKnowledge.progress는 과거 조회 진행 기록입니다. 그때 읽었거나 미확인한 항목이지 현재 시장 사실이나 답변 완료의 증거가 아닙니다.
"""


def research_only(packet):
    """Only a frozen, supported read-stage scope can select internal research."""
    scope = packet.get("researchScope") or {}
    return scope.get("version") == SCOPE_VERSION and (scope.get("mode") == "research-only" or scope.get("assessmentOnly") is True)


def research_disposition(result):
    """Research review is independent from customer narrative admission."""
    if not research_only(result.get("input") or {}):
        return ""
    from .research_review import needed
    if not needed(result):
        return "research-only"
    return ("research-reviewed" if (result.get("researchQuality") or {}).get("status") == "accepted"
            else "research-review-required")


def instructions(packet):
    scope = packet.get("researchScope", {})
    if scope.get("version") != SCOPE_VERSION:
        return ""
    extra = ("이번 입력은 잠정 지식 연구 전용입니다. caseReviews와 questionResolutions는 빈 배열입니다. "
             "researchDeferredQuestions는 다음 질문 평가로 넘긴 원래 질문과 완료 기준입니다. 이를 수정·종료하거나 다른 문장으로 새로 만들지 마세요. "
             "선택한 근거의 knowledgeResearch 및 관련 사업 설명만 연구하고, 독립 검토도 이 변경만 평가하세요.\n"
             if scope.get("assessmentPolicy") == AUTHOR_SCOPE_POLICY else "")
    from .question_evidence import VERSION as evidence_policy
    if packet.get("questionEvidencePolicy") == evidence_policy and scope.get("mode") == "question-assessment":
        extra += "ready 질문은 caseReviews와 questionResolutions에서 빠짐없이 검토하세요. 근거의 기간·지표·완료 기준을 다시 확인하고 부족하면 구체적인 사유로 wait/research/blocked를 선택하세요.\n"
    from .question_assessment import enabled, INSTRUCTIONS as assessment_instructions
    if enabled(packet):
        extra += assessment_instructions
    return INSTRUCTIONS + extra


def validate_plan(result, packet):
    scope = packet.get("researchScope", {})
    if scope.get("version") != SCOPE_VERSION:
        return
    ready = {row["caseId"] for row in scope["questions"] if row["status"] == "ready"}
    scoped = {row["caseId"] for row in scope["questions"]}
    from .question_evidence import VERSION as evidence_policy
    if packet.get("questionEvidencePolicy") == evidence_policy and scope["mode"] == "question-assessment":
        reviewed = {row["caseId"] for row in result.get("caseReviews", [])}
        if ready - reviewed:
            raise PlanValidationError("ready-question-review-missing", "caseReviews", "ready 질문을 원래 완료 기준에 따라 각각 검토하세요.")
    if scope.get("assessmentPolicy") == AUTHOR_SCOPE_POLICY and (result.get("caseReviews") or result.get("questionResolutions")):
        raise PlanValidationError("provisional-knowledge-scope", "caseReviews", "잠정 지식 연구에서는 기존 질문을 갱신하지 말고 두 과제 평가 배열을 비우세요.")
    if scope["mode"] == "research-only" and result["notification"]["send"]:
        raise PlanValidationError("partial-research-notification", "notification.send", "잠정 연구는 내부 저장만 허용하므로 false로 작성하세요.")
    if any(row["action"] in {"answered", "dismissed"} and row["caseId"] in scoped - ready for row in result.get("caseReviews", [])):
        raise PlanValidationError("partial-research-question", "caseReviews", "대기 중인 질문의 답변·종료를 선언하지 말고 원래 기준과 부족 근거를 유지하세요.")
    if scope["mode"] == "research-only" and any(row["action"] in {"answered", "dismissed"} for row in result.get("caseReviews", [])):
        raise PlanValidationError("partial-research-question", "caseReviews", "잠정 연구에서 질문 답변·종료를 선언할 수 없습니다.")
    from .question_assessment import enabled
    if enabled(packet) and scope.get("assessmentOnly"):
        required = {row["caseId"] for row in scope["questions"] if row["selectedFactIds"]}
        if required - {row["caseId"] for row in result.get("caseReviews", [])}:
            raise PlanValidationError("partial-question-review-missing", "caseReviews", "선택한 근거가 있는 원래 질문의 부분 답변과 부족분을 각각 검토하세요.")
        if result["notification"]["send"]:
            raise PlanValidationError("partial-research-notification", "notification.send", "질문 부분 평가는 내부 저장하며 고객 알림은 false입니다.")
