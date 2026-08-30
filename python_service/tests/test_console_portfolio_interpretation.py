import unittest
from types import SimpleNamespace

from digital_twin.application.console_read_model_service import ConsoleReadModelService
from digital_twin.domain.investment_reasoning import DecisionSynthesis, FactDelta, SubjectDecisionCase


class ConsolePortfolioInterpretationTest(unittest.TestCase):
    def lifecycle(self):
        return {
            "portfolioId": "portfolio:default",
            "status": "ready",
            "portfolioState": {
                "portfolioId": "portfolio:default",
                "cashWeightPct": 1.5,
                "positions": [],
            },
            "snapshotCheckpoint": {
                "observedAt": "2026-08-30T03:00:00Z",
                "balanceFingerprint": "balance:current",
                "valuationSnapshotId": "valuation:current",
                "portfolioTotal": 1000000,
                "cashBalance": 15000,
            },
            "exposureSnapshot": {
                "metrics": [{
                    "exposure_type": "position",
                    "key": "MSTR",
                    "policyDeltaPct": 12.5,
                }],
            },
            "portfolioRiskSnapshot": {
                "dataState": "partial",
                "annualizedVolatilityPct": 42,
                "maximumDrawdownPct": -23,
                "missingData": ["MSTR:benchmarkReturnSamples"],
            },
            "rebalanceState": {
                "revision": "rebalance:current",
                "dataState": "partial",
                "volatilityPolicyDeltaPct": 7,
                "drawdownPolicyDeltaPct": 3,
            },
            "rebalanceProposal": {
                "proposalId": "proposal:1",
                "status": "review-required",
                "scenarios": [],
            },
            "portfolioDecisionCycle": {
                "cycleId": "cycle:1",
                "dataState": "partial",
                "missingData": ["MSTR:benchmarkReturnSamples"],
                "candidates": [{
                    "candidate_id": "candidate:1",
                    "candidate_type": "REDUCE_POSITION_EXPOSURE",
                    "label": "MSTR 정책 초과분 검토",
                }],
            },
            "actionPlans": [{
                "planId": "plan:1",
                "action": "TRIM",
                "status": "review-required",
                "orderIntents": [{"symbol": "MSTR"}],
            }],
        }

    @staticmethod
    def subject_case(
        updated_at="2026-08-30T03:01:00Z",
        with_ai=True,
        subject_revision="rebalance:current",
    ):
        return {
            "subjectCaseId": "case:portfolio:1",
            "sourceAboxSnapshotId": "abox:1",
            "sourceSubjectId": "portfolio:default",
            "sourceSubjectRevision": subject_revision,
            "inferenceGenerationId": "generation:1",
            "stage": "PUBLISHED" if with_ai else "OBSERVATION",
            "updatedAt": updated_at,
            "synthesis": {
                "execution_action": "HOLD",
                "data_state": "partial",
            },
            "aiJudgment": {
                "result_id": "ai-result:1",
                "action": "TRIM",
                "confidence": 0.74,
                "rationale": "집중 위험을 낮추되 거래 비용을 함께 확인합니다.",
                "next_observations": ["MSTR 유동성 확인"],
                "reversal_conditions": ["종목 비중이 정책 상한 아래로 회복"],
                "model": "test-model",
            } if with_ai else {},
            "publication": {
                "outcomeKind": "FINAL" if with_ai else "OBSERVATION",
                "createdAt": updated_at,
                "explanationSnapshot": {"reason": "변화가 없어 AI를 실행하지 않았습니다."},
            },
        }

    def test_summary_separates_current_calculation_from_current_ai_interpretation(self):
        payload = ConsoleReadModelService().portfolio(
            self.lifecycle(),
            "summary",
            subject_case=self.subject_case(),
        )

        interpretation = payload["interpretation"]
        self.assertEqual("portfolio-interpretation-v1", interpretation["contract"])
        self.assertEqual("ready", interpretation["status"])
        self.assertTrue(interpretation["ai"]["executed"])
        self.assertTrue(interpretation["ai"]["current"])
        self.assertEqual("TRIM", interpretation["action"])
        self.assertEqual("generation:1", interpretation["trace"]["inferenceGenerationId"])
        self.assertGreaterEqual(len(interpretation["drivers"]), 3)
        self.assertEqual("ready", payload["summary"]["interpretationStatus"])

    def test_old_ai_result_is_explicitly_marked_stale(self):
        payload = ConsoleReadModelService().portfolio(
            self.lifecycle(),
            "interpretation",
            subject_case=self.subject_case("2026-08-28T03:00:00Z", subject_revision=""),
        )

        interpretation = payload["interpretation"]
        self.assertEqual("stale", interpretation["status"])
        self.assertEqual("stale", interpretation["revision"]["state"])
        self.assertFalse(interpretation["ai"]["current"])

    def test_exact_subject_revision_takes_precedence_over_observation_time(self):
        payload = ConsoleReadModelService().portfolio(
            self.lifecycle(),
            "interpretation",
            subject_case=self.subject_case("2026-08-28T03:00:00Z"),
        )

        interpretation = payload["interpretation"]
        self.assertEqual("ready", interpretation["status"])
        self.assertEqual("current", interpretation["revision"]["state"])
        self.assertEqual("subject-revision", interpretation["revision"]["comparisonBasis"])

    def test_different_subject_revision_is_stale_even_when_recent(self):
        payload = ConsoleReadModelService().portfolio(
            self.lifecycle(),
            "interpretation",
            subject_case=self.subject_case(subject_revision="rebalance:old"),
        )

        interpretation = payload["interpretation"]
        self.assertEqual("stale", interpretation["status"])
        self.assertEqual("subject-revision", interpretation["revision"]["comparisonBasis"])

    def test_rebalance_includes_review_plan_without_automatic_execution(self):
        payload = ConsoleReadModelService().portfolio(
            self.lifecycle(),
            "rebalance",
            subject_case=self.subject_case(with_ai=False),
        )

        self.assertEqual("review-required", payload["actionPlans"][0]["status"])
        self.assertEqual(1, payload["actionPlans"][0]["orderIntentCount"])
        self.assertFalse(payload["interpretation"]["ai"]["executed"])


class PortfolioReasoningRevisionLineageTest(unittest.TestCase):
    def test_subject_revision_is_preserved_from_request_to_subject_case(self):
        request = SimpleNamespace(
            source_event_ids=("event:1",),
            account_ids=("default",),
            symbols=(),
            fact_types=("RebalanceState",),
            source_observed_at="2026-08-30T03:00:00Z",
            context={
                "workClasses": ["PORTFOLIO"],
                "subjectIds": ["portfolio:default"],
                "subjectRevisions": {"portfolio:default": "rebalance:current"},
            },
        )
        fact_delta = FactDelta.from_request(request)
        batch_case = SimpleNamespace(
            case_id="reasoning-case:1",
            request_id="request:1",
            deployment_id="deployment:1",
            release_fingerprint="release:1",
            fact_delta=fact_delta,
        )
        synthesis = DecisionSynthesis(
            synthesis_id="synthesis:1",
            account_id="default",
            symbol="",
            source_abox_snapshot_id="abox:1",
            inference_generation_id="generation:1",
        )

        subject_case = SubjectDecisionCase.create(batch_case, synthesis, ())

        self.assertEqual("portfolio:default", subject_case.source_subject_id)
        self.assertEqual("rebalance:current", subject_case.source_subject_revision)
        restored = SubjectDecisionCase.from_dict(subject_case.to_dict())
        self.assertEqual("rebalance:current", restored.source_subject_revision)

        subject_case.mark_delivery("suppressed", "cooldown")
        restored = SubjectDecisionCase.from_dict(subject_case.to_dict())
        self.assertEqual("READY", restored.stage)
        self.assertEqual("suppressed", restored.delivery_state)
        self.assertEqual("cooldown", restored.delivery_reason)


if __name__ == "__main__":
    unittest.main()
