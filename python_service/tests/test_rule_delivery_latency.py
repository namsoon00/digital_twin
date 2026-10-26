"""Latency reductions preserve source history and committed delivery authority."""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import asdict
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
    def test_managed_delivery_omits_durable_cache_capability_without_changing_source_settings(self):
        from digital_twin.infrastructure import operational_store
        from digital_twin.infrastructure.composition import reasoning
        from digital_twin.modules.reasoning.domain.reasoning_engine_versions import EngineControlState

        stores = Mock()
        registry = stores.reasoning_engine_registry_store.return_value
        registry.control.return_value = EngineControlState('live', 'live', 'candidate')
        registry.get.side_effect = lambda deployment: {'engineVersion': 'v2', 'graphStoreBinding': deployment}
        with ExitStack() as stack:
            stack.enter_context(patch('digital_twin.infrastructure.operational_store', stores))
            stack.enter_context(patch('digital_twin.infrastructure.composition.runtime_support.ontology_graph_single_writer_enabled', return_value=False))
            stack.enter_context(patch('digital_twin.infrastructure.composition.runtime_support.typedb_capacity_guard', return_value=lambda: {'ready': True}))
            stack.enter_context(patch('digital_twin.modules.reasoning.infrastructure.mysql_reasoning_ingress.MySQLReasoningIngressRouter'))
            builder = stack.enter_context(patch.object(reasoning, 'build_v2_reasoning_engine'))
            for role, deployment, enabled in [
                ('delivery', 'live', False), ('active', 'live', False),
                ('active', 'other', True), ('candidate', 'candidate', True), ('configured', 'live', True),
            ]:
                with self.subTest(role=role, deployment=deployment):
                    reasoning.build_v2_reasoning_job_runner({'fixture': True}, worker_role=role, deployment_id=deployment)
                    self.assertIs(enabled, builder.call_args.kwargs['persistent_graph_cache_enabled'])

        configured = {'ontologyProjectionGraphPersistentCacheEnabled': '1', 'ontologyProjectionGraphCacheEnabled': '1'}
        binding = SimpleNamespace(candidate_rulebox={}, rulebox_release_preflight={}, rulebox_fingerprint='rules',
                                  release_identity={'releaseFingerprint': 'frozen'}, release_artifact_persistence={})
        launch = SimpleNamespace(configured=configured, platform=Mock(), descriptor=descriptor(),
                                 deployment_health={}, candidate_settings=dict(configured), store_settings=dict(configured))
        warmup = SimpleNamespace(runtime_rulebox_catalog={}, compiled_ontology_release={}, runtime_world_partition={})
        delivery = SimpleNamespace(delivery_authorized=lambda: False, subject_decision_orchestrator=None,
                                   ai_insight_handoff_service=None, insight_dispatch_service=None)
        stores.reset_mock()
        with ExitStack() as stack:
            stack.enter_context(patch('digital_twin.infrastructure.operational_store', stores))
            stack.enter_context(patch('digital_twin.infrastructure.composition.reasoning_launch.prepare_v2_launch', return_value=launch))
            stack.enter_context(patch('digital_twin.infrastructure.composition.reasoning_binding.bind_v2_release', return_value=binding))
            stack.enter_context(patch('digital_twin.infrastructure.ontology_graph_store.ontology_repository_from_settings'))
            stack.enter_context(patch('digital_twin.infrastructure.time_series_factory.build_time_series_adapters', return_value={descriptor().time_series_backend_id: Mock()}))
            stack.enter_context(patch('digital_twin.infrastructure.statistical_signal_factory.build_statistical_signal_pipeline_service'))
            stack.enter_context(patch('digital_twin.infrastructure.composition.reasoning_warmup.warm_v2_release', return_value=warmup))
            stack.enter_context(patch('digital_twin.infrastructure.composition.reasoning_release_health.record_v2_release_health'))
            stack.enter_context(patch('digital_twin.infrastructure.composition.reasoning_delivery.wire_v2_decision_services', return_value=delivery))
            stack.enter_context(patch('digital_twin.infrastructure.composition.outcomes.build_hypothesis_lifecycle_service'))
            stack.enter_context(patch('digital_twin.infrastructure.event_bus.default_event_bus'))
            recorder = stack.enter_context(patch('digital_twin.infrastructure.ontology_projection.PortfolioOntologyProjectionRecorder'))
            for enabled in [False, True]:
                engine = reasoning.build_v2_reasoning_engine(configured, persistent_graph_cache_enabled=enabled)
                port = recorder.call_args.kwargs['graph_assembly_cache_store']
                self.assertIs(stores.ontology_graph_assembly_cache_store.return_value if enabled else None, port)
                self.assertEqual(configured, recorder.call_args.kwargs['settings'])
                self.assertEqual(configured, engine.settings)
                self.assertEqual(int(enabled), stores.ontology_graph_assembly_cache_store.call_count)
                self.assertEqual('frozen', engine.release_identity()['releaseFingerprint'])

    def test_ready_delivery_burst_removes_sleeps_and_retains_candidate_writer_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = LocalGraphWriterGuard('live', 'delivery', Path(directory))
            candidate = LocalGraphWriterGuard('candidate', 'candidate', Path(directory))
            turns, sleeps = [], []

            def next_lane(deployment_id):
                self.assertEqual(descriptor().deployment_id, deployment_id)
                self.assertFalse(guard.status()['acquired'])
                self.assertFalse(guard.status()['deliveryIntent'])
                return 'CONTEXT'

            runner = IndependentReasoningJobRunner(
                SimpleNamespace(next_lane=next_lane), SimpleNamespace(descriptor=descriptor),
                SimpleNamespace(), deployment_role='delivery', graph_writer_guard=guard,
            )
            runner.worker_liveness_loop = lambda *_: None
            runner.prepare_queue_turn = lambda: {'status': 'ready'}

            def run(_preparation):
                self.assertTrue(guard.status()['acquired'])
                turns.append(len(turns) + 1)
                return {'status': 'completed', 'processedCount': 1}

            def sleep(seconds):
                sleeps.append((len(turns), seconds))
                self.assertTrue(candidate.acquire()['acquired'])
                self.assertFalse(guard.acquire()['acquired'])
                guard.release()
                candidate.release()

            runner._run_once = run
            with patch('digital_twin.modules.reasoning.application.independent_reasoning_engine.time.sleep', side_effect=sleep):
                runner.watch(should_retire=lambda: len(turns) == 7)
            self.assertEqual([(3, 5), (6, 5)], sleeps)
            self.assertEqual(list(range(1, 8)), turns)
            self.assertFalse(guard.status()['acquired'])
            self.assertTrue(candidate.acquire()['acquired'])
            candidate.release()

    def test_watch_fast_path_requires_progress_and_due_work_and_never_bypasses_backoff(self):
        completed = {'status': 'completed', 'processedCount': 1}
        queue = SimpleNamespace(next_lane=Mock(return_value='REALTIME'))
        runner = IndependentReasoningJobRunner(
            queue, SimpleNamespace(descriptor=descriptor), SimpleNamespace(), deployment_role='delivery',
        )
        for role, result in [
            ('candidate', completed), ('configured', completed),
            *[('delivery', {'status': status, 'processedCount': 1}) for status in
              ['idle', 'retry', 'failed', 'excluded', 'deferred', 'lease-lost', 'inactive-control-binding']],
            ('delivery', {'status': 'completed', 'processedCount': 0}),
            ('delivery', {**completed, 'retryAfterSeconds': 12}),
            ('delivery', {**completed, 'graphWriterBackoff': {'retryAfterSeconds': 12}}),
            ('delivery', {**completed, 'result': {'retryable': True}}),
        ]:
            with self.subTest(role=role, result=result):
                runner.deployment_role = role
                queue.next_lane.reset_mock()
                runner._consecutive_delivery_turns = 2
                self.assertEqual(5, runner.watch_delay_seconds(result, 5))
                self.assertEqual(0, runner._consecutive_delivery_turns)
                queue.next_lane.assert_not_called()
        runner.deployment_role = 'active'
        for response in ['', RuntimeError('queue unavailable')]:
            queue.next_lane.side_effect = response if isinstance(response, Exception) else None
            queue.next_lane.return_value = response
            self.assertEqual(5, runner.watch_delay_seconds(completed, 5))
        queue.next_lane.side_effect = None
        queue.next_lane.return_value = 'CONTEXT'
        self.assertEqual([0, 0, 5, 0], [runner.watch_delay_seconds(completed, 5) for _ in range(4)])
        runner.queue = SimpleNamespace()
        self.assertEqual(5, runner.watch_delay_seconds(completed, 5))
        runner.worker_liveness_loop = lambda *_: None
        runner.run_watch_turn = Mock(return_value=completed)
        with patch('digital_twin.modules.reasoning.application.independent_reasoning_engine.time.sleep') as sleep:
            runner.watch(should_retire=lambda: True)
            sleep.assert_not_called()
            runner.run_watch_turn.assert_called_once()

    def test_optional_database_cache_omission_preserves_graphs_memory_hits_and_source_identity(self):
        from digital_twin.infrastructure import ontology_projection as api
        from projection_input_fixture import PersistentCache, RULES, clear_caches, recorder, source_snapshot

        clear_caches(api)
        self.addCleanup(clear_caches, api)
        source = source_snapshot()
        persistent = PersistentCache()
        replay = recorder(api, source, cache=True, persistent=persistent)
        expected = replay.build_graph_assembly(deepcopy(source), RULES)
        self.assertEqual(['get', 'put'], persistent.calls)
        clear_caches(api)
        live = recorder(api, source, cache=True, persistent=persistent)
        # Match delivery composition: omit the optional port, not source settings.
        live.graph_assembly_cache_store = None
        before = deepcopy(live.settings)
        actual = live.build_graph_assembly(deepcopy(source), RULES)
        self.assertEqual(asdict(expected[0]), asdict(actual[0]))
        self.assertEqual(asdict(expected[1]), asdict(actual[1]))
        self.assertEqual(replay.last_runtime_contexts, live.last_runtime_contexts)
        self.assertEqual(before, live.settings)
        self.assertEqual(['get', 'put'], persistent.calls)
        self.assertFalse(actual[2]['runtimeStages'].get('graphAssemblyPersistentCacheStored', 0))
        actual[0].worldview['callerMutation'] = True
        hit = live.build_graph_assembly(deepcopy(source), RULES)
        self.assertEqual('hit', hit[2]['status'])
        self.assertEqual('memory', hit[2]['cacheLayer'])
        self.assertEqual(asdict(expected[0]), asdict(hit[0]))
        changed = deepcopy(source)
        changed.positions[0].current_price += 1
        changed.positions[0].source_as_of = '2026-07-20T00:02:00Z'
        changed.generated_at = changed.positions[0].source_as_of
        self.assertNotEqual('hit', live.build_graph_assembly(changed, RULES)[2]['status'])
        # A new replay process still reuses the persisted original input.
        clear_caches(api)
        restored = recorder(api, source, cache=True, persistent=persistent)
        restored_result = restored.build_graph_assembly(deepcopy(source), RULES)
        self.assertEqual('persistent', restored_result[2]['cacheLayer'])
        self.assertEqual(asdict(expected[1]), asdict(restored_result[1]))

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
        failures = ['busy', 'lease-lost', 'lease-unknown', 'deployment', 'readiness', 'source-error']
        cases = [('delivery', failure) for failure in failures]
        cases += [('candidate', failure) for failure in failures if failure != 'busy']
        for role, failure in cases:
            with self.subTest(role=role, failure=failure), tempfile.TemporaryDirectory() as directory:
                guard = LocalGraphWriterGuard('live', role, Path(directory))
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
                    self.assertFalse(guard.status()['grantedTurnReserved'])
                finally:
                    contender.release()

    def test_candidate_prepares_once_despite_delivery_arriving_during_source_read(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = LocalGraphWriterGuard('candidate', 'candidate', Path(directory))
            delivery = LocalGraphWriterGuard('active', 'delivery', Path(directory))
            assembler = FakeAssembler()
            def assemble(request):
                self.assertFalse(guard.status()['acquired'])
                self.assertFalse(delivery.acquire()['acquired'])
                self.assertTrue(delivery.status()['deliveryIntent'])
                return FakeAssembler().assemble(request)
            assembler.assemble = Mock(side_effect=assemble)
            executor = FakeExecutor()
            original = executor.execute
            def execute(request, snapshots):
                self.assertTrue(guard.status()['acquired'])
                self.assertFalse(delivery.acquire()['acquired'])
                return original(request, snapshots)
            executor.execute = Mock(side_effect=execute)
            engine = V2ReasoningEngine(descriptor(), assembler, executor, FakeCandidateBuilder())
            runner = IndependentReasoningJobRunner(
                SimpleNamespace(heartbeat=lambda *args: True), engine, SimpleNamespace(),
                graph_writer_guard=guard)
            runner.prepare_queue_turn = lambda: {'status': 'ready'}
            runner.control_binding_status = lambda _: {'selected': True}
            runner.execution_readiness = lambda: {'ready': True}
            runner._run_once = lambda _: engine.consume([source_event()], input_assembly_scope=
                lambda: runner.unlocked_input_assembly(['job:1'], threading.Event()))
            result = runner.run_once()
            self.assertNotEqual('deferred', result['status'])
            assembler.assemble.assert_called_once()
            executor.execute.assert_called_once()
            self.assertTrue(delivery.acquire()['acquired'])
            delivery.release()

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
