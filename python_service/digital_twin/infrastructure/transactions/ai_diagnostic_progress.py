"""Join durable AI recovery events to notification receipts and their outbox."""
from datetime import datetime, timedelta, timezone

from digital_twin.modules.ai_orchestration.domain.planning import identity
from digital_twin.modules.ai_orchestration.domain.diagnostic_progress import VERSION, progress_state, progress_fingerprint
from digital_twin.modules.ai_orchestration.infrastructure.mysql_diagnostic_progress import decode
from digital_twin.modules.notifications.application.ai_observation_diagnostic import render_diagnostic_progress
from digital_twin.modules.notifications.domain.notifications import NotificationJob, notification_debug_number
from digital_twin.modules.notifications.domain.delivery_suppression import NotificationDeliverySuppressed


class AIDiagnosticProgress:
    def __init__(self, control, notifications):
        self.control, self.notifications = control, notifications

    @staticmethod
    def receipt(connection, job_id, account, task_id, kind):
        rows = connection.execute("SELECT completed_at,metadata_json FROM notification_delivery_attempts WHERE job_id=%s "
            "AND status='delivered' ORDER BY completed_at DESC LIMIT 4", (job_id,)).fetchall()
        for row in rows:
            metadata = decode(row["metadata_json"])
            source = metadata.get("aiObservationDiagnostic" if kind == "aiObservationDiagnostic" else "aiControlObservation") or {}
            checkpoint = metadata.get("deliveryCheckpoint") or {}
            if (metadata.get("accountId") == account and metadata.get("messageType") == kind
                    and source.get("taskId") == task_id and metadata.get("receiptVerified") is True
                    and metadata.get("destinationVerified") is True and checkpoint.get("messageIds")
                    and checkpoint.get("destinationFingerprint")):
                return {"jobId": job_id, "taskId": task_id, "deliveredAt": row["completed_at"]}
        return None

    def adopt_recent(self, connection):
        # One recent original per scope. Its successors must still have exact
        # deterministic parent/child identities; ticker similarity is not lineage.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat().replace("+00:00", "Z")
        rows = connection.execute("SELECT job_id,account_id,symbol,payload_json FROM notification_jobs "
            "WHERE message_type='aiObservationDiagnostic' AND status='done' AND created_at>=%s "
            "ORDER BY created_at DESC LIMIT 30", (cutoff,)).fetchall()
        for row in rows:
            context = (decode(row["payload_json"]).get("context") or {})
            diagnostic = context.get("aiObservationDiagnostic") or {}
            if not diagnostic or context.get("aiDiagnosticProgress"):
                continue
            task = connection.execute("SELECT payload_json,result_json FROM ai_control_tasks WHERE task_id=%s AND account_id=%s AND symbol=%s",
                (diagnostic.get("taskId", ""), row["account_id"], row["symbol"])).fetchone()
            if not task:
                continue
            source, result = decode(task["payload_json"]), decode(task["result_json"])
            exists = connection.execute("SELECT root_job_id FROM ai_observation_diagnostic_followups "
                "WHERE account_id=%s AND symbol=%s AND world_id=%s LIMIT 1",
                (row["account_id"], row["symbol"], source.get("worldId", ""))).fetchone()
            if exists or not self.receipt(connection, row["job_id"], row["account_id"], diagnostic["taskId"], "aiObservationDiagnostic"):
                continue
            self.control.register_diagnostic_followup(connection, row["job_id"], source, result)

    def run_once(self):
        with self.control.transaction() as connection:
            self.adopt_recent(connection)
        with self.control.connect() as connection:
            rows = connection.execute("SELECT root_job_id FROM ai_observation_diagnostic_followups "
                "WHERE closed=0 ORDER BY updated_at,root_job_id LIMIT 20").fetchall()
        emitted = 0
        for row in rows:
            with self.control.transaction() as connection:
                scope = connection.execute("SELECT * FROM ai_observation_diagnostic_followups WHERE root_job_id=%s FOR UPDATE", (row["root_job_id"],)).fetchone()
                if scope["closed"]:
                    continue
                state = decode(scope["state_json"])
                if not self.receipt(connection, scope["root_job_id"], scope["account_id"], scope["root_task_id"], "aiObservationDiagnostic"):
                    self.control.save_diagnostic_progress(connection, scope, state, scope["cursor_task_id"])
                    continue
                task = self.control.diagnostic_chain(connection, scope)
                if not task:
                    # Retention or an ownership mismatch is not successful repair.
                    self.control.save_diagnostic_progress(connection, scope, {**state, "closed": True, "closureReason": "lineage-unavailable"}, scope["cursor_task_id"])
                    continue
                candidates = {**state.get("deliveries", {}), **task.get("deliveries", {})}
                delivered, failed = None, None
                for job_id, task_id in candidates.items():
                    delivered = self.receipt(connection, job_id, scope["account_id"], task_id, "aiObservation")
                    if delivered:
                        break
                    job = connection.execute("SELECT status FROM notification_jobs WHERE job_id=%s AND account_id=%s", (job_id, scope["account_id"])).fetchone()
                    if (job and job["status"] in {"failed", "suppressed", "cancelled"}
                            and (task_id == task["taskId"] or job_id not in state.get("reportedDeliveryFailures", []))):
                        failed = {**job, "jobId": job_id}
                update = progress_state(task, failed, delivered)
                state["deliveries"] = candidates
                if task["taskId"] == scope["root_task_id"] and not delivered:
                    update = None  # The original already describes this attempt.
                event = None
                if update:
                    fingerprint = progress_fingerprint(update)
                    duplicate = fingerprint == state.get("fingerprint") or (update["stage"] == "reviewing" and state.get("reviewNotified"))
                    if not duplicate:
                        revision = int(state.get("revision", 0)) + 1
                        event_id = identity("ai-diagnostic-progress", scope["root_job_id"], revision)
                        event = {**update, "version": VERSION, "eventId": event_id,
                            "rootJobId": scope["root_job_id"], "rootTaskId": scope["root_task_id"],
                            "accountId": scope["account_id"], "symbol": scope["symbol"], "worldId": scope["world_id"], "name": state["name"]}
                        body = render_diagnostic_progress(event, debug_number=notification_debug_number(event_id))
                        job = NotificationJob(job_id=event_id, account_id=scope["account_id"], account_label="",
                            message_type="aiObservationDiagnostic", text=body, dedupe_key=event_id,
                            context={"messageType": "aiObservationDiagnostic", "accountId": scope["account_id"], "symbol": scope["symbol"],
                                     "aiDiagnosticProgress": event, "notificationContent": {"kind": "report", "body": body}})
                        if not self.notifications.enqueue_with_connection(connection, job):
                            raise RuntimeError("진단 후속 안내를 발송 대기열에 기록하지 못했습니다.")
                        state.update(revision=revision, closed=bool(update.get("terminal")))
                        if update["stage"] == "reviewing":
                            state["reviewNotified"] = True
                        else:
                            state["fingerprint"] = fingerprint
                        if update["stage"] == "delivery-blocked":
                            state["reportedDeliveryFailures"] = sorted(set(state.get("reportedDeliveryFailures", [])) | {update["deliveryJobId"]})
                        emitted += 1
                self.control.save_diagnostic_progress(connection, scope, state, task["taskId"], event)
        return {"status": "ok", "checked": len(rows), "queued": emitted}

    def check_delivery(self, job, message):
        event = job.context.get("aiDiagnosticProgress") or {}
        with self.control.connect() as connection:
            row = connection.execute("SELECT payload_json FROM ai_observation_diagnostic_events WHERE event_id=%s", (job.job_id,)).fetchone()
            delivered = connection.execute("SELECT job_id FROM notification_delivery_attempts WHERE job_id=%s AND status='delivered' LIMIT 1", (job.job_id,)).fetchone()
        if (not row or decode(row["payload_json"]) != event or event.get("version") != VERSION
                or event.get("accountId") != job.account_id or event.get("symbol") != job.context.get("symbol") or delivered):
            raise NotificationDeliverySuppressed("원본 진단에 연결된 후속 처리 기록을 확인할 수 없거나 이미 전송한 안내입니다.")
        expected = render_diagnostic_progress(event, debug_number=notification_debug_number(job.job_id))
        if job.context.get("notificationDisplayTimezone") == "Asia/Seoul":
            from digital_twin.modules.notifications.domain.display_time import notification_times_kst
            expected = notification_times_kst(expected)
        if message != expected or ((job.context.get("transportDelivery") or {}).get("message", message) != expected):
            raise NotificationDeliverySuppressed("후속 안내 본문이 저장된 처리 기록과 다릅니다.")
