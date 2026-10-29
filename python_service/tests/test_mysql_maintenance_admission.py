import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from digital_twin.platform.domain.mysql_maintenance_admission import mysql_maintenance_admission
from digital_twin.infrastructure.mysql_realtime_workload_guard import MySQLRealtimeWorkloadGuard


class MySQLMaintenanceAdmissionTests(unittest.TestCase):
    def _assert_capacity_turn_never_alerts_from_fallback_limits(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from digital_twin.infrastructure import cli, settings as settings_module
        from digital_twin.infrastructure.mysql_operational import MySQLRuntimeSettingsStore

        # Strict operational reads do not disguise a database failure as the
        # legacy file or defaults. Other settings callers retain their fallback.
        with patch.object(MySQLRuntimeSettingsStore, 'load', side_effect=TimeoutError), \
             patch.object(MySQLRuntimeSettingsStore, '__init__', return_value=None) as init, \
             patch.object(settings_module, 'read_json', return_value={'fallback': 'yes'}) as fallback:
            with self.assertRaises(TimeoutError):
                settings_module.read_settings_store(fast_operational_read=True, require_store_read=True)
            fallback.assert_not_called()
            self.assertEqual('1', init.call_args.args[0]['_skipOperationalSchemaBootstrap'])
            self.assertEqual({'fallback': 'yes'}, settings_module.read_settings_store(fast_operational_read=True))

        startup = {'operationalMySqlDataMaxSizeMb': '32768', 'typedbDataMaxSizeMb': '24576'}
        saved = {**startup, 'operationalMySqlDataMaxSizeMb': '40960', 'typedbDataMaxSizeMb': '32768'}
        changed = {**saved, 'operationalMySqlDataMaxSizeMb': '45056'}
        scheduler, results = Mock(), []
        admission = SimpleNamespace(run_cleanup=False, deferral_started_at=1,
            status='deferred', reason='busy', deferred_seconds=0, next_interval_seconds=30)
        with patch.object(cli, 'runtime_settings', side_effect=[startup, saved, changed, TimeoutError]) as read, \
             patch.object(cli, 'build_ontology_reasoning_queue_probe', return_value=lambda: {'effectivePendingCount': 1}), \
             patch.object(cli, 'operational_storage_inventory', side_effect=lambda config: {'mysqlLimitMb': int(config['operationalMySqlDataMaxSizeMb'])}) as inventory, \
             patch.object(cli, 'observe_operational_storage_capacity') as observe, \
             patch('digital_twin.platform.domain.mysql_maintenance_admission.mysql_maintenance_admission', return_value=admission), \
             patch.object(cli, 'run_mysql_operational_cleanup') as cleanup, \
             patch.object(cli, 'OperationalHistoryRetentionScheduler', return_value=scheduler) as factory:
            scheduler.run_forever.side_effect = lambda: results.extend(factory.call_args.args[0]() for _ in range(3))
            cli.maintenance_command(SimpleNamespace(maintenance_action='watch', interval='60'))
        self.assertEqual([saved, changed], [call.args[0] for call in inventory.call_args_list])
        self.assertEqual([40960, 45056], [call.kwargs['snapshot']['mysqlLimitMb'] for call in observe.call_args_list])
        self.assertEqual('capacity-policy-unavailable', results[-1]['skipped'])
        self.assertEqual('TimeoutError', results[-1]['settingsReadErrorType'])
        self.assertEqual(60, results[-1]['nextIntervalSeconds'])
        self.assertTrue(all(call.kwargs == {'fast_operational_read': True, 'require_store_read': True}
                            for call in read.call_args_list[1:]))
        cleanup.assert_not_called()

    def test_cleanup_timeout_does_not_replay_bounded_or_completed_retention(self):
        from unittest.mock import Mock, patch
        from digital_twin.infrastructure import cli
        for include_legacy in (False, True):
            with self.subTest(include_legacy=include_legacy):
                error = RuntimeError(2013, "read operation timed out")
                store, service = Mock(), Mock()
                store.connect.return_value.__enter__ = Mock(return_value=Mock())
                store.connect.return_value.__exit__ = Mock(return_value=False)
                inventory = [{}, error] if include_legacy else [{}]
                service.run_once.side_effect = None if include_legacy else error
                service.run_once.return_value = {"deleted": 2}
                with patch.object(cli, "operational_storage_inventory", side_effect=inventory), \
                     patch.object(cli, "observe_operational_storage_capacity"), \
                     patch.object(cli, "MySQLOperationalConnection", return_value=store) as factory, \
                     patch.object(cli, "apply_mysql_operational_history_retention", return_value={}), \
                     patch.object(cli, "MySQLMinimalRetentionService", return_value=service), \
                     patch("digital_twin.modules.outcomes.infrastructure.mysql_decision_calibration_inputs.MySQLDecisionCalibrationInputStore"), \
                     patch.object(cli.time, "sleep") as sleep, \
                     self.assertRaises(RuntimeError) as raised:
                    cli.run_mysql_operational_cleanup({}, include_legacy=include_legacy)
                self.assertIs(error, raised.exception)
                self.assertEqual("capacity-refresh" if include_legacy else "minimal-retention",
                                 error.orbit_maintenance_stage)
                factory.assert_called_once()
                service.run_once.assert_called_once()
                sleep.assert_not_called()

    def test_retention_lock_distinguishes_contention_from_database_failure(self):
        from unittest.mock import Mock
        from digital_twin.infrastructure.mysql_minimal_retention import MySQLMinimalRetentionRepository
        connection = Mock()
        repository = MySQLMinimalRetentionRepository(connection)
        for acquired, expected in ((0, False), (1, True)):
            connection.execute.return_value.fetchone.return_value = {"acquired": acquired}
            self.assertEqual(expected, repository._acquire_lock())
        connection.execute.return_value.fetchone.return_value = {"acquired": None}
        with self.assertRaisesRegex(RuntimeError, "returned NULL"):
            repository._acquire_lock()
        error = RuntimeError(2013, "read operation timed out")
        connection.execute.side_effect = error
        with self.assertRaises(RuntimeError) as raised:
            repository._acquire_lock()
        self.assertIs(error, raised.exception)

    def test_maintenance_alert_preserves_safe_query_and_policy_context(self):
        from unittest.mock import Mock, patch
        from digital_twin.infrastructure import schedulers
        error = RuntimeError(2013, "read operation timed out")
        error.orbit_mysql_statement = "DELETE monitor_snapshot_history"
        error.orbit_mysql_elapsed_ms = 10000
        error.orbit_retention_policy = "snapshotHistory"
        error.orbit_maintenance_stage = "minimal-retention"
        scheduler = schedulers.OperationalHistoryRetentionScheduler(Mock(side_effect=error), 60, Mock())
        with patch.object(schedulers, "install_stop_handlers"), \
             patch.object(schedulers, "wait_until_running", side_effect=lambda *a: scheduler.stop()), \
             patch.object(schedulers, "report_runtime_error") as report:
            scheduler.run_forever()
        self.assertIs(error, report.call_args.args[2])
        stage = report.call_args.args[3]
        for expected in ("stage=minimal-retention", "policy=snapshotHistory",
                         "mysql=DELETE monitor_snapshot_history", "query=10.0s"):
            self.assertIn(expected, stage)

    def test_failed_lock_does_not_restart_overdue_cleanup_clock(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from digital_twin.infrastructure import cli
        scheduler, guard = Mock(), Mock()
        guard.maintenance_turn.side_effect = [nullcontext(SimpleNamespace(acquired=False)),
                                               nullcontext(SimpleNamespace(acquired=True))]
        with patch.object(cli, 'runtime_settings', return_value={}), \
             patch.object(cli, 'build_ontology_reasoning_queue_probe', return_value=lambda: {'effectivePendingCount': 3}), \
             patch.object(cli, 'operational_storage_inventory', return_value={}), \
             patch.object(cli, 'observe_operational_storage_capacity'), \
             patch.object(cli, 'MySQLRealtimeWorkloadGuard', return_value=guard), \
             patch.object(cli.time, 'time', side_effect=[1000, 1901, 1961, 1962]), \
             patch.object(cli, 'run_mysql_operational_cleanup', return_value={'minimalRetention': {'status': 'ok'}}) as cleanup, \
             patch.object(cli, 'OperationalHistoryRetentionScheduler', return_value=scheduler) as factory:
            scheduler.run_forever.side_effect = lambda: [factory.call_args.args[0]() for _ in range(3)]
            cli.maintenance_command(SimpleNamespace(maintenance_action='watch', interval='60'))
        cleanup.assert_called_once_with({}, include_legacy=False)
        self.assertEqual([60, 60], [call.kwargs['wait_seconds'] for call in guard.maintenance_turn.call_args_list])

    def test_overdue_maintenance_reserves_the_gap_before_another_monitor(self):
        import threading
        from contextlib import contextmanager
        from unittest.mock import patch
        with TemporaryDirectory() as directory:
            guard = MySQLRealtimeWorkloadGuard(Path(directory) / 'work.lock')
            maintenance = MySQLRealtimeWorkloadGuard(guard.path)
            gate_held, entered, release, monitor_entered = (threading.Event() for _ in range(4))
            errors = []
            original = maintenance._acquire
            @contextmanager
            def traced(**kwargs):
                with original(**kwargs) as lease:
                    if kwargs['role'] == 'admission' and lease.acquired:
                        gate_held.set()
                    yield lease
            def cleanup():
                try:
                    with maintenance.maintenance_turn(wait_seconds=2) as lease:
                        if not lease.acquired:
                            raise RuntimeError('maintenance lost reserved turn')
                        entered.set(); release.wait(2)
                except Exception as error:
                    errors.append(error)
            def monitor():
                with guard.monitor_cycle():
                    monitor_entered.set()
            worker, follower = threading.Thread(target=cleanup), threading.Thread(target=monitor)
            with patch.object(maintenance, '_acquire', traced):
                try:
                    with guard.monitor_cycle():
                        worker.start(); self.assertTrue(gate_held.wait(1))
                        follower.start(); self.assertFalse(monitor_entered.wait(0.05))
                    self.assertTrue(entered.wait(1))
                    self.assertFalse(monitor_entered.is_set())
                finally:
                    release.set()
                    worker.join(3)
                    if follower.ident:
                        follower.join(3)
            self.assertFalse(errors)
            self.assertFalse(worker.is_alive())
            self.assertTrue(monitor_entered.is_set())

    def test_retention_cursor_survives_failed_policy_and_new_repository(self):
        from unittest.mock import patch
        from test_mysql_minimal_retention import ApplyConnection, Cursor
        from digital_twin.infrastructure.mysql_minimal_retention import MySQLMinimalRetentionRepository
        from digital_twin.platform.domain.mysql_minimal_retention import mysql_minimal_retention_policy
        class ProgressConnection(ApplyConnection):
            next_policy = ''
            def execute(self, sql, params=()):
                if 'SELECT next_policy' in sql:
                    return Cursor(one={'next_policy': self.next_policy})
                if 'INSERT INTO mysql_retention_progress' in sql:
                    self.next_policy = params[1]
                return super().execute(sql, params)
        connection = ProgressConnection()
        policy = mysql_minimal_retention_policy({'mysqlMinimalRetentionEnabled': '1'})
        repository = MySQLMinimalRetentionRepository(connection)
        with patch.object(repository, '_compact_obsolete_world_projection_payloads', side_effect=TimeoutError), self.assertRaises(TimeoutError) as raised:
            repository.apply(policy)
        self.assertEqual("worldProjection:obsoletePayload", raised.exception.orbit_retention_policy)
        self.assertEqual('worldProjection:completed', connection.next_policy)
        restarted = MySQLMinimalRetentionRepository(connection)
        def bounded(*args):
            args[1]['remainingBytes'] = 0
            return {'deleted': 1, 'tables': {'ontology_world_projection_outbox': 1}}
        with patch.object(restarted, '_delete_world_projection_rows', side_effect=bounded), \
             patch.object(restarted, '_compact_obsolete_world_projection_payloads', side_effect=AssertionError('must resume')):
            result = restarted.apply(policy)
        self.assertEqual(['worldProjection:completed'], result['progress']['attemptedPolicies'])
        self.assertEqual('inferenceDetail:completed', result['progress']['nextPolicy'])
        self.assertGreater(result['progress']['remainingPolicies'], 0)

    def test_compression_requires_pause_allowlist_and_identical_content(self):
        from unittest.mock import Mock, patch
        from digital_twin.infrastructure import mysql_snapshot_storage as storage
        connection = Mock()
        for table, paused in [('monitor_snapshot_history', False), ('runtime_settings', True)]:
            with self.assertRaises(ValueError):
                storage.compress_snapshot_table(connection, table, writers_paused=paused)
        connection.execute.assert_not_called()
        connection.execute.return_value.fetchone.return_value = {'rowFormat': 'Dynamic'}
        with patch.object(storage, 'compression_supported', return_value=True), \
             patch.object(storage, 'table_fingerprint', side_effect=[{'rows': 1, 'sha256': 'a'}, {'rows': 1, 'sha256': 'b'}]), \
             self.assertRaisesRegex(RuntimeError, 'contents changed'):
            storage.compress_snapshot_table(connection, 'monitor_snapshot_history', writers_paused=True)
        self.assertFalse(any('ANALYZE' in str(call) for call in connection.execute.call_args_list))
        for table in storage.COMPRESSED_SNAPSHOT_TABLES:
            sql = 'CREATE TABLE IF NOT EXISTS ' + table + ' (id INT PRIMARY KEY) ENGINE=InnoDB'
            self.assertIn('ROW_FORMAT=COMPRESSED', storage.snapshot_schema_statement(sql))
            self.assertEqual(sql, storage.snapshot_schema_statement(sql, supported=False))

    def test_capacity_refresh_runs_even_when_busy_queue_defers_retention(self):
        self._assert_capacity_turn_never_alerts_from_fallback_limits()
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from digital_twin.infrastructure import cli
        scheduler = Mock()
        with patch.object(cli, 'runtime_settings', return_value={}), \
             patch.object(cli, 'build_ontology_reasoning_queue_probe', return_value=lambda: {'effectivePendingCount': 3}), \
             patch.object(cli, 'operational_storage_inventory', return_value={'freeMb': 8000}) as inventory, \
             patch.object(cli, 'observe_operational_storage_capacity') as observe, \
             patch.object(cli, 'run_mysql_operational_cleanup') as cleanup, \
             patch.object(cli, 'OperationalHistoryRetentionScheduler', return_value=scheduler) as factory:
            scheduler.run_forever.side_effect = lambda: factory.call_args.args[0]()
            cli.maintenance_command(SimpleNamespace(maintenance_action='watch', interval='60'))
        inventory.assert_called_once()
        observe.assert_called_once_with({}, snapshot={'freeMb': 8000})
        cleanup.assert_not_called()

    def test_realtime_queue_defers_cleanup_until_maximum_deferral(self):
        settings = {"mysqlMaintenanceMaxRealtimeDeferralSeconds": "900"}

        first = mysql_maintenance_admission(
            settings,
            pending_count=3,
            now_epoch=1000,
        )
        still_busy = mysql_maintenance_admission(
            settings,
            pending_count=2,
            now_epoch=1899,
            deferral_started_at=first.deferral_started_at,
        )

        self.assertFalse(first.run_cleanup)
        self.assertFalse(still_busy.run_cleanup)
        self.assertEqual("realtime-queue-deferred", still_busy.status)

        with TemporaryDirectory() as directory:
            first_guard = MySQLRealtimeWorkloadGuard(Path(directory) / "mysql-workload.lock")
            second_guard = MySQLRealtimeWorkloadGuard(Path(directory) / "mysql-workload.lock")
            with first_guard.monitor_cycle() as monitor_lease:
                with second_guard.maintenance_turn() as maintenance_lease:
                    self.assertTrue(monitor_lease.acquired)
                    self.assertFalse(maintenance_lease.acquired)
            with second_guard.maintenance_turn() as released_lease:
                self.assertTrue(released_lease.acquired)

    def test_sustained_queue_allows_only_bounded_cleanup(self):
        result = mysql_maintenance_admission(
            {"mysqlMaintenanceMaxRealtimeDeferralSeconds": "900"},
            pending_count=4,
            now_epoch=1900,
            deferral_started_at=1000,
            last_legacy_at=1,
        )

        self.assertTrue(result.run_cleanup)
        self.assertFalse(result.include_legacy)
        self.assertEqual("bounded-cleanup-after-max-deferral", result.status)
        self.assertEqual(1000, result.deferral_started_at)
        idle_but_contended = mysql_maintenance_admission(
            {"mysqlMaintenanceMaxRealtimeDeferralSeconds": "120"},
            pending_count=0, now_epoch=1121, deferral_started_at=1000,
        )
        self.assertEqual("bounded-cleanup-after-max-deferral", idle_but_contended.status)
        self.assertFalse(idle_but_contended.include_legacy)

    def test_idle_queue_runs_legacy_only_on_hourly_cadence(self):
        settings = {"mysqlLegacyRetentionIntervalSeconds": "3600"}

        bounded = mysql_maintenance_admission(
            settings,
            pending_count=0,
            now_epoch=4000,
            last_legacy_at=1000,
        )
        full = mysql_maintenance_admission(
            settings,
            pending_count=0,
            now_epoch=4600,
            last_legacy_at=1000,
        )

        self.assertTrue(bounded.run_cleanup)
        self.assertFalse(bounded.include_legacy)
        self.assertTrue(full.include_legacy)


if __name__ == "__main__":
    unittest.main()
