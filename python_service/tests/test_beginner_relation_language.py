import unittest

from digital_twin.domain.investment_research import build_active_investment_opinion
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.application.notification_ai_gate_message import execution_telegram_message
from digital_twin.domain.notification_ontology_sections import ontology_rule_lines, relation_axis_summary_lines
from digital_twin.domain.notification_templates import NotificationTemplate, alert_context, compact_investment_notification, render_notification
from digital_twin.domain.portfolio import AlertEvent, Position


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

    def test_long_investment_notification_is_compacted_to_one_telegram_message(self):
        rendered = "<b>판단 요약</b>\n" + ("• 긴 근거 설명입니다.\n" * 500) + '<a href="https://example.test/detail">상세</a>'
        compacted = compact_investment_notification(rendered, {
            "messageType": "investmentInsight",
            "notifyLinkUrl": "https://example.test/notifications",
            "notificationNumber": "N-TEST1234",
        })

        self.assertLessEqual(len(compacted), 3700)
        self.assertNotIn("<b>", compacted)
        self.assertIn("상세 링크: https://example.test/notifications", compacted)
        self.assertIn("알림 번호: N-TEST1234", compacted)

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

    def test_typedb_add_buy_candidate_can_create_add_opinion(self):
        position = self.holding_position()
        position.profit_loss_rate = 6.5
        relation_context = {
            "signalStrength": 78,
            "decision": {
                "actionGroup": "addBuy",
                "decisionStage": "ADD_BUY_REVIEW",
                "score": 78,
            },
            "executionPlan": {
                "addBuyAssessment": {
                    "blockedReasons": [],
                }
            },
            "activeRules": [
                {
                    "ruleId": "graph.instrument_profile.cyclical_growth.recovery_add_review.v1",
                    "relationType": "ALLOWS_ACTION",
                    "tboxClass": "AddBuyEligibility",
                    "label": "성장·사이클 회복 추가매수 후보",
                    "strengthScore": 78,
                }
            ],
        }

        opinion = build_active_investment_opinion(position, relation_context)

        self.assertEqual("ADD", opinion.action)
        self.assertGreater(opinion.score_breakdown["supportScore"], opinion.score_breakdown["riskScore"])

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

    def test_absolute_beginner_strategy_guide_compacts_repeated_detail_for_one_message(self):
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
                "아주 긴 부족 데이터 설명입니다. 이 문장은 전략 가이드에서 말줄임표 없이 끝까지 보여야 합니다. 고객이 실제 투자 판단 전에 어떤 데이터가 비어 있는지 전체 문장을 확인할 수 있어야 합니다.",
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
            "<b>판단</b>",
            "<b>핵심 근거</b>",
            "<b>다음 조건</b>",
            "근거 1",
            "근거 2",
            "근거 3",
            "반대 신호: 반대 1",
            "다시 판단할 조건",
            "확인 1",
            "부족 1",
            "부족 2",
        ]:
            self.assertIn(expected, message)
        for hidden in ["[AI]", "근거 4", "근거 5", "반대 2", "반대 3", "확인 3", "확인 4", "부족 3", "부족 4", "부족 5", "검증 3", "고객이 실제 투자 판단 전에"]:
            self.assertNotIn(hidden, message)
        self.assertIn("AI 판단 확신도", message)
        self.assertNotIn("<b>점수 안내</b>", message)
        self.assertIn("관계 분석 규칙", message)
        self.assertIn("실행 조건", message)

    def test_beginner_message_adds_term_hints_and_compacts_strategy_guide(self):
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

        self.assertIn("<b>판단</b>", message)
        self.assertIn("<b>핵심 근거</b>", message)
        self.assertIn("<b>다음 조건</b>", message)
        self.assertIn("근거 1", message)
        self.assertIn("근거 2", message)
        self.assertIn("근거 3", message)
        self.assertNotIn("근거 4", message)
        self.assertNotIn("근거 5", message)
        self.assertIn("반대 1", message)
        self.assertNotIn("반대 2", message)
        self.assertNotIn("반대 3", message)
        self.assertNotIn("반대 4", message)
        self.assertIn("확인 1", message)
        self.assertNotIn("확인 2", message)
        self.assertNotIn("확인 3", message)
        self.assertNotIn("확인 4", message)
        self.assertIn("부족 1", message)
        self.assertIn("부족 2", message)
        self.assertNotIn("부족 3", message)
        self.assertNotIn("부족 4", message)
        self.assertNotIn("부족 5", message)
        self.assertIn("AI 판단 확신도", message)
        self.assertNotIn("<b>점수 안내</b>", message)
        self.assertNotIn("관계 강도", message)
        self.assertIn("RuleBox(관계 분석 규칙)", message)

    def test_execution_message_includes_relation_axis_summary(self):
        response = NotificationAIValidatedResponse(
            action="TRIM",
            action_label="분할축소",
            confidence=74,
            summary="손실과 가격 흐름을 함께 봐 일부 줄이는 판단입니다.",
            evidence=["20일 평균보다 낮음"],
            counter_evidence=["외국인·기관 순매수"],
            next_checks=["20일 평균 회복 여부 확인"],
        )

        message = execution_telegram_message(
            {
                "messageType": "investmentInsight",
                "messageDeliveryLevel": "beginner",
                "title": "SK하이닉스 알림",
                "target": "SK하이닉스 / 000660",
                "displayTarget": "SK하이닉스 / 000660",
                "ontologyRelationContext": {
                    "executionPlan": {
                        "decisionDrivers": [
                            {
                                "category": "trend",
                                "direction": "risk",
                                "importance": 91,
                                "summary": "현재가가 20일 평균보다 12.9% 낮아 최근 가격 흐름이 약합니다.",
                            },
                            {
                                "category": "investorFlow",
                                "direction": "counter",
                                "importance": 82,
                                "summary": "외국인과 기관 합산 흐름은 순매수 1,362,211주입니다.",
                            },
                            {
                                "category": "research",
                                "direction": "risk",
                                "importance": 76,
                                "summary": "새 공시가 있어 원문 조건과 가격 반응을 함께 확인해야 합니다.",
                            },
                        ]
                    },
                    "activeRules": [
                        {
                            "ruleId": "graph.strategy_profile.loss_tolerance_breach.v1",
                            "label": "계정 손실 관리 기준 초과",
                            "strengthScore": 86,
                        }
                    ],
                },
            },
            response,
        )

        self.assertIn("<b>핵심 근거</b>", message)
        self.assertIn("가격 회복·약화", message)
        self.assertIn("수급 심리", message)
        self.assertIn("투자 성향·정책", message)
        self.assertIn("20일 평균보다 12.9%", message)
        self.assertIn("확인 필요 점수", message)
        self.assertIn("86.0/100점", message)
        self.assertIn("<b>점수 안내</b>", message)
        self.assertIn("상승·하락 확률이나 매수·매도 확률이 아니며", message)
        self.assertIn("0~34 참고", message)
        self.assertIn("70~84 대응 검토", message)
        self.assertIn("85~100 즉시 재확인", message)
        self.assertIn("규칙 신뢰도 25%", message)
        self.assertIn("위험·기회 근거 42%", message)

    def test_execution_message_includes_deterministic_valuation_details(self):
        response = NotificationAIValidatedResponse(
            action="BUY",
            action_label="매수 점검",
            confidence=72,
            summary="현재가와 적정가를 비교해 진입 조건을 확인합니다.",
            evidence=["안전마진이 요구 기준을 넘었습니다."],
            next_checks=["추세와 거래량 확인"],
        )
        context = {
            "messageType": "investmentInsight",
            "messageDeliveryLevel": "beginner",
            "title": "삼성전자 알림",
            "target": "삼성전자 / 005930",
            "displayTarget": "삼성전자 / 005930",
            "ontologyRelationContext": {
                "signalStrength": 78,
                "decision": {
                    "actionGroup": "valuation",
                    "label": "저평가 조건 확인",
                },
                "facts": {
                    "currency": "KRW",
                    "currentPrice": 80000,
                    "valuationRows": [{"sourceType": "user"}],
                    "valuationFormula": "적정가 = 예상 EPS x 목표 PER",
                    "valuationSubstitution": "9,000원 x 11배 = 99,000원",
                    "valuationCurrentPrice": 80000,
                    "valuationFairValue": 99000,
                    "valuationMarginOfSafetyPct": 23.75,
                    "valuationMinimumMarginOfSafetyPct": 20,
                    "valuationSourceLabel": "사용자 입력",
                    "valuationReliabilityLabel": "사용자 가정",
                    "valuationReliabilityScore": 55,
                    "valuationExplanation": "예상 EPS 9,000원에 목표 PER 11배를 적용해 적정가 99,000원으로 계산했습니다.",
                    "valuationDataStatus": "available",
                    "valuationMissingInputs": [],
                    "valuationHasUserInput": True,
                    "valuationHasExternalInput": False,
                },
                "executionPlan": {
                    "decisionDrivers": [
                        {
                            "category": "valuation",
                            "direction": "support",
                            "importance": 78,
                            "summary": "사용자 적정가 기준 안전마진이 있습니다.",
                        }
                    ]
                },
                "activeRules": [
                    {
                        "ruleId": "graph.valuation.margin_of_safety.opportunity.v1",
                        "label": "안전마진 + 추세/수급 확인 -> 저평가 조건 확인",
                        "strengthScore": 78,
                    }
                ],
            },
        }

        axes = relation_axis_summary_lines(context)
        message = execution_telegram_message(context, response)

        self.assertTrue(any(line.startswith("밸류에이션") for line in axes))
        self.assertIn("<b>밸류에이션</b>", message)
        self.assertIn("기준 적정가", message)
        self.assertIn("99,000원", message)
        self.assertIn("계정 기준 +20.0% 충족", message)
        self.assertIn("사용자 입력", message)
        self.assertIn("사용자 가정", message)
        self.assertIn("사용자 적정가 기준 안전마진", message)
        self.assertNotIn("대입값", message)

    def test_execution_message_marks_ai_valuation_proposal_as_unapproved(self):
        response = NotificationAIValidatedResponse(
            action="HOLD",
            action_label="보유",
            confidence=74,
            summary="AI 제안 적정가를 기준으로 보유 조건을 확인합니다.",
            evidence=["우선주는 배당수익률 기준으로 봅니다."],
            next_checks=["사용자 적정가 승인 여부 확인"],
        )
        message = execution_telegram_message(
            {
                "messageType": "investmentInsight",
                "messageDeliveryLevel": "absoluteBeginner",
                "title": "STRC 알림",
                "target": "스트래티지 우선주 / STRC",
                "displayTarget": "스트래티지 우선주 / STRC",
                "ontologyRelationContext": {
                    "facts": {
                        "currency": "USD",
                        "currentPrice": 87.76,
                        "valuationRows": [{"sourceType": "ai"}],
                        "valuationFormula": "AI 제안 적정가 = 연간 배당 / 요구수익률",
                        "valuationSubstitution": "연간 배당 $9 / 요구수익률 9.5% = $94.74",
                        "valuationCurrentPrice": 87.76,
                        "valuationFairValue": 94.7368,
                        "valuationMarginOfSafetyPct": 7.95,
                        "valuationMinimumMarginOfSafetyPct": 8,
                        "valuationSourceLabel": "AI 제안",
                        "valuationReliabilityLabel": "AI 초안(사용자 검토 전)",
                        "valuationReliabilityScore": 58,
                        "valuationExplanation": "연간 배당 $9을 요구수익률 9.5%로 나눠 적정가 $94.74로 계산했습니다. 이 값은 AI 제안값이라 사용자 검토 전 초안입니다.",
                        "valuationDataStatus": "available",
                        "valuationMissingInputs": [],
                        "valuationHasUserInput": False,
                        "valuationHasExternalInput": False,
                        "valuationHasAiProposal": True,
                        "valuationApprovalStatus": "ai_applied_pending_review",
                        "valuationReviewStatus": "ai_applied_pending_review",
                        "valuationAutoApplied": True,
                        "valuationRequiresUserApproval": True,
                        "valuationIsAiGenerated": True,
                        "valuationSourceReason": "우선주/인컴형은 보통주 PER보다 배당수익률 기준 적정가가 더 적합합니다.",
                        "valuationPerStatus": "not_applicable",
                        "valuationPerReason": "우선주와 배당형 상품은 보통주 이익 배수보다 배당, 액면 기준가, 요구수익률이 가격 설명에 더 직접적입니다.",
                        "valuationPreferredMetric": "배당수익률/요구수익률",
                        "valuationFundamentalDataSourcePriority": "배당 조건 > 금리/요구수익률 > 외부 PER",
                    },
                },
            },
            response,
        )

        self.assertIn("<b>밸류에이션</b>", message)
        self.assertIn("AI 제안", message)
        self.assertIn("사용자 검토 전", message)
        self.assertIn("연간 배당", message)
        self.assertIn("요구수익률", message)
        self.assertIn("배당수익률 기준", message)
        self.assertNotIn("데이터 우선순위", message)

    def test_execution_message_shows_valuation_missing_state(self):
        response = NotificationAIValidatedResponse(
            action="HOLD",
            action_label="보유",
            confidence=61,
            summary="가격 흐름을 먼저 확인합니다.",
            evidence=["20일 평균선 근처입니다."],
            next_checks=["적정가 입력 여부 확인"],
        )
        message = execution_telegram_message(
            {
                "messageType": "investmentInsight",
                "messageDeliveryLevel": "beginner",
                "title": "엔비디아 알림",
                "target": "엔비디아 / NVDA",
                "displayTarget": "엔비디아 / NVDA",
                "ontologyRelationContext": {
                    "facts": {
                        "currency": "USD",
                        "currentPrice": 164.25,
                        "valuationRows": [],
                        "valuationDataStatus": "missing",
                        "valuationMissingInputs": ["적정가", "예상 EPS", "목표 PER"],
                    },
                    "activeRules": [
                        {
                            "ruleId": "graph.price.reclaim.thesis_support.v1",
                            "label": "가격 회복 조건 확인",
                            "strengthScore": 62,
                        }
                    ],
                },
            },
            response,
        )

        self.assertIn("<b>밸류에이션</b>", message)
        self.assertIn("기준 적정가", message)
        self.assertIn("계산 불가", message)
        self.assertIn("적정가 · 예상 EPS · 목표 PER", message)
        self.assertNotIn("계산 상태", message)

    def test_template_message_includes_relation_axis_summary(self):
        event = AlertEvent(
            "main",
            "메인",
            "WATCH",
            "investmentInsight",
            "main:insight:000660",
            "SK하이닉스",
            [
                "현재가: 2,118,000원",
                "수익률: -9.6%",
                "상태: 분할축소 우선 점검",
            ],
            symbol="000660",
            metadata={
                "ontologyRelationContext": {
                    "executionPlan": {
                        "decisionDrivers": [
                            {
                                "category": "trend",
                                "direction": "risk",
                                "importance": 91,
                                "summary": "현재가가 20일 평균보다 12.9% 낮아 최근 가격 흐름이 약합니다.",
                            },
                            {
                                "category": "investorFlow",
                                "direction": "counter",
                                "importance": 82,
                                "summary": "외국인과 기관 합산 흐름은 순매수 1,362,211주입니다.",
                            },
                        ]
                    },
                    "activeRules": [
                        {
                            "ruleId": "graph.strategy_profile.loss_tolerance_breach.v1",
                            "label": "계정 손실 관리 기준 초과",
                            "strengthScore": 86,
                        }
                    ],
                }
            },
        )

        message = render_notification(NotificationTemplate("investmentInsight", "{telegramMessage}"), alert_context(event))

        self.assertIn("<b>관계축 요약</b>", message)
        self.assertIn("가격 회복·약화", message)
        self.assertIn("수급 심리", message)
        self.assertIn("투자 성향·정책", message)
        self.assertIn("20일 평균보다 12.9%", message)

    def test_absolute_beginner_relation_block_uses_plain_language(self):
        message = render_notification(
            NotificationTemplate("investmentInsight", "{telegramMessage}"),
            {
                "messageType": "investmentInsight",
                "messageDeliveryLevel": "absoluteBeginner",
                "telegramMessage": "<b>[관찰] 🛡️ SK하이닉스: 분할축소 우선 점검</b>",
                "displayTarget": "SK하이닉스 / 000660",
                "ontologyRelationContext": {
                    "graphStoreUsed": True,
                    "inferenceBoxUsed": True,
                    "engineVersion": "typedb-inferencebox-relation-context-v1",
                    "signalStrength": 94,
                    "signalStrengthLabel": "매우 강함",
                    "confidence": 94,
                    "decision": {
                        "actionGroup": "eventRisk",
                        "label": "뉴스 리스크 대응 검토",
                        "selectedRuleId": "graph.disclosure.event_risk.v1",
                    },
                    "activeRules": [
                        {
                            "ruleId": "graph.disclosure.event_risk.v1",
                            "label": "보유 종목 + 공시/신고 이벤트 -> 공시 이벤트 리스크 추론",
                            "strengthScore": 94,
                        },
                        {
                            "ruleId": "graph.execution.capacity_safe.v1",
                            "label": "보유 종목 + 작은 실행 노출 -> 실행 가능 용량 확인",
                            "strengthScore": 94,
                        },
                    ],
                },
            },
        )

        self.assertIn("<b>관계 판단 쉽게 보기</b>", message)
        self.assertIn("관계 분석은 SK하이닉스", message)
        self.assertIn("94점으로", message)
        self.assertIn("가격이 오를지 맞히는 값이 아니라", message)
        self.assertIn("뉴스나 공시 때문에 보유 이유를 다시 확인", message)
        self.assertIn("매도 확정은 아닙니다", message)
        self.assertIn("추론은 SK하이닉스의 현재 데이터가", message)
        self.assertIn("보유 종목이고 공시/신고 이벤트", message)
        self.assertIn("알림으로 연결하는 방식입니다", message)
        self.assertIn("새 공시나 신고가 있어, 원문 내용과 다음 가격 반응을 함께 확인해야 합니다.", message)
        self.assertNotIn("습니다입니다", message)
        self.assertNotIn("SK하이닉스을", message)
        self.assertNotIn("엔진", message)
        self.assertNotIn("선택 규칙", message)
        self.assertNotIn("성립 규칙", message)
        self.assertNotIn("관계 신호:", message)
        self.assertNotIn("AI 질문", message)


if __name__ == "__main__":
    unittest.main()
