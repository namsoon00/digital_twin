import unittest

from digital_twin.application.reasoning_engine_platform import ReasoningEnginePlatformService
from digital_twin.domain.reasoning_engine_versions import (
    EngineReleaseBundle,
    ReasoningEngineDescriptor,
    engine_transition_allowed,
    promotion_blockers,
    reasoning_release_identity,
)


def descriptor(status="candidate"):
    return ReasoningEngineDescriptor(
        engine_family="ontology-investment-brain",
        engine_version="v2",
        deployment_id="ontology-v2-shadow",
        status=status,
        graph_store_binding="typedb-v9",
        time_series_backend_id="questdb-shadow",
        release_bundle=EngineReleaseBundle("tbox-v1", "rulebox-v1", "prompt-v1", "features-v1"),
    )


class ReasoningEngineVersionTests(unittest.TestCase):
    def test_release_identity_changes_with_runtime_or_rulebox(self):
        first = reasoning_release_identity(descriptor(), "rules-a")
        second = reasoning_release_identity(descriptor(), "rules-a")
        changed_rules = reasoning_release_identity(descriptor(), "rules-b")
        changed_runtime_descriptor = ReasoningEngineDescriptor(
            **{
                **descriptor().__dict__,
                "release_bundle": EngineReleaseBundle(
                    "tbox-v1", "rulebox-v1", "prompt-v1", "features-v1",
                    runtime_revision="revision-b",
                ),
            }
        )
        changed_runtime = reasoning_release_identity(changed_runtime_descriptor, "rules-a")

        self.assertEqual(first, second)
        self.assertNotEqual(first["releaseFingerprint"], changed_rules["releaseFingerprint"])
        self.assertNotEqual(first["releaseFingerprint"], changed_runtime["releaseFingerprint"])
        self.assertTrue(first["validationCohortId"].startswith("reasoning-cohort:"))

    def test_shadow_must_become_candidate_before_active(self):
        self.assertTrue(engine_transition_allowed("shadow", "candidate"))
        self.assertFalse(engine_transition_allowed("shadow", "active"))

    def test_promotion_requires_full_parity_and_zero_shadow_delivery(self):
        ready = {
            "factParityPct": 100,
            "ruleSlotCoveragePct": 100,
            "unexplainedDecisionDifferenceCount": 0,
            "shadowDeliveryCount": 0,
        }
        self.assertEqual((), promotion_blockers(descriptor(), {"status": "ready"}, ready))

        unsafe = dict(ready, factParityPct=99.5, shadowDeliveryCount=1)
        self.assertEqual(
            ("fact-parity-incomplete", "shadow-delivery-detected"),
            promotion_blockers(descriptor(), {"status": "ready"}, unsafe),
        )

    def test_history_gate_requires_coverage_freshness_and_zero_delivery(self):
        class Registry:
            def get(self, deployment_id):
                return {
                    "deploymentId": deployment_id,
                    "status": "shadow",
                    "health": {"status": "ready"},
                }

        class Comparisons:
            def summary(self, deployment_id, limit=200, **kwargs):
                del deployment_id, limit, kwargs
                return {
                    "sampleCount": 20,
                    "distinctSymbolCount": 5,
                    "nonEmptyNativeInferenceSampleCount": 20,
                    "nonEmptyDecisionSampleCount": 20,
                    "distinctMatchedRuleCount": 5,
                    "marketClassCount": 2,
                    "minimumFactParityPct": 100.0,
                    "minimumRuleSlotCoveragePct": 100.0,
                    "unexplainedDecisionDifferenceCount": 0,
                    "shadowDeliveryCount": 0,
                    "statusCounts": {"equivalent": 20},
                    "baselineP95DurationMs": 100,
                    "candidateP95DurationMs": 120,
                    "queueWaitP95Ms": 20,
                    "latestComparisonAt": "2099-01-01T00:00:00Z",
                }

        platform = ReasoningEnginePlatformService(
            Registry(),
            {
                "reasoningEnginePromotionMinimumComparisons": "20",
                "reasoningEnginePromotionMinimumSymbols": "5",
                "reasoningEnginePromotionMinimumNativeInferenceSamples": "20",
                "reasoningEnginePromotionMinimumDecisionSamples": "20",
                "reasoningEnginePromotionMinimumMatchedRules": "5",
                "reasoningEnginePromotionMinimumMarketClasses": "2",
            },
            comparison_store=Comparisons(),
        )

        readiness = platform.promotion_readiness("ontology-v2-shadow")

        self.assertTrue(readiness["ready"])
        self.assertEqual([], readiness["blockers"])


if __name__ == "__main__":
    unittest.main()
