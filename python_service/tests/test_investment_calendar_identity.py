import unittest

from digital_twin.modules.investment_calendar.domain.investment_calendar import InvestmentCalendarEvent
from digital_twin.modules.investment_calendar.application.investment_calendar_service import unique_calendar_events


class InvestmentCalendarIdentityTest(unittest.TestCase):
    def test_replayed_source_event_reuses_durable_id(self):
        payload = {
            "title": "SK하이닉스 실적 발표",
            "eventType": "earnings",
            "startsAt": "2026-10-28T09:00:00+09:00",
            "symbols": ["000660"],
            "source": "OpenDART",
            "sourceUrl": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261028000123",
            "payload": {
                "autoDetected": True,
                "sourceEvidenceId": "research:000660:dart:20261028000123",
                "receiptNo": "20261028000123",
                "sourceObservedAt": "2026-10-28T00:01:00Z",
            },
        }

        first = InvestmentCalendarEvent.from_payload(payload)
        payload["payload"]["sourceObservedAt"] = "2026-10-28T00:06:00Z"
        second = InvestmentCalendarEvent.from_payload(payload)

        self.assertEqual(first.event_id, second.event_id)
        self.assertTrue(first.event_id.startswith("source-event-"))

    def test_source_schedule_correction_updates_same_identity(self):
        payload = {
            "title": "NVIDIA 실적 발표 예정",
            "eventType": "earnings",
            "startsAt": "2026-11-18T16:00:00-05:00",
            "symbols": ["NVDA"],
            "source": "issuer-ir",
            "payload": {
                "autoDetected": True,
                "sourceEvidenceId": "issuer:nvda:fy2027-q3",
                "schedulePhase": "earnings",
            },
        }

        first = InvestmentCalendarEvent.from_payload(payload)
        payload["startsAt"] = "2026-11-18T16:30:00-05:00"
        second = InvestmentCalendarEvent.from_payload(payload)

        self.assertEqual(first.event_id, second.event_id)
        self.assertNotEqual(first.starts_at, second.starts_at)

    def test_recurring_official_calendar_page_does_not_merge_occurrences(self):
        base = {
            "title": "미국 연준 FOMC 기준금리 결정",
            "eventType": "centralBank",
            "source": "Federal Reserve",
            "sourceUrl": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            "payload": {"autoDetected": True, "dateSource": "federal-reserve-fomc-calendar"},
        }

        january = InvestmentCalendarEvent.from_payload({**base, "startsAt": "2026-01-28T19:00:00Z"})
        march = InvestmentCalendarEvent.from_payload({**base, "startsAt": "2026-03-18T18:00:00Z"})
        january_replay = InvestmentCalendarEvent.from_payload({**base, "startsAt": "2026-01-28T19:00:00Z"})

        self.assertNotEqual(january.event_id, march.event_id)
        self.assertEqual(january.event_id, january_replay.event_id)
        self.assertEqual(2, len(unique_calendar_events([january, march, january_replay])))

        provider_snapshot = {
            "title": "TSLA 실적 발표 예정",
            "eventType": "earnings",
            "symbols": ["TSLA"],
            "source": "yfinance",
            "payload": {
                "autoDetected": True,
                "sourceEvidenceId": "research:TSLA:yfinance",
                "sourceKind": "financial-fact",
            },
        }
        july = InvestmentCalendarEvent.from_payload({**provider_snapshot, "startsAt": "2026-07-21T15:00:00Z"})
        october = InvestmentCalendarEvent.from_payload({**provider_snapshot, "startsAt": "2026-10-20T15:00:00Z"})
        self.assertNotEqual(july.event_id, october.event_id)

    def test_manual_events_without_id_remain_distinct(self):
        payload = {
            "title": "개인 점검",
            "eventType": "custom",
            "startsAt": "2026-10-01",
            "source": "manual",
        }

        self.assertNotEqual(
            InvestmentCalendarEvent.from_payload(payload).event_id,
            InvestmentCalendarEvent.from_payload(payload).event_id,
        )

    def test_legacy_replay_rows_are_collapsed_by_source_identity(self):
        base = {
            "title": "SK하이닉스 실적 발표",
            "eventType": "earnings",
            "startsAt": "2026-10-28T09:00:00+09:00",
            "symbols": ["000660"],
            "source": "OpenDART",
            "payload": {
                "autoDetected": True,
                "sourceEvidenceId": "research:000660:dart:20261028000123",
            },
        }
        first = InvestmentCalendarEvent.from_payload({
            **base,
            "eventId": "legacy-random-1",
            "updatedAt": "2026-09-20T00:00:00Z",
        })
        latest = InvestmentCalendarEvent.from_payload({
            **base,
            "eventId": "legacy-random-2",
            "updatedAt": "2026-09-21T00:00:00Z",
        })

        events = unique_calendar_events([first, latest])

        self.assertEqual(1, len(events))
        self.assertEqual("legacy-random-2", events[0].event_id)


if __name__ == "__main__":
    unittest.main()
