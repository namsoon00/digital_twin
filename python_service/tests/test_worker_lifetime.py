import ctypes
import gc
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import weakref

from digital_twin.infrastructure.event_bus import EventBus, default_event_bus
from digital_twin.infrastructure.worker_lifetime import (
    WorkerLifetime, _RUsageInfoV0, managed_worker_lifetime, process_memory_bytes,
)
from digital_twin.shared_kernel.events import DomainEvent


class WorkerLifetimeTests(unittest.TestCase):
    def test_pressure_reclaims_python_garbage_before_retiring(self):
        memory = Mock(side_effect=[300, 80, 300, 250])
        clock = Mock(return_value=0)
        collect, emit = Mock(), Mock()
        guard = WorkerLifetime(max_bytes=200, memory_reader=memory,
                               clock=clock, collect=collect, emit=emit)
        self.assertFalse(guard.should_retire())
        self.assertFalse(guard.should_retire())  # sampling stays bounded
        self.assertEqual(2, memory.call_count)
        clock.return_value = 60
        self.assertTrue(guard.should_retire())
        self.assertTrue(guard.should_retire())
        self.assertEqual(2, collect.call_count)
        self.assertEqual(4, memory.call_count)
        self.assertIn('"action": "collected"', emit.call_args_list[0].args[0])
        self.assertIn('"action": "retire"', emit.call_args_list[1].args[0])

    def test_missing_memory_read_is_not_zero_or_proof_of_recovery(self):
        clock = Mock(return_value=0)
        guard = WorkerLifetime(max_seconds=120, memory_reader=lambda: None,
                               clock=clock, emit=Mock())
        self.assertFalse(guard.should_retire())
        clock.return_value = 120
        self.assertTrue(guard.should_retire())
        guard = WorkerLifetime(max_bytes=200, memory_reader=Mock(side_effect=[300, None]),
                               collect=Mock(), emit=Mock())
        self.assertTrue(guard.should_retire())

    def test_linux_counts_swapped_out_memory(self):
        with patch('digital_twin.infrastructure.worker_lifetime.sys.platform', 'linux'), \
             patch.object(Path, 'read_text', return_value='VmRSS:\t100 kB\nVmSwap:\t500 kB\n'):
            self.assertEqual(600 * 1024, process_memory_bytes())

    def test_macos_uses_current_footprint_instead_of_resident_memory(self):
        def read(_pid, flavor, pointer):
            self.assertEqual(0, flavor)
            usage = ctypes.cast(pointer, ctypes.POINTER(_RUsageInfoV0)).contents
            usage.resident_size = 100
            usage.phys_footprint = 900
            return 0
        self.assertEqual(96, ctypes.sizeof(_RUsageInfoV0))
        with patch('digital_twin.infrastructure.worker_lifetime.sys.platform', 'darwin'), \
             patch('digital_twin.infrastructure.worker_lifetime._mac_rusage', return_value=read):
            self.assertEqual(900, process_memory_bytes())

    def test_manual_watch_is_not_automatically_retired(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(managed_worker_lifetime())
        with patch.dict(os.environ, {'ORBIT_MANAGED_WORKER_LIFETIME': '1',
                                    'ORBIT_WORKER_MAX_MEMORY_MB': '-1',
                                    'ORBIT_WORKER_MAX_AGE_SECONDS': 'invalid'}):
            guard = managed_worker_lifetime()
            self.assertEqual(2 * 1024 ** 3, guard.max_bytes)
            self.assertEqual(3600, guard.max_seconds)

    def test_manager_enables_retirement_only_when_a_supervisor_can_replace_workers(self):
        from digital_twin import service_manager
        settings = {'mysqlRuntimeManaged': '0', 'reasoningEngineAutoRetireStaleCandidateEnabled': '0'}
        with patch.object(service_manager, 'runtime_settings', return_value=settings), \
             patch.object(service_manager, 'web_worker_spec', return_value={}), \
             patch.object(service_manager, 'active_reasoning_engine_version', return_value='v2'), \
             patch.object(service_manager, 'candidate_reasoning_worker_enabled', return_value=False), \
             patch.object(service_manager, 'configured_supervisor_available', return_value=False) as configured, \
             patch.object(service_manager, 'supervisor_running', return_value=False):
            unmanaged = service_manager.worker_specs()
            self.assertNotIn('ORBIT_MANAGED_WORKER_LIFETIME', unmanaged['notifications']['env'])
            configured.return_value = True
            managed = service_manager.worker_specs()
            self.assertEqual('1', managed['notifications']['env']['ORBIT_MANAGED_WORKER_LIFETIME'])
            self.assertEqual('1', managed['reasoning-engine-delivery']['env']['ORBIT_MANAGED_WORKER_LIFETIME'])
            self.assertNotIn('ORBIT_MANAGED_WORKER_LIFETIME', managed['monitor']['env'])

    def test_notification_retirement_occurs_after_receipt_settlement(self):
        from digital_twin.infrastructure.schedulers import NotificationQueueScheduler
        order = []
        runner = Mock()
        runner.run_once.side_effect = lambda **_kwargs: order.append('receipt-settled') or 0
        guard = Mock()
        guard.should_retire.side_effect = lambda: order.append('retirement-check') or True
        scheduler = NotificationQueueScheduler(runner, 30, error_reporter=Mock(), lifetime=guard)
        with patch('digital_twin.infrastructure.schedulers.install_stop_handlers'), \
             patch('digital_twin.infrastructure.schedulers.wait_until_running') as sleep:
            scheduler.run_forever()
        self.assertEqual(['receipt-settled', 'retirement-check'], order)
        runner.run_once.assert_called_once()
        sleep.assert_not_called()

    def test_reasoning_retires_only_after_writer_release_and_stops_liveness(self):
        from digital_twin.modules.reasoning.application.independent_reasoning_engine import IndependentReasoningJobRunner
        engine = Mock()
        engine.descriptor.return_value.deployment_id = 'test-deployment'
        runner = IndependentReasoningJobRunner(Mock(), engine, Mock())
        order = []
        runner.acquire_graph_writer_ownership = lambda: {'acquired': True}
        runner._run_once = lambda: order.append('job-settled') or {'status': 'ok'}
        runner.record_graph_writer_result = lambda _result: {}
        runner.run_background_graph_turn = lambda: order.append('background-settled')
        runner.release_graph_writer_ownership = lambda: order.append('writer-released')
        def retire():
            order.append('retirement-check')
            return True
        thread = Mock()
        with patch('digital_twin.modules.reasoning.application.independent_reasoning_engine.threading.Thread', return_value=thread):
            runner.watch(should_retire=retire)
        self.assertEqual(['job-settled', 'background-settled', 'writer-released', 'retirement-check'], order)
        thread.join.assert_called_once_with(timeout=2)

    def test_cli_exits_process_instead_of_reconstructing_dynamic_runner(self):
        from digital_twin.infrastructure.cli import watch_v2_reasoning_engine
        guard = WorkerLifetime(max_bytes=1, memory_reader=lambda: 2, collect=Mock(), emit=Mock())
        runner = Mock()
        runner.watch.side_effect = lambda should_retire: should_retire()
        factory = Mock(return_value=runner)
        with patch('digital_twin.infrastructure.worker_lifetime.managed_worker_lifetime', return_value=guard), \
             patch('digital_twin.infrastructure.cli.runtime_settings', return_value={}):
            self.assertEqual(0, watch_v2_reasoning_engine(factory, {}, worker_role='delivery'))
        factory.assert_called_once()
        runner.shutdown.assert_called_once()


class EventBusRetentionTests(unittest.TestCase):
    def test_runtime_dispatch_preserves_all_durable_events_without_retaining_payloads(self):
        event_log = Mock()
        # A counter doesn't itself retain the arguments like a Mock would.
        recorded, dispatched = [], []
        event_log.handle = lambda event: recorded.append(event.event_id)
        with patch('digital_twin.infrastructure.operational_store.event_log', return_value=event_log):
            bus = default_event_bus()
        bus.subscribe_all(lambda event: dispatched.append(event.event_id))
        references = []
        for index in range(300):
            event = DomainEvent('test', 'test', payload={'large': bytearray(65536)})
            references.append(weakref.ref(event))
            bus.publish(event)
        del event
        gc.collect()
        self.assertEqual(recorded, dispatched)
        self.assertEqual(300, len(recorded))
        self.assertEqual([], bus.published)
        self.assertTrue(all(reference() is None for reference in references))

    def test_recorded_dispatch_and_debug_history_are_bounded(self):
        bus = EventBus(history_limit=2)
        for index in range(5):
            bus.dispatch_recorded(DomainEvent('test', str(index)))
        self.assertEqual(['3', '4'], [event.aggregate_id for event in bus.published])

    def test_handler_error_history_does_not_retain_failed_handler_frames(self):
        references = []
        class Payload:
            pass
        def fail(_event):
            payload = Payload()
            references.append(weakref.ref(payload))
            raise ValueError('failed')
        bus = EventBus(history_limit=0)
        bus.subscribe_all(fail)
        for index in range(150):
            bus.publish(DomainEvent('test', str(index)))
        gc.collect()
        self.assertTrue(all(reference() is None for reference in references))
        self.assertEqual(100, len(bus.handler_errors))
        self.assertEqual('ValueError: failed', str(bus.handler_errors[-1]))
        self.assertIsNone(bus.handler_errors[-1].__traceback__)

    def test_recorder_failure_still_prevents_dispatch_and_raise_policy_keeps_original_error(self):
        recorder, handler = Mock(side_effect=OSError('storage unavailable')), Mock()
        bus = EventBus(recorder=recorder)
        bus.subscribe_all(handler)
        with self.assertRaises(OSError):
            bus.publish(DomainEvent('test', 'test'))
        handler.assert_not_called()
        bus = EventBus(raise_handler_errors=True)
        bus.subscribe_all(lambda _event: (_ for _ in ()).throw(ValueError('original')))
        with self.assertRaisesRegex(ValueError, 'original'):
            bus.publish(DomainEvent('test', 'test'))
