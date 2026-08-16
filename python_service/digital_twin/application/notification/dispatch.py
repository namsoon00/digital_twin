"""Channel selection and delivery isolated from workflow orchestration."""

from typing import Callable, Dict

from ...domain.message_types import ONTOLOGY_REASONING_QUEUE, is_operations_delivery_message_type
from ...domain.notifications import NotificationJob


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
        if operations_delivery:
            if str(job.message_type or "") == ONTOLOGY_REASONING_QUEUE and not self.operations_notifier_factory:
                raise RuntimeError("운영 알림 전송기가 구성되지 않아 계정 채널로 대체 발송하지 않았습니다.")
            factory = self.operations_notifier_factory or self.notifier_factory
        else:
            factory = self.notifier_factory
        audience = "operations" if operations_delivery else "account"
        channel = "operationsTelegram" if operations_delivery else "accountNotification"
        context = dict(job.context or {})
        context["deliveryAudience"] = audience
        context["deliveryChannel"] = channel
        job.context = context
        attempt_id = ""
        if hasattr(self.queue, "start_delivery_attempt"):
            attempt_id = self.queue.start_delivery_attempt(
                job,
                channel,
                audience,
                {"messageBytes": len(str(message or "").encode("utf-8"))},
            )
        notifier = factory(accounts.get(job.account_id))
        try:
            delivery = notifier.send(message)
        except Exception as error:
            if attempt_id and hasattr(self.queue, "complete_delivery_attempt"):
                self.queue.complete_delivery_attempt(job, attempt_id, False, reason=str(error))
            raise
        provider = str(getattr(delivery, "label", "") or "")
        reason = str(getattr(delivery, "reason", "") or "")
        context = dict(job.context or {})
        context["deliveryProvider"] = provider
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
            )
        if not delivered:
            raise RuntimeError(reason or "notification delivery failed")
