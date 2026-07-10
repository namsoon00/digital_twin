import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.notification_rules import apply_state_cooldown_rule, default_notification_rule, evaluate_notification_rule
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


if __name__ == "__main__":
    unittest.main()
