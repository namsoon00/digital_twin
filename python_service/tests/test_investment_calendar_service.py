import unittest
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.investment_calendar_extraction_service import InvestmentCalendarExtractionService
from digital_twin.application.investment_calendar_service import InvestmentCalendarService
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.events import (
    DomainEvent,
    INVESTMENT_CALENDAR_EVENT_SAVED,
    ONTOLOGY_REASONING_REQUESTED,
    RESEARCH_EVIDENCE_COLLECTED,
)
from digital_twin.domain.investment_calendar import InvestmentCalendarEvent, event_type_label, utc_iso
from digital_twin.domain.message_types import INVESTMENT_CALENDAR_REMINDER
from digital_twin.domain.notification_ai_gate_validation import build_notification_ai_gate_prompt


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
        self.assertIn("투자 영향", job.text)
        self.assertIn("확인할 것", job.text)
        self.assertIn("계정 성향", job.text)
        self.assertIn("균형형", job.text)
        self.assertIn("centralBank", job.context["eventType"])
        self.assertIn("investmentImpact", job.context)
        self.assertIn("watchItems", job.context)
        self.assertEqual("balanced", job.context["investmentStrategyProfile"])
        self.assertEqual("균형형", job.context["investmentStrategyProfileLabel"])
        self.assertIn("investmentStrategyGuidance", job.context)
        self.assertTrue(job.context["watchItems"])
        prompt = build_notification_ai_gate_prompt(job.context)
        self.assertIn("계정의 투자 성향은 균형형", prompt)

    def test_research_evidence_adr_listing_auto_registers_calendar_event(self):
        store = MemoryCalendarStore()
        extractor = InvestmentCalendarExtractionService(
            calendar_service=self.service(store=store),
            account_repository=SimpleNamespace(load_all=lambda: [self.account()]),
        )
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "changedItems": [
                    {
                        "evidenceId": "research:AAPL:sec:f6",
                        "symbol": "AAPL",
                        "kind": "filing",
                        "source": "SEC EDGAR",
                        "title": "Apple files F-6 for ADR listing on NYSE on 2026-08-20",
                        "summary": "American depositary receipt listing schedule confirmed.",
                        "url": "https://www.sec.gov/example/f6",
                        "publishedAt": "2026-07-15T00:00:00Z",
                        "observedAt": "2026-07-15T00:10:00Z",
                        "materialityScore": 91,
                    }
                ]
            },
        )

        result = extractor.handle(event)

        self.assertEqual(1, result["candidateCount"])
        self.assertEqual(1, result["savedCount"])
        saved = next(iter(store.events.values()))
        self.assertEqual("adrListing", saved.event_type)
        self.assertEqual("ADR/GDR 상장", event_type_label(saved.event_type))
        self.assertEqual(["AAPL"], saved.symbols)
        self.assertEqual(["main"], saved.account_ids)
        self.assertEqual("active", saved.status)
        self.assertTrue(saved.payload["autoDetected"])
        self.assertEqual("research:AAPL:sec:f6", saved.payload["sourceEvidenceId"])

    def test_research_evidence_without_event_date_is_not_registered_by_default(self):
        store = MemoryCalendarStore()
        extractor = InvestmentCalendarExtractionService(
            calendar_service=self.service(store=store),
            account_repository=SimpleNamespace(load_all=lambda: [self.account()]),
        )
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "changedItems": [
                    {
                        "evidenceId": "research:AAPL:sec:f6-undated",
                        "symbol": "AAPL",
                        "kind": "filing",
                        "source": "SEC EDGAR",
                        "title": "Apple considers ADR listing in overseas market",
                        "summary": "American depositary receipt plan is being reviewed.",
                        "url": "https://www.sec.gov/example/f6-undated",
                        "publishedAt": "2026-07-15T00:00:00Z",
                        "observedAt": "2026-07-15T00:10:00Z",
                        "materialityScore": 91,
                    }
                ]
            },
        )

        result = extractor.handle(event)

        self.assertEqual(0, result["candidateCount"])
        self.assertEqual(0, result["savedCount"])
        self.assertEqual({}, store.events)

    def test_adr_listing_reminder_message_includes_event_guidance_and_strategy_profile(self):
        now_at = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
        event = InvestmentCalendarEvent.from_payload({
            "eventId": "adr-event-1",
            "title": "ADR/GDR 상장: SK files F-6",
            "eventType": "adrListing",
            "startsAt": utc_iso(now_at + timedelta(hours=1)),
            "timezone": "UTC",
            "importance": 92,
            "symbols": ["SK"],
            "markets": ["NYSE"],
            "reminderOffsetsMinutes": [60],
        })
        store = MemoryCalendarStore()
        store.upsert(event)
        queue = MemoryQueue()
        service = self.service(store=store, queue=queue)

        result = service.enqueue_due_reminders(now_at=now_at)

        self.assertEqual(1, result["queuedCount"])
        job = queue.jobs[0]
        self.assertIn("ADR/GDR 상장", job.text)
        self.assertIn("투자 영향", job.text)
        self.assertIn("확인할 것", job.text)
        self.assertIn("원주/ADR 교환비율", job.text)
        self.assertIn("계정 성향", job.text)
        self.assertIn("균형형", job.text)
        self.assertEqual("adrListing", job.context["eventType"])
        self.assertEqual("ADR/GDR 상장", job.context["eventTypeLabel"])
        self.assertIn("원주/ADR 교환비율과 수수료", job.context["watchItems"])
        self.assertEqual("balanced", job.context["investmentStrategyProfile"])


if __name__ == "__main__":
    unittest.main()
