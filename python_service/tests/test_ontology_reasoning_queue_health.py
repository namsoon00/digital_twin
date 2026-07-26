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
    def test_delay_requires_consecutive_observations_before_alerting(self):
        started = datetime(2026, 7, 25, 1, 0, tzinfo=UTC)
        first = evaluate_ontology_reasoning_queue_health(
            queue_snapshot(),
            warning_age_minutes=30,
            critical_age_minutes=180,
            required_consecutive_observations=3,
            now=started,
        )
        second = evaluate_ontology_reasoning_queue_health(
            queue_snapshot(),
            first.to_dict(),
            warning_age_minutes=30,
            critical_age_minutes=180,
            required_consecutive_observations=3,
            now=started + timedelta(minutes=1),
        )
        third = evaluate_ontology_reasoning_queue_health(
            queue_snapshot(),
            second.to_dict(),
            warning_age_minutes=30,
            critical_age_minutes=180,
            required_consecutive_observations=3,
            now=started + timedelta(minutes=2),
        )

        self.assertEqual("delayed", first.candidate_state)
        self.assertEqual("healthy", first.state)
        self.assertFalse(first.alert_required)
        self.assertEqual(2, second.consecutive_delayed_observations)
        self.assertEqual("delayed", third.state)
        self.assertTrue(third.alert_required)

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

    def test_projection_runtime_summary_keeps_deployment_and_patch_provenance(self):
        runner = OntologyReasoningRunner.__new__(OntologyReasoningRunner)
        monitor = type("Monitor", (), {
            "last_ontology_projection_results": {
                "main": {
                    "runtimeObservation": {
                        "durationMs": 1234,
                        "status": "ok",
                        "runtimeIdentity": {
                            "contract": "orbit-runtime-identity-v1",
                            "version": "release-1",
                            "revision": "abc123",
                            "source": "test",
                        },
                        "scope": {
                            "targetScopedManifestPatch": {
                                "status": "full-global-impact",
                                "mode": "full-manifest-fallback",
                                "fallbackReason": "global-value-context-without-explicit-subject",
                                "targetSymbolCount": 2,
                                "deferredScopeCount": 4,
                            },
                        },
                    },
                },
            },
        })()

        summary = runner.projection_runtime_summary(monitor)

        self.assertEqual("abc123", summary["runtimeIdentity"]["revision"])
        self.assertEqual("full-global-impact", summary["targetScopedManifestPatch"]["status"])
        self.assertEqual(4, summary["targetScopedManifestPatch"]["deferredScopeCount"])

    def test_critical_request_age_escalates_immediately(self):
        health = evaluate_ontology_reasoning_queue_health(
            queue_snapshot("2026-07-24T20:00:00Z"),
            previous={"state": "healthy"},
            warning_age_minutes=30,
            critical_age_minutes=90,
            required_consecutive_observations=3,
            now=datetime(2026, 7, 25, 0, 1, tzinfo=UTC),
        )

        self.assertEqual("critical", health.state)
        self.assertEqual("oldest-request-critical", health.reason_code)
        self.assertTrue(health.alert_required)

    def test_request_count_burst_requires_confirmation(self):
        started = datetime(2026, 7, 25, 0, 1, tzinfo=UTC)
        snapshot = queue_snapshot("2026-07-25T00:00:00Z", rawRequestCount=200)
        first = evaluate_ontology_reasoning_queue_health(
            snapshot,
            previous={"state": "healthy"},
            warning_age_minutes=30,
            critical_age_minutes=90,
            critical_pending_count=200,
            required_consecutive_observations=3,
            now=started,
        )
        second = evaluate_ontology_reasoning_queue_health(
            snapshot,
            previous=first.to_dict(),
            warning_age_minutes=30,
            critical_age_minutes=90,
            critical_pending_count=200,
            required_consecutive_observations=3,
            now=started + timedelta(minutes=1),
        )
        third = evaluate_ontology_reasoning_queue_health(
            snapshot,
            previous=second.to_dict(),
            warning_age_minutes=30,
            critical_age_minutes=90,
            critical_pending_count=200,
            required_consecutive_observations=3,
            now=started + timedelta(minutes=2),
        )

        self.assertEqual("critical", first.candidate_state)
        self.assertEqual("healthy", first.state)
        self.assertFalse(first.alert_required)
        self.assertEqual("critical", third.state)
        self.assertTrue(third.alert_required)

    def test_latest_state_mailbox_pressure_uses_effective_pending_count(self):
        health = evaluate_ontology_reasoning_queue_health(
            queue_snapshot(
                "2026-07-25T00:00:00Z",
                rawRequestCount=320,
                effectivePendingCount=9,
                mailboxPendingEntryCount=9,
                queueDispatch={
                    "oldestRequestAt": "2026-07-25T00:00:00Z",
                    "pendingSymbolCount": 6,
                    "overduePendingSymbolCount": 0,
                    "mode": "fairness-drain",
                },
            ),
            previous={"state": "healthy"},
            warning_age_minutes=30,
            critical_age_minutes=90,
            warning_pending_count=100,
            critical_pending_count=200,
            now=datetime(2026, 7, 25, 0, 1, tzinfo=UTC),
        )

        self.assertEqual("healthy", health.state)
        self.assertEqual("queue-healthy", health.reason_code)
        self.assertEqual(320, health.raw_pending_count)
        self.assertEqual(9, health.effective_pending_count)

    def test_fairness_drain_overdue_symbols_do_not_page_before_request_slo_is_breached(self):
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

        self.assertEqual("healthy", health.state)
        self.assertEqual("healthy", health.candidate_state)
        self.assertEqual("fairness-drain-progress", health.reason_code)
        self.assertFalse(health.alert_required)

    def test_recovery_from_delayed_queue_is_alerted(self):
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
        self.assertTrue(health.alert_required)

    def test_recovery_is_an_operations_alert_with_incident_duration(self):
        now = datetime(2026, 7, 25, 1, 0, tzinfo=UTC)
        store = MemoryStore({
            "queueDelayHealth": {
                "state": "critical",
                "candidateState": "critical",
                "stateSince": "2026-07-25T00:10:00Z",
                "firstObservedAt": "2026-07-25T00:00:00Z",
                "consecutiveDelayedObservations": 3,
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
        self.assertEqual(50, health["incidentDurationMinutes"])
        self.assertEqual(1, len(queue.jobs))
        job = queue.jobs[0]
        self.assertEqual(ONTOLOGY_REASONING_QUEUE, job.message_type)
        self.assertEqual("", job.account_id)
        self.assertEqual("operations", job.context["deliveryAudience"])
        self.assertIn("[운영] 온톨로지 추론 요청 대기 정상 복구", job.text)
        self.assertIn("해소: critical 상태가 정상 처리로 복구되었습니다. 감지 후 50분 지속됐습니다.", job.text)

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

    def test_global_operational_message_is_enqueued_once(self):
        queue = MemoryQueue()
        enqueuer = OntologyReasoningQueueHealthNotificationEnqueuer(queue)
        event = ontology_reasoning_queue_health_changed_event({
            "alertRequired": True,
            "state": "delayed",
            "previousState": "healthy",
            "stateSince": "2026-07-25T01:00:00Z",
            "checkedAt": "2026-07-25T01:00:00Z",
            "oldestRequestAt": "2026-07-25T00:00:00Z",
            "oldestRequestAgeMinutes": 60,
            "rawPendingCount": 120,
            "pendingSymbolCount": 5,
            "overduePendingSymbolCount": 2,
            "queueMode": "fairness-drain",
            "reason": "가장 오래된 추론 요청이 지연 기준을 넘었습니다.",
        })

        enqueuer.handle(event)

        self.assertEqual(1, len(queue.jobs))
        job = queue.jobs[0]
        self.assertEqual("", job.account_id)
        self.assertEqual(ONTOLOGY_REASONING_QUEUE, job.message_type)
        self.assertIn(event.event_id, job.dedupe_key)
        self.assertIn("가장 오래된 요청", job.text)
        self.assertTrue(is_operations_delivery_message_type(job.message_type))

        rendered = render_notification(NotificationTemplate.default(ONTOLOGY_REASONING_QUEUE), job.context)
        self.assertIn("[운영] 관계 분석 추론 요청 대기 지연 감지", rendered)
        self.assertNotIn("AI 의견", rendered)
        self.assertNotIn("모델 판단", rendered)

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
