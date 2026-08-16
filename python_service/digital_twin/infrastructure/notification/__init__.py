"""Notification infrastructure adapters."""

from .ingress import (
    QueueingNotifier,
    account_delivery_context,
    enqueue_text,
    notification_queue,
    notification_templates,
    queued_notifier_for_account,
    send_events,
)
from .transport import (
    ConsoleNotifier,
    NotificationResult,
    TelegramNotifier,
    notifier_for_account,
    notifier_for_operations,
    notifier_from_settings,
    telegram_message_chunks,
    telegram_plain_text,
    uses_telegram_html,
)

__all__ = [
    "ConsoleNotifier",
    "NotificationResult",
    "QueueingNotifier",
    "TelegramNotifier",
    "account_delivery_context",
    "enqueue_text",
    "notification_queue",
    "notification_templates",
    "notifier_for_account",
    "notifier_for_operations",
    "notifier_from_settings",
    "queued_notifier_for_account",
    "send_events",
    "telegram_message_chunks",
    "telegram_plain_text",
    "uses_telegram_html",
]
