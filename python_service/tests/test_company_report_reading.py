import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.news_intelligence.domain.company_change_report import build_company_change_report, render_company_change_report
from digital_twin.modules.news_intelligence.domain.company_report_reading import financial_reading_cards, valuation_reading_cards
from digital_twin.modules.read_models.application.company_report_insight_query_service import CompanyReportInsightQueryService


def metric(key, value, period="2026-06-30", duration="quarterly", **extra):
    return {"key": key, "label": key, "value": value, "period": period,
            "durationBasis": duration, "scope": "CFS", "provider": "OpenDART", "currency": "KRW",
            "basisLabel": period + " · " + duration + " · 연결", "sourceDocumentId": "filing-1",
            "sourceReferences": [{"datasetId": "opendart.company_facts", "revisionId": "source-1"}], **extra}


def payload():
    return {"generatedAt": "2026-09-30T01:00:00Z", "snapshot": {"accountId": "a", "generatedAt": "2026-09-30T00:00:00Z"},
            "instrument": {"symbol": "TEST", "currency": "KRW", "currentPrice": 100},
            "companyReportEvidence": {"recentFinancials": [{"period": "2026-06-30", "metrics": [
                metric("revenue", 200), metric("operatingIncome", -20), metric("netIncome", 10),
                metric("operatingCashFlow", 30, duration="year-to-date"),
                metric("capitalExpenditure", -50, duration="year-to-date"),
            ]}], "annualFinancials": [{"period": "2025-12-31", "metrics": [
                metric("revenue", 100, "2025-12-31", "annual"), metric("operatingIncome", 20, "2025-12-31", "annual"),
            ]}]}, "investmentAnalysis": {}, "valuation": {}}


class CompanyReportReadingTests(unittest.TestCase):
    def test_recent_meaning_preserves_distinct_cash_flow_period_and_sources(self):
        report = build_company_change_report(payload())
        cards = report["reading"]["financial"]
        self.assertEqual(["operating-margin", "cash-after-investment", "earnings-basis"], [c["key"] for c in cards])
        self.assertIn("-10.00%", cards[0]["fact"])
        self.assertIn("매출보다 영업비용이 큽니다", cards[0]["meaning"])
        self.assertIn("year-to-date", cards[1]["fact"])
        self.assertIn("-20 KRW", cards[1]["fact"])
        self.assertTrue(all(c["evidence"] and c["nextChecks"] and c["limitations"] for c in cards))
        rendered = render_company_change_report(report)
        self.assertIn("2026-06-30", rendered)
        self.assertIn("영업적자", rendered)
        self.assertIn("차입·상환·배당", str(report["sections"]))
        self.assertIn("OpenDART", rendered)
        self.assertLess(len(rendered), 850)
        self.assertEqual(3, len(report["brief"]["sections"]))
        self.assertEqual(report["brief"], report["notificationContent"])
        self.assertNotIn("기업의 확정 가치", rendered)
        self.assertNotIn("2025-12-31", rendered)
        self.assertNotIn("매출 100당", rendered)
        self.assertIn("이 숫자의 의미", rendered)
        self.assertLess(report["sections"].index(next(s for s in report["sections"] if s["key"] == "businessMeaning")),
                        report["sections"].index(next(s for s in report["sections"] if s["key"] == "annualFinancials")))

    def test_mixed_sources_periods_units_and_missing_values_are_not_joined(self):
        for field, value in (("provider", "yfinance"), ("durationBasis", "annual"), ("currency", "USD"), ("scope", "OFS"), ("period", "2025-12-31"), ("value", None)):
            with self.subTest(field=field):
                operating = {**metric("operatingIncome", 20), field: value}
                evidence = {"recentFinancials": [{"metrics": [metric("revenue", 100), operating]}]}
                self.assertEqual([], financial_reading_cards(evidence))

    def test_margin_comparison_requires_same_previous_period_and_basis(self):
        revenue, operating = metric("revenue", 200), metric("operatingIncome", 20)
        for item, old in ((revenue, 100), (operating, 20)):
            item["comparison"] = {"previousValue": old, "previousPeriod": "2025-06-30",
                                  "currentValue": item["value"], "currentPeriod": item["period"],
                                  "previousSource": {**item, "period": "2025-06-30"}}
        evidence = {"recentFinancials": [{"metrics": [revenue, operating]}]}
        self.assertIn("-10.00%p", financial_reading_cards(evidence)[0]["fact"])
        operating["comparison"]["previousSource"]["provider"] = "other"
        self.assertNotIn("%p", financial_reading_cards(evidence)[0]["fact"])

    def test_reference_values_stay_hidden_and_reverse_conditions_remain_conditional(self):
        value = payload()
        value["valuation"] = {"model": {"id": "model"}, "fairValue": {"low": 777770, "base": 888880, "high": 999990},
                              "quality": {"decisionEligible": False},
                              "impliedExpectations": {"status": "solved", "impliedEbitMarginPct": 17.5,
                                  "assumptionReviewState": "required", "quoteAsOf": "2026-09-29T00:00:00Z",
                                  "fixedAssumptions": {"waccPct": 12.0}}}
        value["investmentAnalysis"]["valuationModels"] = [{"modelId": "model", "currency": "KRW", "fairValue": 888880, "referenceOnly": True}]
        rendered = render_company_change_report(build_company_change_report(value))
        self.assertNotIn("888,880", rendered)
        self.assertIn("17.50%", rendered)
        self.assertIn("실제 시장 기대를 관측한 값이 아닙니다", rendered)
        self.assertIn("가정 검토가 남은", rendered)
        value["investmentAnalysis"]["valuationModels"][0].update(decisionEligible=True, referenceOnly=False)
        value["valuation"]["quality"]["decisionEligible"] = True
        self.assertTrue(valuation_reading_cards(value)[0]["calculationEligible"])
        value["investmentAnalysis"]["modelAgreement"] = {"status": "conflict"}
        self.assertFalse(valuation_reading_cards(value)[0]["calculationEligible"])

    def test_schema_upgrade_and_internal_model_changes_have_distinct_meaning(self):
        value = payload()
        baseline = build_company_change_report(value)
        old = copy.deepcopy(baseline)
        old["contractVersion"] = "company-change-report-v2"
        old["material"].pop("reportContractVersion", None)
        upgraded = build_company_change_report(value, old)
        self.assertEqual("expanded", upgraded["reportKind"])
        self.assertEqual("unchanged", build_company_change_report(value, baseline)["reportKind"])
        value["investmentAnalysis"]["valuationModels"] = [{"modelId": "model", "reviewStatus": "pending"}]
        changed = build_company_change_report(value, baseline)
        self.assertIn("기업 수치·문서의 변경 없이", render_company_change_report(changed))
        value["instrument"]["currentPrice"] = 150
        self.assertEqual("unchanged", build_company_change_report(value, changed)["reportKind"])

    def test_valuation_driven_operating_loss_requires_same_official_filing(self):
        from digital_twin.infrastructure.external_signal_provider_sec import ExternalSignalSecMixin
        from digital_twin.modules.news_intelligence.domain.company_knowledge import build_company_knowledge
        from digital_twin.modules.news_intelligence.domain.company_report_evidence import build_company_report_evidence
        def fact(value):
            return {"units": {"USD": [{"val": value, "start": "2026-04-01", "end": "2026-06-30",
                "filed": "2026-07-30", "form": "10-Q", "fp": "Q2", "fy": 2026,
                "frame": "CY2026Q2", "accn": "0000000001-26-000001"}]}}
        source = {"facts": {"us-gaap": {"Revenues": fact(100), "OperatingIncomeLoss": fact(-6800),
                   "CryptoAssetUnrealizedGainLossOperating": fact(-6780)}}}
        company = build_company_knowledge("TEST", sec_filing={"provider": "SEC EDGAR", "cik": "0000000001",
            "facts": ExternalSignalSecMixin().sec_company_facts_summary(source)}, source_references=[{
                "datasetId": "sec.company_facts", "revisionId": "filing-revision", "subjectKey": "TEST"}])
        evidence = build_company_report_evidence("TEST", {"companyKnowledge": {"TEST": company}})
        value = payload()
        value["companyReportEvidence"] = evidence
        report = build_company_change_report(value)
        card = report["reading"]["financial"][0]
        self.assertEqual("digital-asset-remeasurement", card["driver"])
        self.assertIn("-20 USD", card["fact"])
        message = render_company_change_report(report)
        self.assertIn("디지털자산 평가손실", message)
        self.assertIn("같은 금액의 현금", message)
        self.assertIn("제외해도 영업손실이 남습니다", message)
        self.assertIn("부채·우선주", message)
        self.assertNotIn("매출 100당", message)
        self.assertNotIn("6800.00%", message)
        self.assertIn("정상화 이익이나 현금흐름이 아닙니다", str(card["limitations"]))
        metrics = evidence["recentFinancials"][0]["metrics"]
        adjustment = next(item for item in metrics if item["key"] == "cryptoAssetUnrealizedGainLossOperating")
        for field, other in (("official", False), ("sourceDocumentId", "other-filing"),
                             ("periodStart", "2026-01-01"), ("durationBasis", "year-to-date")):
            saved = adjustment[field]
            adjustment[field] = other
            self.assertNotEqual("digital-asset-remeasurement", financial_reading_cards(evidence)[0].get("driver"))
            adjustment[field] = saved
        adjustment.update(key="cryptoAssetUnrealizedLossOperating", value=6780)
        self.assertEqual("digital-asset-remeasurement", financial_reading_cards(evidence)[0].get("driver"))
        adjustment["value"] = -6780
        self.assertNotEqual("digital-asset-remeasurement", financial_reading_cards(evidence)[0].get("driver"))


class CompanyReportInsightTests(unittest.TestCase):
    def setUp(self):
        self.financial = {"decisionFingerprint": "financial-1", "report": {"sourceReferences": [
            {"datasetId": "opendart.company_facts", "revisionId": "revision-1"}]}}
        self.case = {"subjectCaseId": "case", "accountId": "a", "symbol": "TEST", "sourceAboxSnapshotId": "abox",
                     "inferenceGenerationId": "generation", "candidateSet": {"fingerprint": "candidate"}}
        self.episode = {**self.case, "candidateFingerprint": "candidate", "episodeId": "insight",
                        "aiAuthored": True, "publicationContractPassed": True, "createdAt": "2026-09-30T00:00:00Z",
                        "insight": {"financialEvidence": copy.deepcopy(self.financial), "insightAssessment": {
                            "publishable": True, "dominantThesis": "수익성 변화 확인", "causalMechanism": "비용 증가가 영업이익에 반영됐습니다.",
                            "investmentImplication": "이익률 가정을 다시 확인할 필요가 있습니다.",
                            "invalidationCondition": "다음 분기 영업이익률이 회복되는지 확인해야 합니다.", "risks": ["수요 둔화가 지속될 수 있습니다."], "evidenceIds": ["fact-1"]}}}
        self.reader = CompanyReportInsightQueryService(
            SimpleNamespace(latest=lambda *args: [self.case]),
            SimpleNamespace(latest_insight_episodes=lambda **kwargs: [self.episode]))

    def query(self):
        return self.reader.query("a", "TEST", self.financial, "2026-09-30T01:00:00Z")

    def test_current_validated_financial_basis_is_captured_without_action(self):
        result = self.query()
        self.assertEqual("available", result["state"])
        self.assertNotIn("action", result)
        value = payload()
        value["companyReportInsight"] = result
        report = build_company_change_report(value)
        self.assertIn(result["meaning"], str(report["sections"]))
        self.assertIn(result["meaning"], render_company_change_report(report))
        self.assertIn(result["invalidation"], render_company_change_report(report))
        self.assertEqual("linkedInsight", report["sections"][0]["key"])
        self.assertIn(result["risks"][0], render_company_change_report(report))
        self.episode["insight"]["insightAssessment"]["investmentImplication"] = "바뀐 설명"
        self.assertEqual(result["meaning"], report["reading"]["insight"]["meaning"])

    def test_wrong_scope_generation_publication_and_future_are_rejected(self):
        for key, value in (("accountId", "other"), ("symbol", "OTHER"), ("sourceAboxSnapshotId", "old"),
                           ("candidateFingerprint", "old"), ("inferenceGenerationId", "old"),
                           ("aiAuthored", False), ("publicationContractPassed", False),
                           ("publicationMode", "typedb-fallback"), ("createdAt", "2027-01-01T00:00:00Z")):
            with self.subTest(key=key):
                old = self.episode.get(key)
                self.episode[key] = value
                self.assertEqual("unavailable", self.query()["state"])
                self.episode[key] = old

    def test_changed_or_unversioned_financials_and_trade_directives_are_rejected(self):
        self.financial["report"]["sourceReferences"][0]["revisionId"] = "new"
        self.assertEqual("basis-mismatch", self.query()["state"])
        self.financial["report"]["sourceReferences"] = []
        self.assertEqual("basis-mismatch", self.query()["state"])
        self.financial = copy.deepcopy(self.episode["insight"]["financialEvidence"])
        self.episode["insight"]["insightAssessment"]["investmentImplication"] = "매수하세요"
        self.assertEqual("unavailable", self.query()["state"])

    def test_read_failure_preserves_explicit_unavailable_state(self):
        def fail(*args):
            raise RuntimeError("do not expose database internals")
        self.reader.subject_case_repository.latest = fail
        result = self.query()
        self.assertEqual("unavailable", result["state"])
        self.assertNotIn("database", str(result))


if __name__ == "__main__":
    unittest.main()
