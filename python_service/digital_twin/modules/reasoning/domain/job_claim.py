"""Ownership of one durable reasoning attempt, independent of storage."""

from datetime import datetime, timezone
from typing import Mapping


class ReasoningJobLeaseLost(RuntimeError):
    def __init__(self, job_id: str, reason: str):
        self.job_id = job_id
        self.reason = reason
        super().__init__("Reasoning job transition rejected: " + reason)


def require_job_claim(
    row: Mapping[str, object],
    job_id: str,
    worker_id: str,
    claimed_at: str,
    now: datetime,
) -> None:
    if not row:
        raise ReasoningJobLeaseLost(job_id, "job-missing")
    status = str(row.get("job_status") or "")
    if status in {"completed", "failed", "excluded", "superseded"}:
        raise ReasoningJobLeaseLost(job_id, "already-terminal")
    if worker_id:
        if status != "processing" or str(row.get("lease_owner") or "") != worker_id:
            raise ReasoningJobLeaseLost(job_id, "different-owner")
        if claimed_at and str(row.get("claimed_at") or "") != claimed_at:
            raise ReasoningJobLeaseLost(job_id, "different-attempt")
        try:
            expires = datetime.fromisoformat(
                str(row.get("lease_expires_at") or "").replace("Z", "+00:00")
            )
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        except ValueError:
            raise ReasoningJobLeaseLost(job_id, "invalid-lease") from None
        if expires <= now:
            raise ReasoningJobLeaseLost(job_id, "lease-expired")
    elif status == "processing" and row.get("lease_owner"):
        raise ReasoningJobLeaseLost(job_id, "owner-required")
