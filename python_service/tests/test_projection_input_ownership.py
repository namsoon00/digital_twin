import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, fields
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from digital_twin.domain.ontology_contracts import (
    OntologyEntity,
    OntologyRelation,
    PortfolioOntology,
)
from digital_twin.domain.ontology_worlds import world_from_snapshot
from digital_twin.infrastructure import ontology_projection as api
from digital_twin.modules.reasoning.application.projection_input import (
    assembly,
    capture,
    ports,
)
from digital_twin.modules.reasoning.domain.projection_input_policy import (
    ProjectionInputPolicy,
)
from projection_input_fixture import (
    AS_OF,
    RULES,
    TBOX,
    PersistentCache,
    assembly_scenario,
    clear_caches,
    contract_scenarios,
    recorder,
    source_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "digital_twin/modules/reasoning/application/projection_input"


class ProjectionInputOwnershipTests(unittest.TestCase):
    def setUp(self):
        clear_caches(api)
        self.addCleanup(clear_caches, api)

    def test_projection_input_leaf_bodies_match_frozen_source(self):
        contract = json.loads(
            (ROOT / "tests/fixtures/projection_input_ownership_v1.json").read_text()
        )
        for name, entry in contract["members"].items():
            with self.subTest(member=name):
                tree = ast.parse((ROOT / entry["path"]).read_text())
                members = tree.body
                if entry["owner"]:
                    members = next(
                        n
                        for n in tree.body
                        if isinstance(n, ast.ClassDef) and n.name == entry["owner"]
                    ).body
                node = deepcopy(
                    next(
                        n
                        for n in members
                        if isinstance(n, (ast.ClassDef, ast.FunctionDef))
                        and n.name == name
                    )
                )
                restores = entry["restore"]

                class Restore(ast.NodeTransformer):
                    def visit_Call(self, item):
                        replacement = restores.get(ast.unparse(item))
                        if replacement:
                            return ast.parse(replacement, mode="eval").body
                        return self.generic_visit(item)

                    def visit_Attribute(self, item):
                        replacement = restores.get(ast.unparse(item))
                        if replacement:
                            return ast.Name(id=replacement, ctx=ast.Load())
                        return self.generic_visit(item)

                    def visit_Name(self, item):
                        if item.id in restores:
                            item.id = restores[item.id]
                        return item

                node = Restore().visit(node)
                body = node.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    body[0].value.value = inspect.cleandoc(body[0].value.value)
                digest = hashlib.sha256(
                    ast.dump(
                        ast.Module(body=body, type_ignores=[]), include_attributes=False
                    ).encode()
                ).hexdigest()
                self.assertEqual(entry["bodyHash"], digest)

    def test_projection_input_execution_and_progress_match_original(self):
        expected = json.loads(
            (ROOT / "tests/fixtures/projection_input_execution_v1.json").read_text()
        )["scenarios"]
        self.assertEqual(expected, contract_scenarios(api))

    def test_projection_input_packages_import_without_runtime_or_drivers(self):
        script = """
import importlib
import sys
for name in ['assembly', 'capture', 'context', 'decision_memory', 'hypotheses', 'identity', 'temporal']:
    importlib.import_module('digital_twin.modules.reasoning.application.projection_input.' + name)
import digital_twin.modules.reasoning.infrastructure.projection_input_cache
for name in sys.modules:
    assert not name.startswith('digital_twin.infrastructure.'), name
    assert not name.startswith(('typedb', 'pymysql', 'requests')), name
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=dict(os.environ, PYTHONPATH=str(ROOT)),
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_projection_input_capabilities_are_explicit_and_have_no_graph_writer(self):
        owners = {
            "DecisionMemoryInputs": [APP / "decision_memory.py"],
            "HypothesisInputs": [APP / "hypotheses.py"],
            "TemporalInputs": [APP / "temporal.py"],
            "RuntimeContextInputs": [APP / "context.py"],
            "ProjectionIdentityInputs": [APP / "identity.py"],
            "CaptureInputs": [APP / "capture.py"],
            "CacheFlowInputs": [APP / "cache_flow.py"],
            "ModelEvidenceInputs": [APP / "model_evidence.py"],
            "AssemblyInputs": [APP / "assembly.py"],
            "PersistentCacheInputs": [
                ROOT
                / "digital_twin/modules/reasoning/infrastructure/projection_input_cache.py"
            ],
        }
        for name, paths in owners.items():
            used = {
                node.attr
                for path in paths
                for node in ast.walk(ast.parse(path.read_text()))
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "_inputs"
            }
            declared = {field.name for field in fields(getattr(ports, name))}
            self.assertEqual(used, declared, name)
            self.assertTrue(
                declared.isdisjoint(
                    {
                        "repository",
                        "save_graph",
                        "activate",
                        "publish",
                        "enqueue",
                        "send",
                    }
                ),
                name,
            )
        for path in APP.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("infrastructure", node.module or "", str(path))
        method = inspect.getsource(assembly.build_graph_assembly)
        self.assertLess(len(method.splitlines()), 170)

    def test_projection_source_capture_preserves_facts_and_removes_secret_settings(
        self,
    ):
        snapshot = source_snapshot()
        before = asdict(snapshot)
        instance = recorder(api, snapshot)
        context = instance.runtime_context_overrides[snapshot.account_id]
        context["settings"]["telegramBotToken"] = "synthetic-secret-never-persist"
        instance.settings["tossClientSecret"] = "synthetic-secret-never-persist"
        graph, persisted, _ = instance.build_graph_assembly(snapshot, RULES)
        self.assertEqual(before, asdict(snapshot))
        self.assertNotIn(
            "synthetic-secret-never-persist", json.dumps(instance.last_runtime_contexts)
        )
        self.assertEqual(
            AS_OF, instance.last_runtime_contexts[snapshot.account_id]["asOf"]
        )
        self.assertEqual(
            "fixture-tbox-fingerprint", persisted.worldview["activeTBox"]["fingerprint"]
        )
        self.assertEqual([], graph.opinions)
        self.assertEqual([], persisted.beliefs)

    def test_projection_context_reads_are_scoped_point_in_time_and_cached(self):
        snapshot = source_snapshot()
        instance = recorder(api, snapshot, cache=True, overrides=False)
        calls = []
        instance.decision_episode_store = SimpleNamespace(
            list_for_symbols=lambda symbols, **kw: calls.append(
                ("episodes", list(symbols), kw)
            )
            or [],
            outcome_history_for_symbols=lambda symbols, **kw: calls.append(
                ("outcomes", list(symbols), kw)
            )
            or [],
            performance=lambda **kw: calls.append(("performance", [], kw))
            or {"status": "insufficient"},
        )
        observer = Mock(return_value={"status": "observed"})
        instance.outcome_observation_service = SimpleNamespace(
            observe_snapshot=observer
        )
        instance.market_time_series_store = SimpleNamespace(
            load_temporal_windows=lambda account, symbols, definitions, **kw: calls.append(
                ("temporal", sorted(symbols), {"account_id": account, **kw})
            )
            or {}
        )
        instance.hypothesis_proposal_store = SimpleNamespace(
            list_hypothesis_proposals=lambda *args: [
                {
                    "accountId": snapshot.account_id,
                    "symbol": "AAA",
                    "proposalId": "included",
                },
                {
                    "accountId": "different-account",
                    "symbol": "AAA",
                    "proposalId": "excluded",
                },
                {
                    "accountId": snapshot.account_id,
                    "symbol": "BBB",
                    "proposalId": "excluded",
                },
            ]
        )
        result = instance.runtime_context(snapshot, target_symbols=["AAA"])
        first_calls = deepcopy(calls)
        again = instance.runtime_context(snapshot, target_symbols=["AAA"])
        self.assertEqual(result, again)
        self.assertEqual(first_calls, calls)
        observer.assert_called_once_with(snapshot)
        self.assertEqual(
            ["episodes", "outcomes", "performance", "temporal"],
            [row[0] for row in calls],
        )
        for stage, symbols, kw in calls:
            self.assertEqual(AS_OF, kw["as_of"], stage)
            self.assertEqual(snapshot.account_id, kw["account_id"], stage)
            if symbols:
                self.assertEqual(["AAA"], symbols)
        self.assertEqual(
            ["included"], [row["proposalId"] for row in result["hypothesisProposals"]]
        )

    def test_projection_optional_source_failures_do_not_manufacture_memory(self):
        instance = recorder(api, overrides=False)
        failure = Mock(side_effect=RuntimeError("fixture source unavailable"))
        instance.decision_episode_store = SimpleNamespace(
            list_for_symbols=failure, performance=failure
        )
        instance.market_time_series_store = SimpleNamespace(
            load_temporal_windows=failure
        )
        instance.hypothesis_proposal_store = SimpleNamespace(
            list_hypothesis_proposals=failure
        )
        instance.investment_domain_store = SimpleNamespace(
            ontology_portfolio_lifecycle_context=failure
        )
        result = instance.runtime_context(source_snapshot(), target_symbols=["AAA"])
        self.assertEqual([], result["decisionEpisodes"])
        self.assertEqual([], result["hypothesisProposals"])
        self.assertEqual({}, result["temporalObservationWindows"])
        self.assertEqual("unavailable", result["decisionEpisodeProjection"]["status"])
        self.assertEqual({"status": "ok"}, result["dataPipelineHealth"])

    def test_projection_cache_copies_ttl_and_lru_do_not_leak_graph_mutations(self):
        context_cache = api.SharedProjectionRuntimeContextCache()
        graph_cache = api.SharedPortfolioGraphAssemblyCache()
        graph = PortfolioOntology("fixture", worldview={"nested": {"value": "source"}})
        with patch.object(api.time, "monotonic", return_value=100):
            context_cache.put("one", {"nested": {"value": "source"}}, 2)
            graph_cache.put("one", graph, graph, 2, {"fixture": "source"})
            graph.worldview["nested"]["value"] = "outside"
            cached = graph_cache.get("one", 20)
            cached["graph"].worldview["nested"]["value"] = "caller"
            context_cache.get("one", 20)["context"]["nested"]["value"] = "caller"
            self.assertEqual(
                "source",
                graph_cache.get("one", 20)["graph"].worldview["nested"]["value"],
            )
            self.assertEqual(
                "source", context_cache.get("one", 20)["context"]["nested"]["value"]
            )
            context_cache.put("two", {}, 2)
            context_cache.get("one", 20)
            context_cache.put("three", {}, 2)
            self.assertEqual("miss", context_cache.get("two", 20)["status"])
        with patch.object(api.time, "monotonic", return_value=121):
            self.assertEqual("miss", context_cache.get("one", 20)["status"])
            self.assertEqual("miss", graph_cache.get("one", 20)["status"])

    def test_projection_cache_identity_includes_source_release_clock_account_and_scope(
        self,
    ):
        snapshot = source_snapshot()
        instance = recorder(api, snapshot)

        def key(source=snapshot, rules=RULES, tbox=TBOX, context=None, targets=None):
            return instance.graph_assembly_cache_key(
                source, rules, tbox, context or {}, target_symbols=targets
            )

        original = key()
        variations = [
            key(source=source_snapshot(as_of="2026-07-20T00:02:00Z")),
            key(source=source_snapshot(account_id="another-account")),
            key(rules={**RULES, "ruleboxRulesHash": "different-release"}),
            key(tbox={**TBOX, "fingerprint": "different-tbox"}),
            key(context={"asOf": "different-observation"}),
            key(targets=["AAA"]),
        ]
        changed = deepcopy(snapshot)
        changed.positions[0].source_as_of = "2026-07-20T00:00:30Z"
        variations.append(key(source=changed))
        instance.repository.database = "another-world-store"
        variations.append(key())
        for variation in variations:
            self.assertNotEqual(original, variation)

    def test_projection_lineage_failure_never_populates_cache_or_reaches_writer(self):
        persistent = PersistentCache()
        snapshot = source_snapshot()
        instance = recorder(api, snapshot, cache=True, persistent=persistent)
        save = Mock(
            side_effect=AssertionError("input assembly has no writer authority")
        )
        instance.repository.save_graph = save
        original_builder = assembly.build_factual_graph

        def graph_with_calibration(*args):
            graph = original_builder(*args)
            graph.entities.append(
                OntologyEntity(
                    "calibration:fixture", "Fixture", "hypothesis-calibration"
                )
            )
            graph.relations.append(
                OntologyRelation(
                    "stock:AAA", "calibration:fixture", "HAS_HYPOTHESIS_CALIBRATION"
                )
            )
            return graph

        instance.graph_for_graph_store_persistence = (
            lambda graph, rules: PortfolioOntology(graph.portfolio_id)
        )
        with patch.object(
            assembly, "build_factual_graph", side_effect=graph_with_calibration
        ):
            with self.assertRaisesRegex(RuntimeError, "calibration lineage"):
                instance.build_graph_assembly(snapshot, RULES)
        save.assert_not_called()
        self.assertEqual({}, persistent.rows)
        self.assertEqual({}, api.SHARED_PORTFOLIO_GRAPH_ASSEMBLY_CACHE.entries)
        self.assertEqual(["get"], persistent.calls)

    def test_projection_record_input_failure_preserves_previous_published_state(self):
        snapshot = source_snapshot()
        instance = recorder(api, snapshot)
        active = {
            "manifestId": "previous-manifest",
            "generationId": "previous-generation",
        }
        save = Mock(side_effect=lambda graph: active.update(generationId="unexpected"))
        instance.repository.save_graph = save
        instance.ensure_rulebox_ready = lambda: {**RULES, "status": "ready"}
        instance.recover_pending_abox_activation = lambda *args, **kw: {
            "status": "skipped"
        }
        instance.store_projection_result = Mock()
        with patch.object(
            assembly,
            "build_factual_graph",
            side_effect=RuntimeError("fixture assembly interrupted"),
        ):
            result = instance.record_snapshot(snapshot)
        self.assertFalse(result["saved"])
        self.assertEqual("error", result["status"])
        self.assertIn("fixture assembly interrupted", result["reason"])
        self.assertIn("ontology_graph.start", result["failureStage"])
        save.assert_not_called()
        self.assertEqual(
            {"manifestId": "previous-manifest", "generationId": "previous-generation"},
            active,
        )

    def test_projection_world_identity_rejects_incoherent_shared_premises(self):
        snapshot = source_snapshot()
        instance = recorder(api, snapshot)
        instance.world_partitioned_reasoning_enabled = lambda: True
        instance.world_rule_partition = lambda rules: {
            "status": "ready",
            "overlayRules": [],
        }
        instance.catalog_for_rules = lambda *args: RULES
        for proof in [
            {},
            {"ready": True},
            {
                "ready": True,
                "inferenceGenerationId": "one",
                "sourceAboxSnapshotId": "abox:one",
                "generationVector": {
                    "inferenceGenerationId": "two",
                    "sourceAboxSnapshotId": "abox:one",
                },
            },
        ]:
            with self.subTest(proof=proof), self.assertRaisesRegex(
                RuntimeError, "SharedPremiseWorld"
            ):
                instance.build_projection_graph(
                    snapshot,
                    RULES,
                    world_from_snapshot(snapshot, instance.settings),
                    shared_premise_proof=proof,
                )

    def test_projection_model_failure_is_diagnostic_not_an_investment_action(self):
        result = assembly_scenario(api, "scoring-failure")
        self.assertEqual(1, len(result["scorerCalls"]))
        self.assertEqual(AS_OF, result["scorerCalls"][0]["as_of"])
        self.assertEqual(
            "error",
            result["contexts"]["fixture-account"]["statisticalSignalPipeline"][
                "status"
            ],
        )
        self.assertEqual([], result["persistenceGraph"]["opinions"])
        self.assertFalse(
            any(
                row["kind"] == "model-hypothesis-evidence"
                for row in result["persistenceGraph"]["entities"]
            )
        )

    def test_projection_progress_and_oversized_optional_replay_do_not_abort_graph(self):
        snapshot = source_snapshot()
        instance = recorder(api, snapshot)
        with patch.object(
            capture,
            "pack_projection_runtime_contexts",
            side_effect=ValueError("oversized fixture"),
        ):
            graph, persisted, _ = instance.build_graph_assembly(
                snapshot,
                RULES,
                progress_callback=Mock(
                    side_effect=RuntimeError("closed progress client")
                ),
            )
        self.assertTrue(graph.entities)
        self.assertTrue(persisted.entities)

    def test_projection_parallel_accounts_keep_source_and_cache_ownership(self):
        def build(index):
            snapshot = source_snapshot("fixture-" + str(index))
            instance = recorder(api, snapshot, cache=True)
            first = instance.build_graph_assembly(snapshot, RULES)
            first[0].worldview["callerOnly"] = index
            second = instance.build_graph_assembly(snapshot, RULES)
            return snapshot.account_id, second, instance.last_runtime_contexts

        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(build, range(6)))
        for account_id, (graph, persisted, telemetry), contexts in results:
            self.assertEqual(account_id, graph.portfolio_id)
            self.assertEqual(account_id, persisted.portfolio_id)
            self.assertEqual({account_id}, set(contexts))
            self.assertNotIn("callerOnly", graph.worldview)
            self.assertEqual("hit", telemetry["status"])

    def test_projection_input_policy_remains_bounded_and_does_not_change_settings(self):
        settings = {
            "ontologyProjectionGraphCacheTtlSeconds": "invalid",
            "ontologyDecisionEpisodeContextMaxEpisodes": "99999",
        }
        before = deepcopy(settings)
        policy = ProjectionInputPolicy(settings)
        self.assertEqual(45, policy.graph_assembly_cache_ttl_seconds())
        self.assertEqual(60, policy.decision_episode_context_maximum_episodes())
        self.assertFalse(policy.graph_assembly_cache_enabled())
        self.assertFalse(policy.graph_assembly_persistent_cache_enabled())
        self.assertEqual(before, settings)


if __name__ == "__main__":
    unittest.main()
