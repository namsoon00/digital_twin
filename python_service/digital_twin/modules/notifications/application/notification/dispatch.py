"""Channel selection and delivery isolated from workflow orchestration."""

from typing import Callable, Dict
from copy import deepcopy
import hashlib

from digital_twin.modules.notifications.domain.message_types import ONTOLOGY_REASONING_QUEUE, is_operations_delivery_message_type
from digital_twin.modules.notifications.domain.notifications import NotificationJob


class NotificationDispatchService:
    def __init__(
        self,
        queue,
        notifier_factory: Callable,
        operations_notifier_factory: Callable = None,
    ):
        self.queue = queue
        self.notifier_factory = notifier_factory
        self.operations_notifier_factory = operations_notifier_factory

    def deliver(self, job: NotificationJob, accounts: Dict[str, object], message: str) -> None:
        operations_delivery = is_operations_delivery_message_type(job.message_type)
        if not operations_delivery and job.account_id and job.account_id not in accounts:
            raise RuntimeError("알림 수신 계정을 찾을 수 없어 다른 계정으로 대체 발송하지 않았습니다.")
        if operations_delivery:
            if str(job.message_type or "") == ONTOLOGY_REASONING_QUEUE and not self.operations_notifier_factory:
                raise RuntimeError("운영 알림 전송기가 구성되지 않아 계정 채널로 대체 발송하지 않았습니다.")
            factory = self.operations_notifier_factory or self.notifier_factory
        else:
            factory = self.notifier_factory
        audience = "operations" if operations_delivery else "account"
        channel = "operationsTelegram" if operations_delivery else "accountNotification"
        context = dict(job.context or {})
        notifier = factory(accounts.get(job.account_id))
        resumable = getattr(notifier, "supports_delivery_checkpoints", False) is True
        persist_progress = getattr(self.queue, "save_delivery_progress", None) or getattr(self.queue, "update", None)
        progress = dict(context.get("transportDelivery") or {})
        if resumable:
            # Freeze the exact artifact before the first external side effect.
            # Later render-time clocks or enrichment cannot change chunk offsets.
            message = str(progress.get("message") or message)
            progress["message"] = message
            if progress.get("relationChangeEvidence"):
                context["relationChangeEvidence"] = deepcopy(progress["relationChangeEvidence"])
            elif context.get("relationChangeEvidence"):
                progress["relationChangeEvidence"] = deepcopy(context["relationChangeEvidence"])
            context["transportDelivery"] = progress
        context.pop("deliveryRetryAfterSeconds", None)
        context["deliveryAudience"] = audience
        context["deliveryChannel"] = channel
        job.context = context
        if resumable:
            persist_progress(job)
        attempt_id = ""
        message_bytes = str(message or "").encode("utf-8")
        rendered_audit = {"messageBytes": len(message_bytes),
            "accountId": job.account_id, "messageType": job.message_type,
            "messageSha256": hashlib.sha256(message_bytes).hexdigest(),
            "renderedMessage": str(message or "") if len(message_bytes) <= 65536 else "",
            "renderedMessageStatus": "complete" if len(message_bytes) <= 65536 else "oversize-hash-only",
            "inferenceGenerationId": context.get("inferenceGenerationId") or "",
            "deliveryBaseline": context.get("investmentInsightDeliveryHistory") or {}}
        relation_change = context.get("relationChangeEvidence") or {}
        if relation_change.get("version") and relation_change.get("current"):
            # The receipt outlives the large job payload. Preserve the exact
            # last-delivered facts for the next customer comparison.
            rendered_audit["relationChangeSnapshot"] = relation_change["current"]
            rendered_audit["relationChangeSubjectKey"] = context.get("deliverySubjectGroupKey") or ""
        if hasattr(self.queue, "start_delivery_attempt"):
            attempt_id = self.queue.start_delivery_attempt(
                job,
                channel,
                audience,
                rendered_audit,
            )
        def save_checkpoint(checkpoint):
            current = dict(job.context or {})
            current["transportDelivery"] = {**progress, "message": message, "checkpoint": checkpoint}
            job.context = current
            persist_progress(job)

        try:
            delivery = (
                notifier.send_resumable(message, checkpoint=progress.get("checkpoint"), on_checkpoint=save_checkpoint)
                if resumable else notifier.send(message)
            )
        except Exception as error:
            if attempt_id and hasattr(self.queue, "complete_delivery_attempt"):
                self.queue.complete_delivery_attempt(job, attempt_id, False, reason=str(error), metadata=rendered_audit)
            raise
        provider = str(getattr(delivery, "label", "") or "")
        reason = str(getattr(delivery, "reason", "") or "")
        receipt_metadata = dict(getattr(delivery, "metadata", {}) or {})
        receipt_metadata.update(rendered_audit)
        context = dict(job.context or {})
        context["deliveryProvider"] = provider
        context["deliveryRetryAfterSeconds"] = receipt_metadata.get("retryAfterSeconds") or 0
        if reason:
            context["deliveryNote"] = reason
        if attempt_id:
            context["deliveryAttemptId"] = attempt_id
        job.context = context
        delivered = bool(getattr(delivery, "delivered", False))
        if attempt_id and hasattr(self.queue, "complete_delivery_attempt"):
            self.queue.complete_delivery_attempt(
                job,
                attempt_id,
                delivered,
                provider=provider,
                reason=reason,
                metadata=receipt_metadata,
            )
        if not delivered:
            raise RuntimeError(reason or "notification delivery failed")
