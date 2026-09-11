"""Manifest repair contracts and publication-side faults without TypeDB I/O."""

import ast
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, fields, is_dataclass
import importlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from backend_stabilization_fixtures import STAGES
from digital_twin.modules.reasoning.infrastructure.projection_write import (
    patch_manifest_identity as identity,
    patch_manifest_source as source,
    patch_manifest_results as packets,
    record,
    stage_results,
)


coordinator = importlib.import_module(record.patch_manifest.__module__)
STAMP = "2026-09-10T01:00:00Z"


def normalize(value):
    if is_dataclass(value):
        return normalize(asdict(value))
    if isinstance(value, SimpleNamespace):
        return normalize(vars(value))
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    return value


class ManifestFixture:
    def __init__(self, mode="target-scoped", merge_results=None, fault="", noop=False):
        self.calls = []
        self.fault = fault
        self.active_generation = "generation:old"
        self.active = {
            "manifestId": "manifest:active",
            "sourceObservedAt": STAMP,
            "inferenceGenerationId": "generation:old",
            "worldId": "world:portfolio",
            "releaseArtifactId": "artifact:frozen",
            "ruleFingerprint": "executable:frozen",
            "nativeRulePlannerTopology": {
                "status": "ok",
                "symbols": ["AAPL", "MSFT"],
                "symbolCount": 2,
            },
        }
        self.original_active = deepcopy(self.active)
        self.snapshot = SimpleNamespace(account_id="fixture", generated_at=STAMP)
        self.graph = SimpleNamespace(worldview={"sourceObservedAt": STAMP})
        self.persistence_graph = SimpleNamespace(
            worldview={
                "sourceObservedAt": STAMP,
                "worldId": "world:portfolio",
                "releaseArtifactId": "artifact:frozen",
                "ruleFingerprint": "executable:frozen",
            }
        )
        applied = {
            "applied": True,
            "status": "applied",
            "targetSymbols": ["AAPL"],
            "replacementSymbols": [] if noop else ["AAPL"],
            "scopeManifestFingerprint": "scopes:merged",
            "scopePlan": [{"scopeId": "a:bucket:1"}],
            "selectedIncomingScopeIds": [] if noop else ["a:bucket:1"],
            "retiredScopeIds": [],
            "deferredRelationScopeIds": ["relation:deferred"],
            "reusedActiveRelationScopeIds": ["relation:retained"],
            "manifestPatchContract": {"contractId": "patch:exact"},
        }
        self.merge_results = deepcopy(merge_results if merge_results is not None else [applied])
        self.default_applied = applied
        self.kwargs = {
            "_store": self,
            "_bindings": self,
            "active_abox": self.active,
            "compact_reasoning_context": {
                "sourceObservedAt": STAMP,
                "scopeRepairRequestsBySymbol": {},
            },
            "emit_progress": self.progress,
            "graph": self.graph,
            "graph_input": {"mode": mode},
            "market_world_context": SimpleNamespace(world_id="world:market"),
            "material_fingerprint": "incoming:fingerprint",
            "material_snapshot_id": "manifest:incoming",
            "observation_followup_targets": ["AAPL"],
            "persistence_graph": self.persistence_graph,
            "planner_topology": {"status": "ok", "symbols": ["AAPL"], "symbolCount": 1},
            "portfolio_world_context": SimpleNamespace(world_id="world:portfolio"),
            "projection_run": "audit:fixture",
            "rulebox_bootstrap": {"releaseArtifactId": "artifact:frozen"},
            "runtime_stages": {},
            "scoped_identity": {"manifestId": "manifest:incoming"},
            "shared_premise_proof": {"source": "proof:frozen"},
            "snapshot": self.snapshot,
            "target_scoped_patch": {
                "eligible": True,
                "targetSymbols": ["AAPL"],
                "scopeIntegrityAuditDue": True,
            },
            "target_symbols": ["AAPL"],
        }
        self.repaired_graph = SimpleNamespace(worldview={"sourceObservedAt": STAMP})
        self.repaired_persistence = deepcopy(self.persistence_graph)

    def step(self, name, *args):
        self.calls.append((name, *deepcopy(args)))
        occurrence = sum(call[0] == name for call in self.calls)
        if self.fault in {name, name + ":" + str(occurrence)}:
            raise RuntimeError("injected " + self.fault)

    def progress(self, name, **details):
        self.step(name, details)

    def build_projection_graph(self, snapshot, release, world, **kwargs):
        assert snapshot is self.snapshot
        assert release is self.kwargs["rulebox_bootstrap"]
        assert world is self.kwargs["portfolio_world_context"]
        assert kwargs["market_world_context"] is self.kwargs["market_world_context"]
        assert kwargs["reasoning_context"] is self.kwargs["compact_reasoning_context"]
        assert kwargs["shared_premise_proof"] is self.kwargs["shared_premise_proof"]
        self.step("build", normalize(kwargs))
        return {
            "graph": self.repaired_graph,
            "persistenceGraph": self.repaired_persistence,
            "assembly": {},
            "plannerTopology": self.kwargs["planner_topology"],
            "materialFingerprint": "repair:incoming",
            "materialSnapshotId": "manifest:repair",
            "scopedIdentity": {"manifestId": "manifest:repair"},
            "runtimeStages": {"assemblyMs": 3},
        }

    def target_scoped_patch_targets(self, snapshot, active, scoped, symbols, **kwargs):
        assert snapshot is self.snapshot and active is self.active
        self.step("select-repair-targets", symbols, kwargs)
        return deepcopy(self.kwargs["target_scoped_patch"])

    def repair_epochs(self, graph, active, requests):
        assert active is self.active
        self.step("repair-epochs", requests)
        graph.worldview["repairEpochApplied"] = True
        return {"status": "repaired", "applied": True, "repairedScopeIds": ["scope:repair"]}

    def merge(self, graph, active, symbols, **kwargs):
        assert active is self.active
        self.step("merge", symbols, kwargs)
        result = self.merge_results.pop(0)
        return result

    def topology(self, active, incoming, symbols):
        self.step("topology", active, incoming, symbols)
        return {
            "status": "ok",
            "topology": {"status": "ok", "symbols": ["AAPL", "MSFT"], "symbolCount": 2},
        }

    def fingerprint(self, scopes, topology):
        self.step("fingerprint", scopes, topology)
        return "fingerprint:merged"

    def plan(self, graph, scopes, **kwargs):
        self.step("identity", scopes, kwargs)
        graph.worldview["materialFingerprint"] = kwargs["material_fingerprint"]
        return {"manifestId": "manifest:merged", **kwargs}

    def compact_target_scope_selection_trace(self, applied):
        self.step("trace")
        return {"relationRebindRootScopeIds": ["scope:semantic-root"]}

    def scope_integrity_audit_interval_minutes(self):
        self.step("audit-interval")
        return 7

    def active_graph_store_key(self):
        return "fixture"

    def store_projection_result(self, snapshot, result, audit=None):
        assert snapshot is self.snapshot
        self.step("store-result", result, audit)

    def run(self, implementation=coordinator):
        replacements = {
            "apply_scoped_abox_repair_epochs": self.repair_epochs,
            "merge_target_scoped_abox_manifest": self.merge,
            "merge_native_rule_planner_topology": self.topology,
            "native_rule_planner_manifest_fingerprint": self.fingerprint,
            "apply_scoped_manifest_plan": self.plan,
        }
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(coordinator.time, "perf_counter", side_effect=iter(range(100, 130)))
            )
            for module in (implementation, source, identity):
                for name, replacement in replacements.items():
                    if hasattr(module, name):
                        stack.enter_context(patch.object(module, name, replacement))
            return implementation.patch_manifest(**self.kwargs)


class InternalManifestTests(unittest.TestCase):
    def test_packets_are_frozen_and_contain_no_runtime_capabilities(self):
        for name in (
            "RepairSourceInput",
            "RepairSourceResult",
            "ManifestIdentityInput",
            "ManifestIdentityResult",
            "AppliedPatchInput",
            "FailedPatchInput",
        ):
            cls = getattr(packets, name)
            packet = cls(**{field.name: None for field in fields(cls)})
            with self.assertRaises(FrozenInstanceError):
                setattr(packet, fields(cls)[0].name, "replacement")
        for module in (source, identity):
            tree = ast.parse(Path(module.__file__).read_text())
            self.assertFalse(
                any(
                    isinstance(n, ast.Name) and n.id in {"_store", "_bindings"}
                    for n in ast.walk(tree)
                )
            )

    def test_ineligible_patch_is_identity_without_callbacks(self):
        fixture = ManifestFixture()
        fixture.kwargs["target_scoped_patch"]["eligible"] = False
        result = fixture.run()
        self.assertIs(fixture.graph, result.graph)
        self.assertIs(fixture.persistence_graph, result.persistence_graph)
        self.assertEqual([], fixture.calls)

    def test_applied_patch_retains_deferred_relations_and_frozen_source_release_world(self):
        fixture = ManifestFixture()
        result = fixture.run()
        self.assertEqual("manifest:merged", result.material_snapshot_id)
        self.assertEqual("fingerprint:merged", result.material_fingerprint)
        self.assertEqual("world:portfolio", result.scoped_identity["world_id"])
        self.assertEqual(
            ["scope:semantic-root"], result.target_scoped_patch["relationRebindRootScopeIds"]
        )
        self.assertEqual(
            ["relation:deferred"], result.target_scoped_patch["deferredRelationScopeIds"]
        )
        self.assertEqual(
            ["relation:retained"], result.target_scoped_patch["reusedActiveRelationScopeIds"]
        )
        self.assertEqual(
            {"contractId": "patch:exact"}, result.target_scoped_patch["manifestPatchContract"]
        )
        self.assertEqual(STAMP, result.persistence_graph.worldview["sourceObservedAt"])
        self.assertEqual("artifact:frozen", result.persistence_graph.worldview["releaseArtifactId"])
        self.assertEqual("executable:frozen", result.persistence_graph.worldview["ruleFingerprint"])
        self.assertEqual(fixture.original_active, fixture.active)

    def test_semantic_noop_reuses_verified_active_topology(self):
        fixture = ManifestFixture(noop=True)
        result = fixture.run()
        self.assertTrue(result.target_scoped_patch["semanticNoop"])
        self.assertNotIn("topology", [call[0] for call in fixture.calls])
        topology = next(call[2] for call in fixture.calls if call[0] == "fingerprint")
        self.assertEqual(fixture.active["nativeRulePlannerTopology"], topology)

    def test_complete_source_repair_is_once_with_original_snapshot_and_bounded_targets(self):
        fixture = ManifestFixture()
        fixture.merge_results = [
            {"status": "missing-endpoint", "missingEndpointScopeIds": ["scope:missing"]},
            fixture.default_applied,
        ]
        result = fixture.run()
        self.assertIs(fixture.repaired_graph, result.graph)
        self.assertIs(fixture.repaired_persistence, result.persistence_graph)
        build = [call for call in fixture.calls if call[0] == "build"]
        self.assertEqual(1, len(build))
        self.assertFalse(build[0][1]["target_scoped_input"])
        self.assertEqual(["AAPL"], build[0][1]["target_symbols"])
        self.assertEqual("target-scoped", fixture.kwargs["graph_input"]["mode"])
        self.assertEqual(
            "observation-followup", result.persistence_graph.worldview["targetScopeRetentionMode"]
        )
        self.assertTrue(result.target_scoped_patch["repairInputFallback"]["applied"])
        self.assertEqual(3, fixture.kwargs["runtime_stages"]["targetManifestRepairInputAssemblyMs"])
        self.assertEqual(fixture.original_active, fixture.active)

    def test_blocked_local_patch_never_falls_through_to_full_publication(self):
        for requires_source in (False, True):
            failure = {
                "status": "blocked-scope",
                "requiresCompleteSource": requires_source,
                "missingEndpointScopeIds": [str(i) for i in range(70)],
            }
            fixture = ManifestFixture(merge_results=[failure, failure])
            result = fixture.run()
            self.assertIsInstance(result, stage_results.CompletedProjection)
            self.assertFalse(result.result["saved"])
            self.assertTrue(result.result["preservedActiveGeneration"])
            self.assertEqual(
                50, len(result.result["targetScopedManifestPatch"]["missingEndpointScopeIds"])
            )
            self.assertEqual(
                int(requires_source), sum(call[0] == "build" for call in fixture.calls)
            )
            self.assertEqual(1, sum(call[0] == "store-result" for call in fixture.calls))
            self.assertEqual(fixture.original_active, fixture.active)

    def test_full_source_merge_failure_keeps_original_full_fallback_identity(self):
        fixture = ManifestFixture(mode="full", merge_results=[{"status": "missing-endpoint"}])
        result = fixture.run()
        self.assertEqual("full-manifest-fallback", result.target_scoped_patch["mode"])
        self.assertEqual("manifest:incoming", result.material_snapshot_id)
        self.assertNotIn("build", [call[0] for call in fixture.calls])

    def test_partial_planning_and_diagnostic_faults_never_change_active_generation(self):
        for fault in (
            "repair-epochs",
            "merge",
            "topology",
            "fingerprint",
            "identity",
            "trace",
            "audit-interval",
            "build",
            "store-result",
            "repair-epochs:2",
            "merge:2",
            "select-repair-targets",
            "target_manifest_repair_input.done",
        ):
            with self.subTest(fault=fault):
                fixture = ManifestFixture(fault=fault)
                if fault == "build":
                    fixture.merge_results = [{"status": "missing-endpoint"}]
                elif fault == "store-result":
                    fixture.merge_results = [{"status": "blocked-scope"}]
                elif fault in {
                    "repair-epochs:2",
                    "merge:2",
                    "select-repair-targets",
                    "target_manifest_repair_input.done",
                }:
                    fixture.merge_results = [
                        {"status": "missing-endpoint"},
                        fixture.default_applied,
                    ]
                with self.assertRaisesRegex(RuntimeError, "injected " + fault):
                    fixture.run()
                self.assertEqual("generation:old", fixture.active_generation)
                self.assertEqual(fixture.original_active, fixture.active)

    def test_record_faults_on_both_sides_of_publication_retain_committed_generation_and_audit(self):
        for fault in ("before-publication", "after-publication", "followups"):
            calls = []
            state = {"active": "generation:old"}
            audit = object()
            snapshot = SimpleNamespace(account_id="fixture", generated_at=STAMP)
            store = SimpleNamespace(source="fixture", settings={}, store_projection_result=Mock())

            def invoke(name):
                def stage(**kwargs):
                    calls.append(name)
                    if name == "publish_candidate":
                        if fault == "before-publication":
                            raise RuntimeError(fault)
                        state["active"] = "generation:new"
                        if fault == "after-publication":
                            raise RuntimeError(fault)
                    if name == "schedule_followups":
                        raise RuntimeError(fault)
                    result_type = getattr(
                        stage_results, "".join(part.title() for part in name.split("_")) + "Result"
                    )
                    values = {
                        field.name: kwargs.get(field.name, {}) for field in fields(result_type)
                    }
                    if "projection_run" in values:
                        values["projection_run"] = audit
                    return result_type(**values)

                return stage

            with ExitStack() as stack:
                for name in STAGES:
                    stack.enter_context(patch.object(record, name, invoke(name)))
                result = record.record_snapshot(store, snapshot, ["AAPL"], {}, _bindings=object())
            self.assertEqual("error", result["status"])
            self.assertEqual(
                "generation:old" if fault == "before-publication" else "generation:new",
                state["active"],
            )
            self.assertIs(audit, store.store_projection_result.call_args.args[2])
            self.assertEqual(int(fault == "followups"), calls.count("schedule_followups"))


if __name__ == "__main__":
    unittest.main()
