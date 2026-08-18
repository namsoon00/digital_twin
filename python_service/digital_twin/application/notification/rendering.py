"""Notification document rendering orchestration."""

import hashlib
from datetime import datetime, timezone
from typing import Callable, Dict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from ...domain.notifications import NotificationJob, notification_debug_number


class NotificationRenderingService:
    """Prepare send-time context and render the exact customer artifact."""

    def __init__(
        self,
        template_renderer: Callable = None,
        context_enricher: Callable = None,
        now_provider: Callable = None,
        link_base_resolver: Callable = None,
    ):
        self.template_renderer = template_renderer
        self.context_enricher = context_enricher
        self.now_provider = now_provider or (lambda: datetime.now(ZoneInfo("UTC")))
        self.link_base_resolver = link_base_resolver

    def render(self, job: NotificationJob) -> str:
        self.apply_send_time_context(job)
        if self.context_enricher:
            self.context_enricher(job)
        rendered = (
            str(self.template_renderer(job) or "").strip()
            if self.template_renderer
            else job.text.strip()
        )
        if rendered:
            job.text = rendered
            context = dict(job.context or {})
            context["notificationPresentationAudit"] = {
                "version": "notification-presentation-v2",
                "detailLevel": str(context.get("notificationDetailLevel") or "full"),
                "renderedBytes": len(rendered.encode("utf-8")),
                "renderedSha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                "detailUrl": str(context.get("notificationDetailUrl") or ""),
            }
            job.context = context
        return rendered

    def apply_send_time_context(self, job: NotificationJob) -> None:
        now = self.now_provider()
        if not isinstance(now, datetime):
            now = datetime.now(ZoneInfo("UTC"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo("UTC"))
        sent_at = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        sent_time = now.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
        context = dict(job.context or {})
        base_url = str(context.get("notifyLinkUrl") or "").strip()
        if self.link_base_resolver:
            try:
                base_url = str(self.link_base_resolver(base_url) or base_url).strip()
            except Exception:  # noqa: BLE001 - a link override must not block notification delivery.
                pass
        detail_url = ""
        if base_url:
            parts = urlsplit(base_url)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            query.update({
                "tab": "notifications",
                "notification": "decisions",
                "detail": "notification-job",
                "detailKey": str(job.job_id or ""),
            })
            detail_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        context.update({
            "jobId": job.job_id,
            "notificationNumber": notification_debug_number(job.job_id),
            "sentAt": sent_at,
            "sentTime": sent_time,
            "sentLine": "발송시각 " + sent_time,
            "notificationDetailUrl": detail_url,
        })
        job.context = context

    @staticmethod
    def append_holding_timing_sent_time(context: Dict[str, object], sent_time: str) -> None:
        plain_line = "발송시각 " + sent_time
        rich_line = "• <b>발송시각</b>: <code>" + sent_time + "</code>"
        raw_lines = str(context.get("rawLines") or "")
        if "발송시각" not in raw_lines:
            context["rawLines"] = "\n".join(part for part in [raw_lines, plain_line] if str(part or "").strip())
        telegram_data = str(context.get("telegramDataLines") or "")
        if "발송시각" not in telegram_data:
            context["telegramDataLines"] = "\n".join(part for part in [telegram_data, rich_line] if str(part or "").strip())
        telegram_message = str(context.get("telegramMessage") or "")
        if telegram_message and "발송시각" not in telegram_message:
            marker = "\n\n<b>발송 기준</b>"
            context["telegramMessage"] = (
                telegram_message.replace(marker, "\n" + rich_line + marker, 1)
                if marker in telegram_message
                else telegram_message + "\n" + rich_line
            )
        readable_message = str(context.get("readableMessage") or "")
        if readable_message and "발송시각" not in readable_message:
            plain_bullet = "• 발송시각: " + sent_time
            marker = "\n\n발송 기준"
            context["readableMessage"] = (
                readable_message.replace(marker, "\n" + plain_bullet + marker, 1)
                if marker in readable_message
                else readable_message + "\n" + plain_bullet
            )
