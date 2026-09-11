import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from digital_twin.domain.ontology_contracts import OntologyEntity
from digital_twin.infrastructure import typedb_ontology as api
from digital_twin.modules.reasoning.infrastructure.abox_persistence import controls, ports, world_calls
from abox_persistence_fixture import (
    METHODS, NEW, NOW, OLD, OTHER_WORLD, WORLD, RecordingABoxStore,
    contract_fingerprints, control_graph, run_scenario,
)


ROOT = Path(__file__).resolve().parents[1] / "digital_twin"
PACKAGE = "digital_twin.modules.reasoning.infrastructure.abox_persistence"
PERSISTENCE = ROOT / "modules/reasoning/infrastructure/abox_persistence"


def controls_state(payload, world=WORLD):
    return {tuple(key): value for key, value in payload["controls"] if key[0] == world}


def declared_capabilities(tree, name):
    node = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == name)
    result = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
    result.update(item.target.id for item in node.body if isinstance(item, ast.AnnAssign))
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "WriteIO":
            result.update(declared_capabilities(tree, base.id))
    return result


class ABoxPersistenceTests(unittest.TestCase):
    def setUp(self):
        for binding, value in [("runtime_settings", {}), ("utc_now", NOW)]:
            patcher = patch.object(api, binding, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_storage_extraction_matches_original_execution_contracts(self):
        golden = json.loads((Path(__file__).parent / "fixtures/abox_persistence_v6.json").read_text())
        self.assertEqual(golden["engineVersion"], api.TYPEDB_NATIVE_RULE_ENGINE_VERSION)
        actual = contract_fingerprints(api)
        self.assertEqual(set(golden["scenarios"]), set(actual))
        for scenario, expected in golden["scenarios"].items():
            with self.subTest(scenario=scenario, original=golden["sourceRevision"]):
                self.assertEqual(expected, actual[scenario])

    def test_storage_and_activation_execute_without_loading_repository_or_driver(self):
        result = subprocess.run([sys.executable, "-c", """
from contextlib import nullcontext
from functools import partial
from types import SimpleNamespace
import sys
from digital_twin.modules.reasoning.infrastructure.abox_persistence import controls, lifecycle, writer
from digital_twin.modules.reasoning.infrastructure.abox_persistence.ports import ABoxRuntime
from abox_persistence_fixture import METHODS, NEW, NOW, OLD, WORLD, RecordingABoxStore, rows_fixture
runtime = ABoxRuntime(lambda: {}, lambda: NOW, lambda seconds, label: nullcontext(), lambda error: 'test-error')
modules = {'writer': writer, 'controls': controls, 'lifecycle': lifecycle}
implementation = SimpleNamespace(**{
    name: getattr(modules[module], name) if name in {
        'prepare_pending_abox_activation_for_inference', 'finalize_scoped_abox_manifest'
    } else partial(getattr(modules[module], name), runtime=runtime)
    for name, module in METHODS.items()
})
store = RecordingABoxStore(implementation)
nodes, relations = rows_fixture()
assert store.write_persistence_rows(store, store.driver_imports(), nodes, relations)['insertedNodeCount'] == 3
store.stage_candidate()
assert store.prepare_pending_abox_activation_for_inference(WORLD)['status'] == 'activated'
assert store.finalize_scoped_abox_manifest(NEW, OLD, WORLD)['status'] == 'ok'
for name in sys.modules:
    assert '.application.' not in name, name
    assert not name.startswith(('typedb', 'pymysql', 'mysql')), name
    if name.startswith('digital_twin.infrastructure.'):
        assert name == 'digital_twin.infrastructure.graph_store_payloads', name
"""], env=dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT.parent), str(Path(__file__).parent)])),
            capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_physical_writer_and_control_ports_are_explicit_and_separate(self):
        tree = ast.parse((PERSISTENCE / "ports.py").read_text())
        used = {"writer": set(), "controls": set()}
        dependencies = {}
        for path in PERSISTENCE.glob("*.py"):
            dependencies[path.stem] = set()
            for node in ast.walk(ast.parse(path.read_text())):
                owner = "writer" if path.stem == "writer" else "controls"
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "store":
                    used[owner].add(node.attr)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
                    if isinstance(node.args[0], ast.Name) and node.args[0].id == "store":
                        used[owner].add(node.args[1].value)
                if isinstance(node, ast.Import):
                    self.assertFalse(any(item.name.startswith(("typedb", "digital_twin.infrastructure")) for item in node.names))
                if not isinstance(node, ast.ImportFrom):
                    continue
                module = importlib.util.resolve_name("." * node.level + (node.module or ""), PACKAGE) if node.level else node.module
                if module.startswith(PACKAGE + "."):
                    dependencies[path.stem].add(module.rsplit(".", 1)[1])
                if module.startswith("digital_twin"):
                    self.assertTrue(module.startswith((PACKAGE + ".", "digital_twin.domain.",
                                                     "digital_twin.modules.reasoning.infrastructure.typeql."))
                                    or module == "digital_twin.infrastructure.graph_store_payloads", (path.name, module))
        self.assertEqual(declared_capabilities(tree, "ABoxRowStore"), used["writer"])
        self.assertEqual(declared_capabilities(tree, "ABoxControlStore"), used["controls"])
        self.assertNotIn("activate_scoped_abox_manifest", used["writer"])
        self.assertNotIn("write_persistence_rows", used["controls"])
        self.assertTrue({"run_rulebox", "save_rulebox", "seed_ontology", "save_graph"}.isdisjoint(set.union(*used.values())))

        def visit(name, path):
            self.assertNotIn(name, path)
            for dependency in dependencies.get(name, set()):
                visit(dependency, path + [name])
        for name in dependencies:
            visit(name, [])
        self.assertEqual({"settings", "now", "timeout", "error_code"}, set(ports.ABoxRuntime.__dataclass_fields__))

    def test_physical_writes_verify_endpoints_and_do_not_activate_partial_candidates(self):
        baseline = RecordingABoxStore(api.TypeDBOntologyGraphRepository).state()
        for scenario in ["identity-conflict", "node-write", "timeout", "inventory", "missing-endpoint", "relation-write"]:
            with self.subTest(scenario=scenario):
                payload = run_scenario(api, "writer", scenario)
                self.assertEqual("RuntimeError", payload["result"]["raised"])
                self.assertEqual(baseline, payload["controls"])
                if scenario != "relation-write":
                    self.assertFalse(any(event[0] == "query" and event[1].startswith(("GIVEN", "RELATION")) for event in payload["journal"]))
        missing = run_scenario(api, "writer", "missing-endpoint")
        self.assertEqual(3, len(missing["nodes"]))
        self.assertEqual(1, missing["trace"]["missingRelationEndpointCount"])
        success = run_scenario(api, "writer", "success")
        inventory_index = next(i for i, event in enumerate(success["journal"]) if event[0] == "inventory")
        self.assertTrue(any(event == ["commit"] for event in success["journal"][:inventory_index]))
        self.assertTrue(success["relations"])

    def test_write_reuse_retries_and_fallback_preserve_bounded_driver_lifetimes(self):
        for scenario in ["success", "fresh", "retry-node", "given-fallback", "legacy-relations"]:
            with self.subTest(scenario=scenario):
                payload = run_scenario(api, "writer", scenario)
                self.assertEqual(3, payload["result"]["insertedNodeCount"])
                self.assertEqual(2, len(payload["relations"]))
                opened = sum(event == ["open-driver"] for event in payload["journal"])
                closed = sum(event == ["close-driver"] for event in payload["journal"])
                begun = sum(event[0] == "begin" for event in payload["journal"])
                self.assertEqual(opened, closed)
                self.assertEqual(opened, begun)
        self.assertIn(["retry"], run_scenario(api, "writer", "retry-node")["journal"])
        self.assertEqual(2, run_scenario(api, "writer", "given-fallback")["result"]["relationGivenFallbackCount"])
        reused = run_scenario(api, "writer", "reused")
        self.assertEqual(3, reused["result"]["reusedNodeCount"])
        self.assertFalse(any(event[0] == "begin" for event in reused["journal"]))

    def test_control_patch_preserves_other_worlds_and_unchanged_scope_pointers(self):
        for full in [False, True]:
            with self.subTest(full=full):
                store = RecordingABoxStore(api.TypeDBOntologyGraphRepository)
                before = dict(store.controls)
                with patch.object(api, "runtime_settings", return_value={}):
                    result = store.replace_scoped_abox_control_graph(
                        store, store.driver_imports(), control_graph(), world_id=WORLD,
                        scope_ids=["scope:a", "scope:a", "scope:removed"], replace_all_scope_pointers=full,
                    )
                self.assertEqual(1, store.commits)
                self.assertTrue(result["atomic"])
                self.assertEqual(["scope:a", "scope:removed"], result["replacedScopeIds"])
                self.assertEqual({k: v for k, v in before.items() if k[0] == OTHER_WORLD},
                                 {k: v for k, v in store.controls.items() if k[0] == OTHER_WORLD})
                self.assertEqual(not full, (WORLD, "abox-scope-active-pointer", "scope:b") in store.controls)
                self.assertEqual(NEW, store.controls[WORLD, "worldview-manifest-active-pointer", ""]["manifest"])
                self.assertIn((WORLD, "abox-activation-pending", ""), store.controls)

    def test_oversized_control_patch_fails_before_any_write_at_every_limit_boundary(self):
        for raw_limit, limit in [(None, 256), (1, 8), (8, 8), (9, 9), (512, 512), (999, 512)]:
            for excess in [0, 1]:
                with self.subTest(configured=raw_limit, limit=limit, excess=excess):
                    store = RecordingABoxStore(api.TypeDBOntologyGraphRepository)
                    store.stage_candidate()
                    before = store.state()
                    scopes = ["scope:" + str(i) for i in range((limit - 4) // 2)]
                    graph = control_graph(scopes=scopes)
                    required = 2 + len(scopes) + len(graph.entities)
                    for i in range(limit + excess - required):
                        graph.entities.append(OntologyEntity(
                            "extra:" + str(i), "Extra scope", "abox-scope-active-pointer",
                            {"worldId": WORLD, "scopeId": "extra:" + str(i), "manifest": NEW},
                        ))
                    settings = {} if raw_limit is None else {"typedbScopedControlWriteTransactionQueryCount": raw_limit}
                    with patch.object(api, "runtime_settings", return_value=settings):
                        if excess:
                            with self.assertRaises(controls.AtomicControlPatchTooLarge) as caught:
                                store.replace_scoped_abox_control_graph(store, store.driver_imports(), graph,
                                                                        world_id=WORLD, scope_ids=scopes)
                            self.assertEqual(limit + 1, caught.exception.query_count)
                            self.assertEqual(limit, caught.exception.transaction_limit)
                            self.assertEqual(before, store.state())
                            self.assertEqual([], store.journal)
                        else:
                            result = store.replace_scoped_abox_control_graph(store, store.driver_imports(), graph,
                                                                            world_id=WORLD, scope_ids=scopes)
                            self.assertEqual(limit, result["queryCount"])
                            self.assertEqual(1, store.commits)
                            self.assertTrue(result["atomic"])

    def test_activation_failure_retains_recovery_journal_and_exposes_limit_diagnostics(self):
        for failure in ["control-insert", "control-delete", "commit"]:
            with self.subTest(failure=failure):
                store = RecordingABoxStore(api.TypeDBOntologyGraphRepository, failure)
                store.stage_candidate()
                before = store.state()
                result = store.prepare_pending_abox_activation_for_inference(WORLD)
                self.assertEqual("error", result["status"])
                self.assertEqual(before, store.state())
                self.assertEqual(0, store.commits)
        store = RecordingABoxStore(api.TypeDBOntologyGraphRepository)
        store.stage_candidate()
        before = store.state()
        scopes = ["scope:" + str(i) for i in range(5)]
        with patch.object(api, "runtime_settings", return_value={"typedbScopedControlWriteTransactionQueryCount": 8}), patch.object(
            store, "scoped_manifest_control_delta", return_value={"changedScopeIds": scopes, "replacedScopeIds": scopes},
        ):
            result = store.prepare_pending_abox_activation_for_inference(WORLD)
        self.assertEqual("error", result["status"])
        self.assertEqual("typedbAtomicControlPatchLimit", result["reasonCode"])
        self.assertEqual(14, result["requiredControlQueryCount"])
        self.assertEqual(8, result["atomicControlQueryLimit"])
        self.assertTrue(result["preservedPreviousAbox"])
        self.assertEqual(before, store.state())
        self.assertFalse(any(event[0] == "begin" for event in store.journal))

    def test_prepare_and_finalize_require_generation_target_and_durable_proof_alignment(self):
        for scenario in ["invalid-journal", "empty-target", "wrong-phase", "active-changed", "pending-read", "active-read"]:
            with self.subTest(prepare=scenario):
                payload = run_scenario(api, "prepare", scenario)
                self.assertNotIn(payload["result"]["status"], {"ready", "activated"})
                self.assertFalse(any(event[0] == "begin" for event in payload["journal"]))
        post_read = run_scenario(api, "prepare", "post-read")
        self.assertEqual("error", post_read["result"]["status"])
        self.assertIn((WORLD, "abox-activation-pending", ""), controls_state(post_read))
        for scenario in ["active-changed", "candidate-changed", "proof-read", "proof-stale", "proof-target", "proof-incomplete", "commit"]:
            with self.subTest(finalize=scenario):
                payload = run_scenario(api, "finalize", scenario)
                self.assertEqual("error", payload["result"]["status"])
                self.assertIn((WORLD, "abox-activation-pending", ""), controls_state(payload))
        for scenario in ["success", "no-match"]:
            payload = run_scenario(api, "finalize", scenario)
            self.assertEqual("ok", payload["result"]["status"])
            self.assertNotIn((WORLD, "abox-activation-pending", ""), controls_state(payload))
            self.assertTrue(payload["result"]["cleanupDeferred"])
            self.assertFalse(any(event[0] == "query" and 'has ontology-box "ABox"' in event[1] for event in payload["journal"]))

    def test_adapter_delegates_and_world_call_contract_remain_explicit(self):
        tree = ast.parse((ROOT / "infrastructure/typedb_ontology.py").read_text())
        delegates = {}
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            for node in cls.body:
                if isinstance(node, ast.FunctionDef) and node.name in METHODS:
                    delegates[node.name] = node
        self.assertEqual(set(METHODS), set(delegates))
        for name, node in delegates.items():
            self.assertEqual(1, len(node.body), name)
            self.assertIsInstance(node.body[0], ast.Return)
            call = node.body[0].value
            self.assertEqual("_abox_" + METHODS[name], call.func.value.id)
            self.assertEqual(name, call.func.attr)
        self.assertIs(api.typedb_call_for_world, world_calls.typedb_call_for_world)
        self.assertIs(api.TypeDBOntologyGraphRepository.scoped_abox_control_delete_query, controls.scoped_abox_control_delete_query)
        self.assertEqual("legacy", world_calls.typedb_call_for_world(lambda: "legacy", world_id=" "))
        self.assertEqual(WORLD, world_calls.typedb_call_for_world(lambda *, world_id: world_id, world_id=" " + WORLD + " "))
        query = controls.scoped_abox_control_delete_query("abox-activation-pending", WORLD, 'scope:"quoted"')
        self.assertIn('has ontology-world-id "' + WORLD + '"', query)
        self.assertIn('has ontology-scope-id "scope:\\"quoted\\""', query)


if __name__ == "__main__":
    unittest.main()
