"""Record owner feedback and open governed product-improvement proposals."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict, Mapping

from digital_twin.domain.investment_brain import LearningProposal


FEEDBACK_CHANGE_TYPES = {
    "too-vague": "review-prompt-specificity-and-action-contract",
    "too-technical": "review-customer-language-contract",
    "not-relevant": "review-notification-admission-relevance",
    "duplicate": "review-deduplication-state-transition",
    "too-late": "review-reasoning-delivery-latency",
}

FEEDBACK_REASON_LABELS = {
    "too-vague": "알림이 모호함",
    "too-technical": "알림 표현이 어려움",
    "not-relevant": "알림 관련성이 낮음",
    "duplicate": "알림이 반복됨",
    "too-late": "알림 도착이 늦음",
    "unspecified": "알림이 도움되지 않음",
}


def _int_setting(settings: Mapping[str, object], key: str, fallback: int) -> int:
    try:
        return int(float(str((settings or {}).get(key) or fallback)))
    except (TypeError, ValueError):
        return fallback


class NotificationFeedbackService:
    """Own the feedback use case without giving it deployment authority."""

    def __init__(self, notification_store, learning_proposal_store=None, settings=None):
        self.notification_store = notification_store
        self.learning_proposal_store = learning_proposal_store
        self.settings = dict(settings or {})

    def record(
        self,
        job_id: str,
        recipient_id: str,
        *,
        read=None,
        acknowledged=None,
        important=None,
        usefulness=None,
        feedback_reason=None,
    ) -> Dict[str, object]:
        job = self.notification_store.get(job_id)
        if not job:
            return {"error": "알림을 찾지 못했습니다."}
        receipt = self.notification_store.update_receipt(
            job_id,
            recipient_id,
            read=read,
            acknowledged=acknowledged,
            important=important,
            usefulness=usefulness,
            feedback_reason=feedback_reason,
        )
        proposal = self.propose_quality_review(receipt)
        return {
            "receipt": receipt,
            "learningProposal": proposal,
            "evolutionState": (
                "review-proposal-created" if proposal else "feedback-recorded"
            ),
        }

    def propose_quality_review(self, receipt: Mapping[str, object]) -> Dict[str, object]:
        if str(receipt.get("usefulness") or "") != "not-helpful":
            return {}
        reason = str(receipt.get("feedbackReason") or "unspecified")
        evidence_loader = getattr(
            self.notification_store,
            "negative_feedback_evidence",
            None,
        )
        proposal_saver = getattr(
            self.learning_proposal_store,
            "save_learning_proposal",
            None,
        )
        if not callable(evidence_loader) or not callable(proposal_saver):
            return {}
        threshold = max(
            2,
            min(
                20,
                _int_setting(
                    self.settings,
                    "investmentMessageQualityProposalThreshold",
                    3,
                ),
            ),
        )
        evidence = list(evidence_loader(
            str(receipt.get("recipientId") or "local-owner"),
            reason,
            limit=max(20, threshold * 4),
        ) or [])
        if len(evidence) < threshold:
            return {}
        stamp = str(receipt.get("feedbackAt") or "")
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            parsed = datetime.now(timezone.utc)
        iso_year, iso_week, _ = parsed.isocalendar()
        proposal_key = "|".join([
            str(receipt.get("recipientId") or "local-owner"),
            reason,
            str(iso_year),
            str(iso_week),
        ])
        proposal_id = "learning-proposal:message-quality:" + hashlib.sha256(
            proposal_key.encode("utf-8")
        ).hexdigest()[:24]
        job_ids = list(dict.fromkeys(
            str(item.get("jobId") or "") for item in evidence
            if str(item.get("jobId") or "")
        ))
        episode_ids = list(dict.fromkeys(
            str(item.get("decisionEpisodeId") or "") for item in evidence
            if str(item.get("decisionEpisodeId") or "")
        ))
        message_types = sorted({
            str(item.get("messageType") or "") for item in evidence
            if str(item.get("messageType") or "")
        })
        label = FEEDBACK_REASON_LABELS.get(reason, FEEDBACK_REASON_LABELS["unspecified"])
        proposal = LearningProposal(
            proposal_id=proposal_id,
            title=label + " 개선 검토",
            reason=(
                "최근 사용자 평가에서 '" + label + "' 응답이 "
                + str(len(evidence)) + "건 확인됐습니다. 연결된 실제 알림을 재생해 "
                "발송 조건과 문장 계약을 함께 검토해야 합니다."
            ),
            source_episode_ids=episode_ids,
            affected_rule_ids=[],
            proposed_change={
                "changeType": FEEDBACK_CHANGE_TYPES.get(
                    reason,
                    "review-notification-message-quality",
                ),
                "feedbackReason": reason,
                "negativeFeedbackCount": len(evidence),
                "sourceNotificationJobIds": job_ids,
                "sourceMessageTypes": message_types,
                "reviewWindow": str(iso_year) + "-W" + str(iso_week).zfill(2),
                "automaticDeployment": False,
                "requiredValidation": [
                    "historical-notification-replay",
                    "customer-message-quality-contract",
                    "human-approval",
                ],
            },
        )
        return proposal_saver(proposal).to_dict()
