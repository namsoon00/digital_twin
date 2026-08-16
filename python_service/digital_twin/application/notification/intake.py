"""Version-neutral ingress from producers into notification requests."""

from typing import Dict, Mapping

from ...domain.events import DomainEvent
from ...domain.investment_strategy_guidance import append_strategy_block, merge_strategy_context
from ...domain.message_types import INVESTMENT_INSIGHT
from ...domain.notification.request import NotificationRequest, NotificationSourceTrace
from ...domain.notification_templates import alert_context, text_context
from ...domain.notifications import NotificationJob
from ...domain.portfolio import AlertEvent


class NotificationIngressService:
    """Build the stable notification request used by both V1 and V2 outputs."""

    def __init__(
        self,
        template_renderer=None,
        settings: Mapping[str, object] = None,
        context_enricher=None,
    ):
        self.template_renderer = template_renderer
        self.settings = dict(settings or {})
        self.context_enricher = context_enricher

    def request_from_alert(
        self,
        event: AlertEvent,
        source_event: DomainEvent = None,
        account_context: Mapping[str, object] = None,
    ) -> NotificationRequest:
        context = merge_strategy_context(
            alert_context(event),
            account_context,
            self.settings,
        )
        if str(event.rule or "") == INVESTMENT_INSIGHT:
            context = append_strategy_block(context)
        if callable(self.context_enricher):
            context = dict(self.context_enricher(context) or context)
        source_event_id = str(getattr(source_event, "event_id", "") or "")
        source_event_name = str(getattr(source_event, "name", "") or "")
        trace = NotificationSourceTrace.from_context(
            context,
            source_event_id=source_event_id,
            source_event_name=source_event_name,
        )
        context = self.context_with_contract(context, trace)
        message = (
            self.template_renderer(str(event.rule or "alert"), context)
            if callable(self.template_renderer)
            else str(event.title or "").strip()
        )
        request_id = str(getattr(event, "key", "") or "").strip()
        dedupe_key = (
            ":".join(["outbox", source_event_id, request_id])
            if source_event_id and request_id
            else ""
        )
        return NotificationRequest(
            request_id=request_id,
            account_id=str(event.account_id or ""),
            account_label=str(event.account_label or ""),
            message_type=str(event.rule or "alert"),
            source_text=str(message or "").strip(),
            context=context,
            dedupe_key=dedupe_key,
            trace=trace,
        )

    def request_from_text(
        self,
        text: str,
        *,
        account_id: str = "",
        account_label: str = "",
        message_type: str = "notification",
        source_event: DomainEvent = None,
        dedupe_key: str = "",
        context: Mapping[str, object] = None,
        request_id: str = "",
    ) -> NotificationRequest:
        values = dict(context or text_context(text, message_type, account_id, account_label))
        trace = NotificationSourceTrace.from_context(
            values,
            source_event_id=str(getattr(source_event, "event_id", "") or ""),
            source_event_name=str(getattr(source_event, "name", "") or ""),
        )
        return NotificationRequest(
            request_id=str(request_id or ""),
            account_id=str(account_id or ""),
            account_label=str(account_label or ""),
            message_type=str(message_type or "notification"),
            source_text=str(text or "").strip(),
            context=self.context_with_contract(values, trace),
            dedupe_key=str(dedupe_key or ""),
            trace=trace,
        )

    @staticmethod
    def context_with_contract(
        context: Mapping[str, object],
        trace: NotificationSourceTrace,
    ) -> Dict[str, object]:
        values = dict(context or {})
        values["notificationSourceTrace"] = trace.to_dict()
        values["notificationRequestContractVersion"] = "notification-request-v1"
        return values

    @staticmethod
    def job_from_request(request: NotificationRequest) -> NotificationJob:
        return NotificationJob.create(
            request.source_text,
            account_id=request.account_id,
            account_label=request.account_label,
            message_type=request.message_type,
            source_event_id=request.trace.source_event_id,
            source_event_name=request.trace.source_event_name,
            dedupe_key=request.dedupe_key,
            context=dict(request.context or {}),
        )

    def job_from_alert(
        self,
        event: AlertEvent,
        source_event: DomainEvent = None,
        account_context: Mapping[str, object] = None,
    ) -> NotificationJob:
        return self.job_from_request(
            self.request_from_alert(event, source_event, account_context)
        )
