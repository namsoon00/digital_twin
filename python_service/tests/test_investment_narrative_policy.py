import unittest

from digital_twin.modules.decisions.domain.investment_narrative_policy import narrative_presentation_errors
from digital_twin.modules.decisions.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.modules.decisions.application.notification_ai_judgement_service import hypothesis_comparison_needs_repair


class InvestmentNarrativePolicyTests(unittest.TestCase):
    def test_no_action_does_not_allow_hold_or_trade_directions(self):
        for text in ("보유자는 1주를 보유하되 추가매수는 보류해야 합니다.",
                     "현재 행동은 보유입니다.", "HOLD하되 ADD는 보류해야 합니다.", "매도하세요."):
            with self.subTest(text=text):
                self.assertIn("actionless-narrative-contains-investment-directive",
                              narrative_presentation_errors("NO_ACTION", [text]))
        self.assertEqual([], narrative_presentation_errors("NO_ACTION", [
            "외국인의 순매수는 가격 회복을 지지하지만 거래량은 부족합니다.",
            "다음 조회에서 거래량이 늘고 20일선 위에 있는지 확인합니다.",
            "보유 수량은 1주입니다. 매수나 매도를 결정할 근거는 부족합니다.",
        ]))
        self.assertEqual([], narrative_presentation_errors("HOLD", ["현재 행동은 보유입니다."]))

    def test_completed_research_comparison_does_not_repeat_model_call(self):
        response = NotificationAIValidatedResponse(action="NO_ACTION", hypotheses=[{"hypothesisId": "research:1"}],
                                                   hypothesis_comparison_state="research-reviewed")
        self.assertFalse(hypothesis_comparison_needs_repair("investmentInsight", response))
        response.action = "BUY"
        self.assertTrue(hypothesis_comparison_needs_repair("investmentInsight", response))

    def test_customer_narrative_rejects_internal_identifiers(self):
        self.assertIn("customer-narrative-contains-internal-identifier",
                      narrative_presentation_errors("NO_ACTION", ["regime-transition-risk가 약화됐습니다."]))
