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
    def test_changed_cash_and_debt_drive_message_while_enrichment_stays_quiet(self):
        value = payload()
        metrics = value["companyReportEvidence"]["recentFinancials"][0]["metrics"]
        metrics.append(metric("totalDebt", 100, duration="instant"))
        baseline = build_company_change_report(value)
        self.assertFalse(baseline["deliveryEligible"])
        for key, changed, phrase in (("operatingCashFlow", -30, "순유입에서 순유출"),
                                     ("totalDebt", 200, "totalDebt 증가"),
                                     ("operatingIncome", -40, "손실 확대")):
            candidate = copy.deepcopy(value)
            item = next(m for m in candidate["companyReportEvidence"]["recentFinancials"][0]["metrics"] if m["key"] == key)
            item["value"] = changed
            report = build_company_change_report(candidate, baseline)
            self.assertTrue(report["deliveryEligible"])
            self.assertIn(phrase, render_company_change_report(report))
            self.assertNotEqual(baseline["brief"], report["brief"])
            self.assertTrue(report["deliveryPolicy"]["changes"][0]["evidence"])
        for change in ("source", "small", "document", "upgrade"):
            candidate = copy.deepcopy(value)
            prior = copy.deepcopy(baseline)
            if change == "source":
                for m in candidate["companyReportEvidence"]["recentFinancials"][0]["metrics"]:
                    m["sourceDocumentId"] = "new-source-same-numbers"
            elif change == "small":
                candidate["companyReportEvidence"]["recentFinancials"][0]["metrics"][0]["value"] = 201
            elif change == "document":
                candidate["companyReportEvidence"]["documents"] = [{"documentId": "new", "title": "문서 추가"}]
            else:
                prior["contractVersion"] = "company-change-report-v2"
            self.assertFalse(build_company_change_report(candidate, prior)["deliveryEligible"])
        candidate = copy.deepcopy(value)
        candidate["companyReportEvidence"]["recentFinancials"][0]["metrics"][-1].update(value=200, sourceMetric="LongTermDebtCurrent")
        self.assertFalse(build_company_change_report(candidate, baseline)["deliveryEligible"])
        candidate = copy.deepcopy(value)
        candidate["companyReportEvidence"]["recentFinancials"][0]["metrics"][1]["value"] = 0
        self.assertIn("손익분기", render_company_change_report(build_company_change_report(candidate, baseline)))

    def test_new_official_period_and_same_period_correction_are_not_conflated(self):
        value = payload()
        baseline = build_company_change_report(value)
        recent = value["companyReportEvidence"]["recentFinancials"][0]
        recent["period"] = "2026-09-30"
        for m in recent["metrics"]:
            m.update(period="2026-09-30", official=True, sourceDocumentId="new-quarter", basisLabel="2026-09-30 · 단일 분기")
        report = build_company_change_report(value, baseline)
        self.assertTrue(report["deliveryEligible"])
        self.assertIn("비교 가능한 전년 동기", render_company_change_report(report))
        self.assertNotIn("수치 정정", render_company_change_report(report))
        for m in recent["metrics"]:
            m["comparison"] = {"basis": "year-over-year", "previousValue": m["value"],
                               "previousPeriod": "2025-09-30", "previousSource": {**m, "period": "2025-09-30"}}
        same = build_company_change_report(value, baseline)
        self.assertTrue(same["deliveryEligible"])
        self.assertIn("전년 동기와 동일", render_company_change_report(same))
        recent["metrics"][0]["value"] += 1
        self.assertIn("revenue 증가", render_company_change_report(build_company_change_report(value, baseline)))
        for m in recent["metrics"]:
            m["official"] = False
        self.assertFalse(build_company_change_report(value, baseline)["deliveryEligible"])
        value = payload()
        m = value["companyReportEvidence"]["recentFinancials"][0]["metrics"][1]
        m.update(value=-40, periodStart="different-start")
        self.assertFalse(build_company_change_report(value, baseline)["deliveryEligible"])

    def test_quiet_baseline_coalescing_receipt_cooldown_and_restart(self):
        from contextlib import nullcontext
        from datetime import datetime, timedelta, timezone
        from digital_twin.modules.news_intelligence.application.company_change_report_reconciliation_service import CompanyChangeReportReconciler
        class State:
            value = {}
            def load(self): return copy.deepcopy(self.value)
            def replace(self, value): self.value = copy.deepcopy(value)
        class States:
            def __init__(self): self.values = {}
            def subject(self, account, symbol): return nullcontext(self.values.setdefault((account, symbol), State()))
        class Queue:
            def __init__(self): self.jobs = []
            def recent_company_reports(self, symbol, account_id, limit):
                return [job for job in reversed(self.jobs) if job.account_id == account_id and job.context["symbol"] == symbol][:limit]
            def get(self, job_id): return next((j for j in self.jobs if j.job_id == job_id), None)
            def enqueue(self, job):
                if any(j.dedupe_key == job.dedupe_key for j in self.jobs): return False
                self.jobs.append(job)
                return True
            def mark_suppressed(self, job, reason): job.status = "suppressed"
        now = [datetime(2026, 9, 30, tzinfo=timezone.utc)]
        value, state, queue = payload(), States(), Queue()
        account = SimpleNamespace(account_id="a", label="test", enabled=True, watchlist_symbols=())
        def runner():
            return CompanyChangeReportReconciler(account_repository=SimpleNamespace(load=lambda:[account]),
                monitor_store=SimpleNamespace(previous={"a":{"positions":[{"symbol":"TEST"}]}}),
                valuation_query_service=SimpleNamespace(query=lambda request:copy.deepcopy(value)),
                queue=queue, state_store=state, settings={}, now_provider=lambda:now[0])
        self.assertEqual(0, runner().run_once()["queued"])
        cash = value["companyReportEvidence"]["recentFinancials"][0]["metrics"][3]
        cash["value"] = -30
        self.assertEqual(0, runner().run_once()["queued"])
        now[0] += timedelta(minutes=29)
        cash["value"] = -60
        self.assertEqual(0, runner().run_once()["queued"])
        now[0] += timedelta(minutes=1)
        self.assertEqual(1, runner().run_once()["queued"])
        self.assertIn("-60 KRW", queue.jobs[0].text)
        self.assertEqual(0, runner().run_once()["queued"])
        # A fresh service instance reads durable state. Only a successful receipt
        # advances the baseline/cooldown; queued status never counts as delivery.
        queue.jobs[0].status = "done"
        queue.jobs[0].updated_at = now[0].isoformat()
        runner().run_once()
        cash["value"] = 30
        runner().run_once()
        now[0] += timedelta(hours=23, minutes=59)
        self.assertEqual(0, runner().run_once()["queued"])
        now[0] += timedelta(minutes=1)
        self.assertEqual(1, runner().run_once()["queued"])
        self.assertIn("순유출에서 순유입", queue.jobs[-1].text)
        self.assertNotEqual(queue.jobs[0].dedupe_key, queue.jobs[1].dedupe_key)
        queue.jobs[1].status = "done"
        queue.jobs[1].updated_at = now[0].isoformat()
        runner().run_once()
        cash["value"] = -60
        runner().run_once()
        now[0] += timedelta(hours=24)
        self.assertEqual(1, runner().run_once()["queued"])
        self.assertNotEqual(queue.jobs[0].dedupe_key, queue.jobs[2].dedupe_key)
        from digital_twin.modules.notifications.application.notification.workflow import NotificationQueueRunner
        legacy = copy.deepcopy(queue.jobs[-1])
        legacy.context.pop("companyReportDelivery")
        self.assertFalse(NotificationQueueRunner.apply_deferred_admission_delivery_gate(SimpleNamespace(queue=queue), legacy))
        self.assertEqual("suppressed", legacy.status)
        self.assertTrue(NotificationQueueRunner.apply_deferred_admission_delivery_gate(SimpleNamespace(queue=queue), queue.jobs[-1]))
        # A cold start without any financial data must not stay uncomparable.
        value, state, queue = payload(), States(), Queue()
        available = value.pop("companyReportEvidence")
        self.assertEqual(0, runner().run_once()["queued"])
        value["companyReportEvidence"] = available
        self.assertEqual(0, runner().run_once()["queued"])
        state.values[("a", "TEST")].value["baseline"]["contractVersion"] = "old-contract"
        self.assertEqual(0, runner().run_once()["queued"])
        available["recentFinancials"][0]["metrics"][3]["value"] = -30
        self.assertEqual(0, runner().run_once()["queued"])
        now[0] += timedelta(minutes=30)
        self.assertEqual(1, runner().run_once()["queued"])

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
        for field, value in (("provider", "yfinance"), ("durationBasis", "annual"), ("currency", "USD"), ("scope", "OFS"), ("period", "2025-12-31"), ("periodStart", "2026-01-01"), ("sourceDocumentId", "other-filing"), ("value", None)):
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
        self.assertNotIn("17.50%", rendered)
        self.assertIn("평가 가정 검토 전", rendered)
        value["valuation"]["impliedExpectations"].update(assumptionReviewState="complete", officialFinancialsReady=True,
            financialEvidence={"period": "2023-12-31"})
        self.assertNotIn("17.50%", render_company_change_report(build_company_change_report(value)))
        value["valuation"]["impliedExpectations"]["financialEvidence"]["period"] = "2025-12-31"
        self.assertIn("17.50%", render_company_change_report(build_company_change_report(value)))
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
        self.assertIn(result["invalidation"], str(report["sections"]))
        self.assertIn("매출보다 영업비용이 큽니다", render_company_change_report(report))
        self.assertIn("영업적자", report["summary"])
        self.assertEqual("linkedInsight", report["sections"][0]["key"])
        self.assertIn(result["risks"][0], str(report["sections"]))
        fallback = copy.deepcopy(report)
        fallback["reading"]["financial"] = []
        from digital_twin.modules.news_intelligence.domain.company_change_report import _reading_notification_content
        fallback_brief = str(_reading_notification_content(fallback))
        self.assertIn(result["meaning"], fallback_brief)
        self.assertIn(result["invalidation"], fallback_brief)
        self.assertIn(result["risks"][0], fallback_brief)
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




class CompanyTemporalResearchTests(unittest.TestCase):
    def annual_payload(self):
        value = payload()
        reports = []
        for year, net, pretax, tax, cash, capex in (
            (2024, 93736, 123485, 29749, 118254, 9447),
            (2025, 112010, 132729, 20719, 111482, 12715),
        ):
            period = str(year) + '-12-31'
            entries = [metric(key, amount, period, 'annual', official=True,
                              periodStart=str(year) + '-01-01', sourceDocumentId='annual-' + str(year))
                       for key, amount in (('netIncome', net), ('pretaxIncome', pretax), ('taxProvision', tax),
                                           ('operatingCashFlow', cash), ('capitalExpenditure', capex), ('freeCashFlow', cash-capex))]
            reports.append({'period': period, 'frequency': 'annual', 'publishedAt': str(year+1)+'-02-01', 'metrics': entries})
        value['companyReportEvidence'] = {'annualFinancials': reports, 'recentFinancials': []}
        return value

    def test_exact_bridges_reject_mixed_windows_sources_and_nonreconciling_earnings(self):
        value = self.annual_payload()
        cards = financial_reading_cards(value['companyReportEvidence'])
        income, cash = cards[:2]
        self.assertEqual({'totalChange':18274, 'firstContribution':9244, 'secondContribution':9030, 'residual':0}, income['components'])
        self.assertEqual(-10040, cash['components']['totalChange'])
        for field, bad in (('currency','USD'), ('provider','other'), ('periodStart','2025-03-01'), ('sourceDocumentId','other'), ('official',False), ('value',999)):
            changed = copy.deepcopy(value)
            changed['companyReportEvidence']['annualFinancials'][-1]['metrics'][1][field] = bad
            self.assertNotIn('net-income-bridge', [c['key'] for c in financial_reading_cards(changed['companyReportEvidence'])])
        self.assertIn('정상화 이익', income['limitations'][0])

    def test_registration_correction_new_period_restart_and_no_future_evidence(self):
        from digital_twin.modules.news_intelligence.domain.company_research_record import advance_company_research_record
        value = self.annual_payload()
        stamp = '2026-09-30T02:00:00Z'
        report = build_company_change_report(value)
        record = advance_company_research_record(report, {}, stamp)
        self.assertEqual([], record['history'])
        self.assertEqual(1, record['revision'])
        baseline = copy.deepcopy(record['baseline'])
        self.assertEqual(record, advance_company_research_record(report, copy.deepcopy(record), stamp))
        future_doc = copy.deepcopy(report)
        future_doc['evidence']['documents'] = [{'documentId':'future', 'publishedAt':'2025-02-01',
                                               'observedAt':'2027-02-01', 'bodyVerified':True, 'excerpt':'unknown at cutoff'}]
        self.assertEqual(record, advance_company_research_record(future_doc, record, stamp))
        corrected = copy.deepcopy(value)
        corrected['companyReportEvidence']['annualFinancials'][-1]['metrics'][0]['value'] += 1
        next_record = advance_company_research_record(build_company_change_report(corrected), record, stamp)
        self.assertEqual('same-period-revision', next_record['history'][-1]['kind'])
        self.assertEqual(baseline, next_record['baseline'])
        new = copy.deepcopy(value)
        row = new['companyReportEvidence']['annualFinancials'][-1]
        row.update(period='2026-12-31', publishedAt='2027-02-01')
        for m in row['metrics']:
            m.update(period='2026-12-31', periodStart='2026-01-01', sourceDocumentId='annual-2026')
        # Reject future financial reports before their actual publication.
        self.assertFalse(any(c['kind']=='new-period' for e in advance_company_research_record(build_company_change_report(new), record, stamp)['history'] for c in e['changes']))
        new['snapshot']['generatedAt'] = '2027-02-02T00:00:00Z'
        # Include last year's comparable report in the new read model.
        new['companyReportEvidence']['annualFinancials'].insert(0, value['companyReportEvidence']['annualFinancials'][-1])
        renewed = advance_company_research_record(build_company_change_report(new), next_record, '2027-02-02T01:00:00Z')
        self.assertEqual('new-period', renewed['history'][-1]['kind'])
        self.assertEqual('review-required', renewed['status'])
        self.assertEqual(baseline, renewed['baseline'])
        self.assertEqual(renewed, advance_company_research_record(report, renewed, '2027-02-02T01:00:00Z'))
        self.assertTrue(all(c['before'] for c in renewed['history'][-1]['changes']))
        self.assertNotIn('action', renewed)

    def test_temporal_state_is_saved_quietly_and_read_on_report(self):
        from datetime import datetime, timezone
        from digital_twin.modules.news_intelligence.application.company_change_report_reconciliation_service import CompanyChangeReportReconciler
        class State:
            def __init__(self): self.value = {}
            def load(self): return copy.deepcopy(self.value)
            def replace(self, value): self.value = copy.deepcopy(value)
        state = State()
        queue = SimpleNamespace(recent_for_symbol=lambda *a,**k: [])
        runner = CompanyChangeReportReconciler(account_repository=None, monitor_store=None,
            valuation_query_service=None, queue=queue, now_provider=lambda:datetime(2026,10,1,tzinfo=timezone.utc))
        value = self.annual_payload()
        self.assertEqual('baseline-saved', runner._reconcile_subject(SimpleNamespace(account_id='a'), 'TEST', value, state))
        stored = state.load()['researchRecord']
        report = build_company_change_report({**value, 'companyResearchRecord': stored})
        section = next(s for s in report['sections'] if s['key']=='researchRecord')
        self.assertTrue(section['recordId'])
        self.assertTrue(section['rows'])
        self.assertFalse(report['deliveryEligible'])
        runner._reconcile_subject(SimpleNamespace(account_id='a'), 'TEST', value, state)
        self.assertEqual(stored, state.load()['researchRecord'])

    def test_research_questions_reach_ai_memory_without_future_or_action_authority(self):
        from digital_twin.modules.news_intelligence.domain.company_research_record import advance_company_research_record, company_research_memory
        from digital_twin.modules.decisions.application.decision_continuity_service import DecisionContinuityService
        from digital_twin.modules.decisions.domain.decision_continuity import compact_decision_continuity_packet
        from digital_twin.modules.decisions.domain.notification_ai_decision_brief import _minimum_decision_continuity
        report = build_company_change_report(self.annual_payload())
        record = advance_company_research_record(report, {}, '2026-09-30T02:00:00Z')
        self.assertEqual({}, company_research_memory(record, 'a', 'TEST', '2026-09-30T01:59:00Z'))
        self.assertEqual({}, company_research_memory(record, 'other', 'TEST', '2026-10-01T00:00:00Z'))
        service = DecisionContinuityService(company_research_reader=lambda account, symbol, cutoff: company_research_memory(record, account, symbol, cutoff))
        packet = service.build(account_id='a', symbol='TEST', captured_at='2026-10-01T00:00:00Z')
        self.assertTrue(packet['companyResearch']['originalQuestions'])
        self.assertEqual('not-evaluated', packet['companyResearch']['qualification'])
        self.assertFalse(packet['previousDecision'])
        self.assertEqual(packet['companyResearch'], compact_decision_continuity_packet(packet)['companyResearch'])
        self.assertTrue(_minimum_decision_continuity(packet)['companyResearch']['originalQuestions'])
        self.assertFalse(service.build(account_id='a', symbol='TEST', captured_at='2026-09-29T00:00:00Z').get('companyResearch'))

    def test_report_passages_skip_cover_and_keep_qualifying_text(self):
        from digital_twin.infrastructure.sec_report_passages import report_passages
        from digital_twin.infrastructure.external_signal_provider_sec import ExternalSignalSecMixin
        body = '<p>' + 'Cover page. ' * 3000 + '</p><div>Income tax expense decreased primarily due to the prior-year charge. This change does not establish a recurring benefit in future periods.</div>'
        passages = report_passages(body)
        self.assertEqual(1, len(passages))
        self.assertIn('does not establish', passages[0]['quote'])
        self.assertIn(passages[0]['quote'], body)
        generic = '<p>Income tax expenses primarily reflect business operations, with general uncertainty about future tax regimes and the overall economic environment.</p>'
        specific = '<p>Income tax expense decreased compared to the prior year due to a one-time charge. Other changes partially offset this reduction in reported costs.</p>'
        chosen = report_passages(generic * 3 + specific)
        self.assertIn('compared to', chosen[0]['quote'])
        self.assertEqual('income-tax', chosen[0]['topic'])
        recent = {'form':['4']*30+['10-Q','10-K'], 'accessionNumber':['a']*30+['q','k'],
                  'primaryDocument':['a.htm']*32, 'reportDate':['2026-06-30']*31+['2025-12-31']}
        selected = ExternalSignalSecMixin().report_sec_filings({'filings':{'recent':recent}}, '123')
        self.assertEqual(['10-Q','10-K'], [r['form'] for r in selected])


if __name__ == "__main__":
    unittest.main()
