import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.news_intelligence.application.analyze_article import annotate_evidence_eligibility
from digital_twin.news_intelligence.application.revalidate_articles import RevalidateNewsIntelligenceService
from digital_twin.news_intelligence.domain.article_quality import inspect_article_body
from digital_twin.news_intelligence.domain.eligibility import assess_news_eligibility
from digital_twin.news_intelligence.domain.entity_resolution import matched_aliases, resolve_target_entity
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

    def test_body_quality_rejects_navigation_and_investment_promotion(self):
        result = inspect_article_body(
            ("NVIDIA announced quarterly results with audited revenue details. " * 8)
            + " Continue reading. Missed Nvidia? Is now the time to buy?"
        )

        self.assertFalse(result.passed)
        self.assertIn("publisher-navigation", result.issues)
        self.assertIn("investment-promotion", result.issues)

    def test_four_eligibility_layers_require_external_analysis_and_claim_governance(self):
        payload = ready_payload()
        result = assess_news_eligibility(
            payload,
            title="NVIDIA announces multi-year supply agreement",
            summary="NVIDIA announced a verified agreement.",
            symbol="NVDA",
            name="NVIDIA",
            source="Reuters",
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
        )

        self.assertTrue(result.alert.eligible)
        self.assertFalse(result.reasoning.eligible)
        self.assertIn("claim-governance-not-eligible", result.reasoning.reason_codes)

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
        )

        self.assertTrue(result.alert.eligible)
        self.assertTrue(result.reasoning.eligible)

    def test_story_identity_does_not_merge_different_articles_by_generic_takeaway(self):
        first = {"symbol": "NVDA", "eventType": "earnings", "publishedAt": "2026-08-12T01:00:00Z", "title": "BofA raises Nvidia earnings estimate"}
        second = {"symbol": "NVDA", "eventType": "earnings", "publishedAt": "2026-08-12T02:00:00Z", "title": "Nebius expands AI infrastructure funding"}
        self.assertNotEqual(story_identity(first), story_identity(second))

    def test_story_identity_keeps_an_explicit_republication_root(self):
        original = {"claimId": "claim:nvda-contract", "title": "NVIDIA signs contract"}
        copy = {"duplicateOfClaimId": "claim:nvda-contract", "title": "엔비디아 계약 체결"}
        self.assertEqual(story_identity(original), story_identity(copy))

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
