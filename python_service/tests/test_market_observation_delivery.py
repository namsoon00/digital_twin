import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from digital_twin import service_manager
from digital_twin.domain.market_observations import market_observation_delivery_admission
from digital_twin.domain.portfolio import AlertEvent
from digital_twin.infrastructure.mysql_monitoring_stores import MySQLMonitoringCycleRecorder


def state_with_outbox_anchor(price):
    return {
        "metadata": {
            "marketObservationBaselines": {
                "000660": {
                    "price": 1_676_500,
                    "reasoningPrice": 1_676_500,
                    "initialPrice": 1_676_500,
                    "outboxPrice": price,
                    "currency": "KRW",
                    "outboxQueuedAt": "2026-09-06T23:11:50Z",
                }
            }
        }
    }


def market_event(anchor=1_676_500, current=1_729_000):
    return AlertEvent(
        "default",
        "Test",
        "WATCH",
        "marketObservation",
        "default:market-observation:000660:up",
        "SK하이닉스",
        [],
        "000660",
        metadata={
            "deliveryDeferred": False,
            "marketObservation": {
                "outboxBaselinePrice": anchor,
                "baselinePrice": anchor,
                "currentPrice": current,
                "deliveryThresholdPct": 3,
                "direction": "up",
                "currency": "KRW",
            },
        },
        generated_at="2026-09-06T23:12:09Z",
    )


class FakeCursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class SentRowConnection:
    def __init__(self, row=None):
        self.row = row
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        return FakeCursor(self.row)


class MarketObservationDeliveryTests(unittest.TestCase):
    def test_concurrent_candidate_with_old_outbox_anchor_is_suppressed(self):
        event = market_event(anchor=1_676_500, current=1_728_000)

        result = market_observation_delivery_admission(
            event,
            state_with_outbox_anchor(1_729_000),
        )

        self.assertFalse(result["accepted"])
        self.assertEqual("stale-outbox-anchor", result["reasonCode"])
        self.assertEqual(1_729_000, result["authoritativeAnchorPrice"])

    def test_new_material_move_from_current_outbox_anchor_is_accepted(self):
        event = market_event(anchor=1_729_000, current=1_781_000)

        result = market_observation_delivery_admission(
            event,
            state_with_outbox_anchor(1_729_000),
        )

        self.assertTrue(result["accepted"])
        self.assertEqual("current-outbox-anchor", result["reasonCode"])
        self.assertGreater(result["authoritativeChangePct"], 3)

    def test_raw_quote_cadence_remains_active_when_general_cooldown_is_disabled(self):
        recorder = object.__new__(MySQLMonitoringCycleRecorder)
        recorder.runtime_settings = {
            "notificationCooldownEnabled": "0",
            "marketObservationImmediateCadenceMinutes": "10",
        }
        event = market_event(anchor=1_676_500, current=1_729_000)
        connection = SentRowConnection({
            "sent_key": event.cadence_key(),
            "sent_at": "2026-09-06T23:11:50Z",
        })

        accepted, details = recorder.guard_market_observation_delivery_with_connection(
            connection,
            [event],
            {"default": state_with_outbox_anchor(1_676_500)},
            now=datetime(2026, 9, 6, 23, 12, 9, tzinfo=timezone.utc),
        )

        self.assertEqual([], accepted)
        self.assertEqual(1, details["suppressedCount"])
        self.assertEqual("market-observation-cadence", details["suppressions"][0]["reasonCode"])
        self.assertIn("FOR UPDATE", connection.queries[0][0])

        worker_spec = {
            "label": "Python realtime monitor",
            "command": ["python3", "-u", "python_service/service.py", "monitor", "watch"],
            "needle": "python_service/service.py monitor watch",
        }
        with patch.object(service_manager, "pid_exists", return_value=True), patch.object(
            service_manager,
            "command_for_pid",
            return_value="python3 -u python_service/service.py monitor watch",
        ), patch.object(
            service_manager,
            "process_working_directory",
            return_value=str(Path(service_manager.ROOT_DIR).resolve()),
        ), patch.object(service_manager.os, "getpid", return_value=999):
            self.assertTrue(service_manager.is_owned_project_worker_process(123, worker_spec))
            worker_spec["command"][-2:] = ["market-data", "watch"]
            self.assertFalse(service_manager.is_owned_project_worker_process(123, worker_spec))

        with TemporaryDirectory() as temp:
            stop_spec = {
                "label": "Python realtime monitor",
                "pid": Path(temp) / "monitor.pid",
                "log": Path(temp) / "monitor.log",
                "command": ["python3", "-u", "python_service/service.py", "monitor", "watch"],
                "needle": "python_service/service.py monitor watch",
            }
            cleanup = {"found": [123], "stopped": [123], "remaining": []}
            with patch.object(service_manager, "read_pid", return_value=0), patch.object(
                service_manager,
                "reconcile_owned_project_worker_duplicates",
                return_value=cleanup,
            ) as reconcile:
                self.assertEqual(0, service_manager.stop_worker(stop_spec))
            reconcile.assert_called_once_with(stop_spec, keep_pid=0)


if __name__ == "__main__":
    unittest.main()
