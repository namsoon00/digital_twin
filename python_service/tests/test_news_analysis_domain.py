import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.news_intelligence.domain.investment_research import NewsCollectionTarget, ResearchEvidence, research_evidence_from_facts
from digital_twin.modules.news_intelligence.application.news_ai_analysis_service import int_setting
from digital_twin.modules.news_intelligence.domain.news_analysis import (
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
    target_relevant_article_text,
)
from digital_twin.modules.news_intelligence.domain.news_ai_analysis import (
    NewsAiAnalysis,
    apply_news_ai_analysis,
    article_body_quality_needs_refresh,
    build_news_ai_analysis_prompt,
    local_news_ai_analysis,
    normalize_ai_analysis,
    summary_quality_payload,
    summary_texts_similar,
    news_ai_analysis_is_current,
    news_source_contract,
    refreshed_article_summary_quality,
)
from digital_twin.modules.news_intelligence.domain.article_source_contract import assess_article_source_grounding
from digital_twin.modules.news_intelligence.domain.article import article_enrichment_revision
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.news_intelligence.domain.materiality import evidence_materiality
from digital_twin.modules.reasoning.domain.ontology_relation_reasoning import research_evidence_facts
from digital_twin.modules.reasoning.domain.ontology_schema import add_entity
from digital_twin.modules.reasoning.domain.portfolio_ontology_research_concepts import add_research_evidence_concepts
from digital_twin.infrastructure.news_ai_analyzer import FallbackNewsAiAnalyzer, news_ai_analyzer_from_settings


class NewsAnalysisDomainTests(unittest.TestCase):
    def source_contract_evidence(self):
        return ResearchEvidence(
            "research:005380:news:source-contract", "005380", "news", "연합뉴스",
            "현대차 하이브리드 수출 증가", "현대차의 하이브리드 수출이 증가했다.",
            "https://example.test/export", "2026-09-15T00:00:00Z", "context",
            raw_payload={
                "name": "현대차", "relationScope": "direct", "articleReadStatus": "body",
                "articleText": (
                    "현대차는 하이브리드 수출이 작년 같은 기간보다 70% 증가했다고 밝혔다. "
                    "현대차 재무책임자는 과거 실적 발표에서 유럽 전기차 제품군이 부족하다고 설명했다."
                ),
                "articleFacts": {"bodyAvailable": True},
            },
        )

    def source_contract_answer(self, evidence):
        contract = news_source_contract(evidence)
        ids = contract["primaryEventCandidateIds"]
        return {
            "status": "ok", "needsReview": False, "readScope": "body",
            "summary": {
                "oneLineKo": "현대차의 하이브리드 수출이 전년 같은 기간보다 70% 증가했다.",
                "briefKo": "현대차의 하이브리드 수출이 전년 같은 기간보다 70% 증가했다고 회사가 밝혔다.",
            },
            "sourceGrounding": {"headlineStatus": "confirmed", "primaryEventSourceIds": ids, "summarySourceIds": ids},
        }

    def test_source_contract_retains_main_event_and_accepts_grounded_summary(self):
        evidence = self.source_contract_evidence()
        result = apply_news_ai_analysis(evidence, self.source_contract_answer(evidence))
        self.assertEqual("ready", result.raw_payload["articleSummaryQuality"]["state"])
        self.assertTrue(news_ai_analysis_is_current(result))
        self.assertEqual("ready", refreshed_article_summary_quality(result)["state"])
        prompt = json.loads(build_news_ai_analysis_prompt(NewsCollectionTarget("005380", "현대차", "", "", ""), evidence))
        self.assertTrue(prompt["article"]["sourceContract"]["primaryEventCandidateIds"])
        self.assertIn("70%", prompt["article"]["targetRelevantBodyPreview"])

    def test_background_only_citations_do_not_verify_primary_summary(self):
        evidence = self.source_contract_evidence()
        answer = self.source_contract_answer(evidence)
        contract = news_source_contract(evidence)
        background = [row["id"] for row in contract["passages"] if "재무책임자" in row["text"]]
        self.assertTrue(background)
        answer["sourceGrounding"]["summarySourceIds"] = background
        self.assertIn("summary-primary-event-omitted", assess_article_source_grounding(answer, contract)["issues"])

    def test_unknown_and_cross_article_passages_cannot_validate_summary(self):
        for references in (["article-passage:another-article"], 42, "not-a-list", [{"id": "invalid"}]):
            with self.subTest(references=references):
                evidence = self.source_contract_evidence()
                answer = self.source_contract_answer(evidence)
                answer["sourceGrounding"]["summarySourceIds"] = references
                result = apply_news_ai_analysis(evidence, answer)
                self.assertEqual("blocked", result.raw_payload["summaryQualityState"])
                self.assertFalse(result.raw_payload["newsEligibility"]["alertEligible"])
                self.assertEqual("source-review", result.raw_payload["aiAnalysis"]["status"])

    def test_number_from_uncited_background_cannot_leak_into_one_line_summary(self):
        evidence = self.source_contract_evidence()
        evidence.raw_payload["articleText"] += " 현대차는 과거 투자 계획의 금액을 90억원으로 제시했다."
        answer = self.source_contract_answer(evidence)
        answer["summary"]["oneLineKo"] = "현대차의 하이브리드 수출 금액은 90억원으로 집계됐다고 밝혔다."
        result = apply_news_ai_analysis(evidence, answer)
        self.assertIn("summary-number-not-grounded", result.raw_payload["articleSummaryQuality"]["issues"])
        self.assertFalse(result.raw_payload["newsEligibility"]["alertEligible"])

    def test_source_repair_is_bounded_and_reset_by_new_source(self):
        evidence = self.source_contract_evidence()
        answer = self.source_contract_answer(evidence)
        answer["sourceGrounding"]["headlineStatus"] = "mismatch"
        for attempt in range(1, 4):
            evidence = apply_news_ai_analysis(evidence, answer)
            self.assertEqual(attempt, evidence.raw_payload["aiAnalysis"]["sourceRepairAttempts"])
        self.assertEqual("source-invalid", evidence.raw_payload["aiAnalysis"]["status"])
        prompt = json.loads(build_news_ai_analysis_prompt(NewsCollectionTarget("005380", "현대차", "", "", ""), evidence))
        self.assertIn("headline-body-event-mismatch", prompt["repairFeedback"]["issues"])
        evidence.raw_payload["articleText"] += " 현대차는 다음 달에 지역별 판매 수치를 공개한다고 밝혔다."
        evidence = apply_news_ai_analysis(evidence, answer)
        self.assertEqual(1, evidence.raw_payload["aiAnalysis"]["sourceRepairAttempts"])

    def test_generated_summary_is_never_reused_as_source_body(self):
        evidence = self.source_contract_evidence()
        evidence.raw_payload.pop("articleText")
        evidence.raw_payload["aiAnalysis"] = {"status": "ok"}
        evidence.raw_payload["articleSummaryKo"] = "현대차가 상장을 추진한다는 잘못된 요약"
        self.assertEqual([], news_source_contract(evidence)["passages"])

    def test_explicit_headline_refutation_is_not_automatically_blocked(self):
        evidence = self.source_contract_evidence()
        answer = self.source_contract_answer(evidence)
        answer["sourceGrounding"]["headlineStatus"] = "contradicted"
        self.assertTrue(assess_article_source_grounding(answer, news_source_contract(evidence))["passed"])

    def test_primary_export_facts_survive_target_action_ranking(self):
        target = NewsCollectionTarget("005380", "현대차", "KOSPI", "KRW", "자동차")
        title = "현대차 하이브리드 대미수출 증가…기아는 유럽 전기차 수출 증가"
        body = (
            "현대차 하이브리드 대미수출 증가…기아는 전기차 증가... "
            "현대차와 기아의 하이브리드 및 전기차 수출이 올해 들어 증가했다. "
            "특히 현대차가 미국에 보낸 하이브리드차는 작년 동기 대비 70.1％ 급증했다. "
            "현대차·기아의 친환경차 수출은 올해 1~7월 59만대로 22.3% 증가했다. "
            "현대차 재무책임자는 과거 실적 발표에서 유럽 소형 전기차 제품이 없다고 말했다. "
            "그는 해당 제품에 대한 투자 계획을 설명했다."
            " 다른 기사의 추천 제목... 또 다른 추천 기사… 목록입니다."
        )
        selected = target_relevant_article_text(target, title, body)
        self.assertIn("70.1％ 급증", selected)
        self.assertIn("22.3% 증가", selected)
        self.assertLess(selected.index("수출"), selected.index("재무책임자"))

    def test_short_headline_is_not_a_verified_body_passage(self):
        evidence = self.source_contract_evidence()
        evidence.raw_payload["articleText"] = evidence.title + " 연합뉴스"
        self.assertEqual([], news_source_contract(evidence)["passages"])

    def test_source_scope_keeps_adjacent_sales_counterpoint_but_not_market_widgets(self):
        target = NewsCollectionTarget("TSLA", "Tesla", "NASDAQ", "USD", "")
        body = ("Tesla China sales at home continue to slide. It's offering new cash incentives through Sept. 30. "
                "But exports remain strong. U.S. markets close in 4h 53m Other News About Tesla")
        scoped = target_relevant_article_text(target, "Tesla China sales fall; exports remain strong", body)
        self.assertIn("exports remain strong", scoped)
        self.assertIn("Sept. 30", scoped)
        self.assertNotIn("markets close", scoped)

    def test_publisher_navigation_without_body_cannot_supply_another_story(self):
        for prefix in ("", "LG전자 교육환경 개선. "):
            with self.subTest(prefix=prefix):
                cleaned = clean_article_body_text(
                    prefix + "Google 검색에서 한국경제 기사를 더 자주 볼 수 있습니다. "
                    "LG전자 자회사가 나스닥 상장을 추진하며 자금을 조달한다."
                )
                self.assertNotIn("상장", cleaned)
                self.assertNotIn("Google", cleaned)

    def test_sales_incentive_is_not_share_offering(self):
        self.assertEqual("product", classify_news_event_type(
            "Tesla Sales In China Continue Negative Streak; Exports Reach Key Level",
            "Tesla is offering cash incentives to vehicle buyers. China sales fell while exports rose.",
        ))
        self.assertEqual("capital_policy", classify_news_event_type(
            "Company announces a public offering of common stock", "New shares will be issued.",
        ))

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

    def test_korean_publisher_footer_is_removed_from_article_body(self):
        body = (
            "네이버는 신규 검색 서비스를 공개했고 하반기부터 이용 대상을 확대한다고 밝혔다. "
            "회사는 해당 서비스가 광고 매출과 검색 이용률에 미칠 영향을 다음 분기부터 공개할 예정이다. "
            "기사에 대해 반론·정정추후 보도를 청구하실 분은 담당자에게 연락해 주십시오. "
            "고충처리인 홍길동 contact@example.com 02-0000-0000"
        )

        cleaned = clean_article_body_text(body)

        self.assertIn("다음 분기부터 공개할 예정이다", cleaned)
        self.assertNotIn("반론", cleaned)
        self.assertNotIn("고충처리인", cleaned)
        self.assertNotIn("contact@example.com", cleaned)

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
        fullwidth = summary_quality_payload(
            "회사의 미국행 하이브리드차 수출은 전년 대비 70.1% 증가했다고 밝혔다.",
            "미국행 하이브리드차 수출은 작년 동기 대비 70.1％ 증가했다.",
        )
        self.assertEqual("ready", fullwidth["state"])
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
                "articleText": "Samsung announced a routine operating update without a change to its outlook.",
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
            "translatedTitleKo": "삼성전자, 정기 운영 업데이트 발표",
            "translationStatus": "complete",
            "confidence": 0.76,
            "materialityScore": 55,
            "sourceGrounding": {
                "headlineStatus": "confirmed",
                "primaryEventSourceIds": news_source_contract(evidence)["primaryEventCandidateIds"],
                "summarySourceIds": news_source_contract(evidence)["primaryEventCandidateIds"],
            },
            "summary": {
                "oneLineKo": "삼성전자가 정기 운영 업데이트를 발표했습니다.",
                "briefKo": "주가 방향을 정할 근거가 부족합니다.",
            },
        })

        facts = updated.raw_payload["articleFacts"]
        self.assertEqual("context", updated.polarity)
        self.assertEqual("context", facts["stockImpactPolarity"])
        self.assertEqual("중립", facts["stockImpactLabel"])
        self.assertEqual("삼성전자가 정기 운영 업데이트를 발표했습니다", facts["eventTakeaway"])
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
                "eventType": "earnings",
                "topicTags": ["earnings"],
                "mentionedPeers": ["Microsoft"],
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
        reference_entities = [
            item
            for item in graph.entities
            if item.kind in {"news-event-type", "news-topic", "peer-company"}
        ]
        self.assertEqual(3, len(reference_entities))
        for item in reference_entities:
            self.assertEqual("global", item.properties["referenceScope"])
            self.assertNotIn("symbol", item.properties)
            self.assertNotIn("materialityPassed", item.properties)
            self.assertNotIn("relationScope", item.properties)
            self.assertNotIn("reviewLevel", item.properties)
            self.assertNotIn("dataState", item.properties)

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
