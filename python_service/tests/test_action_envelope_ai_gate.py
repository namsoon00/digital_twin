import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.notification_ai_gate_validation import (  # noqa: E402
    ai_decision_input_packet,
    local_validated_ai_response,
    validated_response_from_payload,
)
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse  # noqa: E402
from digital_twin.application.notification_ai_gate_message import (  # noqa: E402
    execution_telegram_message,
    notification_topline_change_summary,
)


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
    def test_ai_input_receives_typedb_envelope_and_transition(self):
        context = entry_context()

        packet = ai_decision_input_packet(
            context,
            {"facts": context["ontologyRelationContext"]["facts"]},
            {"level": "beginner", "label": "왕초보"},
        )

        inference = packet["relationshipDatabaseInference"]
        self.assertEqual("ENTRY_ELIGIBLE", inference["actionEnvelope"]["status"])
        self.assertEqual("BUY", inference["actionEnvelope"]["preferredAction"])
        self.assertEqual("action-changed", inference["decisionTransition"]["kind"])

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

    def test_ai_may_lower_entry_eligibility_with_explicit_counter_evidence(self):
        response = validated_response_from_payload(
            entry_context(),
            {
                "action": "HOLD",
                "summary": "진입 조건은 생겼지만 직접 반대 뉴스가 더 중요합니다.",
                "opinion": "원문 확인 전에는 관심을 유지합니다.",
                "evidence": ["진입 관계가 성립했습니다."],
                "counterEvidence": ["본문을 확인한 직접 위험 뉴스가 새로 나왔습니다."],
                "disagreementReason": "직접 위험 뉴스가 진입 지지 관계를 단기적으로 약화시킵니다.",
                "nextChecks": ["위험 뉴스 원문과 가격 반응 확인"],
            },
        )

        self.assertEqual("HOLD", response.action)

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
                "source": "Reuters",
                "headline": "NVIDIA won a new data-center supply contract.",
            },
        })
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
            invalidation_condition="진입 지지 관계가 사라지거나 직접 반대 뉴스가 확인되면 다시 봅니다.",
            next_checks=["정규장 거래량이 유지되는지 확인"],
            reference_date="2026-07-27 10:00 KST",
        )

        message = execution_telegram_message(context, response)

        for heading in ["지금 행동", "이번 변화", "현재 흐름", "바뀐 이유", "다음 행동", "판단 변경 조건", "자료 상태", "뉴스 영향"]:
            self.assertIn(heading, message)
        self.assertIn("[AI]", message)
        self.assertNotIn("API 조회 정보", message)
        self.assertNotIn("뉴스·공시 요약", message)

    def test_compact_message_hides_internal_envelope_status(self):
        context = entry_context()
        context.update({
            "messageDeliveryLevel": "beginner",
            "decisionTransition": {
                "currentAction": "HOLD",
                "currentStatus": "ENTRY_OBSERVING",
                "previousStatus": "",
                "material": True,
            },
        })
        context["ontologyRelationContext"]["actionEnvelope"].update({
            "status": "ENTRY_OBSERVING",
            "preferredAction": "HOLD",
            "selectedDecisionEffect": "constrain",
        })
        response = NotificationAIValidatedResponse(
            action="HOLD",
            action_label="관심 유지",
            validation_state="conditional",
            validation_label="조건부 사용",
            data_state="sufficient",
            data_state_label="판단에 필요한 자료 있음",
            review_level="check",
            review_label="조건 확인",
            summary="매수로 바꿀 만큼의 진입 근거가 아직 확인되지 않았습니다.",
            invalidation_condition="",
            reference_date="2026-07-27 10:00 KST",
        )

        message = execution_telegram_message(context, response)

        self.assertIn("새로 확인된 조건: 관심 유지", message)
        self.assertIn("진입 시점과 금액을 보수적으로", message)
        self.assertNotIn("ENTRY_OBSERVING", message)
        self.assertNotIn("TypeDB", message)

    def test_topline_uses_human_decision_change_labels(self):
        self.assertEqual(
            "새 판단 조건",
            notification_topline_change_summary({
                "decisionTransition": {"kind": "initial", "material": True},
            }),
        )
        self.assertEqual(
            "행동 변경",
            notification_topline_change_summary({
                "decisionTransition": {"kind": "action-changed", "material": True},
            }),
        )

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


if __name__ == "__main__":
    unittest.main()
