import unittest
from datetime import datetime, timedelta, timezone

from digital_twin.application.console_read_model_service import ConsoleReadModelService


def component(result, key):
    return next(item for item in result["components"] if item["id"] == key)


class ConsoleOperationsHealthTests(unittest.TestCase):
    def test_fresh_monitor_and_empty_queues_are_healthy(self):
        now = datetime.now(timezone.utc).isoformat()
        result = ConsoleReadModelService().operations_health({
            "realtime": {
                "monitoring": {"snapshot": {"occurredAt": now}},
                "aiInferenceQueue": {"states": {}, "effectiveAiStatus": "healthy"},
                "notificationJobs": {},
            },
            "reasoning": {"status": "healthy", "effectivePendingCount": 0, "processingCount": 0},
        })

        self.assertEqual("healthy", component(result, "monitoring")["state"])
        self.assertEqual("healthy", component(result, "reasoning")["state"])
        self.assertEqual("healthy", component(result, "ai")["state"])

        degraded = ConsoleReadModelService().operations_health({
            "realtime": {
                "monitoring": {"snapshot": {"occurredAt": now}},
                "aiInferenceQueue": {
                    "states": {},
                    "effectiveAiStatus": "degraded",
                    "effectiveAiWindowHours": 24,
                    "effectiveAiEligibleCount": 8,
                    "effectiveAiAuthoredCount": 0,
                    "effectiveAiFallbackCount": 8,
                    "effectiveAiLatestAt": now,
                },
                "notificationJobs": {
                    "suppressed": 7,
                    "intentional_suppressed": 7,
                    "suppression_categories": {
                        "unchanged_decision": 5,
                        "duplicate_or_cooldown": 2,
                    },
                },
            },
            "reasoning": {"status": "healthy", "effectivePendingCount": 0, "processingCount": 0},
        })

        ai = component(degraded, "ai")
        notifications = component(degraded, "notifications")
        self.assertEqual("warning", ai["state"])
        self.assertIn("최근 24시간 실효 AI 0/8건", ai["detail"])
        self.assertIn("폴백 8건", ai["detail"])
        self.assertNotIn("누적 감사", ai["detail"])
        self.assertIn("정책 억제 7건", notifications["detail"])
        self.assertIn("동일 판단 5", notifications["detail"])
        self.assertIn("중복·쿨다운 2", notifications["detail"])
        self.assertNotIn("누적 보류", notifications["detail"])

    def test_stale_snapshot_and_old_reasoning_or_ai_work_are_visible(self):
        old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        result = ConsoleReadModelService().operations_health({
            "realtime": {
                "monitoring": {"snapshot": {"occurredAt": old}},
                "aiInferenceQueue": {
                    "states": {"pending": {"count": 1, "oldestAt": old}},
                    "effectiveAiStatus": "healthy",
                },
                "notificationJobs": {},
            },
            "reasoning": {
                "status": "active",
                "effectivePendingCount": 1,
                "processingCount": 0,
                "oldestRequestAt": old,
            },
        })

        self.assertEqual("warning", component(result, "monitoring")["state"])
        self.assertEqual("critical", component(result, "reasoning")["state"])
        self.assertEqual("critical", component(result, "ai")["state"])

if __name__ == "__main__":
    unittest.main()
