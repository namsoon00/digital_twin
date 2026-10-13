"""Operations replies require the exact successful original and destination."""
from contextlib import contextmanager
import json

from digital_twin.modules.notifications.domain.delivery_suppression import NotificationDeliverySuppressed
from digital_twin.modules.notifications.domain.research_thread import ResearchDeliveryDeferred


class DiagnosticThreadStore:
    @contextmanager
    def diagnostic_delivery_thread(self, job, destination):
        event = job.context.get("aiDiagnosticProgress") or {}
        if (event.get("accountId") != job.account_id or event.get("symbol") != job.context.get("symbol")
                or not all(event.get(k) for k in ("rootJobId", "rootTaskId", "worldId"))):
            raise NotificationDeliverySuppressed("원본 초안과 후속 안내의 계정·종목 연결을 확인할 수 없습니다.")
        with self.delivery_named_lock("diagnostic-reply:" + event["rootTaskId"][:44]) as acquired:
            if not acquired:
                raise ResearchDeliveryDeferred("원본 초안의 앞선 답글 전송을 기다립니다.")
            with self.connect() as connection:
                row = connection.execute("SELECT metadata_json FROM notification_delivery_attempts "
                    "WHERE job_id=%s AND status='delivered' AND channel='operationsTelegram' "
                    "AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json,'$.accountId'))=%s "
                    "AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json,'$.aiObservationDiagnostic.taskId'))=%s "
                    "AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json,'$.aiObservationDiagnostic.symbol'))=%s "
                    "AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json,'$.messageType'))='aiObservationDiagnostic' "
                    "AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json,'$.deliveryCheckpoint.destinationFingerprint'))=%s "
                    "AND JSON_EXTRACT(metadata_json,'$.receiptVerified')=true "
                    "AND JSON_EXTRACT(metadata_json,'$.destinationVerified')=true ORDER BY completed_at DESC LIMIT 1",
                    (event["rootJobId"], job.account_id, event["rootTaskId"], event["symbol"], destination)).fetchone()
            metadata = json.loads(row["metadata_json"]) if row else {}
            ids = (metadata.get("deliveryCheckpoint") or {}).get("messageIds") or []
            if not ids:
                raise NotificationDeliverySuppressed("이 수신처의 원본 초안 전송을 확인할 수 없어 후속 안내를 새 메시지로 보내지 않습니다.")
            checkpoint = ((job.context.get("transportDelivery") or {}).get("checkpoint") or {})
            if checkpoint.get("messageIds") and (checkpoint.get("destinationFingerprint") != destination
                    or str(checkpoint.get("replyToMessageId")) != str(ids[0])):
                raise NotificationDeliverySuppressed("부분 전송된 후속 안내의 원본 메시지 또는 수신처가 달라졌습니다.")
            yield ids[0]

    def save_diagnostic_delivery_progress(self, job, destination, checkpoint):
        if not checkpoint.get("messageIds") or checkpoint.get("destinationFingerprint") != destination:
            raise ValueError("진단 답글의 전송 기록과 수신처가 다릅니다.")
        self.save_delivery_progress(job)
