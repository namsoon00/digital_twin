import unittest
from types import SimpleNamespace

from digital_twin.application.reasoning_shadow_service import (
    ReasoningEngineShadowRunner,
    ReasoningShadowScheduler,
)
from digital_twin.domain.portfolio import AlertEvent
from digital_twin.domain.ontology_scopes import target_scope_manifest_fingerprint
from digital_twin.domain.ontology_change_impact import macro_scope_id, symbol_scope_id
from digital_twin.domain.reasoning_engine_versions import EngineControlState
from digital_twin.domain.reasoning_shadow import (
    compare_engine_outcomes,
    engine_outcome_packet,
    frozen_projection_runtime_context,
    pack_projection_runtime_contexts,
    unpack_projection_runtime_contexts,
)


def projection(fingerprint="facts-1", status="ok"):
    return {
        "saved": status == "ok",
        "status": status,
        "materialFingerprint": fingerprint,
        "runtimeStages": {"totalMs": 10},
        "graphInput": {"mode": "target-scoped", "targetSymbols": ["005930"]},
        "comparisonScope": {"fingerprint": "target-facts-1", "scopeCount": 3},
        "inferenceBox": {
            "status": "ok" if status == "ok" else "error",
            "sourceAboxSnapshotId": "abox-1",
            "inferenceGenerationId": "inference-1",
            "nativeTypeDbReasoningCompleted": status == "ok",
            "generationAligned": status == "ok",
            "targetSymbols": ["005930"],
        },
    }


def event(action="HOLD", rule_id="graph.holding.v1"):
    return AlertEvent(
        account_id="main",
        account_label="main",
        severity="observe",
        rule="investmentInsight",
        key="insight:005930",
        title="test",
        lines=[],
        symbol="005930",
        metadata={
            "ontologyRelationContext": {
                "graphStore": "typedb",
                "graphStoreUsed": True,
                "facts": {"currentPrice": 70000},
                "decision": {
                    "action": action,
                    "selectedRuleId": rule_id,
                    "decisionStage": "HOLD_REVIEW",
                    "decisionEffect": action,
                },
                "decisionState": {
                    "reviewLevel": "observe",
                    "dataState": "sufficient",
                    "validationState": "ready",
                },
                "activeRules": [{"ruleId": rule_id}],
                "graphStoreInference": {
                    "relations": [{
                        "type": "HAS_INFERRED_OPINION",
                        "ruleId": rule_id,
                        "decisionStage": "HOLD_REVIEW",
                        "decisionEffect": action,
                    }],
                    "traces": [{"ruleId": rule_id, "evidenceId": "evidence-1"}],
                },
            },
        },
    )


class FakeRegistry:
    def __init__(self, status="provisioning"):
        self.status = status
        self.health = {}

    def control(self):
        return EngineControlState("v1", "v1", "v2", 1)

    def get(self, deployment_id):
        return {"deploymentId": deployment_id, "status": self.status, "health": self.health}

    def transition(self, deployment_id, status):
        self.status = status
        return self.get(deployment_id)

    def update_health(self, deployment_id, health):
        del deployment_id
        self.health = dict(health)


class FakeQueue:
    def __init__(self):
        self.saved = None
        self.job = None
        self.completed = []
        self.retried = []
        self.discarded = []

    def enqueue(self, **kwargs):
        self.saved = kwargs
        return {"saved": True, "jobId": "job-1", "status": "queued"}

    def claim(self, candidate_id, worker_id, lease_seconds=900):
        del candidate_id, worker_id, lease_seconds
        job, self.job = self.job, None
        return dict(job or {})

    def complete(self, job_id):
        self.completed.append(job_id)

    def retry(self, job_id, error, max_attempts=5):
        self.retried.append((job_id, error, max_attempts))
        return {"jobId": job_id, "attemptCount": 1, "terminal": False}

    def discard(self, job_id, reason):
        self.discarded.append((job_id, reason))


class FakeComparisonStore:
    def __init__(self):
        self.rows = []

    def record(self, baseline, candidate, source_event, comparison):
        self.rows.append(dict(comparison))
        return {"comparisonId": "comparison-1", "createdAt": "2026-08-15T00:00:00Z", **comparison}


class FakeTemporalService:
    def compare(self, *args):
        del args
        return {"status": "equivalent", "activeWindowsHash": "same", "candidateWindowsHash": "same"}


class FakeCandidateRunner:
    def __init__(self, delivery_attempts=0):
        self.last_detected_alert_events = [event()]
        self.last_ontology_projection_results = {"main": projection()}
        self.shadow_delivery_count = 0
        self.shadow_notification_sink = SimpleNamespace(attempt_count=delivery_attempts)
        self.shadow_release_fingerprint = "rulebox-release-1"

    def run_once(self, **kwargs):
        self.kwargs = kwargs
        return [event()]


class ReasoningShadowTests(unittest.TestCase):
    def test_target_scope_fingerprint_excludes_unrelated_symbols_and_keeps_dependencies(self):
        target_scope = symbol_scope_id("005930", "price")
        dependency_scope = macro_scope_id("fx")
        unrelated_scope = symbol_scope_id("000660", "price")
        plan = [
            {"scopeId": target_scope, "fingerprint": "price-1", "dependencyScopeIds": [dependency_scope]},
            {"scopeId": dependency_scope, "fingerprint": "fx-1", "dependencyScopeIds": []},
            {"scopeId": unrelated_scope, "fingerprint": "price-2", "dependencyScopeIds": []},
        ]

        first = target_scope_manifest_fingerprint(plan, ["005930"])
        plan[2]["fingerprint"] = "unrelated-change"
        second = target_scope_manifest_fingerprint(plan, ["005930"])
        plan[1]["fingerprint"] = "dependency-change"
        third = target_scope_manifest_fingerprint(plan, ["005930"])

        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertNotEqual(second["fingerprint"], third["fingerprint"])
        self.assertEqual(2, first["scopeCount"])
        self.assertEqual({target_scope, dependency_scope}, set(first["scopeManifest"]))

    def test_frozen_projection_context_keeps_policy_and_excludes_secrets(self):
        frozen = frozen_projection_runtime_context({
            "settings": {
                "notificationCooldownMinutes": "10",
                "ontologyThresholdPolicy": {"risk": 80},
                "mysqlPassword": "secret",
                "alphaVantageApiKey": "secret",
            },
            "decisionEpisodes": [{"id": "episode-1"}],
        })

        self.assertEqual("10", frozen["settings"]["notificationCooldownMinutes"])
        self.assertEqual({"risk": 80}, frozen["settings"]["ontologyThresholdPolicy"])
        self.assertNotIn("mysqlPassword", frozen["settings"])
        self.assertNotIn("alphaVantageApiKey", frozen["settings"])
        self.assertEqual([{"id": "episode-1"}], frozen["decisionEpisodes"])

    def test_frozen_projection_context_keeps_graph_policy_inputs(self):
        frozen = frozen_projection_runtime_context({
            "settings": {
                "investmentStrategyProfile": "aggressive",
                "temporalWindowPeriods": "15m,1h,1d,5d,20d",
                "fxExposureReviewPct": "35",
                "investmentBrainMaximumHypothesisCount": "8",
                "typedbAddress": "127.0.0.1:1729",
            }
        })

        self.assertEqual("aggressive", frozen["settings"]["investmentStrategyProfile"])
        self.assertEqual("15m,1h,1d,5d,20d", frozen["settings"]["temporalWindowPeriods"])
        self.assertEqual("35", frozen["settings"]["fxExposureReviewPct"])
        self.assertEqual("8", frozen["settings"]["investmentBrainMaximumHypothesisCount"])
        self.assertNotIn("typedbAddress", frozen["settings"])

    def test_projection_context_packet_round_trips_and_verifies_hash(self):
        contexts = {
            "main": {
                "settings": {"notificationCooldownMinutes": "10"},
                "temporalObservationWindows": {"005930": [{"value": 1}] * 100},
            },
        }
        packet = pack_projection_runtime_contexts(contexts)

        self.assertLess(packet["compressedBytes"], packet["uncompressedBytes"])
        self.assertEqual(contexts, unpack_projection_runtime_contexts(packet))

        packet["sha256"] = "invalid"
        with self.assertRaisesRegex(ValueError, "hash verification"):
            unpack_projection_runtime_contexts(packet)

    def test_equivalent_graph_outputs_require_fact_and_rule_parity(self):
        baseline = engine_outcome_packet("v1", [event()], {"main": projection()}, 100)
        candidate = engine_outcome_packet("v2", [event()], {"main": projection()}, 120)

        result = compare_engine_outcomes(
            baseline,
            candidate,
            [{"status": "equivalent"}],
        ).to_dict()

        self.assertEqual("equivalent", result["status"])
        self.assertEqual(100.0, result["factParityPct"])
        self.assertEqual(100.0, result["ruleSlotCoveragePct"])
        self.assertEqual(0, result["unexplainedDecisionDifferenceCount"])
        self.assertEqual(["005930"], result["symbols"])

    def test_equal_source_scope_ignores_different_retained_store_history(self):
        baseline_projection = projection("baseline-history")
        candidate_projection = projection("candidate-history")
        baseline_projection["persistedComparisonScope"] = {
            "fingerprint": "baseline-persisted",
            "scopeCount": 57,
        }
        candidate_projection["persistedComparisonScope"] = {
            "fingerprint": "candidate-persisted",
            "scopeCount": 51,
        }
        baseline = engine_outcome_packet(
            "v1", [event()], {"main": baseline_projection}, 100
        )
        candidate = engine_outcome_packet(
            "v2", [event()], {"main": candidate_projection}, 120
        )

        result = compare_engine_outcomes(
            baseline,
            candidate,
            [{"status": "equivalent"}],
        ).to_dict()

        self.assertEqual("equivalent", result["status"])
        self.assertEqual(100.0, result["factParityPct"])

    def test_same_facts_with_different_decision_is_unexplained(self):
        baseline = engine_outcome_packet("v1", [event("HOLD")], {"main": projection()}, 100)
        candidate = engine_outcome_packet("v2", [event("BUY")], {"main": projection()}, 120)

        result = compare_engine_outcomes(
            baseline,
            candidate,
            [{"status": "equivalent"}],
        ).to_dict()

        self.assertEqual("unexplained-difference", result["status"])
        self.assertEqual(1, result["unexplainedDecisionDifferenceCount"])

    def test_scheduler_persists_immutable_input_and_never_delivers(self):
        queue = FakeQueue()
        registry = FakeRegistry()
        scheduler = ReasoningShadowScheduler(queue, registry, {"reasoningEngineShadowEnabled": "1"})
        runner = SimpleNamespace(
            last_reasoning_source_states={
                "main": {"accountId": "main", "generatedAt": "2026-08-15T00:00:00Z"},
            },
            last_detected_alert_events=[event()],
            last_ontology_projection_results={"main": projection()},
            ontology_projection_recorder=SimpleNamespace(
                last_runtime_contexts={
                    "main": {
                        "settings": {"notificationCooldownMinutes": "10", "mysqlPassword": "secret"},
                        "temporalObservationWindows": {"005930": []},
                    },
                },
            ),
        )

        result = scheduler.schedule(
            runner,
            [SimpleNamespace(event_id="event-1")],
            ["NVDA"],
            {"targetSymbols": ["NVDA"]},
            100,
        )

        self.assertTrue(result["saved"])
        self.assertEqual("2026-08-15T00:00:00Z", queue.saved["payload"]["sourceSnapshotIds"]["main"])
        self.assertEqual(["005930", "NVDA"], queue.saved["payload"]["symbols"])
        self.assertEqual(
            {"main": ["005930"]},
            queue.saved["payload"]["projectionTargetSymbolsByAccount"],
        )
        self.assertEqual(2, result["symbolCount"])
        contexts = unpack_projection_runtime_contexts(
            queue.saved["payload"]["projectionRuntimeContextPacket"]
        )
        self.assertNotIn("mysqlPassword", contexts["main"]["settings"])
        self.assertTrue(queue.saved["payload"]["projectionRuntimeContextHashes"]["main"])
        self.assertGreater(result["projectionRuntimeContextBytes"], 0)
        self.assertGreater(result["projectionRuntimeContextCompressedBytes"], 0)
        self.assertEqual(0, queue.saved["payload"]["baselineOutcome"]["deliveryCount"])

    def test_shadow_worker_records_comparison_and_promotes_to_shadow_only(self):
        queue = FakeQueue()
        baseline = engine_outcome_packet("v1", [event()], {"main": projection()}, 100)
        queue.job = {
            "jobId": "job-1",
            "sourceEventId": "event-1",
            "baselineDeploymentId": "v1",
            "payload": {
                "baselineDeploymentId": "v1",
                "candidateDeploymentId": "v2",
                "accountIds": ["main"],
                "symbols": ["005930"],
                "sourceSnapshotIds": {"main": "2026-08-15T00:00:00Z"},
                "sourceStates": {"main": {"generatedAt": "2026-08-15T00:00:00Z"}},
                "projectionTargetSymbolsByAccount": {"main": ["005930"]},
                "projectionRuntimeContextPacket": pack_projection_runtime_contexts({
                    "main": {"settings": {}},
                }),
                "baselineOutcome": baseline,
                "reasoningContext": {},
            },
        }
        registry = FakeRegistry()
        comparisons = FakeComparisonStore()
        worker = ReasoningEngineShadowRunner(
            queue,
            comparisons,
            registry,
            candidate_runner_factory=lambda payload: FakeCandidateRunner(),
            temporal_snapshot_service=FakeTemporalService(),
            temporal_definitions=[{"key": "1d"}],
            settings={
                "reasoningEngineShadowEnabled": "1",
                "reasoningEngineShadowYieldToActiveQueue": "0",
            },
        )

        result = worker.run_once()

        self.assertEqual("completed", result["status"])
        self.assertEqual("equivalent", result["comparisonStatus"])
        self.assertEqual(["job-1"], queue.completed)
        self.assertEqual("shadow", registry.status)
        self.assertEqual(0, comparisons.rows[0]["shadowDeliveryCount"])

    def test_shadow_delivery_attempt_is_recorded_as_a_promotion_violation(self):
        queue = FakeQueue()
        queue.job = {
            "jobId": "job-2",
            "sourceEventId": "event-2",
            "baselineDeploymentId": "v1",
            "payload": {
                "baselineDeploymentId": "v1",
                "candidateDeploymentId": "v2",
                "accountIds": ["main"],
                "symbols": ["005930"],
                "sourceSnapshotIds": {"main": "2026-08-15T00:00:00Z"},
                "sourceStates": {"main": {"generatedAt": "2026-08-15T00:00:00Z"}},
                "projectionTargetSymbolsByAccount": {"main": ["005930"]},
                "projectionRuntimeContextPacket": pack_projection_runtime_contexts({
                    "main": {"settings": {}},
                }),
                "baselineOutcome": engine_outcome_packet(
                    "v1", [event()], {"main": projection()}, 100
                ),
                "reasoningContext": {},
            },
        }
        comparisons = FakeComparisonStore()
        registry = FakeRegistry()
        worker = ReasoningEngineShadowRunner(
            queue,
            comparisons,
            registry,
            candidate_runner_factory=lambda payload: FakeCandidateRunner(delivery_attempts=1),
            temporal_snapshot_service=FakeTemporalService(),
            temporal_definitions=[{"key": "1d"}],
            settings={
                "reasoningEngineShadowEnabled": "1",
                "reasoningEngineShadowYieldToActiveQueue": "0",
            },
        )

        result = worker.run_once()

        self.assertEqual("delivery-violation", result["comparisonStatus"])
        self.assertEqual(1, comparisons.rows[0]["shadowDeliveryCount"])
        self.assertEqual("provisioning", registry.status)
        self.assertEqual("blocked", registry.health["status"])

    def test_legacy_shadow_job_without_immutable_context_is_not_retried(self):
        queue = FakeQueue()
        queue.job = {
            "jobId": "legacy-job",
            "payload": {"baselineOutcome": {}, "sourceStates": {"main": {}}},
        }
        worker = ReasoningEngineShadowRunner(
            queue,
            FakeComparisonStore(),
            FakeRegistry(),
            candidate_runner_factory=lambda payload: FakeCandidateRunner(),
            temporal_snapshot_service=FakeTemporalService(),
            temporal_definitions=[],
            settings={
                "reasoningEngineShadowEnabled": "1",
                "reasoningEngineShadowYieldToActiveQueue": "0",
            },
        )

        result = worker.run_once()

        self.assertEqual("invalid-input", result["status"])
        self.assertEqual("legacy-job", queue.discarded[0][0])
        self.assertEqual([], queue.retried)


if __name__ == "__main__":
    unittest.main()
