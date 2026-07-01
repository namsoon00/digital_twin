import json
import urllib.error
import urllib.request
from typing import Dict, Iterable

from ..domain.accounts import AccountConfig
from ..domain.notifications import NotificationJob
from ..domain.portfolio import AlertEvent
from .settings import runtime_settings


class NotificationResult:
    def __init__(self, delivered: bool, label: str, reason: str = "", queued: int = 0):
        self.delivered = delivered
        self.label = label
        self.reason = reason
        self.queued = queued


class ConsoleNotifier:
    label = "Console"

    def send(self, text: str) -> NotificationResult:
        print(text)
        return NotificationResult(False, self.label, "콘솔 전용 모드")


class TelegramNotifier:
    label = "Telegram"

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send(self, text: str) -> NotificationResult:
        if not self.bot_token or not self.chat_id:
            return NotificationResult(False, self.label, "텔레그램 토큰 또는 chat id 미설정")
        body = json.dumps({
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }).encode("utf-8")
        request = urllib.request.Request(
            "https://api.telegram.org/bot" + self.bot_token + "/sendMessage",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
                if payload.get("ok") is False:
                    return NotificationResult(False, self.label, str(payload.get("description") or "발송 실패"))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as error:
            return NotificationResult(False, self.label, str(error))
        return NotificationResult(True, self.label)


def notifier_from_settings():
    settings = runtime_settings()
    provider = str(settings.get("notifyProvider") or "").strip().lower()
    if provider == "telegram" or (not provider and settings.get("telegramBotToken") and settings.get("telegramChatId")):
        return TelegramNotifier(str(settings.get("telegramBotToken") or ""), str(settings.get("telegramChatId") or ""))
    return ConsoleNotifier()


def notifier_for_account(account: AccountConfig = None):
    if not account:
        return notifier_from_settings()
    provider = str(account.notify_provider or "").strip().lower()
    if provider == "telegram" or (not provider and account.telegram_bot_token and account.telegram_chat_id):
        return TelegramNotifier(account.telegram_bot_token, account.telegram_chat_id)
    return notifier_from_settings()


def notification_queue():
    from .sqlite_operational import SQLiteNotificationJobStore

    return SQLiteNotificationJobStore()


class QueueingNotifier:
    label = "Notification Queue"

    def __init__(self, account: AccountConfig = None, message_type: str = "notification", queue=None):
        self.account = account
        self.message_type = message_type
        self.queue = queue or notification_queue()

    def send(self, text: str) -> NotificationResult:
        job = NotificationJob.create(
            text,
            account_id=self.account.account_id if self.account else "",
            account_label=self.account.label if self.account else "",
            message_type=self.message_type,
        )
        if not job.text:
            return NotificationResult(False, self.label, "empty notification text")
        if not self.queue.enqueue(job):
            return NotificationResult(False, self.label, "notification queue enqueue failed")
        return NotificationResult(True, self.label, "queued=1", queued=1)


def queued_notifier_for_account(account: AccountConfig = None, message_type: str = "notification", queue=None):
    return QueueingNotifier(account, message_type=message_type, queue=queue)


def enqueue_text(
    text: str,
    account: AccountConfig = None,
    message_type: str = "notification",
    dry_run: bool = False,
    queue=None,
) -> NotificationResult:
    if dry_run:
        print(text)
        return NotificationResult(False, "Dry Run", "dry-run")
    return queued_notifier_for_account(account, message_type=message_type, queue=queue).send(text)


def send_events(events: Iterable[AlertEvent], dry_run: bool = False, accounts: Dict[str, AccountConfig] = None, queue=None) -> NotificationResult:
    events = list(events)
    messages = [event.message() for event in events]
    if dry_run:
        print("\n\n".join(messages) if messages else "No messages.")
        return NotificationResult(False, "Dry Run", "dry-run")
    target_queue = queue or notification_queue()
    queued = 0
    for event, message in zip(events, messages):
        job = NotificationJob.create(
            message,
            account_id=event.account_id,
            account_label=event.account_label,
            message_type=event.rule or "alert",
        )
        if target_queue.enqueue(job):
            queued += 1
    return NotificationResult(True, "Notification Queue", "queued=" + str(queued), queued=queued)
