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
                    "nativeExecutionMode": "schema-function",
                    "matches": [{"ruleId": "rule.price"}],
                    "executedRules": [
                        {"ruleId": "rule.price", "elapsedMs": 9000, "queryCount": 1},
                        {"ruleId": "rule.prior", "elapsedMs": 1200, "queryCount": 1},
                    ],
                    "skippedRules": [
                        {"ruleId": "rule.research", "status": "not-applicable"},
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
        rules = {item["ruleId"]: item for item in trace["rules"]}
        self.assertEqual("matched", rules["rule.price"]["status"])
        self.assertEqual("changed-rule-dependency", rules["rule.price"]["selectedReason"])
        self.assertEqual("evaluated-no-match", rules["rule.prior"]["status"])
        self.assertEqual("not-applicable", rules["rule.research"]["status"])
        self.assertEqual(3, trace["summary"]["ruleRunCount"])


if __name__ == "__main__":
    unittest.main()
