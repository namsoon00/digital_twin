"""Notification producer adapters backed by the durable job queue."""

from typing import Dict, Iterable

from ...application.notification.intake import NotificationIngressService
from ...domain.accounts import AccountConfig
from ...domain.data_freshness import data_freshness_required, freshness_record
from ...domain.events import DomainEvent
from ...domain.message_types import PORTFOLIO_HOLDINGS_SNAPSHOT
from ...domain.notification_ai import enrich_notification_ai_context
from ...domain.notification_templates import text_context
from ...domain.portfolio import AlertEvent
from ..settings import runtime_settings, utc_now
from .transport import NotificationResult


def notification_queue():
    from ..operational_store import notification_job_store

    return notification_job_store()


def notification_templates():
    from ..operational_store import notification_template_store

    return notification_template_store()


def account_delivery_context(account: AccountConfig = None) -> Dict[str, object]:
    if account and hasattr(account, "message_delivery_context"):
        return account.message_delivery_context()
    return {}


class QueueingNotifier:
    label = "Notification Queue"

    def __init__(
        self,
        account: AccountConfig = None,
        message_type: str = "notification",
        queue=None,
        source_event: DomainEvent = None,
        dedupe_key: str = "",
    ):
        self.account = account
        self.message_type = message_type
        self.queue = queue or notification_queue()
        self.source_event = source_event
        self.dedupe_key = dedupe_key

    def send(self, text: str) -> NotificationResult:
        context = text_context(
            text,
            self.message_type,
            self.account.account_id if self.account else "",
            self.account.label if self.account else "",
        )
        context.update(account_delivery_context(self.account))
        if self.message_type == PORTFOLIO_HOLDINGS_SNAPSHOT and data_freshness_required(self.message_type):
            context.setdefault("dataFreshnessRequired", True)
            context.setdefault(
                "dataFreshness",
                freshness_record(
                    "manualPortfolioSnapshot",
                    self.message_type,
                    settings=runtime_settings(),
                    source_fetched_at=context.get("eventGeneratedAt") or context.get("referenceDate") or utc_now(),
                    data_quality="manual",
                ),
            )
        ingress = NotificationIngressService(settings=runtime_settings())
        request = ingress.request_from_text(
            text,
            account_id=self.account.account_id if self.account else "",
            account_label=self.account.label if self.account else "",
            message_type=self.message_type,
            source_event=self.source_event,
            dedupe_key=self.dedupe_key,
            context=context,
        )
        job = ingress.job_from_request(request)
        if not job.text:
            return NotificationResult(False, self.label, "empty notification text")
        if not self.queue.enqueue(job):
            return NotificationResult(False, self.label, job.last_error or "notification queue enqueue failed")
        return NotificationResult(True, self.label, "queued=1", queued=1)


def queued_notifier_for_account(
    account: AccountConfig = None,
    message_type: str = "notification",
    queue=None,
    source_event: DomainEvent = None,
    dedupe_key: str = "",
):
    return QueueingNotifier(
        account,
        message_type=message_type,
        queue=queue,
        source_event=source_event,
        dedupe_key=dedupe_key,
    )


def enqueue_text(
    text: str,
    account: AccountConfig = None,
    message_type: str = "notification",
    dry_run: bool = False,
    queue=None,
    source_event: DomainEvent = None,
    dedupe_key: str = "",
) -> NotificationResult:
    if dry_run:
        print(text)
        return NotificationResult(False, "Dry Run", "dry-run")
    return queued_notifier_for_account(
        account,
        message_type=message_type,
        queue=queue,
        source_event=source_event,
        dedupe_key=dedupe_key,
    ).send(text)


def send_events(
    events: Iterable[AlertEvent],
    dry_run: bool = False,
    accounts: Dict[str, AccountConfig] = None,
    queue=None,
    source_event: DomainEvent = None,
) -> NotificationResult:
    events = list(events)
    templates = notification_templates()
    settings = runtime_settings()
    ingress = NotificationIngressService(
        template_renderer=templates.render,
        settings=settings,
        context_enricher=lambda context: enrich_notification_ai_context(context, settings),
    )
    requests = [
        ingress.request_from_alert(
            event,
            source_event=source_event,
            account_context=account_delivery_context(
                accounts.get(event.account_id) if accounts else None
            ),
        )
        for event in events
    ]
    messages = [request.source_text for request in requests]
    if dry_run:
        print("\n\n".join(messages) if messages else "No messages.")
        return NotificationResult(False, "Dry Run", "dry-run")
    target_queue = queue or notification_queue()
    queued = 0
    for request in requests:
        if target_queue.enqueue(ingress.job_from_request(request)):
            queued += 1
    return NotificationResult(True, "Notification Queue", "queued=" + str(queued), queued=queued)
