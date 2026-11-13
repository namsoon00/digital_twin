import ast
import json
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

from module_migration_fixtures import definition_fingerprint


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "python_service/digital_twin"
INVENTORY = json.loads((ROOT / "docs/domain-ownership.json").read_text())


class DomainOwnershipCompletionTests(unittest.TestCase):
    def test_every_original_file_has_an_explicit_destination_or_removal(self):
        self.assertEqual(287, len(INVENTORY))
        for source, target in INVENTORY.items():
            with self.subTest(source=source):
                self.assertFalse((PACKAGE / source).is_file())
                for destination in target if isinstance(target, list) else [target]:
                    if destination is not None:
                        self.assertTrue((PACKAGE / destination).is_file(), destination)
        self.assertEqual([], list((PACKAGE / "domain").rglob("*.py")))
        self.assertEqual([], list((PACKAGE / "application").rglob("*.py")))

    def test_shared_kernel_is_minimal_and_business_independent(self):
        files = sorted((PACKAGE / "shared_kernel").glob("*.py"))
        self.assertEqual({"__init__.py", "clock.py", "events.py", "event_payloads.py", "parsing.py"}, {p.name for p in files})
        for path in files:
            for node in ast.walk(ast.parse(path.read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                if isinstance(node, ast.ImportFrom) and node.level:
                    names = [importlib.util.resolve_name("." * node.level + (node.module or ""), "digital_twin.shared_kernel")]
                for name in names:
                    self.assertFalse(name.startswith(("digital_twin.modules", "digital_twin.platform", "digital_twin.infrastructure")), (path.name, name))

    def test_owned_contracts_load_in_fresh_processes_without_import_order_coupling(self):
        owners = sorted(p.name for p in (PACKAGE / "modules").iterdir() if p.is_dir() and not p.name.startswith("_"))
        for order in (owners, list(reversed(owners)), owners[5:] + owners[:5]):
            script = "import importlib\nfor owner in " + repr(order) + ":\n    module = importlib.import_module('digital_twin.modules.' + owner + '.contracts')\n    for name in module.__all__: getattr(module, name)\n"
            result = subprocess.run([sys.executable, "-c", script], env=dict(os.environ, PYTHONPATH=str(PACKAGE.parent)), capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_owned_event_modules_load_and_keep_existing_news_names(self):
        event_modules = sorted({
            "digital_twin." + ".".join(path.relative_to(PACKAGE).with_suffix("").parts)
            for path in (PACKAGE / "modules").glob("*/domain/*events.py")
        })
        script = "import importlib\nfor name in " + repr(event_modules) + ": importlib.import_module(name)\n"
        result = subprocess.run([sys.executable, "-c", script], env=dict(os.environ, PYTHONPATH=str(PACKAGE.parent)), capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        events = importlib.import_module("digital_twin.modules.news_intelligence.domain.events")
        for name in (
            "ARTICLE_COLLECTED", "ARTICLE_REJECTED", "ARTICLE_ANALYZED",
            "STORY_CREATED", "STORY_UPDATED", "ARTICLE_ALERT_ELIGIBLE",
            "ARTICLE_REASONING_ELIGIBLE", "ARTICLE_RETRACTED",
        ):
            self.assertEqual("news." + name.lower(), getattr(events, name))
        self.assertEqual(
            {"name": "news.article_collected", "articleId": "fixture-article", "payload": {"revision": 1}},
            events.NewsIntelligenceEvent(events.ARTICLE_COLLECTED, "fixture-article", {"revision": 1}).to_dict(),
        )

    def test_business_code_cannot_use_removed_global_layers(self):
        for path in PACKAGE.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                for name in names:
                    self.assertFalse(name == "digital_twin.domain" or name.startswith(("digital_twin.domain.", "digital_twin.application.", "digital_twin.platform.contracts_legacy")), (str(path), name))

    def test_moved_business_definitions_keep_baseline_executable_semantics(self):
        fixture = json.loads((ROOT / "python_service/tests/fixtures/domain_semantics_v1.json").read_text())
        changes = json.loads((ROOT / "python_service/tests/fixtures/closed_loop_semantic_changes.json").read_text())["domain"]
        temporal = json.loads((ROOT / "python_service/tests/fixtures/temporal_feature_semantic_changes.json").read_text())
        prior_changes = dict(changes)
        changes.update(temporal["domain"])
        changes.update(json.loads((ROOT / "python_service/tests/fixtures/compiled_rulebox_semantic_changes.json").read_text())["domain"])
        checked_changes = set()
        checked_removals = set()
        self.assertEqual("637b49023", fixture["baseline"])
        self.assertGreater(len(fixture["definitions"]), 3000)
        parsed = {}
        for item in fixture["definitions"]:
            key = item["target"] + "::" + item["name"]
            if key in temporal["removed"]:
                moved = temporal["removed"][key]
                self.assertEqual(item["hash"], moved["baselineHash"])
                self.assertTrue(moved["reason"])
                current = ast.parse((PACKAGE / item["target"]).read_text())
                self.assertFalse(any(isinstance(n, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == item["name"] for t in n.targets) for n in current.body))
                successor = ast.parse((PACKAGE / moved["successorTarget"]).read_text())
                node = next(n for n in successor.body if isinstance(n, ast.FunctionDef) and n.name == moved["successorName"])
                self.assertEqual(moved["successorHash"], definition_fingerprint(node))
                checked_removals.add(key)
                continue
            if key in temporal["domain"]:
                self.assertEqual(prior_changes.get(key, {}).get("hash", item["hash"]), temporal["domain"][key]["previousHash"])
            path = PACKAGE / item["target"]
            if path not in parsed:
                tree = ast.parse(path.read_text())
                parsed[path] = {}
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                        parsed[path].setdefault(node.name, []).append(node)
                    elif isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                parsed[path].setdefault(target.id, []).append(node)
                    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                        parsed[path].setdefault(node.target.id, []).append(node)
            with self.subTest(source=item["source"], definition=item["name"]):
                self.assertIn(item["name"], parsed[path])
                key = item["target"] + "::" + item["name"]
                reviewed = changes.get(key)
                if reviewed:
                    checked_changes.add(key)
                    self.assertTrue(reviewed["reason"])
                    self.assertNotEqual(item["hash"], reviewed["hash"])
                self.assertEqual(reviewed["hash"] if reviewed else item["hash"], definition_fingerprint(parsed[path][item["name"]][item.get("occurrence", 0)]))
        self.assertEqual(set(changes), checked_changes)
        self.assertEqual(set(temporal["removed"]), checked_removals)
