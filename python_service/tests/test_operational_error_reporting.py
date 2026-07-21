import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.infrastructure.operational_error_reporting import OperationalErrorReporter
from digital_twin.infrastructure.schedulers import RealtimeScheduler


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return SimpleNamespace(delivered=True, reason="")


class OperationalErrorReporterTests(unittest.TestCase):
    def test_reports_sanitized_error_and_records_audit_event(self):
        notifier = FakeNotifier()
        events = []
        secret = "123456:abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN"
        reporter = OperationalErrorReporter(
            notifier_factory=lambda: notifier,
            event_publisher=events.append,
            settings_provider=lambda: {"telegramBotToken": secret},
            cooldown_seconds=300,
        )

        result = reporter.report("Python news collector", RuntimeError("Authorization: Bearer " + secret))

        self.assertTrue(result["sent"])
        self.assertEqual(1, len(notifier.messages))
        self.assertIn("Python news collector", notifier.messages[0])
        self.assertNotIn(secret, notifier.messages[0])
        self.assertIn("***", notifier.messages[0])
        self.assertEqual("system.error_reported", events[0].name)
        self.assertNotIn(secret, str(events[0].payload))

    def test_repeated_error_is_aggregated_until_cooldown_expires(self):
        notifier = FakeNotifier()
        now = [0.0]
        reporter = OperationalErrorReporter(
            notifier_factory=lambda: notifier,
            event_publisher=lambda _event: None,
            monotonic_provider=lambda: now[0],
            cooldown_seconds=300,
        )

        first = reporter.report("Python market data collector", RuntimeError("provider unavailable"))
        second = reporter.report("Python market data collector", RuntimeError("provider unavailable"))
        now[0] = 301.0
        third = reporter.report("Python market data collector", RuntimeError("provider unavailable"))

        self.assertTrue(first["sent"])
        self.assertTrue(second["suppressed"])
        self.assertTrue(third["sent"])
        self.assertEqual(2, len(notifier.messages))
        self.assertIn("이전 발송 이후 같은 오류 발생: 2회", notifier.messages[1])

    def test_realtime_scheduler_reports_cycle_error_without_stopping_worker(self):
        class FailingRunner:
            def run_once(self):
                raise RuntimeError("monitor cycle failed")

        class StoppingReporter:
            def __init__(self):
                self.calls = []

            def report(self, component, error, stage):
                self.calls.append((component, str(error), stage))
                scheduler.stop()
                return {"sent": True, "suppressed": False}

        reporter = StoppingReporter()
        scheduler = RealtimeScheduler(FailingRunner(), 180, error_reporter=reporter)

        with patch("digital_twin.infrastructure.schedulers.install_stop_handlers"):
            scheduler.run_forever()

        self.assertEqual(
            [("Python realtime monitor", "monitor cycle failed", "monitor cycle")],
            reporter.calls,
        )
