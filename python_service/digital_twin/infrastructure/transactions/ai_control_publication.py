"""Atomic central-observation outbox and receipt-backed final delivery gate.

Composition boundary: central tasks and notification receipts have different
owners. Neither business module imports the other's private adapters.
"""
from contextlib import contextmanager
import json

from digital_twin.modules.ai_orchestration.domain.planning import enabled, identity, stamp
from digital_twin.modules.ai_orchestration.domain.publication import MESSAGE_TYPE, RETIRED_REASON, legacy_route_retired, publication_block, repeat_block
from digital_twin.modules.ai_orchestration.domain.insight_quality import quality_block
from digital_twin.modules.ai_orchestration.domain.insight_memory import receipt_facts, restore_legacy_receipt
from digital_twin.modules.ai_orchestration.domain.observation_diagnostic import observation_diagnostic
from digital_twin.modules.reasoning.contracts import quote_clock_assessment
from digital_twin.modules.decisions.domain.investment_narrative_policy import narrative_presentation_errors
from digital_twin.modules.decisions.domain.narrative_numeric_grounding import ungrounded_narrative_numbers
from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation
from digital_twin.modules.notifications.application.ai_observation_diagnostic import render_ai_observation_diagnostic
from digital_twin.modules.notifications.domain.message_types import AI_OBSERVATION_DIAGNOSTIC
from digital_twin.modules.notifications.domain.delivery_suppression import NotificationDeliverySuppressed
from digital_twin.modules.notifications.domain.notifications import NotificationJob, notification_debug_number


def validate_narrative(result):
    if result.get("insightVersion"):
        return quality_block(result)
    fields = [result[key] for key in ("summary", "hypothesis", "counterEvidence", "comparison")]
    fields += [result["notification"]["reason"], *result.get("questions", [])]
    if any(len(text) > 700 for text in fields) or narrative_presentation_errors("NO_ACTION", fields):
        return "문장의 길이 또는 행동 지시 표현을 확인해야 하므로 관찰 기록으로 보관합니다."
    packet = result["input"]
    facts = list(packet.get("facts", []))
    facts += (packet.get("lastDeliveredNotification") or {}).get("facts", [])
    facts += result.get("comparisonFacts", [])
    rows = [{"evidenceId": fact.get("id", ""), "field": key, "value": value}
            for fact in facts for key, value in fact.items()
            if key not in {"id", "symbol", "sourceEntityId", "sourceSnapshotId", "sourceWorldId", "accountId"}
            and isinstance(value, (int, float)) and not isinstance(value, bool)]
    if any(ungrounded_narrative_numbers(text, rows) for text in fields):
        return "해석에 포함된 수치를 현재 근거와 대조하지 못해 관찰 기록으로 보관합니다."
    return ""


class AIControlPublication:
    def __init__(self, settings, control_store, notification_store, subjects):
        self.settings, self.control, self.notifications, self.subjects = settings, control_store, notification_store, subjects

    def retire_legacy_work(self):
        if not legacy_route_retired(self.settings):
            return {"requests": 0, "notifications": 0}
        with self.notifications.transaction() as connection:
            requests = connection.execute(
                "UPDATE ai_inference_requests SET status='superseded',lease_owner='',lease_expires_at='',"
                "last_error=%s,updated_at=%s,completed_at=%s WHERE status IN ('pending','processing','retry')",
                (RETIRED_REASON, stamp(), stamp())).rowcount
            rows = connection.execute(
                "SELECT text,payload_json FROM notification_jobs WHERE message_type='investmentInsight' "
                "AND status IN ('pending','failed','processing','awaiting_ai') FOR UPDATE").fetchall()
            for row in rows:
                job = self.notifications.job_from_row(row)
                job.status, job.last_error, job.updated_at = "suppressed", RETIRED_REASON, stamp()
                job.context["deliverySuppressionReason"] = "legacy-investment-route-retired"
                self.notifications.upsert_job_with_connection(connection, job)
                self.notifications.record_lifecycle_with_connection(connection, job, "suppressed", "suppressed", RETIRED_REASON)
        return {"requests": requests, "notifications": len(rows)}

    def receipts(self, account_id, symbol=""):
        with self.notifications.connect() as connection:
            rows = connection.execute(
                "SELECT job_id,completed_at,metadata_json FROM notification_delivery_attempts "
                "WHERE status='delivered' AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json,'$.messageType'))=%s "
                "AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json,'$.accountId'))=%s "
                "AND (%s='' OR JSON_UNQUOTE(JSON_EXTRACT(metadata_json,'$.aiControlObservation.symbol'))=%s) "
                "ORDER BY completed_at DESC LIMIT 200", (MESSAGE_TYPE, account_id, symbol, symbol)).fetchall()
        result, seen = [], set()
        for row in rows:
            if row["job_id"] in seen:
                continue
            seen.add(row["job_id"])
            snapshot = json.loads(row["metadata_json"]).get("aiControlObservation", {})
            result.append({**snapshot, "deliveredAt": row["completed_at"], "jobId": row["job_id"]})
        return result

    def memory(self, account_id, symbol):
        receipt = next(iter(self.receipts(account_id, symbol)), {})
        if not receipt or receipt.get("insightVersion") or not receipt.get("taskId"):
            return receipt
        with self.control.connect() as connection:
            row = connection.execute("SELECT result_json FROM ai_control_tasks WHERE task_id=%s AND account_id=%s "
                                     "AND symbol=%s AND status='completed'",
                                     (receipt["taskId"], account_id, symbol)).fetchone()
        return restore_legacy_receipt(receipt, json.loads(row["result_json"])) if row else receipt

    def publish(self, connection, task, result):
        reason = publication_block(result) or quality_block(result) or self.review_block(task["taskId"], result)
        if reason:
            publication = {"status": "recorded", "reason": reason}
            diagnostic = observation_diagnostic(result, task["taskId"], reason)
            if diagnostic:
                job_id = identity("ai-control-diagnostic", task["taskId"])[:48]
                body = render_ai_observation_diagnostic(diagnostic, debug_number=notification_debug_number(job_id))
                context = {"messageType": AI_OBSERVATION_DIAGNOSTIC, "accountId": task["accountId"],
                    "symbol": task["symbol"], "name": task["name"], "aiControlTaskId": task["taskId"],
                    "aiObservationDiagnostic": diagnostic, "notificationContent": {"kind": "report", "body": body}}
                job = NotificationJob(job_id=job_id, account_id=task["accountId"], account_label="",
                    message_type=AI_OBSERVATION_DIAGNOSTIC, text=body, context=context, dedupe_key=job_id)
                accepted = self.notifications.enqueue_with_connection(connection, job)
                publication["diagnostic"] = {"status": "queued" if accepted else "suppressed", "jobId": job_id,
                                             "reason": "검증 미통과 초안을 운영 채널로 보냅니다." if accepted else job.last_error}
            return publication
        job_id = identity("ai-control", task["taskId"])[:48]
        body = render_ai_observation(result)
        context = {"accountId": task["accountId"], "symbol": task["symbol"], "name": task["name"],
                   "messageType": MESSAGE_TYPE, "title": task["name"] + " AI 관찰",
                   "aiControlTaskId": task["taskId"], "aiControlObservation": result,
                   "notificationContent": {"kind": "ai-interpretation", "body": body},
                   "notificationWriterProvenance": {"aiAuthored": True, "writerRole": "narrative-only", "source": "ai-control"}}
        job = NotificationJob(job_id=job_id, account_id=task["accountId"], account_label="",
                              message_type=MESSAGE_TYPE, text=body, context=context, dedupe_key=job_id)
        accepted = self.notifications.enqueue_with_connection(connection, job)
        return {"status": "queued" if accepted else "suppressed", "jobId": job_id,
                "reason": "AI가 새 해석을 제안해 발송 검증을 기다립니다." if accepted else job.last_error}

    def review_block(self, task_id, result):
        quality = result.get("quality") or {}
        proof = self.control.review_proof(quality.get("reviewInputId", ""))
        if not isinstance(proof, dict) or proof.get("taskId") != task_id or proof.get("draftHash") != quality.get("draftHash"):
            return "저장된 원문과 일치하는 독립 검토 실행을 확인하지 못했습니다."
        return ""

    @contextmanager
    def delivery_guard(self, job, message):
        if job.message_type == "investmentInsight" and legacy_route_retired(self.settings):
            raise NotificationDeliverySuppressed(RETIRED_REASON)
        if job.message_type == AI_OBSERVATION_DIAGNOSTIC:
            with self.notifications.delivery_subject_lock(job.account_id, "ai-control-diagnostic") as acquired:
                if not acquired:
                    raise RuntimeError("다른 AI 진단 알림이 발송 중이므로 잠시 후 다시 확인합니다.")
                self.check_diagnostic_delivery(job, message)
                yield
            return
        if job.message_type != MESSAGE_TYPE:
            yield
            return
        # Account lock also serializes the daily account quota, across symbols.
        with self.notifications.delivery_subject_lock(job.account_id, "ai-control") as acquired:
            if not acquired:
                raise RuntimeError("다른 AI 관찰 알림이 발송 중이므로 잠시 후 다시 확인합니다.")
            from digital_twin.infrastructure.settings import runtime_settings
            if not enabled(runtime_settings()):
                raise NotificationDeliverySuppressed("중앙 AI 알림이 일시 중지되었습니다.")
            task_id = (job.context or {}).get("aiControlTaskId", "")
            with self.control.connect() as connection:
                row = connection.execute("SELECT status,account_id,symbol,result_json FROM ai_control_tasks WHERE task_id=%s", (task_id,)).fetchone()
            if not row or row["status"] != "completed" or row["account_id"] != job.account_id:
                raise NotificationDeliverySuppressed("완료된 중앙 AI 관찰과 알림의 계정이 일치하지 않습니다.")
            result = json.loads(row["result_json"])
            packet = result.get("input", {})
            if (result.get("publication", {}).get("jobId") != job.job_id
                    or result.get("publication", {}).get("status") != "queued"
                    or packet.get("symbol") != row["symbol"]
                    or (job.context or {}).get("symbol") != row["symbol"]):
                raise NotificationDeliverySuppressed("중앙 AI가 등록한 원본 알림이 아닙니다.")
            if not any(subject["accountId"] == job.account_id and subject["symbol"] == row["symbol"]
                       and subject["worldId"] == packet.get("worldId") for subject in self.subjects()):
                raise NotificationDeliverySuppressed("현재 관찰 대상에서 제외된 계정 또는 종목입니다.")
            expected = render_ai_observation(result, sent_at=job.context.get("aiControlRenderedAt", ""), debug_number=notification_debug_number(job.job_id))
            if message != expected or ((job.context or {}).get("transportDelivery") or {}).get("message", message) != expected:
                raise NotificationDeliverySuppressed("발송 본문이 검증한 중앙 AI 관찰 원본과 다릅니다.")
            receipts = self.receipts(job.account_id)
            last_delivered = self.memory(job.account_id, row["symbol"])
            if last_delivered and not any(receipt["jobId"] == last_delivered["jobId"] for receipt in receipts):
                receipts.append(last_delivered)
            if any(receipt["jobId"] == job.job_id for receipt in receipts):
                raise NotificationDeliverySuppressed("이미 성공적으로 전달한 AI 관찰입니다.")
            reason = publication_block(result) or quality_block(result) or self.review_block(task_id, result) or repeat_block(result, receipts, job.account_id, row["symbol"])
            baseline = (packet.get("lastDeliveredNotification") or {}).get("jobId", "")
            latest = last_delivered.get("jobId", "")
            if latest != baseline:
                reason = "분석 이후 다른 알림이 먼저 전달되어 다음 관찰에서 다시 비교합니다."
            if reason:
                raise NotificationDeliverySuppressed(reason)
            # Receipt stores exactly the facts and explanation actually delivered.
            job.context["aiControlDeliverySnapshot"] = {"accountId": job.account_id, "symbol": row["symbol"],
                "taskId": task_id, "inputFingerprint": result["inputFingerprint"], "observedAt": result["observedAt"],
                **{key: result[key] for key in ("summary", "hypothesis", "counterEvidence", "comparison", "portfolioImpact", "insightVersion", "followUpConditions", "observations")},
                "insightFingerprint": result["quality"]["insightFingerprint"], "facts": receipt_facts(result)}
            if "quoteAssessment" in packet:
                job.context["aiControlDeliverySnapshot"].update(
                    captureQuoteAssessment=packet["quoteAssessment"],
                    deliveryQuoteAssessment=quote_clock_assessment(packet["facts"], stamp()))
            yield

    def check_diagnostic_delivery(self, job, message):
        task_id = (job.context or {}).get("aiControlTaskId", "")
        with self.control.connect() as connection:
            row = connection.execute("SELECT status,account_id,symbol,result_json FROM ai_control_tasks WHERE task_id=%s", (task_id,)).fetchone()
        if not row or row["status"] != "completed" or row["account_id"] != job.account_id:
            raise NotificationDeliverySuppressed("완료된 AI 관찰의 검증 미통과 초안이 아닙니다.")
        result = json.loads(row["result_json"])
        publication = result.get("publication") or {}
        queued = publication.get("diagnostic") or {}
        diagnostic = observation_diagnostic(result, task_id, publication.get("reason", ""))
        if (not diagnostic or publication.get("status") != "recorded" or queued.get("status") != "queued"
                or queued.get("jobId") != job.job_id or job.job_id != identity("ai-control-diagnostic", task_id)[:48]
                or diagnostic["accountId"] != job.account_id or diagnostic["symbol"] != row["symbol"]
                or (job.context or {}).get("aiObservationDiagnostic") != diagnostic):
            raise NotificationDeliverySuppressed("저장된 AI 초안과 진단 알림의 출처가 일치하지 않습니다.")
        expected = render_ai_observation_diagnostic(diagnostic, debug_number=notification_debug_number(job.job_id))
        if message != expected or ((job.context or {}).get("transportDelivery") or {}).get("message", message) != expected:
            raise NotificationDeliverySuppressed("진단 본문이 저장된 AI 초안과 다릅니다.")
        with self.notifications.connect() as connection:
            receipt = connection.execute("SELECT job_id FROM notification_delivery_attempts WHERE job_id=%s AND status='delivered' LIMIT 1", (job.job_id,)).fetchone()
        if receipt:
            raise NotificationDeliverySuppressed("이미 전달한 AI 검증 미통과 초안입니다.")
