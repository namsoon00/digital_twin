import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from digital_twin.application.operational_storage_capacity_service import (
    OperationalStorageCapacityNotificationEnqueuer,
    OperationalStorageCapacityService,
)
from digital_twin.domain.events import operational_storage_capacity_changed_event
from digital_twin.domain.message_types import (
    OPERATIONAL_STORAGE_CAPACITY,
    is_operations_delivery_message_type,
)
from digital_twin.domain.operational_notification_presentation import operational_notification_presentation
from digital_twin.infrastructure.operational_storage_guard import operational_storage_inventory


class StateStore:
    def __init__(self):
        self.payload = {}

    def load(self):
        return dict(self.payload)

    def replace(self, payload):
        self.payload = dict(payload or {})


class Queue:
    def __init__(self, fails=False):
        self.fails = fails
        self.jobs = []

    def enqueue(self, job):
        if self.fails:
            raise OSError("No space left on device")
        self.jobs.append(job)


class Notifier:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return SimpleNamespace(delivered=True, reason="")


class OperationalStorageCapacityTests(unittest.TestCase):
    def limited_snapshot(self):
        return {
            "freeMb": 60 * 1024,
            "freePercent": 40,
            "typedbSizeMb": 5000,
            "typedbLimitMb": 4096,
            "typedbWalMb": 2200,
            "typedbCheckpointMb": 1800,
            "mysqlSizeMb": 300,
            "mysqlLimitMb": 4096,
            "logSizeMb": 12,
            "logLimitMb": 512,
            "cleanupMode": "accelerated",
        }

    def healthy_snapshot(self):
        return {
            **self.limited_snapshot(),
            "typedbSizeMb": 300,
            "typedbWalMb": 20,
            "typedbCheckpointMb": 30,
            "cleanupMode": "normal",
        }

    def test_state_change_reminder_and_recovery_are_durable(self):
        current = [datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)]
        store = StateStore()
        service = OperationalStorageCapacityService(
            store=store,
            settings={"operationalStorageAlertReminderMinutes": "60"},
            now_provider=lambda: current[0],
        )

        first, first_event = service.record(self.limited_snapshot())
        self.assertEqual("limited", first["state"])
        self.assertEqual("state-changed", first["alertKind"])
        self.assertIsNotNone(first_event)

        current[0] += timedelta(minutes=30)
        repeated, repeated_event = service.record(self.limited_snapshot())
        self.assertFalse(repeated["alertRequired"])
        self.assertIsNone(repeated_event)

        current[0] += timedelta(minutes=31)
        reminder, reminder_event = service.record(self.limited_snapshot())
        self.assertEqual("reminder", reminder["alertKind"])
        self.assertIsNotNone(reminder_event)

        current[0] += timedelta(minutes=1)
        recovered, recovered_event = service.record(self.healthy_snapshot())
        self.assertEqual("healthy", recovered["state"])
        self.assertEqual("recovered", recovered["alertKind"])
        self.assertIsNotNone(recovered_event)
        self.assertEqual("limited", recovered["recoveredFromState"])

    def test_runtime_write_failure_does_not_bypass_the_capacity_reminder_window(self):
        current = [datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)]
        service = OperationalStorageCapacityService(
            store=StateStore(),
            settings={"operationalStorageAlertReminderMinutes": "60"},
            now_provider=lambda: current[0],
        )
        service.record(self.limited_snapshot())

        current[0] += timedelta(minutes=10)
        repeated, repeated_event = service.record(self.limited_snapshot(), force_alert=True)
        self.assertFalse(repeated["alertRequired"])
        self.assertIsNone(repeated_event)

        current[0] += timedelta(minutes=51)
        due, due_event = service.record(self.limited_snapshot(), force_alert=True)
        self.assertTrue(due["alertRequired"])
        self.assertIsNotNone(due_event)

    def test_queue_failure_uses_direct_operations_notifier(self):
        notifier = Notifier()
        payload = {
            **self.limited_snapshot(),
            "state": "critical",
            "previousState": "limited",
            "alertRequired": True,
            "alertKind": "runtime-write-failure",
            "checkedAt": "2026-08-02T09:00:00Z",
            "warningFreeMb": 49152,
            "minimumFreeMb": 32768,
            "criticalFreeMb": 20480,
            "limitingComponents": [{"component": "typedb"}],
            "suggestedAction": "TypeDB 안전 재구축을 실행하세요.",
        }
        OperationalStorageCapacityNotificationEnqueuer(
            Queue(fails=True),
            fallback_notifier_factory=lambda: notifier,
        ).handle(operational_storage_capacity_changed_event(payload))

        self.assertEqual(1, len(notifier.messages))
        self.assertIn("운영 저장공간 제한 심각", notifier.messages[0])
        self.assertIn("TypeDB", notifier.messages[0])

    def test_storage_capacity_uses_the_operations_delivery_channel_and_presentation(self):
        self.assertTrue(is_operations_delivery_message_type(OPERATIONAL_STORAGE_CAPACITY))
        presentation = operational_notification_presentation(
            OPERATIONAL_STORAGE_CAPACITY,
            {"messageType": OPERATIONAL_STORAGE_CAPACITY, "state": "critical"},
        )
        self.assertEqual("🚨", presentation.icon)

    def test_inventory_reports_typedb_wal_and_checkpoint_sizes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wal = root / "typedb-data" / "db" / "wal"
            checkpoint = root / "typedb-data" / "db" / "checkpoint"
            wal.mkdir(parents=True)
            checkpoint.mkdir(parents=True)
            (wal / "segment").write_bytes(b"w" * (2 * 1024 * 1024))
            (checkpoint / "segment").write_bytes(b"c" * (3 * 1024 * 1024))
            inventory = operational_storage_inventory(
                {
                    "typedbDataMaxSizeMb": "4096",
                    "operationalMySqlDataMaxSizeMb": "4096",
                    "operationalLogMaxSizeMb": "512",
                },
                data_path=root,
                disk_usage_provider=lambda _path: SimpleNamespace(
                    free=80 * 1024 * 1024 * 1024,
                    total=100 * 1024 * 1024 * 1024,
                ),
            )

        self.assertGreater(inventory["typedbSizeMb"], 0)
        self.assertGreater(inventory["typedbWalMb"], 0)
        self.assertGreater(inventory["typedbCheckpointMb"], 0)


if __name__ == "__main__":
    unittest.main()
