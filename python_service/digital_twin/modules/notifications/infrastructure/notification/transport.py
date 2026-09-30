"""Console and Telegram channel transports."""

import hashlib
import json
import re
import urllib.error
import urllib.request
from html import unescape
from typing import Dict, Iterable

from digital_twin.modules.accounts.contracts import AccountConfig
from digital_twin.infrastructure.external_signal_utils import guarded_external_call, root_api_error
from digital_twin.infrastructure.settings import runtime_settings


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
    def __init__(
        self,
        delivered: bool,
        label: str,
        reason: str = "",
        queued: int = 0,
        metadata: Dict[str, object] = None,
    ):
        self.delivered = delivered
        self.label = label
        self.reason = reason
        self.queued = queued
        self.metadata = dict(metadata or {})


class ConsoleNotifier:
    label = "Console"

    def send(self, text: str) -> NotificationResult:
        print(text)
        return NotificationResult(False, self.label, "콘솔 전용 모드")


class TelegramNotifier:
    label = "Telegram"
    supports_delivery_checkpoints = True

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
                # sendMessage has no idempotency key. A hidden HTTP retry can
                # repeat a message accepted before a lost response.
                attempts=1,
            )
            if not isinstance(response_payload, dict) or response_payload.get("ok") is not True:
                description = (
                    response_payload.get("description")
                    if isinstance(response_payload, dict)
                    else ""
                )
                return self.api_failure(response_payload if isinstance(response_payload, dict) else {}, str(description or "발송 실패"))
            receipt = response_payload.get("result")
            receipt = receipt if isinstance(receipt, dict) else {}
            receipt_chat = receipt.get("chat")
            receipt_chat = receipt_chat if isinstance(receipt_chat, dict) else {}
            message_id = receipt.get("message_id")
            returned_chat_id = str(receipt_chat.get("id") or "").strip()
            configured_chat_id = str(self.chat_id or "").strip()
            if message_id in (None, "") or not returned_chat_id:
                return NotificationResult(
                    False,
                    self.label,
                    "Telegram API가 메시지 생성 영수증을 반환하지 않았습니다.",
                )
            if returned_chat_id != configured_chat_id:
                return NotificationResult(
                    False,
                    self.label,
                    "Telegram API 응답의 수신 대상이 설정 계정과 일치하지 않습니다.",
                )
            return NotificationResult(
                True,
                self.label,
                metadata={
                    "receiptVerified": True,
                    "destinationVerified": True,
                    "chatFingerprint": hashlib.sha256(returned_chat_id.encode("utf-8")).hexdigest()[:16],
                    "messageIds": [str(message_id)],
                    "chunkCount": 1,
                },
            )
        except urllib.error.HTTPError as error:
            return self.http_failure(error)
        except (urllib.error.URLError, ValueError) as error:
            return NotificationResult(False, self.label, str(error))
        except RuntimeError as error:
            original = root_api_error(error)
            if isinstance(original, urllib.error.HTTPError):
                return self.http_failure(original)
            return NotificationResult(False, self.label, str(error))
        return NotificationResult(False, self.label, "Telegram 발송 결과를 확인하지 못했습니다.")

    def http_failure(self, error) -> NotificationResult:
        try:
            payload = json.loads(error.read().decode("utf-8", "replace") or "{}")
        except (ValueError, OSError):
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        payload.setdefault("error_code", error.code)
        return self.api_failure(payload, "HTTP " + str(error.code))

    def api_failure(self, payload, reason) -> NotificationResult:
        code = payload.get("error_code")
        description = str(payload.get("description") or "")
        parameters = payload.get("parameters") or {}
        try:
            retry_after = max(0, int(parameters.get("retry_after") or 0))
        except (ValueError, TypeError, AttributeError):
            retry_after = 0
        return NotificationResult(False, self.label,
            ("HTTP " + str(code) + " · " if code else "") + (description or reason),
            metadata={"errorCode": code, "retryAfterSeconds": retry_after,
                      "formatError": code == 400 and any(term in description.lower() for term in ("can't parse entities", "can't find end", "unsupported start tag"))})

    def send(self, text: str) -> NotificationResult:
        return self.send_resumable(text)

    def send_resumable(self, text: str, checkpoint=None, on_checkpoint=None) -> NotificationResult:
        if not self.bot_token or not self.chat_id:
            return NotificationResult(False, self.label, "텔레그램 토큰 또는 chat id 미설정")
        text = str(text or "")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        destination = hashlib.sha256((self.bot_token.partition(":")[0] + ":" + str(self.chat_id)).encode("utf-8")).hexdigest()
        progress = dict(checkpoint or {})
        message_ids = list(progress.get("messageIds") or [])
        if message_ids and (progress.get("messageSha256") != digest or progress.get("destinationFingerprint") != destination):
            return NotificationResult(False, self.label, "부분 전송 이후 본문 또는 수신처가 변경되어 재전송하지 않았습니다.")
        payloads = []
        if len(str(text or "")) > TELEGRAM_MESSAGE_LIMIT:
            chunks = list(telegram_message_chunks(telegram_plain_text(text)))
            total = len(chunks)
            for index, chunk in enumerate(chunks, start=1):
                label = ("(" + str(index) + "/" + str(total) + ")\n") if total > 1 else ""
                payloads.append({"chat_id": self.chat_id, "text": label + chunk})
        else:
            payload = {"chat_id": self.chat_id, "text": text}
            if uses_telegram_html(text):
                payload["parse_mode"] = "HTML"
            payloads.append(payload)
        if len(message_ids) > len(payloads):
            return NotificationResult(False, self.label, "부분 전송 기록의 조각 수가 본문과 일치하지 않습니다.")
        progress.update(messageSha256=digest, destinationFingerprint=destination,
                        messageIds=message_ids, chunkCount=len(payloads))
        receipt_metadata = {"chatFingerprint": hashlib.sha256(str(self.chat_id).encode("utf-8")).hexdigest()[:16]}
        for payload in payloads[len(message_ids):]:
            result = self.post_message(payload)
            if not result.delivered and payload.get("parse_mode") == "HTML" and result.metadata.get("formatError"):
                result = self.post_message({"chat_id": self.chat_id, "text": telegram_plain_text(payload["text"])})
            if not result.delivered:
                result.metadata.update({"deliveryCheckpoint": dict(progress), "messageIds": list(message_ids), "chunkCount": len(payloads)})
                return result
            ids = list(result.metadata.get("messageIds") or [])
            if len(ids) != 1 or not result.metadata.get("receiptVerified") or not result.metadata.get("destinationVerified"):
                return NotificationResult(False, self.label, "전송 조각의 성공 영수증을 검증하지 못했습니다.", metadata={"deliveryCheckpoint": dict(progress)})
            message_ids.extend(ids)
            receipt_metadata.update(result.metadata)
            progress["messageIds"] = list(message_ids)
            if callable(on_checkpoint):
                on_checkpoint(dict(progress))
        receipt_metadata.update(messageIds=message_ids, chunkCount=len(payloads), receiptVerified=True,
                                destinationVerified=True, deliveryCheckpoint=progress)
        return NotificationResult(True, self.label, metadata=receipt_metadata)


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
