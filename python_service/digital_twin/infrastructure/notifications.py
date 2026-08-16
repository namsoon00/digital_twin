"""Compatibility facade for the notification infrastructure package."""

from .notification.ingress import (
    QueueingNotifier,
    account_delivery_context,
    enqueue_text,
    notification_queue,
    notification_templates,
    queued_notifier_for_account,
    send_events,
)
from .notification.transport import (
    TELEGRAM_API_GUARD_STATE,
    TELEGRAM_HTML_PATTERN,
    TELEGRAM_LINK_PATTERN,
    TELEGRAM_MESSAGE_LIMIT,
    ConsoleNotifier,
    NotificationResult,
    TelegramNotifier,
    telegram_message_chunks,
    telegram_plain_text,
    uses_telegram_html,
)
from .settings import runtime_settings


def notifier_from_settings():
    settings = runtime_settings()
    provider = str(settings.get("notifyProvider") or "").strip().lower()
    if provider == "telegram" or (not provider and settings.get("telegramBotToken") and settings.get("telegramChatId")):
        return TelegramNotifier(str(settings.get("telegramBotToken") or ""), str(settings.get("telegramChatId") or ""))
    return ConsoleNotifier()


def notifier_for_account(account=None):
    if not account:
        return notifier_from_settings()
    provider = str(account.notify_provider or "").strip().lower()
    if provider == "telegram" or (not provider and account.telegram_bot_token and account.telegram_chat_id):
        return TelegramNotifier(account.telegram_bot_token, account.telegram_chat_id)
    return notifier_from_settings()


def notifier_for_operations(account=None):
    settings = runtime_settings()
    token = str(settings.get("operationsTelegramBotToken") or settings.get("telegramBotToken") or "").strip()
    chat_id = str(
        settings.get("operationsTelegramChatId")
        or settings.get("telegramChatId")
        or (account.telegram_chat_id if account else "")
        or ""
    ).strip()
    notifier = TelegramNotifier(token, chat_id)
    notifier.label = "Telegram Operations"
    return notifier

__all__ = [
    "TELEGRAM_API_GUARD_STATE",
    "TELEGRAM_HTML_PATTERN",
    "TELEGRAM_LINK_PATTERN",
    "TELEGRAM_MESSAGE_LIMIT",
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
