import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict

from .portfolio import utc_now_iso


@dataclass
class NotificationJob:
    job_id: str
    account_id: str
    account_label: str
    message_type: str
    text: str
    status: str = "pending"
    attempts: int = 0
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = ""
    last_error: str = ""

    @classmethod
    def create(
        cls,
        text: str,
        account_id: str = "",
        account_label: str = "",
        message_type: str = "notification",
    ):
        return cls(
            job_id=uuid.uuid4().hex,
            account_id=str(account_id or ""),
            account_label=str(account_label or ""),
            message_type=str(message_type or "notification"),
            text=str(text or "").strip(),
        )

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        return cls(
            job_id=str(payload.get("jobId") or payload.get("job_id") or ""),
            account_id=str(payload.get("accountId") or payload.get("account_id") or ""),
            account_label=str(payload.get("accountLabel") or payload.get("account_label") or ""),
            message_type=str(payload.get("messageType") or payload.get("message_type") or "notification"),
            text=str(payload.get("text") or ""),
            status=str(payload.get("status") or "pending"),
            attempts=int(payload.get("attempts") or 0),
            created_at=str(payload.get("createdAt") or payload.get("created_at") or utc_now_iso()),
            updated_at=str(payload.get("updatedAt") or payload.get("updated_at") or ""),
            last_error=str(payload.get("lastError") or payload.get("last_error") or ""),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "jobId": payload["job_id"],
            "accountId": payload["account_id"],
            "accountLabel": payload["account_label"],
            "messageType": payload["message_type"],
            "text": payload["text"],
            "status": payload["status"],
            "attempts": payload["attempts"],
            "createdAt": payload["created_at"],
            "updatedAt": payload["updated_at"],
            "lastError": payload["last_error"],
        }
