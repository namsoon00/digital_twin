"""Fence a job before any receipt, anchor or terminal-state write."""

from digital_twin.infrastructure.storage_values import utc_now
from digital_twin.modules.reasoning.domain.job_claim import require_job_claim


def lock_job_claim(connection, job_id: str, worker_id: str = "", claimed_at: str = ""):
    row = connection.execute(
        "SELECT job_id, job_status, lease_owner, lease_expires_at, claimed_at "
        "FROM reasoning_engine_jobs WHERE job_id = %s FOR UPDATE",
        (str(job_id or ""),),
    ).fetchone()
    require_job_claim(
        row, str(job_id or ""), str(worker_id or ""), str(claimed_at or ""), utc_now()
    )
    return row
