"""Ports owned by the notification bounded context."""

from typing import Iterable, Protocol

from digital_twin.modules.notifications.domain.notification.channel import DeliveryReceipt
from digital_twin.modules.notifications.domain.notification.lifecycle import NotificationLifecycleEvent
from digital_twin.modules.notifications.domain.notification.request import NotificationRequest


class NotificationJobPort(Protocol):
    def enqueue_request(self, request: NotificationRequest) -> bool:
        ...


class NotificationAuditPort(Protocol):
    def record_lifecycle(self, event: NotificationLifecycleEvent) -> None:
        ...

    def lifecycle_for_job(self, job_id: str) -> Iterable[NotificationLifecycleEvent]:
        ...


class NotificationChannel(Protocol):
    def send(self, text: str) -> DeliveryReceipt:
        ...
