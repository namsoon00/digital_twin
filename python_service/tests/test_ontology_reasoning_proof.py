import unittest
from unittest.mock import Mock

from digital_twin.application.ontology_reasoning_proof_service import OntologyReasoningProofService
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_reasoning_proof import classify_reasoning_bottleneck
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.infrastructure.typedb_ontology import TypeDBOntologyGraphRepository


def active_abox(snapshot_id="abox-1"):
    return {
        "status": "ok",
        "worldId": "portfolio:local:main",
        "aboxSnapshotId": snapshot_id,
        "worldviewManifestId": snapshot_id,
        "activePointerId": "active-main",
        "materialFingerprint": "facts-1",
        "scopedAboxManifestVersion": "scoped-abox-manifest-v1",
        "scopeTopologyVersion": "scope-topology-v1",
        "scopePlan": [{"scopeId": "subject:005930"}],
        "scopeGenerationIds": {"subject:005930": "generation-1"},
        "nativeRulePlannerTopology": {},
        "nativeRuleEvidenceReadIndex": {},
    }


class ReadOnlyTypeDBReasoningProfileTests(unittest.TestCase):
    def repository(self):
        repository = TypeDBOntologyGraphRepository(
            address="127.0.0.1:1729",
            retry_count=0,
            persistent_driver_enabled=False,
        )
        rule = default_graph_inference_rules()[0]
        repository.rulebox_snapshot = Mock(return_value={
            "status": "ok",
            "rules": [rule.to_dict()],
        })
        repository.active_abox_metadata = Mock(return_value=active_abox())
        repository.match_typedb_native_rules = Mock(return_value={
            "status": "ok",
            "executedRuleCount": 1,
            "executedRuleWorkCount": 1,
            "skippedRuleCount": 0,
            "matchedCount": 0,
            "readTransactionCount": 1,
            "readQueryCount": 1,
            "parallelRuleExecution": False,
            "nativeRuleParallelism": 1,
            "matches": [],
            "executedRules": [{
                "ruleId": rule.rule_id,
                "queryMode": "typedb-scoped-typeql",
                "elapsedMs": 1200,
                "queryDurationMs": 1100,
                "queryCount": 1,
                "rowCount": 0,
                "candidateSymbols": ["005930"],
            }],
            "skippedRules": [],
        })
        repository.load_graph_for_native_matches = Mock(
            return_value=PortfolioOntology("main")
        )
        return repository

    def test_profile_repeats_same_generation_without_calling_any_write_method(self):
        repository = self.repository()
        forbidden = [
            "save_graph",
            "save_rulebox",
            "write_inferencebox_graph",
            "clear_inferencebox",
        ]
        guards = {}
        for method_name in forbidden:
            guards[method_name] = Mock(side_effect=AssertionError(method_name + " must not be called"))
            setattr(repository, method_name, guards[method_name])

        result = repository.profile_native_rule_reads({
            "worldId": "portfolio:local:main",
            "symbols": ["005930"],
            "repeats": 2,
        })

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["readOnly"])
        self.assertFalse(result["mutatedOperationalState"])
        self.assertEqual([], result["writeMethodsInvoked"])
        self.assertEqual(2, result["validSampleCount"])
        self.assertTrue(all(item["generationUnchanged"] for item in result["samples"]))
        self.assertEqual(2, repository.match_typedb_native_rules.call_count)
        self.assertEqual(2, repository.load_graph_for_native_matches.call_count)
        for guard in guards.values():
            guard.assert_not_called()

    def test_profile_invalidates_a_sample_when_active_abox_changes(self):
        repository = self.repository()
        repository.active_abox_metadata = Mock(side_effect=[
            active_abox("abox-1"),
            active_abox("abox-2"),
        ])

        result = repository.profile_native_rule_reads({
            "worldId": "portfolio:local:main",
            "symbols": ["005930"],
            "repeats": 1,
        })

        self.assertEqual("inconclusive", result["status"])
        self.assertEqual(0, result["validSampleCount"])
        self.assertFalse(result["samples"][0]["generationUnchanged"])

    def test_profile_invalidates_samples_when_rulebox_changes(self):
        repository = self.repository()
        initial_rule = default_graph_inference_rules()[0]
        changed_rule = initial_rule.to_dict()
        changed_rule["description"] = "changed during profile"
        repository.rulebox_snapshot = Mock(side_effect=[
            {"status": "ok", "rules": [initial_rule.to_dict()]},
            {"status": "ok", "rules": [changed_rule]},
        ])

        result = repository.profile_native_rule_reads({
            "worldId": "portfolio:local:main",
            "symbols": ["005930"],
            "repeats": 1,
        })

        self.assertEqual("inconclusive", result["status"])
        self.assertFalse(result["rulebox"]["unchanged"])
        self.assertFalse(result["samples"][0]["ruleboxUnchanged"])
        self.assertFalse(result["samples"][0]["validForComparison"])

    def test_profile_always_uses_direct_typeql_without_compiler_api(self):
        repository = self.repository()

        result = repository.profile_native_rule_reads({
            "worldId": "portfolio:local:main",
            "symbols": ["005930"],
            "repeats": 1,
            "nativeQueryMode": "auto",
        })

        self.assertEqual("direct-typeql", result["nativeQueryMode"])
        self.assertNotIn(
            "use_schema_functions",
            repository.match_typedb_native_rules.call_args.kwargs,
        )
        self.assertFalse(hasattr(repository, "sync_typedb_native_rule_functions"))
        self.assertFalse(hasattr(repository, "prewarm_typedb_native_rule_functions"))

    def test_removed_schema_mode_request_still_uses_direct_typeql(self):
        repository = self.repository()

        result = repository.profile_native_rule_reads({
            "worldId": "portfolio:local:main",
            "symbols": ["005930"],
            "repeats": 1,
            "nativeQueryMode": "direct-typeql",
        })

        self.assertEqual("direct-typeql", result["nativeQueryMode"])
        self.assertNotIn(
            "use_schema_functions",
            repository.match_typedb_native_rules.call_args.kwargs,
        )

    def test_profile_reports_query_failure_without_crashing_or_writing(self):
        repository = self.repository()
        repository.match_typedb_native_rules = Mock(side_effect=RuntimeError("read failed"))
        repository.write_inferencebox_graph = Mock(
            side_effect=AssertionError("profile must not write an InferenceBox")
        )

        result = repository.profile_native_rule_reads({
            "worldId": "portfolio:local:main",
            "symbols": ["005930"],
            "repeats": 1,
        })

        self.assertEqual("inconclusive", result["status"])
        self.assertEqual("query-error", result["samples"][0]["status"])
        self.assertTrue(result["samples"][0]["generationUnchanged"])
        self.assertFalse(result["samples"][0]["validForComparison"])
        repository.write_inferencebox_graph.assert_not_called()

    def test_subject_fanout_comparison_is_read_only_and_fails_closed_without_gain(self):
        repository = self.repository()
        repository.write_inferencebox_graph = Mock(
            side_effect=AssertionError("comparison must not write an InferenceBox")
        )

        result = repository.profile_native_rule_reads({
            "worldId": "portfolio:local:main",
            "symbols": ["005930", "035420"],
            "repeats": 1,
            "compareSubjectFanout": True,
            "subjectParallelism": 2,
        })

        self.assertEqual(3, repository.match_typedb_native_rules.call_count)
        self.assertEqual("rejected", result["subjectFanoutGate"]["status"])
        self.assertIn(
            "minimum-performance-gain-not-met",
            result["subjectFanoutGate"]["reasonCodes"],
        )
        self.assertFalse(result["subjectFanoutGate"]["acceptedForRuntime"])
        repository.write_inferencebox_graph.assert_not_called()


class ProjectionRunStore:
    def latest(self, account_id="", world_id="", limit=10):
        rows = []
        for index in range(2):
            rows.append({
                "runId": "run-" + str(index + 1),
                "status": "ok",
                "completedAt": "2026-08-09T00:0" + str(index) + ":00Z",
                "sourceSymbols": ["005930"],
                "result": {
                    "runtimeObservation": {
                        "runId": "run-" + str(index + 1),
                        "durationMs": 200000,
                        "observedAt": "2026-08-09T00:0" + str(index) + ":00Z",
                        "stages": {
                            "totalMs": 200000,
                            "nativeInferenceMs": 150000,
                            "aboxPersistenceMs": 20000,
                        },
                        "inference": {"targetSymbols": ["005930"]},
                    },
                    "ruleboxExecution": {
                        "nativeStageTimings": {
                            "nativeRuleQueriesMs": 100000,
                            "matchedGraphReadMs": 20000,
                            "inferenceGraphBuildMs": 5000,
                            "inferenceBoxWriteMs": 20000,
                        },
                    },
                },
            })
        return rows[:limit]

    def execution_trace(self, run_id="", limit=1):
        return {
            "status": "ok",
            "runs": [{
                "runId": run_id,
                "rules": [{
                    "ruleId": "graph.slow.rule.v1",
                    "status": "executed",
                    "queryCount": 1,
                    "queryDurationMs": 90000,
                    "durationMs": 91000,
                }],
            }],
        }


class ReadOnlyRepository:
    def __init__(self):
        self.last_payload = {}

    def profile_native_rule_reads(self, payload):
        self.last_payload = dict(payload or {})
        samples = []
        for index in range(2):
            samples.append({
                "sample": index + 1,
                "status": "ok",
                "validForComparison": True,
                "generationFingerprint": "same-generation",
                "wallClockMs": 65000,
                "stageTimings": {
                    "nativeRuleQueriesMs": 60000,
                    "matchedGraphReadMs": 4000,
                    "inferenceGraphBuildMs": 1000,
                },
                "rules": [{
                    "ruleId": "graph.slow.rule.v1",
                    "queryDurationMs": 55000,
                }],
            })
        return {
            "status": "ok",
            "readOnly": True,
            "mutatedOperationalState": False,
            "writeMethodsInvoked": [],
            "excludedOperations": ["inferencebox-write"],
            "rulebox": {"unchanged": True},
            "samples": samples,
        }


class OntologyReasoningProofServiceTests(unittest.TestCase):
    def test_service_confirms_query_bottleneck_only_after_same_generation_replay(self):
        repository = ReadOnlyRepository()
        service = OntologyReasoningProofService(
            ontology_repository=repository,
            projection_run_store=ProjectionRunStore(),
            settings={},
        )

        report = service.prove(
            account_id="main",
            world_id="portfolio:local:main",
            repeats=2,
        )

        self.assertEqual("ok", report["status"])
        self.assertEqual("confirmed", report["verdict"]["status"])
        self.assertEqual("native-read-path-dominant", report["verdict"]["cause"])
        self.assertEqual("native-rule-query", report["verdict"]["productionDominantReadSubstage"])
        self.assertTrue(report["verdict"]["independentlyReproduced"])
        self.assertEqual(
            ["graph.slow.rule.v1"],
            report["verdict"]["slowRuleOverlap"],
        )
        self.assertTrue(report["readOnly"])
        self.assertFalse(report["mutatedOperationalState"])
        self.assertEqual(
            ["graph.slow.rule.v1"],
            repository.last_payload["ruleIds"],
        )

    def test_service_does_not_replace_missing_production_trace_with_fake_rule(self):
        repository = ReadOnlyRepository()
        projection_store = ProjectionRunStore()
        projection_store.execution_trace = lambda run_id="", limit=1: {
            "status": "ok",
            "runs": [{"runId": run_id, "rules": []}],
        }
        service = OntologyReasoningProofService(
            ontology_repository=repository,
            projection_run_store=projection_store,
            settings={},
        )

        report = service.prove(account_id="main", world_id="portfolio:local:main")

        self.assertEqual("inconclusive", report["status"])
        self.assertEqual("unavailable", report["readOnlyReplay"]["profileStatus"])
        self.assertEqual({}, repository.last_payload)

    def test_write_bottleneck_is_supported_but_not_replayed(self):
        verdict = classify_reasoning_bottleneck(
            {
                "sampleCount": 3,
                "medians": {
                    "totalMs": 200000,
                    "nativeInferenceMs": 150000,
                    "aboxPersistenceMs": 10000,
                    "nativeStageTimings": {
                        "nativeRuleQueriesMs": 20000,
                        "inferenceBoxWriteMs": 100000,
                    },
                },
            },
            {
                "validSampleCount": 2,
                "allSamplesUsedSameGeneration": True,
                "medianStageTimings": {"nativeRuleQueriesMs": 18000},
            },
        )

        self.assertEqual("supported", verdict["status"])
        self.assertEqual("inferencebox-write-dominant", verdict["cause"])
        self.assertFalse(verdict["independentlyReproduced"])

    def test_top_level_native_history_and_stable_replay_can_isolate_inner_stage(self):
        verdict = classify_reasoning_bottleneck(
            {
                "sampleCount": 1,
                "medians": {
                    "totalMs": 190000,
                    "nativeInferenceMs": 150000,
                    "aboxPersistenceMs": 25000,
                    "nativeStageTimings": {},
                },
            },
            {
                "validSampleCount": 2,
                "allSamplesUsedSameGeneration": True,
                "medianWallClockMs": 150000,
                "medianStageTimings": {
                    "nativeRuleQueriesMs": 135000,
                    "matchedGraphReadMs": 14000,
                },
            },
        )

        self.assertEqual("confirmed", verdict["status"])
        self.assertEqual("native-read-path-dominant", verdict["cause"])
        self.assertEqual(99.3, verdict["replayDominantStageSharePct"])

    def test_combined_read_path_is_compared_as_one_non_overlapping_boundary(self):
        verdict = classify_reasoning_bottleneck(
            {
                "sampleCount": 2,
                "medians": {
                    "totalMs": 260000,
                    "nativeInferenceMs": 150000,
                    "aboxPersistenceMs": 47000,
                    "nativeStageTimings": {
                        "nativeRuleQueriesMs": 57000,
                        "matchedGraphReadMs": 32000,
                        "inferenceBoxWriteMs": 46000,
                        "inferenceGraphBuildMs": 3000,
                    },
                },
            },
            {
                "validSampleCount": 2,
                "allSamplesUsedSameGeneration": True,
                "medianWallClockMs": 92000,
                "medianStageTimings": {
                    "nativeRuleQueriesMs": 83500,
                    "matchedGraphReadMs": 8000,
                    "inferenceGraphBuildMs": 20,
                },
            },
            ["graph.slow.rule.v1"],
        )

        self.assertEqual("confirmed", verdict["status"])
        self.assertEqual("native-read-path-dominant", verdict["cause"])
        self.assertEqual(59.3, verdict["productionDominantNativeStageSharePct"])
        self.assertEqual("native-rule-query", verdict["productionDominantReadSubstage"])
        self.assertTrue(verdict["independentlyReproduced"])


if __name__ == "__main__":
    unittest.main()
