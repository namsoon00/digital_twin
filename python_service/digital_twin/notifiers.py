import json
import urllib.error
import urllib.request
from typing import Dict, Iterable

from .config import AccountConfig, runtime_settings
from .models import AlertEvent


class NotificationResult:
    def __init__(self, delivered: bool, label: str, reason: str = ""):
        self.delivered = delivered
        self.label = label
        self.reason = reason


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


def send_events(events: Iterable[AlertEvent], dry_run: bool = False, accounts: Dict[str, AccountConfig] = None) -> NotificationResult:
    events = list(events)
    messages = [event.message() for event in events]
    if dry_run:
        print("\n\n".join(messages) if messages else "No messages.")
        return NotificationResult(False, "Dry Run", "dry-run")
    account_map = accounts or {}
    result = NotificationResult(True, "Notification")
    for event, message in zip(events, messages):
        notifier = notifier_for_account(account_map.get(event.account_id))
        result = notifier.send(message)
        if not result.delivered:
            if result.reason:
                print(message)
                print("Delivery: console only (" + result.reason + ")")
            return result
    return result
