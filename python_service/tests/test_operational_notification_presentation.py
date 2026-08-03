import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.message_types import (  # noqa: E402
    EXTERNAL_DATA_CONNECTION,
    MONITOR_CONNECTION,
    ONTOLOGY_REASONING_QUEUE,
    WORK_HANDOFF,
)
from digital_twin.domain.notification_templates import (  # noqa: E402
    NotificationTemplate,
    render_notification,
    text_context,
)
from digital_twin.domain.notification_title_rules import notification_title_icon  # noqa: E402
from digital_twin.domain.operational_notification_presentation import (  # noqa: E402
    operational_notification_presentation,
)
from digital_twin.domain.portfolio import AlertEvent  # noqa: E402
from digital_twin.infrastructure.cli import build_handoff_message  # noqa: E402
from digital_twin.infrastructure.operational_error_reporting import OperationalErrorReporter  # noqa: E402


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return SimpleNamespace(delivered=True, reason="")


class OperationalNotificationPresentationTests(unittest.TestCase):
    def test_pipeline_authentication_failure_uses_security_icon(self):
        presentation = operational_notification_presentation(EXTERNAL_DATA_CONNECTION, {
            "messageType": EXTERNAL_DATA_CONNECTION,
            "apiStatus": "failed",
            "pipelineHealth": {
                "state": "failed",
                "previousState": "healthy",
                "reason": "Toss accounts failed: HTTP Error 401: Unauthorized",
            },
        })

        self.assertEqual("🔐", presentation.icon)
        self.assertEqual("authentication", presentation.tone)

    def test_pipeline_recovery_uses_success_icon(self):
        presentation = operational_notification_presentation(EXTERNAL_DATA_CONNECTION, {
            "messageType": EXTERNAL_DATA_CONNECTION,
            "apiStatus": "healthy",
            "pipelineHealth": {
                "state": "healthy",
                "previousState": "failed",
            },
        })

        self.assertEqual("✅", presentation.icon)
        self.assertEqual("recovered", presentation.tone)

    def test_queue_delay_and_critical_states_have_distinct_icons(self):
        delayed = operational_notification_presentation(ONTOLOGY_REASONING_QUEUE, {
            "messageType": ONTOLOGY_REASONING_QUEUE,
            "queueDelayHealth": {"state": "delayed", "reasonCode": "oldest-request-delayed"},
        })
        critical = operational_notification_presentation(ONTOLOGY_REASONING_QUEUE, {
            "messageType": ONTOLOGY_REASONING_QUEUE,
            "queueDelayHealth": {"state": "critical", "reasonCode": "queue-blocked"},
        })
        draining = operational_notification_presentation(ONTOLOGY_REASONING_QUEUE, {
            "messageType": ONTOLOGY_REASONING_QUEUE,
            "queueDelayHealth": {"state": "draining", "reasonCode": "fairness-drain-progress"},
        })
        recovered = operational_notification_presentation(ONTOLOGY_REASONING_QUEUE, {
            "messageType": ONTOLOGY_REASONING_QUEUE,
            "queueDelayHealth": {
                "state": "healthy",
                "previousState": "critical",
                "alertKind": "recovered",
            },
        })

        self.assertEqual("⏳", delayed.icon)
        self.assertEqual("🚨", critical.icon)
        self.assertEqual("🔄", draining.icon)
        self.assertEqual("draining", draining.tone)
        self.assertEqual("✅", recovered.icon)

    def test_connection_recovery_uses_current_state_instead_of_previous_error(self):
        raw_lines = ["이전 토스 조회 실패 · HTTP Error 401: Unauthorized", "현재 토스 계좌 동기화"]
        presentation = operational_notification_presentation(MONITOR_CONNECTION, {
            "messageType": MONITOR_CONNECTION,
            "rawLines": "\n".join(raw_lines),
        })
        event = AlertEvent("account-1", "운영", "WATCH", MONITOR_CONNECTION, "connection-recovered", "연결 상태", raw_lines)

        self.assertEqual("✅", presentation.icon)
        self.assertEqual("recovered", presentation.tone)
        self.assertEqual("✅", notification_title_icon(MONITOR_CONNECTION, event.lines, event))

    def test_non_operations_message_has_no_operational_presentation(self):
        presentation = operational_notification_presentation("investmentInsight", {
            "messageType": "investmentInsight",
        })

        self.assertIsNone(presentation)

    def test_rendered_operations_badge_uses_presentation_icon(self):
        context = {
            "messageType": EXTERNAL_DATA_CONNECTION,
            "displayTarget": "시장 데이터 수집",
            "apiStatus": "failed",
            "pipelineHealth": {
                "state": "failed",
                "reason": "HTTP Error 403: Forbidden",
            },
            "telegramMessage": "[운영] 시장 데이터 수집 품질 점검 필요",
            "readableMessage": "[운영] 시장 데이터 수집 품질 점검 필요",
            "body": "[운영] 시장 데이터 수집 품질 점검 필요",
        }

        rendered = render_notification(NotificationTemplate.default(EXTERNAL_DATA_CONNECTION), context)

        self.assertTrue(rendered.startswith("<b>🔐 운영 알림 · 시장 데이터 수집</b>"))
        self.assertIn("[운영] 시장 데이터 수집 품질 점검 필요", rendered)

    def test_operational_alert_title_uses_same_authentication_icon(self):
        event = AlertEvent(
            "account-1",
            "운영",
            "ALERT",
            MONITOR_CONNECTION,
            "connection-auth",
            "연결 상태",
            ["상태 연속 인증 실패", "HTTP Error 401: Unauthorized"],
        )

        icon = notification_title_icon(MONITOR_CONNECTION, event.lines, event)

        self.assertEqual("🔐", icon)

    def test_work_handoff_uses_package_badge(self):
        message = build_handoff_message("운영 아이콘 적용", commit="abc1234", validation="통과", push="성공")

        rendered = render_notification(NotificationTemplate.default(WORK_HANDOFF), text_context(message, WORK_HANDOFF))

        self.assertTrue(rendered.startswith("📦 운영 알림 · 작업완료"))
        self.assertIn("작업 완료", rendered)

    def test_direct_http_authentication_error_uses_security_icon(self):
        notifier = FakeNotifier()
        reporter = OperationalErrorReporter(
            notifier_factory=lambda: notifier,
            event_publisher=lambda _event: None,
            cooldown_seconds=0,
        )
        error = HTTPError("https://example.test", 401, "Unauthorized", hdrs=None, fp=None)

        result = reporter.report("Python service process", error, "external API")

        self.assertTrue(result["sent"])
        self.assertTrue(notifier.messages[0].startswith("🔐 시스템 오류"))


if __name__ == "__main__":
    unittest.main()
