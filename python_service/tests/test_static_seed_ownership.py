import ast
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from module_migration_fixtures import restore_domain_imports
from unittest.mock import Mock, patch

from digital_twin.infrastructure import typedb_ontology as api
from digital_twin.modules.reasoning.infrastructure.static_seed import (
    artifact as artifacts,
)
from static_seed_fixture import (
    artifact,
    execution_contracts,
    repository,
    restore_scenario,
)


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "digital_twin/modules/reasoning/infrastructure"
CONTRACT = ROOT / "tests/fixtures/static_seed_ownership_v1.json"


def body_hash(node):
    class Restore(ast.NodeTransformer):
        def visit_Attribute(self, value):
            if isinstance(value.value, ast.Name) and value.value.id == "_bindings":
                return ast.Name(id=value.attr, ctx=ast.Load())
            return self.generic_visit(value)

        def visit_Name(self, value):
            if value.id == "_store":
                value.id = "self"
            return value

    node = Restore().visit(restore_domain_imports(node))
    first = node.body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        first.value.value = inspect.cleandoc(first.value.value)
    return hashlib.sha256(
        ast.dump(
            ast.Module(body=node.body, type_ignores=[]), include_attributes=False
        ).encode()
    ).hexdigest()


class AtomicDriver:
    """A failed/uncommitted transaction never replaces the visible pointer."""

    def __init__(self, failure=""):
        self.visible = ["old"]
        self.failure = failure
        self.events = []
        self.transactions = 0

    def transaction(self, *args, **kwargs):
        self.transactions += 1
        driver = self

        class Transaction:
            def __enter__(self):
                self.rows = copy.deepcopy(driver.visible)
                return self

            def __exit__(self, *args):
                driver.events.append("exit")

            def query(self, query):
                self.query_text = query
                return self

            def resolve(self):
                operation = "delete" if "; delete $n;" in self.query_text else "insert"
                driver.events.append(operation)
                if driver.failure == operation:
                    raise RuntimeError("fixture " + operation + " failure")
                self.rows = [] if operation == "delete" else ["new"]
                # Readers outside this transaction still see the committed row.
                driver.events.append(("visible", tuple(driver.visible)))

            def commit(self):
                driver.events.append("commit")
                if driver.failure == "commit":
                    raise RuntimeError("fixture commit failure")
                driver.visible = copy.deepcopy(self.rows)
                if driver.failure == "ack":
                    driver.failure = ""
                    raise RuntimeError("fixture commit acknowledgement lost")

        return Transaction()


def manifest_store(failure=""):
    repo = repository(api)
    driver = AtomicDriver(failure)
    repo.driver_imports = lambda: (
        (None, None, None, None, SimpleNamespace(WRITE="write")),
        None,
    )
    repo.open_driver = lambda imported: driver
    repo.close_driver = lambda value: driver.events.append("close")
    repo.ensure_database = Mock()
    repo.ensure_schema = Mock()
    repo.write_transaction_options = lambda: None
    repo.write_operation_timeout_seconds = lambda: 1
    repo.with_typedb_retries = lambda operation: operation()
    repo.write_graph = Mock(
        side_effect=AssertionError("Manifest must not use a separate graph transaction")
    )
    return repo, driver


class StaticSeedOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.settings = patch.object(api, "runtime_settings", return_value={})
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.clock = patch.object(api, "utc_now", return_value="2026-01-01T00:00:00Z")
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_original_bodies_are_preserved_except_atomic_pointer_replacement(self):
        contract = json.loads(CONTRACT.read_text())
        entries = {**contract["methods"], **contract["helpers"]}
        self.assertEqual(33, len(entries))
        for name, entry in entries.items():
            if name == "save_seed_static_manifest":
                continue
            tree = ast.parse((INFRA / entry["path"]).read_text())
            node = next(
                n
                for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name
            )
            with self.subTest(name=name):
                self.assertEqual(entry["bodyHash"], body_hash(node))

    def test_original_schema_fingerprints_queries_and_restore_results_match(self):
        golden = json.loads(
            (ROOT / "tests/fixtures/static_seed_execution_v1.json").read_text()
        )
        self.assertEqual(golden["scenarios"], execution_contracts(api))

    def test_facade_retains_signatures_and_write_coordinator_guards(self):
        contract = json.loads(CONTRACT.read_text())
        tree = ast.parse(
            (ROOT / "digital_twin/infrastructure/typedb_ontology.py").read_text()
        )
        cls = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "TypeDBOntologyGraphRepository"
        )
        methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
        for name, entry in contract["methods"].items():
            node = methods[name]
            with self.subTest(name=name):
                self.assertEqual(
                    entry["signature"], ast.dump(node.args, include_attributes=False)
                )
                self.assertEqual(
                    entry["decorators"], [ast.unparse(n) for n in node.decorator_list]
                )
                self.assertEqual(1, len(node.body))
                self.assertIsInstance(node.body[0], ast.Return)
                self.assertEqual(name, node.body[0].value.func.attr)

    def test_each_port_declares_only_used_capabilities(self):
        for path in (INFRA / "static_seed").glob("*_ports.py"):
            port_tree = ast.parse(path.read_text())
            cls = next(
                n
                for n in port_tree.body
                if isinstance(n, ast.ClassDef) and n.name.endswith("Store")
            )
            declared = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
            declared |= {n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)}
            body = ast.parse(
                path.with_name(path.name.replace("_ports", "")).read_text()
            )
            used = {
                n.attr
                for n in ast.walk(body)
                if isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id == "_store"
            }
            self.assertEqual(used, declared, path.name)
            self.assertTrue(
                {
                    "__getattr__",
                    "send",
                    "run_rulebox",
                    "activate_inference_generation",
                }.isdisjoint(used),
                path.name,
            )
            if path.stem in {
                "schema_ports",
                "identity_ports",
                "graphs_ports",
                "reads_ports",
                "preflight_ports",
            }:
                self.assertTrue(
                    {
                        "write_graph",
                        "open_driver",
                        "save_graph",
                        "save_seed_static_manifest",
                    }.isdisjoint(used)
                )

    def test_private_imports_are_acyclic_and_do_not_initialize_runtime(self):
        names = [
            "digital_twin.modules.reasoning.infrastructure.static_seed." + p.stem
            for p in (INFRA / "static_seed").glob("*.py")
        ]
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import importlib, json, sys
for name in json.loads(sys.argv[1]):
    importlib.import_module(name)
for name in sys.modules:
    assert not name.startswith(('typedb', 'pymysql', 'mysql')), name
    assert '.application.' not in name, name
    assert name not in {'digital_twin.infrastructure.typedb_ontology', 'digital_twin.infrastructure.settings', 'digital_twin.infrastructure.graph_store_lifecycle'}, name
    assert not name.startswith('digital_twin.infrastructure.composition'), name
""",
                json.dumps(names),
            ],
            env=dict(os.environ, PYTHONPATH=str(ROOT)),
            capture_output=True,
            text=True,
            timeout=25,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_restore_rejects_invalid_artifacts_before_schema_or_writes(self):
        for mode in ["hash-error", "contract-error", "missing-box"]:
            result = restore_scenario(api, mode)
            self.assertFalse(result["result"]["saved"])
            self.assertEqual([], result["events"])
            self.assertTrue(result["authoredUnchanged"])

    def test_restore_stops_at_failed_phase_and_keeps_authored_identity_separate(self):
        for mode, absent in [
            ("schema-error", "static"),
            ("static-error", "manifest"),
            ("manifest-error", "cache-clear"),
        ]:
            value = restore_scenario(api, mode)
            self.assertFalse(value["result"]["saved"])
            self.assertNotIn(absent, value["events"])
        restored = restore_scenario(api)
        result = restored["result"]
        self.assertTrue(result["saved"])
        self.assertNotEqual(
            result["artifactRuleboxFingerprint"], result["runtimeRuleboxFingerprint"]
        )
        self.assertTrue(restored["authoredUnchanged"])
        self.assertFalse(restore_scenario(api, "readback-error")["result"]["saved"])

    def test_frozen_artifact_restore_never_reconstructs_from_current_catalog(self):
        source = artifact()
        before = copy.deepcopy(source)
        with patch.object(
            artifacts,
            "ontology_seed_graph",
            side_effect=AssertionError("Current catalog forbidden"),
        ), patch.object(
            artifacts,
            "default_graph_inference_rules",
            side_effect=AssertionError("Defaults forbidden"),
        ):
            graph = artifacts.ontology_seed_graph_from_artifact(source)
            self.assertEqual(
                source["graph"]["entities"], [e.to_dict() for e in graph.entities]
            )
            self.assertTrue(restore_scenario(api)["result"]["saved"])
        self.assertEqual(before, source)

    def test_preflight_uses_only_keyed_manifest_and_sentinel_reads(self):
        repo = repository(api)
        source = artifact()
        graph = artifacts.ontology_seed_graph_from_artifact(source)
        metadata = repo.seed_static_manifest_metadata(graph, source["rules"])
        queries = []

        def read(query, columns, **kwargs):
            queries.append(query)
            return [{"json": json.dumps(metadata)}] if columns else [{"found": True}]

        repo.read_rows = read
        result = repo.seed_graph_preflight(graph, source["rules"])
        self.assertTrue(result["ready"])
        self.assertLessEqual(len(queries), 5)
        self.assertTrue(
            all("ontology-storage-id" in q and "limit 1;" in q for q in queries)
        )
        repo.read_rows = Mock(side_effect=RuntimeError("fixture read failure"))
        result = repo.seed_graph_preflight(graph, source["rules"])
        self.assertFalse(result["ready"])
        self.assertEqual("manifest-error", result["status"])
        self.assertEqual(1, repo.read_rows.call_count)

    def test_targeted_refresh_retains_cross_box_endpoint_and_input_graph(self):
        repo = repository(api)
        source = artifact()
        graph = artifacts.ontology_seed_graph_from_artifact(source)
        before = copy.deepcopy(graph)
        sliced = repo.graph_for_boxes(
            graph, ["RuleBox"], retain_cross_box_relations=True
        )
        self.assertIn(
            "ontology-box:RuleBox", repo.external_relation_endpoint_ids(sliced)
        )
        rows, relations = repo.graph_persistence_rows(sliced)
        self.assertTrue(all(r["ontologyBox"] == "RuleBox" for r in rows))
        self.assertTrue(any(r["type"] == "DEFINES_RULE" for r in relations))
        self.assertEqual(before, graph)
        counts = {
            b: {"entityCount": 1, "relationCount": 1}
            for b in repo.seed_static_box_names()
        }
        preflight = {
            "expectedBoxCounts": counts,
            "actualBoxCounts": counts,
            "tboxMatches": True,
            "languageRegistryMatches": True,
            "ruleboxMatches": False,
        }
        self.assertEqual(
            ["RuleBox"], repo.seed_static_boxes_requiring_refresh(preflight)
        )
        self.assertEqual(
            repo.seed_static_box_names(),
            repo.seed_static_boxes_requiring_refresh(
                {**preflight, "tboxMatches": False}
            ),
        )

    def test_schema_only_bootstrap_never_rewrites_static_rows(self):
        repo = repository(api)
        repo.seed_graph_preflight = Mock(
            side_effect=[
                {
                    "ready": True,
                    "status": "current",
                    "preflightMode": "static-seed-manifest",
                    "schemaContractMatches": False,
                },
                {"ready": True, "schemaContractMatches": True},
            ]
        )
        repo.sync_base_schema_contract = Mock(return_value={"saved": True})
        repo.save_seed_static_manifest = Mock(return_value={"saved": True})
        repo.save_static_seed_boxes = Mock(
            side_effect=AssertionError("Static rewrite forbidden")
        )
        result = api.TypeDBOntologyGraphRepository.seed_ontology.__wrapped__(
            repo, {"rules": artifact()["rules"]}
        )
        self.assertTrue(result["saved"])
        self.assertTrue(result["seedSkipped"])
        repo.save_seed_static_manifest.assert_called_once()
        repo.save_static_seed_boxes.assert_not_called()

    def test_failed_static_write_never_publishes_manifest(self):
        repo = repository(api)
        repo.seed_graph_preflight = Mock(
            return_value={"ready": False, "status": "stale"}
        )
        repo.save_static_seed_boxes = Mock(
            return_value={"saved": False, "status": "error"}
        )
        repo.save_seed_static_manifest = Mock(
            side_effect=AssertionError("Publication forbidden")
        )
        repo.clear_rulebox_snapshot_cache = Mock()
        result = api.TypeDBOntologyGraphRepository.seed_ontology.__wrapped__(
            repo, {"rules": artifact()["rules"]}
        )
        self.assertFalse(result["saved"])
        repo.save_seed_static_manifest.assert_not_called()
        repo.clear_rulebox_snapshot_cache.assert_not_called()

    def test_manifest_delete_insert_and_commit_failures_preserve_previous_pointer(self):
        source = artifact()
        graph = artifacts.ontology_seed_graph_from_artifact(source)
        for failure in ["delete", "insert", "commit"]:
            repo, driver = manifest_store(failure)
            with self.subTest(failure=failure):
                result = repo.save_seed_static_manifest(
                    graph, source["rules"], schema_prepared=True
                )
                self.assertFalse(result["saved"])
                self.assertEqual(["old"], driver.visible)
                self.assertEqual(1, driver.transactions)
                self.assertEqual("close", driver.events[-1])
                repo.write_graph.assert_not_called()
                repo.ensure_schema.assert_not_called()

    def test_manifest_replacement_uses_one_commit_and_readers_keep_old_pointer(self):
        source = artifact()
        graph = artifacts.ontology_seed_graph_from_artifact(source)
        repo, driver = manifest_store()
        result = repo.save_seed_static_manifest(graph, source["rules"])
        self.assertTrue(result["saved"])
        self.assertEqual(["new"], driver.visible)
        self.assertEqual(1, driver.transactions)
        self.assertEqual(1, driver.events.count("commit"))
        self.assertTrue(
            all(e[1] == ("old",) for e in driver.events if isinstance(e, tuple))
        )
        self.assertEqual("close", driver.events[-1])
        repo.ensure_schema.assert_called_once()
        repo.write_graph.assert_not_called()

    def test_ambiguous_commit_retry_keeps_one_manifest(self):
        source = artifact()
        graph = artifacts.ontology_seed_graph_from_artifact(source)
        repo, driver = manifest_store("ack")

        def retry(operation):
            try:
                return operation()
            except RuntimeError:
                return operation()

        repo.with_typedb_retries = retry
        result = repo.save_seed_static_manifest(graph, source["rules"])
        self.assertTrue(result["saved"])
        self.assertEqual(["new"], driver.visible)
        self.assertEqual(2, driver.transactions)
        self.assertEqual(2, driver.events.count("close"))

    def test_empty_replacement_is_rejected_before_delete(self):
        source = artifact()
        repo, driver = manifest_store()
        repo.static_graph_insert_queries = lambda graph: []
        result = repo.save_seed_static_manifest(
            artifacts.ontology_seed_graph_from_artifact(source), source["rules"]
        )
        self.assertFalse(result["saved"])
        self.assertEqual(["old"], driver.visible)
        self.assertEqual(0, driver.transactions)
        self.assertEqual("close", driver.events[-1])

    def test_static_generations_append_without_deleting_active_rows(self):
        source = artifact()
        graph = artifacts.ontology_seed_graph_from_artifact(source)
        repo, driver = manifest_store()
        repo.write_graph = Mock()
        result = repo.save_static_seed_boxes(
            graph, ["RuleBox"], source["rules"], schema_prepared=True
        )
        self.assertTrue(result["saved"])
        self.assertEqual([], repo.write_graph.call_args.kwargs["delete_boxes"])
        self.assertEqual(["RuleBox"], result["refreshedBoxes"])
        self.assertEqual(["old"], driver.visible)
        repo.ensure_schema.assert_not_called()
        repo.write_graph = Mock(
            side_effect=RuntimeError("fixture static write failure")
        )
        result = repo.save_static_seed_boxes(
            graph, ["RuleBox"], source["rules"], schema_prepared=True
        )
        self.assertFalse(result["saved"])
        self.assertEqual("close", driver.events[-1])
        self.assertEqual(["old"], driver.visible)


if __name__ == "__main__":
    unittest.main()
