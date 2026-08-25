import unittest
from types import SimpleNamespace

from digital_twin.domain.ontology_execution_trace import reasoning_execution_trace_payload
from digital_twin.domain.ontology_reasoning_queue import reasoning_lane_for_priority


class OntologyExecutionTraceTests(unittest.TestCase):
    def test_reasoning_lanes_reuse_existing_dispatch_priority(self):
        self.assertEqual("REALTIME_REASONING", reasoning_lane_for_priority("observation"))
        self.assertEqual("REALTIME_REASONING", reasoning_lane_for_priority("urgent"))
        self.assertEqual("CONTEXT_REASONING", reasoning_lane_for_priority("market"))
        self.assertEqual("CONTEXT_REASONING", reasoning_lane_for_priority("research"))

    def test_rule_outcome_keeps_precise_matched_subjects_in_a_multi_symbol_run(self):
        run = SimpleNamespace(
            run_id="run:subjects",
            world_id="portfolio:local:main",
            account_id="main",
            source_snapshot_at="2026-08-15T00:00:00Z",
            source_symbols=["005930", "000660"],
            started_at="2026-08-15T00:00:00Z",
            completed_at="2026-08-15T00:00:01Z",
            context_payload={},
        )
        result = {
            "status": "ok",
            "ruleboxExecution": {
                "status": "ok",
                "nativeRuleSelectionApplied": False,
                "typedbNativeRuleMatchedRuleIds": ["rule.flow"],
                "nativeMatchResult": {
                    "matches": [{"ruleId": "rule.flow", "sourceId": "stock:005930"}],
                    "executedRules": [{
                        "ruleId": "rule.flow",
                        "candidateSymbols": ["005930", "000660"],
                        "queryCount": 1,
                    }],
                },
            },
            "inferenceBox": {"inferenceGenerationId": "generation:subjects"},
        }

        trace = reasoning_execution_trace_payload(run, result)

        outcome = trace["ruleOutcomes"][0]
        self.assertTrue(outcome["matched"])
        self.assertEqual(["005930"], outcome["matchedTargetSymbols"])
        self.assertEqual(["000660", "005930"], sorted(outcome["targetSymbols"]))

    def test_rule_level_match_does_not_fan_out_across_multiple_symbols(self):
        run = SimpleNamespace(
            run_id="run:unresolved-subject",
            world_id="portfolio:local:main",
            account_id="main",
            source_snapshot_at="2026-08-15T00:00:00Z",
            source_symbols=["005930", "000660"],
            started_at="2026-08-15T00:00:00Z",
            completed_at="2026-08-15T00:00:01Z",
            context_payload={},
        )
        result = {
            "status": "ok",
            "ruleboxExecution": {
                "status": "ok",
                "typedbNativeRuleMatchedRuleIds": ["rule.flow"],
                "nativeMatchResult": {
                    "matches": [{"ruleId": "rule.flow"}],
                    "executedRules": [{
                        "ruleId": "rule.flow",
                        "candidateSymbols": ["005930", "000660"],
                    }],
                },
            },
            "inferenceBox": {"inferenceGenerationId": "generation:unresolved"},
        }

        outcome = reasoning_execution_trace_payload(run, result)["ruleOutcomes"][0]

        self.assertFalse(outcome["matched"])
        self.assertFalse(outcome["matchIdentityComplete"])
        self.assertEqual([], outcome["matchedTargetSymbols"])
        self.assertEqual("matched-target-unresolved", outcome["status"])
        self.assertEqual("target-unresolved", outcome["detail"]["matchIdentityStatus"])

    def test_preflight_not_applicable_rule_never_becomes_matched(self):
        run = SimpleNamespace(
            run_id="run:not-applicable",
            world_id="portfolio:local:main",
            account_id="main",
            source_snapshot_at="2026-08-15T00:00:00Z",
            source_symbols=["005930"],
            started_at="2026-08-15T00:00:00Z",
            completed_at="2026-08-15T00:00:01Z",
            context_payload={},
        )
        result = {
            "status": "ok",
            "ruleboxExecution": {
                "status": "ok",
                "typedbNativeRuleMatchedRuleIds": ["rule.not-applicable"],
                "nativeMatchResult": {
                    "matches": [{"ruleId": "rule.not-applicable"}],
                    "skippedRules": [{
                        "ruleId": "rule.not-applicable",
                        "status": "not-applicable-preflight",
                    }],
                },
            },
            "inferenceBox": {"inferenceGenerationId": "generation:not-applicable"},
        }

        outcome = reasoning_execution_trace_payload(run, result)["ruleOutcomes"][0]

        self.assertFalse(outcome["matched"])
        self.assertEqual([], outcome["matchedTargetSymbols"])
        self.assertEqual("not-applicable-preflight", outcome["status"])

    def test_execution_trace_records_stages_rules_and_disabled_ai(self):
        run = SimpleNamespace(
            run_id="run:1",
            world_id="portfolio:local:main",
            account_id="main",
            source_snapshot_at="2026-08-09T00:00:00Z",
            source_symbols=["035420"],
            started_at="2026-08-09T00:00:00Z",
            completed_at="2026-08-09T00:00:12Z",
            context_payload={
                "reasoningRequest": {
                    "queueDispatch": {"selectedLanes": ["CRITICAL_REASONING"]},
                },
            },
        )
        result = {
            "status": "ok",
            "runtimeStages": {"graphAssemblyMs": 1200, "nativeInferenceMs": 9500},
            "projectionScope": {
                "targetScopedManifestPatch": {
                    "status": "applied",
                    "mode": "incremental-target-scoped-manifest-patch",
                    "selectedIncomingScopeCount": 1,
                    "deferredScopeCount": 1,
                    "factSlotStatus": "applied",
                    "factSlotFamilies": ["market"],
                    "scopeSelectionTrace": {
                        "version": "target-scope-selection-trace-v1",
                        "selected": [{
                            "scopeId": "symbol:035420:market",
                            "scopeFamily": "market",
                            "symbol": "035420",
                            "disposition": "selected",
                            "reasons": ["semantic-value-change", "event-fact-slot"],
                        }],
                        "deferred": [{
                            "scopeId": "symbol:035420:evidence",
                            "scopeFamily": "evidence",
                            "symbol": "035420",
                            "disposition": "deferred",
                            "reasons": ["unrelated-event-fact-slot"],
                        }],
                    },
                },
            },
            "relationPersistence": {
                "version": "scoped-abox-relation-persistence-v2",
                "scopeCount": 1,
                "scopes": [{
                    "scopeId": "symbol:035420:market",
                    "scopeFamily": "market",
                    "symbol": "035420",
                    "requested": {"entityCount": 9, "relationCount": 7},
                    "inserted": {"entityCount": 4, "relationCount": 3},
                    "reused": {"entityCount": 5, "relationCount": 4},
                }],
            },
            "inferenceImpactPlan": {"candidateRuleIds": ["rule.price"]},
            "ruleboxExecution": {
                "status": "ok",
                "typedbNativeStageTimings": {
                    "nativeRuleQueriesMs": 7000,
                    "matchedGraphReadMs": 1500,
                },
                "nativeRuleSelectionApplied": True,
                "nativeRuleSelectionCandidateCount": 1,
                "nativeRuleSelectionExecutedRuleIds": ["rule.price", "rule.prior"],
                "nativeRuleSelectionDeferredRuleIds": ["rule.research"],
                "typedbNativeRuleMatchedRuleIds": ["rule.price"],
                "nativeMatchResult": {
                    "nativeExecutionMode": "typedb-native-direct-typeql",
                    "matches": [{"ruleId": "rule.price"}],
                    "executedRules": [
                        {"ruleId": "rule.price", "elapsedMs": 9000, "queryCount": 1},
                        {"ruleId": "rule.prior", "elapsedMs": 1200, "queryCount": 1},
                    ],
                    "skippedRules": [
                        {
                            "ruleId": "rule.research",
                            "status": "not-applicable",
                            "failureReason": "Active ABox has no required relation type.",
                        },
                        {
                            "ruleId": "rule.failed",
                            "status": "query-error",
                            "failureReason": "TypeDB query failed.",
                        },
                    ],
                },
            },
            "inferenceBox": {
                "status": "ok",
                "inferenceGenerationId": "inference:1",
                "sourceAboxSnapshotId": "abox:1",
                "generationAligned": True,
                "traceCount": 1,
            },
        }

        trace = reasoning_execution_trace_payload(
            run,
            result,
            settings={
                "notificationAiGateEnabled": "0",
                "notificationAiQueueWorkerCount": "0",
            },
        )

        self.assertEqual("REALTIME_REASONING", trace["lane"])
        ai_stage = next(item for item in trace["stages"] if item["stageKey"] == "notification-ai")
        self.assertEqual("skipped-disabled", ai_stage["status"])
        native_query_stage = next(
            item for item in trace["stages"]
            if item["stageKey"] == "typedb-native:nativeRuleQueriesMs"
        )
        self.assertEqual(7000, native_query_stage["durationMs"])
        self.assertEqual("nativeInferenceMs", native_query_stage["detail"]["nestedUnder"])
        scope_stage = next(
            item for item in trace["stages"]
            if item["stageKey"] == "abox-scope-selection"
        )
        self.assertEqual("applied", scope_stage["status"])
        self.assertEqual(1, scope_stage["detail"]["selectedScopeCount"])
        self.assertEqual(
            "symbol:035420:market",
            scope_stage["detail"]["selectedScopes"][0]["scopeId"],
        )
        persistence_stage = next(
            item for item in trace["stages"]
            if item["stageKey"] == "abox-persistence"
        )
        self.assertEqual(1, persistence_stage["detail"]["scopeCount"])
        self.assertEqual(
            4,
            persistence_stage["detail"]["scopes"][0]["inserted"]["entityCount"],
        )
        rules = {item["ruleId"]: item for item in trace["rules"]}
        self.assertEqual("matched", rules["rule.price"]["status"])
        self.assertEqual("changed-rule-dependency", rules["rule.price"]["selectedReason"])
        self.assertEqual("evaluated-no-match", rules["rule.prior"]["status"])
        self.assertNotIn("rule.research", rules)
        self.assertEqual("query-error", rules["rule.failed"]["status"])
        self.assertEqual(3, trace["summary"]["ruleRunCount"])
        self.assertEqual(4, trace["summary"]["ruleOutcomeCount"])
        self.assertEqual(1, trace["summary"]["compactedRuleCount"])
        selection_stage = next(item for item in trace["stages"] if item["stageKey"] == "rulebox-selection")
        self.assertEqual(1, selection_stage["detail"]["traceSummary"]["statusCounts"]["not-applicable"])


if __name__ == "__main__":
    unittest.main()
