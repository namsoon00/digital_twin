"""Version-neutral ingress from producers into notification requests."""

from typing import Dict, Mapping

from digital_twin.domain.events import DomainEvent
from digital_twin.domain.investment_strategy_guidance import merge_strategy_context
from digital_twin.modules.notifications.domain.notification.request import NOTIFICATION_REQUEST_CONTRACT_VERSION, NotificationRequest, NotificationSourceTrace
from digital_twin.modules.notifications.domain.notification.presentation import presentation_metadata
from digital_twin.domain.notification_templates import alert_context, text_context
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.portfolio import AlertEvent
from digital_twin.modules.notifications.application.notification.presentation import content_body


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
        kind: str = "",
        subject: Mapping[str, object] = None,
        content: Mapping[str, object] = None,
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
            kind=kind,
            subject=dict(subject or {}),
            content=dict(content or {}),
        )

    @staticmethod
    def context_with_contract(
        context: Mapping[str, object],
        trace: NotificationSourceTrace,
    ) -> Dict[str, object]:
        values = dict(context or {})
        values["notificationSourceTrace"] = trace.to_dict()
        values["notificationRequestContractVersion"] = NOTIFICATION_REQUEST_CONTRACT_VERSION
        return values

    @staticmethod
    def job_from_request(request: NotificationRequest) -> NotificationJob:
        context = NotificationIngressService.context_with_contract(request.context, request.trace)
        if request.account_id:
            context["accountId"] = request.account_id
            context["accountLabel"] = request.account_label
        content = dict(request.content or {})
        if request.kind:
            content["kind"] = request.kind
        if request.subject:
            content["subject"] = dict(request.subject)
            if request.subject.get("symbol"):
                context.setdefault("rawSymbol", str(request.subject["symbol"]))
                context.setdefault("symbol", str(request.subject["symbol"]))
            if request.subject.get("market"):
                context.setdefault("market", str(request.subject["market"]))
        if content:
            if request.source_text and not content.get("body"):
                content["body"] = request.source_text
            context["notificationContent"] = content
        if request.extensions:
            context["notificationRequestExtensions"] = dict(request.extensions)
        job = NotificationJob.create(
            request.source_text or content_body(content),
            account_id=request.account_id,
            account_label=request.account_label,
            message_type=request.message_type,
            source_event_id=request.trace.source_event_id,
            source_event_name=request.trace.source_event_name,
            dedupe_key=request.dedupe_key,
            context=context,
        )
        job.context["notificationRequestId"] = request.request_id or job.job_id
        NotificationIngressService.prepare_job(job)
        return job

    @staticmethod
    def prepare_job(job: NotificationJob) -> None:
        """Bridge legacy producers without resetting dedupe or cooldown identities."""

        context = dict(job.context or {})
        context.setdefault("notificationRequestContractVersion", NOTIFICATION_REQUEST_CONTRACT_VERSION)
        context.setdefault("notificationRequestId", job.job_id)
        context.setdefault("notificationSourceTrace", NotificationSourceTrace.from_context(
            context, source_event_id=job.source_event_id, source_event_name=job.source_event_name,
        ).to_dict())
        context["notificationPresentation"] = presentation_metadata(job.message_type, context)
        job.context = context
        if not job.text.strip():
            job.text = content_body(context.get("notificationContent"))

    def job_from_alert(
        self,
        event: AlertEvent,
        source_event: DomainEvent = None,
        account_context: Mapping[str, object] = None,
    ) -> NotificationJob:
        return self.job_from_request(
            self.request_from_alert(event, source_event, account_context)
        )
