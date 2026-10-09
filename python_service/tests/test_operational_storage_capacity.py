import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from digital_twin.platform.application.operational_storage_capacity_service import (
    OperationalStorageCapacityNotificationEnqueuer,
    OperationalStorageCapacityService,
)
from digital_twin.platform.domain.events import operational_storage_capacity_changed_event
from digital_twin.platform.domain.operational_storage_capacity import (
    operational_storage_capacity_read_model,
)
from digital_twin.modules.notifications.domain.message_types import (
    OPERATIONAL_STORAGE_CAPACITY,
    is_operations_delivery_message_type,
)
from digital_twin.modules.notifications.domain.operational_notification_presentation import operational_notification_presentation
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
    def test_operator_read_model_exposes_forecast_cleanup_and_protected_history(self):
        result = operational_storage_capacity_read_model(
            {
                "cleanupMode": "accelerated",
                "mysqlReclaimableMb": 6144,
                "logSizeMb": 700,
                "logLimitMb": 512,
                "mysqlCapacityStage": "warning",
            },
            {
                "state": "warning",
                "checkedAt": "2026-08-30T09:00:00Z",
                "forecastDetected": True,
                "forecastSampleCount": 4,
                "forecastEtaMinutes": 95,
                "forecastThresholdMb": 24576,
                "forecastDepletionRateMbPerMinute": 12.5,
            },
        )

        self.assertEqual("warning", result["capacityState"])
        self.assertTrue(result["forecast"]["available"])
        self.assertEqual(95, result["forecast"]["etaMinutes"])
        self.assertTrue(result["cleanupPlan"]["automatic"])
        self.assertEqual(6332, result["cleanupPlan"]["estimatedReclaimableMb"])
        self.assertIn("투자 결정과 판단 이력", result["cleanupPlan"]["protectedData"])

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

    def test_failed_rotation_keeps_the_concrete_validation_reason(self):
        service = OperationalStorageCapacityService(
            store=StateStore(),
            now_provider=lambda: datetime(2026, 8, 25, 22, 15, tzinfo=timezone.utc),
        )
        snapshot = {
            **self.healthy_snapshot(),
            "maintenanceFailureReason": "후보 작성자 잠금 범위 충돌",
        }

        failed, event = service.record(
            snapshot,
            force_alert=True,
            force_alert_kind="typedb-auto-rotation-failed",
        )

        self.assertEqual("후보 검증 실패: 후보 작성자 잠금 범위 충돌", failed["maintenanceReason"])
        self.assertEqual("후보 작성자 잠금 범위 충돌", failed["maintenanceFailureReason"])
        self.assertIsNotNone(event)

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

    def test_mysql_metadata_failure_does_not_report_false_zero_sizes(self):
        payload = {
            **self.healthy_snapshot(),
            "state": "warning",
            "previousState": "warning",
            "alertRequired": True,
            "alertKind": "typedb-auto-rotation-failed",
            "checkedAt": "2026-08-25T22:15:28Z",
            "mysqlMetadataStatus": "unavailable",
            "mysqlMetadataReason": "MySQL connection refused during restart",
        }

        context = OperationalStorageCapacityNotificationEnqueuer(Queue()).context(
            payload,
            operational_storage_capacity_changed_event(payload),
        )

        self.assertIn("메타데이터 조회 실패", context["readableMessage"])
        self.assertIn("MySQL connection refused during restart", context["readableMessage"])
        self.assertNotIn("데이터·인덱스 0MB", context["readableMessage"])

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

    def test_rotation_capacity_keeps_retired_candidate_and_failed_bytes_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sizes = {"typedb-data": 2, "typedb-data-retired-123": 19,
                     "typedb-data-candidate": 1, "typedb-data-failed-122": 3}
            for name in sizes:
                (root / name).mkdir()
            (root / "typedb-data-retired-alias").symlink_to(root / "typedb-data-retired-123")
            inventory = operational_storage_inventory({}, data_path=root,
                disk_usage_provider=lambda _: SimpleNamespace(free=80 * 1024**3, total=100 * 1024**3),
                size_provider=lambda path: sizes.get(path.name, 0) * 1024**2,
                mysql_metadata_provider=lambda _: {})
            health, _ = OperationalStorageCapacityService(store=StateStore()).record(inventory)
        self.assertEqual(2, health["typedbSizeMb"])
        self.assertEqual(19, health["typedbRetiredSizeMb"])
        self.assertEqual(1, health["typedbCandidateSizeMb"])
        self.assertEqual(3, health["typedbFailedSizeMb"])
        self.assertEqual(25, health["typedbTotalSizeMb"])

    def test_mysql_hard_limit_marks_core_only_without_disabling_core_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mysql-runtime").mkdir()

            def size(path):
                return 16 * 1024 * 1024 * 1024 if Path(path).name == "mysql-runtime" else 0

            inventory = operational_storage_inventory(
                {"operationalMySqlDataMaxSizeMb": "16384"},
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

class StorageForecastRegressionTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 9, 11, 30, tzinfo=timezone.utc)

    def evaluate(self, minute, free, previous=None):
        from digital_twin.platform.domain.operational_storage_capacity import evaluate_operational_storage_capacity
        return evaluate_operational_storage_capacity(
            {"freeMb": free}, previous=previous, now=self.start + timedelta(minutes=minute)
        )

    def sequence(self, values):
        result = {}
        results = []
        for minute, free in enumerate(values):
            result = self.evaluate(minute, free, result)
            results.append(result)
        return results

    def test_startup_burst_then_recovery_does_not_page(self):
        # Reproduce a short startup allocation followed by recovered space.
        results = self.sequence([68900, 68700, 66300, 64600, 64400, 63600,
                                 62300, 62300, 63200, 65000, 66000])
        self.assertFalse(any(row["alertRequired"] for row in results))

    def test_single_allocation_then_plateau_is_not_continuing_depletion(self):
        results = self.sequence([90000] * 6 + [65000] * 20)
        self.assertFalse(any(row["alertRequired"] for row in results))
        self.assertEqual(0, results[-1]["forecastDepletionRateMbPerMinute"])

    def test_sustained_depletion_confirms_before_the_reserve_is_reached(self):
        results = self.sequence([80000 - minute * 2000 for minute in range(12)])
        self.assertTrue(results[5]["forecastConfirmationPending"])
        self.assertFalse(any(row["alertRequired"] for row in results[:10]))
        confirmed = results[10]
        self.assertEqual("forecast", confirmed["alertKind"])
        self.assertGreater(confirmed["freeMb"], confirmed["alertFreeMb"])
        self.assertTrue(confirmed["nonEssentialWritesAllowed"])
        self.assertFalse(results[11]["alertRequired"])

    def test_immediate_thresholds_do_not_wait_for_forecast_confirmation(self):
        pending = self.sequence([80000 - minute * 2000 for minute in range(6)])[-1]
        limited = self.evaluate(6, 11000, pending)
        self.assertEqual("limited", limited["state"])
        self.assertTrue(limited["alertRequired"])
        self.assertFalse(limited["nonEssentialWritesAllowed"])
        critical = self.evaluate(7, 5000, limited)
        self.assertEqual("critical", critical["state"])
        self.assertTrue(critical["alertRequired"])

    def test_observation_gap_requires_new_confirmation(self):
        pending = self.sequence([80000 - minute * 2000 for minute in range(6)])[-1]
        after_gap = self.evaluate(16, 50000, pending)
        self.assertFalse(after_gap["forecastAvailable"])
        self.assertFalse(after_gap["alertRequired"])
        self.assertEqual("", after_gap["forecastCandidateSince"])

    def test_forecast_recovery_has_eta_margin(self):
        from unittest.mock import patch
        prior = self.sequence([80000 - minute * 2000 for minute in range(11)])[-1]
        def forecast(eta):
            return dict(available=True, sampleCount=12, elapsedMinutes=11,
                        depletionRateMbPerMinute=700, etaMinutes=eta,
                        projectedFreeMb=10000, detected=eta <= 60)
        target = 'digital_twin.platform.domain.operational_storage_capacity._depletion_forecast'
        with patch(target, return_value=forecast(65)):
            pending = self.evaluate(11, 60000, prior)
        self.assertTrue(pending["forecastRecoveryPending"])
        self.assertFalse(pending["alertRequired"])
        with patch(target, return_value=forecast(59)):
            crossed = self.evaluate(12, 60000, pending)
        self.assertFalse(crossed["alertRequired"])
        with patch(target, return_value=forecast(80)):
            recovered = self.evaluate(13, 60000, crossed)
        self.assertEqual("recovered", recovered["alertKind"])
        context = OperationalStorageCapacityNotificationEnqueuer(Queue()).context(
            recovered, operational_storage_capacity_changed_event(recovered))
        self.assertEqual("운영 저장공간 소진 예상 해소", context["title"])

    def test_sampling_gap_cannot_prove_an_existing_forecast_incident_recovered(self):
        prior = self.sequence([80000 - minute * 2000 for minute in range(11)])[-1]
        after_gap = self.evaluate(20, 58000, prior)
        self.assertFalse(after_gap["forecastAvailable"])
        self.assertTrue(after_gap["forecastRecoveryPending"])
        self.assertTrue(after_gap["alertEligible"])
        self.assertFalse(after_gap["alertRequired"])

    def test_forecast_confirmation_survives_service_recreation(self):
        store = StateStore()
        result = {}
        for minute in range(11):
            service = OperationalStorageCapacityService(
                store=store, now_provider=lambda: self.start + timedelta(minutes=minute))
            result, event = service.record({"freeMb": 80000 - minute * 2000})
        self.assertEqual("forecast", result["alertKind"])
        self.assertIsNotNone(event)
        context = OperationalStorageCapacityNotificationEnqueuer(Queue()).context(result, event)
        self.assertIn("감소 추세에 따른 사전 알림", context["readableMessage"])

    def test_future_samples_are_discarded_after_clock_moves_back(self):
        previous = {"checkedAt": "2026-10-10T00:00:00Z", "freeMb": 90000,
                    "recentFreeSamples": [{"at": "2026-10-10T00:00:00Z", "freeMb": 90000}]}
        result = self.evaluate(0, 60000, previous)
        self.assertEqual(1, len(result["recentFreeSamples"]))
        self.assertFalse(result["forecastAvailable"])
        self.assertFalse(result["alertRequired"])

    def test_read_model_does_not_claim_incomplete_forecast_is_available(self):
        result = operational_storage_capacity_read_model(
            {}, {"forecastAvailable": False, "forecastSampleCount": 3})
        self.assertFalse(result["forecast"]["available"])


class StorageIncidentRegressionTests(unittest.TestCase):
    def evaluate(self, free, logs=464, previous=None):
        from digital_twin.platform.domain.operational_storage_capacity import evaluate_operational_storage_capacity
        return evaluate_operational_storage_capacity(
            dict(freeMb=free,logSizeMb=logs,logLimitMb=512), previous,
            dict(operationalStorageForecastEnabled=False),
            datetime(2026,10,5,12,0,tzinfo=timezone.utc))

    def test_more_free_space_is_not_component_growth(self):
        first = self.evaluate(18838.8)
        improved = self.evaluate(24044.2, 464.8, first)
        self.assertFalse(improved['alertRequired'])

    def test_disk_recovery_has_margin_and_does_not_flap(self):
        first = self.evaluate(24000,0)
        pending = self.evaluate(24700,0,first)
        self.assertTrue(pending['recoveryPending'])
        self.assertFalse(pending['alertRequired'])
        crossed = self.evaluate(24000,0,pending)
        self.assertFalse(crossed['alertRequired'])
        recovered = self.evaluate(27000,0,crossed)
        self.assertEqual('recovered',recovered['alertKind'])

    def test_component_recovery_preserves_incident_severity_until_margin(self):
        first = self.evaluate(60000,465)
        pending = self.evaluate(60000,459,first)
        crossed = self.evaluate(60000,465,pending)
        self.assertFalse(crossed['alertRequired'])
        self.assertEqual('recovered',self.evaluate(60000,430,crossed)['alertKind'])
        self.assertEqual('state-changed',self.evaluate(60000,500,crossed)['alertKind'])


class OperationalLogRetentionTests(unittest.TestCase):
    def test_rotation_preserves_inode_append_writer_and_archived_content(self):
        import gzip
        from digital_twin.infrastructure.operational_logs import maintain_operational_logs
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'python-worker.log'
            body = b'operational diagnostic\n' * 60000
            path.write_bytes(body)
            inode = path.stat().st_ino
            with path.open('ab') as writer:
                result = maintain_operational_logs(directory, [path], 2*1024*1024)
                writer.write(b'next cycle\n')
            self.assertEqual(1,result['rotated'])
            self.assertEqual(inode,path.stat().st_ino)
            self.assertEqual(b'next cycle\n',path.read_bytes())
            archived = next((Path(directory)/'operational-log-archives').glob('*.gz'))
            self.assertEqual(body,gzip.decompress(archived.read_bytes()))

    def test_unowned_symlink_and_audit_files_are_untouched(self):
        from digital_twin.infrastructure.operational_logs import maintain_operational_logs
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = root/'samples.jsonl'; audit.write_bytes(b'x'*1100000)
            link = root/'worker.log'; link.symlink_to(audit)
            result = maintain_operational_logs(root,[audit,link],1024*1024)
            self.assertEqual(0,result['rotated'])
            self.assertEqual(1100000,audit.stat().st_size)

    def test_old_archives_are_removed_but_other_files_are_preserved(self):
        import os
        import time
        from digital_twin.infrastructure.operational_logs import maintain_operational_logs
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); archive=root/'operational-log-archives'; archive.mkdir()
            old=archive/'worker.log.1.gz'; old.write_bytes(b'old')
            protected=archive/'audit.json'; protected.write_bytes(b'keep')
            os.utime(old,(time.time()-8*86400,)*2)
            maintain_operational_logs(root,[],1024*1024)
            self.assertFalse(old.exists())
            self.assertEqual(b'keep',protected.read_bytes())

    def test_failed_archive_keeps_original_log(self):
        from unittest.mock import patch
        from digital_twin.infrastructure.operational_logs import maintain_operational_logs
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'worker.log'; body=b'x'*1100000; path.write_bytes(body)
            with patch('digital_twin.infrastructure.operational_logs.gzip.GzipFile',side_effect=OSError('disk full')):
                result=maintain_operational_logs(directory,[path],1024*1024)
            self.assertEqual(body,path.read_bytes())
            self.assertEqual(1,len(result['failures']))
            self.assertFalse(list((Path(directory)/'operational-log-archives').glob('*.pending')))

    def test_inventory_counts_compressed_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            archive=root/'operational-log-archives';archive.mkdir()
            (archive/'worker.log.1.gz').write_bytes(b'x'*(2*1024*1024))
            inventory=operational_storage_inventory({},data_path=root,mysql_metadata_provider=lambda _: {})
            self.assertEqual(2,inventory['logSizeMb'])
