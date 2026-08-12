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
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.materiality import evidence_materiality
from digital_twin.domain.ontology_relation_reasoning import research_evidence_facts
from digital_twin.domain.ontology_schema import add_entity
from digital_twin.domain.portfolio_ontology_research_concepts import add_research_evidence_concepts
from digital_twin.infrastructure.news_ai_analyzer import FallbackNewsAiAnalyzer, news_ai_analyzer_from_settings


class NewsAnalysisDomainTests(unittest.TestCase):
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

    def test_summary_quality_accepts_korean_magnitude_translation_and_keeps_target_as_advisory(self):
        quality = summary_quality_payload(
            "시가총액은 5조 달러를 넘었고 올해 AI 데이터센터 구축에는 약 7,000억 달러가 투입됩니다.",
            "Nvidia market capitalization surpassed $5 trillion. Big tech companies are spending close to $700 billion this year building AI data centers.",
            "NVIDIA",
        )

        self.assertEqual("ready", quality["state"])
        self.assertNotIn("summary-number-not-grounded", quality["issues"])
        self.assertNotIn("summary-target-name-omitted", quality["issues"])

    def test_summary_quality_grounds_korean_quarter_number_from_english_ordinal(self):
        quality = summary_quality_payload(
            "애플은 3분기 서비스 매출이 증가했다고 발표했습니다.",
            "Apple reported that services revenue increased in the third quarter.",
            "Apple",
        )

        self.assertEqual("ready", quality["state"])
        self.assertNotIn("summary-number-not-grounded", quality["issues"])

    def test_summary_quality_normalizes_translated_months_and_korean_count_units(self):
        quality = summary_quality_payload(
            "테슬라는 7월 2분기에 48만 126대를 인도했고, 배터리는 20만 마일 뒤에도 성능을 유지했다. 누적 1,000만 번째 차량도 생산했다.",
            "In July, Tesla delivered 480,126 vehicles in Q2. Batteries retained capacity after 200,000 miles, and the company produced its 10-millionth EV.",
            "Tesla",
        )

        self.assertEqual("ready", quality["state"])
        self.assertNotIn("summary-number-not-grounded", quality["issues"])

    def test_summary_quality_normalizes_compound_korean_currency_and_word_counts(self):
        quality = summary_quality_payload(
            "애플의 매출은 1,094억2천만 달러였고 이를 추적하는 기관은 35곳입니다.",
            "Apple reported $109.42 billion in revenue and is tracked by thirty-five firms.",
            "Apple",
        )

        self.assertEqual("ready", quality["state"])
        self.assertNotIn("summary-number-not-grounded", quality["issues"])

    def test_summary_quality_does_not_treat_zero_padded_stock_code_as_article_number(self):
        quality = summary_quality_payload(
            "000660 관련성은 시장 심리 맥락 이상으로 판단하기 어렵습니다.",
            "The article lists Micron among several companies but gives no company-specific figures.",
            "SK하이닉스",
        )

        self.assertEqual("ready", quality["state"])
        self.assertNotIn("summary-number-not-grounded", quality["issues"])

    def test_korean_summary_uses_direct_target_body_and_flags_navigation_headlines(self):
        target = NewsCollectionTarget("066570", "LG전자", "KOSPI", "KRW", "가전/전자")
        body = (
            "LG전자가 미국 소비자 평가에서 가장 신뢰할 수 있는 주방가전 브랜드로 선정된 데 힘입어 "
            "현지 빌트인 시장 공략을 강화한다. LG전자, 美 신뢰 주방가전 1위…빌더시장 3위권 정조준 "
            "한화오션, 칠레 호위함 수주전 진출…남미 방산 영토확장 "
            "엔비디아, AI 메모리 공급 확보 유력…반도체 투자 확대"
        )

        summary = korean_article_summary(target, "LG전자, 美 신뢰 주방가전 1위…빌더시장 3위권 정조준", body, "")
        quality = summary_quality_payload(
            "한화오션 수주전… 남미 방산 확대… 엔비디아 메모리 공급… LG전자 주방가전",
            body,
            "LG전자",
        )

        self.assertIn("LG전자", summary)
        self.assertNotIn("한화오션", summary)
        self.assertNotIn("메모리", summary)
        self.assertNotIn("AI", summary)
        self.assertNotIn("확인된 수치", summary)
        self.assertEqual("needs-review", quality["state"])
        self.assertIn("summary-navigation-contamination", quality["issues"])

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

    def test_legacy_body_quality_gate_is_detected_even_with_current_ai_hash(self):
        target = NewsCollectionTarget("000660", "SK하이닉스", "KOSPI", "KRW", "반도체")
        evidence = ResearchEvidence(
            "research:000660:news:legacy-quality-gate",
            "000660",
            "news",
            "한국경제",
            '"적자 땐 임금조정"…SK하이닉스 제안',
            "SK하이닉스 노사 관련 기사입니다.",
            "https://www.hankyung.com/article/example",
            "2026-08-01T00:00:00Z",
            "risk",
            published_at="2026-08-01T00:00:00Z",
            raw_payload={
                "name": "SK하이닉스",
                "relationScope": "direct",
                "articleReadStatus": "body",
                "articleText": (
                    "적자 나면 임금 깎자고? 성과급 주식 지급과 일정 기간 매도 제한을 두고 "
                    "SK하이닉스 노조가 반발하면서 임단협 진통이 예상된다. 회사는 제안을 수정하지 않으면 "
                    "갈등이 길어질 수 있다고 설명했다. "
                    "Google 검색에서 한국경제 기사를 더 자주 볼 수 있습니다. "
                    "최태원 SK그룹 회장이 SK하이닉스 주식 약 48억원어치를 장내매수했다."
                ),
                "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
                "bodyQualityPassed": True,
                "qualityGate": {"decision": "accept", "passed": True},
            },
        )
        applied = apply_news_ai_analysis(evidence, local_news_ai_analysis(target, evidence).to_dict())
        # Reproduce a legacy persisted flag that was written before boundary cleanup.
        applied.raw_payload["articleFacts"]["bodyQualityPassed"] = True
        applied.raw_payload["bodyQualityPassed"] = True
        applied.raw_payload["qualityGate"] = {"decision": "accept", "passed": True}

        self.assertTrue(article_body_quality_needs_refresh(applied))

    def test_korean_wire_article_prioritizes_target_merger_review_over_unrelated_company_noise(self):
        target = NewsCollectionTarget("035420", "NAVER", "KOSPI", "KRW", "플랫폼")
        title = '美 쿠팡 보고서에…주병기 "배민·네이버에도 같은 잣대"(종합) - 연합뉴스'
        body = (
            "[연합뉴스 자료사진. 재판매 및 DB 금지] (세종=연합뉴스) 김수현 기자 = "
            "미국 측이 쿠팡 관련 조사를 비판한 데 대해 공정위는 쿠팡뿐 아니라 배달의민족, "
            "네이버 등 플랫폼 사업자를 같은 잣대로 제재한다고 밝혔다. "
            "이해진 네이버 이사회 의장이 행사에서 발언하고 있다. "
            "[네이버 제공. 연합뉴스 자료사진. 재판매 및 DB 금지] "
            "네이버와 두나무 합병 심사와 관련해선 \"연말 안에 완료할 수 있을 것\"이라고 전망했다. "
            "주 위원장은 \"네이버에 관련 자료 요청을 13회나 했고, 관련 산업·이해 관계자 의견 청취도 "
            "8월 말까지 상당 부분 완료된다\"며 연내 심사 완료 의지를 내비쳤다. "
            "배민 3천억·쿠팡 600억 규모 상생안 퇴짜와 관련해선 법 위반이 중대했다고 설명했다."
        )

        cleaned = clean_article_body_text(body)
        candidates = article_sentence_candidates(cleaned, target, {"eventType": "regulation"}, 5, headline=title)
        summary = korean_article_summary(target, title, cleaned, "", {"eventType": "regulation"})
        facts = article_analysis_facts(
            target,
            title,
            cleaned,
            "",
            {"eventType": "regulation", "relationScope": "direct"},
            source="연합뉴스",
            read_status="body",
        )

        self.assertNotIn("재판매", cleaned)
        self.assertNotIn("기자 =", cleaned)
        self.assertEqual("regulation", classify_news_event_type(title, cleaned))
        self.assertIn("합병 심사", candidates[0])
        self.assertIn("자료 요청", " ".join(candidates))
        self.assertNotIn("600억", " ".join(candidates))
        self.assertIn("합병 심사", summary)
        self.assertIn("자료 요청", summary)
        self.assertNotIn("600억", summary)
        self.assertNotIn("쿠팡 600억", facts["eventTakeaway"])
        self.assertNotIn("600억", facts["numbers"])

    def test_merger_review_status_guard_rejects_generic_guidance_summary(self):
        target = NewsCollectionTarget("035420", "NAVER", "KOSPI", "KRW", "플랫폼")
        title = '美 쿠팡 보고서에…주병기 "배민·네이버에도 같은 잣대"(종합) - 연합뉴스'
        body = (
            "[재판매 및 DB 금지] (세종=연합뉴스) 김수현 기자 = "
            "공정위는 쿠팡뿐 아니라 배달의민족, 네이버 등 플랫폼 사업자를 같은 잣대로 제재한다고 밝혔다. "
            "네이버와 두나무 합병 심사와 관련해선 \"연말 안에 완료할 수 있을 것\"이라고 전망했다. "
            "주 위원장은 \"네이버에 관련 자료 요청을 13회나 했고 관련 산업·이해 관계자 의견 청취도 "
            "8월 말까지 상당 부분 완료된다\"며 연내 심사 완료 의지를 내비쳤다. 쿠팡 600억 상생안도 언급됐다."
        )
        evidence = ResearchEvidence(
            "research:035420:news:merger-review-status",
            "035420",
            "news",
            "연합뉴스",
            title,
            title,
            "https://example.test/naver-merger-review",
            "2026-07-28T10:46:07Z",
            "context",
            published_at="2026-07-28T10:46:07Z",
            raw_payload={
                "name": "NAVER",
                "market": "KOSPI",
                "currency": "KRW",
                "sector": "플랫폼",
                "relationScope": "direct",
                "eventType": "regulation",
                "articleReadStatus": "body",
                "articleText": body,
                "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
            },
        )

        local = local_news_ai_analysis(target, evidence).to_dict()
        updated = apply_news_ai_analysis(evidence, {
            "status": "ok",
            "model": "test-external",
            "eventType": "guidance",
            "impactPolarity": "neutral",
            "impactLabelKo": "중립",
            "summary": {
                "oneLineKo": "쿠팡 600억 상생안이 향후 매출 전망을 바꿉니다.",
                "briefKo": "재판매 및 DB 금지] 쿠팡 600억 상생안으로 NAVER 매출 전망이 바뀝니다.",
                "whyItMatters": "앞으로의 매출·이익 눈높이를 바꾸는 재료입니다.",
                "watchPoints": ["실적 전망 변화"],
            },
            "keyNumbers": ["600억"],
        })
        prompt = json.loads(build_news_ai_analysis_prompt(target, updated))
        analysis = updated.raw_payload["aiAnalysis"]
        facts = updated.raw_payload["articleFacts"]

        self.assertEqual("regulation", local["eventType"])
        self.assertEqual("neutral", local["impactPolarity"])
        self.assertIn("합병 심사", local["summary"]["briefKo"])
        self.assertEqual("regulation", analysis["eventType"])
        self.assertEqual("neutral", analysis["impactPolarity"])
        self.assertIn("합병 심사", analysis["summary"]["briefKo"])
        self.assertNotIn("600억", analysis["summary"]["briefKo"])
        self.assertNotIn("재판매", analysis["summary"]["briefKo"])
        self.assertNotIn("매출", analysis["summary"]["whyItMatters"])
        self.assertEqual([], facts["numbers"])
        self.assertNotIn("600억", prompt["article"]["targetRelevantBodyPreview"])
        self.assertNotIn("재판매", prompt["article"]["bodyPreview"])

    def test_numeric_highlights_ignores_ranks_dates_and_b2b_labels(self):
        values = numeric_highlights("B2B 시장 1위는 26일 $700 billion 투자와 731조원 수주를 발표했다.")

        self.assertEqual(["$700 billion", "731조"], values)

    def test_yahoo_quote_widget_is_excluded_before_news_analysis(self):
        target = NewsCollectionTarget("035420", "NAVER", "KOSPI", "KRW", "플랫폼")
        title = "Nvidia to acquire $1 billion of new shares of South Korea's Naver"
        raw_body = (
            "At Yahoo Finance, you get free stock quotes, up-to-date news, portfolio management resources, "
            "international market data, social interaction and mortgage rates that help you manage your financial life. "
            + title
            + " SEOUL, July 27 (Reuters) - South Korea's Naver said in a regulatory filing that Nvidia will acquire "
            "$1 billion of its shares to be newly issued as part of an investment partnership to build a new data center. "
            "(Reporting by\u200b Jack Kim; Editing by Chris Reese) "
            "NQ=F Nasdaq 100 Sep 26 28,622.75 +340.50 (+1.20%) "
            "BTC-USD Bitcoin USD 65,334.60 +1,038.99 (+1.62%) "
            "ETH-USD Ethereum USD 1,955.38 +82.37 (+4.40%)"
        )
        evidence = ResearchEvidence(
            "research:035420:news:yahoo-widget",
            "035420",
            "news",
            "Reuters",
            title,
            title,
            "https://finance.yahoo.com/technology/articles/nvidia-acquire-1-billion-shares-230439371.html",
            "2026-07-26T23:04:39Z",
            "context",
            published_at="2026-07-26T23:04:39Z",
            raw_payload={
                "name": "NAVER",
                "relationScope": "direct",
                "articleReadStatus": "body",
                "articleText": raw_body,
                "articleFacts": {
                    "bodyAvailable": True,
                    "bodyQualityPassed": True,
                    "bodyPreview": raw_body,
                    "topics": ["비트코인"],
                    "numbers": ["65,334.60"],
                    "keySentences": [raw_body],
                },
            },
        )

        cleaned = clean_article_body_text(raw_body)
        analysis = local_news_ai_analysis(target, evidence).to_dict()
        updated = apply_news_ai_analysis(evidence, analysis)
        prompt = json.loads(build_news_ai_analysis_prompt(target, evidence))

        self.assertIn("Nvidia will acquire $1 billion", cleaned)
        self.assertNotIn("BTC-USD", cleaned)
        self.assertNotIn("Bitcoin USD", cleaned)
        self.assertNotIn("비트코인", json.dumps(analysis, ensure_ascii=False))
        self.assertEqual("capital_policy", analysis["eventType"])
        self.assertNotIn("BTC-USD", json.dumps(prompt, ensure_ascii=False))
        self.assertNotIn("Bitcoin USD", json.dumps(prompt, ensure_ascii=False))
        self.assertNotIn("BTC-USD", updated.raw_payload["articleText"])
        self.assertNotIn("Bitcoin USD", updated.raw_payload["articleFacts"]["bodyPreview"])
        self.assertNotIn("비트코인", updated.raw_payload["articleFacts"]["topics"])

    def test_navigation_contamination_is_excluded_from_impact_signals_and_ai_prompt(self):
        target = NewsCollectionTarget("066570", "LG전자", "KOSPI", "KRW", "가전/전자")
        evidence = ResearchEvidence(
            "research:066570:news:target-scoped-signals",
            "066570",
            "news",
            "Example News",
            "LG전자, 미국 주방가전 신뢰도 1위",
            "LG전자가 미국 주방가전 시장 공략을 강화합니다.",
            "https://example.test/lg-kitchen",
            "2026-07-26T01:00:00Z",
            "context",
            published_at="2026-07-26T01:00:00Z",
            raw_payload={
                "relationScope": "direct",
                "articleReadStatus": "body",
                "articleText": (
                    "LG전자가 미국 소비자 평가에서 가장 신뢰할 수 있는 주방가전 브랜드로 선정돼 "
                    "현지 빌트인 시장 공략을 강화한다. "
                    "LG전자, 美 신뢰 주방가전 1위…빌더시장 3위권 정조준 "
                    "한화오션, 64조 수주…남미 방산 영토확장 "
                    "엔비디아, 75% 급락…AI 메모리 공급 확대 "
                    "다른 기업, 7500억 투자…최대 수주"
                ),
                "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
            },
        )

        analysis = local_news_ai_analysis(target, evidence).to_dict()
        updated = apply_news_ai_analysis(evidence, analysis)
        prompt = json.loads(build_news_ai_analysis_prompt(target, evidence))

        self.assertEqual("neutral", analysis["impactPolarity"])
        self.assertEqual([], analysis["keyNumbers"])
        self.assertNotIn("급락", analysis["impactReasonKo"])
        self.assertNotIn("64조", analysis["impactReasonKo"])
        self.assertNotIn("75%", analysis["impactReasonKo"])
        self.assertNotIn("일반 이슈 이슈", analysis["impactReasonKo"])
        self.assertNotIn("한화오션", updated.raw_payload["stockImpactReasonKo"])
        self.assertNotIn("엔비디아", json.dumps(prompt, ensure_ascii=False))
        self.assertNotIn("64조", json.dumps(prompt, ensure_ascii=False))

    def test_news_analysis_setting_accepts_numeric_text(self):
        self.assertEqual(12, int_setting({"newsAiAnalysisLimit": "12"}, "newsAiAnalysisLimit", 5))

    def test_news_ai_analyzer_uses_the_enforced_codex_model_policy(self):
        with patch("digital_twin.infrastructure.news_ai_analyzer.codex_command", return_value="codex --model gpt-test exec -") as command:
            analyzer = news_ai_analyzer_from_settings({
                "newsAiAnalysisUseCodex": "1",
                "newsAiAnalysisModel": "gpt-test",
                "newsAiAnalysisTimeoutSeconds": "30",
            })

        self.assertIsInstance(analyzer, FallbackNewsAiAnalyzer)
        command.assert_called_once_with()

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

    def test_english_modal_may_is_not_translated_as_the_month_of_may(self):
        modal = english_fragment_to_korean("Apple may increase iPhone prices")
        dated = english_fragment_to_korean("Apple event starts May 12")

        self.assertNotIn("5월", modal)
        self.assertIn("5월 12", dated)

    def test_local_english_fallback_uses_event_fact_instead_of_word_by_word_translation(self):
        target = NewsCollectionTarget("MSTR", "스트래티지", "NASDAQ", "USD", "디지털자산")
        evidence = ResearchEvidence(
            "research:MSTR:news:stock-sale",
            "MSTR",
            "news",
            "Reuters",
            "Strategy may sell $466 million of MSTR stock while maintaining bitcoin reserves",
            "Strategy may sell $466 million of its stock and said its bitcoin reserve policy remains unchanged.",
            "https://example.test/mstr-sale",
            "2026-07-20T01:00:00Z",
            "context",
            70,
            0.8,
            "2026-07-20T00:30:00Z",
            raw_payload={
                "articleReadStatus": "body",
                "relationScope": "direct",
                "articleFacts": {
                    "bodyAvailable": True,
                    "bodyPreview": "Strategy may sell $466 million of its stock. The company said its bitcoin reserve policy remains unchanged.",
                },
            },
        )

        summary = local_news_ai_analysis(target, evidence).to_dict()["summary"]

        self.assertIn("주식 매각 가능성", summary["briefKo"])
        self.assertIn("비트코인 준비금 정책은 유지", summary["briefKo"])
        self.assertNotIn(" may ", " " + summary["briefKo"] + " ")
        self.assertNotIn(" its ", " " + summary["briefKo"] + " ")
        self.assertEqual([], summary["keyTakeaways"])

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
        self.assertEqual("unrelated", analysis["relevanceState"])
        self.assertIn("플랫폼/블로그", analysis["excludedReason"])
        self.assertFalse(relation_scope_is_investable(analysis["relationScope"]))
        self.assertEqual([], analysis["ontologyRelations"])

    def test_news_analysis_excludes_broadcast_preview_from_direct_company_evidence(self):
        target = NewsCollectionTarget("066570", "LG전자", "KOSPI", "KRW", "가전/전자")

        analysis = classify_news_relevance(
            target,
            "연금부터 HUG 사회주택·LG전자까지…머니카운터 1회 방송 예고",
            "방송에서 LG전자 AI 재평가 가능성을 분석할 예정입니다.",
            "경인방송 뉴스",
            "Google News KR",
        )

        self.assertEqual("editorial_context", analysis["relationScope"])
        self.assertEqual("unrelated", analysis["relevanceState"])
        self.assertFalse(analysis["directMention"])
        self.assertFalse(relation_scope_is_investable(analysis["relationScope"]))
        self.assertIn("실제 기업 사건", analysis["excludedReason"])
        self.assertEqual([], analysis["ontologyRelations"])

    def test_article_analysis_cannot_promote_broadcast_preview_to_support_signal(self):
        evidence = ResearchEvidence(
            "research:066570:news:broadcast-preview",
            "066570",
            "news",
            "경인방송 뉴스",
            "연금부터 HUG 사회주택·LG전자까지…머니카운터 1회 방송 예고",
            "방송에서 LG전자 AI 재평가 가능성을 분석할 예정입니다.",
            "https://example.test/broadcast-preview",
            "2026-07-24T00:00:00Z",
            "context",
            0,
            0,
            "2026-07-24T00:00:00Z",
            raw_payload={
                "relationScope": "direct",
                "articleFacts": {"bodyAvailable": True, "bodyPreview": "LG전자 AI 재평가 분석을 방송에서 다룹니다."},
            },
        )

        updated = apply_news_ai_analysis(evidence, {
            "relationScope": "direct",
            "impactPolarity": "support",
            "impactLabelKo": "호재",
        })

        self.assertEqual("context", updated.polarity)
        self.assertEqual("editorial_context", updated.raw_payload["relationScope"])
        self.assertEqual("blocked", updated.raw_payload["validationState"])
        self.assertEqual("exclude", updated.raw_payload["qualityGate"]["decision"])

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
        self.assertEqual("unrelated", analysis["relevanceState"])
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
        self.assertEqual("unrelated", analysis["relevanceState"])
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
            "https://www.yna.co.kr/view/AKR20260709000100003",
            "2026-07-09T01:00:00Z",
            "support",
            11.0,
            0.78,
            "2026-07-09T01:00:00Z",
            {
                **classify_news_relevance(
                NewsCollectionTarget("005930", "삼성전자", "KOSPI", "KRW", "반도체"),
                "삼성전자 반도체 실적 개선 전망",
                "메모리 수요 회복과 실적 상향 기대",
                "연합뉴스",
                "Google News KR",
                ),
                "articleReadStatus": "body",
                "bodyQualityPassed": True,
                "summaryQualityState": "ready",
                "articleSummaryQuality": {"state": "ready", "issues": []},
                "aiAnalysis": {"status": "ok", "needsReview": False},
                "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True, "readStatus": "body"},
                "dataState": "sufficient",
                "validationState": "ready",
                "evidenceGovernance": {"investmentJudgmentEligible": True},
            },
        )

        facts = research_evidence_facts([evidence.to_dict()])

        self.assertEqual(1, facts["directSupportNewsCount"])
        self.assertIn("earnings", facts["topNewsEventTypes"])
        self.assertIn(facts["newsMaterialityState"], {"material", "notable"})
        self.assertEqual("support", facts["newsEvidenceState"])
        self.assertEqual("support-only", facts["newsConflictState"])
        self.assertIn(facts["newsDataState"], {"sufficient", "partial"})

    def test_ai_article_analysis_treats_earnings_concern_collapse_as_risk(self):
        target = NewsCollectionTarget("000660", "SK하이닉스", "KOSPI", "KRW", "반도체")
        evidence = ResearchEvidence(
            "research:000660:news:hynix-risk",
            "000660",
            "news",
            "연합인포맥스",
            "'ADR 호재' 덮은 실적 우려…SK하이닉스 200만 원 붕괴",
            "RSS/제공 요약: ADR 호재를 덮은 실적 우려와 가격 하락 기사입니다.",
            "https://example.test/hynix",
            "2026-07-13T02:01:00Z",
            "support",
            14.1,
            0.78,
            "2026-07-13T02:01:00Z",
            raw_payload={
                "relationScope": "direct",
                "eventType": "earnings",
                "relevanceScore": 100,
                "materialityScore": 87.8,
                "sourceReliability": 90,
                "articleReadStatus": "body",
                "articleFacts": {
                    "bodyAvailable": False,
                    "feedSummaryPreview": "ADR 호재를 덮은 실적 우려와 200만 원 붕괴",
                },
            },
        )

        analysis = local_news_ai_analysis(target, evidence).to_dict()
        updated = apply_news_ai_analysis(evidence, analysis)

        self.assertEqual("risk", analysis["impactPolarity"])
        self.assertEqual("악재", analysis["impactLabelKo"])
        self.assertIn("붕괴", analysis["riskSignals"])
        self.assertIn("우려", analysis["riskSignals"])
        self.assertEqual("risk", updated.polarity)
        self.assertEqual("악재", updated.raw_payload["stockImpactLabel"])
        self.assertEqual("feed-summary", updated.raw_payload["articleReadStatus"])
        self.assertTrue(updated.raw_payload["aiAnalysis"]["summary"]["briefKo"])

    def test_ai_article_analysis_conflict_becomes_ontology_data_quality_risk(self):
        evidence = ResearchEvidence(
            "research:AAPL:news:conflict",
            "AAPL",
            "news",
            "Reuters",
            "Apple faces lawsuit pressure despite AI partnership optimism",
            "소송 부담과 AI 기대가 섞인 기사입니다.",
            "https://example.test/apple-conflict",
            "2026-07-13T07:31:00Z",
            "support",
            78,
            0.82,
            "2026-07-13T07:31:00Z",
            raw_payload={
                "relationScope": "direct",
                "relevanceScore": 96,
                "materialityScore": 78,
                "materialityPassed": True,
                "stockImpactPolarity": "support",
                "stockImpactLabel": "호재",
                "articleFacts": {
                    "stockImpactPolarity": "support",
                    "bodyAvailable": True,
                },
            },
        )

        updated = apply_news_ai_analysis(evidence, {
            "status": "ok",
            "impactPolarity": "risk",
            "impactLabelKo": "악재",
            "confidence": 0.8,
            "materialityScore": 82,
            "relationScope": "direct",
            "eventType": "regulation",
            "summary": {"briefKo": "소송 부담이 투자심리에 부담입니다."},
            "riskSignals": ["소송"],
        })
        graph = PortfolioOntology("news-conflict")
        stock_id = add_entity(graph, "stock", "AAPL", "Apple", {
            "tboxClass": "Stock",
            "symbol": "AAPL",
            "source": "holding",
        })
        add_research_evidence_concepts(
            graph,
            stock_id,
            "",
            "",
            "AAPL",
            {},
            {"researchEvidence": {"AAPL": [updated.to_dict()]}},
        )

        self.assertTrue(updated.raw_payload["analysisConflict"])
        self.assertEqual("support", updated.raw_payload["analysisConflictExistingPolarity"])
        self.assertEqual("risk", updated.raw_payload["analysisConflictAiPolarity"])
        self.assertEqual("소송 부담이 투자심리에 부담입니다", updated.raw_payload["articleFacts"]["summaryKo"])
        self.assertIn("소송·규제 이슈", updated.raw_payload["articleFacts"]["eventTakeaway"])
        self.assertTrue(any(item.kind == "article-analysis-conflict" for item in graph.entities))
        self.assertTrue(any(item.source == stock_id and item.relation_type == "HAS_DATA_QUALITY" for item in graph.relations))

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

    def test_ai_article_analysis_summarizes_title_rss_facts_for_legal_article(self):
        target = NewsCollectionTarget("AAPL", "Apple", "NASDAQ", "USD", "AI/플랫폼")
        evidence = ResearchEvidence(
            "research:AAPL:news:apple-openai-theft",
            "AAPL",
            "news",
            "The Register",
            "Apple accuses OpenAI of stealing its core tech secrets - The Register",
            "RSS/제공 요약: Apple accuses OpenAI of stealing its core tech secrets.",
            "https://example.test/apple-openai",
            "2026-07-13T07:31:00Z",
            "context",
            62.3,
            0.58,
            "2026-07-13T07:31:00Z",
            raw_payload={
                "relationScope": "direct",
                "relevanceScore": 94.5,
                "materialityScore": 62.3,
                "articleReadStatus": "feed-summary",
                "articleFacts": {
                    "bodyAvailable": False,
                    "feedSummaryPreview": "RSS/제공 요약: Apple accuses OpenAI of stealing its core tech secrets.",
                },
            },
        )

        analysis = local_news_ai_analysis(target, evidence).to_dict()

        self.assertEqual("risk", analysis["impactPolarity"])
        self.assertEqual("regulation", analysis["eventType"])
        self.assertIn("accuses", analysis["riskSignals"])
        self.assertIn("핵심 기술 비밀 탈취 의혹", analysis["summary"]["briefKo"])
        self.assertIn("원문 본문 확보", analysis["summary"]["watchPoints"])
        self.assertIn("본문 원문 미수집", " ".join(analysis["reasoningLimitations"]))

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

    def test_ai_article_analysis_explains_coupang_valuation_slide_impact(self):
        target = NewsCollectionTarget("CPNG", "쿠팡", "NYSE", "USD", "커머스")
        evidence = ResearchEvidence(
            "research:CPNG:news:valuation-slide",
            "CPNG",
            "news",
            "Yahoo Finance",
            "Coupang (CPNG) Slides Ahead Of Earnings As The Valuation Debate Heats Up",
            "Recent move puts Coupang back in focus. Coupang recently closed trading day down 3.21%, extending month long slide of 7.49% as valuation debate heats up.",
            "https://example.test/cpng-slide",
            "2026-07-17T07:12:00Z",
            "risk",
            82.2,
            0.58,
            "2026-07-17T07:12:00Z",
            raw_payload={
                "relationScope": "direct",
                "eventType": "earnings",
                "relevanceScore": 97,
                "materialityScore": 82.2,
                "sourceReliability": 58,
                "articleReadStatus": "body",
                "articleFacts": {
                    "bodyAvailable": True,
                    "bodyPreview": "Recent move puts Coupang back in focus. Coupang recently closed trading day down 3.21%, extending month long slide of 7.49% as valuation debate heats up.",
                },
            },
        )

        analysis = local_news_ai_analysis(target, evidence).to_dict()

        self.assertEqual("risk", analysis["impactPolarity"])
        self.assertIn("slides", analysis["riskSignals"])
        self.assertIn("valuation debate", analysis["riskSignals"])
        self.assertIn("주가 하락", analysis["impactReasonKo"])
        self.assertIn("보유·관심 기준", analysis["portfolioImplicationKo"])
        self.assertIn("자동 매매 판단이 아니라", analysis["actionBoundaryKo"])
        self.assertIn("3.21%", analysis["keyNumbers"])

    def test_ai_article_analysis_keeps_fallback_summary_when_external_ai_omits_summary(self):
        target = NewsCollectionTarget("AAPL", "Apple", "NASDAQ", "USD", "AI/플랫폼")
        evidence = ResearchEvidence(
            "research:AAPL:news:external-empty-summary",
            "AAPL",
            "news",
            "The Register",
            "Apple accuses OpenAI of stealing its core tech secrets",
            "RSS/제공 요약: Apple accuses OpenAI of stealing its core tech secrets.",
            "https://example.test/apple-openai",
            "2026-07-13T07:31:00Z",
            "context",
            62.3,
            0.58,
            "2026-07-13T07:31:00Z",
            raw_payload={
                "relationScope": "direct",
                "relevanceScore": 94.5,
                "materialityScore": 62.3,
            },
        )
        fallback = local_news_ai_analysis(target, evidence)

        analysis = normalize_ai_analysis(
            {"status": "ok", "model": "External article AI", "impactPolarity": "risk", "summary": {}},
            fallback,
        ).to_dict()

        self.assertEqual("External article AI", analysis["model"])
        self.assertIn("핵심 기술 비밀 탈취 의혹", analysis["summary"]["briefKo"])
        self.assertTrue(analysis["summary"]["watchPoints"])

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
        self.assertEqual("regulation", classify_news_event_type("금감원 조사 착수", "회사를 조사 대상으로 지정"))
        self.assertEqual("risk", keyword_polarity("금감원 조사 착수"))

    def test_employment_preference_survey_is_not_regulatory_risk(self):
        target = NewsCollectionTarget("000660", "SK하이닉스", "KOSPI", "KRW", "반도체")
        title = "SK하이닉스, 대학생이 일하고 싶은 기업 2년 연속 1위"
        body = (
            "SK하이닉스가 대학생이 일하고 싶은 기업 조사에서 2년 연속 1위에 올랐다. "
            "채용 플랫폼이 대학생을 대상으로 실시한 설문조사 결과이며 급여와 보상 체계가 주요 선호 이유로 꼽혔다."
        )
        evidence = ResearchEvidence(
            "research:000660:news:employment-preference-survey",
            "000660",
            "news",
            "연합뉴스",
            title,
            body,
            "https://example.test/skhynix-employment-survey",
            "2026-07-27T01:00:00Z",
            "risk",
            published_at="2026-07-27T01:00:00Z",
            raw_payload={
                "name": "SK하이닉스",
                "relationScope": "direct",
                "eventType": "regulation",
                "stockImpactPolarity": "risk",
                "stockImpactLabel": "악재",
                "materialityState": "material",
                "articleReadStatus": "body",
                "articleText": body,
                "articleFacts": {
                    "bodyAvailable": True,
                    "bodyQualityPassed": True,
                    "bodyPreview": body,
                    "eventType": "regulation",
                    "stockImpactPolarity": "risk",
                },
            },
        )

        self.assertEqual("general", classify_news_event_type(title, body))
        self.assertEqual("context", keyword_polarity(title + " " + body))

        local = local_news_ai_analysis(target, evidence).to_dict()
        updated = apply_news_ai_analysis(evidence, {
            "status": "ok",
            "eventType": "regulation",
            "impactPolarity": "risk",
            "impactLabelKo": "악재",
            "materialityState": "material",
            "summary": {"briefKo": "규제 조사 부담이 투자심리에 악영향을 줍니다."},
            "riskSignals": ["조사"],
        })

        self.assertEqual("general", local["eventType"])
        self.assertEqual("neutral", local["impactPolarity"])
        self.assertEqual("context", local["materialityState"])
        self.assertEqual([], local["riskSignals"])
        self.assertEqual("general", updated.raw_payload["eventType"])
        self.assertEqual("general", updated.raw_payload["aiAnalysis"]["eventType"])
        self.assertEqual("neutral", updated.raw_payload["aiAnalysis"]["impactPolarity"])
        self.assertEqual("context", updated.raw_payload["stockImpactPolarity"])
        self.assertEqual("context", updated.raw_payload["materialityState"])
        self.assertFalse(updated.raw_payload.get("analysisConflict"))

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
        self.assertEqual("limited", analysis["sourceTrustState"])
        self.assertEqual("conditional", analysis["validationState"])

    def test_news_analysis_classifies_known_publishers_without_scores(self):
        self.assertEqual("trusted", source_trust_state_for_source("The Economist", "Google News US"))
        self.assertEqual("standard", source_trust_state_for_source("YTN", "Google News KR"))
        self.assertEqual("standard", source_trust_state_for_source("뉴스핌", "Google News KR"))
        self.assertEqual("limited", source_trust_state_for_source("Naver Blog", "Google News KR"))

    def test_legacy_news_payload_uses_publisher_state_without_reliability_number(self):
        states = news_state_payload({
            "provider": "Reuters",
            "relevanceScore": 91,
            "stockImpactLabel": "중립",
        })

        self.assertEqual("direct", states["relevanceState"])
        self.assertEqual("trusted", states["sourceTrustState"])
        self.assertEqual("conditional", states["validationState"])

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
        self.assertEqual("unrelated", analysis["relevanceState"])
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
        impact = stock_impact_analysis(target, title, body, "", analysis, "support")

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

    def test_mstr_related_product_news_is_not_promoted_to_direct_news(self):
        target = NewsCollectionTarget("MSTR", "Strategy", "NASDAQ", "USD", "디지털자산")

        analysis = classify_news_relevance(
            target,
            "MSTY Covered-Call ETF Hits Record Monthly Distribution",
            "The ETF owns MSTR-linked exposure and reacts to Strategy share volatility.",
            "Yahoo Finance",
            "google_rss_us",
        )

        self.assertEqual("related_product", analysis["relationScope"])
        self.assertTrue(relation_scope_is_investable(analysis["relationScope"]))
        self.assertFalse(analysis["directMention"])
        self.assertFalse(analysis["qualityGate"]["targetSubjectConfirmed"])
        self.assertTrue(analysis["qualityGate"]["relatedProductContext"])
        self.assertTrue(any(row["role"] == "related_product_context" for row in analysis["entityLinks"]))

    def test_naver_pay_third_party_giveaway_is_not_naver_company_news(self):
        target = NewsCollectionTarget("035420", "NAVER", "KOSPI", "KRW", "플랫폼")

        analysis = classify_news_relevance(
            target,
            "벤큐, 모니터 구매 후 포토후기 작성하면 네이버페이 5만원 증정",
            "제3자 제품 구매 고객에게 네이버페이 포인트를 지급하는 행사입니다.",
            "Example News",
            "google_rss_kr",
        )

        self.assertEqual("entity_mismatch", analysis["relationScope"])
        self.assertFalse(relation_scope_is_investable(analysis["relationScope"]))
        self.assertFalse(analysis["qualityGate"]["targetSubjectConfirmed"])
        self.assertIn("제3자 판촉", analysis["excludedReason"])

    def test_naver_product_name_does_not_hide_explicit_parent_company_subject(self):
        target = NewsCollectionTarget("035420", "NAVER", "KOSPI", "KRW", "플랫폼")

        analysis = classify_news_relevance(
            target,
            "NAVER, 네이버페이 해외 결제 서비스 출시",
            "NAVER가 신규 결제 서비스를 발표했습니다.",
            "Reuters",
            "google_rss_kr",
        )

        self.assertEqual("direct", analysis["relationScope"])
        self.assertTrue(analysis["qualityGate"]["targetSubjectConfirmed"])

    def test_mstr_title_subject_stays_direct_when_symbol_is_explicit(self):
        target = NewsCollectionTarget("MSTR", "Strategy", "NASDAQ", "USD", "디지털자산")

        analysis = classify_news_relevance(
            target,
            "Strategy (MSTR) buys more Bitcoin after financing update",
            "The company disclosed a new treasury purchase.",
            "Reuters",
            "google_rss_us",
        )

        self.assertEqual("direct", analysis["relationScope"])
        self.assertTrue(analysis["directMention"])
        self.assertTrue(analysis["qualityGate"]["targetSubjectConfirmed"])

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
                "relevanceState": "direct",
                "materialityState": "material",
                "sourceTrustState": "trusted",
            },
            polarity="risk",
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
