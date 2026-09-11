import ast
import importlib
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1] / "digital_twin"


class RuntimeCompositionTests(unittest.TestCase):
    def run_isolated(self, code):
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=dict(os.environ, PYTHONPATH=str(ROOT.parent)),
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_runtime_entry_points_resolve_without_loading_business_or_database_implementations(self):
        self.run_isolated("""
import sys
from digital_twin.infrastructure import service_factory, operational_store, mysql_operational
for name in service_factory.__all__:
    getattr(service_factory, name)
for name in sys.modules:
    assert '.application.' not in name, name
    assert name != 'digital_twin.infrastructure.typedb_ontology', name
    assert name != 'digital_twin.infrastructure.mysql_operational_connection', name
    assert name != 'digital_twin.infrastructure.account_transactions', name
assert callable(operational_store.account_reader)
assert callable(operational_store.account_watchlist_repository)
""")

    def test_runtime_valuation_builder_loads_only_its_read_use_case(self):
        self.run_isolated("""
import sys
from unittest.mock import patch
from digital_twin.infrastructure import operational_store
from digital_twin.infrastructure.service_factory import build_instrument_valuation_query_service
monitor = object()
settings = {'appTimezone': 'Asia/Seoul'}
with patch.object(operational_store, 'monitor_store', return_value=monitor) as factory:
    service = build_instrument_valuation_query_service(settings)
    assert service.monitor_store is monitor
    assert service.settings == settings
    factory.assert_called_once_with(settings)
for name in sys.modules:
    assert '.decisions.application.' not in name, name
    assert '.notifications.application.' not in name, name
    assert '.reasoning.application.' not in name, name
    assert name != 'digital_twin.infrastructure.typedb_ontology', name
    assert name != 'digital_twin.infrastructure.account_transactions', name
""")

    def test_runtime_export_catalog_covers_real_builders_without_fallback_wiring(self):
        from digital_twin.infrastructure import mysql_operational, service_factory

        self.assertEqual(set(service_factory.__all__), set(service_factory._EXPORTS))
        for name, (module, attribute) in service_factory._EXPORTS.items():
            self.assertTrue(module.startswith("digital_twin.infrastructure.composition."))
            self.assertIs(getattr(service_factory, name), getattr(importlib.import_module(module), attribute))
        exported = set(service_factory.__all__)
        for path in (ROOT / "infrastructure/composition").glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name.startswith("build_"):
                    self.assertIn(node.name, exported, str(path))
        facade = ast.parse((ROOT / "infrastructure/service_factory.py").read_text())
        self.assertEqual(["__getattr__"], [node.name for node in facade.body if isinstance(node, ast.FunctionDef)])
        with self.assertRaises(AttributeError):
            getattr(service_factory, "undeclared_runtime_builder")
        self.assertEqual(set(mysql_operational.__all__), set(mysql_operational._EXPORTS))
        for name, (module, attribute) in mysql_operational._EXPORTS.items():
            self.assertIs(getattr(mysql_operational, name), getattr(importlib.import_module(module), attribute))
        with self.assertRaises(AttributeError):
            getattr(mysql_operational, "undeclared_runtime_store")

    def test_runtime_workers_receive_account_readers_instead_of_command_repositories(self):
        violations = []
        for path in (ROOT / "infrastructure/composition").glob("*.py"):
            if path.name == "accounts.py":
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Attribute) and node.attr == "account_registry":
                    violations.append((path.name, node.lineno))
        self.assertEqual([], violations)
        from digital_twin.modules.accounts.infrastructure.mysql_account_reader import MySQLAccountReader
        from digital_twin.modules.instruments.infrastructure.mysql_account_watchlist import MySQLAccountWatchlistRepository

        for name in ["upsert", "upsert_with_event", "patch_with_event", "remove", "remove_with_event"]:
            self.assertFalse(hasattr(MySQLAccountReader, name), name)
            self.assertFalse(hasattr(MySQLAccountWatchlistRepository, name), name)
        self.assertFalse(hasattr(MySQLAccountReader, "mutate_watchlist"))
        self.assertTrue(hasattr(MySQLAccountWatchlistRepository, "mutate_watchlist"))


if __name__ == "__main__":
    unittest.main()
