import unittest

from digital_twin.application.console_read_model_service import ConsoleReadModelService


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
    def subject_case(updated_at="2026-08-30T03:01:00Z", with_ai=True):
        return {
            "subjectCaseId": "case:portfolio:1",
            "sourceAboxSnapshotId": "abox:1",
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
            subject_case=self.subject_case("2026-08-28T03:00:00Z"),
        )

        interpretation = payload["interpretation"]
        self.assertEqual("stale", interpretation["status"])
        self.assertEqual("stale", interpretation["revision"]["state"])
        self.assertFalse(interpretation["ai"]["current"])

    def test_rebalance_includes_review_plan_without_automatic_execution(self):
        payload = ConsoleReadModelService().portfolio(
            self.lifecycle(),
            "rebalance",
            subject_case=self.subject_case(with_ai=False),
        )

        self.assertEqual("review-required", payload["actionPlans"][0]["status"])
        self.assertEqual(1, payload["actionPlans"][0]["orderIntentCount"])
        self.assertFalse(payload["interpretation"]["ai"]["executed"])


if __name__ == "__main__":
    unittest.main()
