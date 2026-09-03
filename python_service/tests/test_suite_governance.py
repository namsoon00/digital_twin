import ast
import json
import unittest
from collections import Counter
from functools import lru_cache
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
MANIFEST = TEST_DIR / "suite_manifest.json"


def _entries():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["tests"]


@lru_cache(maxsize=None)
def _test_methods(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    ]


class TestSuiteGovernanceTests(unittest.TestCase):
    def test_manifest_covers_every_discovered_test_module_exactly_once(self):
        declared = [entry["file"] for entry in _entries()]
        discovered = sorted(path.name for path in TEST_DIR.glob("test_*.py"))
        self.assertEqual(len(declared), len(set(declared)))
        self.assertEqual(sorted(declared), discovered)

    def test_curated_suite_stays_within_the_deliberate_size_budget(self):
        total = sum(len(_test_methods(TEST_DIR / entry["file"])) for entry in _entries())
        self.assertGreaterEqual(total, 600)
        # Recovery watchdog, immutable decision audit, isolated replay, and
        # user-impact-separated operational health are independent production
        # boundaries and each keeps its own contract. Official issuer,
        # financial, corporate-action, and market-index ingestion also keeps
        # adapter, ABox, world-routing, and semantic validation contracts.
        # Notification AI single-flight additionally protects semantic join,
        # material replacement, terminal suppression, and publish races.
        # Outcome-contract storage width, terminal-source queue health, and
        # material-event-to-alert coverage are separate production boundaries.
        # Durable post-retention outcomes and batch-to-subject suppression
        # resolution protect two additional alert-coverage failure modes.
        # Physical TypeDB relation-endpoint preflight prevents silent zero-row
        # match-inserts from creating incomplete candidate ABox generations.
        self.assertLessEqual(total, 896)

    def test_no_single_module_recreates_a_monolithic_regression_suite(self):
        counts = {
            entry["file"]: len(_test_methods(TEST_DIR / entry["file"]))
            for entry in _entries()
        }
        self.assertTrue(counts)
        self.assertLessEqual(max(counts.values()), 50, counts)

    def test_tiers_and_core_gate_are_explicit_and_distinct(self):
        entries = _entries()
        self.assertEqual({entry["tier"] for entry in entries}, {"unit", "contract", "integration", "system"})
        core = {entry["file"] for entry in entries if entry["core"]}
        full = {entry["file"] for entry in entries}
        self.assertTrue(core)
        self.assertLess(core, full)

    def test_test_method_names_are_unique_across_the_curated_suite(self):
        names = []
        for entry in _entries():
            names.extend(_test_methods(TEST_DIR / entry["file"]))
        duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
        self.assertEqual(duplicates, [])


if __name__ == "__main__":
    unittest.main()
