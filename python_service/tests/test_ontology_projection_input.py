import json
import unittest
from copy import deepcopy

from digital_twin.domain.investment_research import research_evidence_from_external_signals
from digital_twin.domain.ontology_projection_input import (
    compact_external_signals_for_ontology,
    compact_monitor_state_for_reasoning_base,
    compact_monitor_state_for_reasoning_symbol,
    compact_monitor_state_for_ontology,
    projection_input_summary,
    reasoning_snapshot_symbols,
    temporal_research_signals_for_symbol,
)
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder


class OntologyProjectionInputTests(unittest.TestCase):

    def test_existing_company_knowledge_keeps_structured_periods(self):
        source = {
            "companyKnowledge": {
                "AAPL": {
                    "schemaVersion": "company-knowledge-v1",
                    "symbol": "AAPL",
                    "companyName": "Apple Inc.",
                    "factRevision": "revision-one",
                    "financials": {
                        "annual": [{"period": "2025-09-30", "revenueGrowthPct": 5.0}],
                        "quarterly": [{"period": "2026-06-30", "revenueGrowthPct": 8.0}],
                    },
                    "valuation": {"peRatio": 25.0, "returnOnEquityPct": 32.0},
                    "provenance": [{"provider": "SEC EDGAR", "scope": "official-filing"}],
                    "coverage": {"dataState": "sufficient", "financialPeriods": 2},
                },
            },
        }

        compact = compact_external_signals_for_ontology(source, target_symbols=["AAPL"])

        company = compact["companyKnowledge"]["AAPL"]
        self.assertEqual(5.0, company["financials"]["annual"][0]["revenueGrowthPct"])
        self.assertEqual(8.0, company["financials"]["quarterly"][0]["revenueGrowthPct"])
        self.assertEqual("sufficient", company["coverage"]["dataState"])

    def source_signals(self):
        return {
            "macro": {
                "series": {
                    "DGS10": {
                        "value": 4.5,
                        "deltaBp": 3,
                        "date": "2026-07-29",
                        "provider": "FRED",
                    },
                },
                "yieldSpread10y2y": 0.3,
            },
            "fxRates": {
                "USDKRW": {
                    "base": "USD",
                    "quote": "KRW",
                    "rate": 1400,
                    "deltaPct": 0.2,
                    "provider": "Alpha Vantage",
                },
            },
            "equityQuotes": {
                "AAPL": {
                    "price": 200,
                    "changePercent": 1.5,
                    "volume": 1000,
                    "latestTradingDay": "2026-07-29",
                    "provider": "Alpha Vantage",
                },
                "NVDA": {"price": 180, "changePercent": -1.2, "volume": 2000},
            },
            "yfinanceData": {
                "AAPL": {
                    "provider": "yfinance",
                    "querySymbol": "AAPL",
                    "quote": {"price": 200, "volume": 1000},
                    "info": {"longName": "Apple Inc.", "forwardPE": 28},
                    "analystPriceTargets": {"mean": 230},
                    "optionChains": [{"expiration": "2026-08-21", "summary": {"callVolume": 10}}],
                    "history": [{"close": 100, "raw": "x" * 4000} for _ in range(40)],
                    "incomeStatement": [{"raw": "x" * 3000} for _ in range(10)],
                    "balanceSheet": [{"raw": "x" * 3000} for _ in range(10)],
                    "cashFlow": [{"raw": "x" * 3000} for _ in range(10)],
                },
            },
            "researchEvidence": {
                "AAPL": [{
                    "evidenceId": "research:aapl:1",
                    "symbol": "AAPL",
                    "kind": "news",
                    "source": "Reuters",
                    "title": "Apple reports demand update",
                    "url": "https://example.test/apple",
                    "summary": "A concise source summary",
                    "polarity": "support",
                    "publishedAt": "2026-07-29T01:00:00Z",
                    "payload": {
                        "relationScope": "direct",
                        "eventType": "earnings",
                        "articleText": "body " * 80000,
                        "articleTextPreview": "preview " * 400,
                        "articleSummaryKo": "본문에서 확인한 핵심 사실",
                        "aiAnalysis": {
                            "status": "ok",
                            "version": "v1",
                            "readScope": "body",
                            "relationScope": "direct",
                            "impactPolarity": "support",
                            "summary": {
                                "oneLineKo": "핵심 요약",
                                "briefKo": "투자자가 확인할 내용",
                                "watchPoints": ["다음 실적"],
                            },
                        },
                        "claimLedger": {
                            "claims": [{
                                "claimId": "claim:aapl:1",
                                "statement": "매출 증가를 발표했다",
                                "verificationStatus": "verified",
                                "investmentJudgmentEligible": True,
                            }],
                        },
                        "evidenceGovernance": {
                            "investmentJudgmentEligible": True,
                            "verificationStatus": "verified",
                        },
                    },
                }],
                "NVDA": [{
                    "evidenceId": "research:nvda:1",
                    "symbol": "NVDA",
                    "kind": "news",
                    "title": "NVIDIA update",
                    "payload": {"relationScope": "direct", "articleText": "body " * 1000},
                }],
            },
            "newsHeadlines": {
                "AAPL": {
                    "provider": "Google News",
                    "items": [{
                        "title": "Apple reports demand update",
                        "url": "https://example.test/apple",
                        "summary": "headline summary",
                        "payload": {"relationScope": "direct", "articleText": "body " * 10000},
                    }],
                },
            },
            "quality": {"dataState": "sufficient", "symbolCoverage": {"requested": 2, "covered": 2}},
            "freshness": {"status": "fresh", "ageMinutes": 1},
        }

    def test_projection_input_keeps_rule_facts_but_drops_provider_archives(self):
        source = self.source_signals()
        original = deepcopy(source)

        compact = compact_external_signals_for_ontology(source, target_symbols=["AAPL"])

        payload = compact["researchEvidence"]["AAPL"][0]["payload"]
        self.assertEqual("direct", payload["relationScope"])
        self.assertEqual("earnings", payload["eventType"])
        self.assertEqual("핵심 요약", payload["aiAnalysis"]["summary"]["oneLineKo"])
        self.assertEqual("claim:aapl:1", payload["claimLedger"]["claims"][0]["claimId"])
        self.assertNotIn("articleText", payload)
        self.assertLess(len(payload["articleTextPreview"]), 1300)
        self.assertNotIn("history", compact["yfinanceData"]["AAPL"])
        self.assertEqual(10, compact["yfinanceData"]["AAPL"]["statementMetricCounts"]["incomeStatement"])
        self.assertEqual("company-knowledge-v1", compact["companyKnowledge"]["AAPL"]["schemaVersion"])
        self.assertEqual("Apple Inc.", compact["companyKnowledge"]["AAPL"]["companyName"])
        self.assertEqual(original, source)

        summary = projection_input_summary(source, compact, target_symbols=["AAPL"])
        self.assertGreater(summary["sourceExternalSignalBytes"], 100000)
        self.assertGreater(summary["reducedExternalSignalBytes"], 100000)
        self.assertGreater(summary["reductionPct"], 90)

    def test_target_projection_keeps_shared_macro_and_selected_symbol_inputs_only(self):
        compact = compact_external_signals_for_ontology(self.source_signals(), target_symbols=["AAPL"])

        self.assertIn("macro", compact)
        self.assertIn("fxRates", compact)
        self.assertEqual({"AAPL"}, set(compact["equityQuotes"]))
        self.assertEqual({"AAPL"}, set(compact["researchEvidence"]))
        self.assertNotIn("NVDA", compact["researchEvidence"])

    def test_discarded_body_change_does_not_change_projection_but_factual_change_does(self):
        first = self.source_signals()
        body_only = deepcopy(first)
        body_only["researchEvidence"]["AAPL"][0]["payload"]["articleText"] = "different body " * 90000
        price_changed = deepcopy(first)
        price_changed["equityQuotes"]["AAPL"]["price"] = 201

        first_compact = compact_external_signals_for_ontology(first, target_symbols=["AAPL"])
        body_compact = compact_external_signals_for_ontology(body_only, target_symbols=["AAPL"])
        changed_compact = compact_external_signals_for_ontology(price_changed, target_symbols=["AAPL"])

        self.assertEqual(
            json.dumps(first_compact, ensure_ascii=False, sort_keys=True),
            json.dumps(body_compact, ensure_ascii=False, sort_keys=True),
        )
        self.assertNotEqual(
            json.dumps(first_compact, ensure_ascii=False, sort_keys=True),
            json.dumps(changed_compact, ensure_ascii=False, sort_keys=True),
        )

    def test_temporal_projection_retains_research_events_without_global_provider_payload(self):
        temporal = temporal_research_signals_for_symbol(self.source_signals(), "AAPL")

        self.assertEqual({"researchEvidence", "newsHeadlines", "equityQuotes"}, set(temporal))
        evidence = research_evidence_from_external_signals("AAPL", temporal)
        self.assertTrue(any(item.evidence_id == "research:aapl:1" for item in evidence))
        self.assertNotIn("yfinanceData", temporal)

    def test_runtime_history_compacts_external_signal_archives_without_mutating_history(self):
        source = self.source_signals()
        metadata = {
            "monitorStateHistory": [{
                "generatedAt": "2026-07-29T01:00:00Z",
                "positions": {"AAPL": {"currentPrice": 200}},
                "externalSignals": source,
            }],
        }
        original = deepcopy(metadata)

        compact_metadata = PortfolioOntologyProjectionRecorder.factual_runtime_metadata(
            metadata,
            target_symbols=["AAPL"],
        )

        history_signals = compact_metadata["monitorStateHistory"][0]["externalSignals"]
        self.assertNotIn("NVDA", history_signals["researchEvidence"])
        self.assertNotIn("articleText", history_signals["researchEvidence"]["AAPL"][0]["payload"])
        self.assertEqual(original, metadata)

    def test_monitor_history_projection_excludes_decisions_and_provider_archives(self):
        source = self.source_signals()
        state = {
            "generatedAt": "2026-07-29T01:00:00Z",
            "portfolio": {"totalValue": 1000},
            "positions": {"AAPL": {"currentPrice": 200}},
            "watchlist": {"NVDA": {"currentPrice": 180}},
            "decisions": {"AAPL": {"decision": "매수"}},
            "externalSignals": source,
        }

        projected = compact_monitor_state_for_ontology(state, target_symbols=["AAPL"])

        self.assertEqual("2026-07-29T01:00:00Z", projected["generatedAt"])
        self.assertIn("AAPL", projected["positions"])
        self.assertNotIn("decisions", projected)
        self.assertEqual({"AAPL"}, set(projected["externalSignals"]["researchEvidence"]))
        self.assertNotIn("articleText", projected["externalSignals"]["researchEvidence"]["AAPL"][0]["payload"])

    def test_monitor_history_projection_keeps_only_temporal_article_facts(self):
        state = {
            "generatedAt": "2026-07-29T01:00:00Z",
            "positions": {"AAPL": {"currentPrice": 200}},
            "externalSignals": self.source_signals(),
        }

        projected = compact_monitor_state_for_ontology(state, target_symbols=["AAPL"])
        evidence_row = projected["externalSignals"]["researchEvidence"]["AAPL"][0]
        parsed = research_evidence_from_external_signals("AAPL", projected["externalSignals"])

        self.assertEqual("research:aapl:1", evidence_row["evidenceId"])
        self.assertEqual("direct", evidence_row["payload"]["relationScope"])
        self.assertNotIn("summary", evidence_row)
        self.assertNotIn("aiAnalysis", evidence_row["payload"])
        self.assertTrue(any(item.evidence_id == "research:aapl:1" for item in parsed))
        self.assertLess(len(json.dumps(projected, ensure_ascii=False)), 20000)

    def test_reasoning_snapshot_input_splits_shared_and_selected_symbol_facts(self):
        state = {
            "accountId": "main",
            "accountLabel": "메인",
            "provider": "toss",
            "mode": "live",
            "status": "ok",
            "generatedAt": "2026-07-29T01:00:00Z",
            "portfolio": {"totalValue": 1000},
            "positions": {"AAPL": {"currentPrice": 200}},
            "watchlist": {"NVDA": {"currentPrice": 180}},
            "metadata": {
                "marketProxyQuotes": {"SPY": {"currentPrice": 600}},
                "ontology": {"projection": {"large": "ignore"}, "inferenceMissingState": {"missing": True}},
            },
            "externalSignals": self.source_signals(),
        }

        base = compact_monitor_state_for_reasoning_base(state)
        aapl = compact_monitor_state_for_reasoning_symbol(state, "AAPL")

        self.assertEqual({"AAPL", "NVDA"}, reasoning_snapshot_symbols(state))
        self.assertIn("macro", base["externalSignals"])
        self.assertNotIn("equityQuotes", base["externalSignals"])
        self.assertEqual(600, base["metadata"]["marketProxyQuotes"]["SPY"]["currentPrice"])
        self.assertEqual(True, base["metadata"]["ontology"]["inferenceMissingState"]["missing"])
        self.assertNotIn("projection", base["metadata"]["ontology"])
        self.assertEqual({"AAPL"}, set(aapl["externalSignals"]["equityQuotes"]))
        self.assertNotIn("articleText", aapl["externalSignals"]["researchEvidence"]["AAPL"][0]["payload"])


if __name__ == "__main__":
    unittest.main()
