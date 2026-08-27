import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.notification_ai_gate_validation import (  # noqa: E402
    ai_decision_input_packet,
    build_notification_ai_gate_prompt,
    local_validated_ai_response,
    validated_response_from_payload,
)
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse  # noqa: E402
from digital_twin.domain.notification_ai_gate_text import user_friendly_ai_text  # noqa: E402
from digital_twin.application.notification_ai_gate_message import (  # noqa: E402
    execution_telegram_message,
    notification_topline_change_summary,
    typedb_decision_assessment_rows,
)
from digital_twin.application.notification_service import NotificationAIValidatedGateEnricher  # noqa: E402
from digital_twin.domain.notifications import NotificationJob  # noqa: E402


def entry_context():
    return {
        "messageType": "investmentInsight",
        "displayTarget": "NVIDIA / NVDA",
        "referenceDate": "2026-07-27T01:00:00Z",
        "decisionTransition": {
            "kind": "action-changed",
            "summary": "관심 유지에서 소액 진입 검토로 바뀌었습니다.",
            "material": True,
        },
        "ontologyRelationContext": {
            "source": "typedbInferenceBox",
            "graphStore": "typedb",
            "graphStoreUsed": True,
            "fallbackUsed": False,
            "facts": {"source": "watchlist", "isWatchlist": True, "symbol": "NVDA"},
            "targetRole": "watchlist",
            "actionPolicy": "ENTRY_ONLY",
            "decision": {
                "basis": "typedbInferenceBox",
                "candidateAction": "BUY",
                "targetRole": "watchlist",
                "actionPolicy": "ENTRY_ONLY",
                "allowedActions": ["BUY", "HOLD", "AVOID"],
                "blockedActions": ["ADD", "TRIM", "SELL"],
            },
            "actionEnvelope": {
                "status": "ENTRY_ELIGIBLE",
                "statusLabel": "소액 진입 조건 성립",
                "investmentViewAction": "BUY",
                "executionAction": "BUY",
                "executionDisposition": "ready",
                "preferredAction": "BUY",
                "targetRole": "watchlist",
                "allowedActions": ["BUY", "HOLD", "AVOID"],
                "blockedActions": ["ADD", "TRIM", "SELL"],
                "aiAllowedActions": ["BUY", "HOLD", "AVOID"],
                "dataReadiness": {"state": "ready", "dataState": "sufficient", "usable": True},
                "supportRuleIds": ["graph.entry.confirmed.v1"],
                "constraintRuleIds": ["graph.macro.regime.risk.v1"],
                "nextChecks": ["다음 정규장에서 거래량이 유지되는지 확인"],
                "invalidationConditions": ["진입 지지 관계가 사라지거나 직접 반대 근거가 생기면 재검토"],
            },
            "executionPlan": {
                "actionPolicy": "ENTRY_ONLY",
                "allowedActions": ["BUY", "HOLD", "AVOID"],
                "blockedActionCodes": ["ADD", "TRIM", "SELL"],
                "primaryAction": "ENTRY_REVIEW",
                "primaryActionLabel": "소액 진입 조건 확인",
                "decisionDrivers": [],
            },
            "activeRules": [],
        },
    }


class ActionEnvelopeAiGateTests(unittest.TestCase):
    def test_local_fallback_preserves_investment_view_when_policy_defers_execution(self):
        context = entry_context()
        context["messageDeliveryLevel"] = "beginner"
        context["ontologyRelationContext"]["actionEnvelope"].update({
            "status": "ENTRY_DEFERRED",
            "investmentViewAction": "BUY",
            "executionAction": "HOLD",
            "executionDisposition": "constrained",
            "preferredAction": "HOLD",
            "selectedRuleId": "graph.entry.confirmed.v1",
            "portfolioConstraintRuleIds": ["graph.portfolio.position_limit.v1"],
        })

        response = local_validated_ai_response(context, source="typedb-fallback")
        message = execution_telegram_message(context, response)

        self.assertEqual("BUY", response.investment_view_action)
        self.assertEqual("HOLD", response.execution_action)
        self.assertEqual("HOLD", response.action)
        self.assertEqual(["graph.portfolio.position_limit.v1"], response.portfolio_constraint_rule_ids)
        self.assertIn("AI 해석", message)
        self.assertNotIn("종목 의견: 소액 진입 검토", message)
        self.assertIn("관심 유지", message)

    def test_ai_cannot_lower_entry_eligibility_without_counter_evidence(self):
        response = validated_response_from_payload(
            entry_context(),
            {
                "action": "AVOID",
                "summary": "지금은 기다립니다.",
                "opinion": "대기합니다.",
                "evidence": ["진입 관계가 성립했습니다."],
                "counterEvidence": [],
                "nextChecks": ["정규장 확인"],
            },
        )

        self.assertEqual("BUY", response.action)
        self.assertTrue(any("진입 조건" in item for item in response.validation_warnings))

    def test_ai_follow_up_contract_rejects_provider_unsupported_fields(self):
        context = entry_context()
        context["ontologyRelationContext"]["facts"].update({
            "currentPrice": 226.17,
            "volumeRatio": 0.8,
            "marketEvidenceProfile": {
                "profileKey": "US_EQUITY",
                "capabilities": {
                    "pricePath": {"state": "fresh"},
                    "volume": {"state": "fresh"},
                    "investorFlow": {"state": "providerUnsupported"},
                },
                "observableFollowUpFields": ["currentPrice", "volumeRatio"],
            },
        })

        response = validated_response_from_payload(context, {
            "action": "BUY",
            "summary": "가격 회복은 유효하지만 거래량 확인이 더 필요합니다.",
            "opinion": "소액 진입을 검토합니다.",
            "investmentView": "가격 회복은 투자 매력을 높이지만 거래 확인은 약합니다.",
            "executionDecision": "현재는 소액 진입만 검토합니다.",
            "evidence": ["가격 회복 관계가 성립했습니다."],
            "counterEvidence": ["거래량은 평균보다 낮습니다."],
            "followUpConditions": [
                {"field": "volumeRatio", "operator": ">=", "threshold": 1, "purpose": "strengthen", "label": "평균 거래량 회복"},
                {"field": "foreignNetVolume", "operator": ">", "threshold": 0, "purpose": "strengthen", "label": "외국인 순매수"},
            ],
        })

        self.assertIn("투자 매력", response.investment_view)
        self.assertIn("소액 진입", response.execution_decision)
        self.assertEqual(["volumeRatio"], [item["field"] for item in response.follow_up_conditions])
        self.assertEqual(["foreignNetVolume"], [item["field"] for item in response.unsupported_follow_ups])

    def test_v2_execution_contract_accepts_only_input_bound_supported_causal_path(self):
        context = entry_context()
        context["notificationAiDecisionContractVersion"] = "notification-ai-decision-contract-v2"
        context["ontologyRelationContext"]["activeRules"] = [{
            "ruleId": "graph.entry.confirmed.v1",
            "evidence": [{"evidenceId": "evidence:price-volume:NVDA"}],
        }]

        response = validated_response_from_payload(context, {
            "action": "BUY",
            "summary": "가격 회복과 거래 증가가 함께 확인됐습니다.",
            "opinion": "소액 진입을 검토합니다.",
            "evidence": ["가격과 거래가 함께 회복됐습니다."],
            "counterEvidence": ["정규장 지속성은 더 확인해야 합니다."],
            "nextChecks": ["다음 정규장 거래 지속성"],
            "decisionReadiness": "ready",
            "causalChain": [{
                "driver": "가격 회복과 거래 증가",
                "channel": "flow",
                "expectedEffect": "진입 조건의 신뢰도를 높임",
                "evidenceIds": ["evidence:price-volume:NVDA"],
                "status": "supported",
            }],
            "alternativeAction": {
                "action": "HOLD",
                "whyNotSelected": "현재 진입 지지 근거가 확인됐습니다.",
                "switchCondition": "거래 증가가 사라지면 관심 유지로 바꿉니다.",
            },
        })

        self.assertEqual("BUY", response.action)
        self.assertEqual("ready", response.decision_readiness)
        self.assertEqual(["evidence:price-volume:NVDA"], response.causal_chain[0]["evidenceIds"])
        self.assertEqual("HOLD", response.alternative_action["action"])

    def test_v2_execution_contract_rejects_a_hallucinated_causal_evidence_id(self):
        context = entry_context()
        context["notificationAiDecisionContractVersion"] = "notification-ai-decision-contract-v2"
        context["ontologyRelationContext"]["activeRules"] = [{
            "ruleId": "graph.entry.confirmed.v1",
            "evidence": [{"evidenceId": "evidence:price-volume:NVDA"}],
        }]

        response = validated_response_from_payload(context, {
            "action": "BUY",
            "summary": "진입을 검토합니다.",
            "opinion": "소액 진입을 검토합니다.",
            "evidence": ["가격 회복 조건이 확인됐습니다."],
            "counterEvidence": [],
            "nextChecks": ["다음 정규장 확인"],
            "decisionReadiness": "ready",
            "causalChain": [{
                "driver": "확인되지 않은 성장 가정",
                "channel": "revenue",
                "expectedEffect": "매출 증가",
                "evidenceIds": ["evidence:invented"],
                "status": "supported",
            }],
        })

        self.assertEqual("HOLD", response.action)
        self.assertEqual("conditional", response.decision_readiness)
        self.assertEqual([], response.causal_chain[0]["evidenceIds"])
        self.assertTrue(any("인과 경로" in item for item in response.validation_warnings))

    def test_v3_reference_only_hypothesis_cannot_unlock_entry_action(self):
        context = entry_context()
        context["notificationAiDecisionContractVersion"] = "notification-ai-decision-contract-v3"
        relation = context["ontologyRelationContext"]
        relation["actionEnvelope"].update({
            "selectedRuleId": "graph.entry.confirmed.v1",
            "dataReadiness": {
                "state": "ready",
                "usable": True,
                "eligibleRuleIds": ["graph.entry.confirmed.v1", "graph.entry.risk.v1"],
            },
        })
        relation["activeRules"] = [{
            "ruleId": "graph.entry.confirmed.v1",
            "evidence": [{"evidenceId": "evidence:price:NVDA"}],
        }]
        relation["investmentBrain"] = {
            "hypothesisSet": {
                "minimumComparisonCount": 3,
                "hypotheses": [
                    {
                        "hypothesisId": "hypothesis:support",
                        "familyId": "family:support",
                        "stance": "support",
                        "evidenceState": "supported",
                        "supportingRuleIds": ["graph.entry.confirmed.v1"],
                        "supportingEvidenceIds": ["evidence:price:NVDA"],
                    },
                    {
                        "hypothesisId": "hypothesis:risk",
                        "familyId": "family:risk",
                        "stance": "risk",
                        "evidenceState": "contested",
                        "supportingRuleIds": ["graph.entry.risk.v1"],
                        "supportingEvidenceIds": ["evidence:risk:NVDA"],
                    },
                    {
                        "hypothesisId": "hypothesis:valuation-reference",
                        "familyId": "family:valuation",
                        "stance": "context",
                        "evidenceState": "blocked",
                        "supportingRuleIds": ["graph.valuation.reference.v1"],
                        "supportingEvidenceIds": ["evidence:valuation:NVDA"],
                    },
                ],
            },
        }

        response = validated_response_from_payload(context, {
            "action": "BUY",
            "summary": "세 가설을 비교해 진입합니다.",
            "opinion": "소액 진입을 검토합니다.",
            "evidence": ["가격 회복이 확인됐습니다."],
            "counterEvidence": ["위험 경로도 남아 있습니다."],
            "decisionReadiness": "ready",
            "hypotheses": [
                {
                    "hypothesisId": "hypothesis:support",
                    "verdict": "supported",
                    "reasoning": "가격 근거가 연결됐습니다.",
                    "supportingEvidenceIds": ["evidence:price:NVDA"],
                },
                {
                    "hypothesisId": "hypothesis:risk",
                    "verdict": "weakened",
                    "reasoning": "위험은 있으나 주경로보다 약합니다.",
                    "supportingEvidenceIds": ["evidence:risk:NVDA"],
                },
            ],
            "selectedHypothesisId": "hypothesis:support",
            "causalChain": [{
                "driver": "가격 회복",
                "channel": "수급",
                "expectedEffect": "진입 가능성 증가",
                "evidenceIds": ["evidence:price:NVDA"],
                "status": "supported",
            }],
        })

        self.assertEqual("HOLD", response.action)
        self.assertEqual("conditional", response.decision_readiness)
        self.assertEqual(2, len(response.hypotheses))
        self.assertTrue(any("시스템 증거 계약" in item for item in response.validation_warnings))

    def test_ai_may_hold_with_explicit_disagreement_when_no_counter_was_found(self):
        response = validated_response_from_payload(
            entry_context(),
            {
                "action": "HOLD",
                "summary": "진입 조건은 생겼지만 판단 적격 근거 계열이 부족합니다.",
                "opinion": "관심을 유지합니다.",
                "evidence": ["가격 회복 관계가 성립했습니다."],
                "counterEvidence": [],
                "counterEvidenceStatus": "none-found",
                "disagreementReason": "독립 근거 계열과 가치평가 자료가 부족해 실행 판단을 낮췄습니다.",
                "nextChecks": ["독립 근거 계열과 가치평가 자료 확인"],
            },
        )

        self.assertEqual("HOLD", response.action)
        self.assertEqual("none-found", response.counter_evidence_status)
        self.assertEqual("review-only", response.decision_assurance["executionEligibility"])
        self.assertFalse(any("진입 후보를 유지했습니다" in item for item in response.validation_warnings))

    def test_previous_ai_decision_prevents_false_first_judgment(self):
        context = entry_context()
        context["previousInvestmentDecisionEpisode"] = {
            "episodeId": "decision-episode:previous",
            "accountId": "main",
            "symbol": "NVDA",
            "action": "HOLD",
            "decidedAt": "2026-07-26T01:00:00Z",
        }

        response = validated_response_from_payload(
            context,
            {
                "action": "HOLD",
                "summary": "진입 조건은 생겼지만 거래 확인이 더 필요합니다.",
                "opinion": "관심을 유지합니다.",
                "changeAnalysis": "이번 알림은 첫 행동 판단이므로 이전 행동과 직접 비교할 수 없습니다.",
                "evidence": ["가격 회복 조건이 확인됐습니다."],
                "counterEvidence": ["거래량 확인이 부족합니다."],
                "disagreementReason": "거래량 확인이 부족해 진입 후보를 바로 실행하지 않습니다.",
                "nextChecks": ["다음 정규장 거래량 확인"],
            },
        )

        self.assertNotIn("첫", response.change_analysis)
        self.assertIn("이전 AI 최종 판단과 같은 관심 유지", response.change_analysis)
        self.assertTrue(any("결정 이력 기준" in item for item in response.validation_warnings))

    def test_deferred_entry_message_separates_candidate_from_final_hold(self):
        context = entry_context()
        context["messageDeliveryLevel"] = "beginner"
        context["aiDecisionTransition"] = {
            "historyAvailable": True,
            "kind": "unchanged",
            "previousAction": "HOLD",
            "currentAction": "HOLD",
        }
        context["ontologyRelationContext"]["actionEnvelope"].update({
            "status": "ENTRY_DEFERRED",
            "statusLabel": "진입 조건 추가 확인",
            "preferredAction": "HOLD",
            "selectedDecisionEffect": "defer",
            "dataReadiness": {"state": "partial", "dataState": "partial", "usable": True},
        })
        context["notificationAiExecutionAudit"] = {
            "decisionBrief": {
                "accountPolicy": {
                    "portfolioLifecycle": {
                        "exposureSnapshot": {
                            "metrics": [
                                {
                                    "exposure_type": "position",
                                    "key": "MSTR",
                                    "ratio_pct": 56.7,
                                    "policy_limit_pct": 45.0,
                                },
                                {
                                    "exposure_type": "currency",
                                    "key": "non-KRW",
                                    "ratio_pct": 60.6,
                                    "policy_limit_pct": 25.0,
                                },
                                {
                                    "exposure_type": "cash",
                                    "key": "KRW",
                                    "ratio_pct": 0.1,
                                    "policy_limit_pct": 3.0,
                                },
                            ]
                        }
                    }
                }
            }
        }
        response = NotificationAIValidatedResponse(
            action="HOLD",
            action_label="관심 유지",
            precomputed_action="HOLD",
            validation_state="conditional",
            data_state="partial",
            review_level="check",
            summary="가격 회복은 확인됐지만 거래 확인이 부족해 관심을 유지합니다.",
            opinion="지금은 주문하지 않습니다.",
            change_analysis=(
                "이전 AI 최종 판단과 같은 관심 유지입니다. "
                "이전 AI 최종 판단과 같은 관심 유지입니다."
            ),
            evidence=["기간 회복과 우호적 추세 전이가 확인됐습니다."],
            counter_evidence=["거래량과 투자자 수급 확인이 부족합니다."],
            missing_data_impact=["웹 상세에서 확인할 체결·호가 자료가 없습니다."],
            next_checks=["거래 확인이 보강되면 다시 판단합니다."],
            source="test AI",
            raw_response='{"action":"HOLD"}',
        )

        message = execution_telegram_message(context, response)

        self.assertIn("[AI] 지금은 매수하지 않고 관심종목으로 유지합니다.", message)
        self.assertIn("<b>TypeDB 경쟁 추론</b>", message)
        self.assertIn("TypeDB 검토 설명 진입 후보·추가 확인 · AI 최종 행동 관심 유지", message)
        self.assertIn("<b>핵심 근거</b>", message)
        self.assertIn("<b>반대 근거</b>", message)
        self.assertLess(
            message.index("<b>핵심 근거</b>"),
            message.index("<b>반대 근거</b>"),
        )
        self.assertIn("TypeDB 검토 설명 진입 후보·추가 확인 · AI 최종 행동 관심 유지", message)
        self.assertNotIn("TypeDB 검토 설명 관심 유지 · AI 최종 행동 관심 유지", message)
        self.assertNotIn("<b>포트폴리오 영향</b>", message)
        self.assertNotIn("현금 비중 0.1%", message)
        self.assertNotIn("전체 외화 비중 60.6%", message)
        self.assertNotIn("기존 종목 집중 초과", message)
        self.assertIn("포트폴리오 리밸런싱: 이번 종목 시세 판단에서는", message)
        self.assertNotIn(
            "이전 AI 최종 판단과 같은 관심 유지입니다. 이전 AI 최종 판단과 같은 관심 유지입니다.",
            message,
        )
        self.assertNotIn("<b>자료 상태</b>", message)

    def test_local_response_explains_entry_envelope_without_engine_terms(self):
        response = local_validated_ai_response(entry_context())

        self.assertEqual("BUY", response.action)
        self.assertIn("소액 진입", response.summary)
        self.assertNotIn("TypeDB", response.summary)

    def test_beginner_message_uses_compact_action_flow_and_only_decision_changing_news(self):
        context = entry_context()
        context.update({
            "messageDeliveryLevel": "beginner",
            "title": "NVIDIA: 소액 진입 검토",
            "rawLines": [
                "현재가: $208.16",
                "추세: 20일 평균보다 2% 높음",
            ],
            "newsImpact": {
                "decisionChanging": True,
                "decisionInlineEligible": True,
                "decisionDriverConfirmed": True,
                "source": "Reuters",
                "headline": "NVIDIA won a new data-center supply contract.",
                "url": "https://example.test/nvidia-contract",
            },
            "previousInvestmentDecisionEpisode": {
                "episodeId": "decision-episode:previous",
                "action": "HOLD",
                "decidedAt": "2026-07-26T01:00:00Z",
            },
            "aiDecisionTransition": {
                "historyAvailable": True,
                "kind": "action-changed",
                "previousAction": "HOLD",
                "currentAction": "BUY",
            },
        })
        context["ontologyRelationContext"]["facts"]["temporalWindows"] = [
            {"windowKey": "15M", "priceChangePct": 0.4},
            {"windowKey": "1H", "priceChangePct": 0.8},
            {"windowKey": "SESSION", "priceChangePct": 1.1},
            {"windowKey": "3D", "priceChangePct": 1.9},
            {"windowKey": "5D", "priceChangePct": 2.8},
            {"windowKey": "20D", "priceChangePct": -7.4},
            {"windowKey": "60D", "priceChangePct": -12.0},
        ]
        response = NotificationAIValidatedResponse(
            action="BUY",
            action_label="소액 진입 검토",
            validation_state="conditional",
            validation_label="조건부 사용",
            data_state="sufficient",
            data_state_label="판단에 필요한 자료 있음",
            review_level="check",
            review_label="조건 확인",
            summary="진입 지지 관계가 확인됐고 거시 부담은 진입 시점과 규모를 제한하는 조건입니다.",
            evidence=["가격 회복 관계와 진입 지지 관계가 함께 확인됐습니다."],
            counter_evidence=["거시 부담이 남아 있어 한 번에 크게 진입하지 않습니다."],
            change_analysis="관심 유지에서 소액 진입 검토로 바뀌었고 가격 회복 근거가 새로 확인됐습니다.",
            invalidation_condition="진입 지지 관계가 사라지거나 직접 반대 뉴스가 확인되면 다시 봅니다.",
            next_checks=["정규장 거래량이 유지되는지 확인"],
            reference_date="2026-07-27 10:00 KST",
            source="test AI",
            raw_response='{"action":"BUY"}',
        )

        message = execution_telegram_message(context, response)

        for heading in [
            "지금 행동", "이번 변화", "현재 흐름", "시간축 분석",
            "핵심 근거", "반대 근거", "TypeDB 경쟁 추론", "회사 가치",
            "주요 사건·일정", "다음 행동", "판단 변경 조건",
            "판단에서 제외한 정보", "뉴스 영향", "판단 이력",
        ]:
            self.assertIn(heading, message)
        self.assertIn("장중 +1.1% · 5일 +2.8% · 20일 -7.4%", message)
        self.assertNotIn("15분 +0.4%", message)
        self.assertNotIn("60일 -12.0%", message)
        self.assertIn("관심 유지", message)
        self.assertIn("현재 소액 진입 검토", message)
        self.assertNotIn("decision-episode:previous", message)
        self.assertNotIn("<b>추론 추적</b>", message)
        self.assertNotIn("<b>원문·출처</b>", message)
        self.assertNotIn("<b>출처</b>", message)
        self.assertIn('<a href="https://example.test/nvidia-contract">', message)
        self.assertNotIn("<b>자료 상태</b>", message)
        self.assertNotIn("<b>포트폴리오 영향</b>", message)
        self.assertIn("[AI]", message)
        self.assertNotIn("API 조회 정보", message)
        self.assertNotIn("뉴스·공시 요약", message)

    def test_typedb_fallback_is_labeled_as_typedb_not_ai(self):
        context = entry_context()
        context["messageDeliveryLevel"] = "beginner"

        message = execution_telegram_message(
            context,
            local_validated_ai_response(context, source="TypeDB inference fallback"),
        )

        self.assertIn("[TypeDB 추론]", message)
        self.assertNotIn("[AI]", message)

    def test_compact_message_explains_macro_constraint_with_observed_rates(self):
        context = entry_context()
        context.update({
            "messageDeliveryLevel": "beginner",
            "displayTarget": "Apple / AAPL",
            "rawLines": [
                "현재가: $334.15",
                "추세: 5일선 $328.5보다 1.7% 높음, 20일선 $316.91보다 5.4% 높음, 60일선 $303.57보다 10.1% 높음",
            ],
        })
        context["ontologyRelationContext"]["facts"].update({
            "ma5Distance": 1.7,
            "ma20Distance": 5.4,
            "ma60Distance": 10.1,
            "macroDgs10": 4.71,
            "macroDgs2": 4.37,
            "instrumentSensitivities": {"rate": "medium"},
        })
        context["ontologyRelationContext"]["actionEnvelope"]["constraintRuleIds"] = [
            "graph.macro.rate.high_regime_entry.risk.v1",
        ]
        context["ontologyRelationContext"]["activeRules"] = [
            {"ruleId": "graph.price.reclaim.thesis_support.v1", "actionGroup": "entry"},
            {
                "ruleId": "graph.macro.rate.high_regime_entry.risk.v1",
                "actionGroup": "macroRegime",
                "evidenceRole": "risk",
                "decisionEffect": "constrain",
                "ruleScopeFamilies": ["macro-rates"],
            },
        ]
        context["ontologyRelationContext"]["executionPlan"]["decisionDrivers"] = [
            {
                "category": "trend",
                "summary": "현재가는 5일선 대비 +1.7%, 20일선 대비 +5.4%, 60일선 대비 +10.1%입니다.",
            },
        ]
        response = NotificationAIValidatedResponse(
            action="BUY",
            action_label="소액 진입 검토",
            data_state_label="판단에 필요한 자료 있음",
            summary="진입을 뒷받침하는 근거가 확인돼 소액 진입을 검토할 수 있습니다.",
            invalidation_condition="거시 부담 관계가 사라지고 진입 지지 관계가 새로 성립하면 소액 진입 여부를 다시 검토",
            next_checks=[
                "가격 회복, 거래 확인, 반대 이벤트 해소를 확인 / 금리, 환율, 지수, 크립토와 종목 반응을 함께 확인",
            ],
            reference_date="2026-07-27 22:03 KST",
        )

        message = execution_telegram_message(context, response)

        self.assertIn("미국 10년 금리 4.71%", message)
        self.assertIn("미국 2년 금리 4.37%", message)
        self.assertIn("금리 부담이 완화되고", message)
        self.assertIn("Apple 가격이 5일선·20일선·60일선 위를 유지하는지", message)
        self.assertIn("진입 제한을 완화할 조건", message)
        for internal in ["거시 부담 관계", "진입 지지 관계", "원시"]:
            self.assertNotIn(internal, message)

    def test_observed_rates_without_materialized_macro_rule_are_not_a_decision_reason(self):
        context = entry_context()
        context.update({
            "messageDeliveryLevel": "beginner",
            "displayTarget": "NVIDIA / NVDA",
            "rawLines": ["현재가: $207.51", "추세: 20일선보다 1.5% 높음"],
        })
        context["ontologyRelationContext"]["facts"].update({
            "macroDgs10": 4.75,
            "macroDgs2": 4.28,
            "instrumentSensitivities": {"rate": "medium"},
        })
        context["ontologyRelationContext"]["actionEnvelope"]["constraintRuleIds"] = []
        context["ontologyRelationContext"]["activeRules"] = [
            {"ruleId": "graph.price.reclaim.thesis_support.v1", "label": "가격 회복 시도"},
        ]
        response = NotificationAIValidatedResponse(
            action="HOLD",
            action_label="관심 유지",
            validation_state="conditional",
            validation_label="조건부 사용",
            data_state="partial",
            data_state_label="일부 자료만 있음",
            summary="가격 회복이 이어지는지 확인합니다.",
            evidence=["가격 회복 시도가 확인됐습니다."],
            reference_date="2026-08-13 16:00 KST",
        )

        message = execution_telegram_message(context, response)
        reason_section = message.split("<b>핵심 근거</b>", 1)[0]

        self.assertNotIn("미국 10년 금리", reason_section)
        self.assertIn("금리·환율: TypeDB 행동 규칙과 직접 연결되지 않아 이번 판단 변경 이유에서 제외했습니다.", message)

    def test_existing_validated_response_rebuilds_stale_presentation_cache(self):
        context = entry_context()
        context.update({
            "messageDeliveryLevel": "beginner",
            "telegramMessage": "old rendered message entry_observing",
            "decisionTransition": {
                "kind": "envelope-changed",
                "currentStatus": "entry_observing",
                "summary": "이전 조건에서 entry_observing으로 바뀌었습니다.",
                "material": True,
            },
            "notificationAiValidatedResponse": NotificationAIValidatedResponse(
                action="HOLD",
                action_label="관심 유지",
                data_state_label="일부 자료만 있음",
                summary="관심 유지가 맞습니다.",
                evidence=["거시 부담이 남아 있습니다. supportingEvidenceIds: relation-evidence:abc"],
                missing_data_impact=[
                    "연구 사이클에서 changedEvidenceCount가 0이고 reasoningRefreshed도 false라, "
                    "기존 뉴스·조사 내용을 새 판단 근거처럼 강화할 수 없습니다.",
                ],
            ).to_dict(),
        })
        job = NotificationJob.create(
            "old rendered message",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )

        NotificationAIValidatedGateEnricher(settings={
            "notificationAiGateEnabled": "1",
            "notificationAiGateMessageTypes": "investmentInsight",
        })(job)

        message = job.context["telegramMessage"]
        self.assertIn("[관심 유지] 현재 행동은 관심 유지입니다. 매수 판단으로 바뀐 것은 아닙니다.", message)
        self.assertNotIn("기존 뉴스·조사 내용을 새 판단 근거처럼 강화", message)
        for internal in ["old rendered message", "entry_observing", "supportingEvidenceIds", "relation-evidence", "changedEvidenceCount", "reasoningRefreshed"]:
            self.assertNotIn(internal, message)

    def test_incomplete_hypothesis_comparison_is_labeled_as_ai_safety_hold(self):
        context = entry_context()
        context["messageDeliveryLevel"] = "beginner"
        response = NotificationAIValidatedResponse(
            action="HOLD",
            action_label="관심 유지",
            summary="경쟁 가설 비교가 끝나지 않아 실행 판단을 보류합니다.",
            current_action_plan="새 주문 없이 다음 근거를 확인합니다.",
            hypotheses=[{"hypothesisId": "hypothesis:safety"}],
            selected_hypothesis_id="hypothesis:safety",
            hypothesis_comparison_state="fallback",
            hypothesis_selection_source="safety-fallback-fallback",
            source="Codex AI (GPT-5.6 Sol · max)",
            raw_response='{"action":"HOLD"}',
        )

        message = execution_telegram_message(context, response)

        self.assertIn("[AI 안전 보류]", message)
        self.assertNotIn("[AI] 관심 유지", message)

    def test_compact_message_hides_stale_or_unlinked_news_impact(self):
        context = entry_context()
        context.update({
            "messageDeliveryLevel": "beginner",
            "newsImpact": {
                "decisionChanging": True,
                "source": "Yahoo Finance",
                "headline": "A partner company mentions NVIDIA.",
            },
        })
        response = NotificationAIValidatedResponse(
            action="BUY",
            action_label="소액 진입 검토",
            validation_state="ready",
            validation_label="검증 완료",
            data_state="sufficient",
            data_state_label="판단에 필요한 자료 있음",
            review_level="check",
            review_label="조건 확인",
            summary="진입을 뒷받침하는 근거가 확인됐습니다.",
            reference_date="2026-07-27 10:00 KST",
        )

        self.assertNotIn("뉴스 영향", execution_telegram_message(context, response))

    def test_compact_message_includes_independent_ontology_assessments(self):
        context = entry_context()
        context["messageDeliveryLevel"] = "beginner"
        context["ontologyRelationContext"]["assessmentBundle"] = {
            "evidenceQuality": {"status": "supported"},
            "investmentOpinion": {"status": "supported", "candidateAction": "BUY"},
            "portfolioFit": {"status": "not-evaluated"},
            "executionReadiness": {"status": "supported"},
            "recommendedPlan": {"status": "ready", "investmentAction": "BUY"},
        }
        response = NotificationAIValidatedResponse(
            action="BUY",
            action_label="소액 진입 검토",
            summary="진입 근거가 확인됐습니다.",
        )

        message = execution_telegram_message(context, response)

        self.assertIn("온톨로지 판단 영역", message)
        self.assertIn("TypeDB 검토 설명: 소액 진입 검토", message)
        self.assertIn("최종 조합: 검토 설명과 실행 조건이 함께 성립", message)


if __name__ == "__main__":
    unittest.main()
