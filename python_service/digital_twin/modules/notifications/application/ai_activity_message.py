"""Render recorded AI operations without invoking a model."""
from html import escape
from digital_twin.modules.ai_orchestration.contracts import AI_ACTIVITY_VERSION

LABELS = {"observe": "기업·질문 연구", "market-observe": "시장 관찰", "research": "자료 수집",
          "research-plan": "추가 조사 계획", "independent-observation": "중앙 AI 분석",
          "news-analysis": "뉴스 분석", "model-review": "모델 검토", "source-search": "외부 자료 탐색",
          "investment-judgement": "투자 판단 검토", "interactive-chat": "대화 응답",
          "recovery-wait": "제공사 재시도 대기", "budget-wait": "내부 예산 대기",
          "resource-deferred": "실행 자리·메모리 대기", "paused": "일시 중지", "idle": "예약 대기",
          "unavailable": "실행 오류", "completed": "완료", "failed": "실패", "pending": "대기",
          "unchanged-wait": "새 자료 대기", "unchanged": "새 변화 없음", "retired": "관찰 종료",
          "accepted": "검증 통과", "observation-only": "관찰 기록", "rejected": "검증 미통과",
          "queued": "발송 대기", "recorded": "기록 저장", "suppressed": "발송 보류",
          "quota": "제공사 사용 한도 오류", "timeout": "실행 시간 초과",
          "execution-error": "실행 오류 · 상세 원인은 로컬 기록에서 확인",
          "created": "질문 생성", "assessment": "근거와 판단 연결",
          "research-waiting": "추가 자료 대기", "research-returned": "조사 결과 반영",
          "research-repeat-suppressed": "동일 조사 반복 억제", "review-deferred": "검토 대기",
          "business-review-deferred": "기업 가치 검토 대기", "review-failed": "검토 실패",
          "business-reviewed": "기업 가치 검토 완료", "feedback-proposed": "개선안 기록",
          "owner-review": "사용자 피드백 반영", "waiting": "후속 확인 대기",
          "open": "조사 중", "answered": "답변 확보", "partial": "일부 근거 확보",
          "awaiting-evidence": "필요 자료 대기", "research-cooldown": "추가 조사 간격 대기"}


def phase(version):
    for marker, label in (("repair", "출력 수정"), ("review", "결과 검토"),
                          ("retrieval", "추가 조회 계획"), ("research-plan", "조사 계획")):
        if marker in version:
            return label
    return "분석 작성" if "observation" in version else ""


def render_ai_activity(value):
    if value.get("version") != AI_ACTIVITY_VERSION:
        raise ValueError("unsupported AI activity event")
    details = value["details"]
    lines = ["🧠 AI 실행 · " + value["label"]]
    purpose = phase(details.get("promptVersion", ""))
    if purpose:
        lines.append("단계: " + purpose)
    if value.get("name") or value.get("symbol"):
        lines += ["대상: " + (value.get("name") or value["symbol"])
                  + (" (" + value["symbol"] + ")" if value.get("symbol") and value.get("name") != value["symbol"] else "")]
    for key, label in (("capability", "작업"), ("workload", "호출 용도"), ("status", "상태"),
                       ("caseStage", "연구 단계"), ("quality", "검증"), ("publicationStatus", "결과 알림"),
                       ("reasonCode", "사유"), ("nextCheckAt", "다음 확인")):
        if details.get(key):
            lines.append(label + ": " + LABELS.get(details[key], details[key]))
    for key, label in (("factCount", "확인 근거"), ("memoryCount", "이전 판단"), ("readCount", "조회"),
                       ("childCount", "후속 예약")):
        if key in details:
            lines.append(f"{label}: {details[key]:,}건")
    for row in details.get("reads", []):
        lines.append(f"• {row['tool']} · {LABELS.get(row['status'], row['status'])} · 근거 {row['factCount']}건")
    if "inputTokens" in details and "outputTokens" in details:
        lines.append(f"사용 토큰: 입력 {details['inputTokens']:,} / 출력 {details['outputTokens']:,}")
    elif value["stage"] in {"model-completed", "model-failed"}:
        lines.append("사용 토큰: 미측정")
    if "elapsedMs" in details:
        lines.append(f"처리 시간: {details['elapsedMs'] / 1000:.1f}초")
    if value["stage"] == "task-completed":
        lines.append("작업 처리 기록이며 판단 정확도나 투자 성과를 뜻하지 않습니다.")
    reference = details.get("callId") or value.get("taskId") or value["eventId"]
    lines += ["기록 " + value["at"] + " · " + reference[:12]]
    return escape("\n".join(lines), quote=False)
