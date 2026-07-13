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
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.strategy_alerts import StrategyAlertMixin
from digital_twin.domain.portfolio import utc_now_iso


class NotificationDataQualityPolicyTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
