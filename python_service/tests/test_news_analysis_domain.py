import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence, research_evidence_from_facts
from digital_twin.application.news_ai_analysis_service import int_setting
from digital_twin.domain.news_analysis import (
    article_analysis_facts,
    article_sentence_candidates,
    classify_news_relevance,
    classify_news_event_type,
    clean_article_body_text,
    clean_article_summary_noise,
    english_fragment_to_korean,
    keyword_polarity,
    korean_article_summary,
    numeric_highlights,
    news_state_payload,
    relation_scope_is_investable,
    source_trust_state_for_source,
    stock_impact_analysis,
)
from digital_twin.domain.news_ai_analysis import (
    NewsAiAnalysis,
    apply_news_ai_analysis,
    article_body_quality_needs_refresh,
    build_news_ai_analysis_prompt,
    local_news_ai_analysis,
    normalize_ai_analysis,
    summary_quality_payload,
    summary_texts_similar,
)
from digital_twin.news_intelligence.domain.article import article_enrichment_revision
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.materiality import evidence_materiality
from digital_twin.domain.ontology_relation_reasoning import research_evidence_facts
from digital_twin.domain.ontology_schema import add_entity
from digital_twin.domain.portfolio_ontology_research_concepts import add_research_evidence_concepts
from digital_twin.infrastructure.news_ai_analyzer import FallbackNewsAiAnalyzer, news_ai_analyzer_from_settings


class NewsAnalysisDomainTests(unittest.TestCase):
    def test_enrichment_revision_ignores_operational_timestamps(self):
        base = {
            "articleSourceRevision": "news-source:stable",
            "articleSummaryKo": "애플이 연간 매출 전망을 상향했습니다.",
            "summaryQualityState": "ready",
            "translationStatus": "complete",
            "sourceLanguage": "en",
            "aiAnalysis": {
                "status": "ok",
                "version": "news-ai-analysis-test",
                "externalCompletedAt": "2026-08-27T00:00:00Z",
            },
            "evidenceGovernance": {"checkedAt": "2026-08-27T00:00:00Z", "dataState": "sufficient"},
            "promptEvidenceAdmission": {"checkedAt": "2026-08-27T00:00:00Z", "eligible": True},
        }
        replay = {
            **base,
            "aiAnalysis": {**base["aiAnalysis"], "externalCompletedAt": "2026-08-28T00:00:00Z"},
            "evidenceGovernance": {**base["evidenceGovernance"], "checkedAt": "2026-08-28T00:00:00Z"},
            "promptEvidenceAdmission": {**base["promptEvidenceAdmission"], "checkedAt": "2026-08-28T00:00:00Z"},
        }

        self.assertEqual(article_enrichment_revision(base), article_enrichment_revision(replay))

    def test_inline_decision_contract_requires_verified_direct_body_event(self):
        eligible = normalize_ai_analysis({
            "readScope": "body",
            "impactPolarity": "support",
            "relevanceState": "direct",
            "sourceTrustState": "trusted",
            "materialityState": "material",
            "dataState": "sufficient",
            "validationState": "ready",
            "decisionInlineEligible": True,
            "decisionInlineReasonKo": "회사가 공식적으로 공개한 신규 공급 계약이 수요 전망을 직접 강화합니다.",
            "needsReview": False,
        }).to_dict()
        partner_story = normalize_ai_analysis({
            "readScope": "body",
            "impactPolarity": "support",
            "relevanceState": "related",
            "sourceTrustState": "trusted",
            "materialityState": "material",
            "dataState": "sufficient",
            "validationState": "ready",
            "decisionInlineEligible": True,
            "decisionInlineReasonKo": "파트너사 자체 성과입니다.",
            "needsReview": False,
        }).to_dict()

        self.assertTrue(eligible["decisionInlineEligible"])
        self.assertFalse(partner_story["decisionInlineEligible"])

    def test_google_result_boundary_drops_unrelated_following_article(self):
        target = NewsCollectionTarget("000660", "SK하이닉스", "KOSPI", "KRW", "반도체")
        body = (
            "적자 나면 임금 깎자고? 성과급 주식 지급과 일정 기간 매도 제한을 두고 "
            "SK하이닉스 노조가 반발하면서 임단협 진통이 예상된다. 회사는 제안을 수정하지 않으면 "
            "갈등이 길어질 수 있다고 설명했다. "
            "Google 검색에서 한국경제 기사를 더 자주 볼 수 있습니다. "
            "최태원 SK그룹 회장이 SK하이닉스 주식 약 48억원어치를 장내매수했다."
        )

        cleaned = clean_article_body_text(body)
        facts = article_analysis_facts(
            target,
            '"적자 땐 임금조정"…SK하이닉스 제안',
            body,
            "",
            {"relationScope": "direct"},
            read_status="body",
            body_minimum_chars=280,
        )

        self.assertNotIn("48억원", cleaned)
        self.assertNotIn("Google 검색", cleaned)
        self.assertFalse(facts["bodyQualityPassed"])
        self.assertEqual("limited", facts["bodyQualityState"])

    def test_reenrichment_blocks_legacy_body_after_google_result_boundary(self):
        target = NewsCollectionTarget("000660", "SK하이닉스", "KOSPI", "KRW", "반도체")
        evidence = ResearchEvidence(
            "research:000660:news:google-result-boundary",
            "000660",
            "news",
            "한국경제",
            '"적자 땐 임금조정"…SK하이닉스 제안',
            "최태원 SK그룹 회장이 SK하이닉스 주식 약 48억원어치를 장내매수했다.",
            "https://www.hankyung.com/article/example",
            "2026-08-01T00:00:00Z",
            "risk",
            published_at="2026-08-01T00:00:00Z",
            raw_payload={
                "name": "SK하이닉스",
                "relationScope": "direct",
                "materialityPassed": True,
                "articleReadStatus": "body",
                "articleText": (
                    "적자 나면 임금 깎자고? 성과급 주식 지급과 일정 기간 매도 제한을 두고 "
                    "SK하이닉스 노조가 반발하면서 임단협 진통이 예상된다. 회사는 제안을 수정하지 않으면 "
                    "갈등이 길어질 수 있다고 설명했다. "
                    "Google 검색에서 한국경제 기사를 더 자주 볼 수 있습니다. "
                    "최태원 SK그룹 회장이 SK하이닉스 주식 약 48억원어치를 장내매수했다."
                ),
                "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
                "qualityGate": {"decision": "accept", "passed": True},
            },
        )

        updated = apply_news_ai_analysis(evidence, local_news_ai_analysis(target, evidence).to_dict())
        assessment = evidence_materiality(updated)

        self.assertFalse(updated.raw_payload["bodyQualityPassed"])
        self.assertFalse(updated.raw_payload["qualityGate"]["passed"])
        self.assertFalse(assessment.passed)
        self.assertEqual("blocked", assessment.review_level)

    def test_numeric_highlights_ignores_ranks_dates_and_b2b_labels(self):
        values = numeric_highlights("B2B 시장 1위는 26일 $700 billion 투자와 731조원 수주를 발표했다.")

        self.assertEqual(["$700 billion", "731조"], values)

    def test_normalizes_article_summary_fields_without_repeating_the_same_fact(self):
        fallback = NewsAiAnalysis(
            summary={
                "whyItMatters": "서비스 가격 인상은 매출과 고객 이탈률에 함께 영향을 줄 수 있습니다.",
                "watchPoints": ["다음 분기 서비스 매출과 고객 이탈률"],
            },
        )

        analysis = normalize_ai_analysis({
            "summary": {
                "oneLineKo": "Apple이 서비스 가격을 10% 인상했습니다.",
                "briefKo": "기사 요약: Apple이 서비스 가격을 10% 인상했습니다. Apple이 서비스 가격을 10% 인상했습니다.",
                "keyTakeaways": [
                    "Apple이 서비스 가격을 10% 인상했습니다.",
                    "인상은 다음 결제일부터 적용됩니다.",
                ],
                "whyItMatters": "Apple이 서비스 가격을 10% 인상했습니다.",
                "watchPoints": [
                    "Apple이 서비스 가격을 10% 인상했습니다.",
                    "다음 분기 서비스 매출과 고객 이탈률",
                ],
            },
        }, fallback).to_dict()["summary"]

        self.assertEqual(1, analysis["briefKo"].count("10% 인상"))
        self.assertEqual(["인상은 다음 결제일부터 적용됩니다"], analysis["keyTakeaways"])
        self.assertIn("매출과 고객 이탈률", analysis["whyItMatters"])
        self.assertEqual(["다음 분기 서비스 매출과 고객 이탈률"], analysis["watchPoints"])
        self.assertFalse(summary_texts_similar(analysis["briefKo"], analysis["whyItMatters"]))

    def test_summary_numeric_grounding_accepts_equivalent_korean_magnitudes_and_ranges(self):
        quality = summary_quality_payload(
            "회사는 전망을 1,080억 달러로 제시했고 총마진은 71~72%, 오차 범위는 50bp라고 밝혔다.",
            "The company gave a $108B outlook and expects gross margin of 71% to 72%, plus or minus 50 basis points.",
            "Nvidia",
        )

        self.assertEqual("ready", quality["state"])
        self.assertEqual([], quality["numericGrounding"]["unmatched"])

    def test_summary_numeric_grounding_reports_the_unmatched_token_and_nearest_source(self):
        quality = summary_quality_payload(
            "회사는 전망을 1,200억 달러로 제시해 향후 매출 기대를 높였다.",
            "The company gave a $108B revenue outlook.",
            "Nvidia",
        )

        self.assertEqual("blocked", quality["state"])
        mismatch = quality["numericGrounding"]["unmatched"][0]
        self.assertEqual("1,200억 달러", mismatch["token"])
        self.assertEqual(108_000_000_000.0, mismatch["nearestSource"]["normalizedValue"])

    def test_summary_numeric_grounding_maps_zero_word_to_numeric_zero(self):
        quality = summary_quality_payload(
            "Strategy의 순레버리지가 0에 가까워졌다고 설명한다.",
            "Strategy said its net leverage is now near zero.",
            "Strategy",
        )

        self.assertEqual("ready", quality["state"])
        self.assertEqual([], quality["numericGrounding"]["unmatched"])

    def test_summary_document_length_is_advisory_not_an_investment_number(self):
        quality = summary_quality_payload(
            "제공된 263자 분량은 문장이 중간에서 끝나 핵심 사건을 완결적으로 확인하기 어렵다.",
            "The supplied preview ends before the company action is fully described.",
            "Nvidia",
        )

        self.assertNotEqual("blocked", quality["state"])
        self.assertIn("summary-document-metadata-number", quality["advisories"])

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
        self.assertEqual("direct", analysis["relevanceState"])
        self.assertEqual("trusted", analysis["sourceTrustState"])
        self.assertEqual("material", analysis["materialityState"])
        self.assertTrue(any(item["type"] == "NEWS_SUPPORTS_ENTRY" for item in analysis["ontologyRelations"]))
        self.assertEqual("conditional", analysis["validationState"])

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
        self.assertEqual("unrelated", analysis["relevanceState"])
        self.assertEqual([], analysis["ontologyRelations"])
        self.assertEqual("exclude", analysis["qualityGate"]["decision"])
        self.assertFalse(analysis["directMention"])
        self.assertTrue(any("카카오게임즈" in item.get("terms", []) for item in analysis["entityLinks"]))

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

    def test_ai_neutral_impact_replaces_directional_article_fact_and_preserves_audit_value(self):
        evidence = ResearchEvidence(
            "research:005930:news:neutralized",
            "005930",
            "news",
            "Reuters",
            "Samsung announces routine operating update",
            "방향성이 확인되지 않은 운영 업데이트입니다.",
            "https://example.test/neutralized",
            "2026-07-20T01:00:00Z",
            "risk",
            70,
            0.8,
            "2026-07-20T01:00:00Z",
            raw_payload={
                "stockImpactPolarity": "risk",
                "stockImpactLabel": "악재",
                "articleFacts": {
                    "bodyAvailable": True,
                    "stockImpact": "negative",
                    "stockImpactPolarity": "risk",
                    "stockImpactLabel": "악재",
                },
            },
        )

        updated = apply_news_ai_analysis(evidence, {
            "status": "ok",
            "impactPolarity": "neutral",
            "impactLabelKo": "중립",
            "confidence": 0.76,
            "materialityScore": 55,
            "summary": {"briefKo": "주가 방향을 정할 근거가 부족합니다."},
        })

        facts = updated.raw_payload["articleFacts"]
        self.assertEqual("context", updated.polarity)
        self.assertEqual("context", facts["stockImpactPolarity"])
        self.assertEqual("중립", facts["stockImpactLabel"])
        self.assertEqual("risk", facts["preAiStockImpactPolarity"])
        self.assertTrue(updated.raw_payload["analysisConflict"])

    def test_ai_article_analysis_ignores_never_miss_boilerplate_for_listing(self):
        target = NewsCollectionTarget("000660", "SK하이닉스", "KOSPI", "KRW", "반도체")
        evidence = ResearchEvidence(
            "research:000660:news:hynix-listing",
            "000660",
            "news",
            "Yahoo Finance",
            "SK hynix (KOSE:A000660) Joins The NASDAQ Composite After Its Major US Listing",
            "SK hynix joins the NASDAQ Composite after its major US listing. Never miss important update on your portfolio and cut through noise.",
            "https://example.test/hynix-listing",
            "2026-07-17T07:13:00Z",
            "context",
            76,
            0.58,
            "2026-07-17T07:13:00Z",
            raw_payload={
                "relationScope": "direct",
                "relevanceScore": 97,
                "materialityScore": 76,
                "sourceReliability": 58,
                "articleReadStatus": "body",
                "articleFacts": {
                    "bodyAvailable": True,
                    "feedSummaryPreview": "Never miss important update on your portfolio and cut through noise.",
                    "bodyPreview": "SK hynix joins the NASDAQ Composite after its major US listing. Never miss important update on your portfolio and cut through noise.",
                },
            },
        )

        analysis = local_news_ai_analysis(target, evidence).to_dict()

        self.assertNotEqual("risk", analysis["impactPolarity"])
        self.assertNotIn("miss", analysis["riskSignals"])
        self.assertIn("NASDAQ Composite 편입", analysis["summary"]["oneLineKo"])
        self.assertNotIn("Never miss", analysis["summary"]["briefKo"])
        self.assertIn("당장 방향성 근거보다 이벤트 확인용 정보", analysis["portfolioImplicationKo"])

    def test_ontology_projection_materializes_article_ai_analysis_node(self):
        evidence = ResearchEvidence(
            "research:AAPL:news:ai",
            "AAPL",
            "news",
            "Reuters",
            "Apple shares fall on earnings concern",
            "실적 우려",
            "https://example.test/apple-ai",
            "2026-07-10T01:00:00Z",
            "risk",
            88,
            0.82,
            "2026-07-10T01:00:00Z",
            raw_payload={
                "relationScope": "direct",
                "relevanceScore": 96,
                "sourceReliability": 90,
                "materialityScore": 88,
                "aiAnalysis": {
                    "version": "news-ai-analysis-v1",
                    "model": "unit",
                    "impactPolarity": "risk",
                    "impactLabelKo": "악재",
                    "confidence": 0.82,
                    "materialityScore": 88,
                    "summary": {
                        "oneLineKo": "실적 우려 기사",
                        "briefKo": "실적 우려로 가격 부담을 확인합니다.",
                        "watchPoints": ["가격 반응"],
                    },
                    "riskSignals": ["실적 우려"],
                },
            },
        )
        graph = PortfolioOntology("test")

        add_research_evidence_concepts(
            graph,
            "stock:AAPL",
            "",
            "",
            "AAPL",
            {},
            {"researchEvidence": {"AAPL": [evidence.to_dict()]}},
        )

        ai_entities = [item for item in graph.entities if item.kind == "article-ai-analysis"]
        self.assertEqual(1, len(ai_entities))
        self.assertEqual("ArticleAIAnalysis", ai_entities[0].properties["tboxClass"])
        self.assertEqual("risk", ai_entities[0].properties["impactPolarity"])
        self.assertTrue(any(item.relation_type == "HAS_ANALYSIS" for item in graph.relations))

    def test_ontology_projection_materializes_official_document_analysis(self):
        evidence = ResearchEvidence(
            "research:005930:dart:official",
            "005930",
            "disclosure",
            "OpenDART",
            "자기주식 취득 결정",
            "회사가 자기주식 취득을 결의했습니다.",
            "https://dart.fss.or.kr/example",
            "2026-08-25T00:00:00Z",
            "support",
            published_at="2026-08-25T00:00:00Z",
            raw_payload={
                "relationScope": "direct",
                "officialDocumentState": "document-verified",
                "metadataVerified": True,
                "documentVerified": True,
                "analysisReady": True,
                "documentHash": "document-hash",
                "sourceRevision": "202608250001",
                "sourceAsOf": "2026-08-25T00:00:00Z",
                "disclosureAnalysis": {
                    "status": "ready",
                    "version": "disclosure-analysis-v5",
                    "summary": "회사가 자기주식 취득을 결의했습니다.",
                    "confirmedFacts": ["보통주 1,000,000주를 취득합니다."],
                },
            },
        )
        graph = PortfolioOntology("official-disclosure")

        add_research_evidence_concepts(
            graph,
            "stock:005930",
            "",
            "",
            "005930",
            {},
            {"researchEvidence": {"005930": [evidence.to_dict()]}},
        )

        research = next(item for item in graph.entities if item.kind == "research-evidence")
        self.assertEqual("document-verified", research.properties["officialDocumentState"])
        self.assertEqual("202608250001", research.properties["sourceRevision"])
        self.assertEqual(
            "회사가 자기주식 취득을 결의했습니다.",
            research.properties["disclosureAnalysis"]["summary"],
        )

    def test_english_legal_keyword_uses_word_boundary(self):
        self.assertEqual("regulation", classify_news_event_type("Apple sues OpenAI", "legal dispute"))
        self.assertNotEqual("regulation", classify_news_event_type("Apple issues software update", "general product release"))
        self.assertEqual("regulation", classify_news_event_type("금감원 조사 착수", "회사를 조사 대상으로 지정"))
        self.assertEqual("risk", keyword_polarity("금감원 조사 착수"))

    def test_concrete_corporate_action_in_title_beats_background_earnings_terms(self):
        self.assertEqual(
            "acquisition",
            classify_news_event_type(
                "Nvidia Is Buying Hugging Face for $12.9 Billion",
                "The announcement followed Nvidia earnings and revenue growth.",
            ),
        )
        self.assertEqual(
            "strategic_investment",
            classify_news_event_type(
                "Nvidia invests in CoreWeave",
                "The company discussed the investment after quarterly results.",
            ),
        )
        self.assertEqual(
            "capital_policy",
            classify_news_event_type(
                "Nvidia is buying back $5 billion of shares",
                "The article also reviews acquisition activity and earnings.",
            ),
        )

    def test_headline_specific_event_types_beat_generic_body_terms(self):
        cases = (
            ("supply_chain", "SK Hynix breaks ground on new fab", "Revenue and profit were also discussed."),
            ("product", "Apple unveils foldable iPhone", "The launch may affect future earnings."),
            ("guidance", "Hyundai raises annual guidance", "The company also discussed partnerships."),
            ("labor", "Hyundai union begins collective bargaining", "Production and supply could be affected."),
            ("reorganization", "Nvidia announces organization restructuring", "Revenue growth remains strong."),
        )
        for expected, title, summary in cases:
            with self.subTest(title=title):
                self.assertEqual(expected, classify_news_event_type(title, summary))

    def test_market_roundup_headlines_are_not_company_earnings_events(self):
        self.assertEqual(
            "price_commentary",
            classify_news_event_type(
                "Premarket movers: Nvidia, Salesforce and CrowdStrike rally on earnings",
                "Nvidia revenue and profit beat estimates.",
            ),
        )

    def test_legacy_news_payload_uses_publisher_state_without_reliability_number(self):
        states = news_state_payload({
            "provider": "Reuters",
            "relevanceScore": 91,
            "stockImpactLabel": "중립",
        })

        self.assertEqual("direct", states["relevanceState"])
        self.assertEqual("trusted", states["sourceTrustState"])
        self.assertEqual("conditional", states["validationState"])

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
