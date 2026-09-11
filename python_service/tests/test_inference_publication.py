import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from module_migration_fixtures import is_domain_dependency
from unittest.mock import patch

from digital_twin.infrastructure import typedb_ontology as api
from digital_twin.modules.reasoning.infrastructure.inference_publication import markers, ports
from inference_publication_fixture import (
    METHODS, NOW, OTHER_WORLD, WORLD, RecordingPublicationStore,
    contract_fingerprints, graph_fixture, run_scenario,
)


ROOT = Path(__file__).resolve().parents[1] / "digital_twin"
PACKAGE = "digital_twin.modules.reasoning.infrastructure.inference_publication"
PUBLICATION = ROOT / "modules/reasoning/infrastructure/inference_publication"


def transaction_records(payload):
    result = []
    current = None
    for event in payload["journal"]:
        if event[0] == "begin":
            current = []
            result.append(current)
        if current is not None:
            current.append(event)
        if event[0] == "exit":
            current = None
    return result


def active_marker_writes(transaction):
    return [event for event in transaction if event[0] == "query" and event[1].startswith("MARKER ")
            and json.loads(event[1][7:])["kind"] == "inference-generation"]


class InferencePublicationTests(unittest.TestCase):
    def test_publication_extraction_matches_original_transaction_and_result_contracts(self):
        golden = json.loads((Path(__file__).parent / "fixtures/inference_publication_v6.json").read_text())
        self.assertEqual(golden["engineVersion"], api.TYPEDB_NATIVE_RULE_ENGINE_VERSION)
        actual = contract_fingerprints(api)
        self.assertEqual(set(golden["scenarios"]), set(actual))
        for name, expected in golden["scenarios"].items():
            with self.subTest(scenario=name, original=golden["sourceRevision"]):
                self.assertEqual(expected, actual[name])

    def test_publication_executes_with_injected_io_without_loading_the_graph_repository(self):
        result = subprocess.run([sys.executable, "-c", """
from contextlib import nullcontext
from types import SimpleNamespace
import sys
from digital_twin.modules.reasoning.infrastructure.inference_publication import lifecycle, validation, writer
from digital_twin.modules.reasoning.infrastructure.inference_publication.ports import PublicationRuntime
from inference_publication_fixture import NOW, RecordingPublicationStore, graph_fixture
runtime = PublicationRuntime(lambda: {}, lambda: NOW, lambda seconds, label: nullcontext(), lambda error: 'test-error')
implementation = SimpleNamespace(
    inference_generation_candidate_summary=validation.inference_generation_candidate_summary,
    validate_inference_generation_candidate=validation.validate_inference_generation_candidate,
    activate_inference_generation=lambda store, *args, **kwargs: lifecycle.activate_inference_generation(store, *args, runtime=runtime, **kwargs),
)
store = RecordingPublicationStore(implementation)
assert writer.write_inferencebox_graph(store, graph_fixture(), runtime=runtime)['saved']
for name in sys.modules:
    assert '.application.' not in name, name
    assert not name.startswith(('typedb', 'pymysql', 'mysql')), name
    if name.startswith('digital_twin.infrastructure.'):
        assert name == 'digital_twin.infrastructure.graph_store_payloads', name
"""], env=dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT.parent), str(Path(__file__).parent)])),
            capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_publication_ports_and_facade_keep_storage_dependencies_explicit(self):
        port_tree = ast.parse((PUBLICATION / "ports.py").read_text())
        port = next(node for node in port_tree.body if isinstance(node, ast.ClassDef) and node.name == "PublicationStore")
        declared = {node.name for node in port.body if isinstance(node, ast.FunctionDef)}
        declared.update(node.target.id for node in port.body if isinstance(node, ast.AnnAssign))
        used = set()
        for path in PUBLICATION.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "store":
                    used.add(node.attr)
                if not isinstance(node, ast.ImportFrom):
                    continue
                module = importlib.util.resolve_name("." * node.level + (node.module or ""), PACKAGE) if node.level else node.module
                if module.startswith("digital_twin"):
                    self.assertTrue(
                        is_domain_dependency(module)
                        or module.startswith((PACKAGE + ".", "digital_twin.modules.reasoning.infrastructure.typeql."))
                        or module == "digital_twin.infrastructure.graph_store_payloads",
                        (path.name, module),
                    )
        self.assertEqual(declared, used)
        self.assertTrue({"run_rulebox", "save_graph", "save_rulebox", "seed_ontology"}.isdisjoint(declared))
        tree = ast.parse((ROOT / "infrastructure/typedb_ontology.py").read_text())
        repository = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "TypeDBOntologyGraphRepository")
        for node in repository.body:
            if isinstance(node, ast.FunctionDef) and node.name in METHODS:
                self.assertEqual(1, len(node.body), node.name)
                self.assertIsInstance(node.body[0], ast.Return)
                call = node.body[0].value
                self.assertEqual(node.name, call.func.attr)
                self.assertEqual("self", call.args[0].id)
        self.assertEqual({"settings", "now", "timeout", "error_code"}, set(ports.PublicationRuntime.__dataclass_fields__))

    def test_publication_commits_candidate_then_reads_one_snapshot_before_atomic_activation(self):
        payload = run_scenario(api, "success")
        transactions = transaction_records(payload)
        reads = [tx for tx in transactions if tx[0][1] == "read"]
        self.assertEqual(1, len(reads))
        self.assertEqual(4, sum(event[0] == "read" for event in reads[0]))
        activations = [tx for tx in transactions if active_marker_writes(tx)]
        self.assertEqual(1, len(activations))
        self.assertGreater(transactions.index(activations[0]), transactions.index(reads[0]))
        queries = [event[1] for event in activations[0] if event[0] == "query"]
        self.assertEqual(3, len(queries))
        for query in queries[:2]:
            self.assertIn('has ontology-world-id "' + WORLD + '"', query)
            self.assertTrue(query.endswith("delete $n;"))
        self.assertEqual(1, sum(event[0] == "commit" for event in activations[0]))
        self.assertEqual("generation:new", payload["activeByWorld"][WORLD])
        self.assertEqual("generation:other", payload["activeByWorld"][OTHER_WORLD])
        graph = graph_fixture()
        graph.worldview.pop("inferenceGenerationAt")
        with patch.object(api, "utc_now", return_value=NOW):
            wrapper_row = api.inference_generation_marker_row(graph, [], [], "active")
        self.assertEqual(wrapper_row, markers.inference_generation_marker_row(graph, [], [], "active", now=lambda: NOW))
        self.assertEqual(WORLD, wrapper_row["worldId"])

    def test_publication_failures_never_replace_the_previous_active_generation(self):
        for scenario in ["node-write", "timeout", "relation-write", "candidate-marker", "validation-read",
                         "validation-count", "validation-source", "activation-insert", "activation-commit",
                         "driver-missing", "disabled"]:
            with self.subTest(scenario=scenario):
                payload = run_scenario(api, scenario)
                self.assertFalse(payload["result"]["saved"])
                self.assertEqual("generation:old", payload["activeByWorld"][WORLD])
                self.assertEqual("generation:other", payload["activeByWorld"][OTHER_WORLD])
                for tx in transaction_records(payload):
                    if active_marker_writes(tx):
                        self.assertFalse(any(event[0] == "commit" for event in tx))
        self.assertEqual("typedbTimeout", run_scenario(api, "timeout")["result"]["reasonCode"])

    def test_publication_distinguishes_completed_empty_results_from_incomplete_evaluation(self):
        complete = run_scenario(api, "empty-complete")["result"]
        incomplete = run_scenario(api, "empty-incomplete")["result"]
        self.assertTrue(complete["saved"])
        self.assertEqual("no-match", complete["candidateValidation"]["nativeInferenceOutcome"])
        self.assertFalse(incomplete["saved"])
        self.assertIn("candidate-empty-evaluation-not-complete", incomplete["reason"])
        stable = run_scenario(api, "stable-lease")
        self.assertTrue(stable["result"]["saved"])
        self.assertEqual("stable-write-lease", stable["result"]["candidateValidation"]["sourceAboxValidationMode"])
        self.assertFalse(any(event[0] == "active-abox" for event in stable["journal"]))

    def test_publication_retries_and_relation_fallbacks_keep_the_transaction_order(self):
        retry = run_scenario(api, "retry-node")
        self.assertTrue(retry["result"]["saved"])
        self.assertIn(["retry"], retry["journal"])
        self.assertTrue(any(event[:2] == ["exit", "rolled-back"] for event in retry["journal"]))
        fallback = run_scenario(api, "given-fallback")
        self.assertTrue(fallback["result"]["saved"])
        self.assertEqual(1, fallback["result"]["writeTiming"]["relationGivenFallbackCount"])
        self.assertEqual(1, fallback["result"]["writeTiming"]["relationLegacyQueryCount"])
        fresh = run_scenario(api, "fresh")
        self.assertTrue(fresh["result"]["writeTiming"]["candidateDeleteSkipped"])
        self.assertEqual([], fresh["pruned"])

    def test_publication_retention_is_world_scoped_and_keeps_active_results_on_failure(self):
        payload = run_scenario(api, "prune")
        self.assertEqual("ok", payload["result"]["status"])
        self.assertEqual({"generation:old", "generation:orphan"}, {generation for generation, _ in payload["pruned"]})
        self.assertEqual({WORLD}, {world for _, world in payload["pruned"]})
        for scenario in ["prune", "prune-read", "prune-write"]:
            result = run_scenario(api, scenario)
            self.assertEqual("generation:new", result["activeByWorld"][WORLD])
            self.assertEqual("generation:other", result["activeByWorld"][OTHER_WORLD])
            if scenario != "prune":
                self.assertEqual("error", result["result"]["status"])
                self.assertEqual([], result["pruned"])
        store = RecordingPublicationStore(api.TypeDBOntologyGraphRepository)
        self.assertEqual("skipped", store.prune_inferencebox_generations("")["status"])
        self.assertEqual([], store.journal)


if __name__ == "__main__":
    unittest.main()
