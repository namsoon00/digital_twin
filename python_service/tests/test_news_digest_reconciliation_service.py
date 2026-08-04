import unittest
from datetime import datetime, timezone

from digital_twin.application.news_digest_service import NewsDigestEventReconciler
from digital_twin.domain.events import DomainEvent, RESEARCH_EVIDENCE_COLLECTED


class MemoryCursorStore:
    def __init__(self, payload=None):
        self.payload = dict(payload or {})

    def load(self):
        return dict(self.payload)

    def replace(self, payload):
        self.payload = dict(payload or {})


class MemoryEventReader:
    def __init__(self, events):
        self.events = list(events or [])
        self.calls = []

    def research_evidence_events_after(self, after_occurred_at="", after_event_id="", limit=100):
        self.calls.append((after_occurred_at, after_event_id, limit))
        rows = [
            event for event in self.events
            if event.occurred_at > after_occurred_at
            or (event.occurred_at == after_occurred_at and event.event_id > after_event_id)
        ]
        return rows[:limit]


class RecordingEnqueuer:
    def __init__(self, failing_event_id=""):
        self.events = []
        self.failing_event_id = failing_event_id

    def handle(self, event):
        if event.event_id == self.failing_event_id:
            raise RuntimeError("notification enqueue unavailable")
        self.events.append(event.event_id)
        return 1


def news_event(event_id, occurred_at):
    return DomainEvent(
        name=RESEARCH_EVIDENCE_COLLECTED,
        aggregate_id="news:test",
        payload={"savedCount": 1},
        occurred_at=occurred_at,
        event_id=event_id,
    )


class NewsDigestEventReconcilerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)

    def test_replays_events_in_order_and_advances_cursor(self):
        events = [
            news_event("event-a", "2026-08-04T08:55:00Z"),
            news_event("event-b", "2026-08-04T08:56:00Z"),
        ]
        reader = MemoryEventReader(events)
        enqueuer = RecordingEnqueuer()
        cursor = MemoryCursorStore()
        service = NewsDigestEventReconciler(
            reader,
            enqueuer,
            cursor,
            initial_lookback_minutes=10,
            now_provider=lambda: self.now,
        )

        result = service.run_once()
        second = service.run_once()

        self.assertEqual(["event-a", "event-b"], enqueuer.events)
        self.assertEqual(2, result["processedCount"])
        self.assertEqual(2, result["queuedCount"])
        self.assertEqual("event-b", cursor.payload["lastEventId"])
        self.assertEqual(0, second["processedCount"])

    def test_failed_event_does_not_advance_cursor(self):
        events = [
            news_event("event-a", "2026-08-04T08:55:00Z"),
            news_event("event-b", "2026-08-04T08:56:00Z"),
        ]
        cursor = MemoryCursorStore({
            "lastOccurredAt": "2026-08-04T08:54:00Z",
            "lastEventId": "event-before",
        })
        service = NewsDigestEventReconciler(
            MemoryEventReader(events),
            RecordingEnqueuer(failing_event_id="event-b"),
            cursor,
            now_provider=lambda: self.now,
        )

        with self.assertRaisesRegex(RuntimeError, "notification enqueue unavailable"):
            service.run_once()

        self.assertEqual("event-before", cursor.payload["lastEventId"])
        self.assertEqual("2026-08-04T08:54:00Z", cursor.payload["lastOccurredAt"])

    def test_initial_scan_is_bounded_to_recent_events(self):
        reader = MemoryEventReader([])
        cursor = MemoryCursorStore()
        service = NewsDigestEventReconciler(
            reader,
            RecordingEnqueuer(),
            cursor,
            initial_lookback_minutes=10,
            now_provider=lambda: self.now,
        )

        result = service.run_once()

        self.assertEqual("2026-08-04T08:50:00Z", reader.calls[0][0])
        self.assertEqual("idle", result["status"])
        self.assertEqual("2026-08-04T08:50:00Z", cursor.payload["lastOccurredAt"])


if __name__ == "__main__":
    unittest.main()
