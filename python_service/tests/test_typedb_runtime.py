import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import urllib.error

from digital_twin.infrastructure import typedb_ontology as api
from digital_twin.modules.reasoning.infrastructure.typedb_runtime import http, ports, schema_plan
from typedb_runtime_fixture import IMPORTED, PARTIAL, SCHEMA, contract_fingerprints


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "digital_twin/modules/reasoning/infrastructure/typedb_runtime"
PREFIX = "digital_twin.modules.reasoning.infrastructure.typedb_runtime"


class SchemaDriver:
    """Commit journal only; never pretends to validate TypeQL natively."""

    def __init__(self, failure=0):
        self.committed = []
        self.begun = 0
        self.rollbacks = 0
        self.failure = failure

    def transaction(self, database, transaction_type, options=None):
        self.begun += 1
        self.pending = []
        return self

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        if error:
            self.rollbacks += 1

    def query(self, query):
        self.pending.append(query)
        return self

    def resolve(self):
        return None

    def commit(self):
        if self.begun == self.failure:
            raise RuntimeError("schema commit failed")
        self.committed.extend(self.pending)


class TypeDBRuntimeTests(unittest.TestCase):
    def setUp(self):
        for patcher in [
            patch.object(api, "runtime_settings", return_value={}),
            patch.object(api.TypeDBOntologyGraphRepository, "_process_base_schema_ready", {}),
        ]:
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(api, "typedb_operation_timeout", side_effect=lambda *args: nullcontext())
        patcher.start()
        self.addCleanup(patcher.stop)

    def repository(self, **options):
        return api.TypeDBOntologyGraphRepository("runtime-contract.invalid:1729", database="synthetic", **options)

    def test_extraction_preserves_original_schema_queries_and_control_flow(self):
        golden = json.loads((ROOT / "tests/fixtures/typedb_runtime_v6.json").read_text())
        self.assertEqual(api.TYPEDB_NATIVE_RULE_ENGINE_VERSION, golden["engineVersion"])
        actual = contract_fingerprints(api)
        self.assertEqual(set(golden["scenarios"]), set(actual))
        for scenario, expected in golden["scenarios"].items():
            with self.subTest(scenario=scenario, original=golden["sourceRevision"]):
                self.assertEqual(expected, actual[scenario])

    def test_shared_driver_creation_is_single_flight_and_keeps_long_write_deadline(self):
        repo = self.repository(persistent_driver_enabled=True, query_timeout_seconds=2,
                               schema_operation_timeout_seconds=60, write_operation_timeout_seconds=180)
        driver = Mock()
        start = threading.Barrier(8)

        def open_one():
            start.wait(timeout=5)
            return repo.open_driver(IMPORTED, request_timeout_seconds=1)

        def create_driver(*args, **kwargs):
            # Give competing callers time to reach the guarded creation path.
            threading.Event().wait(.01)
            return driver

        with patch.object(repo, "create_driver", side_effect=create_driver) as create:
            with ThreadPoolExecutor(max_workers=8) as executor:
                opened = list(executor.map(lambda _: open_one(), range(8)))
            self.assertTrue(all(value is driver for value in opened))
            create.assert_called_once_with(IMPORTED, request_timeout_seconds=180.0)
        repo.close_driver(driver)
        driver.close.assert_not_called()
        repo.invalidate_persistent_driver()
        repo.invalidate_persistent_driver()
        driver.close.assert_called_once()
        self.assertIsNone(repo._persistent_driver)

    def test_dedicated_reads_and_short_lived_drivers_do_not_close_shared_channel(self):
        for persistent, dedicated in [(False, False), (True, False), (True, True)]:
            with self.subTest(persistent=persistent, dedicated=dedicated):
                repo = self.repository(persistent_driver_enabled=persistent)
                shared, read = Mock(), Mock()
                repo._persistent_driver = shared if persistent else None
                with patch.object(repo, "create_driver", return_value=read), patch.object(repo, "native_rule_dedicated_read_driver_enabled", return_value=dedicated):
                    driver = repo.open_native_rule_read_driver(IMPORTED, request_timeout_seconds=3)
                    repo.close_native_rule_read_driver(driver)
                    if persistent and not dedicated:
                        self.assertIs(shared, driver)
                        read.close.assert_not_called()
                    else:
                        self.assertIs(read, driver)
                        read.close.assert_called_once()
                    shared.close.assert_not_called()

    def test_transport_configuration_keeps_tls_credentials_and_timeout_bounds(self):
        factory = Mock()
        tls = SimpleNamespace(enabled=lambda: "tls-on", disabled=lambda: "tls-off")
        imports = ((factory, lambda user, password: (user, password), lambda tls, **kwargs: (tls, kwargs), tls, None), None)
        for secure in [False, True]:
            repo = self.repository(user="synthetic-user", password="synthetic-password", tls_enabled=secure, retry_count=8)
            repo.create_driver(imports, request_timeout_seconds=0.1)
            args = factory.driver.call_args.args
            self.assertEqual(("synthetic-user", "synthetic-password"), args[1])
            self.assertEqual(("tls-on" if secure else "tls-off", {"primary_failover_retries": 2, "request_timeout_millis": 1000}), args[2])

    def test_retries_invalidate_before_backoff_and_never_retry_rejected_errors(self):
        repo = self.repository(retry_count=3, persistent_driver_enabled=True)
        journal = []
        operation = Mock(side_effect=[TimeoutError("transport"), "ok"])
        with patch.object(repo, "invalidate_persistent_driver", side_effect=lambda: journal.append("invalidate")), patch.object(api.time, "sleep", side_effect=lambda seconds: journal.append(seconds)):
            self.assertEqual("ok", repo.with_typedb_retries(operation))
            self.assertEqual(["invalidate", 0.25], journal)
            journal.clear()
            operation = Mock(side_effect=ValueError("semantic failure"))
            with self.assertRaisesRegex(ValueError, "semantic failure"):
                repo.with_typedb_retries(operation, retry_if=lambda error: False)
            operation.assert_called_once()
            self.assertEqual(["invalidate"], journal)
            journal.clear()
            operation = Mock(side_effect=TimeoutError("transport"))
            with self.assertRaises(TimeoutError):
                repo.with_typedb_retries(operation)
            self.assertEqual(4, operation.call_count)
            self.assertEqual(["invalidate", .25, "invalidate", .5, "invalidate", .75, "invalidate"], journal)
        broken = Mock()
        broken.close.side_effect = RuntimeError("already disconnected")
        repo._persistent_driver = broken
        repo.invalidate_persistent_driver()
        self.assertIsNone(repo._persistent_driver)

    def test_readiness_cache_preserves_process_sharing_ttl_and_database_isolation(self):
        first, second = self.repository(), self.repository()
        others = [self.repository(tls_enabled=True), self.repository(), self.repository()]
        others[1].database = "another-db"
        others[2].address = "another-host.invalid:1729"
        with patch.object(api.time, "monotonic", return_value=0):
            first.mark_process_base_schema_ready("schema-a")
            self.assertTrue(second.process_base_schema_is_ready("schema-a"))
            self.assertFalse(second.process_base_schema_is_ready("schema-b"))
            for other in others:
                self.assertFalse(other.process_base_schema_is_ready("schema-a"))
                other.mark_process_base_schema_ready("schema-a")
        with patch.object(api.time, "monotonic", return_value=300):
            self.assertTrue(second.process_base_schema_is_ready("schema-a"))
        with patch.object(api.time, "monotonic", return_value=300.01):
            self.assertFalse(second.process_base_schema_is_ready("schema-a"))
        first.mark_process_base_schema_ready("schema-a")
        first.mark_process_base_schema_ready("schema-b")
        first.invalidate_process_base_schema_readiness()
        entries = api.TypeDBOntologyGraphRepository._process_base_schema_ready
        self.assertEqual(3, len(entries))
        self.assertTrue(all(key[:3] != first.process_base_schema_cache_key("")[:3] for key in entries))
        first.mark_process_base_schema_ready("")
        first.address = ""
        first.mark_process_base_schema_ready("schema-c")
        self.assertFalse(first.process_base_schema_is_ready("schema-c"))
        self.assertEqual(3, len(entries))

    def test_database_creation_invalidates_only_matching_readiness_and_handles_create_races(self):
        repo = self.repository()
        repo.mark_process_base_schema_ready("old")
        driver = Mock()
        driver.databases.contains.return_value = False
        repo.ensure_database(driver)
        self.assertTrue(repo._database_created_in_process)
        self.assertFalse(repo.process_base_schema_is_ready("old"))
        driver.databases.create.assert_called_once_with("synthetic")
        driver.databases.contains.return_value = True
        repo.ensure_database(driver)
        self.assertFalse(repo._database_created_in_process)
        driver.databases.contains.side_effect = RuntimeError("inspection unavailable")
        driver.databases.create.side_effect = RuntimeError("already exists")
        repo.ensure_database(driver)
        self.assertFalse(repo._database_created_in_process)
        driver.databases.create.side_effect = RuntimeError("permission denied")
        with self.assertRaisesRegex(RuntimeError, "permission denied"):
            repo.ensure_database(driver)
        repo.ensure_database(object())
        self.assertFalse(repo._database_created_in_process)
        driver.databases.delete.assert_not_called()

    def test_transaction_deadlines_and_optional_older_driver_behavior(self):
        repo = self.repository()
        fake = SimpleNamespace(TransactionOptions=lambda **kwargs: SimpleNamespace(**kwargs))
        with patch.dict(sys.modules, {"typedb.driver": fake}):
            self.assertEqual(1000, repo.read_transaction_options(.1).transaction_timeout_millis)
            self.assertEqual(2100, repo.read_transaction_options(2.1).transaction_timeout_millis)
            self.assertEqual(62000, repo.schema_transaction_options(62).schema_lock_acquire_timeout_millis)
            with patch.object(repo, "write_operation_timeout_seconds", return_value=180):
                self.assertEqual(180000, repo.write_transaction_options().transaction_timeout_millis)
            driver = Mock()
            driver.transaction.side_effect = [TypeError("unexpected keyword argument 'options'"), "legacy-tx"]
            self.assertEqual("legacy-tx", repo.schema_transaction(driver, "schema", 60))
            self.assertEqual(("synthetic", "schema"), driver.transaction.call_args.args)
            driver.transaction.side_effect = TypeError("internal driver problem")
            with self.assertRaisesRegex(TypeError, "internal driver"):
                repo.schema_transaction(driver, "schema")
        with patch.dict(sys.modules, {"typedb.driver": SimpleNamespace()}):
            self.assertIsNone(repo.read_transaction_options())
            self.assertIsNone(repo.write_transaction_options())
            self.assertIsNone(repo.schema_transaction_options())
            imported, error = repo.driver_imports()
            self.assertIsNone(imported)
            self.assertIsNotNone(error)

    def test_fresh_partial_schema_fails_closed_and_does_not_mark_failed_commits_ready(self):
        repo = self.repository(fresh_candidate_rebuild=True)
        repo.schema_query = lambda: SCHEMA
        with patch.object(repo, "typedb_schema_text", side_effect=TimeoutError("inspection")), patch.object(repo, "synchronize_base_schema_batches") as write:
            with self.assertRaises(TimeoutError):
                repo.ensure_schema(object(), IMPORTED)
            write.assert_not_called()
        self.assertEqual("", repo._base_schema_ready_fingerprint)
        self.assertEqual({}, api.TypeDBOntologyGraphRepository._process_base_schema_ready)
        repo._database_created_in_process = True
        driver = SchemaDriver(failure=2)
        repo._fresh_schema_bootstrap_batch_size = 1
        repo.schema_transaction_options = lambda timeout=None: None
        with self.assertRaisesRegex(RuntimeError, "schema commit failed"):
            repo.ensure_schema(driver, IMPORTED)
        self.assertEqual(1, len(driver.committed))
        self.assertEqual(1, driver.rollbacks)
        self.assertEqual("", repo._base_schema_ready_fingerprint)
        self.assertEqual({}, api.TypeDBOntologyGraphRepository._process_base_schema_ready)
        repo._database_created_in_process = False
        completed = "\n".join(driver.committed)
        resumed = SchemaDriver()
        with patch.object(repo, "typedb_schema_text", return_value=completed):
            repo.ensure_schema(resumed, IMPORTED)
        self.assertNotIn(driver.committed[0], resumed.committed)
        fingerprint = hashlib.sha256(SCHEMA.encode()).hexdigest()
        self.assertTrue(repo.process_base_schema_is_ready(fingerprint))
        self.assertEqual(fingerprint, repo._base_schema_ready_fingerprint)

    def test_readiness_hits_avoid_schema_catalogue_and_commit_calls(self):
        for repository_local in [False, True]:
            repo = self.repository()
            repo.schema_query = lambda: SCHEMA
            fingerprint = hashlib.sha256(SCHEMA.encode()).hexdigest()
            if repository_local:
                repo._base_schema_ready_fingerprint = fingerprint
            else:
                repo.mark_process_base_schema_ready(fingerprint)
            with patch.object(repo, "typedb_schema_text", side_effect=AssertionError("catalogue read")), patch.object(repo, "synchronize_base_schema_batches", side_effect=AssertionError("schema write")):
                repo.ensure_schema(object(), IMPORTED)
            self.assertEqual(fingerprint, repo._base_schema_ready_fingerprint)

    def test_native_and_http_bootstrap_preserve_batch_metrics_and_deadlines(self):
        repo = self.repository(http_address="http://synthetic.invalid")
        repo.schema_query = lambda: SCHEMA
        repo.schema_transaction_options = lambda timeout=None: None
        driver = SchemaDriver()
        with patch.object(api.time, "perf_counter", return_value=42):
            native = repo.synchronize_base_schema_batches(driver, IMPORTED, PARTIAL, batch_size=2, operation_timeout_seconds=60)
            with patch.object(repo, "typedb_http_json_request", side_effect=lambda path, *args, **kwargs: {"token": "synthetic-token"} if path == "/v1/signin" else {}) as request:
                over_http = repo.synchronize_base_schema_batches_http(PARTIAL, batch_size=2, operation_timeout_seconds=60)
                calls = request.call_args_list
        self.assertEqual("http-bounded-schema-batches", over_http["mode"])
        self.assertEqual({**native, "mode": over_http["mode"]}, over_http)
        self.assertEqual(driver.committed, [call.args[1]["query"] for call in calls[1:]])
        self.assertTrue(over_http["resumed"])
        for call in calls[1:]:
            self.assertEqual(60000, call.args[1]["transactionOptions"]["schemaLockAcquireTimeoutMillis"])
            self.assertTrue(call.args[1]["commit"])
        previous = dict(repo._last_base_schema_sync)
        with patch.object(repo, "typedb_http_json_request", side_effect=[{"token": "synthetic-token"}, RuntimeError("offline")]):
            with self.assertRaisesRegex(RuntimeError, "batch failed at 1/"):
                repo.synchronize_base_schema_batches_http()
        self.assertEqual(previous, repo._last_base_schema_sync)
        with patch.object(repo, "typedb_http_json_request", return_value={}) as request:
            with self.assertRaisesRegex(RuntimeError, "access token"):
                repo.synchronize_base_schema_batches_http()
            request.assert_called_once()

    def test_http_transport_handles_payloads_optional_token_and_bounded_errors(self):
        repo = self.repository(http_address="schema-contract.invalid/")
        for raw, expected in [(b'{"ok":true}', {"ok": True}), (b'[]', {"result": []}), (b'', {})]:
            response = MagicMock()
            response.__enter__.return_value.read.return_value = raw
            with patch.object(http.urllib.request, "urlopen", return_value=response) as request:
                self.assertEqual(expected, repo.typedb_http_json_request("/v1/query", {"query": "synthetic"}, .1, "synthetic-token"))
                sent = request.call_args.args[0]
                self.assertEqual("http://schema-contract.invalid/v1/query", sent.full_url)
                self.assertEqual("POST", sent.method)
                self.assertEqual("Bearer synthetic-token", sent.get_header("Authorization"))
                self.assertEqual(1.0, request.call_args.kwargs["timeout"])
        for error, pattern in [
            (urllib.error.HTTPError("http://synthetic.invalid", 503, "offline", {}, io.BytesIO(b"unavailable")), "HTTP 503"),
            (urllib.error.URLError("unreachable"), "connection failed"),
        ]:
            with patch.object(http.urllib.request, "urlopen", side_effect=error):
                with self.assertRaisesRegex(RuntimeError, pattern):
                    repo.typedb_http_json_request("/v1/query", {}, 2)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"not JSON"
        with patch.object(http.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                repo.typedb_http_json_request("/v1/query", {}, 2)
        repo.http_address = ""
        with self.assertRaisesRegex(RuntimeError, "address is unavailable"):
            repo.typedb_http_json_request("/v1/query", {}, 2)

    def test_schema_inspection_and_contract_markers_are_read_only(self):
        repo = self.repository()
        expected = {"schemaContractVersion": "synthetic-v1", "schemaContractFingerprint": "synthetic-fp"}
        repo.base_schema_contract_metadata = lambda: expected
        for status, metadata, result in [("ok", expected, "current"), ("ok", {}, "stale"), ("error", {}, "unavailable"), ("missing", {}, "missing")]:
            repo.read_seed_static_manifest = lambda: {"status": status, "metadata": metadata}
            self.assertEqual(result, repo.base_schema_contract_state()["status"])
        legacy = SimpleNamespace(databases=SimpleNamespace(get=lambda name: SimpleNamespace(schema=lambda: SCHEMA)))
        self.assertEqual(SCHEMA, repo.typedb_schema_text(legacy))
        self.assertIn("ontology-node", repo.typedb_schema_type_names(legacy))
        for driver in [object(), SimpleNamespace(databases=SimpleNamespace(get=lambda name: object()))]:
            with self.assertRaises(RuntimeError):
                repo.typedb_schema_text(driver)

    def test_schema_migration_errors_roll_back_without_ready_markers(self):
        repo = self.repository()
        driver = SchemaDriver(failure=1)
        with self.assertRaisesRegex(RuntimeError, "schema commit failed"):
            repo.migrate_ontology_storage_identity(driver, IMPORTED, "owns ontology-id @key")
        self.assertEqual([], driver.committed)
        self.assertEqual(1, driver.rollbacks)
        self.assertEqual({}, api.TypeDBOntologyGraphRepository._process_base_schema_ready)
        self.assertFalse(any("delete" in query.lower() for query in driver.pending))

    def test_pure_schema_plan_handles_quotes_dependency_order_and_partial_ownership(self):
        quoted = 'define attribute label, value string @values("a;b,c");'
        statements = schema_plan.schema_definition_statements(quoted)
        self.assertEqual(1, len(statements))
        self.assertEqual(1, len(schema_plan.schema_definition_clauses(statements[0])))
        ordered = schema_plan.schema_topological_definitions(["entity child, sub parent;", "entity parent, sub root;", "entity child, sub parent;"])
        self.assertEqual(["entity parent, sub root;", "entity child, sub parent;"], ordered)
        with self.assertRaisesRegex(ValueError, "Cyclic"):
            schema_plan.schema_topological_definitions(["entity a, sub b;", "entity b, sub a;"])
        plan = schema_plan.base_schema_bootstrap_plan(SCHEMA, PARTIAL, batch_size=1)
        queries = "\n".join(row["query"] for row in plan)
        self.assertNotIn("attribute ontology-id,", queries)
        self.assertIn("ontology-node owns ontology-storage-id @unique;", queries)
        self.assertEqual([], schema_plan.base_schema_bootstrap_plan(SCHEMA, SCHEMA))

    def test_contract_sync_reports_disabled_missing_driver_failure_and_success(self):
        repo = self.repository(retry_count=0)
        repo.base_schema_contract_metadata = lambda: {"schemaContractVersion": "synthetic"}
        repo.address = ""
        self.assertEqual("disabled", repo.sync_base_schema_contract()["status"])
        repo.address = "synthetic.invalid"
        repo.driver_imports = lambda: (None, ImportError("optional driver"))
        self.assertFalse(repo.sync_base_schema_contract()["saved"])
        repo.driver_imports = lambda: IMPORTED
        driver = Mock()
        repo.open_driver = lambda imported: driver
        repo.ensure_database = lambda driver: None
        repo.ensure_schema = Mock(side_effect=TimeoutError("schema unavailable"))
        failed = repo.sync_base_schema_contract()
        self.assertEqual("error", failed["status"])
        self.assertFalse(failed["saved"])
        driver.close.assert_called_once()
        repo.ensure_schema = lambda driver, imported: None
        result = repo.sync_base_schema_contract()
        self.assertEqual("ok", result["status"])
        self.assertEqual("current-schema-contract", result["schemaSync"]["mode"])

    def test_runtime_import_and_pure_execution_do_not_load_facade_or_driver(self):
        result = subprocess.run([sys.executable, "-c", """
import sys
import importlib
from contextlib import nullcontext
from types import SimpleNamespace
from digital_twin.modules.reasoning.infrastructure.typedb_runtime import connection, inspection, lifecycle, schema_plan
from digital_twin.modules.reasoning.infrastructure.typedb_runtime.ports import TypeDBRuntime
for module in ['bootstrap', 'connection', 'http', 'inspection', 'lifecycle', 'migrations', 'readiness', 'schema_plan', 'transactions']:
    importlib.import_module('digital_twin.modules.reasoning.infrastructure.typedb_runtime.' + module)
runtime = TypeDBRuntime(lambda seconds, label: nullcontext(), lambda: 0, lambda: 0, lambda seconds: None, lambda error: 'test-error')
schema = 'define attribute sample-value, value string;'
assert schema_plan.base_schema_bootstrap_plan(schema)
store = SimpleNamespace(schema_query=lambda: schema, _base_schema_ready_fingerprint='', process_base_schema_is_ready=lambda fp: False, _fresh_candidate_rebuild=True, _database_created_in_process=True, http_address='', _fresh_schema_bootstrap_batch_size=16, _fresh_schema_bootstrap_timeout_seconds=60, mark_process_base_schema_ready=lambda fp: None)
events = []
store.synchronize_base_schema_batches = lambda *args, **kwargs: events.append(kwargs)
lifecycle.ensure_schema(store, object(), (None, None), runtime=runtime)
assert events == [{'batch_size': 16, 'operation_timeout_seconds': 60}]
assert store._base_schema_ready_fingerprint
for name in sys.modules:
    assert '.application.' not in name, name
    assert not name.startswith(('typedb', 'pymysql', 'mysql')), name
    assert not name.startswith('digital_twin.infrastructure.'), name
"""], env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_explicit_ports_bound_dependencies_and_prevent_rule_execution(self):
        port_tree = ast.parse((PACKAGE / "ports.py").read_text())
        declarations = {
            node.name: {child.name for child in node.body if isinstance(child, ast.FunctionDef)}
            | {child.target.id for child in node.body if isinstance(child, ast.AnnAssign)}
            for node in port_tree.body if isinstance(node, ast.ClassDef)
        }
        capabilities = {"connection": "ConnectionPort", "transactions": "TransactionPort", "readiness": "SchemaCachePort", "inspection": "SchemaInspectionPort", "migrations": "SchemaMigrationPort", "bootstrap": "SchemaBootstrapPort", "http": "HttpPort", "lifecycle": "SchemaLifecyclePort"}
        for path in PACKAGE.glob("*.py"):
            tree = ast.parse(path.read_text())
            used = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "store":
                    used.add(node.attr)
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.level:
                        self.assertIn(node.module, {"ports", "constants"})
                    elif node.module.startswith("digital_twin"):
                        self.assertTrue(node.module.startswith(("digital_twin.domain.", "digital_twin.modules.reasoning.infrastructure.typeql.")), (path.name, node.module))
                    elif node.module.startswith("typedb"):
                        self.assertIn(path.stem, {"connection", "transactions"})
            if path.stem in capabilities:
                self.assertEqual(declarations[capabilities[path.stem]], used, path.name)
            self.assertTrue({"run_rulebox", "save_rulebox", "activate_scoped_abox_manifest", "write_persistence_rows"}.isdisjoint(used))
        self.assertEqual({"timeout", "monotonic", "perf_counter", "sleep", "error_code"}, set(ports.TypeDBRuntime.__dataclass_fields__))


if __name__ == "__main__":
    unittest.main()
