import sys
import unittest
import copy
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.read_models.application.instrument_valuation_query_service import InstrumentValuationQueryService
from digital_twin.modules.news_intelligence.application.company_change_report_reconciliation_service import CompanyChangeReportReconciler
from digital_twin.modules.news_intelligence.domain.company_change_report import build_company_change_report, render_company_change_report
from digital_twin.modules.news_intelligence.domain.company_report_evidence import build_company_report_evidence
from digital_twin.modules.notifications.application.notification.rendering import NotificationRenderingService
from digital_twin.modules.news_intelligence.domain.company_knowledge import (
    build_company_knowledge,
    company_valuation_context,
)
from digital_twin.modules.portfolio.domain.instrument_valuation import InstrumentValuationQuery
from digital_twin.modules.portfolio.domain.portfolio import (
    AccountSnapshot,
    PortfolioSummary,
    Position,
    utc_now_iso,
)


class StubMonitorStore:
    def __init__(self, previous):
        self.previous = previous


class InstrumentValuationQueryTests(unittest.TestCase):
    def external_signals(self):
        stamp = utc_now_iso()
        stored = build_company_knowledge(
            "035720",
            overview={
                "provider": "yfinance",
                "fetchedAt": "2026-01-01T00:00:00Z",
                "peRatio": 7.0,
                "forwardPE": 18.0,
                "trailingEPS": 900.0,
            },
        )
        return {
            "companyKnowledge": {"035720": stored},
            "companyOverviews": {
                "035720": {
                    "provider": "KIS Open API",
                    "source": "KIS inquire-price",
                    "fetchedAt": stamp,
                    "peRatio": 31.13,
                    "forwardPE": 0.0,
                    "pbr": 1.35,
                    "trailingEPS": 1110.0,
                    "epsPeriod": "ttm",
                    "epsBasis": "diluted",
                    "accountingBasis": "K-IFRS",
                    "securityLine": "035720",
                    "currency": "KRW",
                    "multipleObservations": [
                        {
                            "observationId": f"kis:historical-per:{year}",
                            "issuer": "035720", "securityLine": "035720", "multipleMetric": "per",
                            "basis": "historical", "value": value, "earningsHorizon": "ttm",
                            "accountingBasis": "K-IFRS", "epsBasis": "diluted", "currency": "KRW",
                            "priceAsOf": f"{year}-12-31", "earningsAsOf": f"{year}-12-31",
                            "provider": "KIS Open API", "upstreamOrigin": "kis.historical-per",
                            "comparabilityState": "verified", "freshnessState": "historical-valid",
                        }
                        for year, value in ((2022, 20.0), (2023, 24.0), (2024, 28.0), (2025, 32.0))
                    ],
                }
            },
            "yfinanceData": {
                "035720": {
                    "provider": "yfinance",
                    "collectedAt": stamp,
                    "info": {
                        "forwardPE": 19.25,
                        "dividendYield": 0.21,
                    },
                }
            },
            "issuerIrDocuments": {
                "035720": {
                    "sourceUrl": "https://www.kakaocorp.com/ir/noticeList?lang=en",
                    "documentCount": 4,
                    "latestPublishedAt": "2026-08-07",
                    "documentUsePolicy": "reference-only-until-body-verified",
                }
            },
            "externalDataPlatform": {
                "fitness": {
                    "subjects": {
                        "035720": {
                            "purposes": {
                                "investor-relations": {
                                    "state": "fresh", "usable": True,
                                    "reason": "공식 IR 문서 목록이 최신입니다.",
                                    "availableDatasets": ["issuer.ir_documents"],
                                    "freshDatasets": ["issuer.ir_documents"],
                                }
                            }
                        }
                    }
                }
            },
        }

    def snapshot_state(self):
        snapshot = AccountSnapshot(
            account_id="default",
            account_label="테스트 계정",
            provider="test",
            mode="live",
            status="정상",
            generated_at=utc_now_iso(),
            portfolio=PortfolioSummary(
                total=10_000_000,
                invested=3_540_000,
                cash=6_460_000,
                markets=[],
                sectors=[],
                concentration=35.4,
            ),
            positions=[Position(
                symbol="035720",
                name="카카오",
                market="KR",
                currency="KRW",
                quantity=100,
                current_price=35_400,
                updated_at=utc_now_iso(),
                source="holding",
            )],
            external_signals=self.external_signals(),
        )
        return snapshot.to_monitor_state()

    def _assert_fresh_kis_per_wins_stored_value_and_zero_does_not_mask_forward_per(self):
        context = company_valuation_context(self.external_signals(), "035720")

        self.assertEqual("available", context["perStatus"])
        self.assertEqual(31.13, context["metrics"]["peRatio"])
        self.assertEqual(19.25, context["metrics"]["forwardPE"])
        self.assertEqual(1.35, context["metrics"]["pbr"])
        self.assertEqual(0.21, context["metrics"]["dividendYieldPct"])
        self.assertIn("KIS Open API", context["sourceProviders"])

    def _assert_query_exposes_market_multiples_and_auditable_fair_value_without_action(self):
        service = InstrumentValuationQueryService(
            monitor_store=StubMonitorStore({"default": self.snapshot_state()}),
            settings={"aiValuationAutoProposalEnabled": "1"},
        )

        payload = service.query(InstrumentValuationQuery("035720", "default"))

        self.assertEqual("ok", payload["status"])
        self.assertEqual("reference-only", payload["decisionRole"])
        self.assertEqual("holding", payload["instrument"]["scope"])
        self.assertEqual(31.13, payload["marketMetrics"]["currentPER"])
        self.assertEqual(19.25, payload["marketMetrics"]["forwardPER"])
        self.assertEqual(1110.0, payload["marketMetrics"]["trailingEPS"])
        self.assertEqual("ttm", payload["marketMetrics"]["trailingEPSPeriod"])
        self.assertGreater(payload["valuation"]["fairValue"]["base"], 0)
        self.assertTrue(payload["valuation"]["multipleBand"]["evidenceBacked"])
        self.assertEqual(4, payload["valuation"]["multipleBand"]["sampleCount"])
        self.assertFalse(payload["valuation"]["quality"]["decisionEligible"])
        self.assertTrue(payload["valuation"]["identity"]["valuationBundleId"])
        self.assertTrue(payload["valuation"]["identity"]["valuationAssessmentId"])
        self.assertIn(payload["valuation"]["inputSnapshot"]["reproducibilityState"], {"partial", "reproducible"})
        self.assertEqual("unresolved", payload["investmentAnalysis"]["priceExplanation"]["claimStrength"])
        self.assertFalse(payload["investmentAnalysis"]["customerMessageEligible"])
        self.assertEqual("baseline", payload["companyChangeReport"]["reportKind"])
        self.assertTrue(payload["companyChangeReport"]["deliveryEligible"])
        self.assertEqual("factual-reference", payload["companyChangeReport"]["reportRole"])
        self.assertIn("currentVerifiedFacts", payload["investmentAnalysis"])
        self.assertEqual([], payload["investmentAnalysis"]["newlyConfirmedFacts"])
        self.assertTrue(payload["investmentAnalysis"]["valuationModels"])
        self.assertEqual(
            "do-not-average-model-values",
            payload["investmentAnalysis"]["valuationModels"][0]["comparisonPolicy"],
        )
        self.assertIn(payload["valuation"]["modelAgreement"]["status"], {"comparable", "conflict", "insufficient-models"})
        self.assertIn("dataReadiness", payload["valuation"])
        self.assertEqual("fresh", payload["valuation"]["dataReadiness"]["investorRelations"]["status"])
        self.assertEqual(4, payload["valuation"]["dataReadiness"]["investorRelations"]["documentCount"])
        self.assertEqual(["issuer.ir_documents"], payload["valuation"]["dataReadiness"]["investorRelations"]["freshDatasets"])
        self.assertFalse(payload["valuation"]["dataReadiness"]["investorRelations"]["valuationInputEligible"])
        self.assertNotIn("official-ir-source-not-ready", payload["investmentAnalysis"]["nextChecks"])
        for action_key in ("action", "decision", "recommendedAction"):
            self.assertNotIn(action_key, payload)
            self.assertNotIn(action_key, payload["valuation"])

    def test_model_agreement_blocks_material_spread_without_averaging_values(self):
        agreement = InstrumentValuationQueryService._model_agreement([
            {"modelId": "earnings", "fairValue": 100, "currency": "USD", "decisionEligible": True},
            {"modelId": "dcf", "fairValue": 160, "currency": "USD", "decisionEligible": True},
        ])

        self.assertEqual("conflict", agreement["status"])
        self.assertGreater(agreement["spreadPct"], 30)
        self.assertIn("valuation-models-materially-disagree", agreement["blockingReasons"])
        self.assertFalse(agreement["decisionEligible"])
        self.assertNotIn("average", agreement)

    def test_release_audit_block_keeps_overall_data_readiness_limited(self):
        state = self.snapshot_state()
        state["externalSignals"]["driverDcfReadiness"] = {"035720": {
            "status": "ready-for-shadow",
            "releaseState": "blocked",
            "financialEvidence": {
                "status": "official-ready", "officialDecisionReady": True,
                "officialMetricCount": 12, "requiredMetricCount": 12,
            },
            "consensusEvidence": {"status": "validated", "currency": "KRW", "rows": []},
        }}
        payload = InstrumentValuationQueryService(
            monitor_store=StubMonitorStore({"default": state}), settings={},
        ).query(InstrumentValuationQuery("035720", "default"))

        self.assertEqual("limited", payload["valuation"]["dataReadiness"]["status"])

    def _assert_negative_eps_is_explained_as_non_meaningful_per(self):
        signals = {
            "companyKnowledge": {
                "LOSS": build_company_knowledge(
                    "LOSS",
                    overview={
                        "provider": "yfinance",
                        "fetchedAt": utc_now_iso(),
                        "peRatio": 0,
                        "trailingEPS": -2.5,
                    },
                )
            }
        }

        context = company_valuation_context(signals, "LOSS")

        self.assertEqual("not-meaningful-loss", context["perStatus"])
        self.assertEqual(-2.5, context["metrics"]["trailingEPS"])

    def _assert_query_does_not_expose_a_stale_positive_per_for_a_loss_company(self):
        state = self.snapshot_state()
        position = state["positions"].pop("035720")
        position.update({"symbol": "LOSS", "name": "적자기업"})
        state["positions"]["LOSS"] = position
        state["externalSignals"] = {
            "companyKnowledge": {
                "LOSS": build_company_knowledge(
                    "LOSS",
                    overview={
                        "provider": "yfinance",
                        "fetchedAt": "2026-01-01T00:00:00Z",
                        "peRatio": 12.0,
                        "trailingEPS": 1.0,
                    },
                )
            },
            "companyOverviews": {
                "LOSS": {
                    "provider": "KIS Open API",
                    "fetchedAt": utc_now_iso(),
                    "peRatio": 0,
                    "trailingEPS": -2.5,
                }
            },
        }
        service = InstrumentValuationQueryService(
            monitor_store=StubMonitorStore({"default": state}),
            settings={},
        )

        payload = service.query(InstrumentValuationQuery("LOSS", "default"))

        self.assertEqual("not-meaningful-loss", payload["marketMetrics"]["perStatus"])
        self.assertIsNone(payload["marketMetrics"]["currentPER"])

    def test_instrument_valuation_read_model_contract(self):
        self._assert_fresh_kis_per_wins_stored_value_and_zero_does_not_mask_forward_per()
        self._assert_query_exposes_market_multiples_and_auditable_fair_value_without_action()
        self._assert_negative_eps_is_explained_as_non_meaningful_per()
        self._assert_query_does_not_expose_a_stale_positive_per_for_a_loss_company()
        self._assert_company_change_report_and_reconciliation_contract()

    def _assert_company_change_report_and_reconciliation_contract(self):
        service = InstrumentValuationQueryService(
            monitor_store=StubMonitorStore({"default": self.snapshot_state()}), settings={},
        )
        payload = service.query(InstrumentValuationQuery("035720", "default"))
        baseline = payload["companyChangeReport"]
        self.assertEqual([], baseline["changes"]["factChanges"])
        self.assertEqual([], baseline["changes"]["valuationChanges"])
        self.assertNotIn("이번에 달라진 점", render_company_change_report(baseline))
        upgraded = build_company_change_report(payload, {**baseline, "contractVersion": "company-change-report-v1"})
        self.assertEqual("expanded", upgraded["reportKind"])
        self.assertFalse(upgraded["materialChange"])
        self._assert_company_report_evidence_contract(payload)
        quote_only = copy.deepcopy(payload)
        quote_only["instrument"]["currentPrice"] = 99_999
        unchanged = build_company_change_report(quote_only, baseline)
        self.assertEqual("unchanged", unchanged["reportKind"])
        self.assertFalse(unchanged["deliveryEligible"])

        factual = copy.deepcopy(payload)
        factual["investmentAnalysis"]["currentVerifiedFacts"] = [{
            "driverId": "revenue", "label": "매출", "value": 100,
            "unit": "KRW", "period": "FY2026", "evidenceId": "filing:revenue:2026",
        }]
        factual["investmentAnalysis"]["companyDrivers"] = {"materialFingerprint": "drivers:100"}
        factual_baseline = build_company_change_report(factual)
        changed_payload = copy.deepcopy(factual)
        changed_payload["investmentAnalysis"]["currentVerifiedFacts"][0]["value"] = 125
        changed_payload["investmentAnalysis"]["companyDrivers"]["materialFingerprint"] = "drivers:125"
        changed = build_company_change_report(changed_payload, factual_baseline)
        self.assertEqual("change", changed["reportKind"])
        self.assertEqual(100.0, changed["changes"]["factChanges"][0]["previous"]["value"])
        self.assertEqual(125.0, changed["changes"]["factChanges"][0]["current"]["value"])
        self.assertIn("이번에 달라진 점", render_company_change_report(changed))

        class Queue:
            def __init__(self):
                self.keys = set()
                self.jobs = []

            def enqueue(self, job):
                if job.dedupe_key in self.keys:
                    return False
                self.keys.add(job.dedupe_key)
                self.jobs.append(job)
                return True

        class Accounts:
            @staticmethod
            def load():
                return [SimpleNamespace(
                    account_id="default", label="테스트", enabled=True,
                    watchlist_symbols=("035720",),
                )]

        class Query:
            @staticmethod
            def query(_request):
                return payload

        queue = Queue()
        reconciler = CompanyChangeReportReconciler(
            account_repository=Accounts(),
            monitor_store=StubMonitorStore({"default": self.snapshot_state()}),
            valuation_query_service=Query(),
            queue=queue,
            settings={"companyChangeReportMaxSymbols": "2"},
        )
        self.assertEqual(1, reconciler.run_once()["queued"])
        self.assertEqual(0, reconciler.run_once()["queued"])
        self.assertEqual("informationUpdate", queue.jobs[0].message_type)
        self.assertEqual("company-change-report", queue.jobs[0].context["notificationContent"]["kind"])
        self.assertNotIn("body", queue.jobs[0].context["notificationContent"])
        queue.jobs[0].context["notifyLinkUrl"] = "https://reports.example.test/"
        message = NotificationRenderingService().render(queue.jobs[0])
        self.assertIn("📑 기업 보고서", message)
        self.assertIn("상세 기업 보고서", message)
        self.assertIn("detailKey=" + queue.jobs[0].job_id, message)
        self.assertNotIn("&lt;b&gt;", message)
        self.assertEqual(1, message.count(baseline["summary"]))
        from digital_twin.infrastructure.web.adapters.notification_presentation import notification_job_public_payload
        settings = {"notificationProcessingStaleMinutes": 2}
        summary_payload = notification_job_public_payload(queue.jobs[0], settings=settings, include_customer_document=True)
        self.assertEqual(baseline["reportId"], summary_payload["companyChangeReport"]["reportId"])
        self.assertNotIn("companyChangeReport", notification_job_public_payload(queue.jobs[0], settings=settings))

        class RotationQueue(Queue):
            def recent_for_symbol(self, symbol, account_id="", limit=20):
                return [
                    job for job in reversed(self.jobs)
                    if job.context.get("symbol") == symbol and (not account_id or job.account_id == account_id)
                ][:limit]

        class RotationQuery:
            @staticmethod
            def query(request):
                rotated = copy.deepcopy(payload)
                report = rotated["companyChangeReport"]
                report["symbol"] = request.symbol
                report["name"] = "회사 " + request.symbol
                report["reportId"] = "company-change-report:" + request.symbol
                report["materialFingerprint"] = "fingerprint:" + request.symbol
                return rotated

        from datetime import datetime, timedelta, timezone
        clock = [datetime(1970, 1, 1, tzinfo=timezone.utc)]
        rotation_queue = RotationQueue()
        rotation_state = {"positions": [{"symbol": symbol} for symbol in ("A", "B", "C", "D")], "watchlist": []}
        class RotationAccounts:
            @staticmethod
            def load():
                return [type("Account", (), {"account_id": "default", "enabled": True, "watchlist_symbols": ()})()]

        rotating = CompanyChangeReportReconciler(
            account_repository=RotationAccounts(), monitor_store=StubMonitorStore({"default": rotation_state}),
            valuation_query_service=RotationQuery(), queue=rotation_queue,
            settings={"companyChangeReportBatchSize": "2", "companyChangeReportRotationSeconds": "60"},
            now_provider=lambda: clock[0],
        )
        first = rotating.run_once()
        self.assertEqual(["A", "B"], first["selectedSymbols"])
        self.assertEqual(4, first["universeCount"])
        self.assertEqual(4, first["withoutReportBeforeRun"])
        for job in rotation_queue.jobs:
            job.status = "done"
        second = rotating.run_once()
        self.assertEqual(["C", "D"], second["selectedSymbols"])
        for job in rotation_queue.jobs:
            job.status = "done"
        third = rotating.run_once()
        self.assertEqual(["A", "B"], third["selectedSymbols"])
        clock[0] += timedelta(seconds=60)
        fourth = rotating.run_once()
        self.assertEqual(["C", "D"], fourth["selectedSymbols"])

    def _assert_company_report_evidence_contract(self, payload):
        source = {"provider": "OpenDART", "currency": "KRW", "period": "2025-12-31",
                  "durationBasis": "annual", "scope": "CFS", "official": True,
                  "sourceUrl": "https://dart.fss.or.kr/test?rcpNo=123", "receiptNo": "123"}
        reference = {"datasetId": "opendart.company_facts", "revisionId": "source-1"}
        row = {"period": "2025-12-31", "frequency": "annual", "provider": "OpenDART",
               "financialReportingVersion": "financial-reporting-v2", "revenue": 200, "operatingIncome": 40,
               "metricProvenance": {"revenue": source, "operatingIncome": source},
               "reportContract": {"contractVersion": "financial-report-observation-v1", "revisionState": "immutable-source-bound",
                                  "periodEnd": "2025-12-31", "frequency": "annual", "durationBases": ["annual"],
                                  "provider": "OpenDART", "observationId": "filing-123", "sourceReferences": [reference]},
               "comparisonEvidence": {"revenueGrowthPct": {"status": "verified-comparable", "basis": "year-over-year",
                                      "currentPeriod": "2025-12-31", "previousPeriod": "2024-12-31", "currentValue": 200,
                                      "previousValue": 100, "changePct": 100, "source": source,
                                      "previousSource": {**source, "period": "2024-12-31"}}}}
        signals = {"companyKnowledge": {"035720": {"profile": {"companyName": "회사 <테스트>"}, "financials": {"annual": [row, {"period": "2024-12-31", "revenue": 100}]}}},
                   "issuerIrDocuments": {"035720": {"items": [
                       {"documentId": "ir-1", "title": "실적 <발표>", "publishedAt": "2026-01-20", "url": "https://example.test/ir",
                        "documentVerified": True, "officialDocumentText": "회사 발표문 <script>내용</script>", "documentHash": "hash-1"},
                       {"documentId": "ir-2", "title": "미검증 문서", "url": "javascript:alert(1)", "officialDocumentText": "인용하면 안 되는 본문"},
                   ]}}}
        evidence = build_company_report_evidence("035720", signals)
        self.assertEqual("partial", evidence["coverage"]["state"])
        self.assertEqual("부분 확보", evidence["coverage"]["label"])
        self.assertEqual("preparing", build_company_report_evidence("UNKNOWN", {})["coverage"]["state"])
        self.assertEqual(1, evidence["coverage"]["annualPeriods"])
        self.assertEqual(100, evidence["annualFinancials"][0]["metrics"][0]["comparison"]["changePct"])
        self.assertEqual("", evidence["documents"][1]["url"])
        self.assertEqual("", evidence["documents"][1]["excerpt"])
        enriched = copy.deepcopy(payload)
        enriched["companyReportEvidence"] = evidence
        enriched["snapshot"]["generatedAt"] = "2026-09-27T22:19:13Z"
        enriched["investmentAnalysis"]["nextChecks"] = ["fy1-revenue-consensus-growth-outlier", "untranslated-internal-code"]
        enriched["investmentAnalysis"]["valuationModels"] = [{"modelId": "semiconductor-cycle-earnings", "fairValue": 1793019.9,
                                                                  "referenceOnly": True, "currency": "KRW", "reviewStatus": "ai_applied_pending_review"}]
        report = build_company_change_report(enriched)
        rendered = render_company_change_report(report)
        self.assertIn("2026-09-28 07:19 KST", report["sourceCutoffDisplay"])
        self.assertIn("자료 확보 수준: 부분 확보", str(report["sections"]))
        self.assertIn("실적 <발표>", str(report["sections"]))
        self.assertNotIn("실적 &lt;발표&gt;", rendered)
        self.assertNotIn("1,793,020", rendered)
        self.assertNotIn("fy1-revenue-consensus-growth-outlier", rendered)
        self.assertNotIn("untranslated-internal-code", rendered)
        self.assertTrue(any("20.00%" in line for section in report["sections"] for line in section.get("rows", [])))
        refreshed = copy.deepcopy(enriched)
        refreshed["companyReportEvidence"]["annualFinancials"][0]["metrics"][0]["sourceReferences"][0]["revisionId"] = "polled-again"
        self.assertEqual("unchanged", build_company_change_report(refreshed, report)["reportKind"])
        refreshed["companyReportEvidence"]["annualFinancials"][0]["metrics"][0]["value"] = 220
        updated = build_company_change_report(refreshed, report)
        self.assertEqual("change", updated["reportKind"])
        self.assertTrue(updated["changes"]["evidenceChanges"])
        mismatched = copy.deepcopy(signals)
        mismatched["companyKnowledge"]["035720"]["financials"]["annual"][0]["comparisonEvidence"]["revenueGrowthPct"]["currentValue"] = 999
        metric = build_company_report_evidence("035720", mismatched)["annualFinancials"][0]["metrics"][0]
        self.assertNotIn("comparison", metric)
        primary_alternative = copy.deepcopy(signals)
        alternate = copy.deepcopy(row)
        alternate["operatingCashFlow"] = 30
        alternate["metricProvenance"]["operatingCashFlow"] = source
        primary_alternative["companyKnowledge"]["035720"]["valuationFinancialCandidates"] = [alternate]
        expanded_evidence = build_company_report_evidence("035720", primary_alternative)
        self.assertEqual(1, len(expanded_evidence["annualFinancials"]))
        self.assertTrue(any(item["key"] == "operatingCashFlow" for item in expanded_evidence["annualFinancials"][0]["metrics"]))
        mixed = copy.deepcopy(signals)
        mixed["companyKnowledge"]["035720"]["financials"]["annual"][0]["metricProvenance"]["operatingIncome"] = {**source, "durationBasis": "year-to-date", "provider": "yfinance", "official": False}
        mixed_report = build_company_change_report({**payload, "companyReportEvidence": build_company_report_evidence("035720", mixed)})
        self.assertFalse(any("영업이익률" in line for section in mixed_report["sections"] if section["key"] == "financialReading" for line in section.get("rows", [])))

    def test_trailing_eps_never_inherits_forecast_period(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        original = InstrumentValuationQueryService(
            monitor_store=StubMonitorStore({"default": self.snapshot_state()}), settings={},
        )
        forecast = SimpleNamespace(
            rows=[{"epsScenario": {"period": "forward-12m"}}],
            status="reference-only", model_service_version="test",
        )
        with patch.object(original.valuation_service, "evaluate", return_value=forecast):
            payload = original.query(InstrumentValuationQuery("035720", "default"))
        self.assertEqual("ttm", payload["marketMetrics"]["trailingEPSPeriod"])
        implied = original._implied_expectations([{
            "valuationModelFamily": "driver-dcf",
            "valuationAssessmentId": "valuation-assessment:one",
            "impliedExpectations": {
                "status": "solved", "solverId": "reverse-dcf:one",
                "impliedRevenueGrowthPct": 12.5, "independentEvidence": False,
            },
        }], "035720")
        self.assertEqual("reverse-dcf:one", implied["solverId"])
        self.assertEqual("valuation-assessment:one", implied["valuationAssessmentId"])
        self.assertFalse(implied["independentEvidence"])


if __name__ == "__main__":
    unittest.main()
