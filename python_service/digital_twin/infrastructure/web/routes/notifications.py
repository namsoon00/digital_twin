"""Notifications HTTP routes; order is wired in web.composition."""

from dataclasses import dataclass
from digital_twin.infrastructure.web.adapters.notification_configuration import include_internal_notification_query
from digital_twin.infrastructure.web.adapters.notification_configuration import list_notification_rules_payload
from digital_twin.infrastructure.web.adapters.notification_configuration import list_templates_payload
from digital_twin.infrastructure.web.adapters.notification_configuration import notification_schedules_payload
from digital_twin.infrastructure.web.adapters.notification_configuration import reset_notification_rule_payload
from digital_twin.infrastructure.web.adapters.notification_configuration import reset_template_payload
from digital_twin.infrastructure.web.adapters.notification_configuration import save_notification_rule_payload
from digital_twin.infrastructure.web.adapters.notification_configuration import save_template_payload
from digital_twin.infrastructure.web.adapters.notification_inbox import mark_all_notifications_read_payload
from digital_twin.infrastructure.web.adapters.notification_inbox import notification_job_detail_payload
from digital_twin.infrastructure.web.adapters.notification_inbox import notification_jobs_payload
from digital_twin.infrastructure.web.adapters.notification_inbox import replay_notification_payload
from digital_twin.infrastructure.web.adapters.notification_inbox import update_notification_receipt_payload
from digital_twin.infrastructure.web.adapters.notification_testing import notification_template_test_payload
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.router import NOT_HANDLED
from digital_twin.infrastructure.web.router import Query
from typing import Callable
import re
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class NotificationsRoutes:
    """HTTP translation with explicit replaceable use-case/read-model callbacks."""

    list_notification_rules_payload: Callable[..., object] = list_notification_rules_payload
    list_templates_payload: Callable[..., object] = list_templates_payload
    mark_all_notifications_read_payload: Callable[..., object] = mark_all_notifications_read_payload
    notification_job_detail_payload: Callable[..., object] = notification_job_detail_payload
    notification_jobs_payload: Callable[..., object] = notification_jobs_payload
    notification_schedules_payload: Callable[..., object] = notification_schedules_payload
    notification_template_test_payload: Callable[..., object] = notification_template_test_payload
    replay_notification_payload: Callable[..., object] = replay_notification_payload
    reset_notification_rule_payload: Callable[..., object] = reset_notification_rule_payload
    reset_template_payload: Callable[..., object] = reset_template_payload
    save_notification_rule_payload: Callable[..., object] = save_notification_rule_payload
    save_template_payload: Callable[..., object] = save_template_payload
    update_notification_receipt_payload: Callable[..., object] = update_notification_receipt_payload

    def route_notification_templates(self, request, path: str, query: Query):
        if path == "/api/notification-templates":
            if request.command == "GET":
                return request.send_payload(200, self.list_templates_payload())
            if request.command in {"POST", "PUT"}:
                if not request.ensure_writable("공유 모드에서는 알림 템플릿을 변경할 수 없습니다."):
                    return
                return request.send_payload(200, self.save_template_payload(request.read_json_body()))

        if path == "/api/notification-rules":
            if request.command == "GET":
                return request.send_payload(200, self.list_notification_rules_payload(include_internal_notification_query(query)))
            if request.command in {"POST", "PUT"}:
                if not request.ensure_writable("공유 모드에서는 알림 룰을 변경할 수 없습니다."):
                    return
                return request.send_payload(200, self.save_notification_rule_payload(request.read_json_body()))

        if path == "/api/notification-jobs" and request.command == "GET":
            return request.send_payload(200, self.notification_jobs_payload(query))

        if path == "/api/notification-jobs/read-all" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 알림 확인 상태를 변경할 수 없습니다."):
                return
            return request.send_payload(200, self.mark_all_notifications_read_payload(request.read_json_body()))

        notification_receipt_match = re.match(r"^/api/notification-jobs/([^/]+)/receipt$", path)
        if notification_receipt_match and request.command in {"POST", "PUT", "PATCH"}:
            if not request.ensure_writable("공유 모드에서는 알림 확인 상태를 변경할 수 없습니다."):
                return
            job_id = urllib.parse.unquote(notification_receipt_match.group(1))
            payload = self.update_notification_receipt_payload(job_id, request.read_json_body())
            return request.send_payload(200 if not payload.get("error") else 404, payload)

        notification_section_match = re.match(
            r"^/api/notification-jobs/([^/]+)/(reasoning|ai-review|delivery)$",
            path,
        )
        if notification_section_match and request.command == "GET":
            payload = self.notification_job_detail_payload(
                urllib.parse.unquote(notification_section_match.group(1)),
                first_query(query, "recipientId") or "local-owner",
                section=notification_section_match.group(2),
                include_sensitive=not request.share_access().shared,
            )
            return request.send_payload(200 if payload.get("jobId") else 404, payload or {"error": "알림 작업을 찾지 못했습니다."})

        notification_job_match = re.match(r"^/api/notification-jobs/([^/]+)$", path)
        if notification_job_match and request.command == "GET":
            payload = self.notification_job_detail_payload(
                urllib.parse.unquote(notification_job_match.group(1)),
                first_query(query, "recipientId") or "local-owner",
                include_sensitive=not request.share_access().shared,
            )
            return request.send_payload(200 if payload.get("job") else 404, payload or {"error": "알림 작업을 찾지 못했습니다."})

        if path == "/api/notification-jobs/replay" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 알림을 재발송할 수 없습니다."):
                return
            return request.send_payload(200, self.replay_notification_payload(request.read_json_body()))
        return NOT_HANDLED

    def route_notification_schedules(self, request, path: str, query: Query):
        if path == "/api/notification-schedules" and request.command == "GET":
            return request.send_payload(200, self.notification_schedules_payload(include_internal_notification_query(query)))

        if path == "/api/notification-templates/test-send" and request.command == "POST":
            if not request.ensure_writable("공유 모드에서는 실제 알림을 발송할 수 없습니다."):
                return
            status, payload = self.notification_template_test_payload(request.read_json_body())
            return request.send_payload(status, payload)

        template_match = re.match(r"^/api/notification-templates/([^/]+)$", path)
        if template_match and request.command == "DELETE":
            if not request.ensure_writable("공유 모드에서는 알림 템플릿을 변경할 수 없습니다."):
                return
            return request.send_payload(200, self.reset_template_payload(urllib.parse.unquote(template_match.group(1))))

        rule_match = re.match(r"^/api/notification-rules/([^/]+)$", path)
        if rule_match and request.command == "DELETE":
            if not request.ensure_writable("공유 모드에서는 알림 룰을 변경할 수 없습니다."):
                return
            return request.send_payload(200, self.reset_notification_rule_payload(urllib.parse.unquote(rule_match.group(1))))
        return NOT_HANDLED
