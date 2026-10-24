"""Latency reductions preserve source history and committed delivery authority."""

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from stabilization_database import StabilizationDatabaseCase
from digital_twin.modules.outcomes.infrastructure.performance_report_cache import ExactPerformanceReportCache
from digital_twin.modules.notifications.infrastructure.notification.wakeup import NotificationWakeSignal
from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore
from digital_twin.infrastructure.schedulers import NotificationQueueScheduler
from digital_twin.infrastructure.graph_writer_guard import LocalGraphWriterGuard
from digital_twin.modules.reasoning.application.independent_reasoning_engine import IndependentReasoningJobRunner, V2ReasoningEngine
from test_independent_reasoning_engine import descriptor, source_event, FakeAssembler, FakeExecutor, FakeCandidateBuilder


class RuleDeliveryLatencyTests(TestCase):
    def test_source_reads_release_writer_but_native_inference_rechecks_claim_and_control(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = LocalGraphWriterGuard('live', 'delivery', Path(directory))
            contender = LocalGraphWriterGuard('active', 'active', Path(directory))
            background = LocalGraphWriterGuard('candidate', 'candidate', Path(directory))
            executor = FakeExecutor()
            original_execute = executor.execute
            def execute(request, snapshots):
                self.assertTrue(guard.status()['acquired'])
                self.assertFalse(contender.acquire()['acquired'])
                contender.release()
                return original_execute(request, snapshots)
            executor.execute = execute
            assembler = FakeAssembler()
            def assemble(request):
                self.assertFalse(guard.status()['acquired'])
                self.assertEqual('delivery-waiting', background.acquire()['status'])
                self.assertTrue(contender.acquire()['acquired'])
                contender.release()
                return FakeAssembler().assemble(request)
            assembler.assemble = assemble
            queue = SimpleNamespace(heartbeat=lambda ids, worker, seconds, claimed_tokens:
                ids == ['job:1'] and claimed_tokens == {'job:1': 'original-claim'})
            engine = V2ReasoningEngine(descriptor(), assembler, executor, FakeCandidateBuilder())
            runner = IndependentReasoningJobRunner(queue, engine, SimpleNamespace(), graph_writer_guard=guard)
            runner.job_transitions.remember([{'jobId': 'job:1', 'claimedAt': 'original-claim'}])
            runner.prepare_queue_turn = lambda: {'status': 'ready'}
            runner.control_binding_status = Mock(return_value={'selected': True})
            runner.execution_readiness = Mock(return_value={'ready': True})
            runner._run_once = lambda _: engine.consume([source_event()], input_assembly_scope=
                lambda: runner.unlocked_input_assembly(['job:1'], threading.Event()))
            result = runner.run_once()
            self.assertNotEqual('deferred', result['status'])
            runner.control_binding_status.assert_called_once()
            runner.execution_readiness.assert_called_once()
            self.assertFalse(guard.status()['acquired'])
            self.assertTrue(background.acquire()['acquired'])
            background.release()

    def test_source_preparation_cannot_execute_after_writer_claim_or_deployment_changes(self):
        for failure in ['busy', 'lease-lost', 'lease-unknown', 'deployment', 'readiness', 'source-error']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                guard = LocalGraphWriterGuard('live', 'delivery', Path(directory))
                contender = LocalGraphWriterGuard('active', 'active', Path(directory))
                executor = Mock()
                queue = SimpleNamespace(heartbeat=Mock(return_value=failure != 'lease-lost'))
                if failure == 'lease-unknown': queue.heartbeat.side_effect = RuntimeError('offline')
                assembler = FakeAssembler()
                def assemble(request):
                    self.assertFalse(guard.status()['acquired'])
                    if failure == 'source-error': raise RuntimeError('source unavailable')
                    if failure == 'busy': self.assertTrue(contender.acquire()['acquired'])
                    return FakeAssembler().assemble(request)
                assembler.assemble = assemble
                engine = V2ReasoningEngine(descriptor(), assembler, executor, FakeCandidateBuilder())
                runner = IndependentReasoningJobRunner(queue, engine, SimpleNamespace(), graph_writer_guard=guard)
                runner.prepare_queue_turn = lambda: {'status': 'ready'}
                runner.control_binding_status = lambda _: {'selected': failure != 'deployment'}
                runner.execution_readiness = lambda: {'ready': failure != 'readiness'}
                runner._run_once = lambda _: engine.consume([source_event()], input_assembly_scope=
                    lambda: runner.unlocked_input_assembly(['job:1'], threading.Event()))
                try:
                    if failure == 'source-error':
                        with self.assertRaisesRegex(RuntimeError, 'source unavailable'): runner.run_once()
                    else:
                        result = runner.run_once()
                        self.assertEqual('deferred', result['status'])
                        self.assertTrue(result['retryable'])
                        if failure == 'busy':
                            self.assertEqual('typedb-graph-writer-owned', result['reason_code'])
                            self.assertTrue(contender.status()['acquired'])
                    executor.execute.assert_not_called()
                    self.assertFalse(guard.status()['acquired'])
                finally:
                    contender.release()

    def test_exact_report_reuse_preserves_repairs_scope_thresholds_and_mutation_isolation(self):
        cache = ExactPerformanceReportCache(2)
        evaluator = Mock(side_effect=lambda rows, minimum_sample_count: {'rows': deepcopy(rows), 'minimum': minimum_sample_count})
        rows = [{'accountId': 'one', 'outcomes': [{'observedAt': 'now', 'return': 1}]}]
        expected = cache.evaluate(rows, 5, evaluator)
        expected['rows'].clear()
        self.assertEqual(rows, cache.evaluate(rows, 5, evaluator)['rows'])
        self.assertEqual(1, evaluator.call_count)
        for field, value in [('return', -1), ('observedAt', 'later')]:
            changed = deepcopy(rows); changed[0]['outcomes'][0][field] = value
            self.assertEqual(changed, cache.evaluate(changed, 5, evaluator)['rows'])
        changed = deepcopy(rows); changed[0]['accountId'] = 'two'
        cache.evaluate(changed, 5, evaluator)
        cache.evaluate(changed, 6, evaluator)
        cache.evaluate(rows, 5, evaluator)
        self.assertEqual(6, evaluator.call_count)
        failing = Mock(side_effect=RuntimeError('unavailable'))
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                cache.evaluate(rows, 5, failing)
        self.assertEqual(2, failing.call_count)

    def test_committed_enqueue_wakes_but_rollback_and_suppression_do_not(self):
        store = object.__new__(MySQLNotificationJobStore)
        events = []; signal = Mock(); signal.notify.side_effect = lambda: events.append('wake')
        store.delivery_wakeup = Mock(return_value=signal)
        @contextmanager
        def transaction():
            events.append('begin'); yield object(); events.append('commit')
        store.transaction = transaction; store.enqueue_with_connection = Mock(return_value=True)
        self.assertTrue(store.enqueue(object()))
        self.assertEqual(['begin', 'commit', 'wake'], events)
        events.clear(); store.enqueue_with_connection.return_value = False
        self.assertFalse(store.enqueue(object())); self.assertEqual(['begin', 'commit'], events)
        events.clear(); store.enqueue_with_connection.side_effect = RuntimeError('rollback')
        with self.assertRaisesRegex(RuntimeError, 'rollback'): store.enqueue(object())
        self.assertEqual(['begin'], events)

    def test_cross_process_wake_is_bounded_isolated_and_recovers_when_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            signal = NotificationWakeSignal(path=Path(directory)/'wake')
            before = signal.version()
            subprocess.run([sys.executable, '-c',
                'from digital_twin.modules.notifications.infrastructure.notification.wakeup import NotificationWakeSignal; import sys; NotificationWakeSignal(path=sys.argv[1]).notify()',
                str(signal.path)], check=True)
            self.assertNotEqual(before, signal.version())
            sleep = Mock(); signal.wait(before, lambda: True, 30, clock=lambda: 0, sleep=sleep)
            sleep.assert_not_called()
            ticks = iter([0, 1, 2]); signal.wait(signal.version(), lambda: True, 2, clock=lambda: next(ticks), sleep=sleep)
            self.assertEqual([((1.0,),), ((1.0,),)], [(c.args,) for c in sleep.call_args_list])
            bad = NotificationWakeSignal(path=Path(directory)/'missing'/'wake'); bad.notify()
            self.assertIsNone(bad.version())
            self.assertNotEqual(NotificationWakeSignal({'mysqlDatabase': 'a'}).path,
                                NotificationWakeSignal({'mysqlDatabase': 'b'}).path)

    def test_notification_scheduler_observes_wakes_during_delivery_and_keeps_polling_fallback(self):
        wake = Mock(); wake.version.return_value = 'before-claim'
        runner = SimpleNamespace(run_once=Mock(return_value=0))
        scheduler = NotificationQueueScheduler(runner, 30, error_reporter=Mock(),
            lifetime=SimpleNamespace(should_retire=lambda: False), wakeup=wake)
        wake.wait.side_effect = lambda *args: scheduler.stop()
        with patch('digital_twin.infrastructure.schedulers.install_stop_handlers'):
            scheduler.run_forever()
        self.assertEqual('before-claim', wake.wait.call_args.args[0])
        self.assertEqual(1, runner.run_once.call_count)


class RuleLatencyHistoryTests(StabilizationDatabaseCase):
    def test_shadow_history_limits_identities_before_payloads_without_changing_scope_or_ties(self):
        stamp = '2026-10-24T00:00:00Z'; future = '2026-10-25T00:00:00Z'
        for index, (account, symbol, observed, orphan) in enumerate([
                (self.deployment, 'AAA', stamp, False), (self.deployment, 'AAA', stamp, False),
                ('other', 'AAA', stamp, False), (self.deployment, 'BBB', stamp, False),
                (self.deployment, 'AAA', future, False), (self.deployment, 'AAA', stamp, True)]):
            identity = self.deployment + ':' + str(index)
            if not orphan:
                episode = {'accountId': account, 'symbol': symbol, 'hypothesisId': identity,
                           'hypothesis': {'claim': 'preserve', 'audit': 'x'*10000}, 'observedFromAt': stamp}
                self.sql('INSERT INTO investment_hypothesis_observation_episodes '
                    '(episode_id,account_id,symbol,claim_identity,payload_json,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                    (identity,account,symbol,identity,json.dumps(episode),stamp,stamp))
            self.sql('INSERT INTO investment_hypothesis_observation_outcomes '
                '(outcome_id,observation_episode_id,account_id,symbol,observed_at,payload_json,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                (identity,identity,account,symbol,observed,json.dumps({'price':index}),stamp))
        rows = self.decisions.shadow_performance_episodes(account_id=self.deployment,symbol='AAA',limit=1,as_of=stamp)
        self.assertEqual([self.deployment+':1'], [r['episodeId'] for r in rows])
        self.assertEqual(10000, len(rows[0]['hypothesisSet']['hypotheses'][0]['audit']))
        self.assertEqual(stamp, rows[0]['outcomes'][0]['observedAt'])
        rows = self.decisions.shadow_performance_episodes(account_id=self.deployment,symbol='AAA',limit=2,as_of=stamp)
        self.assertEqual([self.deployment+':1',self.deployment+':0'], [r['episodeId'] for r in rows])
