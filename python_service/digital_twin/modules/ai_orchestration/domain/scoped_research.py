"""Extra guards for the versioned partial-research route; legacy inputs unchanged."""
from .scoped_retrieval import SCOPE_VERSION
from .validation_diagnostic import PlanValidationError

INSTRUCTIONS = """진행 범위 검증 계약:
current.researchScope는 조회 단계의 질문별 근거 선택과 미확인 조건입니다. ready는 질문 답변의 승인이나 가설 검증이 아닙니다.
waiting 질문은 answered/dismissed로 닫지 마세요. 필요한 근거가 있는 질문만 평가하고 나머지 질문의 기간·완료 기준은 유지하세요.
mode=research-only이면 notification.send=false입니다. 부분 근거에 기반한 잠정 knowledgeResearch와 반론·부족 항목을 내부 연구로 작성하세요.
증거 없는 설명을 만들지 말고 조사 질문으로 남기세요. 연구 가설에는 어떤 근거와 가정으로 연결했는지, 무엇이 반증하는지를 보존하세요.
이전 researchKnowledge.progress는 과거 조회 진행 기록입니다. 그때 읽었거나 미확인한 항목이지 현재 시장 사실이나 답변 완료의 증거가 아닙니다.
"""


def instructions(packet):
    return INSTRUCTIONS if packet.get("researchScope", {}).get("version") == SCOPE_VERSION else ""


def validate_plan(result, packet):
    scope = packet.get("researchScope", {})
    if scope.get("version") != SCOPE_VERSION:
        return
    ready = {row["caseId"] for row in scope["questions"] if row["status"] == "ready"}
    scoped = {row["caseId"] for row in scope["questions"]}
    if scope["mode"] == "research-only" and result["notification"]["send"]:
        raise PlanValidationError("partial-research-notification", "notification.send", "잠정 연구는 내부 저장만 허용하므로 false로 작성하세요.")
    if any(row["action"] in {"answered", "dismissed"} and row["caseId"] in scoped - ready for row in result.get("caseReviews", [])):
        raise PlanValidationError("partial-research-question", "caseReviews", "대기 중인 질문의 답변·종료를 선언하지 말고 원래 기준과 부족 근거를 유지하세요.")
    if scope["mode"] == "research-only" and any(row["action"] in {"answered", "dismissed"} for row in result.get("caseReviews", [])):
        raise PlanValidationError("partial-research-question", "caseReviews", "잠정 연구에서 질문 답변·종료를 선언할 수 없습니다.")
