import unittest

from digital_twin.modules.news_intelligence.domain.investment_research import ResearchEvidence
from digital_twin.modules.read_models.application.instrument_timeline_query_service import InstrumentTimelineQueryService
from digital_twin.modules.read_models.domain.instrument_timeline import InstrumentTimelineQuery


class EvidenceStore:
    def __init__(self, items):
        self.items = list(items)

    def latest(self, symbol="", limit=100):
        return [item for item in self.items if not symbol or item.symbol == symbol][:limit]


class EmptyStore:
    def list(self, *args, **kwargs):
        return []

    def list_events(self, *args, **kwargs):
        return []

    def timeline_for_symbol(self, *args, **kwargs):
        return []


def service_for(items):
    empty = EmptyStore()
    return InstrumentTimelineQueryService(
        time_series_store=empty,
        evidence_store=EvidenceStore(items),
        calendar_store=empty,
        decision_episode_store=empty,
        hypothesis_lifecycle_store=empty,
        notification_job_store=empty,
    )


class InstrumentTimelineDeduplicationTest(unittest.TestCase):
    def test_distinct_disclosures_show_reporter_and_receipt_number(self):
        rows = [
            ResearchEvidence(
                evidence_id="research:000660:dart:20260821000524",
                symbol="000660",
                kind="disclosure",
                source="OpenDART",
                title="임원ㆍ주요주주특정증권등소유상황보고서",
                published_at="20260821",
                raw_payload={
                    "receiptNo": "20260821000524",
                    "officialDocumentText": "보고자 : 양동훈 1. 발행회사에 관한 사항",
                    "disclosureAnalysis": {"summary": "양동훈 이사의 보유 주식이 변동됐습니다."},
                },
            ),
            ResearchEvidence(
                evidence_id="research:000660:dart:20260821000527",
                symbol="000660",
                kind="disclosure",
                source="OpenDART",
                title="임원ㆍ주요주주특정증권등소유상황보고서",
                published_at="20260821",
                raw_payload={
                    "receiptNo": "20260821000527",
                    "filerName": "손현철",
                    "disclosureAnalysis": {"summary": "손현철 이사의 보유 주식이 변동됐습니다."},
                },
            ),
        ]

        events = service_for(rows).events(InstrumentTimelineQuery(symbol="000660"))

        self.assertEqual(2, len(events))
        self.assertEqual(
            {"임원ㆍ주요주주특정증권등소유상황보고서 · 양동훈", "임원ㆍ주요주주특정증권등소유상황보고서 · 손현철"},
            {event["title"] for event in events},
        )
        self.assertTrue(all("접수번호 20260821" in event["summary"] for event in events))
        self.assertEqual(2, len({event["metadata"]["timelineIdentity"] for event in events}))

    def test_same_news_story_is_one_timeline_event_with_source_count(self):
        rows = [
            ResearchEvidence(
                evidence_id="news-1",
                symbol="000660",
                kind="news",
                source="연합뉴스",
                title="솔리다임 미국 상장 검토",
                summary="솔리다임이 미국 상장을 검토합니다.",
                published_at="2026-09-26T01:00:00Z",
                raw_payload={"storyClusterId": "story-solidigm-ipo"},
            ),
            ResearchEvidence(
                evidence_id="news-2",
                symbol="000660",
                kind="news",
                source="매일경제",
                title="SK하이닉스 솔리다임 미국 상장 추진",
                summary="기업가치와 조달 규모가 거론됐습니다.",
                published_at="2026-09-26T00:30:00Z",
                raw_payload={"storyClusterId": "story-solidigm-ipo"},
            ),
        ]

        events = service_for(rows).events(InstrumentTimelineQuery(symbol="000660"))

        self.assertEqual(1, len(events))
        self.assertEqual(2, events[0]["metadata"]["sourceCount"])
        self.assertEqual(["news-1", "news-2"], events[0]["metadata"]["evidenceIds"])
        self.assertIn("같은 사건을 2개 출처가 보도", events[0]["summary"])
        self.assertEqual("연합뉴스 외 1개 출처", events[0]["source"])


if __name__ == "__main__":
    unittest.main()
