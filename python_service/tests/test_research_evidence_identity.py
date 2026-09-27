import unittest

from digital_twin.modules.news_intelligence.domain.investment_evidence_governance import canonical_evidence_url
from digital_twin.modules.news_intelligence.domain.investment_research import research_evidence_from_facts


class ResearchEvidenceIdentityTest(unittest.TestCase):
    def test_news_identity_does_not_change_with_collection_time(self):
        def collect(seen_at):
            rows = research_evidence_from_facts("035420", {
                "symbol": "035420",
                "newsHeadlines": {
                    "provider": "Google News",
                    "items": [{
                        "title": "NAVER AI 검색 서비스 확대",
                        "url": "https://example.com/articles/naver-ai",
                        "source": "Example News",
                        "seenDate": seen_at,
                        "publishedAt": "2026-09-27T01:00:00Z",
                        "payload": {"relationScope": "direct"},
                    }],
                },
            })
            return [item for item in rows if item.kind == "news"][0]

        first = collect("2026-09-27T01:05:00Z")
        replay = collect("2026-09-27T01:15:00Z")

        self.assertEqual(first.evidence_id, replay.evidence_id)
        self.assertNotEqual(first.observed_at, replay.observed_at)

    def test_publisher_front_controller_is_not_article_identity(self):
        direct = canonical_evidence_url(
            "https://www.mt.co.kr/finance/2026/09/24/2026092217402487674?utm_source=rss"
        )
        routed = canonical_evidence_url(
            "https://m.mt.co.kr/index.php/finance/2026/09/24/2026092217402487674"
        )

        self.assertEqual(direct, routed)


if __name__ == "__main__":
    unittest.main()
