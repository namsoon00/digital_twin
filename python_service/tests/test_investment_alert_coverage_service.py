import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.investment_alert_coverage_service import (
    InvestmentAlertCoverageNotificationEnqueuer,
    InvestmentAlertCoverageService,
)
from digital_twin.application.ontology_diagnostics_service import OntologyDiagnosticsService
from digital_twin.domain.message_types import INVESTMENT_ALERT_COVERAGE


class FakeCoverageStore:
    def __init__(self, states):
        self.states = list(states)
        self.saved = {}

    def reconcile(self, deployment_id, **kwargs):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        health = {
            "state": state,
            "reason": "coverage " + state,
            "materialEventCount": 10,
            "terminalEventCount": 8 if state != "healthy" else 10,
            "terminalCoveragePct": 80.0 if state != "healthy" else 100.0,
            "overdueEventCount": 2 if state == "warning" else 0,
            "failedEventCount": 1 if state == "critical" else 0,
            "candidateEventCount": 8,
            "deliveredCandidateCount": 1,
            "policyStarvation": False,
        }
        return {"status": "ok", "health": health, "recordCount": 10, "stateCounts": {}}

    def load_health_state(self, deployment_id):
        return dict(self.saved.get(deployment_id) or {})

    def save_health_state(self, deployment_id, payload):
        self.saved[deployment_id] = dict(payload)


class FakeQueue:
    def __init__(self):
        self.jobs = []

    def enqueue(self, job):
        self.jobs.append(job)
        return True


class InvestmentAlertCoverageServiceTests(unittest.TestCase):
    def test_diagnostics_exposes_coverage_health_as_operations_only_boundary(self):
        service = OntologyDiagnosticsService(
            ontology_repository=None,
            alert_coverage_provider=lambda: {
                "deploymentId": "delivery:v2",
                "lookbackHours": 24,
                "health": {
                    "state": "warning",
                    "reason": "one event overdue",
                    "materialEventCount": 5,
                    "terminalEventCount": 4,
                    "terminalCoveragePct": 80.0,
                    "overdueEventCount": 1,
                },
            },
        )

        boundary = service.alert_coverage_boundary()

        self.assertEqual("warning", boundary["status"])
        self.assertEqual(5, boundary["materialEventCount"])
        self.assertEqual(1, boundary["overdueEventCount"])

    def test_warning_requires_consecutive_observations_and_recovery_emits_event(self):
        now = datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc)
        clock = [now]
        store = FakeCoverageStore(["warning", "warning", "healthy"])
        service = InvestmentAlertCoverageService(
            store,
            lambda: "delivery-v2",
            {"investmentAlertCoverageConsecutiveObservations": "2"},
            now_provider=lambda: clock[0],
            monotonic_provider=lambda: clock[0].timestamp(),
        )

        first, first_event = service.run_once(force=True)
        self.assertFalse(first["alertRequired"])
        self.assertIsNone(first_event)

        clock[0] += timedelta(minutes=1)
        second, second_event = service.run_once(force=True)
        self.assertTrue(second["alertRequired"])
        self.assertEqual("incident-start", second["alertKind"])
        self.assertIsNotNone(second_event)

        clock[0] += timedelta(minutes=1)
        recovered, recovered_event = service.run_once(force=True)
        self.assertTrue(recovered["alertRequired"])
        self.assertEqual("recovered", recovered["alertKind"])
        self.assertIsNotNone(recovered_event)

    def test_critical_failure_alerts_immediately_and_enqueues_operations_job(self):
        now = datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc)
        store = FakeCoverageStore(["critical"])
        service = InvestmentAlertCoverageService(
            store,
            lambda: "delivery-v2",
            {"investmentAlertCoverageConsecutiveObservations": "3"},
            now_provider=lambda: now,
            monotonic_provider=lambda: now.timestamp(),
        )
        payload, event = service.run_once(force=True)
        self.assertTrue(payload["alertRequired"])
        self.assertEqual("incident-start", payload["alertKind"])

        queue = FakeQueue()
        InvestmentAlertCoverageNotificationEnqueuer(queue).handle(event)
        self.assertEqual(1, len(queue.jobs))
        self.assertEqual(INVESTMENT_ALERT_COVERAGE, queue.jobs[0].message_type)
        self.assertIn("처리 실패: 1건", queue.jobs[0].text)


if __name__ == "__main__":
    unittest.main()
