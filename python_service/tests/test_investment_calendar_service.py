import unittest
import sys
import io
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.investment_calendar_extraction_service import InvestmentCalendarExtractionService
from digital_twin.application.investment_calendar_candidate_service import InvestmentCalendarCandidateService
from digital_twin.application.investment_calendar_discovery_service import InvestmentCalendarDiscoveryService
from digital_twin.application.investment_calendar_research_service import InvestmentCalendarResearchRecommendationService
from digital_twin.application.investment_calendar_service import InvestmentCalendarService
from digital_twin.application.official_calendar_sync_service import OfficialCalendarSyncService
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.events import (
    DomainEvent,
    INVESTMENT_CALENDAR_EVENT_SAVED,
    ONTOLOGY_REASONING_REQUESTED,
    RESEARCH_EVIDENCE_COLLECTED,
)
from digital_twin.domain.investment_calendar import InvestmentCalendarEvent, due_reminders_for_event, event_type_label, utc_iso
from digital_twin.domain.investment_calendar_candidates import InvestmentCalendarReviewCandidate
from digital_twin.domain.investment_calendar_extraction import calendar_candidate_from_research_item, calendar_candidate_sets_from_research_items
from digital_twin.domain.investment_research import ResearchEvidence
from digital_twin.infrastructure.bok_calendar_source import BokPolicyDecisionCalendarSource, parse_bok_policy_decision_events
from digital_twin.infrastructure.opendart_calendar_source import (
    OpenDartEarningsCalendarSource,
    parse_opendart_corp_codes,
    parse_opendart_earnings_event,
)
from digital_twin.infrastructure.samsung_ir_calendar_source import parse_samsung_ir_earnings_events
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


class MemoryCandidateStore:
    def __init__(self):
        self.candidates = {}

    def upsert(self, payload):
        candidate = InvestmentCalendarReviewCandidate.from_payload(payload)
        self.candidates[candidate.candidate_id] = candidate
        return True

    def get(self, candidate_id):
        return self.candidates.get(candidate_id)

    def list(self, status="pending", limit=100, offset=0):
        items = list(self.candidates.values())
        if status:
            items = [item for item in items if item.status == status]
        return items[int(offset or 0): int(offset or 0) + int(limit or 100)]

    def count(self, status="pending"):
        items = list(self.candidates.values())
        if status:
            items = [item for item in items if item.status == status]
        return len(items)

    def mark_status(self, candidate_id, status, review_note=""):
        candidate = self.candidates.get(candidate_id)
        if not candidate:
            return None
        candidate.status = status
        candidate.review_note = review_note
        self.candidates[candidate_id] = candidate
        return candidate

    def summary(self):
        result = {}
        for candidate in self.candidates.values():
            result[candidate.status] = result.get(candidate.status, 0) + 1
        return result

    def feedback_summary(self):
        result = {}
        for candidate in self.candidates.values():
            bucket = result.setdefault(candidate.event_type, {"accepted": 0, "rejected": 0})
            if candidate.status == "registered":
                bucket["accepted"] += 1
            elif candidate.status == "rejected":
                bucket["rejected"] += 1
        return result


class MemorySymbolStore:
    def __init__(self, items=None):
        self.items = {
            str(item.get("symbol") or "").upper(): dict(item)
            for item in items or []
        }

    def get(self, symbol, market=""):
        return self.items.get(str(symbol or "").upper())


class MemoryResearchEvidenceStore:
    def __init__(self, items=None):
        self.items = list(items or [])

    def latest(self, symbol="", kind="", limit=50):
        rows = list(self.items)
        if symbol:
            rows = [item for item in rows if str(item.symbol or "").upper() == str(symbol or "").upper()]
        if kind:
            rows = [item for item in rows if item.kind == kind]
        return rows[: int(limit or 50)]

    def upsert_many(self, items):
        by_id = {item.evidence_id: item for item in self.items}
        saved = 0
        for item in items or []:
            if not isinstance(item, ResearchEvidence):
                continue
            by_id[item.evidence_id] = item
            saved += 1
        self.items = list(by_id.values())
        return saved


class MemoryNewsCollectionRunner:
    def __init__(self):
        self.calls = 0

    def run_once(self, force=False):
        self.calls += 1
        return {
            "status": "ok",
            "targetCount": 1,
            "fetchedCount": 1,
            "savedCount": 0,
            "changedCount": 0,
            "materialChangedCount": 0,
            "symbols": ["AAPL"],
            "providers": ["memory"],
        }


class MemoryCalendarDiscoveryGateway:
    def __init__(self, items=None, statuses=None):
        self.items = list(items or [])
        self.statuses = list(statuses or [])
        self.calls = []

    def collect_for_target(self, target, source_types=None):
        self.calls.append({
            "symbol": target.normalized_symbol(),
            "sourceTypes": list(source_types or []),
        })
        rows = [item for item in self.items if item.symbol == target.normalized_symbol()]
        statuses = self.statuses or [{
            "source": "memory-calendar-data",
            "symbol": target.normalized_symbol(),
            "ok": True,
            "count": len(rows),
        }]
        return rows, statuses


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

    def service(self, store=None, queue=None, publisher=None, symbol_store=None):
        return InvestmentCalendarService(
            repository=store or MemoryCalendarStore(),
            account_repository=SimpleNamespace(load_all=lambda: [self.account()]),
            notification_queue=queue or MemoryQueue(),
            settings={"investmentCalendarReminderLookbackMinutes": "180"},
            event_publisher=publisher or MemoryPublisher(),
            symbol_repository=symbol_store,
        )

    def test_calendar_read_model_uses_company_name_and_keeps_symbol_code(self):
        store = MemoryCalendarStore()
        store.upsert(InvestmentCalendarEvent.from_payload({
            "eventId": "samsung-cnt-earnings",
            "title": "028260 실적 발표 예정",
            "eventType": "earnings",
            "startsAt": "2026-10-27T09:00:00+09:00",
            "symbols": ["028260"],
            "markets": ["KOSPI"],
        }))
        symbol_store = MemorySymbolStore([{
            "symbol": "028260",
            "name": "삼성물산",
            "market": "KOSPI",
            "sector": "기타 전문 도매업",
        }])

        result = self.service(store=store, symbol_store=symbol_store).list_events({
            "from": "2026-01-01T00:00:00Z",
            "to": "2026-12-31T23:59:59Z",
        })

        self.assertEqual("삼성물산 실적 발표 예정", result["events"][0]["displayTitle"])
        self.assertEqual("삼성물산", result["events"][0]["displayName"])
        self.assertEqual("028260", result["events"][0]["symbolDetails"][0]["symbol"])

    def scheduled_yfinance_evidence(self):
        return ResearchEvidence(
            evidence_id="research:AAPL:yfinance-calendar",
            symbol="AAPL",
            kind="financial-fact",
            source="yfinance",
            title="yfinance 종합 데이터",
            summary="캘린더 데이터",
            published_at="2026-07-20T00:00:00Z",
            observed_at="2026-07-20T00:00:00Z",
            raw_payload={
                "provider": "yfinance",
                "sourceKind": "unofficial-yahoo-finance-wrapper",
                "calendar": {
                    "Earnings Date": ["2026-08-05"],
                    "Ex-Dividend Date": "2026-08-10",
                    "Dividend Date": "2026-08-14",
                },
                "sourceTrustState": "standard",
                "materialityState": "context",
                "dataState": "sufficient",
                "validationState": "conditional",
            },
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

    def test_tentative_event_preserves_disabled_reminders_and_is_not_actionable(self):
        now_at = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
        event = InvestmentCalendarEvent.from_payload({
            "eventId": "tentative-earnings-1",
            "title": "AAPL 실적 발표 예정",
            "eventType": "earnings",
            "startsAt": utc_iso(now_at + timedelta(hours=1)),
            "timezone": "UTC",
            "status": "tentative",
            "symbols": ["AAPL"],
            "reminderOffsetsMinutes": [],
        })

        self.assertEqual([], event.reminder_offsets_minutes)
        self.assertFalse(event.active())
        self.assertEqual([], due_reminders_for_event(event, now_at=now_at))

    def test_unverified_automatic_time_is_never_actionable(self):
        now_at = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
        event = InvestmentCalendarEvent.from_payload({
            "eventId": "estimated-earnings-1",
            "title": "005930 실적 발표 예정",
            "eventType": "earnings",
            "startsAt": utc_iso(now_at + timedelta(hours=1)),
            "timezone": "UTC",
            "allDay": False,
            "status": "active",
            "symbols": ["005930"],
            "markets": ["US"],
            "reminderOffsetsMinutes": [60],
            "payload": {
                "autoDetected": True,
                "officialSource": False,
                "scheduleState": "estimated",
                "reviewRequired": True,
            },
        })

        self.assertEqual(["KOSPI"], event.markets)
        self.assertFalse(event.reminder_eligible())
        self.assertEqual([], due_reminders_for_event(event, now_at=now_at))

    def test_all_day_event_round_trip_preserves_local_date(self):
        event = InvestmentCalendarEvent.from_payload({
            "eventId": "all-day-us-event",
            "title": "AAPL 실적 발표일 후보",
            "eventType": "earnings",
            "startsAt": "2026-08-20",
            "timezone": "America/New_York",
            "allDay": True,
            "status": "tentative",
            "symbols": ["AAPL"],
            "markets": ["US"],
        })

        restored = InvestmentCalendarEvent.from_dict(event.to_dict())

        self.assertEqual(event.starts_at, restored.starts_at)
        self.assertEqual("2026-08-20", restored.to_dict()["localDate"])

    def test_list_events_hides_candidate_only_rows_by_default(self):
        now_at = datetime.now(timezone.utc)
        store = MemoryCalendarStore()
        store.upsert(InvestmentCalendarEvent.from_payload({
            "eventId": "confirmed-event",
            "title": "확정 일정",
            "eventType": "earnings",
            "startsAt": utc_iso(now_at + timedelta(days=1)),
            "timezone": "UTC",
            "status": "active",
        }))
        store.upsert(InvestmentCalendarEvent.from_payload({
            "eventId": "candidate-only-event",
            "title": "추정 후보",
            "eventType": "earnings",
            "startsAt": utc_iso(now_at + timedelta(days=2)),
            "timezone": "UTC",
            "status": "candidateOnly",
        }))

        result = self.service(store=store).list_events({
            "from": utc_iso(now_at),
            "to": utc_iso(now_at + timedelta(days=3)),
        })

        self.assertEqual(["confirmed-event"], [item["eventId"] for item in result["events"]])
        self.assertEqual(1, result["summary"]["total"])
        self.assertEqual(2, result["summary"]["storedTotal"])

    def test_list_events_reports_the_next_future_schedule_only(self):
        now_at = datetime.now(timezone.utc)
        store = MemoryCalendarStore()
        store.upsert(InvestmentCalendarEvent.from_payload({
            "eventId": "calendar-past",
            "title": "지난 일정",
            "eventType": "macro",
            "startsAt": utc_iso(now_at - timedelta(days=1)),
            "timezone": "UTC",
        }))
        store.upsert(InvestmentCalendarEvent.from_payload({
            "eventId": "calendar-future",
            "title": "다음 일정",
            "eventType": "earnings",
            "startsAt": utc_iso(now_at + timedelta(days=1)),
            "timezone": "UTC",
        }))

        result = self.service(store=store).list_events({
            "from": utc_iso(now_at - timedelta(days=2)),
            "to": utc_iso(now_at + timedelta(days=2)),
        })

        self.assertEqual(1, result["summary"]["upcoming"])
        self.assertEqual(utc_iso(now_at + timedelta(days=1)), result["summary"]["nextStartsAt"])

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
                        "materialityState": "material",
                        "sourceTrustState": "trusted",
                        "dataState": "sufficient",
                        "validationState": "ready",
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

    def test_verified_disclosure_analysis_supplies_calendar_date(self):
        evidence = {
            "evidenceId": "research:005930:dart:buyback",
            "symbol": "005930",
            "kind": "disclosure",
            "source": "OpenDART",
            "title": "자기주식 취득 결정",
            "summary": "회사가 자기주식 취득을 결의했습니다.",
            "url": "https://dart.fss.or.kr/example",
            "publishedAt": "2026-08-25T00:00:00Z",
            "observedAt": "2026-08-25T00:10:00Z",
            "materialityState": "material",
            "sourceTrustState": "trusted",
            "dataState": "sufficient",
            "validationState": "ready",
            "payload": {
                "documentVerified": True,
                "analysisReady": True,
                "disclosureAnalysis": {
                    "confirmedFacts": ["자기주식 취득은 2026-08-26부터 시작합니다."],
                    "documentDates": ["2026-08-26"],
                },
            },
        }

        candidates = calendar_candidate_sets_from_research_items([evidence])

        self.assertEqual(1, len(candidates["ready"]))
        candidate = candidates["ready"][0]
        self.assertEqual("capitalMarketEvent", candidate.event_type)
        self.assertEqual("2026-08-26", candidate.payload["eventLocalDate"])
        self.assertTrue(candidate.payload["officialSource"])

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
                        "materialityState": "material",
                        "sourceTrustState": "trusted",
                        "dataState": "sufficient",
                        "validationState": "ready",
                    }
                ]
            },
        )

        result = extractor.handle(event)

        self.assertEqual(0, result["candidateCount"])
        self.assertEqual(0, result["savedCount"])
        self.assertEqual({}, store.events)

    def test_research_evidence_without_event_date_is_saved_as_review_candidate(self):
        store = MemoryCalendarStore()
        candidate_store = MemoryCandidateStore()
        extractor = InvestmentCalendarExtractionService(
            calendar_service=self.service(store=store),
            account_repository=SimpleNamespace(load_all=lambda: [self.account()]),
            candidate_repository=candidate_store,
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
                        "title": "Apple files F-6 for ADR listing",
                        "summary": "American depositary receipt plan is being reviewed.",
                        "url": "https://www.sec.gov/example/f6-undated",
                        "publishedAt": "2026-07-15T00:00:00Z",
                        "observedAt": "2026-07-15T00:10:00Z",
                        "materialityState": "material",
                        "sourceTrustState": "trusted",
                        "dataState": "sufficient",
                        "validationState": "ready",
                    }
                ]
            },
        )

        result = extractor.handle(event)

        self.assertEqual(0, result["candidateCount"])
        self.assertEqual(0, result["savedCount"])
        self.assertEqual(1, result["reviewCandidateCount"])
        self.assertEqual(1, result["storedReviewCandidateCount"])
        candidate = next(iter(candidate_store.candidates.values()))
        self.assertEqual("pending", candidate.status)
        self.assertEqual("missingDate", candidate.review_reason)
        self.assertEqual("adrListing", candidate.event_type)
        self.assertEqual("", candidate.starts_at)
        self.assertEqual({}, store.events)

    def test_candidate_list_hides_undated_or_unstructured_automatic_candidates(self):
        store = MemoryCandidateStore()
        calendar = self.service(store=MemoryCalendarStore())
        candidate_service = InvestmentCalendarCandidateService(store, calendar)
        store.upsert({
            "candidateId": "undated-auto",
            "proposedEventId": "event-undated-auto",
            "title": "제목 키워드 후보",
            "eventType": "listing",
            "status": "pending",
            "payload": {"autoDetected": True, "officialSource": False},
        })
        store.upsert({
            "candidateId": "dated-auto",
            "proposedEventId": "event-dated-auto",
            "title": "공식 일정 후보",
            "eventType": "earnings",
            "startsAt": "2026-08-20T00:00:00Z",
            "status": "pending",
            "payload": {
                "autoDetected": True,
                "officialSource": False,
                "structuredEventType": "earnings",
                "dateSource": "calendar.Earnings Date",
            },
        })

        result = candidate_service.list_candidates({"status": "pending", "limit": 20})

        self.assertEqual(1, result["total"])
        self.assertEqual(["dated-auto"], [item["candidateId"] for item in result["candidates"]])
        self.assertEqual("undated-auto", result["hidden"][0]["candidateId"])

    def test_non_official_keyword_news_does_not_create_automatic_candidate(self):
        candidate = calendar_candidate_from_research_item({
            "evidenceId": "research:AAPL:yahoo:keyword-hit",
            "symbol": "AAPL",
            "kind": "news",
            "source": "Yahoo Finance",
            "title": "Apple is added to a regional stock index after earnings",
            "summary": "A news article mentions an index move but provides no official schedule.",
            "url": "https://finance.yahoo.com/example/keyword-hit",
            "publishedAt": "2026-07-15T00:00:00Z",
        }, include_review=True)

        self.assertIsNone(candidate)

    def test_news_source_name_containing_ir_is_not_an_official_calendar_source(self):
        candidate = calendar_candidate_from_research_item({
            "evidenceId": "research:AAPL:wire:capital-raise",
            "symbol": "AAPL",
            "kind": "news",
            "source": "PR Newswire",
            "title": "Apple capital raise expected on 2026-08-20",
            "summary": "A news article speculates about financing.",
            "url": "https://www.prnewswire.com/ir/example-capital-raise",
            "publishedAt": "2026-07-15T00:00:00Z",
        }, include_review=True)

        self.assertIsNone(candidate)

    def test_review_candidate_approval_registers_calendar_event_and_feedback(self):
        calendar_store = MemoryCalendarStore()
        candidate_store = MemoryCandidateStore()
        candidate_store.upsert({
            "candidateId": "candidate-1",
            "proposedEventId": "auto-special-event-review-1",
            "title": "ADR/GDR 상장: Apple files F-6",
            "eventType": "adrListing",
            "startsAt": "",
            "importance": 92,
            "readinessState": "needs-review",
            "symbols": ["AAPL"],
            "markets": ["NYSE"],
            "source": "SEC EDGAR",
            "sourceUrl": "https://www.sec.gov/example/f6",
            "sourceEvidenceId": "research:AAPL:sec:f6-review",
            "payload": {"sourceParser": "sec-edgar"},
        })
        service = InvestmentCalendarCandidateService(
            candidate_repository=candidate_store,
            calendar_service=self.service(store=calendar_store),
        )

        result = service.approve_candidate("candidate-1", {"startsAt": "2026-08-20T09:00", "reviewNote": "confirmed"})

        self.assertEqual("registered", result["candidate"]["status"])
        saved = next(iter(calendar_store.events.values()))
        self.assertEqual("adrListing", saved.event_type)
        self.assertEqual("2026-08-20T00:00:00Z", saved.starts_at)
        self.assertEqual({"adrListing": {"accepted": 1, "rejected": 0}}, candidate_store.feedback_summary())

    def test_candidate_list_uses_server_pagination_metadata(self):
        calendar_store = MemoryCalendarStore()
        candidate_store = MemoryCandidateStore()
        for index in range(7):
            candidate_store.upsert({
                "candidateId": "candidate-" + str(index),
                "proposedEventId": "event-" + str(index),
                "title": "AI 추천 후보 " + str(index),
                "eventType": "indexInclusion",
                "startsAt": "2026-08-" + str(10 + index).zfill(2),
                "importance": 80,
                "readinessState": "needs-review",
            })
        service = InvestmentCalendarCandidateService(
            candidate_repository=candidate_store,
            calendar_service=self.service(store=calendar_store),
        )

        result = service.list_candidates({"status": "pending", "page": "1", "pageSize": "3"})

        self.assertEqual(3, len(result["candidates"]))
        self.assertEqual(7, result["total"])
        self.assertEqual(1, result["pageInfo"]["page"])
        self.assertEqual(3, result["pageInfo"]["pageSize"])
        self.assertEqual(3, result["pageInfo"]["offset"])
        self.assertTrue(result["pageInfo"]["hasPrev"])
        self.assertTrue(result["pageInfo"]["hasNext"])

    def test_candidate_read_model_uses_company_name_and_keeps_symbol_code(self):
        candidate_store = MemoryCandidateStore()
        candidate_store.upsert({
            "candidateId": "candidate-028260",
            "proposedEventId": "event-028260",
            "title": "028260 실적 발표 예정",
            "eventType": "earnings",
            "startsAt": "2026-10-27T00:00:00Z",
            "symbols": ["028260"],
            "markets": ["KOSPI"],
        })
        service = InvestmentCalendarCandidateService(
            candidate_repository=candidate_store,
            calendar_service=self.service(),
            symbol_repository=MemorySymbolStore([{
                "symbol": "028260",
                "name": "삼성물산",
                "market": "KOSPI",
            }]),
        )

        result = service.list_candidates({"status": "pending", "limit": 20})

        self.assertEqual("삼성물산 실적 발표 예정", result["candidates"][0]["displayTitle"])
        self.assertEqual("삼성물산", result["candidates"][0]["displayName"])
        self.assertEqual("028260", result["candidates"][0]["symbolDetails"][0]["symbol"])

    def test_ai_research_recommendation_saves_pending_review_candidate_without_registering_event(self):
        calendar_store = MemoryCalendarStore()
        candidate_store = MemoryCandidateStore()
        runner = MemoryNewsCollectionRunner()
        evidence_store = MemoryResearchEvidenceStore([
            ResearchEvidence(
                evidence_id="research:AAPL:sec:f6-ai",
                symbol="AAPL",
                kind="filing",
                source="SEC EDGAR",
                title="Apple files F-6 for ADR listing on NYSE on 2026-08-20",
                summary="American depositary receipt listing schedule confirmed.",
                url="https://www.sec.gov/example/f6-ai",
                published_at="2026-07-15T00:00:00Z",
                observed_at="2026-07-15T00:10:00Z",
                raw_payload={
                    "form": "F-6",
                    "eventDate": "2026-08-20",
                    "materialityState": "material",
                    "sourceTrustState": "trusted",
                    "dataState": "sufficient",
                    "validationState": "ready",
                },
            )
        ])
        service = InvestmentCalendarResearchRecommendationService(
            candidate_repository=candidate_store,
            evidence_repository=evidence_store,
            account_repository=SimpleNamespace(load_all=lambda: [self.account()]),
            news_collection_runner_factory=lambda: runner,
            settings={},
        )

        result = service.recommend({"symbol": "AAPL", "runCollection": True})

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, runner.calls)
        self.assertEqual(1, result["candidateCount"])
        self.assertEqual(1, result["storedCandidateCount"])
        self.assertEqual({}, calendar_store.events)
        candidate = next(iter(candidate_store.candidates.values()))
        self.assertEqual("pending", candidate.status)
        self.assertEqual("aiResearchRecommended", candidate.review_reason)
        self.assertEqual("adrListing", candidate.event_type)
        self.assertEqual("2026-08-20T00:00:00Z", candidate.starts_at)
        self.assertTrue(candidate.payload["aiResearchRecommended"])
        self.assertEqual("ai-research-calendar-recommender-v1", candidate.payload["detector"])
        self.assertIn("investmentImpact", candidate.payload)
        self.assertIn("watchItems", candidate.payload)
        self.assertIn("positiveScenario", candidate.payload)
        self.assertEqual(["main"], candidate.account_ids)

    def test_structured_provider_calendar_creates_dated_review_candidates(self):
        evidence = self.scheduled_yfinance_evidence().to_dict()

        candidates = calendar_candidate_sets_from_research_items([evidence])

        self.assertEqual(0, len(candidates["ready"]))
        self.assertEqual(3, len(candidates["review"]))
        self.assertEqual({"earnings", "dividend"}, {item.event_type for item in candidates["review"]})
        self.assertTrue(all(item.starts_at for item in candidates["review"]))
        self.assertTrue(all(item.review_reason == "sourceNeedsVerification" for item in candidates["review"]))
        self.assertTrue(all(item.payload["scheduleState"] == "estimated" for item in candidates["review"]))
        self.assertTrue(all(item.payload["timeState"] == "estimatedDefault" for item in candidates["review"]))
        self.assertTrue(all(item.payload["eventLocalTime"] == "09:00" for item in candidates["review"]))
        self.assertTrue(all(not item.all_day for item in candidates["review"]))

    def test_date_only_calendar_candidate_uses_configured_default_time_and_timezone(self):
        evidence = self.scheduled_yfinance_evidence().to_dict()

        candidates = calendar_candidate_sets_from_research_items(
            [evidence],
            display_timezone="America/New_York",
            default_time="08:30",
        )

        earnings = next(item for item in candidates["review"] if item.event_type == "earnings")
        self.assertEqual("2026-08-05T12:30:00Z", earnings.starts_at)
        self.assertEqual("08:30", earnings.payload["eventLocalTime"])
        self.assertEqual("estimatedDefault", earnings.payload["timeState"])
        self.assertEqual("settings.investmentCalendarCandidateDefaultTime", earnings.payload["timeSource"])

    def test_calendar_candidate_preserves_provider_timestamp(self):
        evidence = self.scheduled_yfinance_evidence().to_dict()
        evidence["payload"]["calendar"] = {
            "Earnings Date": ["2026-08-05T16:30:00-04:00"],
        }

        candidates = calendar_candidate_sets_from_research_items(
            [evidence],
            display_timezone="Asia/Seoul",
            default_time="09:00",
        )

        earnings = candidates["review"][0]
        self.assertEqual("2026-08-05T20:30:00Z", earnings.starts_at)
        self.assertEqual("05:30", earnings.payload["eventLocalTime"])
        self.assertEqual("sourceProvided", earnings.payload["timeState"])
        self.assertEqual("calendar.Earnings Date", earnings.payload["timeSource"])

    def test_structured_provider_calendar_preserves_target_market(self):
        evidence = self.scheduled_yfinance_evidence().to_dict()
        evidence["symbol"] = "000660"
        evidence["payload"]["market"] = "KOSPI"

        candidates = calendar_candidate_sets_from_research_items([evidence])

        self.assertEqual(3, len(candidates["review"]))
        self.assertTrue(all(item.markets == ["KOSPI"] for item in candidates["review"]))

    def test_automatic_candidate_activates_without_official_url_when_time_is_confirmed(self):
        candidate_store = MemoryCandidateStore()
        candidate_store.upsert({
            "candidateId": "estimated-earnings-candidate",
            "proposedEventId": "estimated-earnings-event",
            "title": "005930 실적 발표 예정",
            "eventType": "earnings",
            "startsAt": "2026-07-29T00:00:00Z",
            "allDay": True,
            "symbols": ["005930"],
            "markets": ["US"],
            "source": "yfinance",
            "payload": {
                "autoDetected": True,
                "officialSource": False,
                "scheduleState": "estimated",
                "reviewRequired": True,
            },
        })
        calendar_store = MemoryCalendarStore()
        service = InvestmentCalendarCandidateService(
            candidate_store,
            self.service(store=calendar_store),
            now=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
        )

        result = service.approve_candidate("estimated-earnings-candidate", {
            "startsAt": "2026-07-30T10:00",
            "timezone": "America/New_York",
        })

        self.assertEqual("registered", result["candidate"]["status"])
        event = calendar_store.get("estimated-earnings-event")
        self.assertFalse(event.all_day)
        self.assertEqual("2026-07-30T14:00:00Z", event.starts_at)
        self.assertEqual("America/New_York", event.timezone)
        self.assertFalse(event.payload["officialSource"])
        self.assertEqual("confirmed", event.payload["scheduleState"])
        self.assertEqual("userConfirmed", event.payload["timeState"])
        self.assertEqual("user-confirmed", event.payload["scheduleVerification"])
        self.assertEqual("사용자 확인 일정", event.source)
        self.assertEqual("", event.source_url)
        self.assertEqual(0, service.reconcile_all_candidates()["reopened"])
        self.assertEqual("registered", candidate_store.get("estimated-earnings-candidate").status)

    def test_candidate_repair_reopens_estimates_and_rejects_keyword_noise(self):
        candidate_store = MemoryCandidateStore()
        calendar_store = MemoryCalendarStore()
        candidate_store.upsert({
            "candidateId": "legacy-yfinance",
            "proposedEventId": "legacy-yfinance-event",
            "title": "AAPL 실적 발표 예정",
            "eventType": "earnings",
            "startsAt": "2026-08-20T00:00:00Z",
            "status": "registered",
            "source": "yfinance",
            "payload": {
                "autoDetected": True,
                "officialSource": False,
                "scheduleState": "estimated",
            },
        })
        candidate_store.upsert({
            "candidateId": "legacy-news-noise",
            "proposedEventId": "legacy-news-event",
            "title": "뉴스 제목 상장 후보",
            "eventType": "listing",
            "startsAt": "2026-08-21T00:00:00Z",
            "status": "registered",
            "source": "PR Newswire",
            "payload": {"autoDetected": True, "officialSource": True},
        })
        calendar_store.upsert(InvestmentCalendarEvent.from_payload({
            "eventId": "legacy-yfinance-event",
            "title": "AAPL 실적 발표 예정",
            "eventType": "earnings",
            "startsAt": "2026-08-20T00:00:00Z",
            "status": "candidateOnly",
            "source": "yfinance",
            "payload": {"autoDetected": True, "officialSource": False},
        }))
        service = InvestmentCalendarCandidateService(
            candidate_store,
            self.service(store=calendar_store),
            now=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
        )

        result = service.reconcile_all_candidates()

        self.assertEqual(1, result["reopened"])
        self.assertEqual(1, result["rejected"])
        self.assertEqual("pending", candidate_store.get("legacy-yfinance").status)
        self.assertEqual("rejected", candidate_store.get("legacy-news-noise").status)

    def test_calendar_discovery_keeps_estimates_in_candidate_queue_only(self):
        calendar_store = MemoryCalendarStore()
        candidate_store = MemoryCandidateStore()
        evidence_store = MemoryResearchEvidenceStore()
        gateway = MemoryCalendarDiscoveryGateway([self.scheduled_yfinance_evidence()])
        calendar = self.service(store=calendar_store)
        discovery = InvestmentCalendarDiscoveryService(
            calendar_service=calendar,
            candidate_repository=candidate_store,
            evidence_repository=evidence_store,
            account_repository=SimpleNamespace(load_all=lambda: [self.account()]),
            research_gateway=gateway,
            settings={"watchlistSymbols": "AAPL", "investmentCalendarDiscoveryMaxSymbols": "3"},
            now=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
        )

        result = discovery.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["targetCount"])
        self.assertEqual(1, result["evidenceCount"])
        self.assertEqual(0, result["tentativeCount"])
        self.assertEqual(3, result["reviewCandidateCount"])
        self.assertEqual(3, result["storedReviewCandidateCount"])
        self.assertEqual(0, len(calendar_store.events))
        self.assertEqual(3, len(candidate_store.candidates))
        self.assertEqual(1, len(gateway.calls))
        self.assertIn("financial-data", gateway.calls[0]["sourceTypes"])
        self.assertEqual(1, len(evidence_store.items))
        self.assertEqual(0, calendar.enqueue_due_reminders(now_at=datetime(2026, 8, 5, tzinfo=timezone.utc))["queuedCount"])

        candidate = next(item for item in candidate_store.candidates.values() if item.event_type == "earnings")
        approval = InvestmentCalendarCandidateService(candidate_store, calendar).approve_candidate(candidate.candidate_id, {
            "startsAt": "2026-08-05T10:00",
            "reviewNote": "발표 시각 확인",
        })
        self.assertEqual("registered", approval["candidate"]["status"])
        approved_event = calendar_store.get(candidate.proposed_event_id)
        self.assertEqual("active", approved_event.status)
        self.assertFalse(approved_event.all_day)
        self.assertFalse(approved_event.payload["officialSource"])
        self.assertEqual("confirmed", approved_event.payload["scheduleState"])
        self.assertEqual("user-confirmed", approved_event.payload["scheduleVerification"])

        discovery.run_once(force=True)
        self.assertEqual("active", calendar_store.get(candidate.proposed_event_id).status)

        rejected = next(item for item in candidate_store.candidates.values() if item.status == "pending")
        rejection = InvestmentCalendarCandidateService(candidate_store, calendar).reject_candidate(rejected.candidate_id, {"reviewNote": "날짜 불확실"})
        self.assertFalse(rejection["removedTentativeEvent"])
        self.assertIsNone(calendar_store.get(rejected.proposed_event_id))

    def test_calendar_discovery_marks_failed_sources_as_partial(self):
        gateway = MemoryCalendarDiscoveryGateway(
            [self.scheduled_yfinance_evidence()],
            statuses=[{"source": "market-provider", "ok": False, "message": "rate limited"}],
        )
        discovery = InvestmentCalendarDiscoveryService(
            calendar_service=self.service(store=MemoryCalendarStore()),
            candidate_repository=MemoryCandidateStore(),
            account_repository=SimpleNamespace(load_all=lambda: [self.account()]),
            research_gateway=gateway,
            settings={"watchlistSymbols": "AAPL"},
            now=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
        )

        result = discovery.run_once(force=True)

        self.assertEqual("partial", result["status"])
        self.assertIn("market-provider: rate limited", result["errors"])

    def test_structured_sec_f6_payload_uses_official_parser_and_date_field(self):
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
                        "evidenceId": "research:AAPL:sec:f6-structured",
                        "symbol": "AAPL",
                        "kind": "filing",
                        "source": "SEC EDGAR",
                        "title": "Apple depositary shares registration",
                        "summary": "Registration statement for depositary receipt program.",
                        "url": "https://www.sec.gov/example/f6-structured",
                        "publishedAt": "2026-07-15T00:00:00Z",
                        "observedAt": "2026-07-15T00:10:00Z",
                        "materialityState": "material",
                        "sourceTrustState": "trusted",
                        "dataState": "sufficient",
                        "validationState": "ready",
                        "payload": {"form": "F-6", "eventDate": "2026-08-20"},
                    }
                ]
            },
        )

        result = extractor.handle(event)

        self.assertEqual(1, result["candidateCount"])
        self.assertEqual(1, result["savedCount"])
        saved = next(iter(store.events.values()))
        self.assertEqual("adrListing", saved.event_type)
        self.assertEqual("sec-edgar", saved.payload["sourceParser"])
        self.assertEqual("eventDate", saved.payload["dateSource"])
        self.assertEqual("2026-08-20T00:00:00Z", saved.starts_at)
        self.assertFalse(saved.all_day)
        self.assertEqual("09:00", saved.payload["eventLocalTime"])
        self.assertEqual("estimatedDefault", saved.payload["timeState"])

    def test_rejected_feedback_can_demote_borderline_candidate_to_review(self):
        item = {
            "evidenceId": "research:AAPL:listing-blog",
            "symbol": "AAPL",
            "kind": "news",
            "source": "SEC EDGAR",
            "title": "Apple files F-6 for ADR listing on NYSE on 2026-08-20",
            "summary": "Official filing confirms the listing schedule.",
            "url": "https://www.sec.gov/example/listing",
            "publishedAt": "2026-07-15T00:00:00Z",
            "sourceTrustState": "trusted",
            "dataState": "sufficient",
            "validationState": "ready",
        }

        candidate = calendar_candidate_from_research_item(
            item,
            include_review=True,
            feedback={"adrListing": {"accepted": 0, "rejected": 3}},
        )

        self.assertIsNotNone(candidate)
        self.assertTrue(candidate.review_required())
        self.assertEqual("feedbackReview", candidate.review_reason)
        self.assertEqual("needs-review", candidate.readiness_state)

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

    def test_bok_policy_decision_html_parses_to_central_bank_events(self):
        html = """
        <h3>2026년</h3>
        <table><tbody>
          <tr><th scope="row">07월 16일(목)</th><td></td></tr>
          <tr><th scope="row">08월 27일(목)</th><td></td></tr>
        </tbody></table>
        """

        events = parse_bok_policy_decision_events(
            html,
            year=2026,
            source_url="https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do?mtgSe=A&menuNo=200755&pYear=2026",
            time_kst="09:00",
        )

        self.assertEqual(2, len(events))
        first = events[0]
        self.assertEqual("official-bok-policy-decision-20260716", first.event_id)
        self.assertEqual("centralBank", first.event_type)
        self.assertEqual(["KR"], first.markets)
        self.assertFalse(first.all_day)
        self.assertEqual("2026-07-16T00:00:00Z", first.starts_at)
        self.assertTrue(first.payload["policyRateDecisionExpected"])
        self.assertEqual("목", first.payload["weekday"])

    def test_official_calendar_sync_registers_bok_policy_decisions(self):
        html = """
        <h3>2026년</h3>
        <table><tbody>
          <tr><th scope="row">07월 16일(목)</th><td></td></tr>
        </tbody></table>
        """
        store = MemoryCalendarStore()
        calendar_service = self.service(store=store)
        source = BokPolicyDecisionCalendarSource(
            settings={
                "investmentCalendarOfficialMacroSyncEnabled": "1",
                "investmentCalendarBokPolicyDecisionEnabled": "1",
                "investmentCalendarBokPolicyDecisionLookaheadYears": "0",
            },
            fetch_text=lambda _url, _headers, _timeout: html,
            now=lambda: datetime(2026, 7, 1, tzinfo=timezone.utc),
            guard_state={},
        )
        sync_service = OfficialCalendarSyncService(
            calendar_service=calendar_service,
            sources=[source],
            settings={"investmentCalendarOfficialMacroSyncEnabled": "1"},
            now=lambda: datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

        result = sync_service.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["fetchedCount"])
        self.assertEqual(1, result["savedCount"])
        saved = store.get("official-bok-policy-decision-20260716")
        self.assertIsNotNone(saved)
        self.assertEqual("한국은행 기준금리 결정 금융통화위원회", saved.title)
        self.assertEqual("Bank of Korea", saved.source)
        self.assertEqual("centralBank", saved.event_type)

    def test_opendart_earnings_source_parses_official_ir_announcement(self):
        document = (
            "기업설명회(IR) 개최(안내공시) 일시 및 장소 일시 2026-07-30 10:00 "
            "개최목적 2026년 2분기 경영실적 발표 주요 설명회내용 2026년 2분기 경영실적 발표 및 질의응답"
        )
        source = OpenDartEarningsCalendarSource(
            settings={
                "investmentCalendarOfficialMacroSyncEnabled": "1",
                "investmentCalendarOfficialEarningsSyncEnabled": "1",
                "opendartApiKey": "test-key",
                "externalDartCorpCodes": "005930=00126380",
            },
            fetch_json=lambda _url, _headers, _timeout: {
                "status": "000",
                "list": [{
                    "report_nm": "기업설명회(IR)개최(안내공시)",
                    "rcept_no": "20260707001234",
                    "corp_name": "삼성전자",
                }],
            },
            fetch_bytes=lambda _url, _headers, _timeout: document.encode("utf-8"),
            now=lambda: datetime(2026, 7, 29, tzinfo=timezone.utc),
            guard_state={},
        )

        events = source.events()

        self.assertEqual(1, len(events))
        event = events[0]
        self.assertEqual("official-opendart-earnings-005930-2026-q2", event.event_id)
        self.assertEqual("2026-07-30T01:00:00Z", event.starts_at)
        self.assertEqual(["005930"], event.symbols)
        self.assertEqual(["KOSPI"], event.markets)
        self.assertTrue(event.payload["officialSource"])
        self.assertEqual("confirmed", event.payload["scheduleState"])

    def test_opendart_source_resolves_unconfigured_account_symbol(self):
        corp_xml = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<result><list><corp_code>00401731</corp_code><corp_name>LG전자</corp_name>"
            "<stock_code>066570</stock_code></list></result>"
        ).encode("utf-8")
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("CORPCODE.xml", corp_xml)
        document = (
            "기업설명회(IR) 개최(안내공시) 일시 및 장소 일시 2026-07-30 16:00 "
            "개최목적 2026년 2분기 경영실적 발표"
        ).encode("utf-8")

        source = OpenDartEarningsCalendarSource(
            settings={
                "investmentCalendarOfficialMacroSyncEnabled": "1",
                "investmentCalendarOfficialEarningsSyncEnabled": "1",
                "opendartApiKey": "test-key",
                "externalDartCorpCodes": "",
            },
            fetch_json=lambda _url, _headers, _timeout: {
                "status": "000",
                "list": [{
                    "report_nm": "기업설명회(IR)개최(안내공시)",
                    "rcept_no": "20260729001234",
                    "corp_name": "LG전자",
                }],
            },
            fetch_bytes=lambda url, _headers, _timeout: (
                archive_buffer.getvalue() if "corpCode.xml" in url else document
            ),
            now=lambda: datetime(2026, 7, 29, tzinfo=timezone.utc),
            guard_state={},
            target_symbols=["066570"],
        )

        events = source.events()

        self.assertEqual({"066570": "00401731"}, parse_opendart_corp_codes(archive_buffer.getvalue(), ["066570"]))
        self.assertEqual({"066570": "00401731"}, parse_opendart_corp_codes(archive_buffer.getvalue()))
        self.assertEqual(1, len(events))
        self.assertEqual(["066570"], events[0].symbols)

    def test_samsung_ir_source_parses_confirmed_earnings_time(self):
        markup = """
        <dl class="ir-event-list__detail">
          <dt>2Q26 Earnings Conference Call</dt>
          <dd>July 30, 2026, 10:00 a.m. KST</dd>
        </dl>
        """

        events = parse_samsung_ir_earnings_events(
            markup,
            minimum_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(1, len(events))
        self.assertEqual("official-samsung-ir-earnings-005930-2026-q2", events[0].event_id)
        self.assertEqual("2026-07-30T01:00:00Z", events[0].starts_at)
        self.assertEqual(["005930"], events[0].symbols)
        self.assertTrue(events[0].payload["officialSource"])

    def test_official_earnings_schedule_supersedes_nearby_yfinance_estimate(self):
        store = MemoryCalendarStore()
        calendar_service = self.service(store=store)
        candidate_store = MemoryCandidateStore()
        estimated = InvestmentCalendarEvent.from_payload({
            "eventId": "estimated-005930-q2",
            "title": "005930 실적 발표 예정",
            "eventType": "earnings",
            "startsAt": "2026-07-29T00:00:00Z",
            "timezone": "Asia/Seoul",
            "allDay": False,
            "status": "active",
            "symbols": ["005930"],
            "markets": ["US"],
            "source": "yfinance",
            "reminderOffsetsMinutes": [60, 0],
            "payload": {
                "autoDetected": True,
                "officialSource": False,
                "scheduleState": "estimated",
                "reviewRequired": True,
            },
        })
        store.upsert(estimated)
        candidate_store.upsert({
            "candidateId": "estimated-005930-q2-candidate",
            "proposedEventId": "estimated-005930-q2",
            "title": "005930 실적 발표 예정",
            "eventType": "earnings",
            "startsAt": "2026-07-29T00:00:00Z",
            "status": "registered",
            "source": "yfinance",
            "symbols": ["005930"],
            "payload": {"autoDetected": True, "officialSource": False, "scheduleState": "estimated"},
        })
        official = parse_opendart_earnings_event(
            "일시 및 장소 일시 2026-07-30 10:00 개최목적 2026년 2분기 경영실적 발표",
            "005930",
            "삼성전자",
            "20260707001234",
        )
        sync_service = OfficialCalendarSyncService(
            calendar_service=calendar_service,
            sources=[SimpleNamespace(events=lambda: [official])],
            candidate_service=InvestmentCalendarCandidateService(
                candidate_store,
                calendar_service,
                now=lambda: datetime(2026, 7, 29, tzinfo=timezone.utc),
            ),
            settings={"investmentCalendarOfficialMacroSyncEnabled": "1"},
            now=lambda: datetime(2026, 7, 29, tzinfo=timezone.utc),
        )

        result = sync_service.run_once(force=True)

        self.assertEqual(1, result["quarantinedCount"])
        self.assertEqual(1, result["supersededCount"])
        self.assertEqual(1, result["candidateSupersededCount"])
        self.assertEqual("superseded", store.get("estimated-005930-q2").status)
        self.assertEqual([], store.get("estimated-005930-q2").reminder_offsets_minutes)
        confirmed = store.get("official-opendart-earnings-005930-2026-q2")
        self.assertIsNotNone(confirmed)
        self.assertTrue(confirmed.reminder_eligible())
        self.assertEqual("superseded", candidate_store.get("estimated-005930-q2-candidate").status)

        repeated = sync_service.run_once(force=True)

        self.assertEqual(0, repeated["quarantinedCount"])
        self.assertEqual(0, repeated["supersededCount"])
        self.assertEqual(0, repeated["candidateSupersededCount"])

    def test_calendar_sync_quarantines_legacy_unverified_automatic_event(self):
        store = MemoryCalendarStore()
        calendar_service = self.service(store=store)
        store.upsert(InvestmentCalendarEvent.from_payload({
            "eventId": "legacy-yfinance-estimate",
            "title": "005930 실적 발표 예정",
            "eventType": "earnings",
            "startsAt": "2026-08-20T00:00:00Z",
            "timezone": "Asia/Seoul",
            "allDay": False,
            "status": "active",
            "symbols": ["005930"],
            "source": "yfinance",
            "reminderOffsetsMinutes": [60, 0],
            "payload": {
                "autoDetected": True,
                "officialSource": False,
                "scheduleState": "estimated",
                "dateSource": "calendar.Earnings Date",
            },
        }))
        sync_service = OfficialCalendarSyncService(
            calendar_service=calendar_service,
            sources=[],
            settings={"investmentCalendarOfficialMacroSyncEnabled": "1"},
        )

        result = sync_service.run_once(force=True)

        quarantined = store.get("legacy-yfinance-estimate")
        self.assertEqual(1, result["quarantinedCount"])
        self.assertEqual("candidateOnly", quarantined.status)
        self.assertTrue(quarantined.all_day)
        self.assertEqual([], quarantined.reminder_offsets_minutes)
        self.assertFalse(quarantined.reminder_eligible())


if __name__ == "__main__":
    unittest.main()
