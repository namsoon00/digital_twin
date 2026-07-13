import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.ontology_diagnostics_service import OntologyDiagnosticsService
from digital_twin.domain.events import DomainEvent, MONITORING_ALERTS_DETECTED, ONTOLOGY_REASONING_COMPLETED
from digital_twin.domain.notifications import NotificationJob


class FakeOntologyRepository:
    def active_tbox_metadata(self):
        return {
            "configured": True,
            "status": "ok",
            "source": "typedb-typeql",
            "graphStore": "typedb",
            "entityCount": 7,
            "relationCount": 3,
        }

    def rulebox_snapshot(self):
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "source": "typedb-typeql",
            "graphStore": "typedb",
            "ruleCount": 2,
            "conditionCount": 3,
            "derivationCount": 2,
            "nativeReasoningProfile": {
                "status": "partial",
                "supportedRuleCount": 1,
                "unsupportedRuleCount": 1,
                "functionCount": 1,
            },
        }

    def inferencebox_snapshot(self, symbols=None, limit=80):
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "source": "typedbInferenceBox",
            "graphStore": "typedb",
            "reasoningMode": "typedb-native-inferencebox",
            "querySource": "typedb-typeql",
            "typedbReadStatus": "ok",
            "entityCount": 4,
            "relationCount": 2,
            "traceCount": 1,
            "nativeRelationCount": 2,
            "nativeTypeDbReasoningUsed": True,
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "symbols": list(symbols or []),
        }


class FakeEventLog:
    def __init__(self, events):
        self.events = {event.name: event for event in events}

    def latest_events_by_name(self, names):
        return {name: self.events[name] for name in names if name in self.events}


class FakeNotificationQueue:
    def __init__(self, jobs):
        self.jobs = list(jobs)

    def recent(self, limit=80):
        return self.jobs[:limit]


class OntologyDiagnosticsServiceTests(unittest.TestCase):
    def test_status_reports_native_reasoning_and_outbox_boundary(self):
        alert_event = DomainEvent(
            name=MONITORING_ALERTS_DETECTED,
            aggregate_id="main",
            payload={"count": 1, "accountIds": ["main"], "symbols": ["TSLA"]},
            event_id="event-alert-1",
        )
        reasoning_event = DomainEvent(
            name=ONTOLOGY_REASONING_COMPLETED,
            aggregate_id="all",
            payload={"status": "ok"},
            event_id="event-reasoning-1",
        )
        job = NotificationJob.create(
            "본문",
            account_id="main",
            message_type="ontologyInferenceMissing",
            source_event_id=alert_event.event_id,
            source_event_name=alert_event.name,
            context={},
        )
        service = OntologyDiagnosticsService(
            ontology_repository=FakeOntologyRepository(),
            settings={"typeDbAddress": "127.0.0.1:1729", "typeDbDatabase": "orbit"},
            event_log=FakeEventLog([alert_event, reasoning_event]),
            notification_queue=FakeNotificationQueue([job]),
        )

        payload = service.status(symbols=["tsla"], limit=20)

        self.assertEqual(payload["contract"], "typedb-ontology-diagnostics-v1")
        self.assertEqual(payload["activeGraphStore"], "typedb")
        self.assertTrue(payload["typedb"]["addressConfigured"])
        self.assertEqual(payload["inferenceBox"]["reasoningMode"], "typedb-native-inferencebox")
        self.assertTrue(payload["reasoningBoundary"]["nativeTypeDbReasoningUsed"])
        self.assertFalse(payload["reasoningBoundary"]["typedbBootstrapReasoningUsed"])
        self.assertEqual(payload["notificationBoundary"]["status"], "ok")
        self.assertEqual(payload["notificationBoundary"]["jobsForLatestAlert"][0]["jobId"], job.job_id)

    def test_notification_boundary_warns_when_latest_alert_has_no_outbox_job(self):
        alert_event = DomainEvent(
            name=MONITORING_ALERTS_DETECTED,
            aggregate_id="main",
            payload={"count": 1},
            event_id="event-alert-2",
        )
        unrelated = NotificationJob.create("본문", message_type="investmentInsight", source_event_id="other")
        service = OntologyDiagnosticsService(
            ontology_repository=FakeOntologyRepository(),
            event_log=FakeEventLog([alert_event]),
            notification_queue=FakeNotificationQueue([unrelated]),
        )

        payload = service.status()

        self.assertEqual(payload["notificationBoundary"]["status"], "warning")
        self.assertIn("no recent notification job", payload["notificationBoundary"]["reason"])


if __name__ == "__main__":
    unittest.main()
