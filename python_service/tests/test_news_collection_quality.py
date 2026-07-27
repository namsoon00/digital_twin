import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.news_ai_analysis_service import NewsAiAnalysisService
from digital_twin.application.news_collection_service import NewsCollectionRunner
from digital_twin.domain.data_pipeline_health import evaluate_news_collection_health
from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.domain.materiality import evidence_materiality
from digital_twin.domain.news_ai_analysis import article_text_parts, local_news_ai_analysis
from digital_twin.domain.news_analysis import article_analysis_facts, article_quality_gate
from digital_twin.infrastructure.external_signal_utils import ExternalCircuitOpen
from digital_twin.infrastructure import news_sources
from digital_twin.infrastructure.news_sources import NewsSourceGateway, article_metadata_from_html, extract_article_text


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
                },
            )

        retained = claim("research:AAPL:news:retained", "Reuters", "https://reuters.example.test/apple-buyback")
        current = claim("research:AAPL:news:current", "Bloomberg", "https://bloomberg.example.test/apple-buyback")

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


if __name__ == "__main__":
    unittest.main()
