import ast
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

from digital_twin.shared_kernel.events import DomainEvent
from digital_twin.infrastructure.event_bus import EventBus


ROOT = Path(__file__).resolve().parents[1] / "digital_twin"
MODULES = {
    "accounts", "instruments", "portfolio", "market_data", "news_intelligence",
    "investment_calendar", "model_registry", "reasoning", "decisions", "outcomes",
    "notifications", "read_models",
}


class ModuleBoundaryTests(unittest.TestCase):
    def test_business_modules_have_real_implementations_and_explicit_interfaces(self):
        actual = {path.name for path in (ROOT / "modules").iterdir() if path.is_dir() and not path.name.startswith("_")}
        self.assertEqual(MODULES, actual)
        for name in sorted(MODULES):
            with self.subTest(module=name):
                package = ROOT / "modules" / name
                self.assertTrue(any(path.name != "__init__.py" for path in (package / "application").rglob("*.py")))
                for surface in ["public", "contracts"]:
                    api = importlib.import_module("digital_twin.modules." + name + "." + surface)
                    self.assertEqual(set(api.__all__), set(api._EXPORTS))
                    for exported, (target, attribute) in api._EXPORTS.items():
                        self.assertTrue(target.startswith("digital_twin.modules." + name + "."), target)
                        if surface == "contracts":
                            self.assertTrue(target.startswith("digital_twin.modules." + name + ".domain."), target)
                        self.assertIs(getattr(api, exported), getattr(importlib.import_module(target), attribute))
                    with self.assertRaises(AttributeError):
                        getattr(api, "undeclared_internal_implementation")

    def test_business_modules_cannot_import_another_modules_private_layers(self):
        violations = []
        dependencies = {owner: set() for owner in MODULES}
        for path in sorted((ROOT / "modules").rglob("*.py")):
            relative = path.relative_to(ROOT / "modules")
            if len(relative.parts) < 2:
                continue
            owner = relative.parts[0]
            package = "digital_twin.modules." + ".".join(relative.with_suffix("").parts[:-1])
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    name = node.module or ""
                    if node.level:
                        name = importlib.util.resolve_name("." * node.level + name, package)
                    names = [name] + [name + "." + alias.name for alias in node.names]
                for name in names:
                    parts = name.split(".")
                    if parts[:2] == ["digital_twin", "infrastructure"] and len(parts) >= 3:
                        if parts[2] in {"composition", "service_factory", "account_transactions", "transactions"}:
                            violations.append((str(relative), node.lineno, name))
                    if parts[:2] == ["digital_twin", "modules"] and len(parts) >= 4 and parts[2] in MODULES:
                        if parts[2] != owner and parts[3] not in {"public", "contracts"}:
                            violations.append((str(relative), node.lineno, name))
                        if parts[2] != owner and parts[3] == "public":
                            dependencies[owner].add(parts[2])
                    if "domain" in relative.parts and ("application" in parts or "infrastructure" in parts):
                        violations.append((str(relative), node.lineno, name))
        self.assertEqual([], violations)
        visited = set()

        def visit(owner, stack):
            self.assertNotIn(owner, stack, "Circular module API dependency: " + " -> ".join(stack + [owner]))
            if owner in visited:
                return
            for target in sorted(dependencies[owner]):
                visit(target, stack + [owner])
            visited.add(owner)

        for owner in sorted(MODULES):
            visit(owner, [])

    def test_account_public_api_is_synchronous_and_does_not_load_worker_infrastructure(self):
        script = """
import inspect
import sys
from digital_twin.modules.accounts.public import AccountApplicationService
from digital_twin.modules.instruments.public import AccountWatchlistService
assert not inspect.iscoroutinefunction(AccountApplicationService.save_payload)
assert not inspect.iscoroutinefunction(AccountWatchlistService.add)
for name in sys.modules:
    assert name != 'digital_twin.infrastructure.service_factory', name
    assert name != 'digital_twin.infrastructure.typedb_ontology', name
    assert '.notifications.application.' not in name, name
    assert '.decisions.application.' not in name, name
"""
        env = dict(os.environ, PYTHONPATH=str(ROOT.parent))
        result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_platform_application_directory_is_limited_to_runtime_coordination(self):
        self.assertEqual({
            "__init__.py", "data_pipeline_health_service.py", "mysql_minimal_retention_service.py",
            "operational_storage_capacity_service.py", "runtime_checkpoint.py", "scheduler.py",
        }, {path.name for path in (ROOT / "platform/application").glob("*.py")})
        self.assertEqual([], list((ROOT / "application").rglob("*.py")))

    def test_synchronous_event_delivery_records_before_consumers_and_fails_closed(self):
        steps = []
        event = DomainEvent("test.changed", "test:1")
        bus = EventBus(recorder=lambda item: steps.append(("stored", item.event_id)))
        bus.subscribe(event.name, lambda item: steps.append(("handled", item.event_id)))
        bus.publish(event)
        self.assertEqual(["stored", "handled"], [step[0] for step in steps])
        bus.dispatch_recorded(event)
        self.assertEqual(["stored", "handled", "handled"], [step[0] for step in steps])

        def fail(_event):
            raise RuntimeError("storage unavailable")

        failed = EventBus(recorder=fail)
        failed.subscribe(event.name, lambda item: self.fail("uncommitted event was delivered"))
        with self.assertRaisesRegex(RuntimeError, "storage unavailable"):
            failed.publish(event)
        self.assertEqual([], failed.published)


if __name__ == "__main__":
    unittest.main()
