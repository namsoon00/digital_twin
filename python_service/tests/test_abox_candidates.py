import ast
import copy
from contextlib import contextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from module_migration_fixtures import is_domain_dependency
from unittest.mock import Mock, patch

from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_current_state import CURRENT_STATE_ABOX_PERSISTENCE_MODE
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
from digital_twin.infrastructure import typedb_ontology as api
from digital_twin.modules.reasoning.infrastructure.abox_candidates import (
    identity, recovery, row_image, rows, scope_plan, selection, validation,
)
from abox_candidate_fixture import (
    EVIDENCE, LINK, NEW, OLD, STATE, WORLD, RecoveryStore, contract_fingerprints,
    image_fixture, node, recovery_scenario,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "digital_twin/modules/reasoning/infrastructure/abox_candidates"
PREFIX = "digital_twin.modules.reasoning.infrastructure.abox_candidates"


class CandidateValidationFixture:
    scoped_abox_storage_identity = staticmethod(validation.scoped_abox_storage_identity)
    scoped_abox_storage_rows_unique = staticmethod(validation.scoped_abox_storage_rows_unique)

    def __init__(self, state):
        self.state = state
        self.reads = []

    def scoped_abox_storage_rows_by_id(self, node_storage_ids, relation_storage_ids):
        self.reads.append([list(node_storage_ids), list(relation_storage_ids)])
        return copy.deepcopy(self.state)

    def scoped_abox_storage_reuse_plan(self, node_rows, relation_rows, assume_missing_storage=False):
        return validation.scoped_abox_storage_reuse_plan(self, node_rows, relation_rows, assume_missing_storage)


class ABoxCandidateTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(api, "runtime_settings", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_extraction_preserves_original_candidate_and_recovery_contracts(self):
        golden = json.loads((ROOT / "tests/fixtures/abox_candidates_v6.json").read_text())
        self.assertEqual(api.TYPEDB_NATIVE_RULE_ENGINE_VERSION, golden["engineVersion"])
        actual = contract_fingerprints(api)
        self.assertEqual(set(golden["scenarios"]), set(actual))
        for name, expected in golden["scenarios"].items():
            with self.subTest(scenario=name, original=golden["sourceRevision"]):
                self.assertEqual(expected, actual[name])

    def test_rebound_relations_keep_active_evidence_and_bind_exact_candidate_nodes(self):
        data = image_fixture(api)
        before = copy.deepcopy(data)
        result = rows.scoped_abox_candidate_persistence_rows(**data)
        self.assertEqual("ok", result["status"])
        self.assertEqual(before, data)
        self.assertEqual([LINK], result["rebindOnlyRelationScopeIds"])
        self.assertEqual([EVIDENCE], result["deferredScopeIds"])
        self.assertEqual(1, len(result["nodeRows"]))
        self.assertEqual(1, len(result["relationRows"]))
        relation = result["relationRows"][0]
        self.assertEqual("article:published", relation["target"])
        self.assertEqual("link-new", relation["snapshotId"])
        self.assertEqual(identity.ontology_storage_id(data["current_node_rows"][0], "stock:AAA", "node"), relation["sourceStorageId"])
        self.assertEqual(data["active_scope_rows"]["relationRows"][0]["targetStorageId"], relation["targetStorageId"])
        self.assertNotIn(EVIDENCE, {row["scopeId"] for row in result["nodeRows"]})

    def test_candidate_validation_rejects_missing_rows_wrong_generations_and_unknown_endpoints(self):
        for variant, expected in [
            ("missing-active", "active-rebind-relation-count-mismatch"),
            ("missing-node", "active-rebind-endpoint-invalid"),
            ("wrong-count", "candidate-scope-row-count-mismatch"),
            ("wrong-generation", "candidate-relation-generation-mismatch"),
            ("missing-endpoint", "candidate-relation-endpoint-missing"),
        ]:
            with self.subTest(variant=variant):
                data = image_fixture(api)
                if variant == "missing-active":
                    data["active_scope_rows"]["relationRows"] = []
                elif variant == "missing-node":
                    data["current_node_rows"] = data["current_node_rows"][1:]
                elif variant == "wrong-count":
                    data["physical_scope_plan"][0]["entityCount"] = 2
                else:
                    data["semantic_changed_scope_ids"] = [STATE, LINK]
                    relation = copy.deepcopy(data["active_scope_rows"]["relationRows"][0])
                    relation.update(snapshotId="link-new", scopeGenerationId="link-new")
                    if variant == "wrong-generation":
                        relation.update(snapshotId="another-generation", scopeGenerationId="another-generation")
                    else:
                        data["active_scope_rows"]["nodeRows"] = []
                        data["current_node_rows"][1].update(snapshotId="future", scopeGenerationId="future")
                    data["current_relation_rows"] = [relation]
                result = rows.scoped_abox_candidate_persistence_rows(**data)
                self.assertEqual(expected, result["status"])
                self.assertNotIn("nodeRows", result)
                self.assertNotIn("relationRows", result)

    def test_scope_selection_keeps_semantic_changes_separate_from_transitive_rebinding(self):
        data = image_fixture(api)
        plan = copy.deepcopy(data["physical_scope_plan"])
        plan[1]["fingerprint"] = "changed-companion"
        plan.extend([
            {"scopeId": "link:dependent", "dependencyScopeIds": [LINK], "fingerprint": "same", "relationCount": 1},
            {"scopeId": "link:unrelated", "dependencyScopeIds": [EVIDENCE], "fingerprint": "same", "relationCount": 1},
        ])
        active = {"scopeFingerprints": {row["scopeId"]: "same" for row in plan},
                  "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
                  "persistenceMode": CURRENT_STATE_ABOX_PERSISTENCE_MODE}
        semantic = selection.scoped_abox_semantic_changed_scope_ids(plan, active, True)
        changed = selection.scoped_abox_changed_scope_ids(plan, active, True, relation_rebind_root_scope_ids=[STATE])
        self.assertEqual([STATE, EVIDENCE], semantic)
        self.assertEqual([STATE, EVIDENCE, LINK, "link:dependent"], changed)
        self.assertEqual(sorted([LINK, "link:dependent"]), selection.scoped_abox_rebind_only_relation_scope_ids(plan, semantic, changed))
        self.assertEqual(semantic, selection.scoped_abox_changed_scope_ids(plan, active, True, relation_rebind_root_scope_ids=[]))
        self.assertNotIn("link:unrelated", changed)

    def test_physical_plan_and_graph_copy_preserve_source_and_unchanged_generations(self):
        data = image_fixture(api)
        graph = PortfolioOntology("candidate", worldview={"aboxSnapshotId": NEW})
        graph.entities = [
            OntologyEntity("stock:AAA", "AAA", "stock", properties={"ontologyBox": "ABox", "aboxScopeId": STATE, "snapshotId": "logical"}),
            OntologyEntity("type:stock", "Stock", "class", properties={"ontologyBox": "TBox", "snapshotId": "frozen"}),
        ]
        before = asdict(graph)
        active = {"scopeGenerationIds": {STATE: "state-old", EVIDENCE: "evidence-old", LINK: "link-old"}}
        physical = scope_plan.current_state_physical_scope_plan(data["physical_scope_plan"], active, [STATE, LINK], WORLD, transition_id="one")
        by_scope = {item["scopeId"]: item for item in physical}
        self.assertEqual("evidence-old", by_scope[EVIDENCE]["generationId"])
        self.assertFalse(by_scope[EVIDENCE]["physicalGenerationChanged"])
        self.assertNotEqual("state-old", by_scope[STATE]["generationId"])
        clone = row_image.current_state_physical_graph(graph, physical)
        self.assertEqual(before, asdict(graph))
        self.assertEqual("frozen", clone.entities[1].properties["snapshotId"])
        self.assertEqual(by_scope[STATE]["generationId"], clone.entities[0].properties["snapshotId"])
        self.assertEqual(NEW, clone.entities[0].properties["manifestId"])

    def test_immutable_storage_reuse_verifies_missing_and_conflicting_readback(self):
        data = image_fixture(api)
        nodes = data["current_node_rows"]
        relation = data["active_scope_rows"]["relationRows"][0]
        node_identities = [validation.scoped_abox_storage_identity(row, "node") for row in nodes]
        relation_identity = validation.scoped_abox_storage_identity(relation, "relation")
        store = CandidateValidationFixture({"nodes": {row["storageId"]: row for row in node_identities},
                                            "relations": {relation_identity["storageId"]: relation_identity}})
        plan = store.scoped_abox_storage_reuse_plan(nodes + [nodes[0]], [relation])
        self.assertEqual("ok", plan["status"])
        self.assertEqual(2, len(plan["reusedNodeRows"]))
        self.assertEqual([], plan["nodeRowsToInsert"])
        verified = validation.scoped_abox_storage_identity_counts(store, nodes, [relation])
        self.assertEqual("ok", verified["status"])
        self.assertEqual(verified["expectedCountsByScope"], verified["actualCountsByScope"])
        store.state["nodes"].pop(node_identities[0]["storageId"])
        missing = validation.scoped_abox_storage_identity_counts(store, nodes, [relation])
        self.assertEqual("incomplete", missing["status"])
        self.assertEqual([node_identities[0]["storageId"]], missing["missingStorageIds"])
        store.state["relations"][relation_identity["storageId"]]["snapshotId"] = "different-generation"
        conflict = validation.scoped_abox_storage_identity_counts(store, nodes, [relation])
        self.assertEqual("incomplete", conflict["status"])
        self.assertEqual(1, len(conflict["conflicts"]))
        reads = len(store.reads)
        fresh = store.scoped_abox_storage_reuse_plan(nodes, [relation], assume_missing_storage=True)
        self.assertEqual("fresh-candidate-known-empty", fresh["storageLookupMode"])
        self.assertEqual(reads, len(store.reads))

    def test_endpoint_diagnostics_are_deduplicated_and_scoped_without_reads(self):
        relation = image_fixture(api)["active_scope_rows"]["relationRows"][0]
        missing = validation.missing_relation_endpoint_storage_ids([relation, relation], {relation["sourceStorageId"]: {}})
        self.assertEqual([relation["targetStorageId"]], missing)
        diagnostics = validation.missing_relation_endpoint_diagnostics([relation, relation], missing)
        self.assertEqual(1, len(diagnostics))
        self.assertEqual("target", diagnostics[0]["side"])
        self.assertEqual(LINK, diagnostics[0]["relationScopeId"])
        self.assertEqual("link-old", diagnostics[0]["relationGenerationId"])

    def test_retry_only_replays_transport_failures_and_preserves_failed_scope_diagnostics(self):
        repository = api.TypeDBOntologyGraphRepository("fixture.invalid", retry_count=1)
        with patch.object(api.time, "sleep"), patch.object(api, "typedb_error_code", side_effect=lambda error: str(error)):
            operation = Mock(side_effect=[RuntimeError("typedbConnectionError"), "recovered"])
            self.assertEqual("recovered", repository.with_scoped_abox_candidate_verification_retry(operation))
            self.assertEqual(2, operation.call_count)
            operation = Mock(side_effect=RuntimeError("typedbCandidateVerificationError"))
            timing = {}
            verification = {STATE: {"status": "ok"}, LINK: {"status": "incomplete"}}
            with self.assertRaisesRegex(RuntimeError, "typedbCandidateVerificationError"):
                repository.with_scoped_abox_candidate_verification_retry(operation, timing, verification)
            operation.assert_called_once()
            self.assertTrue(timing["candidateVerificationRetryDeferred"])
            self.assertFalse(timing["candidateVerificationRetryAttempted"])
            self.assertEqual([LINK], timing["candidateVerificationFirstFailure"]["failedScopeIds"])
            self.assertEqual({LINK: verification[LINK]}, timing["candidateVerificationFirstFailure"]["scopeVerification"])

    def test_recovery_finalizes_only_completed_aligned_native_results(self):
        for scenario in ["matched", "no-match"]:
            result = recovery_scenario(api, scenario)
            self.assertEqual("finalized", result["result"]["status"])
            self.assertEqual({"status": "empty"}, result["pending"])
            self.assertEqual(["finalize", NEW, OLD, WORLD], result["events"][-1])
        for scenario in ["incomplete", "foreign-source", "partial-targets", "marker-error", "malformed-marker", "initial-unproven"]:
            result = recovery_scenario(api, scenario)
            self.assertEqual("retry-required", result["result"]["status"])
            self.assertEqual("pending", result["pending"]["status"])
            self.assertEqual(NEW, result["active"]["aboxSnapshotId"])
            self.assertFalse(any(event[0] in {"finalize", "restore-control"} for event in result["events"]))

    def test_unreadable_or_incomplete_journals_do_not_authorize_new_judgement(self):
        for scenario in ["journal-error", "active-error", "invalid-journal", "missing-active", "lost-target", "finalize-error"]:
            result = recovery_scenario(api, scenario)
            self.assertNotIn(result["result"].get("status"), {"finalized", "restored", "skipped"})
            self.assertNotEqual("empty", result["pending"]["status"])
            if scenario != "finalize-error":
                self.assertFalse(any(event[0] in {"finalize", "restore-control", "marker"} for event in result["events"]))
        initial = recovery_scenario(api, "initial-empty-target")
        self.assertEqual("finalized-empty-target", initial["result"]["status"])
        self.assertFalse(any(event[0] == "marker" for event in initial["events"]))
        self.assertNotIn("inferenceBox", initial["result"])

    def test_staged_and_oversized_candidates_keep_control_operations_bounded(self):
        for scenario in ["staged", "initial-staged"]:
            result = recovery_scenario(api, scenario)
            self.assertEqual("staged", result["result"]["status"])
            self.assertTrue(all(event[0] in {"journal", "active"} for event in result["events"]))
        for scenario, expected in [("staged-oversized", "discarded-staged-batch"), ("active-oversized", "restored")]:
            result = recovery_scenario(api, scenario)
            self.assertEqual(expected, result["result"]["status"])
            self.assertEqual(["restore-control", OLD, WORLD], result["events"][-1])
            self.assertEqual(OLD, result["active"]["aboxSnapshotId"])
        for scenario in ["staged-oversized-control-error", "active-oversized-control-error", "stale-control-error"]:
            result = recovery_scenario(api, scenario)
            self.assertEqual("error", result["result"]["status"])
            self.assertEqual("pending", result["pending"]["status"])

    def test_shared_world_recovery_retains_its_existing_restore_policy(self):
        expected = {"shared-staged": "discarded-staged-shared-premise", "shared-unproven": "restored", "shared-control-error": "error", "shared-matched": "finalized"}
        for scenario, status in expected.items():
            result = recovery_scenario(api, scenario)
            self.assertEqual(status, result["result"]["status"])
            self.assertTrue(all(event[-1] == "premise:fixture" for event in result["events"]))
            if status == "error":
                self.assertEqual("pending", result["pending"]["status"])

    def test_repeated_recovery_is_idempotent_and_retains_empty_world_callback_contract(self):
        store = RecoveryStore(api.TypeDBOntologyGraphRepository)
        self.assertEqual("finalized", store.run()["result"]["status"])
        self.assertEqual("skipped", store.run()["result"]["status"])
        self.assertEqual(1, sum(event[0] == "finalize" for event in store.events))
        legacy = RecoveryStore(api.TypeDBOntologyGraphRepository)
        legacy.inferencebox_recovery_metadata = lambda: legacy.marker
        legacy.finalize_abox_generation = lambda active, previous: {"status": "ok"}
        self.assertEqual("finalized", legacy.run(world="")["result"]["status"])

    def test_public_recovery_facade_keeps_coordinator_guard_and_releases_on_error(self):
        repository = api.TypeDBOntologyGraphRepository("fixture.invalid", projection_coordinator_write_enforced=True)
        events = []

        @contextmanager
        def refused(owner, world):
            events.append(["refused", owner, world])
            yield {"acquired": False}

        with patch.object(repository, "projection_coordinator_write_scope", side_effect=refused), patch.object(repository, "pending_abox_activation") as read:
            result = repository.recover_pending_abox_activation(WORLD)
            self.assertEqual("deferred-projection-coordinator", result["status"])
            read.assert_not_called()
        self.assertEqual([["refused", "pending-abox-recovery", WORLD]], events)

        @contextmanager
        def acquired(owner, world):
            events.append(["acquired", owner, world])
            try:
                yield {"acquired": True}
            finally:
                events.append(["released"])

        with patch.object(repository, "projection_coordinator_write_scope", side_effect=acquired), patch.object(repository, "pending_abox_activation", return_value={"status": "pending"}), patch.object(repository, "active_abox_metadata", side_effect=RuntimeError("read failed")):
            with self.assertRaisesRegex(RuntimeError, "read failed"):
                repository.recover_pending_abox_activation(WORLD)
        self.assertEqual(["released"], events[-1])

    def test_candidate_modules_execute_without_loading_repository_or_drivers(self):
        result = subprocess.run([sys.executable, "-c", """
import importlib
from functools import partial
from types import SimpleNamespace
import sys
from digital_twin.modules.reasoning.infrastructure.abox_candidates import identity, rows, recovery
from abox_candidate_fixture import image_fixture, RecoveryStore
for name in ['identity', 'scope_plan', 'selection', 'row_image', 'rows', 'validation', 'recovery', 'retry', 'ports']:
    importlib.import_module('digital_twin.modules.reasoning.infrastructure.abox_candidates.' + name)
assert rows.scoped_abox_candidate_persistence_rows(**image_fixture(identity))['status'] == 'ok'
implementation = SimpleNamespace(
    inferencebox_matches_pending_abox_activation=lambda store, *args: recovery.inferencebox_matches_pending_abox_activation(*args),
    recover_pending_abox_activation=partial(recovery.recover_pending_abox_activation, error_code=lambda error: 'test-error'))
assert RecoveryStore(implementation).run()['result']['status'] == 'finalized'
for name in sys.modules:
    assert '.application.' not in name, name
    assert not name.startswith(('typedb', 'pymysql', 'mysql')), name
    if name.startswith('digital_twin.infrastructure.'):
        assert name == 'digital_twin.infrastructure.graph_store_payloads', name
"""], env=dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "tests")])),
            capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_ports_and_imports_prevent_candidate_writes_and_unbounded_recovery_reads(self):
        port_tree = ast.parse((PACKAGE / "ports.py").read_text())
        declared = {node.name: {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
                    | {item.target.id for item in node.body if isinstance(item, ast.AnnAssign)}
                    for node in port_tree.body if isinstance(node, ast.ClassDef)}
        owners = {"row_image": "CandidateRowMapper", "validation": "CandidateValidationStore", "recovery": "CandidateRecoveryStore", "retry": "CandidateRetryStore"}
        dependencies = {}
        for file in PACKAGE.glob("*.py"):
            used = set()
            dependencies[file.stem] = set()
            for item in ast.walk(ast.parse(file.read_text())):
                if isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name) and item.value.id == "store":
                    used.add(item.attr)
                if isinstance(item, ast.Import):
                    self.assertFalse(any(name.name.startswith(("typedb", "digital_twin.infrastructure")) for name in item.names))
                if isinstance(item, ast.ImportFrom) and item.module:
                    if item.level:
                        dependencies[file.stem].add(item.module)
                    elif item.module.startswith("digital_twin"):
                        self.assertTrue(is_domain_dependency(item.module) or item.module.startswith("digital_twin.modules.reasoning.infrastructure.") or item.module == "digital_twin.infrastructure.graph_store_payloads", (file.name, item.module))
                    self.assertFalse(item.module.startswith("typedb"))
            if file.stem in owners:
                self.assertEqual(declared[owners[file.stem]], used, file.name)
            else:
                self.assertFalse(used, file.name)
            self.assertTrue({"save_graph", "delete_box_rows_in_batches", "discard_abox_generation", "inferencebox_snapshot", "load_graph_from_typedb", "run_rulebox"}.isdisjoint(used))

        def visit(name, path):
            self.assertNotIn(name, path)
            for dependency in dependencies.get(name, set()):
                visit(dependency, path + [name])
        for name in dependencies:
            visit(name, [])
        self.assertIs(identity.ontology_storage_id, api.ontology_storage_id)
        self.assertIs(rows.scoped_abox_candidate_persistence_rows, api.TypeDBOntologyGraphRepository.scoped_abox_candidate_persistence_rows)

    def test_row_mapper_selects_changed_scopes_without_mutating_input_rows(self):
        source = node("stock:AAA", STATE, "state-new")
        target = node("article:published", EVIDENCE, "evidence-old")
        relation = {"source": source["id"], "target": target["id"], "type": "HAS_EVIDENCE", "ontologyBox": "ABox"}
        mapper = SimpleNamespace(node_rows=lambda graph: [source, target], rows_for_relations=lambda graph: [relation], support_relation_rows=lambda graph: [])
        before = copy.deepcopy(relation)
        selected_nodes, selected_relations = row_image.scoped_abox_persistence_rows(mapper, PortfolioOntology("fixture"), [STATE])
        self.assertEqual([source], selected_nodes)
        self.assertEqual(before, relation)
        self.assertEqual(STATE, selected_relations[0]["scopeId"])
        self.assertEqual(identity.ontology_storage_id(target, target["id"], "node"), selected_relations[0]["targetStorageId"])

    def test_storage_identity_is_world_generation_scoped_but_manifest_independent(self):
        value = node("stock:AAA", STATE, "one")
        original = identity.ontology_storage_id(value, value["id"], "node")
        for changes in [{"worldId": "portfolio:fixture:other"}, {"snapshotId": "two"}, {"ontologyBox": "InferenceBox"}]:
            self.assertNotEqual(original, identity.ontology_storage_id({**value, **changes}, value["id"], "node"))
        self.assertEqual(original, identity.ontology_storage_id({**value, "manifestId": "another"}, value["id"], "node"))
        material = identity.ontology_row_content_fingerprint(value, "node")
        self.assertEqual(material, identity.ontology_row_content_fingerprint({**value, "manifestId": "another", "updatedAt": "later"}, "node"))
        self.assertNotEqual(material, identity.ontology_row_content_fingerprint({**value, "currentPrice": 123}, "node"))


if __name__ == "__main__":
    unittest.main()
