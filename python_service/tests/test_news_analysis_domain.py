import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.domain.news_analysis import (
    classify_news_relevance,
    classify_news_event_type,
    clean_article_summary_noise,
    confidence_from_analysis_payload,
    impact_from_analysis_payload,
    korean_article_summary,
    source_reliability_score,
)
from digital_twin.domain.ontology_relation_reasoning import research_evidence_facts


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

    def test_korean_article_summary_removes_translation_preface_for_english_source(self):
        target = NewsCollectionTarget("005930", "삼성전자", "KOSPI", "KRW", "반도체")

        summary = korean_article_summary(
            target,
            "Samsung Electronics shares track chip demand",
            "Samsung Electronics shares moved after semiconductor demand expectations improved.",
            analysis={"relationScope": "direct", "eventType": "general"},
        )

        self.assertNotIn("한국어로 정리하면", summary)
        self.assertNotIn("이슈 이슈", summary)
        self.assertNotIn("관련 뉴스입니다", summary)
        self.assertNotIn("뉴스 유형은", summary)
        self.assertIn("본문 요약", summary)
        self.assertIn("반도체 수요 흐름을 따라 움직였다는 내용", summary)
        self.assertNotIn("Samsung Electronics shares moved after semiconductor demand expectations improved.", summary)
        self.assertRegex(summary, r"[가-힣]")

    def test_english_legal_keyword_uses_word_boundary(self):
        self.assertEqual("regulation", classify_news_event_type("Apple sues OpenAI", "legal dispute"))
        self.assertNotEqual("regulation", classify_news_event_type("Apple issues software update", "general product release"))

    def test_news_analysis_downgrades_social_feed_source(self):
        target = NewsCollectionTarget("AAPL", "Apple", "NASDAQ", "USD", "AI")

        analysis = classify_news_relevance(
            target,
            "Breaking News: Apple sued OpenAI, accusing the company of stealing secrets",
            "",
            "facebook.com",
            "Google News US",
        )

        self.assertEqual("direct", analysis["relationScope"])
        self.assertLessEqual(analysis["sourceReliability"], 0.3)
        self.assertLess(confidence_from_analysis_payload(analysis), 0.55)

    def test_news_analysis_scores_known_publishers_above_digest_gate(self):
        self.assertGreaterEqual(source_reliability_score("The Economist", "Google News US"), 0.8)
        self.assertGreaterEqual(source_reliability_score("YTN", "Google News KR"), 0.68)
        self.assertGreaterEqual(source_reliability_score("뉴스핌", "Google News KR"), 0.68)
        self.assertLess(source_reliability_score("Naver Blog", "Google News KR"), 0.5)

    def test_news_analysis_filters_apple_common_noun_false_positive(self):
        target = NewsCollectionTarget("AAPL", "Apple", "NASDAQ", "USD", "AI")

        analysis = classify_news_relevance(
            target,
            "Apple snails spread through Salt River wetlands",
            "Wildlife officials warned that invasive apple snails are damaging local habitats.",
            "Local News",
            "Google News US",
        )

        self.assertEqual("noise", analysis["relationScope"])
        self.assertLess(analysis["relevanceScore"], 35)
        self.assertIn("일반 명사", analysis["excludedReason"])
        self.assertEqual([], analysis["ontologyRelations"])

    def test_english_article_summary_keeps_concrete_article_facts(self):
        target = NewsCollectionTarget("AAPL", "Apple", "NASDAQ", "USD", "AI")

        summary = korean_article_summary(
            target,
            "World in Brief: Apple sues OpenAI; Trump says Iran talks to resume - The Economist",
            "Apple sues OpenAI in a legal dispute over artificial intelligence products. Shares were little changed in pre-market trading.",
            analysis={"relationScope": "direct", "eventType": "regulation"},
        )

        self.assertIn("Apple가 OpenAI를 상대로 소송을 제기", summary)
        self.assertIn("AI 제품을 둘러싼 법적 분쟁", summary)
        self.assertIn("프리마켓에서 큰 변화가 없었다", summary)
        self.assertNotIn("관련 뉴스입니다", summary)
        self.assertNotIn("뉴스 유형은", summary)

    def test_article_summary_filters_google_news_boilerplate(self):
        target = NewsCollectionTarget("000660", "SK하이닉스", "KOSPI", "KRW", "반도체")
        boilerplate = "Comprehensive up-to-date news coverage, aggregated from sources all over the world by Google News."

        summary = korean_article_summary(
            target,
            'SK하이닉스 美 상장에 외신 "역사적 데뷔"... 월가 "반도체 랠리 가능성"',
            boilerplate,
            analysis={"relationScope": "direct", "eventType": "listing"},
        )

        self.assertIn("SK하이닉스 美 상장", summary)
        self.assertNotIn("Comprehensive", summary)
        self.assertNotIn("Google News", summary)
        self.assertNotIn("상승-으로-date", summary)
        self.assertNotIn("aggregated", summary)

    def test_stored_summary_noise_is_removed_before_rendering(self):
        cleaned = clean_article_summary_noise(
            '본문 요약: SK하이닉스 상장 이슈입니다. 상장/거래시장 관련 핵심 내용은 Comprehensive 상승-으로-date news coverage, aggregated 에서 sources all 관련해 world by Google News입니다. 핵심 키워드는 반도체입니다.'
        )

        self.assertIn("SK하이닉스 상장 이슈", cleaned)
        self.assertIn("핵심 키워드는 반도체", cleaned)
        self.assertNotIn("Comprehensive", cleaned)
        self.assertNotIn("Google News", cleaned)


if __name__ == "__main__":
    unittest.main()
