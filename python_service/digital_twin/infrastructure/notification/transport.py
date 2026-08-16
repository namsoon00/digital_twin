"""Console and Telegram channel transports."""

import json
import re
import urllib.error
import urllib.request
from html import unescape
from typing import Dict, Iterable

from ...domain.accounts import AccountConfig
from ..external_signal_utils import guarded_external_call, root_api_error
from ..settings import runtime_settings


TELEGRAM_HTML_PATTERN = re.compile(r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|a|blockquote)(?:\s+[^>]*)?>", re.IGNORECASE)
TELEGRAM_LINK_PATTERN = re.compile(
    r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
TELEGRAM_MESSAGE_LIMIT = 3900
TELEGRAM_API_GUARD_STATE: Dict[str, object] = {}


def uses_telegram_html(text: str) -> bool:
    return bool(TELEGRAM_HTML_PATTERN.search(str(text or "")))


def telegram_plain_text(text: str) -> str:
    with_urls = TELEGRAM_LINK_PATTERN.sub(
        lambda match: (
            TELEGRAM_HTML_PATTERN.sub("", match.group(2)).strip()
            + (": " if TELEGRAM_HTML_PATTERN.sub("", match.group(2)).strip() else "")
            + match.group(1)
        ),
        str(text or ""),
    )
    return unescape(TELEGRAM_HTML_PATTERN.sub("", with_urls))


def telegram_message_chunks(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> Iterable[str]:
    remaining = str(text or "").strip()
    if not remaining:
        return []
    chunks = []
    max_length = max(500, int(limit or TELEGRAM_MESSAGE_LIMIT))
    while len(remaining) > max_length:
        split_at = remaining.rfind("\n", 0, max_length)
        if split_at < max_length // 2:
            split_at = remaining.rfind(" ", 0, max_length)
        if split_at < max_length // 2:
            split_at = max_length
        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


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

    def post_message(self, payload: Dict[str, object]) -> NotificationResult:
        body = json.dumps({"disable_web_page_preview": True, **payload}).encode("utf-8")
        request = urllib.request.Request(
            "https://api.telegram.org/bot" + self.bot_token + "/sendMessage",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            def send_request():
                with urllib.request.urlopen(request, timeout=12) as response:
                    return json.loads(response.read().decode("utf-8") or "{}")

            response_payload = guarded_external_call(
                runtime_settings(),
                "Telegram",
                "sendMessage",
                send_request,
                state=TELEGRAM_API_GUARD_STATE,
                rate_limit_seconds=0,
            )
            if isinstance(response_payload, dict) and response_payload.get("ok") is False:
                return NotificationResult(False, self.label, str(response_payload.get("description") or "발송 실패"))
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                response_payload = json.loads(error.read().decode("utf-8", "replace") or "{}")
                detail = str(response_payload.get("description") or "")
            except (ValueError, OSError):
                detail = ""
            reason = str(error) + ((" · " + detail) if detail else "")
            return NotificationResult(False, self.label, reason)
        except (urllib.error.URLError, ValueError) as error:
            return NotificationResult(False, self.label, str(error))
        except RuntimeError as error:
            original = root_api_error(error)
            if isinstance(original, urllib.error.HTTPError):
                detail = ""
                try:
                    response_payload = json.loads(original.read().decode("utf-8", "replace") or "{}")
                    detail = str(response_payload.get("description") or "")
                except (ValueError, OSError):
                    detail = ""
                reason = str(error) + ((" · " + detail) if detail else "")
                return NotificationResult(False, self.label, reason)
            return NotificationResult(False, self.label, str(error))
        return NotificationResult(True, self.label)

    def send(self, text: str) -> NotificationResult:
        if not self.bot_token or not self.chat_id:
            return NotificationResult(False, self.label, "텔레그램 토큰 또는 chat id 미설정")
        if len(str(text or "")) > TELEGRAM_MESSAGE_LIMIT:
            chunks = list(telegram_message_chunks(telegram_plain_text(text)))
            total = len(chunks)
            for index, chunk in enumerate(chunks, start=1):
                label = ("(" + str(index) + "/" + str(total) + ")\n") if total > 1 else ""
                result = self.post_message({"chat_id": self.chat_id, "text": label + chunk})
                if not result.delivered:
                    return result
            return NotificationResult(True, self.label)
        payload: Dict[str, object] = {"chat_id": self.chat_id, "text": text}
        if uses_telegram_html(text):
            payload["parse_mode"] = "HTML"
        result = self.post_message(payload)
        if not result.delivered and payload.get("parse_mode") == "HTML":
            fallback = dict(payload)
            fallback.pop("parse_mode", None)
            fallback["text"] = telegram_plain_text(text)
            return self.post_message(fallback)
        return result


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


def notifier_for_operations(account: AccountConfig = None):
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
