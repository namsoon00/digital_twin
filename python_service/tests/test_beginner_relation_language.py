import unittest

from digital_twin.domain.investment_research import build_active_investment_opinion
from digital_twin.domain.notification_ontology_sections import ontology_rule_lines
from digital_twin.domain.portfolio import Position


class BeginnerRelationLanguageTests(unittest.TestCase):
    def holding_position(self):
        return Position(
            symbol="AAPL",
            name="애플",
            market="US",
            currency="USD",
            source="holding",
            quantity=1,
            sellable_quantity=1,
            average_price=313.51,
            current_price=315.11,
            market_value=315.11,
            profit_loss_rate=0.5,
            ma5=315.48,
            ma20=300.39,
            ma60=294.77,
            ma5_distance=-0.1,
            ma20_distance=4.9,
            ma60_distance=6.9,
        )

    def test_execution_capacity_signal_does_not_create_sell_opinion(self):
        opinion = build_active_investment_opinion(
            self.holding_position(),
            {
                "signalStrength": 94,
                "decision": {
                    "actionGroup": "executionRisk",
                    "actionLevel": "watch",
                    "score": 94,
                    "label": "실행 가능 용량 확인",
                },
                "activeRules": [
                    {
                        "ruleId": "graph.execution.capacity_safe.v1",
                        "label": "보유 종목 + 작은 실행 노출 -> 실행 가능 용량 확인",
                        "strengthScore": 94,
                        "relationType": "HAS_EXECUTION_CAPACITY",
                    }
                ],
            },
        )

        self.assertEqual("HOLD", opinion.action)
        self.assertIn("바로 사고팔기보다", opinion.thesis)

    def test_event_risk_without_price_breakdown_does_not_force_sell(self):
        opinion = build_active_investment_opinion(
            self.holding_position(),
            {
                "signalStrength": 94,
                "decision": {
                    "actionGroup": "eventRisk",
                    "actionLevel": "review",
                    "score": 94,
                    "label": "뉴스 리스크 대응 검토",
                },
                "activeRules": [
                    {
                        "ruleId": "graph.news.direct_material_risk.v1",
                        "label": "보유 종목 + 직접 중요 리스크 뉴스 -> 이벤트 리스크 추론",
                        "strengthScore": 94,
                        "relationType": "HAS_INFERRED_RISK",
                    }
                ],
            },
        )

        self.assertNotEqual("SELL", opinion.action)
        self.assertIn(opinion.action, {"HOLD", "TRIM"})

    def test_relation_lines_explain_execution_capacity_in_plain_language(self):
        lines = ontology_rule_lines({
            "ontologyRelationContext": {
                "signalStrength": 94,
                "signalStrengthLabel": "매우 강함",
                "confidence": 94,
                "decision": {
                    "actionGroup": "executionRisk",
                    "label": "실행 가능 용량 확인",
                },
                "activeRules": [
                    {
                        "ruleId": "graph.execution.capacity_safe.v1",
                        "label": "보유 종목 + 작은 실행 노출 -> 실행 가능 용량 확인",
                        "strengthScore": 94,
                    }
                ],
            }
        })

        joined = "\n".join(lines)
        self.assertIn("팔아야 한다는 뜻이 아니라", joined)
        self.assertIn("매도해야 한다는 뜻은 아닙니다", joined)


if __name__ == "__main__":
    unittest.main()
