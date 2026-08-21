import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.news_intelligence.application.analyze_article import annotate_evidence_eligibility
from digital_twin.news_intelligence.application.normalize_sources import normalize_evidence_sources
from digital_twin.news_intelligence.application.revalidate_articles import RevalidateNewsIntelligenceService
from digital_twin.news_intelligence.domain.article_quality import inspect_article_body
from digital_twin.news_intelligence.domain.eligibility import assess_news_eligibility
from digital_twin.news_intelligence.domain.entity_resolution import matched_aliases, resolve_target_entity
from digital_twin.news_intelligence.domain.provenance import resolve_source_provenance
from digital_twin.news_intelligence.domain.source import SourceRegistry
from digital_twin.news_intelligence.domain.story import story_identity
from digital_twin.domain.events import NEWS_ARTICLE_ANALYZED, news_article_analyzed_event


class Evidence:
    def __init__(self, title, payload, symbol="NVDA"):
        self.evidence_id = "research:" + symbol + ":news:test"
        self.symbol = symbol
        self.kind = "news"
        self.source = "Reuters"
        self.title = title
        self.summary = "검증된 기사 요약"
        self.url = "https://example.test/article"
        self.lifecycle_state = "active"
        self.raw_payload = payload


class MemoryRepository:
    def __init__(self, items):
        self.items = list(items)
        self.saved = []

    def latest(self, symbol="", kind="news", limit=500):
        return self.items[:limit]

    def upsert_many(self, items):
        self.saved = list(items)
        return len(self.saved)


def ready_payload():
    return {
        "relationScope": "direct",
        "relevanceState": "direct",
        "sourceTrustState": "trusted",
        "materialityState": "material",
        "dataState": "sufficient",
        "validationState": "ready",
        "articleReadStatus": "body",
        "bodyQualityPassed": True,
        "articleText": "NVIDIA announced a verified multi-year supply agreement. " * 8,
        "articleSummaryKo": "엔비디아가 다년 공급 계약을 발표했습니다.",
        "summaryQualityState": "ready",
        "articleSummaryQuality": {"state": "ready", "issues": []},
        "aiAnalysis": {"status": "ok", "needsReview": False},
        "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True, "readStatus": "body"},
        "evidenceGovernance": {"investmentJudgmentEligible": True},
    }


class NewsIntelligenceTests(unittest.TestCase):
    def test_korean_alias_requires_a_real_word_boundary(self):
        self.assertEqual([], matched_aliases("오리온 실적 전망 - 현대차증권", ["현대차"]))
        self.assertEqual(["현대차"], matched_aliases("현대차, 신차 출시", ["현대차"]))

    def test_leading_other_company_is_not_target_subject(self):
        resolution = resolve_target_entity(
            "Onto Innovation Stock Could Catch Nvidia as AI Demand Rises",
            "Onto Innovation compares its opportunity with Nvidia.",
            "NVDA",
            "NVIDIA",
        )

        self.assertEqual("mentioned", resolution.role)
        self.assertFalse(resolution.target_subject_confirmed)
        self.assertEqual("Onto Innovation", resolution.other_subject)

    def test_question_headline_led_by_partners_is_not_a_direct_nvidia_event(self):
        resolution = resolve_target_entity(
            "NVIDIA (NVDA): What Foxconn and Super Micro Are Telling Us about the AI Boom",
            "Foxconn and Super Micro discussed AI demand while Nvidia was mentioned as an ecosystem company.",
            "NVDA",
            "NVIDIA",
        )

        self.assertFalse(resolution.target_subject_confirmed)
        self.assertIn("other-company-is-leading-subject", resolution.reason_codes)

    def test_korean_multi_company_watch_roundup_is_not_a_direct_company_event(self):
        resolution = resolve_target_entity(
            "[오늘 이 종목] SK하이닉스·삼양식품·NAVER·두산에너빌리티 핵심주 주목",
            "여러 종목의 실적과 수급을 함께 정리합니다.",
            "035420",
            "NAVER",
        )

        self.assertFalse(resolution.target_subject_confirmed)
        self.assertIn("multi-company-roundup", resolution.reason_codes)

    def test_question_clause_pronoun_is_not_misread_as_another_company(self):
        resolution = resolve_target_entity(
            "Nvidia Reveals a $21 Billion Position in SpaceX. Here's How That Could Impact Its Earnings",
            "Nvidia disclosed the investment position.",
            "NVDA",
            "NVIDIA",
        )

        self.assertTrue(resolution.target_subject_confirmed)
        self.assertEqual("subject", resolution.role)

    def test_multi_company_roundup_is_not_a_direct_tesla_event(self):
        resolution = resolve_target_entity(
            "Tesla, Palantir and Nvidia Rally as Stocks to Watch",
            "Several technology stocks moved higher.",
            "TSLA",
            "Tesla",
        )

        self.assertEqual("mentioned", resolution.role)
        self.assertIn("multi-company-roundup", resolution.reason_codes)

    def test_other_company_price_move_with_target_context_is_not_direct(self):
        resolution = resolve_target_entity(
            "Onto Innovation Jumps 6% Following Camtek Earnings and NVIDIA Partnership",
            "The article explains Onto Innovation's share move.",
            "NVDA",
            "NVIDIA",
        )

        self.assertEqual("mentioned", resolution.role)
        self.assertEqual("Onto Innovation", resolution.other_subject)

    def test_comparison_led_by_another_company_is_not_direct(self):
        resolution = resolve_target_entity(
            "Amazon.com, Inc. (AMZN) vs. Apple Inc. (AAPL): Hedge Funds Favor One",
            "The comparison includes Apple.",
            "AAPL",
            "Apple",
        )

        self.assertEqual("mentioned", resolution.role)
        self.assertIn("Amazon.com", resolution.other_subject)

    def test_legal_entity_with_apple_prefix_is_not_aapl(self):
        resolution = resolve_target_entity(
            "Apple Hospitality REIT Announces Monthly Distribution",
            "Apple Hospitality REIT declared its monthly dividend.",
            "AAPL",
            "Apple",
        )

        self.assertFalse(resolution.target_subject_confirmed)
        self.assertEqual("Apple Hospitality REIT", resolution.other_subject)

    def test_korean_supplier_is_not_the_customer_company_subject(self):
        resolution = resolve_target_entity(
            "한성크린텍, SK하이닉스서 307억 규모 수주",
            "한성크린텍이 SK하이닉스에서 공사를 수주했다.",
            "000660",
            "SK하이닉스",
        )

        self.assertFalse(resolution.target_subject_confirmed)
        self.assertEqual("customer", resolution.role)
        self.assertEqual("한성크린텍", resolution.other_subject)

    def test_body_quality_rejects_navigation_and_investment_promotion(self):
        result = inspect_article_body(
            ("NVIDIA announced quarterly results with audited revenue details. " * 8)
            + " Continue reading. Missed Nvidia? Is now the time to buy?"
        )

        self.assertFalse(result.passed)
        self.assertIn("publisher-navigation", result.issues)
        self.assertIn("investment-promotion", result.issues)

    def test_body_quality_rejects_unrelated_headline_lists(self):
        body = "\n".join([
            "SK하이닉스는 신규 생산 계획을 발표했다.",
            "철도 노조 협상 다시 결렬",
            "서울 아파트 가격 상승세",
            "프로야구 주말 경기 결과",
            "정부 세제 개편안 발표",
            "국제 유가 장중 하락",
            "주요 뉴스 더보기",
        ])

        result = inspect_article_body(body, 80, ["SK하이닉스"])

        self.assertFalse(result.passed)
        self.assertIn("headline-list-contamination", result.issues)
        self.assertIn("target-context-diluted", result.issues)

    def test_four_eligibility_layers_require_external_analysis_and_claim_governance(self):
        payload = ready_payload()
        result = assess_news_eligibility(
            payload,
            title="NVIDIA announces multi-year supply agreement",
            summary="NVIDIA announced a verified agreement.",
            symbol="NVDA",
            name="NVIDIA",
            source="Reuters",
            url="https://www.reuters.com/technology/nvidia-supply-agreement",
        )

        self.assertTrue(result.archive.eligible)
        self.assertTrue(result.display.eligible)
        self.assertTrue(result.alert.eligible)
        self.assertTrue(result.reasoning.eligible)

        payload["aiAnalysis"] = {"status": "local", "needsReview": False}
        local = assess_news_eligibility(
            payload,
            title="NVIDIA announces multi-year supply agreement",
            symbol="NVDA",
            name="NVIDIA",
            source="Reuters",
            url="https://www.reuters.com/technology/nvidia-supply-agreement",
        )
        self.assertTrue(local.archive.eligible)
        self.assertFalse(local.display.eligible)
        self.assertIn("external-analysis-not-ready", local.display.reason_codes)

    def test_reasoning_requires_governed_claim_even_when_alert_is_allowed(self):
        payload = ready_payload()
        payload["evidenceGovernance"] = {"investmentJudgmentEligible": False}
        result = assess_news_eligibility(
            payload,
            title="NVIDIA announces multi-year supply agreement",
            symbol="NVDA",
            name="NVIDIA",
            source="Reuters",
            url="https://www.reuters.com/technology/nvidia-supply-agreement",
        )

        self.assertTrue(result.alert.eligible)
        self.assertFalse(result.reasoning.eligible)
        self.assertIn("claim-governance-not-eligible", result.reasoning.reason_codes)

    def test_parent_reasoning_gate_downgrades_persisted_claim_ledger(self):
        payload = ready_payload()
        payload["claimLedger"] = {
            "claims": [{"claimId": "claim:test", "investmentJudgmentEligible": True, "reasons": []}],
            "summary": {"claimCount": 1, "eligibleClaimCount": 1},
        }
        evidence = Evidence("NVIDIA announces multi-year supply agreement", payload)

        annotate_evidence_eligibility(evidence)

        self.assertFalse(evidence.raw_payload["newsEligibility"]["reasoningEligible"])
        self.assertFalse(evidence.raw_payload["evidenceGovernance"]["investmentJudgmentEligible"])
        self.assertFalse(evidence.raw_payload["claimLedger"]["claims"][0]["investmentJudgmentEligible"])
        self.assertEqual(0, evidence.raw_payload["claimLedger"]["summary"]["eligibleClaimCount"])

    def test_governed_external_analysis_can_be_conditional_reasoning_evidence(self):
        payload = ready_payload()
        payload["validationState"] = "conditional"
        payload["aiAnalysis"] = {"status": "ok", "needsReview": True}
        result = assess_news_eligibility(
            payload,
            title="NVIDIA announces multi-year supply agreement",
            symbol="NVDA",
            name="NVIDIA",
            source="Reuters",
            url="https://www.reuters.com/technology/nvidia-supply-agreement",
        )

        self.assertTrue(result.alert.eligible)
        self.assertTrue(result.reasoning.eligible)

    def test_ai_detected_body_mismatch_blocks_alert_and_reasoning(self):
        payload = ready_payload()
        payload["aiAnalysis"] = {
            "status": "ok",
            "needsReview": True,
            "reasoningLimitations": ["기사 본문 대신 다른 기사 제목 목록이 수집되었습니다."],
        }
        result = assess_news_eligibility(
            payload,
            title="NVIDIA announces multi-year supply agreement",
            symbol="NVDA",
            name="NVIDIA",
            source="Reuters",
            url="https://www.reuters.com/technology/nvidia-supply-agreement",
        )

        self.assertEqual("content-invalid", result.review_state)
        self.assertFalse(result.alert.eligible)
        self.assertFalse(result.reasoning.eligible)

    def test_price_target_commentary_is_not_alertable_without_a_new_company_event(self):
        payload = ready_payload()
        payload["eventType"] = "price_commentary"
        payload["aiAnalysis"].update({"decisionInlineEligible": False})

        result = assess_news_eligibility(
            payload,
            title="Analyst raises Nvidia price target after stock rally",
            summary="The analyst sees 30% upside in Nvidia stock.",
            symbol="NVDA",
            name="NVIDIA",
            source="Reuters",
            url="https://www.reuters.com/technology/nvidia-price-target",
        )

        self.assertFalse(result.alert.eligible)
        self.assertIn("price-commentary-not-alertable", result.alert.reason_codes)

    def test_story_identity_does_not_merge_different_articles_by_generic_takeaway(self):
        first = {"symbol": "NVDA", "eventType": "earnings", "publishedAt": "2026-08-12T01:00:00Z", "title": "BofA raises Nvidia earnings estimate"}
        second = {"symbol": "NVDA", "eventType": "earnings", "publishedAt": "2026-08-12T02:00:00Z", "title": "Nebius expands AI infrastructure funding"}
        self.assertNotEqual(story_identity(first), story_identity(second))

    def test_story_identity_keeps_an_explicit_republication_root(self):
        original = {"claimId": "claim:nvda-contract", "title": "NVIDIA signs contract"}
        copy = {"duplicateOfClaimId": "claim:nvda-contract", "title": "엔비디아 계약 체결"}
        self.assertEqual(story_identity(original), story_identity(copy))

    def test_canonical_domain_registry_overrides_mislabeled_yahoo_publisher(self):
        payload = ready_payload()
        payload.update({
            "articlePublisher": "Yahoo Finance",
            "provider": "Google News US",
            "articleCanonicalUrl": "https://247wallst.com/investing/2026/08/11/apple-bear-case/",
        })

        result = resolve_source_provenance(
            payload,
            title="Opinion: Wall Street's Apple bear case is wrong",
            source="Yahoo Finance",
            provider="Google News US",
            url=payload["articleCanonicalUrl"],
            published_at="2026-08-11T12:00:00Z",
        )

        self.assertEqual("24/7 Wall St.", result.identity.publisher)
        self.assertEqual("twenty-four-seven-wall-st", result.identity.publisher_id)
        self.assertEqual("Yahoo Finance", result.identity.republisher)
        self.assertEqual("Google News US", result.identity.distribution_channel)
        self.assertEqual("C", result.identity.publisher_tier)
        self.assertTrue(result.provenance_complete)

    def test_google_news_is_a_channel_and_not_an_original_publisher(self):
        result = resolve_source_provenance(
            ready_payload(),
            title="NVIDIA update",
            source="Google News US",
            provider="Google News US",
            url="https://news.google.com/rss/articles/test",
        )

        self.assertEqual("DISCOVERY_ONLY", result.identity.publisher_tier)
        self.assertEqual("Google News US", result.identity.distribution_channel)
        self.assertFalse(result.provenance_complete)

    def test_source_registry_extends_existing_assignment_setting(self):
        registry = SourceRegistry("example.com=trusted,origin=example-wire")
        entry = registry.by_host("www.example.com")

        self.assertEqual("example-wire", entry.publisher_id)
        self.assertEqual("B", entry.tier)
        self.assertEqual("bloomberg-law", registry.by_name("Bloomberg Law News").publisher_id)
        self.assertEqual("yonhap-infomax", registry.by_host("news.einfomax.co.kr").publisher_id)

    def test_short_official_alias_does_not_match_securityweek(self):
        registry = SourceRegistry()

        self.assertIsNone(registry.by_name("SecurityWeek"))
        self.assertEqual("sec-edgar", registry.by_name("SEC filing").publisher_id)

    def test_official_publisher_requires_its_registered_domain(self):
        result = resolve_source_provenance(
            ready_payload(),
            title="SecurityWeek vulnerability report",
            source="SEC EDGAR",
            provider="Google News US",
            url="https://www.securityweek.com/security-report/",
            published_at="2026-08-20T10:00:00Z",
        )

        self.assertNotEqual("sec-edgar", result.identity.publisher_id)
        self.assertFalse(result.provenance_complete)
        self.assertIn("official-publisher-domain-mismatch", result.reason_codes)

    def test_semantic_event_cluster_groups_independent_reports(self):
        first = Evidence("SK하이닉스, 40조원 규모 자사주 매입 추진", ready_payload(), "000660")
        first.evidence_id = "research:000660:news:one"
        first.source = "뉴스핌"
        first.url = "https://www.newspim.com/news/view/one"
        first.published_at = "2026-08-20T01:00:00Z"
        first.raw_payload.update({"eventType": "capital_policy", "articleText": "SK하이닉스가 40조원 규모의 자사주 매입 계획을 검토한다. " * 5})
        second = Evidence("SK Hynix plans 40 trillion won share buyback", ready_payload(), "000660")
        second.evidence_id = "research:000660:news:two"
        second.source = "Reuters"
        second.url = "https://www.reuters.com/technology/sk-hynix-buyback"
        second.published_at = "2026-08-20T02:00:00Z"
        second.raw_payload.update({"eventType": "capital_policy", "articleText": "SK Hynix outlined a 40 trillion won share buyback proposal. " * 5})

        normalized = normalize_evidence_sources([first, second])

        self.assertEqual(normalized[0].raw_payload["storyClusterId"], normalized[1].raw_payload["storyClusterId"])
        self.assertEqual("independent-confirmation", normalized[1].raw_payload["evidenceRelationship"])

    def test_revalidation_splits_share_compensation_from_share_buyback(self):
        buyback = Evidence("SK하이닉스, 자사주 매입 추진", ready_payload(), "000660")
        buyback.evidence_id = "research:000660:news:buyback"
        buyback.url = "https://example.test/sk-hynix-buyback"
        buyback.published_at = "2026-08-20T01:00:00Z"
        buyback.raw_payload.update({
            "eventType": "capital_policy",
            "storyClusterId": "story:legacy-overmerged",
            "articleText": "SK하이닉스가 주주환원을 위해 자사주 매입을 추진한다. " * 5,
        })
        compensation = Evidence("SK하이닉스, 성과급을 자사주로 지급", ready_payload(), "000660")
        compensation.evidence_id = "research:000660:news:compensation"
        compensation.url = "https://example.test/sk-hynix-compensation"
        compensation.published_at = "2026-08-20T02:00:00Z"
        compensation.raw_payload.update({
            "eventType": "labor",
            "storyClusterId": "story:legacy-overmerged",
            "articleText": "SK하이닉스 노사는 직원 성과급을 자사주로 지급하기로 합의했다. " * 5,
        })

        normalized = normalize_evidence_sources([buyback, compensation])

        self.assertNotEqual(normalized[0].raw_payload["storyClusterId"], normalized[1].raw_payload["storyClusterId"])
        self.assertEqual("original", normalized[1].raw_payload["evidenceRelationship"])

    def test_revalidation_splits_company_ai_demand_story_from_market_rate_selloff(self):
        demand = Evidence(
            "NVIDIA (NVDA): What Foxconn and Super Micro Are Telling Us about the AI Boom - Yahoo Finance",
            ready_payload(),
            "NVDA",
        )
        demand.evidence_id = "research:NVDA:news:demand"
        demand.url = "https://example.test/nvidia-ai-demand"
        demand.published_at = "2026-08-19T01:00:00Z"
        demand.raw_payload.update({
            "eventType": "earnings",
            "storyClusterId": "story:legacy-overmerged",
            "articleText": "Foxconn and Super Micro described accelerating AI server demand and data-center capacity plans. " * 6,
            "articleSummaryKo": "AI 서버 수요가 향후 실적에 미칠 영향을 설명합니다.",
        })
        rates = Evidence(
            "Nvidia, AMD, Broadcom, Meta Slide as Bond Yields Surge: Why Tech Stocks Are Getting Hit - Yahoo Finance",
            ready_payload(),
            "NVDA",
        )
        rates.evidence_id = "research:NVDA:news:rates"
        rates.url = "https://example.test/tech-bond-yield-selloff"
        rates.published_at = "2026-08-18T01:00:00Z"
        rates.raw_payload.update({
            "eventType": "earnings",
            "storyClusterId": "story:legacy-overmerged",
            "articleText": "Treasury yields surged and semiconductor shares fell with the broader technology market. " * 6,
            "articleSummaryKo": "국채 수익률 급등으로 기술주가 하락했고 개별 실적 영향은 제한적입니다.",
        })

        normalized = normalize_evidence_sources([demand, rates])

        self.assertNotEqual(normalized[0].raw_payload["storyClusterId"], normalized[1].raw_payload["storyClusterId"])
        self.assertTrue(all(item.raw_payload["evidenceRelationship"] == "original" for item in normalized))

    def test_revalidation_does_not_merge_roundup_with_company_order_disclosure_review(self):
        roundup = Evidence(
            "SK하이닉스·NAVER 등 실적과 해외 수주 핵심주 주목",
            ready_payload(),
            "000660",
        )
        roundup.evidence_id = "research:000660:news:roundup"
        roundup.url = "https://example.test/market-roundup"
        roundup.published_at = "2026-08-21T01:00:00Z"
        roundup.raw_payload.update({
            "eventType": "earnings",
            "storyClusterId": "story:legacy-overmerged",
            "articleText": "여러 종목의 실적과 해외 수주를 함께 소개하는 시장 요약 기사입니다. " * 6,
            "articleSummaryKo": "여러 종목의 실적과 수주 이슈를 나열한 시장 요약입니다.",
        })
        order_review = Evidence(
            "LTA 강조한 SK하이닉스, 반기보고서에는 수주 없음",
            ready_payload(),
            "000660",
        )
        order_review.evidence_id = "research:000660:news:order-review"
        order_review.url = "https://example.test/sk-hynix-order-review"
        order_review.published_at = "2026-08-20T01:00:00Z"
        order_review.raw_payload.update({
            "eventType": "earnings",
            "storyClusterId": "story:legacy-overmerged",
            "articleText": "SK하이닉스의 장기 공급계약 설명과 반기보고서 수주 공시 사이의 차이를 검증합니다. " * 6,
            "articleSummaryKo": "장기 공급계약 설명과 반기보고서 공시의 차이를 검증합니다.",
        })

        normalized = normalize_evidence_sources([roundup, order_review])

        self.assertNotEqual(normalized[0].raw_payload["storyClusterId"], normalized[1].raw_payload["storyClusterId"])

    def test_exact_republication_is_not_a_second_alert_or_reasoning_source(self):
        first = Evidence("NVIDIA announces multi-year supply agreement", ready_payload())
        first.evidence_id = "research:NVDA:news:original"
        first.published_at = "2026-08-11T12:00:00Z"
        first.observed_at = "2026-08-11T12:01:00Z"
        first.url = "https://www.reuters.com/technology/nvidia-supply-agreement"
        first.raw_payload.update({
            "articlePublisher": "Reuters",
            "provider": "Reuters",
            "articleCanonicalUrl": first.url,
        })
        copied = Evidence("NVIDIA announces multi-year supply agreement", ready_payload())
        copied.evidence_id = "research:NVDA:news:copy"
        copied.published_at = "2026-08-11T12:05:00Z"
        copied.observed_at = "2026-08-11T12:06:00Z"
        copied.url = first.url + "?utm_source=google"
        copied.raw_payload.update({
            "articlePublisher": "Reuters",
            "provider": "Google News US",
            "articleCanonicalUrl": copied.url,
        })

        normalized = normalize_evidence_sources([first, copied])
        for item in normalized:
            annotate_evidence_eligibility(item)
        root = next(item for item in normalized if item.evidence_id.endswith("original"))
        duplicate = next(item for item in normalized if item.evidence_id.endswith("copy"))

        self.assertEqual("original", root.raw_payload["evidenceRelationship"])
        self.assertEqual("exact-duplicate", duplicate.raw_payload["evidenceRelationship"])
        self.assertFalse(duplicate.raw_payload["evidenceGovernance"]["investmentJudgmentEligible"])
        self.assertFalse(duplicate.raw_payload["newsEligibility"]["displayEligible"])
        self.assertIn(
            "duplicate-publication",
            duplicate.raw_payload["newsEligibility"]["layers"]["display"]["reasonCodes"],
        )

    def test_opinion_can_be_displayed_but_not_alerted_or_used_for_reasoning(self):
        payload = ready_payload()
        result = assess_news_eligibility(
            payload,
            title="Opinion: NVIDIA valuation is too high",
            summary="NVIDIA valuation commentary.",
            symbol="NVDA",
            name="NVIDIA",
            source="Reuters",
            url="https://www.reuters.com/breakingviews/nvidia-valuation-opinion",
        )

        self.assertTrue(result.display.eligible)
        self.assertFalse(result.alert.eligible)
        self.assertFalse(result.reasoning.eligible)
        self.assertIn("content-type-not-alertable", result.alert.reason_codes)

    def test_revalidation_is_silent_and_blocks_wrong_subject(self):
        payload = ready_payload()
        evidence = Evidence("Onto Innovation Stock Could Catch Nvidia", payload)
        repository = MemoryRepository([evidence])

        result = RevalidateNewsIntelligenceService(repository).revalidate()

        self.assertEqual(1, result.blocked_subject_count)
        self.assertEqual(1, result.saved_count)
        self.assertFalse(result.to_dict()["notificationReplay"])
        self.assertEqual("entity_mismatch", repository.saved[0].raw_payload["relationScope"])
        self.assertFalse(repository.saved[0].raw_payload["newsEligibility"]["alertEligible"])

        second = RevalidateNewsIntelligenceService(repository).revalidate(dry_run=True)

        self.assertEqual(0, second.changed_count)
        self.assertEqual(0, second.saved_count)

    def test_cross_context_event_contains_only_alert_eligible_articles(self):
        allowed = Evidence("NVIDIA announces multi-year supply agreement", ready_payload())
        annotate_evidence_eligibility(allowed)
        blocked_payload = ready_payload()
        blocked_payload["aiAnalysis"] = {"status": "local", "needsReview": False}
        blocked = Evidence("NVIDIA commentary", blocked_payload)
        annotate_evidence_eligibility(blocked)

        event = news_article_analyzed_event({
            "status": "ok",
            "materialChangedItems": [
                {"kind": item.kind, "symbol": item.symbol, "title": item.title, "payload": item.raw_payload}
                for item in (allowed, blocked)
            ],
        })

        self.assertEqual(NEWS_ARTICLE_ANALYZED, event.name)
        self.assertEqual(1, event.payload["materialChangedCount"])
        self.assertEqual("NVIDIA announces multi-year supply agreement", event.payload["materialChangedItems"][0]["title"])

    def test_new_context_does_not_import_legacy_domain_or_application_modules(self):
        root = Path(__file__).resolve().parents[1] / "digital_twin" / "news_intelligence"
        source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
        self.assertNotIn("digital_twin.domain", source)
        self.assertNotIn("digital_twin.application", source)
        self.assertNotIn("from ...domain", source)


if __name__ == "__main__":
    unittest.main()
