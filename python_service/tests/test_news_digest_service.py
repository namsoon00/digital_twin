import unittest
from types import SimpleNamespace

from digital_twin.application.news_digest_service import NewsDigestEnqueuer
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.events import DomainEvent, RESEARCH_EVIDENCE_COLLECTED
from digital_twin.domain.investment_research import ResearchEvidence
from digital_twin.domain.message_types import NEWS_DIGEST
from digital_twin.domain.notification_templates import NotificationTemplate, render_notification


class MemoryNotificationQueue:
    def __init__(self):
        self.jobs = []

    def enqueue(self, job):
        self.jobs.append(job)
        return True


class NewsDigestEnqueuerTests(unittest.TestCase):
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

    def evidence(self):
        return ResearchEvidence(
            "research:AAPL:news:apple-openai",
            "AAPL",
            "news",
            "Semafor",
            "Apple OpenAI lawsuit highlights broader tensions",
            "애플과 OpenAI 관련 소송 이슈입니다.",
            "https://example.test/apple?utm_source=news&ref=long",
            "2026-07-11T00:10:00Z",
            "risk",
            8.0,
            0.82,
            published_at="2026-07-11T00:00:00Z",
            raw_payload={
                "relationScope": "direct",
                "relevanceScore": 97,
                "sourceReliability": 82,
                "materialityScore": 84,
                "stockImpactPolarity": "risk",
                "stockImpactLabel": "위험",
                "stockImpactScore": 81,
                "articleReadStatus": "body",
                "articleAnalysisSource": "article-body",
                "articleSummaryKo": "애플을 직접 다룬 법적 이슈로 다음 장 가격 반응 확인이 필요합니다.",
            },
        )

    def enqueuer(self, queue):
        monitor_store = SimpleNamespace(previous={
            "main": {
                "positions": {},
                "watchlist": {
                    "AAPL": {"symbol": "AAPL", "name": "Apple", "market": "NASDAQ"},
                },
            }
        })
        return NewsDigestEnqueuer(
            account_repository=SimpleNamespace(load=lambda: [self.account()]),
            monitor_store=monitor_store,
            queue=queue,
            settings={},
        )

    def test_enqueues_news_digest_with_short_source_link(self):
        queue = MemoryNotificationQueue()
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "materialChangedItems": [self.evidence().to_dict()],
                "materialChangedSymbols": ["AAPL"],
                "materialChangedCount": 1,
            },
        )

        self.enqueuer(queue).handle(event)

        self.assertEqual(1, len(queue.jobs))
        job = queue.jobs[0]
        self.assertEqual(NEWS_DIGEST, job.message_type)
        self.assertEqual("AAPL", job.context["symbol"])
        self.assertEqual("research:AAPL:news:apple-openai", job.context["newsDigest"]["primaryEvidenceId"])
        self.assertIn('href="https://example.test/apple?utm_source=news&amp;ref=long">원문 보기</a>', job.text)
        self.assertNotIn("• 원문: https://example.test", job.text)
        self.assertIn("기사일: 07/11 09:00 KST", job.text)
        self.assertIn("분석: 기사 본문 읽음", job.text)

    def test_ignores_feed_only_article_by_default(self):
        queue = MemoryNotificationQueue()
        feed_only = self.evidence()
        feed_only.raw_payload["articleReadStatus"] = "feed-summary"
        feed_only.raw_payload["articleAnalysisSource"] = "feed-summary"
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "materialChangedItems": [feed_only.to_dict()],
                "materialChangedSymbols": ["AAPL"],
                "materialChangedCount": 1,
            },
        )

        self.enqueuer(queue).handle(event)

        self.assertEqual([], queue.jobs)

    def test_rendered_news_digest_does_not_append_generic_ai_sections(self):
        queue = MemoryNotificationQueue()
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={"materialChangedItems": [self.evidence().to_dict()]},
        )
        self.enqueuer(queue).handle(event)
        job = queue.jobs[0]

        rendered = render_notification(NotificationTemplate("newsDigest", "{body}"), job.context)

        self.assertNotIn("AI 의견", rendered)
        self.assertNotIn("모델 판단", rendered)
        self.assertIn("뉴스/피드 새 정보", rendered)

    def test_ignores_collection_event_without_material_items(self):
        queue = MemoryNotificationQueue()
        event = DomainEvent(
            name=RESEARCH_EVIDENCE_COLLECTED,
            aggregate_id="news:AAPL",
            payload={
                "materialChangedItems": [],
                "changedItems": [self.evidence().to_dict()],
                "materialChangedCount": 0,
            },
        )

        self.enqueuer(queue).handle(event)

        self.assertEqual([], queue.jobs)


if __name__ == "__main__":
    unittest.main()
