import unittest

from digital_twin.modules.notifications.application.notification_ai_gate_message import compact_decision_transition, execution_telegram_message
from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
from digital_twin.modules.read_models.domain.customer_evidence_explanation import (
    build_customer_evidence_explanations,
    customer_data_limitation_text,
    customer_safe_text,
    customer_text_quality_issues,
    enforce_customer_message_quality,
)
from digital_twin.modules.read_models.domain.customer_investment_document import (
    customer_follow_up_condition_clause,
    customer_investment_text,
)
from digital_twin.modules.decisions.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.modules.decisions.domain.notification_ai_gate_text import user_friendly_ai_text
from digital_twin.modules.notifications.domain.notifications import NotificationJob


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
    def test_customer_boundary_translates_conditions_and_hides_internal_terms(self):
        moving_average = customer_follow_up_condition_clause(
            "ma20Distance",
            "<=",
            0,
            current=10.76,
        )
        volume = customer_follow_up_condition_clause(
            "volumeRatio",
            ">=",
            1.5,
            current=0.06,
        )

        self.assertEqual(
            "현재가가 20일 평균 가격 이하로 내려가면 (현재 20일 평균보다 10.76% 위)",
            moving_average,
        )
        self.assertEqual(
            "최근 평균 대비 거래량이 1.5배 이상이면 (현재 0.06배)",
            volume,
        )
        self.assertNotIn("ma20Distance", moving_average)
        self.assertNotIn("20일선 차이", moving_average)

        text = customer_investment_text(
            "TypeDB 가설 관계의 인과 경로에서 외국인 수급과 단기 추세, "
            "밸류에이션과 펀더멘털을 함께 관측해 무효화 임계값을 정했습니다."
        )

        self.assertIn("외국인 매매 흐름", text)
        self.assertIn("가격 흐름", text)
        self.assertIn("현재 가격 수준", text)
        self.assertIn("실적과 재무 상태", text)
        for internal in (
            "TypeDB", "가설", "인과 경로", "수급", "추세", "밸류에이션",
            "펀더멘털", "관측", "무효화", "임계값",
        ):
            self.assertNotIn(internal, text)

        legacy_ai = customer_investment_text(
            "위험 방향은 부정적이지만 관계 근거가 weakened 상태이므로 현재 행동은 "
            "주문 없는 NO_ACTION입니다. qualification이 shadow이고 "
            "reasoningLineage도 judgementEligible이 아닙니다."
        )
        self.assertIn("지금은 보유 수량을 바꾸지 않습니다", legacy_ai)
        for internal in (
            "weakened", "NO_ACTION", "qualification", "shadow",
            "reasoningLineage", "judgementEligible",
        ):
            self.assertNotIn(internal, legacy_ai)

        catalyst = customer_investment_text(
            "다음 공시의 발행주식수 증가 확인이 핵심 촉매입니다."
        )
        self.assertIn("주가에 영향을 줄 핵심 사건", catalyst)
        self.assertNotIn("상승 계기", catalyst)

        monitored_filing = customer_investment_text(
            "다음 공시에서 주식수와 현금흐름 방향을 확인한 뒤 행동 변경을 검토하셔야 합니다."
        )
        self.assertEqual(
            "다음 공시에서 주식수와 현금흐름 방향이 확인되면 시스템이 행동 변경 여부를 다시 판단합니다.",
            monitored_filing,
        )
        malformed_legacy = customer_investment_text(
            "강한 확인된 신호가 0% 아래로 내려감되고 일부 매도 여부으로 전환하되, "
            "두 조건이 모두 확인하면 실적과 재무 상태 반대 가능성가 약해집니다. "
            "기간 히스토리 부족과 최근 기간 현재 수치 부족도 확인됐습니다."
        )
        self.assertIn("강하게 확인된 신호가 0% 아래로 내려가고", malformed_legacy)
        self.assertIn("일부 매도 여부를 다시 판단하되", malformed_legacy)
        self.assertIn("두 조건이 모두 확인되면", malformed_legacy)
        self.assertIn("실적과 재무 상태가 양호하다는 반대 근거가", malformed_legacy)
        self.assertIn("과거 데이터 부족과 최근 데이터 부족", malformed_legacy)
        for malformed in (
            "강한 확인된", "내려감되고", "여부으로", "모두 확인하면",
            "반대 가능성가", "히스토리", "현재 수치 부족",
        ):
            self.assertNotIn(malformed, malformed_legacy)

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
        research_reason = enforce_customer_message_quality(
            "• 조건부 모델 신호는 연결됐지만 사후 5건의 적중률은 40%입니다."
        )
        self.assertIn("조건부 검증 신호", research_reason)
        self.assertIn("적중률은 40%", research_reason)
        self.assertNotIn("성립값이 부족", research_reason)
        repaired = enforce_customer_message_quality(
            "\n".join([
                "<b>판단이 바뀌는 조건</b>",
                "• ma20Distance가 9.443532746531403 미만이면 재검토합니다.",
                "",
                "<b>반대 근거 확인</b>",
                "• 모든 후보 근거를 비교했으며, 현재 방향을 뒤집는 검증된 반대 사실은 확인되지 않았습니다.",
            ])
        )
        self.assertIn("20일선 차이가 9.44% 미만", repaired)
        self.assertNotIn("ma20Distance", repaired)
        self.assertNotIn("반대 근거 확인", repaired)
        self.assertNotIn("모든 후보 근거", repaired)
        self.assertFalse(customer_text_quality_issues(repaired))
        ai_text = user_friendly_ai_text(
            "현재 보유 10주는 HOLD하고 ma20Slope 1.2와 volume, buyVolume, "
            "sellVolume, bidAskImbalance, macroDgs10, macroDgs2, macroDff, "
            "usdKrwRate, usdKrwDeltaPct를 확인한 뒤 actionEnvelope를 재검토합니다.",
            700,
        )
        self.assertIn("현재 보유 10주를 유지하고", ai_text)
        self.assertIn("20일선 기울기 1.2%", ai_text)
        self.assertIn("금리", ai_text)
        self.assertIn("원·달러 환율", ai_text)
        for internal in (
            "HOLD", "ma20Slope", "buyVolume", "sellVolume",
            "bidAskImbalance", "macroDgs10", "macroDgs2", "macroDff",
            "usdKrwRate", "usdKrwDeltaPct", "actionEnvelope",
        ):
            self.assertNotIn(internal, ai_text)

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

        self.assertIn("지금은 주문하지 않습니다.", message)
        self.assertIn("5일·20일·60일 평균 가격을 모두 웃돌아", message)
        self.assertIn("기존 행동을 바꿀 근거가 한쪽으로 우세하지 않습니다.", message)
        self.assertIn("<b>왜 이렇게 봤나요</b>", message)
        self.assertIn("위험 쪽: 재무 위험 모델은 이번 반등이 이어지지 못할 가능성을 감지했습니다.", message)
        self.assertIn("5일 평균 가격보다 0.9% 높음", message)
        self.assertIn("거래량 48,156 · 평균 대비 0.49배", message)
        self.assertIn("현금흐름과 부채의 실제 수치", message)
        self.assertNotIn("예상 EPS·적정가·목표 PER", message)
        self.assertNotIn("판단 유지", message)
        self.assertNotIn("이전 AI 최종 판단과 같은 관심 유지", message)
        self.assertNotIn("TypeDB 검토 가설", message)
        self.assertNotIn("HAS_INFERRED", message)
        self.assertNotIn("graph.", message)
        self.assertNotIn("expectedEPS", message)
        self.assertNotIn("모델 신호", message)
        self.assertNotIn("TypeDB", message)
        self.assertNotIn("가설", message)

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
        self.assertEqual("repaired", quality["status"])
        self.assertTrue(quality["repairApplied"])
        self.assertEqual([], quality["issues"])


if __name__ == "__main__":
    unittest.main()
