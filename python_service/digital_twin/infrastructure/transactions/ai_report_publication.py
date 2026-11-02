"""Explicit customer delivery of a frozen report, committed under a report lock."""
import hashlib
import json

from digital_twin.modules.notifications.contracts import NotificationJob
from digital_twin.modules.notifications.application.ai_report_message import report_summary, render_ai_report


class AIReportPublication:
    def __init__(self, reports, notification_factory):
        self.reports, self.notification_factory = reports, notification_factory

    def __call__(self, report):
        # Construct notification storage before holding the report row lock.
        queue = self.notification_factory()
        with self.reports.transaction() as connection:
            row = connection.execute("SELECT payload_json,content_hash,notification_job_id FROM ai_portfolio_reports "
                "WHERE report_id=%s AND account_id=%s FOR UPDATE", (report["reportId"], report["accountId"])).fetchone()
            persisted = self.reports.decode(row)
            if not persisted or persisted["contentHash"] != report["contentHash"]:
                raise ValueError("발송할 보고서가 저장된 내용과 일치하지 않습니다.")
            previous_job_id = row["notification_job_id"]
            # Only an explicit send may correct a completed historical research
            # delivery. Never reopen that job, discard its receipt, or reset a
            # partially delivered message's destination checkpoint.
            correction = (previous_job_id and
                self._status(connection, persisted, previous_job_id)["status"] == "other-channel")
            if not previous_job_id or correction:
                artifact = report_summary(persisted)
                job_id = "ai-report-customer-" + report["reportId"][:38]
                if job_id == previous_job_id:
                    raise ValueError("고객 채널 발송 작업의 경로가 일치하지 않습니다. 전송 이력을 확인해 주세요.")
                job = NotificationJob(job_id=job_id, account_id=report["accountId"], account_label="",
                    message_type="aiReport", text=render_ai_report(artifact), dedupe_key=job_id,
                    source_event_id=report["reportId"], source_event_name="ai.portfolio_report_requested",
                    context={"messageType": "aiReport", "accountId": report["accountId"], "aiPortfolioReport": artifact,
                        "requestedDeliveryRoute": "account", "previousDeliveryJobId": previous_job_id or "",
                        "notificationContent": {"kind": "ai-report", "body": artifact["body"]}})
                queue.enqueue_with_connection(connection, job)
                connection.execute("UPDATE ai_portfolio_reports SET notification_job_id=%s WHERE report_id=%s AND account_id=%s",
                                   (job_id, report["reportId"], report["accountId"]))
        return self.status(report)

    def status(self, report):
        with self.reports.connect() as connection:
            row = connection.execute("SELECT notification_job_id FROM ai_portfolio_reports WHERE report_id=%s AND account_id=%s",
                                     (report["reportId"], report["accountId"])).fetchone()
            job_id = (row or {}).get("notification_job_id")
            return self._status(connection, report, job_id)

    @staticmethod
    def _status(connection, report, job_id):
        if not job_id:
            return {"status": "not-requested"}
        job = connection.execute("SELECT status FROM notification_jobs WHERE job_id=%s AND account_id=%s AND message_type='aiReport'",
                                 (job_id, report["accountId"])).fetchone()
        attempts = connection.execute("SELECT status,completed_at,metadata_json FROM notification_delivery_attempts "
            "WHERE job_id=%s ORDER BY completed_at DESC,attempt_id DESC LIMIT 10", (job_id,)).fetchall()
        for attempt in attempts:
            data = json.loads(attempt["metadata_json"])
            identity = data.get("aiPortfolioReport", {})
            if (attempt["status"] == "delivered" and data.get("receiptVerified") is True
                    and data.get("destinationVerified") is True and data.get("accountId") == report["accountId"]
                    and data.get("messageType") == "aiReport"
                    and identity.get("version") == "ai-report-summary-v1" and identity.get("summaryHash")
                    and identity.get("reportId") == report["reportId"] and identity.get("reportHash") == report["contentHash"]
                    and data.get("renderedMessage") and hashlib.sha256(data["renderedMessage"].encode()).hexdigest() == data.get("messageSha256")):
                route = data.get("deliveryRoute")
                if route == "account":
                    return {"status": "delivered", "jobId": job_id, "deliveredAt": attempt["completed_at"],
                            "receiptVerified": True, "deliveryRoute": route}
                if route == "ai-research" and (job is None or job["status"] == "done"):
                    return {"status": "other-channel", "jobId": job_id, "deliveryRoute": route, "receiptVerified": False}
        status = (job or {}).get("status", "receipt-unconfirmed")
        return {"status": "receipt-unconfirmed" if status == "done" else status, "jobId": job_id, "receiptVerified": False}
