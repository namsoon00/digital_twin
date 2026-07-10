import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.portfolio import AlertEvent, utc_now_iso
from digital_twin.infrastructure.notifications import send_events
from digital_twin.infrastructure.sqlite_notifications import SQLiteNotificationJobStore, SQLiteNotificationRuleStore


class CryptoRepeatPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env_patch = mock.patch.dict(os.environ, {
            "DIGITAL_TWIN_DATA_DIR": self.temp.name,
            "SETTINGS_PATH": str(Path(self.temp.name) / "settings.json"),
        }, clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def fresh_data_freshness(self):
        return {
            "source": "CoinGecko",
            "status": "fresh",
            "reason": "신선도 기준 통과",
            "ageMinutes": 0,
            "maxAgeMinutes": 10,
            "sourceFetchedAt": utc_now_iso(),
            "checkedAt": utc_now_iso(),
        }

    def btc_insight_event(self, key: str, relation_score: float, event_key: str) -> AlertEvent:
        return AlertEvent(
            "main",
            "메인",
            "WATCH",
            "investmentInsight",
            key,
            "크립토 변동 / 비트코인 / BTC",
            [
                "인사이트 유형: 외부 환경 변화",
                "핵심 결론: 비트코인 민감 종목 연동 점검",
                "근거 신호: 크립토 변동",
            ],
            "BTC",
            metadata={
                "ontologyInsight": {
                    "subject": "BTC",
                    "dispatchInsightType": "externalRegimeShift",
                    "insightType": "externalRegimeShift",
                    "score": relation_score,
                    "noveltyScore": 25,
                    "confidence": 55,
                    "sourceSignalTypes": ["externalCryptoMove"],
                    "sourceEventKeys": [event_key],
                },
                "sourceSignalTypes": ["externalCryptoMove"],
                "dataFreshness": self.fresh_data_freshness(),
            },
        )

    def test_btc_investment_insight_suppresses_percentage_only_source_key_change(self):
        db_path = Path(self.temp.name) / "service.db"
        queue = SQLiteNotificationJobStore(db_path)
        rules = SQLiteNotificationRuleStore(db_path)
        rule = rules.get("investmentInsight")
        rule.market_hours_enabled = False
        rules.upsert(rule)

        first = self.btc_insight_event("main:insight:btc:1", 60.6, "main:crypto:btc:7d:+4.0%")
        second = self.btc_insight_event("main:insight:btc:2", 64.3, "main:crypto:btc:7d:+4.3%")

        self.assertEqual(1, send_events([first], queue=queue).queued)
        first_job = queue.jobs()[0]
        first_job.status = "done"
        queue.update(first_job)

        self.assertEqual(0, send_events([second], queue=queue).queued)

        jobs = queue.jobs()
        self.assertEqual(["done", "suppressed"], [job.status for job in jobs])
        self.assertEqual(jobs[0].context["honeyFingerprint"], jobs[1].context["honeyFingerprint"])
        self.assertEqual("cooldown", jobs[1].context["honeyStateDecision"])
        self.assertFalse(jobs[1].context.get("honeySimilarityBypassed"))
        self.assertIn("같은 임계값 상태 지속", jobs[1].last_error)


if __name__ == "__main__":
    unittest.main()
