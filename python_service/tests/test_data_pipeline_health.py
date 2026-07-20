import unittest
from datetime import datetime, timezone

from digital_twin.application.data_pipeline_health_service import DataPipelineHealthService
from digital_twin.domain.data_pipeline_health import evaluate_news_collection_health


class MemoryStore:
    def __init__(self):
        self.payload = {}

    def load(self):
        return dict(self.payload)

    def replace(self, payload):
        self.payload = dict(payload)


class DataPipelineHealthTests(unittest.TestCase):
    def test_empty_successful_cycle_is_idle_not_failure(self):
        health = evaluate_news_collection_health({
            "status": "ok",
            "targetCount": 2,
            "fetchedCount": 0,
            "savedCount": 0,
            "statuses": [
                {"source": "yahoo", "ok": True, "count": 0, "candidateCount": 0},
                {"source": "google", "ok": True, "count": 0, "candidateCount": 0},
            ],
        }, now=datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc))
        self.assertEqual(health.state, "idle")
        self.assertEqual(health.reason_code, "no-new-evidence")
        self.assertFalse(health.alert_required)

    def test_repeated_quality_rejection_becomes_degraded(self):
        previous = {
            "state": "idle",
            "stateSince": "2026-07-20T00:58:00Z",
            "firstObservedAt": "2026-07-20T00:58:00Z",
            "consecutiveZeroRuns": 2,
        }
        health = evaluate_news_collection_health({
            "status": "ok",
            "targetCount": 1,
            "fetchedCount": 0,
            "savedCount": 0,
            "statuses": [{
                "source": "yahoo_search",
                "ok": True,
                "count": 0,
                "candidateCount": 5,
                "bodyMissingCount": 4,
                "finalRelevanceRejectedCount": 1,
            }],
        }, previous=previous, blocked_warning_streak=3, now=datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc))
        self.assertEqual(health.state, "degraded")
        self.assertEqual(health.reason_code, "article-body-unavailable")
        self.assertEqual(health.provider_candidate_count, 5)
        self.assertTrue(health.alert_required)

    def test_recovery_from_failed_state_requires_alert(self):
        health = evaluate_news_collection_health({
            "status": "ok",
            "targetCount": 1,
            "fetchedCount": 2,
            "savedCount": 1,
            "statuses": [{"source": "yahoo", "ok": True, "count": 2, "candidateCount": 2}],
        }, previous={"state": "failed", "consecutiveZeroRuns": 4})
        self.assertEqual(health.state, "healthy")
        self.assertTrue(health.alert_required)

    def test_service_persists_zero_streak(self):
        store = MemoryStore()
        service = DataPipelineHealthService(store, {"newsCollectionQualityBlockedWarningStreak": "2"})
        result = {
            "status": "ok",
            "targetCount": 1,
            "fetchedCount": 0,
            "savedCount": 0,
            "statuses": [{"source": "yahoo", "ok": True, "count": 0, "candidateCount": 1, "bodyMissingCount": 1}],
        }
        first, _ = service.record_news_collection(dict(result))
        second, event = service.record_news_collection(dict(result))
        self.assertEqual(first.state, "idle")
        self.assertEqual(second.state, "degraded")
        self.assertEqual(second.consecutive_zero_runs, 2)
        self.assertIsNotNone(event)

    def test_relevance_filtering_only_remains_idle_after_repeated_empty_cycles(self):
        health = evaluate_news_collection_health({
            "status": "ok",
            "targetCount": 3,
            "fetchedCount": 0,
            "savedCount": 0,
            "statuses": [{
                "source": "yahoo_search",
                "ok": True,
                "count": 0,
                "candidateCount": 11,
                "preliminaryRejectedCount": 11,
                "bodyMissingCount": 0,
            }],
        }, previous={"state": "idle", "consecutiveZeroRuns": 50}, blocked_warning_streak=3)

        self.assertEqual("idle", health.state)
        self.assertEqual("candidates-filtered", health.reason_code)
        self.assertFalse(health.alert_required)


if __name__ == "__main__":
    unittest.main()
