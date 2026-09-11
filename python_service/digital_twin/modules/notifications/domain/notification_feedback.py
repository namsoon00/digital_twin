"""User feedback contract for investment notifications.

Transport receipts answer whether a message was delivered or read. This
contract records the separate product question: whether the message helped the
owner make sense of the investment situation.
"""

from __future__ import annotations

from typing import Dict


NOTIFICATION_FEEDBACK_VERSION = "notification-feedback-v1"
NOTIFICATION_USEFULNESS_VALUES = {"", "helpful", "not-helpful"}
NOTIFICATION_FEEDBACK_REASONS = {
    "",
    "actionable",
    "clear",
    "too-vague",
    "too-technical",
    "not-relevant",
    "duplicate",
    "too-late",
    "other",
    "unspecified",
}


def normalize_notification_feedback(
    usefulness: object,
    reason: object = "",
) -> Dict[str, str]:
    normalized_usefulness = str(usefulness or "").strip().lower()
    normalized_reason = str(reason or "").strip().lower()
    if normalized_usefulness not in NOTIFICATION_USEFULNESS_VALUES:
        raise ValueError("지원하지 않는 알림 유용성 평가입니다.")
    if normalized_reason not in NOTIFICATION_FEEDBACK_REASONS:
        raise ValueError("지원하지 않는 알림 평가 사유입니다.")
    if not normalized_usefulness:
        normalized_reason = ""
    elif normalized_usefulness == "not-helpful" and not normalized_reason:
        normalized_reason = "unspecified"
    elif normalized_usefulness == "helpful" and not normalized_reason:
        normalized_reason = "clear"
    return {
        "version": NOTIFICATION_FEEDBACK_VERSION,
        "usefulness": normalized_usefulness,
        "reason": normalized_reason,
    }
