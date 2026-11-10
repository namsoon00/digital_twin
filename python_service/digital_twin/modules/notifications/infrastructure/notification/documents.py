"""Telegram report upload with durable success/uncertain checkpoints.

sendDocument is non-idempotent: an unknown network outcome is not retried.
The caller persists checkpoints under its notification job lease.
"""
import hashlib
import json
import re
import urllib.error
import urllib.request
import uuid


class TelegramDocuments:
    def post_document(self, document):
        from .transport import NotificationResult, TELEGRAM_API_GUARD_STATE
        from digital_twin.infrastructure.external_signal_utils import guarded_external_call, root_api_error
        from digital_twin.infrastructure.settings import runtime_settings
        boundary = "orbit-" + uuid.uuid4().hex
        raw = document["html"].encode("utf-8")
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{self.chat_id}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{document["filename"]}"\r\n'
                'Content-Type: text/html; charset=utf-8\r\n\r\n').encode() + raw + f'\r\n--{boundary}--\r\n'.encode()
        request = urllib.request.Request("https://api.telegram.org/bot" + self.bot_token + "/sendDocument", data=body,
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary}, method="POST")
        try:
            def upload():
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.loads(response.read())
            payload = guarded_external_call(runtime_settings(), "Telegram", self.guard_target("sendDocument"), upload,
                state=TELEGRAM_API_GUARD_STATE, rate_limit_seconds=0, attempts=1)
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                return self.api_failure(payload if isinstance(payload, dict) else {}, "파일 전송 실패")
            result = payload.get("result") or {}
            file = result.get("document") or {}
            if (str((result.get("chat") or {}).get("id")) != str(self.chat_id)
                    or not result.get("message_id") or not file.get("file_id")
                    or file.get("file_size") != len(raw) or file.get("file_name") != document["filename"]):
                return NotificationResult(False, self.label, "파일 전송 영수증을 확인하지 못했습니다.", metadata={"uncertain": True})
            return NotificationResult(True, self.label, metadata={"receiptVerified": True, "destinationVerified": True,
                "documentMessageId": str(result["message_id"]), "documentSha256": document["sha256"]})
        except urllib.error.HTTPError as error:
            return self.http_failure(error)
        except Exception as error:
            original = root_api_error(error)
            if isinstance(original, urllib.error.HTTPError):
                return self.http_failure(original)
            # Do not surface credential-bearing request URLs or retry an unknown upload.
            return NotificationResult(False, self.label, "파일 전송 결과 미확인 · 중복 방지를 위해 자동 재전송을 멈췄습니다.", metadata={"uncertain": True})

    def send_report_resumable(self, text, document, checkpoint=None, on_checkpoint=None):
        from .transport import NotificationResult
        if (not isinstance(document, dict) or not isinstance(document.get("html"), str)
                or not re.fullmatch(r"orbit-alpha-[0-9a-f]{12}\.html", str(document.get("filename", "")))
                or len(document["html"].encode()) > 2_000_000
                or hashlib.sha256(document["html"].encode()).hexdigest() != document.get("sha256")):
            return NotificationResult(False, self.label, "전문 파일 검증 실패")
        progress = dict(checkpoint or {})
        saved = dict(progress.get("document") or {})
        if saved and (saved.get("sha256") != document["sha256"] or saved.get("destinationFingerprint") != self.destination_fingerprint):
            return NotificationResult(False, self.label, "파일 또는 수신처가 변경되어 재전송을 중단했습니다.")
        if saved.get("state") in {"sending", "uncertain"}:
            return NotificationResult(False, self.label, "이전 파일 전송 결과 미확인 · 수동 확인이 필요합니다.", metadata={"uncertain": True})
        summary = self.send_resumable(text, checkpoint=progress, on_checkpoint=on_checkpoint)
        if not summary.delivered:
            return summary
        progress = dict(summary.metadata["deliveryCheckpoint"])
        if saved.get("state") != "delivered":
            saved = {"sha256": document["sha256"], "destinationFingerprint": self.destination_fingerprint, "state": "sending"}
            progress["document"] = saved
            if not callable(on_checkpoint):
                return NotificationResult(False, self.label, "파일 발송의 진행 기록을 저장할 수 없습니다.")
            on_checkpoint(progress)
            result = self.post_document(document)
            if not result.delivered:
                saved["state"] = "failed" if result.metadata.get("errorCode") else "uncertain"
                on_checkpoint(progress)
                result.metadata["deliveryCheckpoint"] = progress
                return result
            saved.update(state="delivered", messageId=result.metadata["documentMessageId"], receiptVerified=True)
            on_checkpoint(progress)
        summary.metadata.update(deliveryCheckpoint=progress, documentReceiptVerified=saved.get("receiptVerified") is True,
            documentSha256=saved["sha256"], documentMessageId=saved.get("messageId", ""))
        return summary
