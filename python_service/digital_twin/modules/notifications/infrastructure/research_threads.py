"""Small durable Telegram anchors independent of notification payload retention."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json

from digital_twin.modules.notifications.domain.research_thread import research_thread_key, ResearchDeliveryDeferred
from digital_twin.infrastructure.operational_common import MAX_NOTIFICATION_DELIVERY_ATTEMPTS
from digital_twin.infrastructure.settings import utc_now


class ResearchThreadStore:
    def register_research_thread(self, connection, job):
        key = research_thread_key(job)
        if key:
            existing = connection.execute("SELECT thread_key FROM notification_research_threads WHERE thread_key=%s", (key,)).fetchone()
            if existing:
                return
            event = job.context["researchProgress"]
            sources = list(dict.fromkeys(event.get("sourceQuestionIds") or []))
            root_case = sources[0] if len(sources) == 1 else event["caseId"]
            # Adopt retained question receipts from before reply support existed.
            original = connection.execute("SELECT job_id FROM notification_jobs WHERE account_id=%s AND symbol=%s "
                "AND message_type='researchProgress' AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.context.researchProgress.caseId'))=%s "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.context.researchProgress.worldId'))=%s "
                "ORDER BY (JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.context.researchProgress.stage'))='created') DESC,created_at,job_id LIMIT 1", (job.account_id, event["symbol"], root_case, event["worldId"])).fetchone()
            connection.execute("INSERT IGNORE INTO notification_research_threads "
                "(thread_key,first_job_id,receipts_json,updated_at) VALUES (%s,%s,'{}',%s)",
                (key, original["job_id"] if original else job.job_id, utc_now()))

    def research_delivery_thread(self, job, destination):
        return self._delivery_thread(job, destination, research_thread_key(job), self.register_research_thread)

    @contextmanager
    def _delivery_thread(self, job, destination, key, register):
        lock = "research-send:" + key[:48]
        with self.connect() as connection:
            acquired = connection.execute("SELECT GET_LOCK(%s,0) AS acquired", (lock,)).fetchone()
            if not acquired or acquired["acquired"] != 1:
                raise ResearchDeliveryDeferred("같은 대화의 앞선 알림 발송을 기다립니다.")
            try:
                with self.transaction() as tx:
                    register(tx, job)
                    row = tx.execute("SELECT first_job_id,receipts_json FROM notification_research_threads WHERE thread_key=%s", (key,)).fetchone()
                    receipts = json.loads(row["receipts_json"])
                    anchor = receipts.get(destination) or {}
                    if not anchor:
                        old = tx.execute("SELECT text,payload_json FROM notification_jobs WHERE job_id=%s", (row["first_job_id"],)).fetchone()
                        if old:
                            previous = self.job_from_row(old)
                            checkpoint = (previous.context.get("transportDelivery") or {}).get("checkpoint") or {}
                            ids = checkpoint.get("messageIds") or []
                            if ids and checkpoint.get("destinationFingerprint") == destination:
                                anchor = {"messageId": str(ids[0]), "jobId": previous.job_id}
                                receipts[destination] = anchor
                                tx.execute("UPDATE notification_research_threads SET receipts_json=%s,updated_at=%s WHERE thread_key=%s",
                                    (json.dumps(receipts), utc_now(), key))
                    if not anchor and row["first_job_id"] != job.job_id:
                        parent = tx.execute("SELECT status,attempts FROM notification_jobs WHERE job_id=%s", (row["first_job_id"],)).fetchone()
                        if parent and (parent["status"] in {"pending", "processing", "awaiting_ai"} or
                                       (parent["status"] == "failed" and parent["attempts"] < MAX_NOTIFICATION_DELIVERY_ATTEMPTS)):
                            raise ResearchDeliveryDeferred("원본 메시지가 전송되기를 기다립니다.")
                yield anchor.get("messageId") if anchor.get("jobId") != job.job_id else None
            finally:
                connection.execute("SELECT RELEASE_LOCK(%s)", (lock,)).fetchone()

    def save_research_delivery_progress(self, job, destination, checkpoint):
        self._save_thread_progress(job, destination, checkpoint, research_thread_key(job))

    def _save_thread_progress(self, job, destination, checkpoint, key):
        ids = checkpoint.get("messageIds") or []
        if not ids or checkpoint.get("destinationFingerprint") != destination:
            raise ValueError("메시지의 전송 영수증이 수신처와 일치하지 않습니다.")
        job.updated_at = utc_now()
        with self.transaction() as connection:
            self.upsert_job_with_connection(connection, job)
            row = connection.execute("SELECT receipts_json FROM notification_research_threads WHERE thread_key=%s FOR UPDATE", (key,)).fetchone()
            receipts = json.loads(row["receipts_json"])
            receipts.setdefault(destination, {"messageId": str(ids[0]), "jobId": job.job_id})
            connection.execute("UPDATE notification_research_threads SET receipts_json=%s,updated_at=%s WHERE thread_key=%s",
                (json.dumps(receipts), job.updated_at, key))
            connection.execute("UPDATE notification_jobs SET processing_started_at=%s WHERE job_id=%s AND status='processing'", (job.updated_at, job.job_id))

    def defer_research_delivery(self, job, reason):
        job.status, job.last_error, job.updated_at = "pending", str(reason), utc_now()
        job.attempts = max(0, job.attempts - 1)
        retry = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            self.upsert_job_with_connection(connection, job)
            connection.execute("UPDATE notification_jobs SET retry_at=%s,processing_started_at='' WHERE job_id=%s", (retry, job.job_id))
            self.record_lifecycle_with_connection(connection, job, "eligibility_checked", "deferred", str(reason))
