import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.news_ai_analysis_service import NewsAiAnalysisService
from digital_twin.application.news_collection_service import NewsCollectionRunner
from digital_twin.domain.data_pipeline_health import evaluate_news_collection_health
from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.domain.materiality import evidence_materiality
from digital_twin.domain.news_ai_analysis import article_text_parts, local_news_ai_analysis
from digital_twin.domain.news_analysis import article_analysis_facts, article_quality_gate
from digital_twin.infrastructure.news_sources import NewsSourceGateway, extract_article_text


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


if __name__ == "__main__":
    unittest.main()
