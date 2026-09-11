"""Integrated ownership and failure-path regressions without production I/O."""

import ast
import builtins
import copy
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import symtable
import sys
from types import SimpleNamespace
import typing
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1] / "digital_twin"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
OWNERSHIP = json.loads((FIXTURES / "backend_integration_ownership_v1.json").read_text())
STORAGE = json.loads((FIXTURES / "storage_ownership_v1.json").read_text())
INTENTIONAL_CHANGES = {
    "save_rulebox": "Restore verified in-memory rules after failed publication.",
    "repository_world_call": "Negotiate optional arguments before exactly one call.",
    "recover_pending_abox_activation": "Never retry an adapter's internal TypeError.",
}


class NormalizeBindings(ast.NodeTransformer):
    def visit_Name(self, node):
        if node.id == "_store":
            node.id = "self"
        return node

    def visit_Attribute(self, node):
        if isinstance(node.value, ast.Name) and node.value.id == "_bindings":
            return ast.Name(id=node.attr, ctx=node.ctx)
        return self.generic_visit(node)


class WithoutImports(ast.NodeTransformer):
    def visit_Import(self, node):
        return None

    def visit_ImportFrom(self, node):
        return None


def body_hash(node):
    node = NormalizeBindings().visit(copy.deepcopy(node))
    if (
        node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    ):
        node.body[0].value.value = inspect.cleandoc(node.body[0].value.value)
    return hashlib.sha256(
        ast.dump(
            ast.Module(body=node.body, type_ignores=[]), include_attributes=False
        ).encode()
    ).hexdigest()


def module_name(path):
    return "digital_twin." + ".".join(path.with_suffix("").parts)


def implementations():
    paths = {ROOT / entry["path"] for entry in STORAGE["adapters"].values()}
    for package in ("graph_writes", "projection_write", "projection_policy"):
        paths.update((ROOT / "modules/reasoning/infrastructure" / package).glob("*.py"))
    return sorted(paths)


class BackendIntegrationTests(unittest.TestCase):
    def test_integrated_projection_and_write_bodies_preserve_baseline_except_audited_failures(
        self,
    ):
        trees = {}
        changed = set()
        for name, entry in OWNERSHIP["methods"].items():
            with self.subTest(method=name):
                path = entry["path"]
                if path not in trees:
                    trees[path] = {
                        n.name: n
                        for n in ast.parse((ROOT / path).read_text()).body
                        if isinstance(n, ast.FunctionDef)
                    }
                method = trees[path][name.split(".")[-1]]
                if method.name in INTENTIONAL_CHANGES:
                    changed.add(method.name)
                    self.assertNotEqual(entry["bodyHash"], body_hash(method))
                else:
                    self.assertEqual(entry["bodyHash"], body_hash(method))
        self.assertEqual(set(INTENTIONAL_CHANGES), changed)
        self.assertEqual(144, len(OWNERSHIP["methods"]))

    def test_integrated_facades_retain_signatures_guards_and_explicit_bindings(self):
        trees = {}
        for name, entry in OWNERSHIP["methods"].items():
            path = entry["source"]
            if path not in trees:
                trees[path] = {
                    c.name + "." + n.name: n
                    for c in ast.parse((ROOT / path).read_text()).body
                    if isinstance(c, ast.ClassDef)
                    for n in c.body
                    if isinstance(n, ast.FunctionDef)
                }
            method = trees[path][".".join(name.split(".")[1:])]
            with self.subTest(method=name):
                self.assertEqual(
                    entry["signature"], ast.dump(method.args, include_attributes=False)
                )
                self.assertEqual(
                    entry["decorators"], [ast.unparse(d) for d in method.decorator_list]
                )
                self.assertEqual(1, len(method.body))
                call = method.body[0].value
                self.assertEqual(method.name, call.func.attr)
                bindings = next(
                    (n.value for n in call.keywords if n.arg == "_bindings"), None
                )
                self.assertEqual(
                    set(entry["runtimeFields"]),
                    {n.arg for n in bindings.keywords} if bindings else set(),
                )

    def test_integrated_ports_declare_only_used_store_capabilities_with_resolvable_types(
        self,
    ):
        for path in implementations():
            if not path.name.endswith("_ports.py"):
                continue
            tree = ast.parse(path.read_text())
            implementation = path.with_name(path.name.replace("_ports", ""))
            used = {
                n.attr
                for n in ast.walk(ast.parse(implementation.read_text()))
                if isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id == "_store"
            }
            module = importlib.import_module(module_name(path.relative_to(ROOT)))
            port = next(
                (
                    c
                    for c in tree.body
                    if isinstance(c, ast.ClassDef) and c.name.endswith("Port")
                ),
                None,
            )
            declared = (
                set()
                if port is None
                else {
                    n.name if isinstance(n, ast.FunctionDef) else n.target.id
                    for n in port.body
                    if isinstance(n, (ast.FunctionDef, ast.AnnAssign))
                }
            )
            self.assertEqual(used, declared, str(path))
            for cls in (c for c in tree.body if isinstance(c, ast.ClassDef)):
                actual = getattr(module, cls.name)
                typing.get_type_hints(actual)
                for method in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
                    typing.get_type_hints(getattr(actual, method.name))

    def test_integrated_leaf_modules_have_no_unbound_runtime_globals(self):
        def global_references(table):
            names = {
                s.get_name()
                for s in table.get_symbols()
                if s.is_global() and s.is_referenced()
            }
            for child in table.get_children():
                names |= global_references(child)
            return names

        for path in implementations():
            module = importlib.import_module(module_name(path.relative_to(ROOT)))
            references = global_references(
                symtable.symtable(path.read_text(), str(path), "exec")
            )
            missing = references - set(vars(module)) - set(dir(builtins))
            self.assertEqual(set(), missing, str(path))

    def test_integrated_storage_retains_original_sql_and_transaction_bodies(self):
        for entry in STORAGE["adapters"].values():
            tree = ast.parse((ROOT / entry["path"]).read_text())
            declarations = {
                n.name: n
                for n in tree.body
                if isinstance(n, (ast.ClassDef, ast.FunctionDef))
            }
            self.assertEqual(
                set(entry["declarations"]), set(declarations), entry["path"]
            )
            for name, expected in entry["declarations"].items():
                normalized = WithoutImports().visit(copy.deepcopy(declarations[name]))
                actual = hashlib.sha256(
                    ast.dump(normalized, include_attributes=False).encode()
                ).hexdigest()
                self.assertEqual(expected, actual, (entry["path"], name))

    def test_integrated_repository_contracts_have_one_owner_and_legacy_identity(self):
        legacy = importlib.import_module("digital_twin.domain.repositories")
        for name, entry in STORAGE["ports"].items():
            path = Path(entry["path"])
            owner = path.parts[1]
            owned = importlib.import_module(module_name(path))
            contract = importlib.import_module(
                "digital_twin.modules." + owner + ".contracts"
            )
            self.assertIs(getattr(owned, name), getattr(contract, name))
            self.assertIs(getattr(owned, name), getattr(legacy, name))
            node = next(
                n
                for n in ast.parse((ROOT / path).read_text()).body
                if getattr(n, "name", None) == name
                or (isinstance(n, ast.AnnAssign) and n.target.id == name)
            )
            self.assertEqual(
                entry["astHash"],
                hashlib.sha256(
                    ast.dump(node, include_attributes=False).encode()
                ).hexdigest(),
                name,
            )
        self.assertIs(
            legacy.MarketDataProviderFactory,
            importlib.import_module(
                "digital_twin.modules.market_data.contracts"
            ).MarketDataProviderFactory,
        )

    def test_integrated_storage_facades_resolve_the_same_adapter_objects(self):
        for source, entry in STORAGE["adapters"].items():
            owned = importlib.import_module(module_name(Path(entry["path"])))
            legacy = importlib.import_module(source)
            for name in entry["declarations"]:
                self.assertIs(
                    getattr(owned, name), getattr(legacy, name), (source, name)
                )

    def test_integrated_temporal_storage_does_not_import_reasoning_workers(self):
        script = """
import sys
from digital_twin.modules.market_data.infrastructure.mysql_temporal_runtime import MySQLTemporalFeatureSnapshotStore
assert 'digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime' not in sys.modules
assert 'digital_twin.infrastructure.typedb_ontology' not in sys.modules
assert 'digital_twin.infrastructure.service_factory' not in sys.modules
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=dict(os.environ, PYTHONPATH=str(ROOT.parent)),
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_integrated_shared_domain_repository_is_exports_only(self):
        tree = ast.parse((ROOT / "domain/repositories.py").read_text())
        self.assertTrue(
            all(isinstance(n, (ast.Expr, ast.ImportFrom)) for n in tree.body)
        )
        for path in (ROOT / "modules").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                self.assertFalse(
                    isinstance(node, ast.ImportFrom)
                    and node.module == "digital_twin.domain.repositories",
                    str(path),
                )

    def test_integrated_recovery_mutation_typeerror_is_never_replayed_without_world(
        self,
    ):
        from digital_twin.infrastructure.ontology_projection import (
            PortfolioOntologyProjectionRecorder,
        )

        calls = []

        class Repository:
            def mutate(self, world_id=""):
                calls.append(world_id)
                raise TypeError("unexpected keyword in inner payload: world_id")

        recorder = PortfolioOntologyProjectionRecorder(Repository())
        with self.assertRaises(TypeError):
            recorder.repository_world_call("mutate", world_id="portfolio:account-a")
        self.assertEqual(["portfolio:account-a"], calls)

    def test_integrated_recovery_negotiates_legacy_optional_parameters_before_call(
        self,
    ):
        from digital_twin.infrastructure.ontology_projection import (
            PortfolioOntologyProjectionRecorder,
        )

        calls = []

        class Repository:
            def mutate(self, candidate):
                calls.append(candidate)
                return {"status": "ok"}

        recorder = PortfolioOntologyProjectionRecorder(Repository())
        result = recorder.repository_world_call(
            "mutate",
            "frozen-candidate",
            world_id="portfolio:fixture",
            max_staged_target_symbols=4,
        )
        self.assertEqual("ok", result["status"])
        self.assertEqual(["frozen-candidate"], calls)

    def test_integrated_pending_recovery_does_not_retry_a_partially_executed_adapter(
        self,
    ):
        from digital_twin.infrastructure.ontology_projection import (
            PortfolioOntologyProjectionRecorder,
        )

        mutation = Mock(
            side_effect=TypeError("max_staged_target_symbols invalid inside adapter")
        )
        repository = SimpleNamespace(
            store_key="typedb",
            pending_abox_activation=lambda **_: {"status": "pending"},
            recover_pending_abox_activation=mutation,
        )
        result = PortfolioOntologyProjectionRecorder(
            repository
        ).recover_pending_abox_activation("portfolio:fixture", 5)
        self.assertEqual("error", result["status"])
        mutation.assert_called_once_with(
            world_id="portfolio:fixture", max_staged_target_symbols=5
        )

    def test_integrated_empty_recovery_journal_does_not_acquire_a_writer(self):
        from digital_twin.infrastructure.ontology_projection import (
            PortfolioOntologyProjectionRecorder,
        )

        mutation = Mock(side_effect=AssertionError("No write is needed"))
        recorder = PortfolioOntologyProjectionRecorder(
            SimpleNamespace(
                store_key="typedb",
                pending_abox_activation=lambda **_: {"status": "empty"},
                recover_pending_abox_activation=mutation,
            )
        )
        self.assertEqual(
            "empty-journal",
            recorder.recover_pending_abox_activation("portfolio:fixture")[
                "recoveryPreflight"
            ],
        )
        mutation.assert_not_called()

    def rulebox_scenario(self, mode):
        from digital_twin.modules.reasoning.infrastructure.graph_writes import (
            rulebox_commands,
        )
        from digital_twin.modules.reasoning.infrastructure.graph_writes.rulebox_commands_ports import (
            SaveRuleboxBindings,
        )
        from static_seed_fixture import artifact

        old = [object()]
        candidate = [object()]
        store = SimpleNamespace(
            _last_rules=old,
            clear_rulebox_snapshot_cache=Mock(),
            rulebox_snapshot=Mock(return_value={"rules": [], "versions": []}),
            append_rulebox_version=Mock(return_value={"saved": True}),
        )

        def seed(payload):
            self.assertIs(old, store._last_rules)
            self.assertFalse(payload["clearInference"])
            store._last_rules = candidate
            if mode == "exception":
                raise RuntimeError("fixture write failed")
            return {"saved": mode == "ok", "status": "ok" if mode == "ok" else "error"}

        store.seed_ontology = seed
        payload = {"rules": artifact()["rules"]}
        bindings = SaveRuleboxBindings(utc_now=lambda: "2026-01-01T00:00:00Z")
        if mode == "exception":
            with self.assertRaisesRegex(RuntimeError, "fixture write failed"):
                rulebox_commands.save_rulebox(store, payload, _bindings=bindings)
        else:
            result = rulebox_commands.save_rulebox(store, payload, _bindings=bindings)
            self.assertEqual(mode == "ok", result["saved"])
        self.assertIs(candidate if mode == "ok" else old, store._last_rules)
        self.assertEqual(
            1 if mode == "ok" else 0, store.append_rulebox_version.call_count
        )

    def test_integrated_failed_rulebox_result_restores_previous_rules_without_history(
        self,
    ):
        self.rulebox_scenario("failure")

    def test_integrated_rulebox_exception_restores_previous_rules_without_history(self):
        self.rulebox_scenario("exception")

    def test_integrated_successful_rulebox_publication_retains_new_rules_and_history(
        self,
    ):
        self.rulebox_scenario("ok")

    def test_integrated_lease_failure_releases_only_owned_global_coordinator(self):
        from digital_twin.modules.reasoning.infrastructure.projection_write import (
            publication,
        )

        for adopted in (False, True):
            release = Mock(return_value={"status": "released"})
            store = SimpleNamespace(
                repository=SimpleNamespace(acquire_scoped_abox_write_lease=Mock()),
                acquire_projection_coordinator_lease=lambda *_: {"acquired": True},
                repository_world_call=Mock(
                    side_effect=RuntimeError("world lease failed")
                ),
                release_projection_coordinator_lease=release,
                projection_coordinator_summary=lambda value: value,
            )
            result = {"aboxSnapshotId": "candidate"}
            if adopted:
                result["_projectionCoordinatorLease"] = {"acquired": True}
            lease = publication.acquire_inference_write_lease(
                store, result, "portfolio:fixture"
            )
            self.assertFalse(lease["acquired"])
            self.assertEqual(0 if adopted else 1, release.call_count)

    def test_integrated_world_release_failure_still_releases_owned_global_lease(self):
        from digital_twin.modules.reasoning.infrastructure.projection_write import (
            publication,
        )

        release = Mock(return_value={"status": "released"})
        store = SimpleNamespace(
            repository=SimpleNamespace(
                release_scoped_abox_write_lease=Mock(
                    side_effect=RuntimeError("world release failed")
                )
            ),
            release_projection_coordinator_lease=release,
        )
        result = publication.release_inference_write_lease(
            store,
            {
                "projectionCoordinatorLeaseOwned": True,
                "_projectionCoordinatorLease": {"acquired": True},
            },
        )
        self.assertEqual("error", result["worldLease"]["status"])
        release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
