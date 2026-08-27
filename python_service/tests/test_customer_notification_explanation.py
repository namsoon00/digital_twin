import unittest

from digital_twin.application.notification_ai_gate_message import (
    compact_decision_transition,
    execution_telegram_message,
)
from digital_twin.application.notification.rendering import NotificationRenderingService
from digital_twin.domain.customer_evidence_explanation import (
    build_customer_evidence_explanations,
    customer_data_limitation_text,
    customer_safe_text,
    customer_text_quality_issues,
)
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notifications import NotificationJob


def review_only_context():
    return {
        "messageType": "investmentInsight",
        "notificationDetailLevel": "concise",
        "messageDeliveryLevel": "beginner",
        "displayTarget": "LS네트웍스 / 000680",
        "target": "LS네트웍스 / 000680",
        "notificationNumber": "N-TEST",
        "referenceDate": "2026-08-27 12:55 KST",
        "sentTime": "2026-08-27 13:10 KST",
        "decisionPublication": {"outcomeKind": "REVIEW_ONLY"},
        "investmentSubjectDecisionCase": {
            "abstention": {
                "reason": "selectedHypothesisId is not present in the routed TypeDB hypothesis set."
            }
        },
        "v2DecisionSynthesis": {
            "change_state": "new-condition",
            "conflict_state": "mixed",
            "judgement_blocked": True,
        },
        "aiDecisionTransition": {
            "historyAvailable": True,
            "kind": "unchanged",
            "previousAction": "HOLD",
            "currentAction": "HOLD",
        },
        "ontologyRelationContext": {
            "targetRole": "watchlist",
            "facts": {
                "source": "watchlist",
                "isWatchlist": True,
                "market": "KR",
                "currency": "KRW",
                "currentPrice": 3025,
                "ma5Distance": 0.934,
                "ma20Distance": 3.058,
                "ma60Distance": 3.146,
                "volume": 48156,
                "volumeRatio": 0.4862,
            },
            "graphStoreInference": {
                "traces": [
                    {
                        "ruleId": "graph.company.market.fragile_rally.risk.v1",
                        "thesisFamily": "fundamental-deterioration",
                        "claimContract": {"expectedDirection": "risk"},
                        "matchedConditions": [
                            {
                                "relationType": "HAS_SHARED_MARKET_PREMISE",
                                "observedValue": {"group": "fragile-rally"},
                            }
                        ],
                    },
                    {
                        "ruleId": "graph.watchlist.pullback.entry.v1",
                        "thesisFamily": "mean-reversion",
                        "claimContract": {"expectedDirection": "support"},
                        "matchedConditions": [
                            {"field": "source", "observedValue": "watchlist"}
                        ],
                    },
                ]
            },
        },
        "notificationNarrativeBrief": {
            "evidenceLedger": [
                {
                    "kind": "inference",
                    "role": "support",
                    "label": "상대가치 부담 신호 · 취약한 반등 점검",
                    "detail": "취약한 반등 점검 / HAS_INFERRED_RISK / LS네트웍스 · 모델 신호",
                    "source": "TypeDB",
                    "sourceAsOf": "2026-08-27T04:06:23Z",
                    "ruleIds": ["graph.company.market.fragile_rally.risk.v1"],
                },
                {
                    "kind": "inference",
                    "role": "support",
                    "label": "가격 경로 회복 신호 · 신규 진입 대기",
                    "detail": "신규 진입 대기 / HAS_INFERRED_SUPPORT / LS네트웍스 · 모델 신호",
                    "source": "TypeDB",
                    "sourceAsOf": "2026-08-27T04:06:23Z",
                    "ruleIds": ["graph.watchlist.pullback.entry.v1"],
                },
            ]
        },
    }


class CustomerNotificationExplanationTests(unittest.TestCase):
    def test_internal_evidence_and_missing_field_names_are_not_customer_text(self):
        raw = (
            "상대가치 부담 신호: 상대가치 부담 신호 / HAS_INFERRED_RISK / "
            "LS네트웍스 · 모델 신호"
        )

        self.assertEqual("상대가치 부담 신호", customer_safe_text(raw))
        limitation = customer_data_limitation_text(
            "적정가 판단에 필요한 값이 일부 부족합니다: expectedEPS, fairValue, targetPER"
        )
        self.assertIn("예상 EPS·적정가·목표 PER", limitation)
        self.assertFalse(customer_text_quality_issues(limitation))

    def test_review_only_message_explains_conflict_without_fake_hold(self):
        context = review_only_context()
        response = NotificationAIValidatedResponse(
            action="HOLD",
            action_label="관심 유지",
            change_analysis="이전 AI 최종 판단과 같은 관심 유지입니다.",
            evidence=[
                "취약한 반등 점검 / HAS_INFERRED_RISK / LS네트웍스 · 모델 신호",
                "신규 진입 대기 / HAS_INFERRED_SUPPORT / LS네트웍스 · 모델 신호",
            ],
            missing_data_impact=[
                "적정가 판단에 필요한 값이 일부 부족합니다: expectedEPS, fairValue, targetPER"
            ],
            next_checks=["가격 경로와 재무 자료를 다시 확인합니다."],
            source="TypeDB inference fallback",
        )

        message = execution_telegram_message(context, response)

        self.assertIn("[관계 검토] 매수·매도·보유 판단을 새로 만들지 않았습니다.", message)
        self.assertIn("<b>서로 다른 근거</b>", message)
        self.assertIn("위험 쪽: 재무 위험 모델은 이번 반등이 이어지지 못할 가능성을 감지했습니다.", message)
        self.assertIn("5일선보다 0.9% 높음", message)
        self.assertIn("거래량은 평균의 0.49배", message)
        self.assertIn("현금흐름과 부채의 실제 수치", message)
        self.assertIn("AI 비교 결과가 시스템이 허용한 후보와 일치하지 않아", message)
        self.assertIn("예상 EPS·적정가·목표 PER", message)
        self.assertNotIn("판단 유지", message)
        self.assertNotIn("이전 AI 최종 판단과 같은 관심 유지", message)
        self.assertNotIn("TypeDB 검토 가설", message)
        self.assertNotIn("HAS_INFERRED", message)
        self.assertNotIn("graph.", message)
        self.assertNotIn("expectedEPS", message)
        self.assertNotIn("모델 신호", message)

    def test_customer_projection_keeps_observed_fields_and_missing_proof_separate(self):
        rows = build_customer_evidence_explanations(review_only_context())

        self.assertEqual(["risk", "support"], [item["role"] for item in rows])
        self.assertEqual([], rows[0]["observedFields"])
        self.assertIn("실제 수치", rows[0]["limitation"])
        self.assertIn("ma20Distance", rows[1]["observedFields"])
        customer_sentences = " ".join(
            str(item.get("statement") or "") + " " + str(item.get("limitation") or "")
            for item in rows
        )
        self.assertFalse(customer_text_quality_issues(customer_sentences))

    def test_final_decision_still_compares_with_previous_decision(self):
        context = review_only_context()
        context["decisionPublication"] = {"outcomeKind": "FINAL_DECISION"}
        response = NotificationAIValidatedResponse(action="HOLD", action_label="관심 유지")

        transition = compact_decision_transition(context, response)

        self.assertIn("판단 유지", transition)
        self.assertIn("관심 유지", transition)

    def test_rendering_audit_records_customer_language_quality(self):
        job = NotificationJob.create(
            "test",
            account_id="default",
            message_type="investmentInsight",
            context=review_only_context(),
        )
        job.text = "• 설명 / HAS_INFERRED_RISK / 내부 · 모델 신호"
        service = NotificationRenderingService()
        service.apply_send_time_context = lambda _job: None
        service.apply_investment_presentation_contract = lambda _job: None

        rendered = service.render(job)

        self.assertNotIn("HAS_INFERRED_RISK", rendered)
        quality = job.context["notificationPresentationAudit"]["customerLanguageQuality"]
        self.assertEqual("passed", quality["status"])
        self.assertEqual([], quality["issues"])


if __name__ == "__main__":
    unittest.main()
