"""Cold input planning without live TypeDB, market providers or delivery."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION, SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION
from digital_twin.modules.reasoning.domain.world_partitioned_reasoning import ACCOUNT_OVERLAY_PROJECTION_CONTRACT_VERSION
from digital_twin.modules.reasoning.infrastructure.projection_write.assemble_source import assemble_source
from digital_twin.modules.reasoning.infrastructure.projection_write.select_source import select_source


def ready_manifest():
    return {"status": "ok", "scopePlan": [{"scopeId": "symbol:AAPL"}],
            "scopeGenerationIds": ["generation:previous"],
            "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            "scopeTopologyVersion": SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
            "accountOverlayProjectionContractVersion": ACCOUNT_OVERLAY_PROJECTION_CONTRACT_VERSION}


class ProjectionInputPreflightTests(unittest.TestCase):
    def assemble(self, manifest=None, fresh=False, targets=None, eligible=True):
        snapshot = object()
        events = []

        def build(source, _catalog, _world, **kwargs):
            self.assertIs(snapshot, source)
            events.append("build")
            mode = "target-scoped" if kwargs["target_scoped_input"] else "full"
            return {"graph": SimpleNamespace(worldview={}), "persistenceGraph": SimpleNamespace(worldview={}),
                    "assembly": {"inputMode": mode, "targetSymbols": ["AAPL"] if mode == "target-scoped" else ["AAPL", "MSFT"]},
                    "plannerTopology": {}, "materialFingerprint": mode, "materialSnapshotId": "source:exact",
                    "scopedIdentity": {"scopePlan": []}, "runtimeStages": {"graphBuildMs": 10}}

        def read(_world):
            events.append("read")
            return deepcopy(manifest or {})

        store = SimpleNamespace(ensure_rulebox_ready=lambda: {"status": "ready"},
            active_abox_metadata=Mock(side_effect=read), snapshot_symbols=lambda _: ["AAPL", "MSFT"],
            world_partitioned_reasoning_enabled=lambda: True, build_projection_graph=Mock(side_effect=build),
            target_scoped_patch_targets=Mock(return_value={"eligible": eligible, "fallbackReason": "fixture-ineligible"}),
            store_projection_result=Mock())
        kwargs = {"_store": store, "compact_reasoning_context": {}, "emit_progress": Mock(),
                  "market_world_context": object(), "portfolio_world_context": SimpleNamespace(world_id="world:fixture"),
                  "runtime_stages": {}, "shared_premise_proof": {}, "snapshot": snapshot,
                  "target_symbols": targets or ["AAPL"]}
        result = assemble_source(**kwargs, fresh_candidate_rebuild=fresh, projection_run=None)
        return result, kwargs, events

    def select(self, result, kwargs):
        return select_source(**kwargs, active_abox=result.active_abox, graph=result.graph,
            graph_input=result.graph_input, material_fingerprint=result.material_fingerprint,
            material_snapshot_id=result.material_snapshot_id, persistence_graph=result.persistence_graph,
            planner_topology=result.planner_topology, projection_graph=result.projection_graph,
            rulebox_bootstrap=result.rulebox_bootstrap, scoped_identity=result.scoped_identity)

    def test_cold_manifest_builds_full_source_exactly_once(self):
        result, kwargs, events = self.assemble(eligible=False)
        selected = self.select(result, kwargs)
        self.assertEqual(["read", "build"], events)
        self.assertEqual("full", result.graph_input["mode"])
        self.assertEqual("full", selected.material_fingerprint)
        self.assertEqual(1, kwargs["runtime_stages"]["fullInputPreflightSelected"])
        kwargs["_store"].build_projection_graph.assert_called_once()
        kwargs["_store"].active_abox_metadata.assert_called_once_with("world:fixture")

    def test_ready_manifest_preserves_target_scoped_assembly(self):
        result, kwargs, events = self.assemble(ready_manifest())
        self.select(result, kwargs)
        self.assertEqual(["read", "build"], events)
        self.assertEqual("target-scoped", result.graph_input["mode"])
        self.assertEqual("incremental-target-patch", result.persistence_graph.worldview["targetScopeRetentionMode"])
        self.assertEqual(0, kwargs["runtime_stages"]["fullInputPreflightSelected"])

    def test_explicit_fresh_candidate_does_not_reuse_serving_manifest(self):
        result, kwargs, events = self.assemble(ready_manifest(), fresh=True, eligible=False)
        self.select(result, kwargs)
        self.assertEqual(["build"], events)
        self.assertEqual({}, result.active_abox)
        self.assertEqual("full", result.graph_input["mode"])

    def test_incomplete_or_old_overlay_selects_complete_input_before_build(self):
        for field, value in [("scopePlan", []), ("scopeGenerationIds", []),
                             ("scopedAboxManifestVersion", "old"), ("scopeTopologyVersion", "old"),
                             ("accountOverlayProjectionContractVersion", "old"), ("status", "error")]:
            with self.subTest(field=field):
                metadata = {**ready_manifest(), field: value}
                result, kwargs, _ = self.assemble(metadata, eligible=False)
                self.select(result, kwargs)
                self.assertEqual("full", result.graph_input["mode"])
                kwargs["_store"].build_projection_graph.assert_called_once()

    def test_complete_target_set_does_not_attempt_partial_graph(self):
        result, kwargs, _ = self.assemble(ready_manifest(), targets=["AAPL", "MSFT"], eligible=False)
        self.select(result, kwargs)
        self.assertEqual("full", result.graph_input["mode"])
        kwargs["_store"].build_projection_graph.assert_called_once()

    def test_post_assembly_safety_rejection_still_rebuilds_complete_source(self):
        result, kwargs, _ = self.assemble(ready_manifest(), eligible=False)
        selected = self.select(result, kwargs)
        self.assertEqual(2, kwargs["_store"].build_projection_graph.call_count)
        self.assertEqual("full", selected.material_fingerprint)
        self.assertTrue(result.graph_input["fallback"])
        self.assertEqual(1, kwargs["runtime_stages"]["targetScopedInputFallback"])
