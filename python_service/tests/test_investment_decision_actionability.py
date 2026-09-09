import unittest

from digital_twin.domain.decision_evidence_contract import hypothesis_decision_eligibility
from digital_twin.domain.investment_decision_actionability import (
    investment_decision_actionability,
    is_concrete_observable_condition,
    persisted_decision_authorization,
)
from digital_twin.domain.investment_decision_history import (
    compact_decision_episode_memory,
)
from digital_twin.domain.investment_reasoning.synthesis import (
    decision_synthesis_from_relation_context,
)
from digital_twin.domain.notification_ai_gate_validation import (
    validated_response_from_payload,
)
from digital_twin.domain.notification_ai_gate_contracts import (
    NotificationAIValidatedResponse,
)
from digital_twin.application.notification_ai_gate_message import (
    execution_telegram_message,
    notification_topline_change_summary,
    prepend_execution_start_badge,
)
from digital_twin.application.portfolio_lifecycle_service import (
    DecisionActionPlanningService,
)


def hypothesis(qualification_status="active"):
    return {
        "hypothesisId": "hypothesis:price-flow-entry",
        "templateId": "hypothesis-template:price-flow-entry",
        "familyId": "price-flow-entry",
        "claim": "가격 회복과 순매수가 이어지면 단기 수요가 강화된다.",
        "stance": "support",
        "candidateAction": "BUY",
        "evidenceState": "supported",
        "supportingRuleIds": ["graph.entry.confirmed.v1"],
        "supportingEvidenceIds": ["evidence:price", "evidence:flow"],
        "counterEvidenceIds": ["evidence:volume"],
        "causalPathIds": ["trace:price-flow-entry"],
        "invalidationConditions": ["20일선 이탈 또는 외국인 순매도 전환"],
        "verificationStatus": "verified-current-generation",
        "approvalStatus": "approved-active",
        "status": "active",
        "scopeState": "market-shared",
        "marketHypothesisId": "market-hypothesis:price-flow-entry",
        "inferenceGenerationId": "generation:1",
        "knowledgeBasis": {
            "requiresHypothesis": True,
            "decisionEligibility": "investment-evidence",
        },
        "claimContract": {
            "claimContractId": "claim:price-flow-entry",
            "claimType": "market-hypothesis",
            "ruleId": "graph.entry.confirmed.v1",
        },
        "qualification": {"status": qualification_status},
    }


def context(qualification_status="active"):
    candidate = hypothesis(qualification_status)
    hypothesis_set = {
        "hypotheses": [candidate],
        "comparisonRequired": False,
        "minimumComparisonCount": 1,
        "selectedHypothesisId": candidate["hypothesisId"],
    }
    return {
        "messageType": "investmentInsight",
        "displayTarget": "NAVER / 035420",
        "notificationAiDecisionContractVersion": "notification-ai-decision-contract-v13",
        "_notificationAiPreparedDecisionCore": {"hypothesisSet": hypothesis_set},
        "ontologyRelationContext": {
            "source": "typedbInferenceBox",
            "graphStoreUsed": True,
            "fallbackUsed": False,
            "targetRole": "watchlist",
            "actionPolicy": "ENTRY_ONLY",
            "facts": {"symbol": "035420", "isWatchlist": True},
            "hypothesisSet": hypothesis_set,
            "decision": {
                "basis": "typedbInferenceBox",
                "candidateAction": "BUY",
                "allowedActions": ["BUY", "HOLD", "AVOID"],
                "blockedActions": ["ADD", "TRIM", "SELL"],
            },
            "actionEnvelope": {
                "status": "ENTRY_ELIGIBLE",
                "targetRole": "watchlist",
                "selectedRuleId": "graph.entry.confirmed.v1",
                "investmentViewAction": "BUY",
                "preferredAction": "BUY",
                "allowedActions": ["BUY", "HOLD", "AVOID"],
                "aiAllowedActions": ["BUY", "HOLD", "AVOID"],
                "blockedActions": ["ADD", "TRIM", "SELL"],
                "dataReadiness": {
                    "state": "ready",
                    "usable": True,
                    "eligibleRuleIds": ["graph.entry.confirmed.v1"],
                },
            },
        },
    }


def relation_context(qualification_status="active"):
    candidate = hypothesis(qualification_status)
    return {
        "accountId": "account:main",
        "subject": {"symbol": "035420", "name": "NAVER"},
        "decision": {
            "basis": "typedbInferenceBox",
            "candidateAction": "BUY",
            "allowedActions": ["BUY", "HOLD", "AVOID"],
            "blockedActions": ["ADD", "TRIM", "SELL"],
        },
        "actionEnvelope": {
            "investmentViewAction": "BUY",
            "preferredAction": "BUY",
            "selectedRuleId": "graph.entry.confirmed.v1",
            "selectedDecisionEffect": "support",
            "investmentJudgementAvailable": True,
            "allowedActions": ["BUY", "HOLD", "AVOID"],
            "blockedActions": ["ADD", "TRIM", "SELL"],
        },
        "hypothesisSet": {"hypotheses": [candidate]},
        "sourceAboxSnapshotId": "abox:1",
        "inferenceGenerationId": "generation:1",
        "generationAligned": True,
        "graphStoreInference": {
            "sourceAboxSnapshotId": "abox:1",
            "inferenceGenerationId": "generation:1",
            "relations": [{
                "ruleId": "graph.entry.confirmed.v1",
                "candidateAction": "BUY",
            }],
            "traces": [{
                "traceId": "trace:price-flow-entry",
                "ruleId": "graph.entry.confirmed.v1",
            }],
        },
    }
def complete_response(qualification_status="active"):
    candidate = hypothesis(qualification_status)
    return {
        "action": "BUY",
        "selectedHypothesisId": candidate["hypothesisId"],
        "currentActionPlan": "소액 분할매수만 검토하고 한 번에 큰 주문은 하지 않습니다.",
        "changeAnalysis": "현재가가 20일선 위로 회복했고 외국인 순매수가 확인됐습니다.",
        "nextActionPlan": "다음 정규장에서 거래량이 평균 1배 이상 유지되면 진입 판단을 다시 확인합니다.",
        "invalidationCondition": "현재가가 20일선 아래로 내려가거나 외국인이 순매도로 바뀌면 매수 판단을 취소합니다.",
        "decisionReadiness": "ready",
        "decisionAssurance": {"executionEligibility": "eligible"},
        "causalChain": [{
            "driver": "가격 회복과 외국인 순매수",
            "channel": "flow",
            "expectedEffect": "단기 매수 수요 강화",
            "evidenceIds": ["evidence:price", "evidence:flow"],
            "status": "supported",
        }],
        "evidence": ["현재가가 20일선 위입니다.", "외국인이 순매수했습니다."],
    }


def ai_payload(qualification_status="active"):
    candidate = hypothesis(qualification_status)
    return {
        **complete_response(qualification_status),
        "summary": "가격 회복과 외국인 순매수가 함께 확인돼 소액 진입을 검토합니다.",
        "opinion": "한 번에 크게 사지 않고 소액 분할매수만 검토합니다.",
        "counterEvidence": ["거래량은 아직 20일 평균을 넘지 않았습니다."],
        "nextChecks": ["다음 정규장 거래량과 외국인 순매수 유지 여부"],
        "hypotheses": [{
            "hypothesisId": candidate["hypothesisId"],
            "templateId": candidate["templateId"],
            "claim": candidate["claim"],
            "stance": "support",
            "evidenceReviewStatus": "all-input-evidence-reviewed",
            "verdict": "supported",
            "reasoning": "가격과 수급 근거가 같은 방향입니다.",
        }],
    }


class InvestmentDecisionActionabilityTests(unittest.TestCase):
    def test_hypothesis_qualification_separates_comparison_from_execution(self):
        shadow = hypothesis_decision_eligibility(hypothesis("shadow"))
        limited = hypothesis_decision_eligibility(hypothesis("limited-active"))
        active = hypothesis_decision_eligibility(hypothesis("active"))

        self.assertTrue(shadow["eligible"])
        self.assertEqual("research-only", shadow["decisionUse"])
        self.assertFalse(shadow["executionEligible"])
        self.assertEqual("conditional", limited["decisionUse"])
        self.assertFalse(limited["executionEligible"])
        self.assertEqual("execution", active["decisionUse"])
        self.assertTrue(active["executionEligible"])

    def test_synthesis_carries_execution_authority_separately_from_comparison(self):
        shadow = decision_synthesis_from_relation_context(
            "account:main", relation_context("shadow")
        )
        active = decision_synthesis_from_relation_context(
            "account:main", relation_context("active")
        )
        research_relation = relation_context("shadow")
        research_relation["dataState"] = "sufficient"
        research_relation["actionEnvelope"].update({
            "judgementBlocked": True,
            "selectedDecisionEffect": "constrain",
        })
        research_hypothesis = research_relation["hypothesisSet"]["hypotheses"][0]
        research_hypothesis["knowledgeBasis"]["decisionEligibility"] = "reference-only"
        research = decision_synthesis_from_relation_context(
            "account:main", research_relation
        )

        self.assertEqual(("hypothesis:price-flow-entry",), shadow.eligible_hypothesis_ids)
        self.assertEqual((), shadow.execution_eligible_hypothesis_ids)
        self.assertFalse(shadow.execution_qualified)
        self.assertEqual("NO_ACTION", shadow.execution_action)
        self.assertFalse(shadow.alternatives[0].execution_eligible)
        self.assertEqual(
            ("hypothesis:price-flow-entry",),
            active.execution_eligible_hypothesis_ids,
        )
        self.assertTrue(active.execution_qualified)
        self.assertEqual("BUY", active.execution_action)
        self.assertTrue(active.alternatives[0].execution_eligible)
        self.assertTrue(research.judgement_blocked)
        self.assertEqual("HYPOTHESIS_RESEARCH_ONLY", research.disposition_code)
        self.assertEqual("RESEARCH_ONLY", research.ai_state)
        self.assertEqual("modify", research.action_authority)
        self.assertEqual((), research.execution_eligible_hypothesis_ids)
        self.assertEqual(
            ("hypothesis:price-flow-entry",),
            research.reference_hypothesis_ids,
        )
        self.assertEqual("REFERENCE_ONLY", research.hypothesis_state)

    def test_blocked_action_does_not_create_a_false_hypothesis_comparison(self):
        hold = hypothesis("shadow")
        hold.update({
            "hypothesisId": "hypothesis:hold",
            "candidateAction": "HOLD",
            "supportingRuleIds": ["graph.hold.v1"],
            "claimContract": {
                **hold["claimContract"],
                "ruleId": "graph.hold.v1",
            },
        })
        add = hypothesis("shadow")
        add.update({
            "hypothesisId": "hypothesis:add",
            "candidateAction": "ADD",
            "supportingRuleIds": ["graph.add.v1"],
            "claimContract": {
                **add["claimContract"],
                "ruleId": "graph.add.v1",
            },
        })
        synthesis = decision_synthesis_from_relation_context("account:main", {
            "accountId": "account:main",
            "subject": {"symbol": "000660", "name": "SK하이닉스"},
            "sourceAboxSnapshotId": "abox:blocked-action",
            "inferenceGenerationId": "generation:blocked-action",
            "generationAligned": True,
            "assessmentBundle": {
                "investmentOpinion": {
                    "candidateAction": "HOLD",
                    "selectedRuleId": "graph.hold.v1",
                    "decisionEffect": "support",
                    "actionConflict": True,
                    "candidateActions": ["HOLD", "ADD"],
                },
            },
            "actionEnvelope": {
                "investmentViewAction": "HOLD",
                "executionAction": "HOLD",
                "selectedRuleId": "graph.hold.v1",
                "selectedDecisionEffect": "support",
                "investmentJudgementAvailable": True,
                "allowedActions": ["HOLD"],
                "blockedActions": ["ADD"],
            },
            "hypothesisSet": {"hypotheses": [hold, add]},
            "graphStoreInference": {
                "sourceAboxSnapshotId": "abox:blocked-action",
                "inferenceGenerationId": "generation:blocked-action",
                "relations": [
                    {"ruleId": "graph.hold.v1", "candidateAction": "HOLD"},
                    {"ruleId": "graph.add.v1", "candidateAction": "ADD"},
                ],
                "traces": [
                    {"traceId": "trace:hold", "ruleId": "graph.hold.v1"},
                    {"traceId": "trace:add", "ruleId": "graph.add.v1"},
                ],
            },
        })

        alternatives = {item.action: item for item in synthesis.alternatives}
        self.assertEqual("HYPOTHESIS_QUALIFICATION_PENDING", synthesis.disposition_code)
        self.assertEqual("hypothesis-qualification-required", synthesis.execution_disposition)
        self.assertEqual("RESEARCH_ONLY", synthesis.ai_state)
        self.assertNotEqual("COMPARISON_REQUIRED", synthesis.action_state)
        self.assertTrue(alternatives["HOLD"].decision_eligible)
        self.assertFalse(alternatives["ADD"].decision_eligible)
        self.assertFalse(alternatives["ADD"].execution_eligible)
        self.assertEqual((), synthesis.execution_eligible_hypothesis_ids)

    def test_active_hypothesis_with_complete_contract_is_actionable(self):
        assessment = investment_decision_actionability(context(), complete_response())

        self.assertEqual("actionable", assessment["status"])
        self.assertTrue(assessment["publishable"])
        self.assertFalse(assessment["gaps"])
        self.assertTrue(all(
            stage["status"] == "passed" for stage in assessment["stages"].values()
        ))

    def test_shadow_hypothesis_cannot_originate_buy(self):
        assessment = investment_decision_actionability(
            context("shadow"), complete_response("shadow")
        )

        self.assertEqual("review-only", assessment["status"])
        self.assertIn("hypothesis-execution-qualification", assessment["gaps"])
        self.assertEqual("failed", assessment["stages"]["hypothesis"]["status"])

    def test_renderer_never_presents_failed_buy_as_a_customer_action(self):
        values = context("shadow")
        values["investmentSubjectDecisionCaseId"] = "subject-case:shadow"
        response_payload = complete_response("shadow")
        response_payload["followUpConditions"] = [{
            "field": "currentPrice",
            "operator": ">=",
            "threshold": 219090,
            "purpose": "switch",
            "onSatisfied": "매수 재비교",
        }]
        values["notificationAiValidatedResponse"] = response_payload
        values["decisionTransition"] = {
            "kind": "action-changed",
            "previousAction": "HOLD",
            "currentAction": "BUY",
        }
        response = NotificationAIValidatedResponse.from_dict(
            response_payload
        )

        stale_topline = notification_topline_change_summary(values)
        self.assertTrue(stale_topline)

        message = prepend_execution_start_badge(
            execution_telegram_message(values, response),
            values,
        )

        self.assertIn("판단 보류", message)
        self.assertIn("지금은 주문하지 않습니다", message)
        self.assertNotIn("소액 진입 검토</b>", message)
        self.assertNotIn(stale_topline, message)
        self.assertNotIn("무엇이 바뀌었나", message)
        self.assertIn("219,090원 이상", message)

        values = context("shadow")
        values.update({
            "displayTarget": "SK하이닉스 / 000660",
            "investmentSubjectDecisionCaseId": "subject-case:research",
            "notificationAiReviewMode": "context-narrative",
            "decisionPublication": {"outcomeKind": "REVIEW_ONLY"},
        })
        response = NotificationAIValidatedResponse.from_dict({
            "action": "NO_ACTION",
            "summary": "단기 가격 회복이 현재 상황을 가장 잘 설명하지만 주문 근거는 아닙니다.",
            "currentActionPlan": "현재 보유 수량은 바꾸지 않습니다.",
            "nextActionPlan": "다음 가격과 외국인 수급에서 회복 지속 여부를 확인합니다.",
            "invalidationCondition": "현재가가 20일선 아래로 내려가거나 외국인이 순매도로 바뀌면 연구 선두에서 제외합니다.",
            "epistemicSummary": "회복 지속성과 펀더멘털 경로는 아직 확인되지 않았습니다.",
            "researchLeadHypothesisId": "hypothesis:recovery",
            "hypothesisComparisonState": "research-reviewed",
            "insightAssessment": {
                "publishable": True,
                "direction": "positive",
                "directionLabel": "상승 요인 우세",
                "horizonLabel": "단기",
                "convictionLabel": "근거 강도 보통",
                "dominantThesis": "단기 가격 회복과 외국인 수급 개선이 상방 관점을 지지합니다.",
                "causalMechanism": "가격 회복이 외국인 순매수와 이어지면 단기 추세가 강화됩니다.",
                "investmentImplication": "보유자는 회복 지속 여부를 기준으로 비중 확대 시점을 구분해야 합니다.",
                "catalysts": ["20일선 위 가격과 외국인 순매수가 다음 거래일에도 유지되는지 확인합니다."],
                "risks": ["외국인 순매도 전환은 현재 상방 관점을 약화합니다."],
            },
            "hypotheses": [
                {
                    "hypothesisId": "hypothesis:fundamental",
                    "claim": "SK하이닉스에서 TypeDB가 확인한 '매출·현금흐름 개선 + 가격 회복 → 펀더멘털 확인' 인과 경로가 현재 상황을 설명한다.",
                    "verdict": "unresolved",
                    "reasoning": "실제 매출과 현금흐름 값이 없어 아직 확인할 수 없습니다.",
                },
                {
                    "hypothesisId": "hypothesis:recovery",
                    "claim": "SK하이닉스에서 TypeDB가 확인한 '단기 회복 + 수급 확인 → 추가매수 후보' 인과 경로가 현재 상황을 설명한다.",
                    "verdict": "unresolved",
                    "reasoning": "조건부 모델 신호의 연결은 확인됐습니다. 사후 5건의 방향 적중률은 40%여서 실행 근거로는 부족합니다.",
                },
                {
                    "hypothesisId": "hypothesis:event",
                    "claim": "SK하이닉스에서 TypeDB가 확인한 '위험 이벤트 + 가격 방어 → 악재 흡수' 인과 경로가 현재 상황을 설명한다.",
                    "verdict": "weakened",
                    "reasoning": "사후 성과가 약해 설명력이 낮아졌습니다.",
                },
            ],
        })

        message = execution_telegram_message(values, response)

        self.assertIn("🧠 AI 투자 인사이트 · SK하이닉스 · 상승 요인 우세", message)
        self.assertIn("상승 요인 우세", message)
        self.assertIn("현재 판단", message)
        self.assertIn("단기 가격 회복", message)
        self.assertIn("근거 연결", message)
        self.assertNotIn("투자 의미", message)
        self.assertNotIn("성립값이 부족", message)
        self.assertIn("현재 보유 수량은 바꾸지 않습니다", message)
        self.assertIn("판단이 바뀌는 조건", message)
        self.assertIn("20일선 아래", message)
        self.assertNotIn("소액 진입", message)
        self.assertNotIn("사후 5건의 방향 적중률", message)
        self.assertNotIn("재판단 기준 없음", message)
        self.assertNotIn("현재 신호는 확인했지만 실행 판단", message)
        self.assertNotIn("모든 후보 근거를 비교했으며", message)

        legacy_response = NotificationAIValidatedResponse.from_dict({
            "action": "NO_ACTION",
            "summary": "가격 변화로 관계 신호가 유지에서 약화로 전환됐다.",
            "currentActionPlan": "현재 보유 수량에는 새 주문을 내지 않는다.",
            "nextChecks": ["다음 장중 가격과 외국인 순매수의 동행 여부"],
            "hypothesisComparisonState": "research-reviewed",
            "researchLeadHypothesisId": "hypothesis:recovery",
            "hypotheses": [{
                "hypothesisId": "hypothesis:recovery",
                "claim": "가격 회복 관계를 다시 확인합니다.",
                "verdict": "weakened",
            }],
        })
        legacy_message = execution_telegram_message(values, legacy_response)
        self.assertIn("🧠 AI 관계 해석 · SK하이닉스", legacy_message)
        self.assertIn("약화로 전환됐습니다.", legacy_message)
        self.assertIn("동행 여부를 확인합니다.", legacy_message)
        self.assertNotIn("· 투자 관점", legacy_message)

    def test_unqualified_executable_episode_cannot_become_continuity_baseline(self):
        old = {
            "episodeId": "episode:old-buy",
            "action": "BUY",
            "validationState": "conditional",
            "decisionReadiness": "conditional",
            "source": "v2-reasoning-case",
        }
        forged_marker = {
            **old,
            "episodeId": "episode:forged-marker",
            "validationState": "ready",
            "decisionReadiness": "ready",
            "decisionAssurance": {"executionEligibility": "eligible"},
            "decisionActionability": {
                "status": "actionable",
                "publishable": True,
                "executionEligible": True,
            },
        }
        response = complete_response()
        hypothesis_set = {"hypotheses": [hypothesis()]}
        actionability = investment_decision_actionability(
            {"hypothesisSet": hypothesis_set},
            response,
        )
        qualified = {
            **old,
            **response,
            "episodeId": "episode:qualified-buy",
            "hypothesisSet": hypothesis_set,
            "validationState": "ready",
            "decisionActionability": actionability,
        }

        self.assertEqual({}, compact_decision_episode_memory(old))
        self.assertEqual({}, compact_decision_episode_memory(forged_marker))
        self.assertEqual(
            "BUY",
            compact_decision_episode_memory(qualified)["action"],
        )
        self.assertEqual(
            "HOLD",
            compact_decision_episode_memory({**old, "action": "HOLD"})["action"],
        )

    def test_persisted_executable_opinion_is_revalidated_before_display(self):
        response = complete_response()
        hypothesis_set = {"hypotheses": [hypothesis()]}
        actionability = investment_decision_actionability(
            {"hypothesisSet": hypothesis_set},
            response,
        )
        qualified = {
            **response,
            "hypothesisSet": hypothesis_set,
            "decisionActionability": actionability,
        }

        blocked = persisted_decision_authorization({
            **qualified,
            "currentActionPlan": "",
        })
        allowed = persisted_decision_authorization(qualified)

        self.assertFalse(blocked["authorized"])
        self.assertEqual("NO_ACTION", blocked["effectiveAction"])
        self.assertIn("current-action-plan", blocked["gaps"])
        self.assertTrue(allowed["authorized"])
        self.assertEqual("BUY", allowed["effectiveAction"])

        class IncompleteEpisode:
            action = "BUY"

            @staticmethod
            def to_dict():
                return {**qualified, "currentActionPlan": ""}

        with self.assertRaisesRegex(ValueError, "actionability contract"):
            DecisionActionPlanningService(repository=None).prepare(
                IncompleteEpisode(),
                {},
            )

    def test_vague_or_action_inconsistent_plan_is_not_publishable(self):
        response = complete_response()
        response["currentActionPlan"] = "현재 판단을 계속 관찰합니다."
        response["nextActionPlan"] = (
            "다음 관찰에서도 가격 회복과 수급 연결이 유지되는지 확인합니다."
        )
        response["invalidationCondition"] = (
            "가격 회복 설명과 수급 관계가 약해지면 다시 확인합니다."
        )

        assessment = investment_decision_actionability(context(), response)

        self.assertFalse(is_concrete_observable_condition(response["nextActionPlan"]))
        self.assertTrue(is_concrete_observable_condition(
            "외국인이 순매도로 전환되면 매수 판단을 취소합니다."
        ))
        self.assertIn("action-plan-consistency", assessment["gaps"])
        self.assertIn("observable-next-condition", assessment["gaps"])
        self.assertIn("invalidation-condition", assessment["gaps"])
        self.assertEqual("failed", assessment["stages"]["action"]["status"])
        self.assertEqual("failed", assessment["stages"]["followUp"]["status"])

        values = context()
        values["investmentSubjectDecisionCaseId"] = "subject-case:vague-boundary"
        values["notificationAiValidatedResponse"] = response
        message = execution_telegram_message(
            values,
            NotificationAIValidatedResponse.from_dict(response),
        )
        self.assertNotIn(response["nextActionPlan"], message)
        self.assertIn("재판단 기준 없음", message)
        self.assertIn("검토 기록으로만 남깁니다", message)

    def test_validator_downgrades_shadow_buy_without_leaving_buy_plan(self):
        response = validated_response_from_payload(
            context("shadow"),
            ai_payload("shadow"),
            raw_response='{"action":"BUY"}',
        )

        self.assertEqual("HOLD", response.action)
        self.assertIn("관심 상태를 유지", response.current_action_plan)
        self.assertNotIn("분할매수만 검토", response.current_action_plan)
        self.assertEqual("review-only", response.decision_assurance["executionEligibility"])

    def test_validator_keeps_active_buy_with_supported_causal_path(self):
        response = validated_response_from_payload(
            context("active"),
            ai_payload("active"),
            raw_response='{"action":"BUY"}',
        )

        self.assertEqual("BUY", response.action)
        self.assertEqual("ready", response.decision_readiness)
        self.assertEqual("eligible", response.decision_assurance["executionEligibility"])


if __name__ == "__main__":
    unittest.main()
