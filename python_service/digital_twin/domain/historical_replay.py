"""Durable contracts for read-only historical investment replay jobs."""

from dataclasses import asdict, dataclass, field
from typing import Dict
import uuid

from .portfolio import utc_now_iso


REPLAY_KINDS = {"decision", "hypothesis"}
TERMINAL_REPLAY_STATUSES = {"completed", "failed"}


@dataclass
class HistoricalReplayJob:
    job_id: str
    replay_kind: str
    request: Dict[str, object]
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = ""
    status: str = "pending"
    attempts: int = 0
    result: Dict[str, object] = field(default_factory=dict)
    last_error: str = ""

    @classmethod
    def create(cls, replay_kind: str, request: Dict[str, object] = None):
        kind = str(replay_kind or "").strip().lower()
        if kind not in REPLAY_KINDS:
            raise ValueError("unsupported historical replay kind: " + kind)
        return cls(
            job_id=uuid.uuid4().hex,
            replay_kind=kind,
            request=dict(request or {}),
        )

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        row = dict(payload or {})
        return cls(
            job_id=str(row.get("jobId") or row.get("job_id") or ""),
            replay_kind=str(row.get("replayKind") or row.get("replay_kind") or ""),
            request=dict(row.get("request") or {}),
            created_at=str(row.get("createdAt") or ""),
            updated_at=str(row.get("updatedAt") or ""),
            status=str(row.get("status") or "pending"),
            attempts=int(row.get("attempts") or 0),
            result=dict(row.get("result") or {}),
            last_error=str(row.get("lastError") or ""),
        )

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        return {
            "jobId": payload["job_id"],
            "replayKind": payload["replay_kind"],
            "request": payload["request"],
            "createdAt": payload["created_at"],
            "updatedAt": payload["updated_at"],
            "status": payload["status"],
            "attempts": payload["attempts"],
            "result": payload["result"],
            "lastError": payload["last_error"],
        }
