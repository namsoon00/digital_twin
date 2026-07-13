import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence, research_evidence_from_facts
from digital_twin.domain.news_analysis import (
    article_analysis_facts,
    classify_news_relevance,
    classify_news_event_type,
    clean_article_summary_noise,
    confidence_from_analysis_payload,
    impact_from_analysis_payload,
    korean_article_summary,
    relation_scope_is_investable,
    source_reliability_score,
    stock_impact_analysis,
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

        self.assertEqual("platform_noise", analysis["relationScope"])
        self.assertLess(analysis["relevanceScore"], 35)
        self.assertIn("플랫폼/블로그", analysis["excludedReason"])
        self.assertFalse(relation_scope_is_investable(analysis["relationScope"]))
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

        self.assertEqual("platform_noise", analysis["relationScope"])
        self.assertLess(analysis["relevanceScore"], 35)
        self.assertIn("플랫폼/블로그", analysis["excludedReason"])
        self.assertEqual("Naver Blog", analysis["sourcePlatform"])
        self.assertEqual("exclude", analysis["qualityGate"]["decision"])
        self.assertTrue(any(item["role"] == "platform_reference" for item in analysis["entityLinks"]))

    def test_news_analysis_excludes_naver_platform_suffix_even_with_material_event_keyword(self):
        target = NewsCollectionTarget("035420", "NAVER", "KOSPI", "KRW", "플랫폼")

        analysis = classify_news_relevance(
            target,
            "지배구조 변화 첫 메시지…카카오게임즈, 자사주 소각 카드 꺼냈다 : 네이버 블로그",
            "RSS/제공 요약: 지배구조 변화 첫 메시지…카카오게임즈, 자사주 소각 카드 꺼냈다 : 네이버 블로그 Naver Blog.",
            "Naver Blog",
            "Google News KR",
        )

        self.assertEqual("platform_noise", analysis["relationScope"])
        self.assertLess(analysis["relevanceScore"], 35)
        self.assertEqual([], analysis["ontologyRelations"])
        self.assertEqual("exclude", analysis["qualityGate"]["decision"])
        self.assertFalse(analysis["directMention"])
        self.assertTrue(any("카카오게임즈" in item.get("terms", []) for item in analysis["entityLinks"]))

    def test_news_analysis_excludes_naver_premium_source_suffix_for_other_company_news(self):
        target = NewsCollectionTarget("035420", "NAVER", "KOSPI", "KRW", "플랫폼")

        analysis = classify_news_relevance(
            target,
            "SK하이닉스, 나스닥 데뷔 첫날 13% 급등 - 네이버 프리미엄콘텐츠",
            "RSS/제공 요약: SK하이닉스, 나스닥 데뷔 첫날 13% 급등 네이버 프리미엄콘텐츠.",
            "네이버 프리미엄콘텐츠",
            "Google News KR",
        )

        self.assertEqual("platform_noise", analysis["relationScope"])
        self.assertEqual("Naver Premium Contents", analysis["sourcePlatform"])
        self.assertFalse(relation_scope_is_investable(analysis["relationScope"]))
        self.assertTrue(any(item["role"] == "article_subject" and "SK하이닉스" in item.get("terms", []) for item in analysis["entityLinks"]))

    def test_news_analysis_keeps_real_naver_company_article_as_direct(self):
        target = NewsCollectionTarget("035420", "NAVER", "KOSPI", "KRW", "플랫폼")

        analysis = classify_news_relevance(
            target,
            "Naver Invests in AI, Commerce; Kakao Streamlines for Profit Growth - 조선일보",
            "Naver Invests in AI, Commerce; Kakao Streamlines for Profit Growth.",
            "조선일보",
            "Google News KR",
        )

        self.assertEqual("direct", analysis["relationScope"])
        self.assertTrue(analysis["directMention"])
        self.assertEqual("accept", analysis["qualityGate"]["decision"])
        self.assertTrue(relation_scope_is_investable(analysis["relationScope"]))

    def test_research_evidence_generation_skips_non_investable_platform_noise(self):
        evidence = research_evidence_from_facts("035420", {
            "symbol": "035420",
            "name": "NAVER",
            "sector": "플랫폼",
            "newsHeadlines": {
                "provider": "Google News KR",
                "items": [{
                    "title": "카카오게임즈, 자사주 소각 카드 꺼냈다 : 네이버 블로그",
                    "summary": "카카오게임즈 자사주 소각 관련 블로그 글입니다.",
                    "source": "Naver Blog",
                    "provider": "Google News KR",
                    "url": "https://blog.naver.com/example",
                    "payload": {
                        "analysisVersion": "news-analysis-v2-domain-ontology",
                        "relationScope": "direct",
                        "relevanceScore": 94,
                        "materialityScore": 82,
                    },
                }],
            },
        })

        self.assertEqual([], evidence)

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

    def test_english_preferred_share_summary_extracts_record_volume_point(self):
        target = NewsCollectionTarget("STRC", "Strategy Preferred", "US", "USD", "디지털자산")

        summary = korean_article_summary(
            target,
            "STRC and SATA Preferred Shares Hit Record $10B in Combined June Trading Volume Despite Bitcoin Dip",
            "STRC and SATA preferred shares hit record $10B in combined June trading volume despite Bitcoin dip.",
            analysis={"relationScope": "direct", "eventType": "price_commentary"},
        )

        self.assertIn("합산 거래대금", summary)
        self.assertIn("$10 billion", summary)
        self.assertIn("사상 최고", summary)
        self.assertIn("비트코인 하락", summary)
        self.assertNotIn("관련 뉴스입니다", summary)
        self.assertNotIn("뉴스 유형은", summary)

    def test_article_analysis_facts_collects_body_based_article_details(self):
        target = NewsCollectionTarget("STRC", "Strategy Preferred", "US", "USD", "디지털자산")
        title = "STRC and SATA Preferred Shares Hit Record $10B in Combined June Trading Volume Despite Bitcoin Dip"
        body = (
            "STRC and SATA preferred shares hit record $10B in combined June trading volume despite Bitcoin dip. "
            "The article says investor demand for Strategy preferred shares stayed active while crypto prices weakened."
        )
        analysis = classify_news_relevance(target, title, body, "CryptoRank", "Google News US")
        impact = stock_impact_analysis(target, title, body, "", analysis, "support", 12)

        facts = article_analysis_facts(
            target,
            title,
            body,
            "",
            analysis,
            impact,
            "CryptoRank",
            "Google News US",
            "https://example.test/strc",
            "2026-07-11T00:25:00Z",
            "body",
            "article-body",
            "body-read",
            "",
        )

        self.assertEqual("body", facts["readStatus"])
        self.assertTrue(facts["bodyAvailable"])
        self.assertGreater(facts["bodyCharCount"], 40)
        self.assertIn("$10B", facts["numbers"])
        self.assertIn("비트코인", facts["topics"])
        self.assertIn("합산 거래대금", facts["eventTakeaway"])
        self.assertTrue(facts["keySentences"])

    def test_stock_impact_analysis_explains_event_channel_and_watchpoint(self):
        target = NewsCollectionTarget("STRC", "Strategy Preferred", "US", "USD", "디지털자산")

        result = stock_impact_analysis(
            target,
            "Strategy CEO Says Convertible Debt Repayment Triggered STRC Plunge",
            "",
            "Strategy CEO says convertible debt repayment triggered STRC plunge.",
            analysis={
                "relationScope": "direct",
                "eventType": "capital_policy",
                "relevanceScore": 94.5,
                "materialityScore": 72,
            },
            polarity="risk",
            impact_score=11,
        )

        reason = result["stockImpactReasonKo"]
        self.assertIn("핵심:", reason)
        self.assertIn("전환사채 상환", reason)
        self.assertIn("급락 원인", reason)
        self.assertIn("영향 경로:", reason)
        self.assertIn("배당 지속성", reason)
        self.assertIn("확인:", reason)
        self.assertNotIn("중요도는 높지만 방향성 표현이 뚜렷하지 않습니다", reason)

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
