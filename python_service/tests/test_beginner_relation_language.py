import unittest

from digital_twin.domain.investment_research import build_active_investment_opinion
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.application.notification_ai_gate_message import execution_telegram_message
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

    def test_absolute_beginner_message_keeps_full_validated_content(self):
        response = NotificationAIValidatedResponse(
            action="HOLD",
            action_label="보유",
            confidence=64,
            summary="관계 강도와 RuleBox 결과를 확인했습니다.",
            opinion="실행 가능 용량은 매도 뜻이 아니라 주문해도 무리가 없는지 보는 값입니다.",
            evidence=[
                "근거 1",
                "근거 2",
                "근거 3",
                "근거 4",
                "근거 5",
            ],
            counter_evidence=[
                "반대 1",
                "반대 2",
                "반대 3",
                "반대 4",
            ],
            invalidation_condition="벤치마크 베타와 관계 신호가 약해지면 판단을 다시 봅니다.",
            next_checks=[
                "확인 1",
                "확인 2",
                "확인 3",
                "확인 4",
            ],
            missing_data_impact=[
                "부족 1",
                "부족 2",
                "부족 3",
                "부족 4",
                "부족 5",
            ],
            validation_warnings=[
                "검증 1",
                "검증 2",
                "검증 3",
            ],
        )

        message = execution_telegram_message(
            {
                "messageDeliveryLevel": "absoluteBeginner",
                "title": "애플 알림",
                "target": "애플 / AAPL",
                "displayTarget": "애플 / AAPL",
            },
            response,
        )

        for expected in [
            "근거 5",
            "반대 4",
            "확인 4",
            "부족 5",
            "검증 3",
        ]:
            self.assertIn(expected, message)
        self.assertIn("확인 필요 강도", message)
        self.assertIn("관계 분석 규칙", message)
        self.assertIn("지금 주문해도 무리가 없는지", message)
        self.assertIn("시장과 같이 움직이는 정도", message)

    def test_beginner_message_adds_term_hints_without_hiding_content(self):
        response = NotificationAIValidatedResponse(
            action="HOLD",
            action_label="보유",
            confidence=64,
            summary="관계 강도와 RuleBox를 확인했습니다.",
            opinion="벤치마크 베타와 실행 가능 용량을 함께 봅니다.",
            evidence=["근거 1", "근거 2", "근거 3", "근거 4", "근거 5"],
            counter_evidence=["반대 1", "반대 2", "반대 3", "반대 4"],
            next_checks=["확인 1", "확인 2", "확인 3", "확인 4"],
            missing_data_impact=["부족 1", "부족 2", "부족 3", "부족 4", "부족 5"],
        )

        message = execution_telegram_message(
            {
                "messageDeliveryLevel": "beginner",
                "title": "애플 알림",
                "target": "애플 / AAPL",
            },
            response,
        )

        self.assertIn("근거 5", message)
        self.assertIn("반대 4", message)
        self.assertIn("확인 4", message)
        self.assertIn("부족 5", message)
        self.assertIn("관계 강도(여러 근거가 같은 방향인지 보는 확인 필요 점수)", message)
        self.assertIn("RuleBox(관계 분석 규칙)", message)


if __name__ == "__main__":
    unittest.main()
