"""Render the rejected prose as an explicitly unverified operations artifact."""
from html import escape

from digital_twin.modules.notifications.application.ai_observation_message import clock_label, figure, metric, source_label


SECTION_LABELS = {"summary": "핵심 해석", "comparison": "이전 알림과 비교", "hypothesis": "가능한 설명",
                  "portfolioImpact": "보유·관심 상황의 의미", "counterEvidence": "반대 근거와 한계",
                  "notificationReason": "AI가 알리려던 이유"}


def repair_summary(diagnostic):
    repair = diagnostic.get("repair") or {}
    validation = diagnostic.get("validationRepair") or {}
    if validation.get("status") == "validated":
        return "출력 형식·필수 항목을 한 차례 수정했습니다. 형식 검증은 통과했지만 최종 발송 검증은 통과하지 못했습니다."
    return {"failed": "한 차례 수정을 시도했지만 완료하지 못했습니다.",
            "rejected": "한 차례 수정 후에도 검증을 통과하지 못했습니다.",
            "accepted": "문장 검토는 통과했지만 발송 조건을 충족하지 못했습니다."}.get(
                repair.get("status"), "이번 기록에는 자동 수정 시도가 없습니다. 다음 관찰에서 다시 확인합니다.")


def render_ai_observation_diagnostic(diagnostic, *, debug_number=""):
    def text(value):
        return escape(str(value or ""), quote=False)

    lines = ["<b>🧪 검증 미통과 초안 · " + text(diagnostic.get("name")) + " (" + text(diagnostic.get("symbol")) + ")</b>",
             "운영자 확인용 · 아래 AI 원문에는 오류나 근거가 부족한 해석이 포함될 수 있습니다.",
             "검증기의 오차단 여부도 함께 확인하기 위해 보냅니다.", "", "<b>정상 알림으로 보내지 못한 이유</b>"]
    for reason in diagnostic.get("reasons", []):
        for key, label in SECTION_LABELS.items():
            if reason.startswith(key + ":"):
                reason = label + reason[len(key):]
        lines.append("• " + text(reason))
    review = diagnostic.get("review") or {}
    if review.get("reason"):
        lines.append("• 독립 검토: " + text(review["reason"]))
    sections = review.get("sections") or {}
    # MySQL JSON storage can reorder object keys. The delivery guard must
    # reproduce identical bytes from equal diagnostic values after reload.
    order = [key for key in SECTION_LABELS if key in sections]
    order.extend(sorted(key for key in sections if key not in SECTION_LABELS))
    for key in order:
        reason = sections[key]
        lines.append("• " + text(SECTION_LABELS.get(key, key)) + ": " + text(reason))
    repair = diagnostic.get("repair") or {}
    repair_text = ({"failed": "수정 시도를 완료하지 못했습니다.",
                                  "rejected": "한 차례 수정 후에도 검증을 통과하지 못했습니다.",
                                  "accepted": "문장 검토는 통과했지만 발송 조건을 충족하지 못했습니다."}.get(
                                      repair.get("status"), "수정 없이 검토를 보류했습니다."))
    if diagnostic.get("version") == "ai-observation-diagnostic-v2":
        repair_text = repair_summary(diagnostic)
    lines += ["", "자동 수정: " + repair_text]
    if diagnostic.get("version") == "ai-observation-diagnostic-v2":
        lines += ["다음 처리: 후속 관찰의 재검토·자료 대기·정식 알림 전송 결과를 이 메시지의 답글로 안내합니다.",
                  "같은 대기 상태는 반복 안내하지 않습니다. 정식 알림은 별도의 검증과 실제 전송 확인이 필요합니다."]
    lines += ["", "<b>AI 원문 · 검증되지 않은 내용</b>"]
    draft = {**diagnostic.get("draft", {}), "notificationReason": diagnostic.get("notificationReason", "")}
    for key, label in SECTION_LABELS.items():
        if draft.get(key):
            lines += ["", label, text(draft[key])]
    lines += ["", "<b>당시 분석에 입력된 시세</b>"]
    quote = diagnostic.get("quote") or {}
    for field in ("currentPrice", "changeRate", "ma5", "ma20", "ma60"):
        if figure(quote.get(field)):
            # Unknown currency is visible, never inferred from the symbol.
            fact = {**quote, "currency": quote.get("currency") or " (통화 미확인)"}
            lines.append("• " + text(metric(fact, field)))
    if not quote:
        lines.append("• 표시 가능한 시세가 없습니다.")
    lines += ["시세 기준 " + clock_label(quote.get("sourceAsOf") or quote.get("asOf")), "출처 " + text(source_label(quote)),
              "자료 상태 " + text(quote.get("dataState") or "미확인") + " · 신선도 " + text(quote.get("freshnessStatus") or "미확인"),
              "자료 수집 " + clock_label(diagnostic.get("capturedAt")),
              "분석 기준 " + clock_label(diagnostic.get("observedAt")), "",
              "정상 AI 관찰의 발송 이력·쿨다운·후속 검증 기준에는 반영하지 않습니다.",
              "관찰 번호 " + text(diagnostic.get("taskId")), "진단 알림 " + text(debug_number)]
    return "\n".join(lines)


def render_diagnostic_progress(event, *, debug_number=""):
    text = lambda value: escape(str(value or ""), quote=False)
    labels = {"reviewing": "후속 자료 재검토 중", "waiting": "자료를 기다리는 중",
              "rejected": "재검토 후에도 발송 보류", "validated": "검증 통과 · 발송 대기",
              "delivered": "정식 AI 알림 전송 완료", "delivery-blocked": "정식 알림 전송 보류",
              "retry": "분석 재시도 대기", "closed": "후속 확인 종료"}
    lines = ["<b>🧪 초안 후속 처리 · " + text(event.get("name")) + " (" + text(event.get("symbol")) + ")</b>",
             "원본 초안 N-" + text(event["rootJobId"][:8].upper()), "",
             "<b>" + text(labels.get(event["stage"], "처리 상태 확인")) + "</b>"]
    for reason in event.get("reasons", [])[:4]:
        for key, label in SECTION_LABELS.items():
            if reason.startswith(key + ":"):
                reason = label + reason[len(key):]
        lines.append("• " + text(reason))
    if len(event.get("reasons", [])) > 4:
        lines.append("• 추가 확인 항목 " + str(len(event["reasons"]) - 4) + "개는 관찰 기록에 보존했습니다.")
    if event.get("repair") or event.get("validationRepair"):
        lines += ["", "자동 수정: " + text(repair_summary(event))]
    if event.get("stage") == "delivered":
        lines += ["정식 알림 N-" + text(event.get("deliveryJobId", "")[:8].upper()),
                  "전송 확인 " + clock_label(event.get("deliveredAt")),
                  "후속 분석이 검증을 통과해 전송됐습니다. 원본의 미검증 문장을 승인한 것은 아닙니다."]
    elif event.get("nextCheckAt"):
        lines.append("다음 정기 확인: " + clock_label(event["nextCheckAt"]) + " (이 안내 시점의 예약 · 새 자료가 들어오면 조정될 수 있음)")
    lines += ["", "확인 " + clock_label(event.get("observedAt")),
              "이 답글은 처리 상태 안내이며 투자 판단이나 정상 AI 알림의 발송 이력에 포함되지 않습니다.",
              "진단 알림 " + text(debug_number)]
    return "\n".join(lines)
