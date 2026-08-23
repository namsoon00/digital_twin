import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from digital_twin.application.reasoning_shadow_service import (
    ReasoningEngineShadowRunner,
    ReasoningShadowScheduler,
)
from digital_twin.domain.portfolio import AlertEvent
from digital_twin.domain.ontology_scopes import target_scope_manifest_fingerprint
from digital_twin.domain.ontology_change_impact import macro_scope_id, symbol_scope_id
from digital_twin.domain.reasoning_engine_versions import (
    EngineControlState,
    reasoning_release_identity,
)
from digital_twin.domain.reasoning_shadow import (
    compare_engine_outcomes,
    engine_outcome_packet,
    frozen_projection_runtime_context,
    pack_projection_runtime_contexts,
    reasoning_comparison_summary,
    unpack_projection_runtime_contexts,
)


def projection(fingerprint="facts-1", status="ok"):
    return {
        "saved": status == "ok",
        "status": status,
        "materialFingerprint": fingerprint,
        "runtimeStages": {"totalMs": 10},
        "ruleboxExecution": {
            "sourceRulesHash": "rulebox-release-1",
            "typedbNativeRuleMatchedCount": 1,
            "typedbNativeRuleMatchedRuleIds": ["graph.holding.v1"],
            "typedbNativeStageTimings": {"nativeRuleQueriesMs": 4},
        },
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
        return {
            "deploymentId": deployment_id,
            "engineFamily": "ontology-investment-brain",
            "engineVersion": str(deployment_id),
            "status": self.status,
            "graphStoreBinding": "typedb-v9",
            "timeSeriesBackendId": "questdb-shadow" if deployment_id == "v2" else "mysql-primary",
            "releaseBundle": {
                "release_id": str(deployment_id) + "-release",
                "tbox_release_id": "tbox-v1",
                "rulebox_release_id": "rulebox-v1",
                "prompt_release_id": "prompt-v1",
                "feature_set_version": "features-v1",
                "runtime_revision": "test-revision",
            },
            "health": self.health,
        }

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

    def claim(self, candidate_id, worker_id, lease_seconds=900, **kwargs):
        del candidate_id, worker_id, lease_seconds, kwargs
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
    def __init__(self, delivery_attempts=0, release_identity=None):
        self.last_detected_alert_events = [event()]
        self.last_ontology_projection_results = {"main": projection()}
        self.shadow_delivery_count = 0
        self.shadow_notification_sink = SimpleNamespace(attempt_count=delivery_attempts)
        self.shadow_release_identity = dict(release_identity or {})
        self.shadow_release_fingerprint = str(
            self.shadow_release_identity.get("releaseFingerprint") or ""
        )

    def run_once(self, **kwargs):
        self.kwargs = kwargs
        return [event()]


class ReasoningShadowTests(unittest.TestCase):
    @staticmethod
    def release(registry, deployment_id):
        return reasoning_release_identity(
            registry.get(deployment_id),
            "rulebox-release-1",
        )
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
        self.assertEqual(1, result["candidateNativeMatchedRuleCount"])
        self.assertEqual(["graph.holding.v1"], result["candidateMatchedRuleIds"])
        self.assertEqual(["KR-EQUITY"], result["marketClasses"])

    def test_summary_requires_substantive_native_and_decision_coverage(self):
        rows = [{
            "status": "equivalent",
            "factParityPct": 100,
            "ruleSlotCoveragePct": 100,
            "createdAt": "2026-08-15T00:00:00Z",
            "payload": {
                "symbols": ["005930", "NVDA"],
                "subjectCount": 1,
                "candidateNativeMatchedRuleCount": 2,
                "candidateMatchedRuleIds": ["graph.a", "graph.b"],
                "candidateActions": ["HOLD"],
                "candidateDurationMs": 120,
                "baselineDurationMs": 100,
                "queueWaitMs": 30,
                "candidatePhaseDurationsMs": {"typedb.preflightReadMs": 2},
            },
        }]

        summary = reasoning_comparison_summary(rows, "v2", "release-fp", "cohort-1")

        self.assertEqual(1, summary["nonEmptyDecisionSampleCount"])
        self.assertEqual(1, summary["nonEmptyNativeInferenceSampleCount"])
        self.assertEqual(2, summary["distinctMatchedRuleCount"])
        self.assertEqual(2, summary["marketClassCount"])
        self.assertEqual(2, summary["candidatePhaseP95Ms"]["typedb.preflightReadMs"])
        self.assertEqual(150, summary["candidateEndToEndP95Ms"])

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
        self.assertTrue(queue.saved["payload"]["candidateReleaseFingerprint"])
        self.assertTrue(queue.saved["payload"]["validationCohortId"])
        self.assertEqual(
            queue.saved["payload"]["candidateReleaseFingerprint"],
            registry.health["candidateReleaseFingerprint"],
        )
        self.assertEqual(
            "rulebox-release-1",
            registry.health["ruleboxFingerprint"],
        )

    def test_shadow_worker_records_comparison_and_promotes_to_shadow_only(self):
        queue = FakeQueue()
        baseline = engine_outcome_packet("v1", [event()], {"main": projection()}, 100)
        registry = FakeRegistry()
        baseline_release = self.release(registry, "v1")
        candidate_release = self.release(registry, "v2")
        queued_at = datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)
        queue.job = {
            "jobId": "job-1",
            "sourceEventId": "event-1",
            "baselineDeploymentId": "v1",
            "payload": {
                "baselineDeploymentId": "v1",
                "candidateDeploymentId": "v2",
                "baselineReleaseIdentity": baseline_release,
                "candidateReleaseIdentity": candidate_release,
                "baselineReleaseId": baseline_release["releaseId"],
                "candidateReleaseId": candidate_release["releaseId"],
                "candidateRuntimeRevision": candidate_release["runtimeRevision"],
                "validationCohortId": candidate_release["validationCohortId"],
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
                "queuedAt": queued_at.isoformat(),
            },
        }
        comparisons = FakeComparisonStore()

        class ClaimClock(datetime):
            candidate_finished = False

            @classmethod
            def now(cls, tz=None):
                value = queued_at + timedelta(
                    seconds=20 if cls.candidate_finished else 2
                )
                return value if tz is not None else value.replace(tzinfo=None)

        class MarkingCandidateRunner(FakeCandidateRunner):
            def run_once(self, **kwargs):
                result = super().run_once(**kwargs)
                ClaimClock.candidate_finished = True
                return result

        worker = ReasoningEngineShadowRunner(
            queue,
            comparisons,
            registry,
            candidate_runner_factory=lambda payload: MarkingCandidateRunner(
                release_identity=payload["candidateReleaseIdentity"]
            ),
            temporal_snapshot_service=FakeTemporalService(),
            temporal_definitions=[{"key": "1d"}],
            settings={
                "reasoningEngineShadowEnabled": "1",
                "reasoningEngineShadowYieldToActiveQueue": "0",
            },
        )

        with patch(
            "digital_twin.application.reasoning_shadow_service.datetime",
            ClaimClock,
        ):
            result = worker.run_once()

        self.assertEqual("completed", result["status"])
        self.assertEqual("equivalent", result["comparisonStatus"])
        self.assertEqual(["job-1"], queue.completed)
        self.assertEqual("shadow", registry.status)
        self.assertEqual(0, comparisons.rows[0]["shadowDeliveryCount"])
        self.assertEqual(2000, comparisons.rows[0]["queueWaitMs"])
        self.assertEqual(
            comparisons.rows[0]["candidateDurationMs"],
            comparisons.rows[0]["candidateExecutionMs"],
        )

    def test_shadow_worker_yields_when_active_reasoning_is_running_without_pending_rows(self):
        worker = ReasoningEngineShadowRunner(
            FakeQueue(),
            FakeComparisonStore(),
            FakeRegistry(),
            candidate_runner_factory=lambda payload: FakeCandidateRunner(),
            temporal_snapshot_service=FakeTemporalService(),
            temporal_definitions=[],
            settings={
                "reasoningEngineShadowEnabled": "1",
                "reasoningEngineShadowYieldToActiveQueue": "1",
                "reasoningEngineShadowActiveQueueMaxPending": "0",
            },
            active_queue_probe=lambda: {
                "status": "healthy",
                "effectivePendingCount": 0,
                "runningEntryCount": 1,
                "retryingEntryCount": 0,
            },
        )

        result = worker.run_once()

        self.assertEqual("deferred-active-queue", result["status"])
        self.assertEqual(
            "active-reasoning-running",
            result["activeQueueGuard"]["reasonCode"],
        )

    def test_shadow_delivery_attempt_is_recorded_as_a_promotion_violation(self):
        queue = FakeQueue()
        registry = FakeRegistry()
        baseline_release = self.release(registry, "v1")
        candidate_release = self.release(registry, "v2")
        queue.job = {
            "jobId": "job-2",
            "sourceEventId": "event-2",
            "baselineDeploymentId": "v1",
            "payload": {
                "baselineDeploymentId": "v1",
                "candidateDeploymentId": "v2",
                "baselineReleaseIdentity": baseline_release,
                "candidateReleaseIdentity": candidate_release,
                "baselineReleaseId": baseline_release["releaseId"],
                "candidateReleaseId": candidate_release["releaseId"],
                "candidateRuntimeRevision": candidate_release["runtimeRevision"],
                "validationCohortId": candidate_release["validationCohortId"],
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
        worker = ReasoningEngineShadowRunner(
            queue,
            comparisons,
            registry,
            candidate_runner_factory=lambda payload: FakeCandidateRunner(
                delivery_attempts=1,
                release_identity=payload["candidateReleaseIdentity"],
            ),
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
