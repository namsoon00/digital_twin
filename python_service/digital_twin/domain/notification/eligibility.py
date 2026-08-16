"""Channel-independent delivery eligibility result."""

from dataclasses import asdict, dataclass, field
from typing import Dict, Mapping


@dataclass(frozen=True)
class NotificationEligibility:
    should_send: bool
    decision: str
    reason: str = ""
    suppression_reason: str = ""
    context_updates: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "shouldSend": payload["should_send"],
            "decision": payload["decision"],
            "reason": payload["reason"],
            "suppressionReason": payload["suppression_reason"],
            "contextUpdates": dict(payload["context_updates"] or {}),
        }
