import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from digital_twin.modules.investment_calendar.infrastructure.us_issuer_calendar_source import (
    UsIssuerEarningsCalendarSource, parse_apple_events, parse_nvidia_events, parse_palantir_events,
)
from digital_twin.modules.investment_calendar.domain.earnings_schedule_projection import earnings_candidate_projection
from digital_twin.modules.investment_calendar.domain.investment_calendar import InvestmentCalendarEvent
from digital_twin.modules.investment_calendar.domain.investment_calendar_candidates import InvestmentCalendarReviewCandidate
from digital_twin.modules.investment_calendar.application.investment_calendar_service import InvestmentCalendarService
from digital_twin.modules.investment_calendar.application.investment_calendar_candidate_service import InvestmentCalendarCandidateService
from digital_twin.modules.investment_calendar.application.investment_calendar_discovery_service import InvestmentCalendarDiscoveryService
from digital_twin.modules.investment_calendar.application.official_calendar_sync_service import OfficialCalendarSyncService


def apple_row(date="11/02/2026 14:00:00", title="FY 26 Fourth Quarter Results", zone="PT"):
    return {"Title": title, "StartDate": date, "TimeZone": zone,
            "LinkToDetailPage": "/event-details/2026/FY-26-Fourth-Quarter-Results/default.aspx"}


def candidate(identity, date, observed="2026-10-29T00:00:00Z", quarter=None):
    body = {"autoDetected": True, "dateSource": "calendar.earningsDate", "timeState": "estimatedDefault",
            "sourceObservedAt": observed, "eventLocalDate": date, "scheduleState": "estimated"}
    if quarter:
        body.update(fiscalYear=2026, fiscalQuarter=quarter)
    return InvestmentCalendarReviewCandidate.from_payload({
        "candidateId": identity, "title": "AAPL earnings", "eventType": "earnings", "status": "pending",
        "startsAt": date + "T00:00:00Z", "allDay": True, "symbols": ["AAPL"], "source": "yfinance",
        "payload": body,
    })


class EarningsCalendarCoverageTest(unittest.TestCase):
    def test_official_feed_preserves_dst_and_correction_identity(self):
        events = parse_apple_events({"GetEventListResult": [apple_row(), apple_row("07/30/2026 14:00:00"),
                                                         apple_row(zone="unknown"), apple_row(title="Annual Meeting")]})
        self.assertEqual([event.starts_at for event in events], ["2026-11-02T22:00:00Z", "2026-07-30T21:00:00Z"])
        self.assertEqual(events[0].event_id, events[1].event_id)
        self.assertEqual(events[0].payload["fiscalYear"], 2026)
        self.assertTrue(InvestmentCalendarEvent.from_payload(events[0].to_calendar_payload()).reminder_eligible())
        self.assertNotEqual(events[0].payload["sourceHash"], events[1].payload["sourceHash"])

    def test_date_only_official_event_has_no_invented_reminder(self):
        markup = '<p class="report-date inview">11 / 02 / 26</p><p class="report-name delay-1">Q3 2026 Earnings</p>'
        event = parse_palantir_events(markup)[0]
        saved = InvestmentCalendarEvent.from_payload(event.to_calendar_payload())
        self.assertEqual(saved.to_dict()["localDate"], "2026-11-02")
        self.assertEqual(saved.starts_at, "2026-11-02T05:00:00Z")
        self.assertTrue(saved.all_day)
        self.assertFalse(saved.reminder_eligible())
        self.assertEqual(saved.reminder_offsets_minutes, [])

    def test_nvidia_announcement_uses_calendar_year_not_fiscal_year(self):
        markup = '''<article><span class="index-item-text-info-date">October 28, 2026</span>
        <a href="/news/nvidia-sets-conference-call-for-third-quarter-financial-results">Financial Results</a>
        NVIDIA will host a conference call on Tuesday, November 17, at 2 p.m. PT (5 p.m. ET)
        to discuss its financial results for the third quarter of fiscal year 2027, which ended October 25, 2026.</article>'''
        event = parse_nvidia_events(markup)[0]
        self.assertEqual(event.starts_at, "2026-11-17T22:00:00Z")
        self.assertEqual(event.payload["fiscalYear"], 2027)
        self.assertEqual(event.payload["fiscalQuarter"], 3)
        self.assertEqual(parse_nvidia_events(markup.replace("will host a conference call", "hosted a conference call")), [])

    def test_projection_replaces_old_estimates_without_losing_other_quarters(self):
        candidates = [candidate("old", "2026-10-30", "2026-10-07T00:00:00Z", 4),
                      candidate("latest", "2026-11-03", quarter=4),
                      candidate("another-quarter", "2026-11-09", quarter=3),
                      candidate("expired", "2026-09-01")]
        arguments = {"from_at": "2026-09-01T00:00:00Z", "to_at": "2026-12-01T00:00:00Z",
                     "now": datetime(2026, 10, 30, tzinfo=timezone.utc)}
        projected = earnings_candidate_projection(candidates, [], **arguments)
        self.assertEqual({row["candidateId"] for row in projected}, {"latest", "another-quarter"})
        self.assertTrue(all(row["readOnly"] and row["reminderOffsetsMinutes"] == [] for row in projected))
        official = parse_apple_events({"GetEventListResult": [apple_row()]})[0].to_calendar_payload()
        self.assertEqual([row["candidateId"] for row in earnings_candidate_projection(candidates, [official], **arguments)], ["another-quarter"])
        self.assertEqual(earnings_candidate_projection(candidates, [], event_type="macro", **arguments), [])
        self.assertEqual(candidates[0].status, "pending")  # Projection did not rewrite its audit.

    def test_candidates_are_opt_in_and_cannot_publish_facts_or_reminders(self):
        day = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
        item = candidate("only-ui", day)
        repository = Mock()
        repository.list.return_value = []
        repository.summary.return_value = {"total": 0}
        reader = Mock()
        reader.list.return_value = [item]
        reader.get.return_value = item
        publisher, facts = Mock(), Mock()
        service = InvestmentCalendarService(repository, candidate_reader=reader, event_publisher=publisher,
                                            reasoning_source_fact_store=facts,
            sync_status_reader=lambda: {"lastResult": {"status": "partial", "errors": ["private request details"],
                                                       "unsupportedSymbols": ["TSLA"]}})
        self.assertEqual(service.list_events()["events"], [])
        reader.list.assert_not_called()
        result = service.list_events({"includeCandidates": True})
        self.assertEqual(result["summary"]["unconfirmed"], 1)
        self.assertNotIn("private request details", str(result))
        self.assertEqual(result["officialCollection"]["lastResult"]["unsupportedSymbols"], ["TSLA"])
        event = result["events"][0]
        self.assertEqual(event["eventId"], "candidate:only-ui")
        self.assertFalse(InvestmentCalendarEvent.from_payload(event).reminder_eligible())
        self.assertFalse(InvestmentCalendarEvent.from_payload(event).reasoning_eligible())
        self.assertTrue(service.get_event(event["eventId"])["readOnly"])
        repository.upsert.assert_not_called()
        publisher.publish.assert_not_called()
        facts.append.assert_not_called()
        reader.list.side_effect = RuntimeError("unavailable")
        self.assertEqual(service.list_events({"includeCandidates": True})["candidateError"], "미확정 일정 조회 실패")

    def test_official_reconciliation_keeps_other_fiscal_periods(self):
        corrected = candidate("moved-date", "2026-09-15", quarter=4)
        other = candidate("other-period", "2026-11-03", quarter=3)
        repository = Mock()
        repository.list.side_effect = lambda status, **_: [corrected, other] if status == "pending" else []
        service = InvestmentCalendarCandidateService(repository, Mock())
        event = parse_apple_events({"GetEventListResult": [apple_row()]})[0]
        result = service.reconcile_official_event(event)
        self.assertEqual(result["candidateIds"], ["moved-date"])
        self.assertEqual(repository.mark_status.call_count, 1)

    def test_unavailable_source_is_isolated_and_feed_identifier_is_not_logged(self):
        def fetch(url, *_):
            if "feed/" in url:
                raise RuntimeError("blocked " + url)
            return 'var Q4ApiKey="public-feed-identifier";'
        with patch("digital_twin.modules.investment_calendar.infrastructure.us_issuer_calendar_source.guarded_external_call",
                   side_effect=lambda settings, label, target, call: call()):
            failed = UsIssuerEarningsCalendarSource("AAPL", fetch_text=fetch)
            event = parse_apple_events({"GetEventListResult": [apple_row()]})[0]
            good = SimpleNamespace(label="OfficialIssuerIR/Other", events=lambda: [event])
            calendar = SimpleNamespace(save_event=lambda event: event)
            result = OfficialCalendarSyncService(calendar, [failed, good], unsupported_symbols=["TSLA"]).run_once(force=True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["savedCount"], 1)
        self.assertEqual(result["sources"][0]["source"], "OfficialIssuerIR/AAPL")
        self.assertNotIn("public-feed-identifier", str(result))
        self.assertEqual(result["unsupportedSymbols"], ["TSLA"])

    def test_discovery_includes_holdings_and_rotates_bounded_batches(self):
        service = InvestmentCalendarDiscoveryService(Mock(), Mock(), settings={"investmentCalendarDiscoveryMaxSymbols": 2},
            target_reader=lambda: {"holdingSymbols": ["AAPL"], "watchlistSymbols": ["PLTR", "NVDA", "TSLA"]})
        self.assertEqual([target.symbol for target in service.targets()], ["AAPL", "PLTR"])
        service.last_result = {"symbols": ["AAPL", "PLTR"]}
        self.assertEqual([target.symbol for target in service.targets()], ["NVDA", "TSLA"])
        self.assertEqual([target.symbol for target in service.targets({"symbol": "AAPL"})], ["AAPL"])


if __name__ == "__main__":
    unittest.main()
