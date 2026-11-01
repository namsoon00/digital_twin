"""Bounded operational events, never prompts, private reasoning or trade advice."""
import re
from .planning import identity, stamp

VERSION = "ai-activity-v1"
STAGES = {
    "task-started": "작업 시작", "task-completed": "작업 처리 완료",
    "task-failed": "작업 실패", "task-deferred": "작업 대기",
    "task-scheduled": "후속 작업 예약", "evidence-started": "근거 확인 시작",
    "evidence-ready": "근거 확인 완료", "memory-read": "이전 판단 확인",
    "input-prepared": "모델 입력 저장", "retrieval-completed": "추가 자료 조회 결과",
    "model-started": "모델 호출 시작", "model-completed": "모델 호출 완료",
    "model-failed": "모델 호출 실패", "case-updated": "질문·가설 진행",
    "worker-state": "워커 상태 변경", "resume-requested": "실행 재확인 요청",
}
TEXT_KEYS = {"workload", "capability", "status", "reasonCode", "nextCheckAt", "quality",
             "publicationStatus", "callId", "inputId", "caseStage", "caseKind", "promptVersion"}
NUMBER_KEYS = {"attempt", "factCount", "memoryCount", "readCount", "inputBytes", "childCount",
               "inputTokens", "outputTokens", "cachedTokens", "elapsedMs", "revision"}


def enabled(settings):
    return str(settings.get("aiActivityNotificationsEnabled", "false")).lower() in {"1", "true", "on"}


def text(value, limit=120):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))[:limit]


def event(stage, source_id, subject=None, details=None, at=None):
    if stage not in STAGES or not source_id:
        raise ValueError("unknown AI activity")
    subject, details = subject or {}, details or {}
    safe = {key: text(details[key]) for key in TEXT_KEYS if details.get(key) is not None}
    # Diagnostics publish a code only, never arbitrary exception text, URLs or credentials.
    if safe.get("reasonCode") and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,99}", safe["reasonCode"]):
        safe["reasonCode"] = "execution-error"
    safe.update({key: details[key] for key in NUMBER_KEYS
                 if type(details.get(key)) in {int, float} and 0 <= details[key] <= 10**12})
    if isinstance(details.get("reads"), list):
        safe["reads"] = [{"tool": text(row.get("tool"), 80), "status": text(row.get("status"), 80),
                          "factCount": max(0, min(row["factCount"], 1000000)) if type(row.get("factCount")) is int else 0}
                         for row in details["reads"][:20] if isinstance(row, dict)]
    return {"version": VERSION, "eventId": identity(VERSION, stage, source_id),
            "eventName": "ai.activity_recorded", "stage": stage, "label": STAGES[stage], "at": at or stamp(),
            **{key: text(subject.get(key), 191 if key == "accountId" else 120)
               for key in ("accountId", "symbol", "name", "taskId")}, "details": safe}
