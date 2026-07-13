import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.notification_rules import (
    apply_similarity_rule,
    apply_state_cooldown_rule,
    default_notification_rule,
    evaluate_notification_rule,
)
from digital_twin.domain.notification_ai_gate_message import (
    notification_topline_change_summary,
    prepend_execution_start_badge,
)
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.strategy_alerts import StrategyAlertMixin
from digital_twin.domain.portfolio import utc_now_iso


class NotificationDataQualityPolicyTests(unittest.TestCase):
    def test_topline_change_summary_is_separated_from_new_alert_badge(self):
        message = prepend_execution_start_badge(
            "<b>[주의] 🛡️ SK하이닉스: 분할축소 점검</b>",
            {"honeyStateReason": "의미 있는 추가 확대: 손익률 추가 악화 -8.9% -> -10.4%"},
        )

        self.assertTrue(message.startswith("<b>🔔 새 알림</b>\n<code>손익 구간: 손실 관리(-10.4%) · 이전 알림 대비 1.5%p 악화</code>"))
        self.assertEqual(1, message.count("🔔 새 알림"))

    def test_topline_change_summary_shows_profit_loss_improvement_delta(self):
        summary = notification_topline_change_summary({
            "profitLossRate": -8.9,
            "previousProfitLossRate": -10.4,
            "honeyStateReason": "의미 있는 추가 확대: 관계 강도 변화",
        })

        self.assertEqual("손익 구간: 손실 관리(-8.9%) · 이전 알림 대비 1.5%p 개선", summary)

    def test_topline_change_summary_uses_nested_profit_loss_delta(self):
        summary = notification_topline_change_summary({
            "ontologyInsight": {"facts": {"profitLossRateDeltaPct": -2.25}},
            "honeyStateReason": "의미 있는 추가 확대: 관계 강도 변화",
        })

        self.assertEqual("손익 구간: 이전 알림 대비 2.2%p 악화", summary)

    def test_topline_change_summary_maps_new_news_disclosure_reason(self):
        summary = notification_topline_change_summary({
            "honeyStateReason": "의미 있는 추가 확대: 새 근거 신호 추가 holdingTiming, externalDartDisclosure",
            "sourceSignalTypes": ["holdingTiming", "externalDartDisclosure"],
        })

        self.assertEqual("새 뉴스·공시", summary)

    def test_watchlist_data_conflict_is_data_quality_signal(self):
        mixin = StrategyAlertMixin()
        signal_type = mixin.watchlist_ontology_signal_type({
            "decision": {"actionGroup": "review"},
            "activeRules": [{"ruleId": "data.conflict.v1"}],
        })

        self.assertEqual("dataQuality", signal_type)

    def test_data_quality_insight_does_not_bypass_cooldown_for_novelty_only(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "데이터 충돌 점검",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "body": "동화약품 데이터 충돌 점검",
                "symbol": "000020",
                "ontologyInsight": {
                    "subject": "000020",
                    "insightType": "dataQualityWarning",
                    "dispatchInsightType": "dataQualityWarning",
                    "score": 61.1,
                    "noveltyScore": 48,
                    "confidence": 75,
                    "sourceEventKeys": [
                        "main:watchlist-ontology:000020:dataQuality:data.conflict.v1",
                        "main:watchlist-quote:000020:0.0%",
                    ],
                },
                "sourceSignalTypes": ["watchlistOntologySignal", "watchlistQuote"],
            },
        )
        previous_context = {
            "severity": "WATCH",
            "ontologyInsight": {
                "subject": "000020",
                "insightType": "dataQualityWarning",
                "dispatchInsightType": "dataQualityWarning",
                "score": 61.1,
                "noveltyScore": 25,
                "confidence": 75,
                "sourceEventKeys": ["main:watchlist-ontology:000020:dataQuality:data.conflict.v1"],
            },
            "sourceSignalTypes": ["watchlistOntologySignal"],
        }
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_score=decision.score,
            previous_context=previous_context,
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=80,
            job=job,
        )

        self.assertFalse(decision.should_send)
        self.assertEqual("cooldown", decision.state_decision)
        self.assertFalse(decision.similarity_bypassed)

    def test_critical_loss_from_raw_lines_bypasses_state_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "SK하이닉스 손실 점검",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "body": "SK하이닉스 손절·분할축소 점검",
                "symbol": "000660",
                "rawLines": "현재가: 2,007,000원\n평균매입가: 2,571,000원\n수익률: -21.9%",
                "ontologyInsight": {
                    "subject": "000660",
                    "dispatchInsightType": "riskManagement",
                    "score": 86,
                    "noveltyScore": 20,
                    "confidence": 70,
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_score=decision.score,
            previous_context={
                "severity": "WATCH",
                "rawLines": "현재가: 2,014,000원\n평균매입가: 2,571,000원\n수익률: -21.8%",
                "ontologyInsight": {"subject": "000660", "dispatchInsightType": "riskManagement"},
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=117,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("mandatory_profit_loss_band", decision.state_decision)
        self.assertTrue(decision.similarity_bypassed)
        self.assertIn("-21.9%", decision.state_reason)

    def test_mandatory_profit_band_bypasses_similarity_penalty(self):
        rule = default_notification_rule("investmentInsight")
        rule.similarity_penalty = -100
        job = NotificationJob.create(
            "수익 보호 점검",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "body": "수익 보호 점검",
                "symbol": "MSTR",
                "profitLossRate": 24.5,
                "ontologyInsight": {
                    "subject": "MSTR",
                    "dispatchInsightType": "riskManagement",
                    "score": 70,
                    "noveltyScore": 20,
                    "confidence": 70,
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_similarity_rule(
            decision,
            rule,
            recent_count=1,
            previous_score=decision.score,
            previous_context={"profitLossRate": 23.0},
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertTrue(decision.similarity_bypassed)
        self.assertIn("+24.5%", decision.similarity_bypass_reason)

    def test_profit_loss_worsening_bypasses_investment_insight_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "손익률 추가 악화",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "000660",
                "rawLines": "수익률: -10.4%\n추세: 60일선 2,000,000원보다 3.0% 높음",
                "ontologyInsight": {"subject": "000660", "dispatchInsightType": "riskManagement", "score": 70, "noveltyScore": 20},
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_score=decision.score,
            previous_context={
                "rawLines": "수익률: -8.9%\n추세: 60일선 2,000,000원보다 3.2% 높음",
                "ontologyInsight": {"subject": "000660", "dispatchInsightType": "riskManagement"},
                "sourceSignalTypes": ["holdingTiming"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=80,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("material_change", decision.state_decision)
        self.assertIn("손익률 추가 악화", decision.state_reason)

    def test_ma60_cross_down_bypasses_investment_insight_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "60일선 이탈",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "005930",
                "rawLines": "수익률: -4.0%\n추세: 60일선 289,838원보다 1.2% 낮음",
                "ontologyInsight": {"subject": "005930", "dispatchInsightType": "riskManagement", "score": 70, "noveltyScore": 20},
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_score=decision.score,
            previous_context={
                "rawLines": "수익률: -3.8%\n추세: 60일선 289,838원보다 0.4% 높음",
                "ontologyInsight": {"subject": "005930", "dispatchInsightType": "riskManagement"},
                "sourceSignalTypes": ["holdingTiming"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=80,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("material_change", decision.state_decision)
        self.assertIn("60일 평균 아래 전환", decision.state_reason)

    def test_action_change_bypasses_investment_insight_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "판단 액션 변경",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "MSTR",
                "profitLossRate": 6.0,
                "activeInvestmentOpinion": {"actionLabel": "분할축소"},
                "ontologyInsight": {"subject": "MSTR", "dispatchInsightType": "riskManagement", "score": 70, "noveltyScore": 20},
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_score=decision.score,
            previous_context={
                "profitLossRate": 6.2,
                "activeInvestmentOpinion": {"actionLabel": "보유"},
                "ontologyInsight": {"subject": "MSTR", "dispatchInsightType": "riskManagement"},
                "sourceSignalTypes": ["holdingTiming"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=80,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("material_change", decision.state_decision)
        self.assertIn("판단 액션 변경", decision.state_reason)

    def test_new_news_or_disclosure_event_bypasses_investment_insight_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "새 공시 근거",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "005930",
                "profitLossRate": -4.0,
                "ontologyInsight": {
                    "subject": "005930",
                    "dispatchInsightType": "riskManagement",
                    "score": 70,
                    "noveltyScore": 20,
                    "sourceEventKeys": ["main:disclosure:005930:202607130001"],
                },
                "sourceSignalTypes": ["holdingTiming", "externalDartDisclosure"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_score=decision.score,
            previous_context={
                "profitLossRate": -4.0,
                "ontologyInsight": {
                    "subject": "005930",
                    "dispatchInsightType": "riskManagement",
                    "sourceEventKeys": ["main:holding:005930:risk"],
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=80,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("material_change", decision.state_decision)
        self.assertIn("새 근거 신호 추가", decision.state_reason)


if __name__ == "__main__":
    unittest.main()
