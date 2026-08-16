import ast
import unittest
from pathlib import Path

from digital_twin.application.notification.dispatch import NotificationDispatchService
from digital_twin.application.notification.intake import NotificationIngressService
from digital_twin.application.notification.query import NotificationTraceQueryService
from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_COMPLETED
from digital_twin.domain.notification.lifecycle import notification_transition_allowed
from digital_twin.domain.portfolio import AlertEvent
from digital_twin.infrastructure.notification.transport import NotificationResult
from digital_twin.infrastructure.mysql_operational_connection import MYSQL_SCHEMA


ROOT = Path(__file__).resolve().parents[1] / "digital_twin"


class RecordingQueue:
    def __init__(self):
        self.started = []
        self.completed = []

    def start_delivery_attempt(self, job, channel, audience, metadata=None):
        self.started.append((job.job_id, channel, audience, dict(metadata or {})))
        return "attempt:1"

    def complete_delivery_attempt(
        self,
        job,
        attempt_id,
        delivered,
        provider="",
        reason="",
        metadata=None,
    ):
        self.completed.append(
            (job.job_id, attempt_id, delivered, provider, reason, dict(metadata or {}))
        )


class SuccessfulNotifier:
    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)
        return NotificationResult(True, "Telegram")


class TraceStore:
    def lifecycle_for_job(self, job_id):
        return [
            {
                "eventId": "event:2",
                "createdAt": "2026-08-16T00:00:02Z",
                "stage": "rendered",
                "outcome": "ready",
                "reason": "",
                "metadata": {},
            },
            {
                "eventId": "event:1",
                "createdAt": "2026-08-16T00:00:01Z",
                "stage": "received",
                "outcome": "accepted",
                "reason": "",
                "metadata": {},
            },
        ]

    def delivery_attempts_for_job(self, job_id):
        return [
            {
                "attemptId": "attempt:1",
                "startedAt": "2026-08-16T00:00:03Z",
                "completedAt": "2026-08-16T00:00:04Z",
                "channel": "accountNotification",
                "audience": "account",
                "provider": "Telegram",
                "status": "delivered",
                "reason": "",
                "metadata": {},
            }
        ]


def investment_alert(engine_deployment_id):
    return AlertEvent(
        account_id="main",
        account_label="기본 계정",
        severity="WATCH",
        rule="investmentInsight",
        key="insight:main:NVDA:generation:1",
        title="NVIDIA",
        lines=["현재가: $224.93"],
        symbol="NVDA",
        metadata={
            "ontologyRelationContext": {
                "reasoningEngineDeploymentId": engine_deployment_id,
                "reasoningEngineVersion": "v2" if "v2" in engine_deployment_id else "v1",
                "sourceAboxSnapshotId": "abox:1",
                "inferenceGenerationId": "generation:1",
                "inferenceGenerationAt": "2026-08-16T00:00:00Z",
            }
        },
    )


class NotificationModularizationTests(unittest.TestCase):
    def test_v1_and_v2_alerts_share_the_same_request_contract(self):
        source_event = DomainEvent(
            name=ONTOLOGY_REASONING_COMPLETED,
            aggregate_id="main:NVDA",
            event_id="event:reasoning:1",
        )
        ingress = NotificationIngressService(
            template_renderer=lambda message_type, context: context["body"],
        )

        requests = [
            ingress.request_from_alert(investment_alert("ontology-v1-active"), source_event),
            ingress.request_from_alert(investment_alert("ontology-v2-active"), source_event),
        ]

        self.assertEqual(
            ["notification-request-v1", "notification-request-v1"],
            [request.contract_version for request in requests],
        )
        self.assertEqual(
            ["ontology-v1-active", "ontology-v2-active"],
            [request.trace.engine_deployment_id for request in requests],
        )
        self.assertTrue(all(request.trace.source_abox_snapshot_id == "abox:1" for request in requests))
        self.assertTrue(all(request.trace.inference_generation_id == "generation:1" for request in requests))
        self.assertTrue(all(request.trace.source_event_id == source_event.event_id for request in requests))
        self.assertTrue(all(request.dedupe_key.startswith("outbox:event:reasoning:1:") for request in requests))

    def test_ingress_enriches_context_before_rendering(self):
        observed = {}

        def enrich(context):
            return {**context, "decisionMarker": "preserved"}

        def render(message_type, context):
            observed.update(context)
            return message_type + ":" + context["decisionMarker"]

        request = NotificationIngressService(
            template_renderer=render,
            context_enricher=enrich,
        ).request_from_alert(investment_alert("ontology-v2-active"))

        self.assertEqual("investmentInsight:preserved", request.source_text)
        self.assertEqual("preserved", observed["decisionMarker"])
        self.assertEqual(
            "ontology-v2-active",
            request.context["notificationSourceTrace"]["engineDeploymentId"],
        )

    def test_dispatch_records_attempt_without_changing_decision_context(self):
        queue = RecordingQueue()
        notifier = SuccessfulNotifier()
        ingress = NotificationIngressService()
        job = ingress.job_from_alert(investment_alert("ontology-v1-active"))
        original_relation = dict(job.context["ontologyRelationContext"])

        NotificationDispatchService(
            queue=queue,
            notifier_factory=lambda _account: notifier,
        ).deliver(job, {}, "rendered message")

        self.assertEqual(["rendered message"], notifier.messages)
        self.assertEqual("accountNotification", queue.started[0][1])
        self.assertTrue(queue.completed[0][2])
        self.assertEqual("Telegram", queue.completed[0][3])
        self.assertEqual(original_relation, job.context["ontologyRelationContext"])
        self.assertEqual("attempt:1", job.context["deliveryAttemptId"])

    def test_trace_query_returns_complete_chronological_timeline(self):
        trace = NotificationTraceQueryService(TraceStore()).trace_for_job("job:1")

        self.assertEqual("notification-trace-v1", trace["contractVersion"])
        self.assertEqual(
            ["received", "rendered", "dispatching", "delivery_result"],
            [item["stage"] for item in trace["timeline"]],
        )
        self.assertEqual([1, 2, 3, 4], [item["sequence"] for item in trace["timeline"]])
        self.assertEqual("started", trace["timeline"][2]["outcome"])
        self.assertEqual("Telegram", trace["timeline"][3]["metadata"]["provider"])

    def test_lifecycle_contract_rejects_out_of_order_delivery(self):
        self.assertTrue(notification_transition_allowed("rendered", "dispatching"))
        self.assertTrue(notification_transition_allowed("dispatching", "delivered"))
        self.assertFalse(notification_transition_allowed("received", "delivered"))

    def test_notification_application_package_has_no_infrastructure_dependency(self):
        application_dir = ROOT / "application" / "notification"
        forbidden = []
        for path in application_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and "infrastructure" in str(node.module or ""):
                    forbidden.append((path.name, node.lineno, node.module))
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if ".infrastructure" in alias.name:
                            forbidden.append((path.name, node.lineno, alias.name))
        self.assertEqual([], forbidden)

    def test_mysql_and_web_expose_complete_notification_lifecycle(self):
        schema = "\n".join(MYSQL_SCHEMA)
        web_source = (ROOT.parents[1] / "public" / "app.js").read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS notification_lifecycle_events", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS notification_delivery_attempts", schema)
        self.assertIn("function renderNotificationLifecycleTrace(job)", web_source)
        self.assertIn("알림 처리 전체 감사 데이터", web_source)


if __name__ == "__main__":
    unittest.main()
