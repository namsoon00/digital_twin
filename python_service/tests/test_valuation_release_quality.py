import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.model_registry.domain.valuation_release import build_valuation_release_manifest, compare_valuation_release_pair, rollback_receipt
from digital_twin.modules.outcomes.domain.investment_assistant_quality import evaluate_investment_assistant_quality


class ValuationReleaseQualityTests(unittest.TestCase):
    def budgets(self):
        return {
            "maxQueueDepth": 10, "maxDbWriteMs": 500, "maxMemoryMb": 1024, "maxCpuPct": 80,
            "maxCollectionRequests": 20, "maxAiInputTokens": 100000, "maxAiOutputTokens": 20000,
        }

    def runtimes(self):
        active = {"graphDatabase": "active-db", "queueNamespace": "active-q", "cacheNamespace": "active-c"}
        candidate = {
            "graphDatabase": "candidate-db", "queueNamespace": "candidate-q", "cacheNamespace": "candidate-c",
            "notificationCreate": False, "notificationEnqueue": False, "notificationTransport": False,
        }
        return active, candidate

    def test_candidate_manifest_requires_isolation_budgets_and_zero_delivery_capability(self):
        active, candidate = self.runtimes()
        manifest = build_valuation_release_manifest(
            release_id="valuation-r1", git_revision="abc123", target_symbols=["NVDA", "PLTR"],
            model_versions={"valuation": "v1"}, source_bundle_ids=["bundle:1", "bundle:2"],
            candidate_runtime=candidate, active_runtime=active, resource_budgets=self.budgets(),
            rollback_artifact_id="artifact:previous",
        )
        self.assertEqual("ready-for-isolated-comparison", manifest["status"])
        self.assertFalse(manifest["deliveryAuthorization"])

        candidate["notificationEnqueue"] = True
        blocked = build_valuation_release_manifest(
            release_id="valuation-r1", git_revision="abc123", target_symbols=["NVDA"],
            model_versions={"valuation": "v1"}, source_bundle_ids=["bundle:1"],
            candidate_runtime=candidate, active_runtime=active, resource_budgets={}, rollback_artifact_id="",
        )
        self.assertEqual("blocked", blocked["status"])
        self.assertTrue(any("candidate-delivery-capability-forbidden" in item for item in blocked["blockedReasons"]))
        self.assertTrue(any("resource-budget-missing" in item for item in blocked["blockedReasons"]))

    def test_paired_comparison_rejects_input_drift_and_delivery_side_effect(self):
        baseline = {
            "sourceBundleId": "bundle:1", "observationClock": "2026-09-25T00:00:00Z",
            "accountId": "a", "symbol": "NVDA", "targetSecurityLine": "NVDA",
            "activePointerBefore": "active-r0", "activePointerAfter": "active-r0",
            "assessment": {"fairValue": 100, "decisionEligible": False},
        }
        candidate = {**baseline, "assessment": {"fairValue": 105, "decisionEligible": False}}
        passed = compare_valuation_release_pair(baseline, candidate, allowed_difference_fields=["fairValue"])
        self.assertEqual("passed", passed["status"])

        candidate = {**candidate, "sourceBundleId": "bundle:future", "notificationEnqueued": True}
        failed = compare_valuation_release_pair(baseline, candidate, allowed_difference_fields=["fairValue"])
        self.assertEqual("failed", failed["status"])
        self.assertFalse(failed["sameFrozenInput"])
        self.assertEqual(1, failed["candidateDeliveryCount"])

    def test_rollback_requires_pointer_alignment_and_old_jobs_superseded(self):
        receipt = rollback_receipt(
            failed_release_id="r1", restored_release_id="r0", restored_artifact_id="artifact:r0",
            pointers={"activeReleaseId": "r0", "readReleaseId": "r0"},
            pending_jobs=[{"releaseId": "r1", "state": "superseded"}],
        )
        self.assertEqual("restored", receipt["status"])

        incomplete = rollback_receipt(
            failed_release_id="r1", restored_release_id="r0", restored_artifact_id="artifact:r0",
            pointers={"activeReleaseId": "r1", "readReleaseId": "r0"},
            pending_jobs=[{"releaseId": "r1", "state": "pending"}],
        )
        self.assertEqual("incomplete", incomplete["status"])
        self.assertIn("failed-release-jobs-still-active", incomplete["blockedReasons"])

    def test_quality_report_counts_independent_episodes_and_excludes_future_replay(self):
        rows = [
            {
                "episodeId": "e1", "recordedAt": "2026-01-01T00:00:00Z", "sourceTraceComplete": True,
                "calculationReproducible": True, "unsupportedClaimCount": 0, "verifiableClaimCount": 5,
                "duplicateDeliveryCount": 0, "successfulDeliveryCount": 1, "labelledMaterialEventCount": 1,
                "missedMaterialEventCount": 0, "aiAttemptCount": 1, "aiFailureCount": 0, "endToEndLatencyMs": 1000,
            },
            {
                "episodeId": "e2", "recordedAt": "2026-01-02T00:00:00Z", "sourceTraceComplete": True,
                "calculationReproducible": True, "unsupportedClaimCount": 0, "verifiableClaimCount": 4,
                "duplicateDeliveryCount": 0, "successfulDeliveryCount": 1, "labelledMaterialEventCount": 1,
                "missedMaterialEventCount": 0, "aiAttemptCount": 1, "aiFailureCount": 0, "endToEndLatencyMs": 1200,
            },
            {"episodeId": "future", "futureInformationUsed": True},
        ]
        report = evaluate_investment_assistant_quality(
            rows, minimum_independent_episodes=2, maximum_unsupported_claim_rate=0,
            maximum_duplicate_delivery_rate=0, minimum_reproducibility_rate=1,
        )
        self.assertEqual(2, report["metrics"]["independentEpisodeCount"])
        self.assertEqual("remain-limited-or-reference", report["expansionDecision"])
        self.assertFalse(report["gates"]["futureLeakage"])


if __name__ == "__main__":
    unittest.main()
