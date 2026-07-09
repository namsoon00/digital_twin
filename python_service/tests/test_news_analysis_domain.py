import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.domain.news_analysis import classify_news_relevance, confidence_from_analysis_payload, impact_from_analysis_payload
from digital_twin.domain.ontology_rules import research_evidence_facts


class NewsAnalysisDomainTests(unittest.TestCase):
    def test_news_analysis_marks_direct_material_event_for_ontology(self):
        target = NewsCollectionTarget("005930", "삼성전자", "KOSPI", "KRW", "반도체")

        analysis = classify_news_relevance(
            target,
            "삼성전자 반도체 실적 개선 전망",
            "메모리 수요 회복과 실적 상향 기대",
            "연합뉴스",
            "Google News KR",
        )

        self.assertEqual("direct", analysis["relationScope"])
        self.assertEqual("earnings", analysis["eventType"])
        self.assertGreaterEqual(analysis["relevanceScore"], 90)
        self.assertGreaterEqual(analysis["materialityScore"], 60)
        self.assertTrue(any(item["type"] == "NEWS_SUPPORTS_ENTRY" for item in analysis["ontologyRelations"]))
        self.assertGreater(confidence_from_analysis_payload(analysis), 0.7)
        self.assertGreater(impact_from_analysis_payload(8, analysis), 8)

    def test_news_analysis_flags_noise_for_unrelated_search_result(self):
        target = NewsCollectionTarget("035420", "NAVER", "KOSPI", "KRW", "플랫폼")

        analysis = classify_news_relevance(
            target,
            "지역 고교 영어 회화 캠프 운영",
            "교육 프로그램 참가자 모집",
            "Naver Blog",
            "Google News KR",
        )

        self.assertEqual("noise", analysis["relationScope"])
        self.assertLess(analysis["relevanceScore"], 35)
        self.assertIn("확인되지 않음", analysis["excludedReason"])
        self.assertEqual([], analysis["ontologyRelations"])

    def test_news_analysis_does_not_treat_naver_blog_source_as_naver_company_news(self):
        target = NewsCollectionTarget("035420", "NAVER", "KOSPI", "KRW", "플랫폼")

        analysis = classify_news_relevance(
            target,
            "제천산업고, 기초 비즈니스 영어 회화 캠프 운영 : 네이버 블로그",
            "교육 프로그램 참가자 모집",
            "Naver Blog",
            "Google News KR",
        )

        self.assertEqual("noise", analysis["relationScope"])
        self.assertLess(analysis["relevanceScore"], 35)
        self.assertIn("플랫폼/블로그", analysis["excludedReason"])

    def test_ontology_news_facts_include_event_type_and_materiality(self):
        evidence = ResearchEvidence(
            "research:005930:news:event",
            "005930",
            "news",
            "연합뉴스",
            "삼성전자 반도체 실적 개선 전망",
            "메모리 수요 회복과 실적 상향 기대",
            "https://example.test/news",
            "2026-07-09T01:00:00Z",
            "support",
            11.0,
            0.78,
            "2026-07-09T01:00:00Z",
            classify_news_relevance(
                NewsCollectionTarget("005930", "삼성전자", "KOSPI", "KRW", "반도체"),
                "삼성전자 반도체 실적 개선 전망",
                "메모리 수요 회복과 실적 상향 기대",
                "연합뉴스",
                "Google News KR",
            ),
        )

        facts = research_evidence_facts([evidence.to_dict()])

        self.assertEqual(1, facts["directSupportNewsCount"])
        self.assertIn("earnings", facts["topNewsEventTypes"])
        self.assertGreaterEqual(facts["averageNewsMaterialityScore"], 60)
        self.assertGreater(facts["newsMomentumScore"], 0)


if __name__ == "__main__":
    unittest.main()
