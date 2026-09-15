import ast
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from module_migration_fixtures import is_domain_dependency

from digital_twin.modules.reasoning.infrastructure import typeql
from typeql_contract_fixture import WORLD, contract_fingerprints, enabled_rules, evidence_index


ROOT = Path(__file__).resolve().parents[1] / "digital_twin"
PACKAGE = "digital_twin.modules.reasoning.infrastructure.typeql"
COMPILER = ROOT / "modules/reasoning/infrastructure/typeql"
GOLDEN = Path(__file__).parent / "fixtures/typeql_compiler_v7.json"


class TypeQLCompilerTests(unittest.TestCase):
    def test_all_account_thresholds_compare_native_fields_not_default_literals(self):
        from digital_twin.modules.reasoning.infrastructure.typeql.preflight import typedb_preflight_value_matches
        from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_value_match, typedb_expected_value
        from digital_twin.modules.model_registry.domain.statistical_signals.graph_scoring import _value_matches
        dynamic = [(rule.rule_id, condition) for rule in enabled_rules()
                   for condition in rule.to_dict()["conditions"]
                   if isinstance(condition.get("value"), dict) and condition["value"].get("field")]
        self.assertEqual(9, len(dynamic))
        for rule_id, condition in dynamic:
            expected = condition["value"]
            with self.subTest(rule=rule_id):
                attribute = typeql.typedb_subject_attribute(expected["field"])
                self.assertTrue(attribute)
                result = typeql.typedb_native_condition_check_query(condition, "stock:000660", 0, world_id=WORLD)
                self.assertIn("has " + attribute, result["query"])
                self.assertIn("not {", result["query"])
                self.assertIn("Expected", result["query"])
                self.assertIsNone(typedb_preflight_value_matches(-8.2, "<=", expected))
                self.assertIsNone(_value_matches(-8.2, "<=", expected))
        with self.assertRaisesRegex(ValueError, "referenc"):
            typedb_expected_value({"field": "strategyLossTolerancePct", "default": -8})
        with self.assertRaises(ValueError):
            typedb_value_match("$s", "ontology-profit-loss-rate", {"field": "typo", "default": -8}, "<=", "$v")

    def test_compiler_matches_versioned_account_policy_contract(self):
        # V6 remains frozen; V7 records the deliberate native field comparison fix.
        expected = json.loads(GOLDEN.read_text())
        actual = contract_fingerprints(typeql)
        self.assertEqual(expected["engineVersion"], typeql.TYPEDB_NATIVE_RULE_ENGINE_VERSION)
        self.assertEqual(set(expected["groups"]), set(actual))
        for group, fingerprint in expected["groups"].items():
            with self.subTest(group=group, original=expected["sourceRevision"]):
                self.assertEqual(fingerprint, actual[group])

    def test_typeql_exports_compile_without_loading_runtime_or_database_implementations(self):
        result = subprocess.run(
            [sys.executable, "-c", """
import sys
from digital_twin.modules.reasoning.infrastructure import typeql
for name in typeql.__all__:
    getattr(typeql, name)
query = typeql.typedb_native_match_query({
    'rule_id': 'test:isolated', 'source_kind': 'stock', 'conditions': [],
}, ['005930'], scoped_manifest_only=True, world_id='portfolio:test:isolated')
assert query['query'].startswith('match ')
for name in sys.modules:
    assert '.application.' not in name, name
    assert not name.startswith(('typedb', 'pymysql', 'mysql')), name
    if name.startswith('digital_twin.infrastructure.'):
        assert name == 'digital_twin.infrastructure.graph_store_payloads', name
"""],
            env=dict(os.environ, PYTHONPATH=str(ROOT.parent)),
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_typeql_leaf_dependencies_are_acyclic_and_adapter_aliases_have_one_owner(self):
        from digital_twin.infrastructure import typedb_ontology

        self.assertEqual(set(typeql.__all__), set(typeql._EXPORTS))
        for name, (module, attribute) in typeql._EXPORTS.items():
            self.assertTrue(module.startswith(PACKAGE + "."))
            implementation = getattr(importlib.import_module(module), attribute)
            self.assertIs(getattr(typeql, name), implementation)
            self.assertIs(getattr(typedb_ontology, name), implementation)
            if callable(implementation):
                self.assertEqual(module, implementation.__module__)
        with self.assertRaises(AttributeError):
            getattr(typeql, "undeclared_compiler_export")

        edges = {}
        for path in COMPILER.glob("*.py"):
            dependencies = set()
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    self.assertEqual(0, node.level, str(path))
                    imports = [node.module or ""]
                elif isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                else:
                    continue
                for module in imports:
                    if module.startswith(PACKAGE + "."):
                        dependencies.add(module.rsplit(".", 1)[-1])
                    elif module.startswith("digital_twin"):
                        self.assertTrue(
                            is_domain_dependency(module)
                            or module in {
                                "digital_twin.modules._exports",
                                "digital_twin.infrastructure.graph_store_payloads",
                            },
                            (str(path), module),
                        )
            edges[path.stem] = dependencies

        def visit(name, ancestors):
            self.assertNotIn(name, ancestors, ancestors + [name])
            for dependency in edges[name]:
                self.assertIn(dependency, edges)
                visit(dependency, ancestors + [name])

        for name in edges:
            visit(name, [])

    def test_typeql_module_preserves_world_scope_and_explicit_unexecutable_plans(self):
        rules = enabled_rules()
        rule = next(item for item in rules if item.rule_id == "graph.execution.capacity_safe.v1").to_dict()
        for world in [WORLD, 'portfolio:query-test:beta"; $untrusted']:
            with self.subTest(world=world):
                query = typeql.typedb_native_match_query(
                    rule, ["005930"], scoped_manifest_only=True, world_id=world,
                )["query"]
                self.assertIn("has ontology-world-id " + typeql.typedb_string(world), query)
                self.assertIn('"worldview-manifest-active-pointer"', query)
                self.assertIn('"abox-scope-active-pointer"', query)
                self.assertIn("has ontology-snapshot-id $sourceScopeGenerationId", query)
                if world != WORLD:
                    self.assertNotIn(typeql.typedb_string(WORLD), query)

        index = evidence_index(rules)
        self.assertEqual(typeql.NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION, index["index"]["version"])
        indexed = typeql.typedb_native_indexed_evidence_match_query(rule, ["005930"], index, WORLD)
        self.assertEqual("ok", indexed["status"])
        self.assertEqual("condition-selector-index", indexed["activeEvidenceRelationStorageMode"])
        self.assertTrue(indexed["query"])
        for missing in [{}, {**index, "status": "unavailable"}]:
            plan = typeql.typedb_native_indexed_evidence_match_query(rule, ["005930"], missing, WORLD)
            self.assertEqual("not-eligible", plan["status"])
            self.assertEqual("", plan["query"])
            self.assertTrue(plan["reason"])
        fallback = typeql.typedb_native_rule_runtime_query_plan(
            rule, ["005930"], scoped_manifest_only=True, world_id=WORLD,
            evidence_read_index={"status": "unavailable"},
        )
        self.assertFalse(fallback["indexedEvidenceQuery"])
        self.assertTrue(fallback["indexedEvidenceFallbackReason"])
        self.assertIn('"worldview-manifest-active-pointer"', fallback["query"])

        unsupported = {**rule, "conditions": [{"kind": "subject_property", "field": "unknown-test-field"}]}
        blocked = typeql.typedb_native_match_query(unsupported, ["005930"], world_id=WORLD)
        self.assertEqual("", blocked["query"])
        self.assertEqual("unsupported subject field", blocked["reason"])
        condition = next(item for item in rule["conditions"] if item["kind"] == "relation")
        aliases = [{**condition, "condition_id": name, "role": "any", "evidence_group_key": "one-observation"}
                   for name in ["first", "alias"]]
        blocked = typeql.typedb_native_match_query(
            {**rule, "conditions": aliases, "any_condition_min_count": 2}, ["005930"], world_id=WORLD,
        )
        self.assertEqual("", blocked["query"])
        self.assertEqual("any condition minimum exceeds independent evidence groups", blocked["reason"])


if __name__ == "__main__":
    unittest.main()
