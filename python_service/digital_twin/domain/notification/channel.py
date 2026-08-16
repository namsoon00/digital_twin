"""Delivery channel result independent of a concrete transport vendor."""

from dataclasses import asdict, dataclass
from typing import Dict


@dataclass(frozen=True)
class DeliveryReceipt:
    delivered: bool
    channel: str
    provider: str = ""
    reason: str = ""
    external_id: str = ""
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "delivered": payload["delivered"],
            "channel": payload["channel"],
            "provider": payload["provider"],
            "reason": payload["reason"],
            "externalId": payload["external_id"],
            "startedAt": payload["started_at"],
            "completedAt": payload["completed_at"],
        }
