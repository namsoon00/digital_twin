"""Notification lifecycle states and append-only audit events."""

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Mapping, Set

from ..portfolio import utc_now_iso


class NotificationStage(str, Enum):
    RECEIVED = "received"
    ELIGIBILITY_CHECKED = "eligibility_checked"
    AWAITING_DECISION = "awaiting_decision"
    DELIVERY_REASON_VALIDATED = "delivery_reason_validated"
    READY_TO_RENDER = "ready_to_render"
    RENDERED = "rendered"
    DISPATCHING = "dispatching"
    DELIVERED = "delivered"
    SUPPRESSED = "suppressed"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    FAILED = "failed"


TERMINAL_NOTIFICATION_STAGES: Set[NotificationStage] = {
    NotificationStage.DELIVERED,
    NotificationStage.SUPPRESSED,
    NotificationStage.SUPERSEDED,
    NotificationStage.EXPIRED,
}


ALLOWED_NOTIFICATION_TRANSITIONS = {
    NotificationStage.RECEIVED: {
        NotificationStage.ELIGIBILITY_CHECKED,
        NotificationStage.SUPPRESSED,
        NotificationStage.FAILED,
    },
    NotificationStage.ELIGIBILITY_CHECKED: {
        NotificationStage.AWAITING_DECISION,
        NotificationStage.DELIVERY_REASON_VALIDATED,
        NotificationStage.READY_TO_RENDER,
        NotificationStage.SUPPRESSED,
        NotificationStage.EXPIRED,
        NotificationStage.FAILED,
    },
    NotificationStage.AWAITING_DECISION: {
        NotificationStage.DELIVERY_REASON_VALIDATED,
        NotificationStage.READY_TO_RENDER,
        NotificationStage.SUPERSEDED,
        NotificationStage.SUPPRESSED,
        NotificationStage.EXPIRED,
        NotificationStage.FAILED,
    },
    NotificationStage.DELIVERY_REASON_VALIDATED: {
        NotificationStage.READY_TO_RENDER,
        NotificationStage.SUPPRESSED,
        NotificationStage.EXPIRED,
        NotificationStage.FAILED,
    },
    NotificationStage.READY_TO_RENDER: {
        NotificationStage.RENDERED,
        NotificationStage.SUPPRESSED,
        NotificationStage.EXPIRED,
        NotificationStage.FAILED,
    },
    NotificationStage.RENDERED: {
        NotificationStage.DISPATCHING,
        NotificationStage.SUPPRESSED,
        NotificationStage.EXPIRED,
        NotificationStage.FAILED,
    },
    NotificationStage.DISPATCHING: {
        NotificationStage.DELIVERED,
        NotificationStage.FAILED,
    },
    NotificationStage.FAILED: {
        NotificationStage.ELIGIBILITY_CHECKED,
        NotificationStage.DELIVERY_REASON_VALIDATED,
        NotificationStage.READY_TO_RENDER,
        NotificationStage.DISPATCHING,
        NotificationStage.SUPPRESSED,
        NotificationStage.EXPIRED,
    },
}


def notification_transition_allowed(current: object, target: object) -> bool:
    try:
        current_stage = NotificationStage(str(current or ""))
        target_stage = NotificationStage(str(target or ""))
    except ValueError:
        return False
    return current_stage == target_stage or target_stage in ALLOWED_NOTIFICATION_TRANSITIONS.get(current_stage, set())


def age_minutes_since(value: str, now=None) -> int:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0, int((current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() // 60))


@dataclass(frozen=True)
class NotificationLifecycleEvent:
    job_id: str
    stage: str
    outcome: str
    reason: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "eventId": payload["event_id"],
            "jobId": payload["job_id"],
            "stage": payload["stage"],
            "outcome": payload["outcome"],
            "reason": payload["reason"],
            "metadata": dict(payload["metadata"] or {}),
            "createdAt": payload["created_at"],
        }
