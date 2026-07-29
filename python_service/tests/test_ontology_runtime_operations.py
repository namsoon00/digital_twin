import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

from digital_twin.domain.ontology_runtime_operations import (
    build_projection_runtime_observation,
    native_replay_validation,
    native_rule_timing_profile,
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

    def test_projection_gate_retries_a_safe_generation_rollback_without_opening_circuit(self):
        service = OntologyReasoningRunner.__new__(OntologyReasoningRunner)
        rolled_back = SimpleNamespace(last_ontology_projection_results={
            "main": {
                "status": "inference-failed-rolled-back",
                "reason": "candidate generation did not align",
                "retryable": True,
                "inferenceAlignment": {"status": "misaligned"},
            },
        })

        result = service.projection_gate(rolled_back)

        self.assertFalse(result["ready"])
        self.assertTrue(result["retryable"])
        self.assertEqual("inference-failed-rolled-back", result["results"][0]["status"])

    def test_projection_gate_treats_waiting_for_a_newer_source_snapshot_as_retryable(self):
        service = OntologyReasoningRunner.__new__(OntologyReasoningRunner)
        waiting = SimpleNamespace(last_ontology_projection_results={
            "main": {
                "status": "deferred-source-snapshot",
                "reason": "The latest monitor snapshot predates the requested fact revision.",
                "recommendedRetryAfterSeconds": 30,
            },
        })

        result = service.projection_gate(waiting)

        self.assertFalse(result["ready"])
        self.assertTrue(result["retryable"])
        self.assertEqual("deferred-source-snapshot", result["results"][0]["status"])

    def test_projection_gate_completes_removed_target_without_requiring_inference(self):
        service = OntologyReasoningRunner.__new__(OntologyReasoningRunner)
        removed_target = SimpleNamespace(last_ontology_projection_results={
            "main": {
                "status": "skipped-inactive-target-symbols",
                "preservedActiveGeneration": True,
                "targetSymbols": ["005930"],
            },
        })

        result = service.projection_gate(removed_target)

        self.assertTrue(result["ready"])
        self.assertEqual([], result["results"])

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
                "targetScopedManifestPatch": {
                    "status": "applied",
                    "mode": "incremental-target-scoped-manifest-patch",
                    "targetSymbols": ["005930"],
                    "selectedIncomingScopeCount": 2,
                    "reusedActiveScopeCount": 10,
                    "deferredScopeCount": 0,
                    "fullReconcileMinutes": 30,
                },
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
        self.assertEqual("applied", observation["scope"]["targetScopedManifestPatch"]["status"])
        self.assertEqual(10, observation["scope"]["targetScopedManifestPatch"]["reusedActiveScopeCount"])
        self.assertEqual("warning", observation["slo"]["state"])

    def test_projection_observation_keeps_runtime_identity_outside_inference(self):
        result = self.sample_result()
        result["runtimeIdentity"] = {
            "contract": "orbit-runtime-identity-v1",
            "version": "release-1",
            "revision": "abc123",
            "source": "test",
            "python": "3.test",
        }

        observation = build_projection_runtime_observation(self.sample_run(), result)

        self.assertEqual("abc123", observation["runtimeIdentity"]["revision"])
        self.assertNotIn("runtimeIdentity", observation["inference"])

    def test_projection_observation_separates_planned_and_actual_native_scope(self):
        result = self.sample_result()
        result["inferenceImpactPlan"]["inferenceTargetSymbols"] = ["005930", "000660"]
        result["inferenceBox"]["targetSymbols"] = ["005930"]
        result["ruleboxExecution"].update({
            "typedbNativeRuleExecutedCount": 4,
            "typedbNativeRuleExecutedWorkCount": 6,
            "typedbNativeRuleTargetParallelism": 2,
            "typedbNativeRuleTargetWorkShardingUsed": True,
            "typedbNativeRuleTargetWorkShardCount": 2,
            "typedbNativeRuleWorkItemCount": 6,
            "typedbNativeRuleCommitMode": "single-inferencebox-generation",
            "nativeRuleSelectionApplied": True,
            "nativeRuleSelectionDeferredCount": 12,
        })
        result["runtimeStages"] = {"nativeInferenceMs": 6200, "totalMs": 8000}

        observation = build_projection_runtime_observation(self.sample_run(), result)

        self.assertEqual(2, observation["inference"]["plannedTargetSymbolCount"])
        self.assertEqual(2, observation["inference"]["requestedTargetSymbolCount"])
        self.assertEqual(1, observation["inference"]["targetSymbolCount"])
        self.assertEqual("partial", observation["inference"]["targetCoverageStatus"])
        self.assertEqual(["000660"], observation["inference"]["notEvaluatedSymbols"])
        self.assertEqual(4, observation["inference"]["executedRuleCount"])
        self.assertEqual(6, observation["inference"]["executedRuleWorkCount"])
        self.assertEqual(2, observation["inference"]["targetParallelism"])
        self.assertTrue(observation["inference"]["targetWorkShardingUsed"])
        self.assertEqual(2, observation["inference"]["targetWorkShardCount"])
        self.assertEqual(6, observation["inference"]["targetWorkItemCount"])
        self.assertEqual("single-inferencebox-generation", observation["inference"]["commitMode"])
        self.assertTrue(observation["inference"]["nativeRuleSelectionApplied"])
        self.assertEqual(6200, observation["stages"]["nativeInferenceMs"])

    def test_dependency_selected_execution_requires_a_verified_prior_native_proof(self):
        result = self.sample_result()
        result["inferenceBox"].update({
            "nativeTypeDbReasoningCompleted": True,
            "targetSymbols": ["005930"],
        })
        result["ruleboxExecution"].update({"nativeRuleSelectionApplied": True})

        missing_proof = native_replay_validation(result)

        self.assertEqual("incomplete-coverage", missing_proof["status"])
        self.assertFalse(missing_proof["verified"])

        result["inferenceReuseProof"] = {
            "status": "verified",
            "coverageComplete": True,
            "selectionApplied": True,
        }
        verified = native_replay_validation(result)

        self.assertEqual("verified-prior-coverage", verified["status"])
        self.assertTrue(verified["verified"])

    def test_runtime_observation_preserves_impact_and_replay_diagnostics(self):
        result = self.sample_result()
        result["inferenceImpactPlan"].update({
            "enabledRuleCount": 9,
            "diagnostics": {
                "classification": "shared-context-impact",
                "reasonCodes": ["shared-macro-context"],
                "globalScopeCount": 2,
                "globalScopeTypes": [{"type": "macro", "label": "거시", "count": 2}],
                "candidateRuleRatioPct": 100,
                "eventScopeAgreement": "aligned",
            },
        })
        result["inferenceBox"].update({
            "nativeTypeDbReasoningCompleted": True,
            "targetSymbols": ["005930"],
        })

        observation = build_projection_runtime_observation(self.sample_run(), result)

        self.assertEqual(9, observation["inference"]["enabledRuleCount"])
        self.assertEqual(100, observation["inference"]["candidateRuleRatioPct"])
        self.assertEqual(
            "shared-context-impact",
            observation["scope"]["impactDiagnostics"]["classification"],
        )
        self.assertEqual("complete-native-evaluation", observation["inference"]["replayValidation"]["status"])

    def test_native_rule_timing_profile_keeps_only_bounded_slowest_rule_details(self):
        profile = native_rule_timing_profile({
            "wallClockMs": 8100,
            "executedRules": [
                {
                    "ruleId": "graph.fast",
                    "nativeRuleId": "typedb.native.graph.fast",
                    "schemaFunctionName": "orbit_fast",
                    "rowCount": 1,
                    "candidateSymbols": ["005930"],
                    "queryComplexity": 2,
                    "queryCount": 1,
                    "elapsedMs": 120,
                    "queryDurationMs": 100,
                },
                {
                    "ruleId": "graph.slow",
                    "nativeRuleId": "typedb.native.graph.slow",
                    "schemaFunctionName": "orbit_slow",
                    "rowCount": 0,
                    "candidateSymbols": ["005930"],
                    "queryComplexity": 6,
                    "queryCount": 2,
                    "elapsedMs": 7900,
                    "queryDurationMs": 7600,
                },
            ],
            "skippedRules": [{
                "ruleId": "graph.blocked",
                "status": "query-timeout",
                "elapsedMs": 300,
                "queryDurationMs": 280,
            }, {
                "ruleId": "graph.not-applicable",
                "status": "not-applicable",
            }],
        })

        self.assertEqual(8100, profile["wallClockMs"])
        self.assertEqual(1, profile["incompleteRuleCount"])
        self.assertEqual(1, profile["notApplicableRuleCount"])
        self.assertEqual(2, profile["executedRuleCount"])
        self.assertEqual(1, profile["incompleteRuleCount"])
        self.assertEqual("graph.slow", profile["slowestRules"][0]["ruleId"])
        self.assertEqual(7600, profile["slowestRules"][0]["queryDurationMs"])

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
