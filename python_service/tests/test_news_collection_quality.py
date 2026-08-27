import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.news_ai_analysis_service import NewsAiAnalysisService
from digital_twin.application.news_analysis_enrichment_service import NewsAnalysisEnrichmentRunner
from digital_twin.application.news_collection_service import NewsCollectionRunner, parse_news_timestamp
from digital_twin.application.news_digest_service import NewsDigestEnqueuer
from digital_twin.domain.data_pipeline_health import evaluate_news_collection_health
from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.domain.materiality import evidence_materiality
from digital_twin.domain.news_ai_analysis import article_text_parts, local_news_ai_analysis
from digital_twin.domain.news_analysis import article_analysis_facts, article_quality_gate
from digital_twin.domain.news_collection_quality import assess_news_collection_admission
from digital_twin.domain.sent_article_filter import (
    article_identity_keys,
    article_weak_identity_keys,
    collect_article_identity_keys_from_context,
)
from digital_twin.infrastructure.external_signal_utils import ExternalCircuitOpen
from digital_twin.infrastructure import news_sources
from digital_twin.infrastructure.news_sources import NewsSourceGateway, article_metadata_from_html, extract_article_text, news_article_identity_token


class NewsCollectionQualityTests(unittest.TestCase):
    def target(self):
        return NewsCollectionTarget("AAPL", "Apple", "NASDAQ", "USD", "Technology")

    def evidence(self, payload=None):
        return ResearchEvidence(
            "research:AAPL:news:quality-test",
            "AAPL",
            "news",
            "Reuters",
            "Apple reports a services update",
            "Apple services update",
            "https://example.test/apple-services",
            "2026-07-24T00:00:00Z",
            "context",
            published_at="2026-07-24T00:00:00Z",
            raw_payload=payload or {
                "relationScope": "direct",
                "articleReadStatus": "body",
                "articleText": "Apple reported that services revenue improved with a clearer outlook for the next quarter. " * 8,
                "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
            },
        )

    def test_generic_takeaway_is_diagnostic_and_cannot_suppress_another_article(self):
        generic_takeaway = "실적과 이익 전망 변화가 핵심"
        first = {
            "kind": "news",
            "evidenceId": "research:AAPL:news:first",
            "title": "Apple raises annual services revenue guidance",
            "url": "https://example.test/apple-guidance",
            "articleFacts": {"eventTakeaway": generic_takeaway},
        }
        second = {
            "kind": "news",
            "evidenceId": "research:TSLA:news:second",
            "title": "Tesla reports quarterly vehicle deliveries",
            "url": "https://example.test/tesla-deliveries",
            "articleFacts": {"eventTakeaway": generic_takeaway},
        }

        self.assertFalse(article_identity_keys(first).intersection(article_identity_keys(second)))
        self.assertTrue(article_weak_identity_keys(first).intersection(article_weak_identity_keys(second)))

    def test_exact_url_still_suppresses_a_syndicated_duplicate(self):
        first = {
            "kind": "news",
            "evidenceId": "research:AAPL:news:first",
            "title": "Apple raises annual services revenue guidance",
            "url": "https://example.test/apple-guidance?utm_source=feed",
        }
        second = {
            "kind": "news",
            "evidenceId": "research:AAPL:news:second",
            "title": "Apple lifts its annual services outlook",
            "url": "https://example.test/apple-guidance",
        }

        self.assertTrue(article_identity_keys(first).intersection(article_identity_keys(second)))

    def test_same_symbol_earnings_articles_share_a_bounded_event_identity(self):
        first = {
            "kind": "news",
            "symbol": "NVDA",
            "eventType": "earnings",
            "publishedAt": "2026-08-26T21:00:00Z",
            "evidenceId": "research:NVDA:news:first",
            "title": "Nvidia quarterly earnings beat expectations",
            "url": "https://example.test/nvidia-earnings",
        }
        follow_up = {
            "kind": "news",
            "symbol": "NVDA",
            "eventType": "earnings",
            "publishedAt": "2026-08-27T09:00:00Z",
            "evidenceId": "research:NVDA:news:follow-up",
            "title": "Wall Street reviews Nvidia revenue outlook",
            "url": "https://example.test/nvidia-outlook",
        }
        unrelated = {
            **follow_up,
            "symbol": "AMD",
            "evidenceId": "research:AMD:news:earnings",
            "url": "https://example.test/amd-earnings",
        }

        self.assertTrue(article_identity_keys(first).intersection(article_identity_keys(follow_up)))
        self.assertFalse(article_identity_keys(first).intersection(article_identity_keys(unrelated)))

    def test_earnings_event_identity_does_not_span_an_unbounded_date_range(self):
        first = {
            "kind": "news",
            "symbol": "NVDA",
            "eventType": "earnings",
            "publishedAt": "2026-08-20T21:00:00Z",
            "evidenceId": "research:NVDA:news:first",
            "title": "Nvidia quarterly earnings beat expectations",
            "url": "https://example.test/nvidia-earnings",
        }
        later = {
            **first,
            "publishedAt": "2026-08-24T09:00:00Z",
            "evidenceId": "research:NVDA:news:later",
            "title": "Nvidia updates its quarterly earnings outlook",
            "url": "https://example.test/nvidia-later",
        }

        self.assertFalse(article_identity_keys(first).intersection(article_identity_keys(later)))

    def test_legacy_weak_precomputed_keys_are_ignored_when_rebuilding_delivery_history(self):
        context = {
            "newsDigest": {
                "eventKind": "news",
                "primaryEvidenceId": "research:AAPL:news:first",
                "primaryUrl": "https://example.test/apple-guidance",
                "primaryTitle": "Apple raises annual services revenue guidance",
                "articleKeys": [
                    "takeaway:757b07881d88148b00bb",
                    "title:0123456789abcdef0123",
                    "url:abcdef0123456789abcd",
                ],
            }
        }

        keys = collect_article_identity_keys_from_context(context)

        self.assertNotIn("takeaway:757b07881d88148b00bb", keys)
        self.assertNotIn("title:0123456789abcdef0123", keys)
        self.assertIn("url:abcdef0123456789abcd", keys)

    def test_analysis_work_revision_depends_on_source_not_provisional_ai_state(self):
        initial = self.evidence({
            "relationScope": "direct",
            "articleText": "Apple raised annual services revenue guidance. " * 8,
            "aiAnalysis": {"status": "deferred"},
        })
        replayed = self.evidence({
            "relationScope": "direct",
            "articleText": "Apple raised annual services revenue guidance. " * 8,
            "aiAnalysis": {"status": "local", "lastExternalAttemptAt": "2026-08-28T00:00:00Z"},
        })

        self.assertEqual(
            NewsAnalysisEnrichmentRunner.work_revision(initial, "model"),
            NewsAnalysisEnrichmentRunner.work_revision(replayed, "model"),
        )

    def test_digest_replay_rejects_a_different_current_source_revision(self):
        current = self.evidence({
            "articleSourceRevision": "news-source:current",
            "articleText": "Apple published corrected annual guidance. " * 8,
        })
        repository = SimpleNamespace(get=lambda _evidence_id: current)
        enqueuer = NewsDigestEnqueuer(None, None, None, evidence_repository=repository)

        hydrated = enqueuer.hydrate_canonical_items([{
            "kind": "news",
            "evidenceId": current.evidence_id,
            "articleSourceRevision": "news-source:previous",
        }])

        self.assertEqual([], hydrated)

    def test_digest_replay_requires_an_exact_enrichment_snapshot_when_revision_bound(self):
        current = self.evidence({
            "articleSourceRevision": "news-source:current",
            "articleEnrichmentRevision": "news-enrichment:current",
            "articleText": "Apple published corrected annual guidance. " * 8,
        })
        repository = SimpleNamespace(get=lambda _evidence_id: current)
        enqueuer = NewsDigestEnqueuer(None, None, None, evidence_repository=repository)

        hydrated = enqueuer.hydrate_canonical_items([{
            "kind": "news",
            "evidenceId": current.evidence_id,
            "articleSourceRevision": "news-source:current",
            "articleEnrichmentRevision": "news-enrichment:previous",
        }])

        self.assertEqual([], hydrated)

    def test_article_body_cache_reuses_content_without_spending_run_budget(self):
        calls = []
        html = "<html><body><article><p>Apple reported improved services revenue and a detailed outlook.</p></article></body></html>"
        gateway = NewsSourceGateway(
            {"newsCollectionArticleBodyMaxPerRun": "1"},
            fetch_text=lambda url, _headers=None: calls.append(url) or html,
        )

        first = gateway.article_content_for_url("https://example.test/story?utm_source=rss")
        second = gateway.article_content_for_url("https://example.test/story")

        self.assertEqual(first, second)
        self.assertEqual(1, len(calls))
        self.assertEqual(1, gateway._article_body_fetches_used)

    def test_compact_gdelt_timestamp_is_normalized_and_rejected_when_stale(self):
        parsed = parse_news_timestamp("20260719T091519")
        stale = self.evidence()
        stale.published_at = "20260719T091519"
        stale.observed_at = "20260719T091519"
        runner = NewsCollectionRunner(
            account_repository=SimpleNamespace(load=lambda: []),
            monitor_store=SimpleNamespace(previous={}),
            symbol_store=SimpleNamespace(),
            evidence_store=SimpleNamespace(),
            gateway=SimpleNamespace(providers=lambda: []),
            settings={"newsEvidenceMaxAgeMinutes": "4320"},
        )

        fresh, rejected = runner.fresh_news_items([stale])

        self.assertEqual("2026-07-19T09:15:19+00:00", parsed.isoformat())
        self.assertEqual([], fresh)
        self.assertEqual([stale], rejected)

    def test_feed_only_related_product_from_unknown_source_is_rejected(self):
        target = NewsCollectionTarget("MSTR", "Strategy", "NASDAQ", "USD", "Digital Assets")
        gateway = NewsSourceGateway({
            "newsCollectionArticleBodyMaxPerTarget": "0",
            "newsCollectionArticleBodyMaxPerRun": "0",
        })
        gateway.reset_provider_diagnostics()

        evidence = gateway.news_evidence_from_article(
            target,
            "GDELT",
            "unknown-finance-blog.test",
            "MSTY Covered-Call ETF Hits Record Monthly Distribution",
            "The ETF owns MSTR-linked exposure and reacts to Strategy share volatility.",
            "https://unknown-finance-blog.test/msty-distribution",
            "2026-08-04T05:00:00Z",
        )

        self.assertIsNone(evidence)
        self.assertEqual(1, gateway._current_provider_diagnostics["feedOnlyQualityRejectedCount"])

    def test_google_feed_ranks_direct_material_story_before_resolution_budget(self):
        xml = """<rss><channel>
        <item><title>Technology ETFs rise as AI stocks gain</title><link>https://news.google.com/rss/articles/context</link><pubDate>Tue, 04 Aug 2026 05:00:00 GMT</pubDate><source>Unknown Blog</source><description>Technology market update</description></item>
        <item><title>Apple reports third-quarter revenue and raises guidance</title><link>https://news.google.com/rss/articles/direct</link><pubDate>Tue, 04 Aug 2026 05:10:00 GMT</pubDate><source>Reuters</source><description>Apple reported revenue and raised guidance.</description></item>
        </channel></rss>"""
        resolved = []
        gateway = NewsSourceGateway(
            {
                "newsCollectionGoogleOriginalUrlMaxPerTarget": "1",
                "newsCollectionGoogleOriginalUrlMaxPerRun": "1",
                "newsCollectionArticleBodyMinimumChars": "80",
                "newsCollectionLookbackMinutes": "1440",
            },
            fetch_text=lambda _url, _headers=None: xml,
            now_provider=lambda: datetime(2026, 8, 4, 5, 20, tzinfo=timezone.utc),
        )

        def resolve(url):
            resolved.append(url)
            gateway._google_original_url_fetches_for_target += 1
            gateway._google_original_url_fetches_used += 1
            return "https://reuters.example.test/apple-results"

        gateway.resolve_google_news_article_url = resolve
        gateway.article_content_for_url = lambda url: {
            "text": "Apple reported third-quarter revenue and raised its full-year guidance after services growth. " * 8,
            "canonicalUrl": url,
            "publisher": "Reuters",
        }
        gateway.reset_provider_diagnostics()

        evidence = gateway.fetch_google_news_rss(self.target(), "US")

        self.assertEqual(["Apple reports third-quarter revenue and raises guidance"], [item.title for item in evidence])
        self.assertEqual(["https://news.google.com/rss/articles/direct"], resolved)
        self.assertEqual(1, gateway._current_provider_diagnostics["googleOriginalUrlDeferredCandidateCount"])
        self.assertEqual(0, gateway._current_provider_diagnostics["googleOriginalUrlBudgetRejectedCount"])

    def test_duplicate_cleanup_keeps_the_stable_canonical_identity(self):
        canonical_url = "https://example.test/apple-results"
        stable_id = "research:AAPL:news:" + news_article_identity_token("Google News US", "ignored", canonical_url)
        stable = self.evidence()
        stable.evidence_id = stable_id
        stable.url = canonical_url
        legacy = self.evidence()
        legacy.evidence_id = "research:AAPL:news:legacy-title-key"
        legacy.url = canonical_url + "?utm_source=rss"

        class Store:
            def __init__(self):
                self.deleted = []

            def latest(self, **_kwargs):
                return [legacy, stable]

            def delete(self, evidence_id):
                self.deleted.append(evidence_id)
                return True

        store = Store()
        runner = NewsCollectionRunner(
            account_repository=SimpleNamespace(load=lambda: []),
            monitor_store=SimpleNamespace(previous={}),
            symbol_store=SimpleNamespace(),
            evidence_store=store,
            gateway=SimpleNamespace(providers=lambda: []),
            settings={"newsEvidenceCleanupEnabled": "1"},
        )

        result = runner.delete_duplicate_news()

        self.assertEqual(1, result["deleted"])
        self.assertEqual([legacy.evidence_id], store.deleted)

    def test_runner_starts_a_fresh_budget_window_for_long_lived_collaborators(self):
        gateway = SimpleNamespace(begin_calls=0, providers=lambda: [])
        analyzer = SimpleNamespace(begin_calls=0)

        def begin_gateway():
            gateway.begin_calls += 1
            return {"articleBodyFetchesUsed": 0}

        def begin_analyzer():
            analyzer.begin_calls += 1
            return {"externalAnalysisUsed": 0}

        gateway.begin_run = begin_gateway
        analyzer.begin_run = begin_analyzer
        runner = NewsCollectionRunner(
            account_repository=SimpleNamespace(load=lambda: []),
            monitor_store=SimpleNamespace(previous={}),
            symbol_store=SimpleNamespace(),
            evidence_store=SimpleNamespace(),
            gateway=gateway,
            article_analysis_service=analyzer,
            settings={"newsEvidenceCleanupEnabled": "0"},
        )

        result = runner.run_once(force=True)

        self.assertEqual("noTargets", result["status"])
        self.assertEqual(1, gateway.begin_calls)
        self.assertEqual(1, analyzer.begin_calls)

    def test_json_ld_body_and_reporter_text_are_preserved(self):
        article_body = (
            "Apple announced a services update with expanded subscription features and revised operating targets. "
            "홍길동 기자는 회사가 다음 분기 매출과 마진 전망을 함께 제시했다고 전했다. "
            "The company said that customer retention and recurring revenue remain central to its outlook. "
            "Investors will review the next earnings release for confirmation of the stated targets."
        ) * 5
        html = (
            "<html><head><script type=\"application/ld+json\">"
            '{"@context":"https://schema.org","@type":"NewsArticle","articleBody":"'
            + article_body.replace('"', '\\"')
            + '"}</script></head><body><p>Navigation item</p></body></html>'
        )

        extracted = extract_article_text(html)

        self.assertIn("홍길동 기자", extracted)
        self.assertIn("customer retention", extracted)
        self.assertGreaterEqual(len(extracted), 1200)

    def test_full_article_text_is_used_and_short_body_is_blocked_from_materiality(self):
        full_body = "Apple described services revenue, customer retention, operating margins, and its outlook for the coming quarter. " * 24
        evidence = self.evidence({
            "relationScope": "direct",
            "articleReadStatus": "body",
            "articleText": full_body,
            "articleTextPreview": full_body[:700],
            "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
        })

        _title, body, _summary, read_scope = article_text_parts(evidence)
        self.assertEqual(full_body.strip(), body)
        self.assertEqual("body", read_scope)

        facts = article_analysis_facts(
            self.target(),
            "Apple update",
            "Apple issued a very short update.",
            "",
            {"relationScope": "direct", "articleReadStatus": "body"},
            body_minimum_chars=280,
        )
        gate = article_quality_gate(facts)
        short_evidence = self.evidence({
            "relationScope": "direct",
            "articleReadStatus": "body",
            "bodyQualityPassed": gate["passed"],
            "articleFacts": facts,
            "qualityGate": gate,
        })

        assessment = evidence_materiality(short_evidence, {"materialityGateEnabled": "1"})
        self.assertFalse(gate["passed"])
        self.assertFalse(assessment.passed)
        self.assertEqual("blocked", assessment.review_level)

    def test_health_is_degraded_when_url_budget_blocks_most_candidates(self):
        health = evaluate_news_collection_health({
            "status": "ok",
            "targetCount": 1,
            "fetchedCount": 1,
            "savedCount": 1,
            "statuses": [{
                "source": "google_rss_kr",
                "ok": True,
                "candidateCount": 13,
                "acceptedCount": 1,
                "googleOriginalUrlBudgetRejectedCount": 12,
            }],
        })

        self.assertEqual("degraded", health.state)
        self.assertEqual("article-original-url-budget-exhausted", health.reason_code)

    def test_collection_admission_keeps_only_direct_material_body_from_trusted_source(self):
        material = ResearchEvidence(
            "research:AAPL:news:material",
            "AAPL",
            "news",
            "Reuters",
            "Apple reports earnings and raises guidance",
            "Apple reported higher revenue and raised its full-year guidance.",
            "https://reuters.example.test/apple-earnings",
            "2026-08-10T00:00:00Z",
            "support",
            published_at="2026-08-10T00:00:00Z",
            raw_payload={
                "relationScope": "direct",
                "eventType": "earnings",
                "articleReadStatus": "body",
                "articleText": "Apple reported higher revenue and raised its full-year guidance. " * 8,
                "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
                "bodyQualityPassed": True,
                "qualityGate": {"passed": True},
            },
        )
        routine = ResearchEvidence(
            "research:AAPL:news:routine",
            "AAPL",
            "news",
            "Reuters",
            "Apple adds a seasonal color to an accessory",
            "Apple added a seasonal color to one accessory.",
            "https://reuters.example.test/apple-accessory",
            "2026-08-10T00:00:00Z",
            "context",
            published_at="2026-08-10T00:00:00Z",
            raw_payload={
                "relationScope": "direct",
                "eventType": "product",
                "articleReadStatus": "body",
                "articleText": "Apple added a seasonal color to one accessory without changing pricing or guidance. " * 8,
                "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
                "bodyQualityPassed": True,
                "qualityGate": {"passed": True},
            },
        )

        policy = {"newsCollectionQualityGateEnabled": "1"}
        accepted = assess_news_collection_admission(material, policy)
        rejected = assess_news_collection_admission(routine, policy)

        self.assertTrue(accepted.passed)
        self.assertFalse(rejected.passed)
        self.assertIn("materiality-below-policy", rejected.reason_codes)

    def test_direct_adr_listing_is_material_for_collection(self):
        listing = ResearchEvidence(
            "research:000660:news:adr-listing",
            "000660",
            "news",
            "Reuters",
            "SK Hynix evaluates a US ADR listing",
            "SK Hynix is evaluating an ADR listing in the United States.",
            "https://reuters.example.test/sk-hynix-adr",
            "2026-08-10T00:00:00Z",
            "context",
            published_at="2026-08-10T00:00:00Z",
            raw_payload={
                "relationScope": "direct",
                "eventType": "listing",
                "articleReadStatus": "body",
                "articleText": "SK Hynix is evaluating an ADR listing in the United States. " * 8,
                "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
                "bodyQualityPassed": True,
                "qualityGate": {"passed": True},
            },
        )

        result = assess_news_collection_admission(listing, {"newsCollectionQualityGateEnabled": "1"})

        self.assertEqual("material", listing.materiality_state)
        self.assertTrue(result.passed)

    def test_runner_does_not_persist_articles_rejected_by_collection_quality(self):
        observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        def article(evidence_id, event_type, polarity):
            return ResearchEvidence(
                evidence_id,
                "AAPL",
                "news",
                "Reuters",
                "Apple " + ("reports earnings" if event_type == "earnings" else "adds an accessory color"),
                "Apple published a company update.",
                "https://reuters.example.test/" + evidence_id.rsplit(":", 1)[-1],
                observed_at,
                polarity,
                published_at=observed_at,
                raw_payload={
                    "relationScope": "direct",
                    "eventType": event_type,
                    "articleReadStatus": "body",
                    "articleText": "Apple published a detailed company update with management context. " * 8,
                    "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
                    "bodyQualityPassed": True,
                    "qualityGate": {"passed": True},
                },
            )

        important = article("research:AAPL:news:important", "earnings", "support")
        routine = article("research:AAPL:news:routine", "product", "context")

        class Store:
            def __init__(self):
                self.persisted = []
                self.last_changed_items = []
                self.last_changed_symbols = []

            def latest(self, **_kwargs):
                return []

            def upsert_many(self, rows):
                self.persisted = list(rows)
                self.last_changed_items = list(rows)
                self.last_changed_symbols = ["AAPL"] if rows else []
                return len(self.persisted)

        class Gateway:
            def begin_run(self):
                return {}

            def collect_for_target(self, _target):
                return [important, routine], [{"source": "test", "ok": True}]

            def providers(self):
                return ["test"]

        store = Store()
        runner = NewsCollectionRunner(
            account_repository=SimpleNamespace(load=lambda: []),
            monitor_store=SimpleNamespace(previous={}),
            symbol_store=SimpleNamespace(),
            evidence_store=store,
            gateway=Gateway(),
            settings={
                "newsEvidenceCleanupEnabled": "0",
                "newsCollectionQualityGateEnabled": "1",
                "newsCollectionMinimumRelevanceState": "direct",
                "newsCollectionMinimumMaterialityState": "material",
                "newsCollectionMinimumSourceTrustState": "standard",
                "newsCollectionRequireArticleBody": "1",
            },
        )
        runner.target_plan = lambda: {
            "targets": [self.target()],
            "candidateCount": 1,
            "selectedCount": 1,
            "maxSymbols": 1,
            "rotationSlot": 0,
            "rotationStartIndex": 0,
            "nextRotationAt": observed_at,
            "selectedSymbols": ["AAPL"],
            "omittedSymbolCount": 0,
        }

        result = runner.run_once(force=True)

        self.assertEqual(2, result["fetchedCount"])
        self.assertEqual(1, result["admittedCount"])
        self.assertEqual(1, result["qualityRejectedCount"])
        self.assertEqual([important.evidence_id], [item.evidence_id for item in store.persisted])
        self.assertEqual("retain", store.persisted[0].raw_payload["collectionAdmission"]["decision"])


if __name__ == "__main__":
    unittest.main()
