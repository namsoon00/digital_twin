import unittest
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.investment_calendar_service import InvestmentCalendarService
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.events import INVESTMENT_CALENDAR_EVENT_SAVED, ONTOLOGY_REASONING_REQUESTED
from digital_twin.domain.investment_calendar import InvestmentCalendarEvent, utc_iso
from digital_twin.domain.message_types import INVESTMENT_CALENDAR_REMINDER


class MemoryCalendarStore:
    def __init__(self):
        self.events = {}

    def upsert(self, event):
        self.events[event.event_id] = event
        return event

    def get(self, event_id):
        return self.events.get(event_id)

    def delete(self, event_id):
        return bool(self.events.pop(event_id, None))

    def list(self, from_at="", to_at="", status="", symbol="", event_type="", limit=200):
        items = list(self.events.values())
        if status:
            items = [item for item in items if item.status == status]
        if symbol:
            items = [item for item in items if symbol.upper() in item.symbols]
        if event_type:
            items = [item for item in items if item.event_type == event_type]
        return sorted(items, key=lambda item: item.starts_at)[: int(limit or 200)]

    def reminder_candidates(self, now_at="", lookback_minutes=180):
        return [item for item in self.events.values() if item.status == "active"]

    def summary(self):
        return {"total": len(self.events), "upcoming": len(self.events), "nextStartsAt": ""}


class MemoryQueue:
    def __init__(self):
        self.jobs = []
        self.dedupe = set()

    def enqueue(self, job):
        if job.dedupe_key in self.dedupe:
            return False
        self.dedupe.add(job.dedupe_key)
        self.jobs.append(job)
        return True


class MemoryPublisher:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class InvestmentCalendarServiceTests(unittest.TestCase):
    def account(self):
        return AccountConfig(
            "main",
            "메인",
            "toss",
            "https://example.test",
            "",
            "",
            "",
            ["AAPL"],
        )

    def service(self, store=None, queue=None, publisher=None):
        return InvestmentCalendarService(
            repository=store or MemoryCalendarStore(),
            account_repository=SimpleNamespace(load_all=lambda: [self.account()]),
            notification_queue=queue or MemoryQueue(),
            settings={"investmentCalendarReminderLookbackMinutes": "180"},
            event_publisher=publisher or MemoryPublisher(),
        )

    def test_save_event_normalizes_time_and_requests_ontology_for_symbol_event(self):
        publisher = MemoryPublisher()
        store = MemoryCalendarStore()
        service = self.service(store=store, publisher=publisher)

        result = service.save_event({
            "title": "AAPL 실적 발표",
            "eventType": "earnings",
            "startsAt": "2026-07-14T09:00",
            "timezone": "Asia/Seoul",
            "importance": 80,
            "symbols": ["aapl"],
            "reminderOffsetsMinutes": [60, 0],
        })

        saved = result["event"]
        self.assertEqual("AAPL", saved["symbols"][0])
        self.assertEqual("2026-07-14T00:00:00Z", saved["startsAt"])
        names = [event.name for event in publisher.events]
        self.assertIn(INVESTMENT_CALENDAR_EVENT_SAVED, names)
        self.assertIn(ONTOLOGY_REASONING_REQUESTED, names)

    def test_due_reminder_enqueues_calendar_notification_once(self):
        now_at = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)
        event = InvestmentCalendarEvent.from_payload({
            "eventId": "event-1",
            "title": "FOMC 점검",
            "eventType": "centralBank",
            "startsAt": utc_iso(now_at + timedelta(hours=1)),
            "timezone": "UTC",
            "importance": 90,
            "markets": ["NASDAQ"],
            "reminderOffsetsMinutes": [60],
        })
        store = MemoryCalendarStore()
        store.upsert(event)
        queue = MemoryQueue()
        service = self.service(store=store, queue=queue)

        first = service.enqueue_due_reminders(now_at=now_at)
        second = service.enqueue_due_reminders(now_at=now_at)

        self.assertEqual(1, first["queuedCount"])
        self.assertEqual(0, second["queuedCount"])
        self.assertEqual(1, len(queue.jobs))
        job = queue.jobs[0]
        self.assertEqual(INVESTMENT_CALENDAR_REMINDER, job.message_type)
        self.assertEqual("event-1", job.context["eventId"])
        self.assertEqual(60, job.context["reminderOffsetMinutes"])


if __name__ == "__main__":
    unittest.main()
