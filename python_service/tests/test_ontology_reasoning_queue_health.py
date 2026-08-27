import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.ontology_reasoning_queue_health_service import (  # noqa: E402
    OntologyReasoningQueueHealthNotificationEnqueuer,
    OntologyReasoningQueueHealthService,
)
from digital_twin.application.notification_service import NotificationQueueRunner  # noqa: E402
from digital_twin.application.ontology_reasoning_service import OntologyReasoningRunner  # noqa: E402
from digital_twin.domain.events import ONTOLOGY_REASONING_QUEUE_HEALTH_CHANGED, ontology_reasoning_queue_health_changed_event  # noqa: E402
from digital_twin.domain.message_types import ONTOLOGY_REASONING_QUEUE, is_operations_delivery_message_type  # noqa: E402
from digital_twin.domain.ontology_reasoning_queue_health import evaluate_ontology_reasoning_queue_health  # noqa: E402
from digital_twin.infrastructure.notifications import NotificationResult  # noqa: E402
from digital_twin.domain.notifications import NotificationJob  # noqa: E402
from digital_twin.domain.notification_templates import NotificationTemplate, render_notification  # noqa: E402


UTC = timezone.utc


def queue_snapshot(oldest_request_at="2026-07-25T00:00:00Z", **overrides):
    snapshot = {
        "status": "cooldown",
        "enabled": True,
        "rawRequestCount": 120,
        "mailboxPendingEntryCount": 12,
        "queueDispatch": {
            "oldestRequestAt": oldest_request_at,
            "pendingSymbolCount": 5,
            "overduePendingSymbolCount": 1,
            "mode": "priority-selected",
            "fairnessDrainActive": False,
            "backpressureActive": False,
        },
    }
    snapshot.update(overrides)
    return snapshot


class MemoryStore:
    def __init__(self, payload=None):
        self.payload = dict(payload or {})

    def load(self):
        return dict(self.payload)

    def save(self, payload):
        self.payload = dict(payload or {})


class MemoryQueue:
    def __init__(self):
        self.jobs = []

    def enqueue(self, job):
        self.jobs.append(job)
        return True


class MemoryCursor(MemoryStore):
    def processed_event_ids(self):
        return []


class RecordingQueueHealthService:
    def __init__(self):
        self.snapshots = []

    def record(self, snapshot):
        self.snapshots.append(dict(snapshot or {}))
        return {"state": "healthy", "checkedAt": "2026-07-25T00:00:00Z"}, None


class OntologyReasoningQueueHealthTests(unittest.TestCase):
    def test_blocked_queue_escalates_immediately(self):
        health = evaluate_ontology_reasoning_queue_health(
            queue_snapshot(
                status="circuit-open",
                queueHealth={"status": "blocked", "reason": "TypeDB projection circuit is open."},
            ),
            previous={"state": "healthy"},
            required_consecutive_observations=3,
            now=datetime(2026, 7, 25, 0, 1, tzinfo=UTC),
        )

        self.assertEqual("critical", health.state)
        self.assertTrue(health.blocked)
        self.assertEqual("queue-blocked", health.reason_code)
        self.assertTrue(health.alert_required)

    def test_fairness_drain_is_visible_as_progress_without_paging(self):
        health = evaluate_ontology_reasoning_queue_health(
            queue_snapshot(
                "2026-07-25T00:00:00Z",
                rawRequestCount=7,
                effectivePendingCount=9,
                mailboxPendingEntryCount=9,
                queueDispatch={
                    "oldestRequestAt": "2026-07-25T00:00:00Z",
                    "pendingSymbolCount": 9,
                    "overduePendingSymbolCount": 6,
                    "mode": "fairness-drain",
                    "fairnessDrainActive": True,
                    "effectiveIntervalSeconds": 60,
                },
            ),
            previous={"state": "healthy"},
            warning_age_minutes=30,
            critical_age_minutes=90,
            warning_pending_count=100,
            critical_pending_count=200,
            warning_overdue_symbols=3,
            critical_overdue_symbols=8,
            now=datetime(2026, 7, 25, 0, 4, tzinfo=UTC),
        )

        self.assertEqual("draining", health.state)
        self.assertEqual("draining", health.candidate_state)
        self.assertEqual("fairness-drain-progress", health.reason_code)
        self.assertFalse(health.alert_required)

    def test_domain_marks_recovery_for_the_application_service_to_decide(self):
        previous = {
            "state": "delayed",
            "candidateState": "delayed",
            "stateSince": "2026-07-25T00:10:00Z",
            "firstObservedAt": "2026-07-25T00:00:00Z",
            "consecutiveDelayedObservations": 3,
        }
        health = evaluate_ontology_reasoning_queue_health(
            queue_snapshot("", rawRequestCount=0, mailboxPendingEntryCount=0, queueDispatch={"mode": "waiting"}),
            previous=previous,
            now=datetime(2026, 7, 25, 1, 0, tzinfo=UTC),
        )

        self.assertEqual("healthy", health.state)
        self.assertTrue(health.state_changed)
        self.assertFalse(health.alert_required)

    def test_recovery_is_an_operations_alert_with_incident_duration(self):
        now = datetime(2026, 7, 25, 1, 0, tzinfo=UTC)
        store = MemoryStore({
            "queueDelayHealth": {
                "state": "critical",
                "candidateState": "critical",
                "stateSince": "2026-07-25T00:10:00Z",
                "firstObservedAt": "2026-07-25T00:00:00Z",
                "consecutiveDelayedObservations": 3,
                "incidentOpen": True,
                "incidentId": "ontology-reasoning-queue:2026-07-25T00:00:00Z",
                "incidentStartedAt": "2026-07-25T00:00:00Z",
                "activeAlertDeliveredAt": "2026-07-25T00:15:00Z",
                "activeAlertJobId": "N-active",
                "activeAlertDeliveryState": "done",
            },
        })
        service = OntologyReasoningQueueHealthService(store, now_provider=lambda: now)
        health, event = service.record(
            queue_snapshot("", rawRequestCount=0, mailboxPendingEntryCount=0, queueDispatch={"mode": "waiting"})
        )
        queue = MemoryQueue()
        OntologyReasoningQueueHealthNotificationEnqueuer(queue).handle(event)

        self.assertTrue(health["alertRequired"])
        self.assertEqual("recovered", health["alertKind"])
        self.assertEqual("critical", health["recoveredFromState"])
        self.assertEqual(60, health["incidentDurationMinutes"])
        self.assertEqual(1, len(queue.jobs))
        job = queue.jobs[0]
        self.assertEqual(ONTOLOGY_REASONING_QUEUE, job.message_type)
        self.assertEqual("", job.account_id)
        self.assertEqual("operations", job.context["deliveryAudience"])
        self.assertIn("[운영] 온톨로지 추론 요청 대기 정상 복구", job.text)
        self.assertIn("해소: critical 상태가 정상 처리로 복구되었습니다. 감지 후 60분 지속됐습니다.", job.text)

        account_messages = []
        operations_messages = []
        runner = NotificationQueueRunner(
            queue=None,
            account_repository=None,
            notifier_factory=lambda _account: type("Notifier", (), {
                "send": lambda _self, message: account_messages.append(message) or NotificationResult(True, "Account"),
            })(),
            operations_notifier_factory=lambda _account: type("Notifier", (), {
                "send": lambda _self, message: operations_messages.append(message) or NotificationResult(True, "Operations"),
            })(),
        )
        runner.deliver(job, {}, job.text)
        self.assertEqual([], account_messages)
        self.assertEqual([job.text], operations_messages)
        self.assertEqual("operations", job.context["deliveryAudience"])

    def test_recovery_is_not_enqueued_when_the_active_alert_was_not_delivered(self):
        now = datetime(2026, 7, 25, 1, 0, tzinfo=UTC)
        store = MemoryStore({
            "queueDelayHealth": {
                "state": "critical",
                "candidateState": "critical",
                "stateSince": "2026-07-25T00:10:00Z",
                "firstObservedAt": "2026-07-25T00:00:00Z",
                "consecutiveDelayedObservations": 3,
                "incidentOpen": True,
                "incidentId": "ontology-reasoning-queue:2026-07-25T00:00:00Z",
                "incidentStartedAt": "2026-07-25T00:00:00Z",
                "lastAlertDeliveryState": "suppressed",
            },
        })
        service = OntologyReasoningQueueHealthService(store, now_provider=lambda: now)

        health, event = service.record(
            queue_snapshot("", rawRequestCount=0, mailboxPendingEntryCount=0, queueDispatch={"mode": "waiting"})
        )

        self.assertFalse(health["alertRequired"])
        self.assertIsNone(event)
        self.assertEqual("active-alert-not-delivered", health["recoverySuppressedReason"])

    def test_delivery_provenance_unlocks_recovery_after_drain(self):
        clock = [datetime(2026, 7, 25, 0, 1, tzinfo=UTC)]
        store = MemoryStore()
        service = OntologyReasoningQueueHealthService(
            store,
            {"ontologyReasoningQueueCriticalAgeMinutes": "90"},
            now_provider=lambda: clock[0],
        )
        active_health, active_event = service.record(queue_snapshot(
            "2026-07-25T00:00:00Z",
            rawRequestCount=12,
            effectivePendingCount=12,
            mailboxPendingEntryCount=12,
            queueDispatch={
                "oldestRequestAt": "2026-07-25T00:00:00Z",
                "pendingSymbolCount": 5,
                "overduePendingSymbolCount": 8,
                "mode": "priority-selected",
                "fairnessDrainActive": False,
            },
        ))
        self.assertTrue(active_health["alertRequired"])
        self.assertIsNotNone(active_event)

        queue = MemoryQueue()
        OntologyReasoningQueueHealthNotificationEnqueuer(queue).handle(active_event)
        clock[0] = datetime(2026, 7, 25, 0, 2, tzinfo=UTC)
        draining_health, _ = service.record(queue_snapshot(
            "2026-07-25T00:00:00Z",
            rawRequestCount=12,
            effectivePendingCount=12,
            mailboxPendingEntryCount=12,
            queueDispatch={
                "oldestRequestAt": "2026-07-25T00:00:00Z",
                "pendingSymbolCount": 5,
                "overduePendingSymbolCount": 8,
                "mode": "fairness-drain",
                "fairnessDrainActive": True,
            },
        ))
        self.assertEqual("draining", draining_health["state"])
        service.record_notification_delivery(queue.jobs[0], "done")
        self.assertTrue(service.previous()["activeAlertDeliveredAt"])

        clock[0] = datetime(2026, 7, 25, 1, 0, tzinfo=UTC)
        recovered_health, recovery_event = service.record(
            queue_snapshot("", rawRequestCount=0, mailboxPendingEntryCount=0, queueDispatch={"mode": "waiting"})
        )

        self.assertTrue(recovered_health["alertRequired"])
        self.assertEqual("recovered", recovered_health["alertKind"])
        self.assertIsNotNone(recovery_event)

    def test_operations_alert_never_falls_back_to_an_account_notifier(self):
        job = NotificationJob.create(
            "[운영] 테스트",
            message_type=ONTOLOGY_REASONING_QUEUE,
        )
        runner = NotificationQueueRunner(
            queue=None,
            account_repository=None,
            notifier_factory=lambda _account: type("Notifier", (), {
                "send": lambda _self, _message: NotificationResult(True, "Account"),
            })(),
        )

        with self.assertRaisesRegex(RuntimeError, "계정 채널로 대체 발송하지 않았습니다"):
            runner.deliver(job, {}, job.text)

    def test_delivery_suppresses_an_obsolete_queue_incident_after_recovery(self):
        class DeliveryQueue:
            def __init__(self, job):
                self.jobs = [job]

            def pending(self, limit=10):
                return [job for job in self.jobs if job.status in {"pending", "failed"}][:limit]

            def mark_processing(self, job):
                job.status = "processing"
                job.attempts += 1

            def mark_done(self, job):
                job.status = "done"

            def mark_failed(self, job, reason):
                job.status = "failed"
                job.last_error = str(reason)

            def mark_suppressed(self, job, reason):
                job.status = "suppressed"
                job.last_error = str(reason)

        class EmptyAccounts:
            def load_all(self):
                return []

        job = NotificationJob.create(
            "[운영] 이전 critical 알림",
            message_type=ONTOLOGY_REASONING_QUEUE,
            context={"queueDelayHealth": {"state": "critical", "checkedAt": "2026-07-25T00:00:00Z"}},
        )
        queue = DeliveryQueue(job)
        runner = NotificationQueueRunner(
            queue=queue,
            account_repository=EmptyAccounts(),
            notifier_factory=lambda _account: None,
            operations_notifier_factory=lambda _account: None,
            operational_state_resolver=lambda: {
                "state": "healthy",
                "checkedAt": "2026-07-25T00:02:00Z",
            },
        )

        processed = runner.run_once()

        self.assertEqual(1, processed)
        self.assertEqual("suppressed", job.status)
        self.assertIn("critical에서 healthy", job.last_error)
        self.assertEqual("obsolete_queue_health_at_dispatch", job.context["deliverySuppressionReason"])

    def test_service_reminds_only_after_the_configured_interval(self):
        now = datetime(2026, 7, 25, 2, 0, tzinfo=UTC)
        store = MemoryStore({
            "queueDelayHealth": {
                "state": "delayed",
                "candidateState": "delayed",
                "stateSince": "2026-07-25T00:30:00Z",
                "firstObservedAt": "2026-07-25T00:00:00Z",
                "consecutiveDelayedObservations": 4,
                "lastAlertAt": "2026-07-25T01:00:00Z",
            },
        })
        service = OntologyReasoningQueueHealthService(
            store,
            {
                "ontologyReasoningQueueWarningAgeMinutes": "30",
                "ontologyReasoningQueueCriticalAgeMinutes": "180",
                "ontologyReasoningQueueConsecutiveObservations": "3",
                "ontologyReasoningQueueAlertReminderMinutes": "60",
            },
            now_provider=lambda: now,
        )

        health, event = service.record(queue_snapshot())

        self.assertTrue(health["alertRequired"])
        self.assertEqual("reminder", health["alertKind"])
        self.assertIsNotNone(event)
        self.assertEqual(ONTOLOGY_REASONING_QUEUE_HEALTH_CHANGED, event.name)

    def test_runner_records_queue_health_after_each_turn(self):
        recorder = RecordingQueueHealthService()
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=MemoryCursor(),
            monitor_runner_factory=lambda: None,
            settings={"ontologyReasoningEnabled": "0"},
            queue_health_service=recorder,
            now_provider=lambda: datetime(2026, 7, 25, 0, 0, tzinfo=UTC),
        )

        result = runner.run_once()

        self.assertEqual("disabled", recorder.snapshots[0]["status"])
        self.assertEqual("healthy", result["queueDelayHealth"]["state"])


if __name__ == "__main__":
    unittest.main()
