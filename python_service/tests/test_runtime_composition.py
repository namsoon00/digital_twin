import ast
import importlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1] / "digital_twin"


class RuntimeCompositionTests(unittest.TestCase):
    def test_v2_composition_phases_keep_the_pre_migration_control_flow(self):
        fixture = json.loads(
            (ROOT.parent / "tests/fixtures/reasoning_composition_v6.json").read_text()
        )
        for entry in fixture["phases"]:
            tree = ast.parse((ROOT / "infrastructure/composition" / entry["file"]).read_text())
            function = next(
                n
                for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == entry["function"]
            )
            body = function.body[entry["importCount"] :]
            if entry["returnBundle"]:
                self.assertIsInstance(body[-1], ast.Return)
                body = body[:-1]
            actual = hashlib.sha256(
                ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False).encode()
            ).hexdigest()
            self.assertEqual(entry["bodyHash"], actual, entry["file"])

    def test_v2_launch_copies_settings_and_respects_existing_database_provisioning(self):
        from digital_twin.infrastructure.composition.reasoning_launch import prepare_v2_launch

        settings = {
            "typedbDatabase": "previous",
            "ontologyTemporalObservationAnchorProjectionEnabled": "auto",
        }
        descriptor = SimpleNamespace(
            deployment_id="fixture-v2",
            engine_version="v2",
            status="provisioning",
            time_series_backend_id="fixture-series",
            release_bundle=SimpleNamespace(feature_set_version="fixture-features"),
        )
        platform = Mock()
        platform.deployment_descriptor.return_value = descriptor
        platform.graph_database_for.return_value = "fixture-graph"
        platform.registry.get.return_value = {
            "health": {
                "graphStoreProvisioning": {"mode": "reuse-existing", "database": "fixture-graph"}
            }
        }
        with patch(
            "digital_twin.infrastructure.reasoning_engine_factory.build_reasoning_engine_platform",
            return_value=platform,
        ):
            result = prepare_v2_launch(settings, "fixture-v2")
        self.assertEqual("previous", settings["typedbDatabase"])
        self.assertEqual("auto", settings["ontologyTemporalObservationAnchorProjectionEnabled"])
        self.assertEqual("fixture-graph", result.candidate_settings["typedbDatabase"])
        self.assertEqual("fixture-series", result.candidate_settings["timeSeriesActiveBackendId"])
        self.assertEqual(
            "0", result.candidate_settings["ontologyTemporalObservationAnchorProjectionEnabled"]
        )
        self.assertNotIn("typedbFreshCandidateRebuild", result.candidate_settings)
        self.assertIsNot(result.store_settings, result.candidate_settings)
        platform.initialize.assert_called_once_with()
        platform.registry.get.return_value = {"health": {}}
        with patch(
            "digital_twin.infrastructure.reasoning_engine_factory.build_reasoning_engine_platform",
            return_value=platform,
        ):
            fresh = prepare_v2_launch(settings, "fixture-v2")
        self.assertEqual("1", fresh.candidate_settings["typedbFreshCandidateRebuild"])

    def test_v2_launch_rejects_a_different_engine_before_opening_its_graph(self):
        from digital_twin.infrastructure.composition.reasoning_launch import prepare_v2_launch

        platform = Mock()
        platform.deployment_descriptor.return_value = SimpleNamespace(engine_version="v1")
        with patch(
            "digital_twin.infrastructure.reasoning_engine_factory.build_reasoning_engine_platform",
            return_value=platform,
        ):
            with self.assertRaisesRegex(RuntimeError, "descriptor is unavailable"):
                prepare_v2_launch({"fixture": "value"}, "wrong-version")
        platform.graph_database_for.assert_not_called()
        platform.registry.get.assert_not_called()

    def test_v2_warmup_uses_frozen_rules_and_rejects_an_incomplete_release(self):
        from digital_twin.infrastructure.composition.reasoning_warmup import warm_v2_release

        recorder = Mock()
        recorder.ensure_rulebox_ready.return_value = {
            "status": "ready",
            "rules": ["not-authoritative"],
        }
        recorder.world_rule_partition.return_value = {
            "status": "ready",
            "sharedRules": ["shared"],
            "overlayRules": ["overlay"],
        }
        with patch(
            "digital_twin.infrastructure.graph_store_rulebox.rulebox_rules_from_payload",
            return_value=["parsed"],
        ) as parse, patch(
            "digital_twin.domain.ontology_compiler.compile_ontology_release",
            return_value={"valid": True},
        ) as compile_release:
            warm_v2_release(recorder, {"rules": ["frozen"]})
            parse.assert_called_once_with({"rules": ["frozen"]})
            compile_release.assert_called_once_with(["parsed"])
            self.assertEqual(2, recorder.catalog_for_rules.call_count)
            recorder.reset_mock()
            compile_release.return_value = {"valid": False, "failures": ["incomplete"]}
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                warm_v2_release(recorder, {"rules": ["frozen"]})
            recorder.catalog_for_rules.assert_not_called()

    def test_v2_composition_phase_imports_do_not_open_the_runtime(self):
        self.run_isolated(
            """
import importlib, sys
for phase in ('launch', 'binding', 'warmup', 'release_health', 'delivery'):
    importlib.import_module('digital_twin.infrastructure.composition.reasoning_' + phase)
for name in sys.modules:
    assert '.application.' not in name, name
    assert name not in {'digital_twin.infrastructure.settings', 'digital_twin.infrastructure.typedb_ontology'}, name
"""
        )

    def run_isolated(self, code):
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=dict(os.environ, PYTHONPATH=str(ROOT.parent)),
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_runtime_entry_points_resolve_without_loading_business_or_database_implementations(
        self,
    ):
        self.run_isolated(
            """
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
"""
        )

    def test_runtime_valuation_builder_loads_only_its_read_use_case(self):
        self.run_isolated(
            """
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
"""
        )

    def test_runtime_export_catalog_covers_real_builders_without_fallback_wiring(self):
        from digital_twin.infrastructure import mysql_operational, service_factory

        self.assertEqual(set(service_factory.__all__), set(service_factory._EXPORTS))
        for name, (module, attribute) in service_factory._EXPORTS.items():
            self.assertTrue(module.startswith("digital_twin.infrastructure.composition."))
            self.assertIs(
                getattr(service_factory, name), getattr(importlib.import_module(module), attribute)
            )
        exported = set(service_factory.__all__)
        for path in (ROOT / "infrastructure/composition").glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name.startswith("build_"):
                    self.assertIn(node.name, exported, str(path))
        facade = ast.parse((ROOT / "infrastructure/service_factory.py").read_text())
        self.assertEqual(
            ["__getattr__"],
            [node.name for node in facade.body if isinstance(node, ast.FunctionDef)],
        )
        with self.assertRaises(AttributeError):
            getattr(service_factory, "undeclared_runtime_builder")
        self.assertEqual(set(mysql_operational.__all__), set(mysql_operational._EXPORTS))
        for name, (module, attribute) in mysql_operational._EXPORTS.items():
            self.assertIs(
                getattr(mysql_operational, name),
                getattr(importlib.import_module(module), attribute),
            )
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
        from digital_twin.modules.accounts.infrastructure.mysql_account_reader import (
            MySQLAccountReader,
        )
        from digital_twin.modules.instruments.infrastructure.mysql_account_watchlist import (
            MySQLAccountWatchlistRepository,
        )

        for name in [
            "upsert",
            "upsert_with_event",
            "patch_with_event",
            "remove",
            "remove_with_event",
        ]:
            self.assertFalse(hasattr(MySQLAccountReader, name), name)
            self.assertFalse(hasattr(MySQLAccountWatchlistRepository, name), name)
        self.assertFalse(hasattr(MySQLAccountReader, "mutate_watchlist"))
        self.assertTrue(hasattr(MySQLAccountWatchlistRepository, "mutate_watchlist"))


if __name__ == "__main__":
    unittest.main()
