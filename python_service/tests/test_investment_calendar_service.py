import unittest
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.investment_calendar_extraction_service import InvestmentCalendarExtractionService
from digital_twin.application.investment_calendar_candidate_service import InvestmentCalendarCandidateService
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
from digital_twin.domain.investment_calendar import InvestmentCalendarEvent, event_type_label, utc_iso
from digital_twin.domain.investment_calendar_candidates import InvestmentCalendarReviewCandidate
from digital_twin.domain.investment_calendar_extraction import calendar_candidate_from_research_item
from digital_twin.domain.investment_research import ResearchEvidence
from digital_twin.infrastructure.bok_calendar_source import BokPolicyDecisionCalendarSource, parse_bok_policy_decision_events
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
            "payload": {"autoDetected": True, "officialSource": True},
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


if __name__ == "__main__":
    unittest.main()
