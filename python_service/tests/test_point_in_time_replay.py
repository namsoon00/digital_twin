import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.historical_decision_replay_service import HistoricalDecisionReplayService
from digital_twin.domain.investment_brain import decision_replay_manifest
from digital_twin.domain.point_in_time_replay import (
    DecisionReplayEnvelope,
    observations_as_of,
    point_in_time_assessment,
)


def episode(episode_id, decided_at, *, facts=None):
    return {
        "episodeId": episode_id,
        "accountId": "main",
        "symbol": "005930",
        "decidedAt": decided_at,
        "engineVersion": "ontology-investment-brain-v5",
        "inferenceGenerationId": "generation:" + episode_id,
        "sourceAboxSnapshotId": "abox:" + episode_id,
        "action": "HOLD",
        "factsAtDecision": facts or {
            "currentPrice": 80000,
            "updatedAt": decided_at,
            "hypothesisOutcomeContract": {
                "contractVersion": "hypothesis-outcome-contract-v2",
                "contractFingerprint": "contract:" + episode_id,
                "effectiveAt": decided_at,
            },
        },
        "hypothesisSet": {"version": "typedb-causal-hypotheses-v6"},
    }


class ReplayStore:
    def __init__(self, records):
        self.records = list(records)
        self.calls = 0

    def list_replay_records(self, account_id="", symbol="", limit=500):
        self.calls += 1
        rows = list(self.records)
        if account_id:
            rows = [row for row in rows if row["episodeSnapshot"].get("accountId") == account_id]
        if symbol:
            rows = [row for row in rows if row["episodeSnapshot"].get("symbol") == symbol]
        return rows[:limit]


class PointInTimeReplayTests(unittest.TestCase):
    def test_new_decision_manifest_freezes_graph_prompt_and_model_versions(self):
        result = decision_replay_manifest(
            {"notificationAiReplayManifest": {
                "promptVersion": "prompt:5",
                "modelVersion": "model:5",
                "decisionContractVersion": "decision:4",
                "reasoningEffort": "max",
            }},
            {
                "engineVersion": "typedb:v2",
                "inferenceGenerationId": "generation:5",
                "sourceAboxSnapshotId": "abox:5",
                "ruleboxRulesHash": "rulebox:5",
            },
        )

        self.assertTrue(result["tboxFingerprint"])
        self.assertEqual("rulebox:5", result["ruleboxFingerprint"])
        self.assertEqual("prompt:5", result["promptVersion"])
        self.assertEqual("model:5", result["modelVersion"])

    def test_temporal_contract_rejects_future_knowledge_but_not_future_deadline(self):
        result = point_in_time_assessment({
            "quote": {"sourceAsOf": "2026-08-15T00:00:00Z"},
            "news": {"publishedAt": "2026-08-15T00:10:01Z"},
            "followUp": {"expiresAt": "2026-08-20T00:00:00Z"},
        }, "2026-08-15T00:10:00Z", snapshot_anchored=True)

        self.assertFalse(result["passed"])
        self.assertEqual(1, result["futureTimestampCount"])
        self.assertEqual("facts.news.publishedAt", result["futureTimestampSamples"][0]["path"])

    def test_observation_partition_never_accepts_missing_or_future_clock(self):
        result = observations_as_of([
            {"outcomeId": "known", "observedAt": "2026-08-15T00:30:00Z"},
            {"outcomeId": "future", "observedAt": "2026-08-15T01:30:00Z"},
            {"outcomeId": "missing"},
        ], "2026-08-15T01:00:00Z")

        self.assertEqual(1, result["includedCount"])
        self.assertEqual(1, result["futureExcludedCount"])
        self.assertEqual(1, result["missingTimestampExcludedCount"])

    def test_contract_accepts_persisted_provider_timestamp_formats(self):
        result = point_in_time_assessment({
            "news": {"publishedAt": "Mon, 10 Aug 2026 05:05:46 GMT"},
            "research": {"observedAt": "20260719T091519"},
            "disclosure": {"receiptDate": "20260810"},
        }, "2026-08-10T06:31:15Z", snapshot_anchored=True)

        self.assertTrue(result["passed"])
        self.assertEqual(3, result["checkedTimestampCount"])
        self.assertEqual(1, result["coarseTimestampCount"])

    def test_replay_envelope_is_deterministic_and_keeps_facts_hidden_by_default(self):
        first = DecisionReplayEnvelope.create(episode("one", "2026-08-15T01:00:00Z"))
        second = DecisionReplayEnvelope.create(episode("one", "2026-08-15T01:00:00Z"))

        self.assertEqual("exact-input-replay", first.replay_class())
        self.assertEqual(first.input_fingerprint, second.input_fingerprint)
        self.assertNotIn("factsAtDecision", first.to_dict())
        self.assertEqual(4, len(first.engine_manifest["missingEngineComparisonFields"]))

    def test_replay_envelope_accepts_complete_engine_comparison_manifest(self):
        payload = episode("complete", "2026-08-15T01:00:00Z")
        payload["factsAtDecision"]["engineManifest"] = {
            "tboxFingerprint": "tbox:1",
            "ruleboxFingerprint": "rulebox:1",
            "promptVersion": "prompt:1",
            "modelVersion": "model:1",
        }

        result = DecisionReplayEnvelope.create(payload).to_dict()

        self.assertTrue(result["engineComparisonReady"])
        self.assertEqual([], result["engineManifest"]["missingEngineComparisonFields"])

    def test_service_separates_original_snapshot_from_later_outcomes(self):
        first = episode("one", "2026-08-15T00:00:00Z")
        second = episode("two", "2026-08-15T01:00:00Z")
        store = ReplayStore([
            {
                "episodeSnapshot": second,
                "outcomes": [],
                "followUps": [],
                "unsupportedFollowUps": [],
            },
            {
                "episodeSnapshot": first,
                "outcomes": [
                    {"outcomeId": "known", "observedAt": "2026-08-15T00:30:00Z", "payload": {"horizonMinutes": 30}},
                    {"outcomeId": "future", "observedAt": "2026-08-15T02:00:00Z", "payload": {"horizonMinutes": 120}},
                ],
                "followUps": [
                    {"conditionId": "known", "status": "satisfied", "transitionAt": "2026-08-15T00:40:00Z"},
                    {"conditionId": "future", "status": "invalidated", "transitionAt": "2026-08-15T02:10:00Z"},
                ],
                "unsupportedFollowUps": [],
            },
        ])

        result = HistoricalDecisionReplayService(store).run(include_cases=True, case_limit=10)

        self.assertEqual("completed", result["status"])
        self.assertFalse(result["mutated"])
        self.assertFalse(result["notificationDeliveryEnabled"])
        self.assertFalse(result["operationalAboxWriteEnabled"])
        self.assertEqual(2, result["exactInputReplayCount"])
        self.assertEqual(1, result["historicalContinuity"]["adjacentDecisionPairs"])
        self.assertEqual(1, result["historicalContinuity"]["outcomesKnownByNextDecision"])
        self.assertEqual(1, result["historicalContinuity"]["futureOutcomesExcluded"])
        self.assertEqual(1, result["historicalContinuity"]["followUpTransitionsKnownByNextDecision"])
        self.assertEqual(1, result["historicalContinuity"]["futureFollowUpTransitionsExcluded"])
        self.assertEqual("not-run", result["engineExecution"]["status"])
        self.assertEqual(1, store.calls)


if __name__ == "__main__":
    unittest.main()
