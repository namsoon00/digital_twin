"""MySQL queue for isolated historical replay work."""

from datetime import timedelta
from typing import Dict, List

from digital_twin.modules.outcomes.domain.historical_replay import HistoricalReplayJob
from digital_twin.modules.outcomes.domain.price_recovery_paper import KIND, clock, utc
from digital_twin.infrastructure.mysql_operational_connection import MySQLOperationalConnection
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.infrastructure.operational_common import json_dumps
from digital_twin.infrastructure.settings import utc_now


class MySQLHistoricalReplayJobStore(MySQLOperationalConnection):
    def enqueue(self, job: HistoricalReplayJob) -> bool:
        self._insert(job)
        return True

    def enqueue_paper_study(self, job: HistoricalReplayJob) -> HistoricalReplayJob:
        self._insert(job, idempotent=True)
        return self.get(job.job_id)

    def _insert(self, job: HistoricalReplayJob, idempotent=False):
        payload = job.to_dict()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO historical_replay_jobs (
                    job_id, replay_kind, status, attempts, created_at, updated_at,
                    request_json, result_json, last_error, payload_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """ + (" ON DUPLICATE KEY UPDATE job_id = job_id" if idempotent else ""),
                (
                    job.job_id, job.replay_kind, job.status, job.attempts,
                    job.created_at, job.updated_at, json_dumps(job.request),
                    json_dumps(job.result), job.last_error, json_dumps(payload),
                ),
            )

    def get(self, job_id: str):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM historical_replay_jobs WHERE job_id = %s",
                (str(job_id or ""),),
            ).fetchone()
        return HistoricalReplayJob.from_dict(_json_loads(row["payload_json"], {})) if row else None

    def list(self, replay_kind: str = "", limit: int = 20) -> List[HistoricalReplayJob]:
        bounded = max(1, min(100, int(limit or 20)))
        query = "SELECT payload_json FROM historical_replay_jobs"
        params = []
        if replay_kind:
            query += " WHERE replay_kind = %s"
            params.append(str(replay_kind))
        query += " ORDER BY created_at DESC, job_id DESC LIMIT %s"
        params.append(bounded)
        with self.connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [HistoricalReplayJob.from_dict(_json_loads(row["payload_json"], {})) for row in rows]

    def claim_pending(self, limit: int = 1) -> List[HistoricalReplayJob]:
        stamp = utc_now()
        expired = utc(clock(stamp) - timedelta(minutes=15))
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT job_id, payload_json FROM historical_replay_jobs
                WHERE (attempts < 3 AND status IN ('pending', 'failed') AND (
                      replay_kind <> %s OR JSON_UNQUOTE(JSON_EXTRACT(request_json, '$.runNotBefore')) <= %s
                    )) OR (status = 'processing' AND replay_kind = %s AND updated_at <= %s)
                ORDER BY created_at, job_id
                LIMIT %s FOR UPDATE SKIP LOCKED
                """,
                (KIND, stamp, KIND, expired, max(1, int(limit or 1))),
            ).fetchall()
            jobs = []
            for row in rows:
                job = HistoricalReplayJob.from_dict(_json_loads(row["payload_json"], {}))
                if job.attempts >= 3:
                    job.status = "failed"
                    job.last_error = "Paper study worker stopped before saving the result."
                    job.updated_at = stamp
                    self._update_with_connection(connection, job)
                    continue
                job.status = "processing"
                job.attempts += 1
                job.updated_at = stamp
                job.last_error = ""
                self._update_with_connection(connection, job, claim=True)
                jobs.append(job)
        return jobs

    def mark_completed(self, job: HistoricalReplayJob, result: Dict[str, object]) -> None:
        job.status = "completed"
        job.result = dict(result or {})
        job.last_error = ""
        job.updated_at = utc_now()
        self._update(job)

    def mark_failed(self, job: HistoricalReplayJob, error: str) -> None:
        job.status = "failed"
        job.last_error = str(error or "")[:1000]
        job.updated_at = utc_now()
        self._update(job)

    def _update(self, job: HistoricalReplayJob) -> None:
        with self.transaction() as connection:
            self._update_with_connection(connection, job)

    def _update_with_connection(self, connection, job: HistoricalReplayJob, claim=False) -> None:
        fence = "" if claim else " AND status = 'processing' AND attempts = %s"
        args = (
            job.status, job.attempts, job.updated_at, json_dumps(job.result),
            job.last_error, json_dumps(job.to_dict()), job.job_id,
        )
        connection.execute(
            """
            UPDATE historical_replay_jobs
            SET status = %s, attempts = %s, updated_at = %s, result_json = %s,
                last_error = %s, payload_json = %s
            WHERE job_id = %s
            """ + fence,
            args if claim else (*args, job.attempts),
        )

    def summary(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM historical_replay_jobs GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["count"] or 0) for row in rows}
