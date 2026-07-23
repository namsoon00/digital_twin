import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

from digital_twin.domain.ontology_runtime_operations import (
    build_projection_runtime_observation,
    summarize_projection_runtime_observations,
)
from digital_twin.application.ontology_reasoning_service import OntologyReasoningRunner
from digital_twin.infrastructure.mysql_ontology_projection_runs import MySQLOntologyProjectionRunStore


class OntologyRuntimeOperationsTests(unittest.TestCase):
    def test_projection_gate_accepts_only_verified_current_generation_no_match(self):
        service = OntologyReasoningRunner.__new__(OntologyReasoningRunner)
        verified = SimpleNamespace(last_ontology_projection_results={
            "main": {
                "status": "ok",
                "ruleboxExecution": {"status": "empty"},
                "inferenceBox": {
                    "status": "empty",
                    "nativeTypeDbReasoningCompleted": True,
                    "nativeInferenceOutcome": "no-match",
                    "generationAligned": True,
                    "sourceAboxSnapshotId": "abox-manifest:current",
                },
            },
        })
        unverified = SimpleNamespace(last_ontology_projection_results={
            "main": {
                "status": "ok",
                "ruleboxExecution": {"status": "empty"},
                "inferenceBox": {
                    "status": "empty",
                    "nativeTypeDbReasoningCompleted": False,
                    "generationAligned": True,
                    "sourceAboxSnapshotId": "abox-manifest:current",
                },
            },
        })

        self.assertTrue(service.projection_gate(verified)["ready"])
        blocked = service.projection_gate(unverified)
        self.assertFalse(blocked["ready"])
        self.assertEqual("empty-unverified", blocked["results"][0]["status"])

    def test_verified_recovery_clears_only_the_projection_circuit_latch(self):
        class Cursor:
            def __init__(self):
                self.payload = {
                    "projectionCircuit": {
                        "status": "open",
                        "consecutiveFailures": 3,
                        "lastFailureReason": "stale generation",
                    },
                    "lastSuccessfulProjectionAt": "2026-07-23T00:00:00Z",
                }

            def load(self):
                return dict(self.payload)

            def save(self, payload):
                self.payload = dict(payload)

        cursor = Cursor()
        runner = OntologyReasoningRunner(
            event_reader=None,
            cursor_store=cursor,
            monitor_runner_factory=lambda: None,
            settings={"ontologyProjectionCircuitFailureThreshold": "3"},
            now_provider=lambda: datetime(2026, 7, 23, tzinfo=timezone.utc),
            projection_recovery_probe=lambda account_ids, symbols: {
                "ready": True,
                "accounts": [{"accountId": "main", "ready": True}],
            },
        )

        recovery = runner.recover_open_projection_circuit([], ["000660"])
        runner.clear_projection_circuit_after_verified_recovery(recovery)

        circuit = cursor.payload["projectionCircuit"]
        self.assertTrue(recovery["ready"])
        self.assertEqual("closed", circuit["status"])
        self.assertEqual(0, circuit["consecutiveFailures"])
        self.assertEqual(3, circuit["recoveredFailureCount"])
        self.assertEqual("2026-07-23T00:00:00Z", cursor.payload["lastSuccessfulProjectionAt"])

    def sample_run(self):
        return SimpleNamespace(
            run_id="ontology-projection:test",
            account_id="main",
            graph_store="typedb",
            abox_snapshot_id="abox-manifest:test",
            entity_count=120,
            relation_count=180,
            started_at="2026-07-22T00:00:00Z",
            completed_at="2026-07-22T00:00:08Z",
        )

    def sample_result(self):
        return {
            "status": "ok",
            "saved": True,
            "materialChangeDetected": True,
            "graphStore": "typedb",
            "aboxSnapshotId": "abox-manifest:test",
            "entityCount": 120,
            "relationCount": 180,
            "projectionScope": {
                "scopeCount": 12,
                "targetSymbols": ["005930"],
            },
            "inferenceImpactPlan": {
                "globalImpact": False,
                "inferenceTargetSymbols": ["005930"],
                "candidateRuleCount": 3,
                "changedScopeFamilies": ["flow"],
                "scopeDelta": {
                    "previousScopeCount": 12,
                    "nextScopeCount": 12,
                    "changedScopeIds": ["symbol:005930:flow"],
                    "affectedScopeIds": ["symbol:005930:flow", "symbol:005930:state"],
                    "dependencyAffectedScopeIds": ["symbol:005930:state"],
                },
            },
            "ruleboxExecution": {"status": "ok", "matchedRuleCount": 2},
            "inferenceBox": {
                "status": "ok",
                "inferenceGenerationId": "inference:test",
                "generationAligned": True,
                "nativeTypeDbReasoningUsed": True,
                "traceCount": 2,
                "relationCount": 4,
                "entityCount": 3,
            },
            "aboxActivationFinalization": {
                "status": "ok",
                "cleanup": {
                    "status": "ok",
                    "removedManifestIds": ["abox-manifest:old"],
                    "remainingInactiveManifestCount": 0,
                    "deletedBatchCount": 2,
                },
            },
        }

    def test_projection_observation_keeps_scope_and_native_inference_cost_together(self):
        observation = build_projection_runtime_observation(
            self.sample_run(),
            self.sample_result(),
            {"ontologyRuntimeProjectionSloSeconds": "5"},
        )

        self.assertEqual("ontology-runtime-observation-v1", observation["version"])
        self.assertEqual(8000, observation["durationMs"])
        self.assertEqual(1, observation["scope"]["changedScopeCount"])
        self.assertEqual(2, observation["scope"]["affectedScopeCount"])
        self.assertEqual(3, observation["inference"]["candidateRuleCount"])
        self.assertEqual(2, observation["inference"]["matchedRuleCount"])
        self.assertTrue(observation["inference"]["generationAligned"])
        self.assertEqual(1, observation["abox"]["cleanup"]["removedManifestCount"])
        self.assertEqual("warning", observation["slo"]["state"])

    def test_projection_observation_separates_planned_and_actual_native_scope(self):
        result = self.sample_result()
        result["inferenceImpactPlan"]["inferenceTargetSymbols"] = ["005930", "000660"]
        result["inferenceBox"]["targetSymbols"] = ["005930"]
        result["ruleboxExecution"].update({
            "typedbNativeRuleExecutedCount": 4,
            "nativeRuleSelectionApplied": True,
            "nativeRuleSelectionDeferredCount": 12,
        })
        result["runtimeStages"] = {"nativeInferenceMs": 6200, "totalMs": 8000}

        observation = build_projection_runtime_observation(self.sample_run(), result)

        self.assertEqual(2, observation["inference"]["plannedTargetSymbolCount"])
        self.assertEqual(1, observation["inference"]["targetSymbolCount"])
        self.assertEqual(4, observation["inference"]["executedRuleCount"])
        self.assertTrue(observation["inference"]["nativeRuleSelectionApplied"])
        self.assertEqual(6200, observation["stages"]["nativeInferenceMs"])

    def test_slo_summary_requires_sustained_breach_before_escalation(self):
        warning = build_projection_runtime_observation(
            self.sample_run(),
            self.sample_result(),
            {"ontologyRuntimeProjectionSloSeconds": "5"},
        )
        ok_result = self.sample_result()
        ok_result["status"] = "unchanged-material-facts"
        ok_run = self.sample_run()
        ok_run.completed_at = "2026-07-22T00:00:03Z"
        ok = build_projection_runtime_observation(ok_run, ok_result, {"ontologyRuntimeProjectionSloSeconds": "5"})

        summary = summarize_projection_runtime_observations(
            [ok, warning, warning, warning],
            {"ontologyRuntimeSloConsecutiveBreachCount": "3"},
        )
        self.assertEqual("ok", summary["status"])
        self.assertFalse(summary["sustainedBreach"])

        sustained = summarize_projection_runtime_observations(
            [warning, warning, warning],
            {"ontologyRuntimeSloConsecutiveBreachCount": "3"},
        )
        self.assertEqual("warning", sustained["status"])
        self.assertTrue(sustained["sustainedBreach"])

    def test_mysql_projection_store_reads_embedded_runtime_samples_without_new_table(self):
        observation = build_projection_runtime_observation(self.sample_run(), self.sample_result())
        store = MySQLOntologyProjectionRunStore.__new__(MySQLOntologyProjectionRunStore)
        store.runtime_settings = {}
        store.latest = lambda account_id="", limit=0: [
            {"result": {"runtimeObservation": observation}},
        ]

        summary = store.runtime_summary("main", limit=999)

        self.assertEqual(1, summary["sampleCount"])
        self.assertEqual("ontology-projection:test", summary["latest"]["runId"])

    def test_intraday_parallel_runtime_samples_remain_bounded_and_independent(self):
        def build(index):
            run = self.sample_run()
            run.run_id = "ontology-projection:intraday-" + str(index)
            run.completed_at = "2026-07-22T00:00:01Z"
            return build_projection_runtime_observation(run, self.sample_result())

        with ThreadPoolExecutor(max_workers=8) as executor:
            observations = list(executor.map(build, range(120)))

        summary = summarize_projection_runtime_observations(observations)
        self.assertEqual(120, summary["sampleCount"])
        self.assertEqual(1000, summary["maximumDurationMs"])
        self.assertEqual("ok", summary["status"])


if __name__ == "__main__":
    unittest.main()
