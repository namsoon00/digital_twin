"""Render the rejected prose as an explicitly unverified operations artifact."""
from html import escape

from digital_twin.modules.notifications.application.ai_observation_message import clock_label, figure, metric, source_label


SECTION_LABELS = {"summary": "핵심 해석", "comparison": "이전 알림과 비교", "hypothesis": "가능한 설명",
                  "portfolioImpact": "보유·관심 상황의 의미", "counterEvidence": "반대 근거와 한계",
                  "notificationReason": "AI가 알리려던 이유"}


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
    lines += ["", "자동 수정: " + ({"failed": "수정 시도를 완료하지 못했습니다.",
                                  "rejected": "한 차례 수정 후에도 검증을 통과하지 못했습니다.",
                                  "accepted": "문장 검토는 통과했지만 발송 조건을 충족하지 못했습니다."}.get(
                                      repair.get("status"), "수정 없이 검토를 보류했습니다."))]
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
