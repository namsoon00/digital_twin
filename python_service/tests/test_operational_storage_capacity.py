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
from digital_twin.infrastructure.operational_storage_guard import (
    accelerated_mysql_cleanup_settings,
    operational_storage_inventory,
)


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
            "typedbSizeMb": 3700,
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

    def disk_snapshot(self, free_mb):
        return {
            **self.healthy_snapshot(),
            "freeMb": free_mb,
            "freePercent": round(free_mb / (100 * 1024) * 100, 2),
            "cleanupMode": "accelerated" if free_mb < 48 * 1024 else "normal",
        }

    def test_state_change_reminder_and_recovery_are_durable(self):
        current = [datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)]
        store = StateStore()
        service = OperationalStorageCapacityService(
            store=store,
            settings={"operationalStorageLimitedAlertReminderMinutes": "60"},
            now_provider=lambda: current[0],
        )

        first, first_event = service.record(self.limited_snapshot())
        self.assertEqual("limited", first["state"])
        self.assertEqual("threshold-crossed", first["alertKind"])
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

    def test_runtime_write_failure_bypasses_capacity_reminders_with_a_short_dedicated_cooldown(self):
        current = [datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)]
        service = OperationalStorageCapacityService(
            store=StateStore(),
            settings={"operationalStorageRuntimeFailureCooldownMinutes": "5"},
            now_provider=lambda: current[0],
        )
        service.record(self.disk_snapshot(30 * 1024))

        current[0] += timedelta(minutes=10)
        immediate, immediate_event = service.record(self.disk_snapshot(30 * 1024), force_alert=True)
        self.assertTrue(immediate["alertRequired"])
        self.assertEqual("runtime-write-failure", immediate["alertKind"])
        self.assertIsNotNone(immediate_event)

        current[0] += timedelta(minutes=2)
        repeated, repeated_event = service.record(self.disk_snapshot(30 * 1024), force_alert=True)
        self.assertFalse(repeated["alertRequired"])
        self.assertIsNone(repeated_event)

        current[0] += timedelta(minutes=4)
        due, due_event = service.record(self.disk_snapshot(30 * 1024), force_alert=True)
        self.assertTrue(due["alertRequired"])
        self.assertIsNotNone(due_event)

    def test_typedb_auto_rotation_uses_a_distinct_forced_incident(self):
        current = [datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)]
        service = OperationalStorageCapacityService(
            store=StateStore(),
            settings={"operationalStorageRuntimeFailureCooldownMinutes": "5"},
            now_provider=lambda: current[0],
        )

        first, first_event = service.record(
            self.limited_snapshot(),
            force_alert=True,
            force_alert_kind="typedb-auto-rotation",
        )

        self.assertTrue(first["alertRequired"])
        self.assertEqual("typedb-auto-rotation", first["alertKind"])
        self.assertEqual(first["checkedAt"], first["lastCapacityRotationAlertAt"])
        self.assertIsNotNone(first_event)

        current[0] += timedelta(minutes=2)
        repeated, repeated_event = service.record(
            self.limited_snapshot(),
            force_alert=True,
            force_alert_kind="typedb-auto-rotation",
        )
        self.assertFalse(repeated["alertRequired"])
        self.assertIsNone(repeated_event)

        failed, failed_event = service.record(
            self.limited_snapshot(),
            force_alert=True,
            force_alert_kind="typedb-auto-rotation-failed",
        )
        self.assertTrue(failed["alertRequired"])
        self.assertEqual("typedb-auto-rotation-failed", failed["alertKind"])
        self.assertEqual(failed["checkedAt"], failed["lastCapacityRotationFailureAlertAt"])
        self.assertIsNotNone(failed_event)

    def test_failed_rotation_separates_maintenance_from_healthy_capacity(self):
        service = OperationalStorageCapacityService(
            store=StateStore(),
            now_provider=lambda: datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc),
        )

        failed, event = service.record(
            self.healthy_snapshot(),
            force_alert=True,
            force_alert_kind="typedb-auto-rotation-failed",
        )

        self.assertEqual("healthy", failed["capacityState"])
        self.assertEqual("warning", failed["state"])
        self.assertEqual("failed", failed["maintenanceState"])
        self.assertTrue(failed["activeStorePreserved"])
        self.assertIsNotNone(event)

    def test_internal_cleanup_warning_does_not_page_until_the_human_threshold(self):
        current = [datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)]
        service = OperationalStorageCapacityService(
            store=StateStore(),
            now_provider=lambda: current[0],
        )

        internal_only, internal_event = service.record(self.disk_snapshot(30 * 1024))
        self.assertEqual("warning", internal_only["state"])
        self.assertFalse(internal_only["alertEligible"])
        self.assertFalse(internal_only["alertRequired"])
        self.assertIsNone(internal_event)

        current[0] += timedelta(minutes=2)
        actionable, actionable_event = service.record(self.disk_snapshot(23 * 1024))
        self.assertEqual("warning", actionable["state"])
        self.assertTrue(actionable["alertEligible"])
        self.assertEqual("threshold-crossed", actionable["alertKind"])
        self.assertIsNotNone(actionable_event)

    def test_forecast_pages_before_the_protected_reserve_is_breached(self):
        current = [datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)]
        service = OperationalStorageCapacityService(
            store=StateStore(),
            settings={
                "operationalStorageForecastMinimumSamples": "3",
                "operationalStorageForecastMinimumElapsedMinutes": "5",
            },
            now_provider=lambda: current[0],
        )
        service.record(self.disk_snapshot(50 * 1024))
        current[0] += timedelta(minutes=3)
        service.record(self.disk_snapshot(40 * 1024))
        current[0] += timedelta(minutes=3)
        forecast, forecast_event = service.record(self.disk_snapshot(30 * 1024))

        self.assertEqual("warning", forecast["state"])
        self.assertTrue(forecast["forecastDetected"])
        self.assertEqual("forecast", forecast["alertKind"])
        self.assertIsNotNone(forecast_event)
        self.assertLess(forecast["forecastEtaMinutes"], 60)

    def test_limited_state_realerts_for_material_worsening_before_the_four_hour_reminder(self):
        current = [datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)]
        service = OperationalStorageCapacityService(
            store=StateStore(),
            settings={"operationalStorageLimitedAlertReminderMinutes": "240"},
            now_provider=lambda: current[0],
        )
        first, first_event = service.record(self.disk_snapshot(11 * 1024))
        self.assertEqual("limited", first["state"])
        self.assertIsNotNone(first_event)

        current[0] += timedelta(minutes=30)
        unchanged, unchanged_event = service.record(self.disk_snapshot(10 * 1024))
        self.assertFalse(unchanged["alertRequired"])
        self.assertIsNone(unchanged_event)

        current[0] += timedelta(minutes=30)
        worsened, worsened_event = service.record(self.disk_snapshot(8 * 1024))
        self.assertEqual("material-worsening", worsened["alertKind"])
        self.assertIsNotNone(worsened_event)

        current[0] += timedelta(minutes=241)
        reminder, reminder_event = service.record(self.disk_snapshot(8 * 1024))
        self.assertEqual("reminder", reminder["alertKind"])
        self.assertIsNotNone(reminder_event)

    def test_component_usage_only_pages_at_the_human_alert_threshold(self):
        current = [datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)]
        service = OperationalStorageCapacityService(store=StateStore(), now_provider=lambda: current[0])

        internal = self.disk_snapshot(60 * 1024)
        internal["typedbSizeMb"] = 3400
        warning, warning_event = service.record(internal)
        self.assertEqual("warning", warning["state"])
        self.assertFalse(warning["alertRequired"])
        self.assertIsNone(warning_event)

        current[0] += timedelta(minutes=2)
        alert = self.disk_snapshot(60 * 1024)
        alert["typedbSizeMb"] = 3700
        limited, limited_event = service.record(alert)
        self.assertEqual("limited", limited["state"])
        self.assertTrue(limited["alertRequired"])
        self.assertIsNotNone(limited_event)

        current[0] += timedelta(minutes=2)
        critical = self.disk_snapshot(60 * 1024)
        critical["typedbSizeMb"] = 3900
        critical_state, critical_event = service.record(critical)
        self.assertEqual("critical", critical_state["state"])
        self.assertEqual("state-changed", critical_state["alertKind"])
        self.assertIsNotNone(critical_event)

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
            "alertFreeMb": 24576,
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
        self.assertIn("운영 저장공간 쓰기 실패", notifier.messages[0])
        self.assertIn("TypeDB", notifier.messages[0])

    def test_auto_rotation_notification_names_the_rebuild(self):
        payload = {
            **self.limited_snapshot(),
            "state": "limited",
            "previousState": "warning",
            "alertRequired": True,
            "alertKind": "typedb-auto-rotation",
            "checkedAt": "2026-08-02T09:00:00Z",
            "warningFreeMb": 49152,
            "alertFreeMb": 24576,
            "minimumFreeMb": 32768,
            "criticalFreeMb": 20480,
            "limitingComponents": [{"component": "typedb"}],
            "suggestedAction": "TypeDB 안전 재구축을 실행하세요.",
        }
        queue = Queue()
        OperationalStorageCapacityNotificationEnqueuer(queue).handle(
            operational_storage_capacity_changed_event(payload)
        )

        self.assertEqual(1, len(queue.jobs))
        self.assertIn("TypeDB 안전 재구축 시작", queue.jobs[0].text)

    def test_mysql_capacity_message_explains_the_policy_limit_and_history_protection(self):
        payload = {
            **self.healthy_snapshot(),
            "state": "limited",
            "previousState": "warning",
            "alertRequired": True,
            "alertKind": "threshold-crossed",
            "checkedAt": "2026-08-13T09:00:00Z",
            "warningFreeMb": 49152,
            "alertFreeMb": 24576,
            "minimumFreeMb": 12288,
            "criticalFreeMb": 6144,
            "mysqlSizeMb": 7373,
            "mysqlLimitMb": 8192,
            "mysqlUsagePercent": 90.0,
            "mysqlCapacityStage": "restricted",
            "limitingComponents": [{"component": "mysql"}],
            "suggestedAction": "MySQL 이력 정리를 가속하세요.",
        }

        context = OperationalStorageCapacityNotificationEnqueuer(Queue()).context(
            payload,
            operational_storage_capacity_changed_event(payload),
        )

        self.assertEqual("MySQL 저장공간 쓰기 제한", context["title"])
        self.assertIn("8192MB (90.0%)", context["readableMessage"])
        self.assertIn("비필수 쓰기 제한", context["readableMessage"])
        self.assertIn("핵심 이력 보호", context["readableMessage"])

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

    def test_mysql_uses_a_sixteen_gigabyte_default_and_starts_cleanup_at_seventy_percent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mysql-runtime").mkdir()

            def size(path):
                return int(11.5 * 1024 * 1024 * 1024) if Path(path).name == "mysql-runtime" else 0

            inventory = operational_storage_inventory(
                {},
                data_path=root,
                disk_usage_provider=lambda _path: SimpleNamespace(
                    free=80 * 1024 * 1024 * 1024,
                    total=100 * 1024 * 1024 * 1024,
                ),
                size_provider=size,
                mysql_metadata_provider=lambda _settings: {
                    "mysqlMetadataStatus": "available",
                    "mysqlLiveDataMb": 1200.0,
                    "mysqlReclaimableMb": 800.0,
                },
            )

        self.assertEqual(16384, inventory["mysqlLimitMb"])
        self.assertEqual(71.9, inventory["mysqlUsagePercent"])
        self.assertEqual("maintenance", inventory["mysqlCapacityStage"])
        self.assertEqual("accelerated", inventory["cleanupMode"])
        self.assertEqual(1200.0, inventory["mysqlLiveDataMb"])
        self.assertEqual(800.0, inventory["mysqlReclaimableMb"])
        effective = accelerated_mysql_cleanup_settings({}, inventory)
        self.assertEqual("500", effective["_effectiveMysqlMinimalRetentionBatchSize"])

    def test_mysql_ninety_percent_stage_blocks_only_nonessential_writes(self):
        current = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
        snapshot = {
            **self.healthy_snapshot(),
            "mysqlSizeMb": 7.3 * 1024,
            "mysqlLimitMb": 8 * 1024,
            "mysqlUsagePercent": 91.2,
            "mysqlCapacityStage": "restricted",
            "nonEssentialWritesAllowed": True,
            "cleanupMode": "accelerated",
        }

        health, _event = OperationalStorageCapacityService(
            store=StateStore(),
            now_provider=lambda: current,
        ).record(snapshot)

        self.assertEqual("limited", health["state"])
        self.assertFalse(health["nonEssentialWritesAllowed"])
        self.assertFalse(health["coreWritesOnly"])

    def test_mysql_hard_limit_marks_core_only_without_disabling_core_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mysql-runtime").mkdir()

            def size(path):
                return 16 * 1024 * 1024 * 1024 if Path(path).name == "mysql-runtime" else 0

            inventory = operational_storage_inventory(
                {},
                data_path=root,
                disk_usage_provider=lambda _path: SimpleNamespace(
                    free=80 * 1024 * 1024 * 1024,
                    total=100 * 1024 * 1024 * 1024,
                ),
                size_provider=size,
            )

        self.assertEqual("core-only", inventory["mysqlCapacityStage"])
        self.assertTrue(inventory["coreWritesOnly"])
        self.assertFalse(inventory["nonEssentialWritesAllowed"])
        self.assertTrue(inventory["ready"])


if __name__ == "__main__":
    unittest.main()
