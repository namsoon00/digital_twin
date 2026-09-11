import ast
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / 'digital_twin/modules/reasoning/infrastructure'
CONTRACT = ROOT / 'tests/fixtures/backend_extraction_v6.json'


def body_hash(node, restore=False):
    node = copy.deepcopy(node)
    if restore:
        class Restore(ast.NodeTransformer):
            def visit_ClassDef(self, child):
                return child

            def visit_Attribute(self, child):
                if isinstance(child.value, ast.Name) and child.value.id == '_bindings':
                    return ast.Name(id=child.attr, ctx=ast.Load())
                return self.generic_visit(child)

            def visit_Name(self, child):
                if child.id == '_store':
                    child.id = 'self'
                return child
        node = Restore().visit(node)
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body[0].value.value = inspect.cleandoc(body[0].value.value)
    return hashlib.sha256(ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False).encode()).hexdigest()


class BackendOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads(CONTRACT.read_text())
        self.modules = {path: ast.parse((INFRA / path).read_text())
                        for path in {row['path'] for row in self.contract['methods'].values()}}

    def test_moved_control_flow_matches_frozen_pre_migration_code(self):
        for name, entry in self.contract['methods'].items():
            method = name.split('.')[1]
            node = next(n for n in self.modules[entry['path']].body if isinstance(n, ast.FunctionDef) and n.name == method)
            with self.subTest(method=name, source=self.contract['sourceRevision']):
                self.assertEqual(entry['bodyHash'], body_hash(node, restore=True))

    def test_execution_results_and_callback_order_match_original(self):
        from digital_twin.infrastructure import typedb_ontology as api
        from backend_ownership_fixture import execution_contracts
        golden = json.loads((ROOT / 'tests/fixtures/backend_execution_v6.json').read_text())
        actual = execution_contracts(api)
        self.assertEqual(set(golden['scenarios']), set(actual))
        for name, expected in golden['scenarios'].items():
            with self.subTest(scenario=name):
                self.assertEqual(expected, actual[name])

    def test_staging_verifies_rows_before_control_and_preserves_uncertain_journal(self):
        from digital_twin.infrastructure import typedb_ontology as api
        from backend_ownership_fixture import save_scenario
        success = save_scenario(api, 'ok')
        order = [event[0] for event in success['events']]
        self.assertLess(order.index('rows'), order.index('counts'))
        self.assertLess(order.index('counts'), order.index('control'))
        self.assertLess(order.index('control'), order.index('close'))
        self.assertEqual('release', order[-1])
        self.assertTrue(success['result']['saved'])
        self.assertEqual('manifest:old', success['result']['aboxPersistenceVerification']['activePointer']['aboxSnapshotId'])
        self.assertEqual('pending', success['journal']['status'])
        for scenario in ['cleanup-error', 'insert-error', 'count-mismatch']:
            failed = save_scenario(api, scenario)
            self.assertFalse(failed['result']['saved'])
            self.assertTrue(failed['result']['preservedActiveGeneration'])
            self.assertNotIn('control', [event[0] for event in failed['events']])
            self.assertEqual('release', failed['events'][-1][0])
        readback = save_scenario(api, 'readback-error')
        self.assertFalse(readback['result']['saved'])
        self.assertEqual('pending', readback['journal']['status'])

    def test_native_lease_failure_never_executes_and_adopted_owner_is_not_released(self):
        from digital_twin.infrastructure import typedb_ontology as api
        from backend_ownership_fixture import native_gate_scenario
        for scenario in ['held', 'invalid-adopted']:
            self.assertEqual([], native_gate_scenario(api, scenario)['events'])
        adopted = native_gate_scenario(api, 'adopted')
        self.assertEqual(['run'], [event[0] for event in adopted['events']])
        self.assertEqual('adopted', adopted['result']['inferenceWriteLease']['status'])
        failed = native_gate_scenario(api, 'error')
        self.assertEqual('RuntimeError', failed['result']['raised'])
        self.assertEqual(['run', 'release'], [event[0] for event in failed['events']])

    def test_orphan_cleanup_reaches_bounded_delete_and_closes_on_failure(self):
        from digital_twin.infrastructure import typedb_ontology as api
        with patch.object(api, 'runtime_settings', return_value={}):
            repository = api.TypeDBOntologyGraphRepository('fixture.invalid', retry_count=0)
        calls = []
        repository.driver_imports = lambda: (('fixture',), None)
        repository.open_driver = lambda imported: 'fixture-driver'
        repository.close_driver = lambda driver: calls.append(['close', driver])
        repository.ensure_database = lambda driver: calls.append(['database'])
        repository.ensure_schema = lambda driver, imported: calls.append(['schema'])
        repository.scoped_abox_orphan_candidate_inventory = lambda world: {
            'candidateGenerationIds': ['orphan-one', 'orphan-two'], 'candidateManifestIds': []}
        repository.delete_box_snapshot_rows_in_batches = lambda driver, imported, box, generation: calls.append(['delete', generation]) or {'status': 'ok', 'deletedBatchCount': 1}
        result = repository.prune_orphan_scoped_abox_candidates('market:fixture', max_generation_count=1)
        self.assertEqual('partial', result['status'])
        self.assertEqual(['orphan-one'], result['removedGenerationIds'])
        self.assertEqual(['orphan-two'], result['remainingGenerationIds'])
        self.assertEqual([['database'], ['schema'], ['delete', 'orphan-one'], ['close', 'fixture-driver']], calls)
        repository.ensure_schema = lambda *args: (_ for _ in ()).throw(RuntimeError('schema unavailable'))
        result = repository.prune_orphan_scoped_abox_candidates('market:fixture')
        self.assertEqual('error', result['status'])
        self.assertEqual(['close', 'fixture-driver'], calls[-1])

    def test_lease_state_is_per_repository_and_nested_adoption_is_thread_local(self):
        from concurrent.futures import ThreadPoolExecutor
        from digital_twin.infrastructure import typedb_ontology as api
        with patch.object(api, 'runtime_settings', return_value={}):
            first = api.TypeDBOntologyGraphRepository('fixture.invalid')
            second = api.TypeDBOntologyGraphRepository('fixture.invalid')
        first.typedb_projection_coordinator_enabled = lambda: False
        self.assertIsNot(first._projection_leases, second._projection_leases)
        lock = first._projection_coordinator_registry_lock
        with first.projection_coordinator_write_scope('outer') as outer:
            self.assertTrue(outer['acquired'])
            with first.projection_coordinator_write_scope('inner') as inner:
                self.assertTrue(inner['adopted'])
            self.assertEqual(1, len(first._projection_coordinator_local.leases))
            with ThreadPoolExecutor(max_workers=1) as worker:
                self.assertEqual({}, worker.submit(first.active_projection_coordinator_lease).result())
            self.assertEqual({}, second.active_projection_coordinator_lease())
        self.assertEqual({}, first.active_projection_coordinator_lease())
        self.assertEqual(0, first._projection_coordinator_local.explicit_scope_depth)
        self.assertIs(lock, first._projection_coordinator_registry_lock)

    def test_query_metric_state_stays_bounded_and_does_not_leak_to_other_repositories(self):
        from digital_twin.infrastructure import typedb_ontology as api
        with patch.object(api, 'runtime_settings', return_value={}):
            first = api.TypeDBOntologyGraphRepository('fixture.invalid', query_metrics_enabled=True)
            second = api.TypeDBOntologyGraphRepository('fixture.invalid', query_metrics_enabled=True)
        lock = first._query_metrics_lock
        for index in range(130):
            first.record_query_metric('fixture', 'match $x isa fixture;', 1, index)
        self.assertEqual(120, first.query_metrics_snapshot()['queryCount'])
        self.assertEqual(0, second.query_metrics_snapshot()['queryCount'])
        first.reset_query_metrics()
        self.assertEqual(0, first.query_metrics_snapshot()['queryCount'])
        self.assertIs(lock, first._query_metrics_lock)

    def test_manifest_index_helpers_match_original_code(self):
        for name, entry in self.contract['helpers'].items():
            tree = ast.parse((INFRA / entry['path']).read_text())
            method = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertEqual(entry['bodyHash'], body_hash(method), name)
        from digital_twin.infrastructure import typedb_ontology as facade
        from digital_twin.modules.reasoning.infrastructure import backend_constants
        names = {name for name in vars(backend_constants) if name.isupper()}
        tree = ast.parse((ROOT / 'digital_twin/infrastructure/typedb_ontology.py').read_text())
        redeclared = {target.id for node in tree.body if isinstance(node, ast.Assign)
                      for target in node.targets if isinstance(target, ast.Name)}
        self.assertTrue(names.isdisjoint(redeclared))
        for name in names:
            self.assertEqual(getattr(backend_constants, name), getattr(facade, name), name)

    def test_native_retry_respects_deadline_and_never_retries_invalid_queries(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from digital_twin.modules.reasoning.infrastructure.native_execution import retry
        execute = Mock(side_effect=AssertionError('No retry may start'))
        store = SimpleNamespace(
            native_rule_entry_has_timeout_failure=lambda result: False,
            native_rule_entry_has_interrupted_transaction_failure=lambda result: True,
            execute_typedb_native_rule_entry=execute,
        )
        failed = {'status': 'partial', 'failure': {'status': 'query-error'}}
        with patch.object(retry.time, 'monotonic', return_value=10):
            result = retry.recover_timed_out_native_rule_entry(
                store, failed, {}, ['AAA'], 'market:fixture', True, (), None, 10.4, 'native')
        self.assertEqual(failed, result)
        store.native_rule_entry_has_interrupted_transaction_failure = lambda result: False
        result = retry.recover_timed_out_native_rule_entry(
            store, failed, {}, ['AAA'], 'market:fixture', True, (), None, 999, 'native')
        self.assertEqual(failed, result)
        execute.assert_not_called()

    def test_native_timeout_shards_do_not_publish_incomplete_rows(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from digital_twin.modules.reasoning.infrastructure.native_execution import retry
        execute = Mock(side_effect=[
            {'status': 'ok', 'rows': [{'symbol': 'AAA'}], 'readTransactionCount': 1},
            {'status': 'partial', 'failure': {'status': 'query-timeout'}, 'readTransactionCount': 1},
        ])
        store = SimpleNamespace(
            native_rule_entry_has_timeout_failure=lambda result: True,
            native_rule_entry_has_interrupted_transaction_failure=lambda result: False,
            execute_typedb_native_rule_entry=execute,
        )
        failed = {'status': 'partial', 'failure': {'status': 'query-timeout'}, 'readTransactionCount': 1}
        with patch.object(retry.time, 'monotonic', return_value=10), patch.object(
            retry, 'typedb_native_rule_target_work_plan', return_value={'workItems': [{'candidateSymbols': ['AAA']}, {'candidateSymbols': ['BBB']}]}
        ):
            result = retry.recover_timed_out_native_rule_entry(
                store, failed, {'candidateSymbols': ['AAA', 'BBB']}, ['AAA', 'BBB'],
                'market:fixture', True, (), None, 999, 'native')
        self.assertEqual('partial', result['status'])
        self.assertNotIn('rows', result)
        self.assertEqual(3, result['readTransactionCount'])
        self.assertEqual(1, result['failure']['timeoutFallbackFailedShardIndex'])
        self.assertEqual(2, execute.call_count)

    def test_facade_keeps_signatures_and_coordinator_decorators(self):
        tree = ast.parse((ROOT / 'digital_twin/infrastructure/typedb_ontology.py').read_text())
        methods = {c.name + '.' + n.name: n for c in tree.body if isinstance(c, ast.ClassDef)
                   for n in c.body if isinstance(n, ast.FunctionDef)}
        for name, entry in self.contract['methods'].items():
            method = methods[name]
            self.assertEqual(entry['signature'], ast.dump(method.args, include_attributes=False), name)
            self.assertEqual(entry['decorators'], [ast.unparse(n) for n in method.decorator_list], name)
            self.assertEqual(1, len(method.body), name)
            call = method.body[0].value
            if isinstance(call, ast.YieldFrom):
                call = call.value
            self.assertIsInstance(call, ast.Call, name)
            self.assertEqual(name.split('.')[1], call.func.attr, name)
            self.assertEqual('_' + entry['path'].removesuffix('.py').replace('/', '_'), call.func.value.id, name)
            bindings = next((n.value for n in call.keywords if n.arg == '_bindings'), None)
            if entry['runtimeFields']:
                self.assertIsInstance(bindings, ast.Call, name)
                self.assertTrue(set(entry['runtimeFields']).issubset({n.arg for n in bindings.keywords}), name)
                self.assertTrue(all(isinstance(n.value, ast.Name) and n.arg == n.value.id for n in bindings.keywords), name)
            else:
                self.assertIsNone(bindings, name)

    def test_ports_declare_every_used_capability_without_dynamic_escape_hatches(self):
        for path, tree in self.modules.items():
            port_tree = ast.parse((INFRA / path.replace('.py', '_ports.py')).read_text())
            ports = [n for n in port_tree.body if isinstance(n, ast.ClassDef) and n.name.endswith('Store')]
            self.assertEqual(1, len(ports))
            declared = {n.name for n in ports[0].body if isinstance(n, ast.FunctionDef)}
            declared.update(n.target.id for n in ports[0].body if isinstance(n, ast.AnnAssign))
            used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == '_store'}
            used.update(n.args[1].value for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id in {'getattr', 'setattr', 'delattr'} and len(n.args) > 1
                        and isinstance(n.args[0], ast.Name) and n.args[0].id == '_store' and isinstance(n.args[1], ast.Constant))
            self.assertEqual(used, declared, path)
            self.assertTrue({'__getattr__', '__getattribute__', 'execute', 'dispatch'}.isdisjoint(declared), path)

    def test_private_backend_imports_do_not_load_repository_or_business_workflows(self):
        names = ['digital_twin.modules.reasoning.infrastructure.' + path.removesuffix('.py').replace('/', '.')
                 for path in self.modules]
        result = subprocess.run([sys.executable, '-c', '''
import importlib, json, sys
for name in json.loads(sys.argv[1]):
    importlib.import_module(name)
for name in sys.modules:
    assert not name.startswith(('typedb', 'pymysql', 'mysql')), name
    assert '.application.' not in name, name
    assert name not in {'digital_twin.infrastructure.typedb_ontology', 'digital_twin.infrastructure.settings'}, name
    assert not name.startswith('digital_twin.infrastructure.composition'), name
''', json.dumps(names)], env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True, timeout=25)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_read_ports_do_not_offer_publication_or_native_execution(self):
        for path, tree in self.modules.items():
            if not path.startswith('graph_reads/'):
                continue
            calls = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == '_store'}
            self.assertTrue({'save_graph', 'write_graph', 'run_rulebox', 'activate_abox_generation',
                             'activate_inference_generation', 'send', 'delete_box_rows_in_batches'}.isdisjoint(calls), path)


if __name__ == '__main__':
    unittest.main()
