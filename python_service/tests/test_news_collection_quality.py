import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.news_ai_analysis_service import NewsAiAnalysisService
from digital_twin.application.news_collection_service import NewsCollectionRunner, parse_news_timestamp
from digital_twin.domain.data_pipeline_health import evaluate_news_collection_health
from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.domain.materiality import evidence_materiality
from digital_twin.domain.news_ai_analysis import article_text_parts, local_news_ai_analysis
from digital_twin.domain.news_analysis import article_analysis_facts, article_quality_gate
from digital_twin.domain.news_collection_quality import assess_news_collection_admission
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

    def test_gateway_resets_per_run_body_budget(self):
        html = "<html><body><article><p>Apple reported improved services revenue and a detailed outlook for the next quarter.</p><p>Management described the demand trend and margin expectations for investors.</p></article></body></html>"
        gateway = NewsSourceGateway(
            {
                "newsCollectionArticleBodyMaxPerTarget": "10",
                "newsCollectionArticleBodyMaxPerRun": "1",
            },
            fetch_text=lambda _url, _headers=None: html,
        )

        self.assertTrue(gateway.article_text_for_url("https://example.test/first"))
        self.assertEqual("", gateway.article_text_for_url("https://example.test/second"))

        gateway.begin_run()

        self.assertTrue(gateway.article_text_for_url("https://example.test/second"))

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

    def test_same_canonical_article_url_has_one_stable_evidence_id(self):
        body = "Apple reported third-quarter services revenue and explained the outlook for subscriptions and margins. " * 8
        gateway = NewsSourceGateway({"newsCollectionArticleBodyMinimumChars": "80"})
        gateway.article_content_for_url = lambda url: {"text": body, "canonicalUrl": url, "publisher": "Reuters"}
        first = gateway.news_evidence_from_article(
            self.target(),
            "Google News US",
            "Reuters.com",
            "Apple reports third-quarter services growth - Reuters",
            "",
            "https://www.reuters.example.test/apple-results?utm_source=google",
            "2026-08-04T05:00:00Z",
        )
        second = gateway.news_evidence_from_article(
            self.target(),
            "Google News US",
            "reuters.com",
            "Apple Reports Third-Quarter Services Growth - REUTERS",
            "",
            "https://reuters.example.test/apple-results?utm_medium=rss",
            "2026-08-04T05:00:00Z",
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.evidence_id, second.evidence_id)

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

    def test_entity_cleanup_retracts_legacy_third_party_naver_pay_promotion(self):
        promotion = ResearchEvidence(
            "research:035420:news:legacy-promotion",
            "035420",
            "news",
            "Example News",
            "벤큐, 구매 후기 작성하면 네이버페이 5만원 증정",
            "제3자 제품 구매 고객에게 네이버페이 포인트를 지급합니다.",
            "https://example.test/benq-promotion",
            "2026-08-04T05:00:00Z",
            "context",
            published_at="2026-08-04T05:00:00Z",
            raw_payload={"name": "NAVER", "provider": "Google News KR"},
        )
        direct = ResearchEvidence(
            "research:035420:news:direct-launch",
            "035420",
            "news",
            "Reuters",
            "NAVER, 네이버페이 해외 결제 서비스 출시",
            "NAVER가 신규 결제 서비스를 발표했습니다.",
            "https://example.test/naver-pay-launch",
            "2026-08-04T05:00:00Z",
            "support",
            published_at="2026-08-04T05:00:00Z",
            raw_payload={"name": "NAVER", "provider": "Google News KR"},
        )

        class Store:
            def __init__(self):
                self.deleted = []

            def latest(self, **_kwargs):
                return [promotion, direct]

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

        result = runner.delete_reclassified_entity_noise_news()

        self.assertEqual(1, result["deleted"])
        self.assertEqual([promotion.evidence_id], store.deleted)

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

    def test_evidence_cleanup_runs_on_its_own_interval_not_every_collection_cycle(self):
        now = {"value": datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)}

        class Store:
            def __init__(self):
                self.cleanup_calls = []

            def delete_stale_news(self, cutoff_iso, limit=500):
                self.cleanup_calls.append({"cutoff": cutoff_iso, "limit": limit})
                return 0

        store = Store()
        runner = NewsCollectionRunner(
            account_repository=SimpleNamespace(load=lambda: []),
            monitor_store=SimpleNamespace(previous={}),
            symbol_store=SimpleNamespace(),
            evidence_store=store,
            gateway=SimpleNamespace(providers=lambda: []),
            settings={
                "newsEvidenceCleanupEnabled": "1",
                "newsEvidenceCleanupIntervalSeconds": "900",
                "newsEvidenceCleanupBatchSize": "500",
            },
            now_provider=lambda: now["value"],
        )

        first = runner.run_once(force=True)
        second = runner.run_once(force=True)
        now["value"] += timedelta(minutes=15)
        third = runner.run_once(force=True)

        self.assertEqual(2, len(store.cleanup_calls))
        self.assertEqual(50, store.cleanup_calls[0]["limit"])
        self.assertTrue(first["evidenceMaintenance"]["due"])
        self.assertFalse(second["evidenceMaintenance"]["due"])
        self.assertTrue(third["evidenceMaintenance"]["due"])

    def test_ai_fallback_is_retried_after_the_next_run_starts(self):
        calls = []

        class Analyzer:
            def analyze_with_timeout(self, target, evidence, _timeout_seconds):
                calls.append(evidence.title)
                payload = local_news_ai_analysis(target, evidence).to_dict()
                payload["model"] = "test-external-analyzer"
                payload["status"] = "fallback" if len(calls) == 1 else "ok"
                return payload

            def analyze(self, target, evidence):
                return self.analyze_with_timeout(target, evidence, 30)

        service = NewsAiAnalysisService(
            Analyzer(),
            {
                "newsAiAnalysisEnabled": "1",
                "newsAiAnalysisMaxPerTarget": "1",
                "newsAiAnalysisMaxPerRun": "1",
            },
        )

        first = service.analyze_many(self.target(), [self.evidence()])[0]
        self.assertEqual("fallback", first.raw_payload["aiAnalysis"]["status"])

        service.begin_run()
        second = service.analyze_many(self.target(), [first])[0]

        self.assertEqual(["Apple reports a services update", "Apple reports a services update"], calls)
        self.assertEqual("ok", second.raw_payload["aiAnalysis"]["status"])

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

    def test_article_metadata_prefers_canonical_url_and_json_ld_publisher(self):
        html = (
            '<html><head><link rel="canonical" href="/markets/apple-update?utm_source=rss">'
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"NewsArticle","publisher":{"name":"Example Journal"}}'
            '</script></head><body></body></html>'
        )

        metadata = article_metadata_from_html(html, "https://www.example.test/feed/article")

        self.assertEqual("https://www.example.test/markets/apple-update?utm_source=rss", metadata["canonicalUrl"])
        self.assertEqual("Example Journal", metadata["publisher"])

    def test_gateway_removes_yahoo_quote_widget_before_persisting_article_facts(self):
        target = NewsCollectionTarget("035420", "NAVER", "KOSPI", "KRW", "플랫폼")
        title = "Nvidia to acquire $1 billion of new shares of South Korea's Naver"
        url = "https://finance.yahoo.com/technology/articles/nvidia-acquire-1-billion-shares-230439371.html"
        raw_body = (
            "At Yahoo Finance, you get free stock quotes, up-to-date news, portfolio management resources, "
            "international market data, social interaction and mortgage rates that help you manage your financial life. "
            + title
            + " SEOUL, July 27 (Reuters) - South Korea's Naver said in a regulatory filing that Nvidia will acquire "
            "$1 billion of its shares to be newly issued as part of an investment partnership to build a new data center. "
            "(Reporting by Jack Kim; Editing by Chris Reese) "
            "NQ=F Nasdaq 100 Sep 26 28,622.75 +340.50 (+1.20%) "
            "BTC-USD Bitcoin USD 65,334.60 +1,038.99 (+1.62%) "
            "ETH-USD Ethereum USD 1,955.38 +82.37 (+4.40%)"
        )
        gateway = NewsSourceGateway({"newsCollectionArticleBodyMaxPerTarget": "1"})
        gateway.article_content_for_url = lambda _url: {
            "text": raw_body,
            "canonicalUrl": url,
            "publisher": "Reuters",
        }

        evidence = gateway.news_evidence_from_article(
            target,
            "Yahoo Finance Search",
            "Reuters",
            title,
            "",
            url,
            "2026-07-26T23:04:39Z",
        )

        self.assertIsNotNone(evidence)
        payload = evidence.raw_payload
        facts = payload["articleFacts"]
        self.assertNotIn("BTC-USD", payload["articleText"])
        self.assertNotIn("Bitcoin USD", payload["articleText"])
        self.assertNotIn("비트코인", payload["articleSummaryKo"])
        self.assertNotIn("비트코인", facts["topics"])
        self.assertEqual("capital_policy", facts["eventType"])

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

    def test_gdelt_uses_a_provider_level_circuit_across_symbol_queries(self):
        news_sources.NEWS_API_GUARD_STATE.clear()
        settings = {
            "externalApiCircuitFailures": "2",
            "externalApiCircuitCooldownMinutes": "30",
        }
        urls = [
            "https://api.gdeltproject.org/api/v2/doc/doc?query=NVDA",
            "https://api.gdeltproject.org/api/v2/doc/doc?query=TSLA",
            "https://api.gdeltproject.org/api/v2/doc/doc?query=AAPL",
        ]
        try:
            with patch("digital_twin.infrastructure.news_sources.default_json_fetcher", side_effect=TimeoutError("gdelt timeout")):
                gateway = NewsSourceGateway(settings)
                with self.assertRaises(RuntimeError):
                    gateway.fetch_json(urls[0], {})
                with self.assertRaises(RuntimeError):
                    gateway.fetch_json(urls[1], {})
                with self.assertRaises(ExternalCircuitOpen):
                    gateway.fetch_json(urls[2], {})
        finally:
            news_sources.NEWS_API_GUARD_STATE.clear()

    def test_circuit_open_provider_is_suppressed_for_the_rest_of_a_collection_run(self):
        calls = []

        def blocked_json(_url, _headers=None):
            calls.append("gdelt")
            raise ExternalCircuitOpen("circuit open until 2026-07-26T13:30:00Z")

        gateway = NewsSourceGateway(
            {"newsCollectionProviders": "gdelt", "newsCollectionGdeltSyncEnabled": "1"},
            fetch_json=blocked_json,
        )
        gateway.begin_run()
        first_items, first_statuses = gateway.collect_for_target(self.target())
        second_items, second_statuses = gateway.collect_for_target(
            NewsCollectionTarget("MSFT", "Microsoft", "NASDAQ", "USD", "Technology")
        )

        self.assertEqual([], first_items)
        self.assertEqual([], second_items)
        self.assertEqual(["gdelt"], calls)
        self.assertTrue(first_statuses[0]["providerSuppressed"])
        self.assertEqual("circuit-open-suppressed", first_statuses[0]["status"])
        self.assertTrue(second_statuses[0]["providerSuppressed"])
        self.assertTrue(second_statuses[0]["circuitOpen"])

    def test_bounded_parallel_primary_wave_skips_unneeded_fallback_provider(self):
        barrier = threading.Barrier(2)
        calls = []
        gateway = NewsSourceGateway({
            "newsCollectionInternationalProviders": "primary_a,primary_b,fallback_c",
            "newsCollectionBoundedParallelEnabled": "1",
            "newsCollectionProviderParallelism": "2",
            "newsCollectionPrimaryProviderCount": "2",
            "newsCollectionPrimaryMinimumItems": "1",
        })
        evidence = self.evidence()

        def fetch_provider(provider, _target):
            calls.append(provider)
            if provider in {"primary_a", "primary_b"}:
                barrier.wait(timeout=1)
            return [evidence] if provider == "primary_a" else []

        gateway.fetch_provider = fetch_provider
        items, statuses = gateway.collect_for_target(self.target())

        self.assertEqual([evidence.evidence_id], [item.evidence_id for item in items])
        self.assertEqual({"primary_a", "primary_b"}, set(calls))
        self.assertNotIn("fallback_c", calls)
        self.assertTrue(all(status.get("parallelBatch") for status in statuses))
        self.assertTrue(all(status.get("providerRole") == "primary" for status in statuses))

    def test_international_target_keeps_shared_yahoo_and_gdelt_providers(self):
        gateway = NewsSourceGateway({
            "newsCollectionInternationalProviders": "google_rss_us,yahoo_search,yahoo_finance,gdelt",
            "newsCollectionKoreanProviders": "google_rss_kr,yahoo_search,yahoo_finance,gdelt",
            "newsCollectionGdeltSyncEnabled": "1",
        })

        self.assertEqual(
            ["google_rss_us", "yahoo_search", "yahoo_finance", "gdelt"],
            gateway.providers_for_target(self.target()),
        )

    def test_collection_governs_retained_and_new_claims_across_cycles(self):
        observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        def claim(evidence_id, source, url):
            return ResearchEvidence(
                evidence_id,
                "AAPL",
                "news",
                source,
                "Apple announces a $1 billion share buyback",
                "Apple announced a $1 billion share buyback plan on Tuesday.",
                url,
                observed_at,
                "support",
                published_at=observed_at,
                raw_payload={
                    "relationScope": "direct",
                    "eventType": "capital_policy",
                    "articleReadStatus": "body",
                    "articleText": "Apple announced a $1 billion share buyback plan on Tuesday.",
                    "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
                    "bodyQualityPassed": True,
                    "articleSummaryKo": "애플이 10억 달러 규모의 자사주 매입 계획을 발표했습니다.",
                    "summaryQualityState": "ready",
                    "articleSummaryQuality": {"state": "ready", "issues": []},
                    "aiAnalysis": {"status": "ok", "needsReview": False},
                },
            )

        retained = claim("research:AAPL:news:retained", "Reuters", "https://www.reuters.com/technology/apple-buyback")
        current = claim("research:AAPL:news:current", "Bloomberg", "https://www.bloomberg.com/news/articles/apple-buyback")

        class Store:
            def __init__(self):
                self.persisted = []
                self.last_changed_items = []
                self.last_changed_symbols = []

            def latest(self, **_kwargs):
                return [retained]

            def upsert_many(self, rows):
                self.persisted = list(rows)
                self.last_changed_items = list(rows)
                self.last_changed_symbols = ["AAPL"]
                return len(self.persisted)

            def summary(self):
                return {"total": len(self.persisted)}

        class Gateway:
            def begin_run(self):
                return {}

            def collect_for_target(self, _target):
                return [current], [{"source": "test", "ok": True}]

            def providers(self):
                return ["test"]

            def korean_providers(self):
                return []

        store = Store()
        runner = NewsCollectionRunner(
            account_repository=SimpleNamespace(load=lambda: []),
            monitor_store=SimpleNamespace(previous={}),
            symbol_store=SimpleNamespace(),
            evidence_store=store,
            gateway=Gateway(),
            settings={
                "newsEvidenceCleanupEnabled": "0",
                "researchClaimRequireVerifiedForInvestment": "1",
                "researchClaimMinimumIndependentSources": "2",
                "researchClaimCrossSourceWindowHours": "72",
                "researchClaimSimilarityThreshold": "0.32",
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

        self.assertEqual(2, result["savedCount"])
        self.assertEqual({retained.evidence_id, current.evidence_id}, {item.evidence_id for item in store.persisted})
        self.assertEqual(2, retained.raw_payload["evidenceGovernance"]["independentSourceCount"])
        self.assertTrue(retained.raw_payload["evidenceGovernance"]["investmentJudgmentEligible"])

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

    def test_collection_admission_requires_source_and_body_quality(self):
        unknown_source = self.evidence({
            "relationScope": "direct",
            "eventType": "capital_policy",
            "sourceTrustState": "unknown",
            "materialityState": "material",
            "articleReadStatus": "body",
            "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
            "bodyQualityPassed": True,
            "qualityGate": {"passed": True},
        })
        unknown_source.source = "Unknown Publisher"
        missing_body = self.evidence({
            "relationScope": "direct",
            "eventType": "capital_policy",
            "sourceTrustState": "trusted",
            "materialityState": "material",
            "articleReadStatus": "feed-summary",
            "articleFacts": {"bodyAvailable": False, "bodyQualityPassed": False},
            "bodyQualityPassed": False,
            "qualityGate": {"passed": False},
        })

        policy = {"newsCollectionQualityGateEnabled": "1"}
        source_result = assess_news_collection_admission(unknown_source, policy)
        body_result = assess_news_collection_admission(missing_body, policy)

        self.assertFalse(source_result.passed)
        self.assertIn("source-trust-below-policy", source_result.reason_codes)
        self.assertFalse(body_result.passed)
        self.assertIn("article-body-missing", body_result.reason_codes)
        self.assertIn("article-quality-gate-failed", body_result.reason_codes)

    def test_claim_footer_does_not_create_article_correction_exception(self):
        routine = ResearchEvidence(
            "research:035720:news:award-category",
            "035720",
            "news",
            "연합뉴스",
            "카카오엔터, 음악 시상 부문 신설",
            "카카오엔터테인먼트가 음악 시상식에 새 부문을 신설했습니다.",
            "https://example.test/kakao-award-category",
            "2026-08-10T00:00:00Z",
            "context",
            published_at="2026-08-10T00:00:00Z",
            raw_payload={
                "relationScope": "direct",
                "eventType": "product",
                "sourceTrustState": "trusted",
                "materialityState": "notable",
                "articleReadStatus": "body",
                "articleFacts": {"bodyAvailable": True, "bodyQualityPassed": True},
                "bodyQualityPassed": True,
                "qualityGate": {"passed": True},
                "isCorrection": "false",
                "claimLedger": {
                    "claims": [
                        {"claim": "새 시상 부문 신설", "isCorrection": False},
                        {
                            "claim": "기사에 대해 반론·정정 보도를 청구할 수 있습니다.",
                            "isCorrection": True,
                        },
                    ],
                },
            },
        )

        result = assess_news_collection_admission(
            routine,
            {"newsCollectionQualityGateEnabled": "1"},
        )

        self.assertFalse(result.correction)
        self.assertFalse(result.passed)
        self.assertIn("materiality-below-policy", result.reason_codes)

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
