import unittest

from digital_twin.modules.news_intelligence.domain.company_knowledge import (
    build_company_knowledge, dart_statement_periods, enrich_financial_periods,
    merge_company_knowledge_rows, statement_periods,
)
from digital_twin.modules.news_intelligence.domain.financial_reporting import (
    current_financial_state, financial_period_sort_key, reporting_period_end, compact_financial_evidence,
)
from digital_twin.modules.reasoning.domain.portfolio_ontology_company_concepts import _period_rank


class FinancialReportingIntegrityTests(unittest.TestCase):
    def _assert_sec_period_contracts(self):
        from digital_twin.infrastructure.external_api.adapters.base import legacy_provider

        provider = legacy_provider({})
        fact = provider.latest_sec_fact({
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [{
                "val": 10, "end": "2022-12-31", "filed": "2023-02-01",
                "form": "10-K", "fp": "FY",
            }]}},
            "Revenues": {"units": {"USD": [{
                "val": 25, "start": "2026-01-01", "end": "2026-06-30",
                "filed": "2026-08-01", "form": "10-Q", "fp": "Q2",
                "frame": "CY2026Q2YTD", "accn": "0001-26-000001",
            }]}},
        }, ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"])

        self.assertEqual(25, fact["value"])
        self.assertEqual("Revenues", fact["tag"])
        self.assertEqual("2026-01-01", fact["start"])
        self.assertEqual("CY2026Q2YTD", fact["frame"])

        knowledge = build_company_knowledge(
            "NVDA",
            yfinance={
                "info": {"financialCurrency": "USD"},
                "incomeStatement": [{"metric": "Total Revenue", "values": {"2025-12-31": 100}}],
            },
            sec_filing={
                "provider": "SEC EDGAR",
                "facts": {
                    "entityName": "NVIDIA Corporation",
                    "revenue": {
                        "tag": "Revenues", "value": 70, "start": "2026-01-01",
                        "end": "2026-06-30", "filed": "2026-08-01",
                        "form": "10-Q", "fp": "Q2", "unit": "USD",
                    },
                    "totalDebt": {
                        "tag": "LongTermDebt", "value": 5, "end": "2020-06-30",
                        "filed": "2020-08-01", "form": "10-Q", "fp": "Q2", "unit": "USD",
                    },
                },
            },
        )

        self.assertEqual(100, knowledge["financials"]["annual"][0]["revenue"])
        self.assertEqual(70, knowledge["financials"]["interim"][0]["revenue"])
        self.assertEqual(1, len(knowledge["financials"]["interim"]))
        self.assertNotIn("totalDebt", knowledge["financials"]["interim"][0])
        self.assertEqual("interim", knowledge["financials"]["interim"][0]["frequency"])
        self.assertEqual(
            "year-to-date",
            knowledge["financials"]["interim"][0]["metricProvenance"]["revenue"]["durationBasis"],
        )

    def test_valid_paired_vendor_ratio_survives_partial_official_override(self):
        self._assert_sec_period_contracts()
        vendor = {"period": "2026-06-30", "frequency": "quarterly", "revenue": 100, "freeCashFlow": 20,
                  "metricProvenance": {field: {"provider": "yfinance", "durationBasis": "quarterly", "currency": "KRW"}
                                       for field in ("revenue", "freeCashFlow")}}
        official = {"period": "2026-06-30", "frequency": "interim", "officialSource": True, "revenue": 101,
                    "metricProvenance": {"revenue": {"provider": "OpenDART", "durationBasis": "quarterly", "currency": "KRW"}}}
        state = current_financial_state({"quarterly": [vendor], "interim": [official]})
        self.assertEqual(101, state["revenue"])
        self.assertEqual(20, state["freeCashFlowMarginPct"])
        evidence = state["derivedMetricEvidence"]["freeCashFlowMarginPct"]
        self.assertEqual("yfinance", evidence["provider"])
        self.assertEqual(100, evidence["denominator"]["value"])
        self.assertIn(evidence, compact_financial_evidence({"currentFinancialState": state})["ratios"])

    def test_ratio_without_a_compatible_paired_source_is_not_invented(self):
        rows = {"quarterly": [{"period": "2026-06-30", "revenue": 100, "freeCashFlow": 20,
            "metricProvenance": {"revenue": {"provider": "OpenDART", "durationBasis": "quarterly"},
                                 "freeCashFlow": {"provider": "yfinance", "durationBasis": "year-to-date"}}}]}
        self.assertNotIn("freeCashFlowMarginPct", current_financial_state(rows))

    def test_dart_collector_retains_comparative_columns_and_rows_after_180(self):
        from datetime import datetime, timezone
        from digital_twin.infrastructure.external_api.adapters.base import legacy_provider, empty_signals
        provider = legacy_provider({})
        provider.guarded_call = lambda _source, _key, call: call()
        rows = [{"account_nm": "row", "ord": str(i)} for i in range(200)]
        rows.append({"account_nm": "영업이익", "thstrm_amount": "130", "frmtrm_q_amount": "100",
                     "thstrm_add_amount": "240", "frmtrm_add_amount": "210"})
        provider.fetch_json = lambda url, _headers: {"status": "000", "list": rows if "fnlttSinglAcntAll" in url else []}
        disclosure = {}
        provider.attach_opendart_company_facts(empty_signals(), disclosure, "000001", "00000001", "test-only", datetime(2026, 9, 16, tzinfo=timezone.utc))
        saved = disclosure["financialStatements"]
        self.assertEqual(201, len(saved))
        self.assertEqual("100", saved[-1]["frmtrm_q_amount"])
        self.assertEqual("210", saved[-1]["frmtrm_add_amount"])

    def test_iso_quarter_order_uses_year_and_month_not_day_and_time(self):
        periods = ["2026-03-31T00:00:00", "2025-12-31T00:00:00", "2026-06-30T00:00:00"]
        self.assertEqual(periods[2], max(periods, key=financial_period_sort_key))
        self.assertGreater(_period_rank(periods[2], "quarterly"), _period_rank(periods[0], "quarterly"))

    def test_date_ranges_offsets_and_invalid_dates(self):
        for value in ["2026.01.01 ~ 2026.06.30", "20260630", "2026-06-30T00:00:00+09:00"]:
            self.assertEqual("2026-06-30", reporting_period_end(value).isoformat())
        self.assertIsNone(reporting_period_end("2026-02-30"))
        self.assertIsNone(reporting_period_end("unknown"))

    def test_latest_comparison_uses_adjacent_actual_quarters(self):
        rows = statement_periods({"balanceSheet": [{"metric": "Ordinary Shares Number", "values": {
            "2026-06-30T00:00:00": 4401, "2026-03-31T00:00:00": 4400, "2025-12-31T00:00:00": 2800,
        }}]}, frequency="quarterly")
        enriched = enrich_financial_periods(rows)
        self.assertAlmostEqual(0.0227, enriched[0]["sharesOutstandingGrowthPct"])
        self.assertNotIn("sharesOutstandingGrowthPct", enriched[1])
        self.assertEqual("share-count-discontinuity-unverified", enriched[1]["comparisonEvidence"]["sharesOutstandingGrowthPct"]["reason"])

    def test_missing_current_is_not_negative_one_hundred_percent(self):
        rows = enrich_financial_periods([{"period": "2026-06-30"}, {"period": "2026-03-31", "revenue": 100}])
        self.assertNotIn("revenueGrowthPct", rows[0])

    def test_missing_quarter_does_not_become_quarter_over_quarter(self):
        rows = enrich_financial_periods([
            {"period": "2026-06-30", "frequency": "quarterly", "revenue": 100},
            {"period": "2025-12-31", "frequency": "quarterly", "revenue": 80},
        ])
        self.assertNotIn("revenueGrowthPct", rows[0])

    def test_share_count_basis_change_is_not_dilution(self):
        rows = [{"period": period, "sharesOutstanding": value, "metricProvenance": {
            "sharesOutstanding": {"shareCountBasis": basis, "provider": "vendor"}}}
            for period, value, basis in [("2026-06-30", 120, "issued"), ("2026-03-31", 100, "ordinary-outstanding")]]
        self.assertNotIn("sharesOutstandingGrowthPct", enrich_financial_periods(rows)[0])

    def test_dart_full_api_without_date_fields_parses_yoy_income(self):
        rows = dart_statement_periods([{
            "bsns_year": "2026", "reprt_code": "11012", "sj_div": "CIS", "account_nm": "영업이익",
            "account_id": "dart_OperatingIncomeLoss", "thstrm_amount": "130", "frmtrm_q_amount": "100",
            "frmtrm_amount": "400", "rcept_no": "20260814000001", "currency": "KRW",
        }], {"scope": "CFS"})
        enriched = enrich_financial_periods(rows)
        self.assertEqual(["2026-06-30", "2025-06-30"], [row["period"] for row in rows])
        self.assertEqual(30, enriched[0]["operatingIncomeGrowthPct"])
        self.assertEqual("frmtrm_q_amount", rows[1]["metricProvenance"]["operatingIncome"]["amountField"])

    def test_dart_balance_prior_is_previous_year_end(self):
        rows = dart_statement_periods([{"bsns_year": "2026", "reprt_code": "11012", "sj_div": "BS",
            "account_nm": "자산총계", "thstrm_amount": "120", "frmtrm_amount": "100"}])
        self.assertEqual(["2026-06-30", "2025-12-31"], [row["period"] for row in rows])

    def test_dart_cashflow_ytd_does_not_divide_quarterly_revenue(self):
        periods = [{"period": "2026-06-30", "operatingCashFlow": 20, "netIncome": 10,
                    "metricProvenance": {"operatingCashFlow": {"durationBasis": "year-to-date"},
                                         "netIncome": {"durationBasis": "quarterly"}}}]
        self.assertNotIn("cashConversionPct", enrich_financial_periods(periods)[0])

    def test_official_unparsed_input_is_an_explicit_integrity_error(self):
        row = build_company_knowledge("EXAMPLE", dart_disclosure={"provider": "OpenDART", "financialStatements": [{"account_nm": "unknown"}]})
        self.assertEqual("error", row["financialIntegrity"]["status"])
        self.assertEqual(1, row["financialIntegrity"]["officialInputRows"])
        self.assertFalse(row["coverage"]["officialCoverage"]["financials"])
        self.assertFalse(row["coverage"]["officialCoverage"]["capital"])

    def test_current_financial_state_keeps_source_of_each_metric(self):
        secondary = {"period": "2026-06-30", "frequency": "quarterly", "revenue": 100,
                     "metricProvenance": {"revenue": {"provider": "yfinance", "durationBasis": "quarterly"}}}
        official = {"period": "2026-06-30", "frequency": "interim", "officialSource": True, "revenue": 101,
                    "metricProvenance": {"revenue": {"provider": "OpenDART", "durationBasis": "quarterly"}}}
        state = current_financial_state({"interim": [official], "quarterly": [secondary]})
        self.assertEqual(101, state["revenue"])
        self.assertEqual("OpenDART", state["metricProvenance"]["revenue"]["provider"])

    def test_newer_shorter_history_replaces_stale_long_history(self):
        old = {"financials": {"quarterly": [{"period": "2026-03-31"}, {"period": "2025-12-31"}]}}
        new = {"financials": {"quarterly": [{"period": "2026-06-30"}]}}
        self.assertEqual("2026-06-30", merge_company_knowledge_rows(old, new)["financials"]["quarterly"][0]["period"])

    def test_issued_and_weighted_shares_never_substitute_outstanding(self):
        rows = statement_periods({"balanceSheet": [
            {"metric": "Share Issued", "values": {"2026-06-30": 120}},
            {"metric": "Weighted Ordinary Shares Number", "values": {"2026-06-30": 110}},
        ]}, frequency="quarterly")
        self.assertEqual(120, rows[0]["issuedShares"])
        self.assertNotIn("sharesOutstanding", rows[0])

    def test_unattributed_legacy_comparison_is_not_verified(self):
        rows = enrich_financial_periods([{"period": "2026-06-30", "frequency": "quarterly", "revenue": 100},
                                        {"period": "2026-03-31", "frequency": "quarterly", "revenue": 80}])
        self.assertNotIn("revenueGrowthPct", rows[0])
        self.assertEqual("missing-comparison-lineage", rows[0]["comparisonEvidence"]["revenueGrowthPct"]["reason"])

    def test_dart_does_not_treat_prior_annual_cashflow_as_prior_half(self):
        rows = dart_statement_periods([{"bsns_year": "2026", "reprt_code": "11012", "sj_div": "CF",
            "account_nm": "영업활동현금흐름", "thstrm_amount": "20", "frmtrm_amount": "100", "frmtrm_nm": "제 31 기"}])
        self.assertEqual(1, len(rows))

    def test_excluded_comparison_is_retained_for_audit_not_a_rule_value(self):
        rows = enrich_financial_periods(statement_periods({"balanceSheet": [{"metric": "Ordinary Shares Number",
            "values": {"2026-06-30": 160, "2026-03-31": 100}}]}, frequency="quarterly"))
        state = current_financial_state({"quarterly": rows})
        self.assertNotIn("sharesOutstandingGrowthPct", state)
        packet = compact_financial_evidence({"currentFinancialState": state})
        self.assertEqual("excluded", packet["comparisons"][0]["status"])

    def test_unsorted_legacy_rows_are_never_taken_as_latest_or_verified(self):
        state = current_financial_state({"quarterly": [{"period": "2026-03-31"}, {"period": "2026-06-30"}]})
        self.assertEqual("2026-06-30", state["period"])
        self.assertEqual("legacy-unverified", state["financialReportingVersion"])


class FinancialNarrativeContractTests(unittest.TestCase):
    def test_historical_source_anomaly_is_not_a_current_financial_warning(self):
        from digital_twin.modules.news_intelligence.domain.company_knowledge import company_prompt_context
        row = build_company_knowledge("TEST", yfinance={"provider": "yfinance", "info": {"financialCurrency": "KRW"},
            "quarterlyBalanceSheet": [{"metric": "Ordinary Shares Number", "values": {
                "2026-06-30": 4401, "2026-03-31": 4400, "2025-12-31": 2800}}]})
        self.assertTrue(row["financialIntegrity"]["issues"])
        context = company_prompt_context({"companyKnowledge": {"TEST": row}}, "TEST")
        self.assertEqual([], context["financialIntegrity"]["issues"])
        self.assertGreater(context["financialIntegrity"]["historyIssueCount"], 0)
        self.assertTrue(row["financialIntegrity"]["issues"])
        row["financialIntegrity"]["issues"].append("official-financial-statements-unparsed")
        context = company_prompt_context({"companyKnowledge": {"TEST": row}}, "TEST")
        self.assertIn("official-financial-statements-unparsed", context["financialIntegrity"]["issues"])

    def company(self):
        from digital_twin.modules.news_intelligence.domain.company_knowledge import company_prompt_context
        row = build_company_knowledge("TEST", yfinance={"provider": "yfinance", "info": {"financialCurrency": "KRW"},
            "quarterlyIncomeStatement": [{"metric": "Operating Income", "values": {"2026-06-30": 130, "2026-03-31": 100}}],
            "quarterlyBalanceSheet": [{"metric": "Ordinary Shares Number", "values": {"2026-06-30": 1001, "2026-03-31": 1000}}]})
        return company_prompt_context({"companyKnowledge": {"TEST": row}}, "TEST")

    def context(self):
        return {"ontologyRelationContext": {"facts": {"companyContext": self.company(), "ma20Distance": -7.2},
                "activeRules": [{"ruleId": "graph.company.market.value_trap.risk.v1"}]}}

    def test_compacted_prompt_preserves_complete_comparison_packet(self):
        from digital_twin.modules.decisions.domain.notification_ai_decision_brief import _minimum_company_context
        from digital_twin.modules.decisions.domain.notification_ai_context_router import _company_context
        company = self.company()
        packet = company["financialEvidence"]
        self.assertEqual(packet, _minimum_company_context(company, emergency=True)["financialEvidence"])
        compact, _ = _company_context({"companyContext": company}, [{"ruleId": "graph.company.capital.dilution.risk.v1"}], [], {})
        self.assertEqual(packet, compact["financialEvidence"])

    def test_numeric_financial_evidence_is_citable(self):
        from digital_twin.modules.notifications.domain.notification_narrative import build_decision_core_evidence_ledger
        rows = build_decision_core_evidence_ledger(facts={}, rules=[], hypotheses=[], company_evidence=self.company())
        by_id = {row["evidenceId"]: row for row in rows}
        self.assertEqual(30, by_id["financial:operatingIncome:change"]["value"])
        self.assertEqual("2026-03-31", by_id["financial:operatingIncome:previous"]["sourceAsOf"])

    def test_same_financials_are_background_not_a_new_filing(self):
        from digital_twin.modules.notifications.domain.financial_evidence_presentation import financial_evidence_links, financial_evidence_rows
        context = self.context()
        first = financial_evidence_rows(context)
        self.assertIn("전분기 대비 +30.00%", " ".join(first))
        context["previousDeliveredInvestmentAIInsightEpisode"] = {"financialEvidence": self.company()["financialEvidence"]}
        reused = financial_evidence_rows(context)
        self.assertEqual(1, len(reused))
        self.assertIn("직전 알림과 같습니다", reused[0])
        self.assertIn("새로 반영된 재무 변화는 없", reused[0])
        self.assertNotIn("전분기 대비 +30.00%", reused[0])
        self.assertEqual((), financial_evidence_links(context))

    def test_source_correction_can_notify_once_but_cannot_publish_invalid_insight(self):
        from digital_twin.modules.decisions.domain.investment_insight_assessment import investment_insight_delivery_transition
        context = self.context()
        assessment = {"publishable": True, "direction": "positive", "thesisKey": "recovery"}
        context["previousInvestmentAIInsightEpisode"] = {"episodeId": "old", "insightAssessment": assessment}
        self.assertIn("financial-evidence-changed", investment_insight_delivery_transition(context, assessment)["changes"])
        context["previousInvestmentAIInsightEpisode"]["financialEvidence"] = self.company()["financialEvidence"]
        self.assertFalse(investment_insight_delivery_transition(context, assessment)["material"])
        self.assertFalse(investment_insight_delivery_transition(context, {"publishable": False})["material"])

    def test_price_below_average_is_recovery_not_maintenance(self):
        from digital_twin.modules.notifications.application.notification_ai_gate_message import _price_confirmation_check
        text = _price_confirmation_check(self.context())
        self.assertIn("회복", text)
        self.assertNotIn("유지", text)

    def test_financial_terms_do_not_expand_recursively(self):
        from digital_twin.modules.read_models.domain.customer_investment_document import customer_investment_text
        text = "순자산 대비 주가(PBR), 자기자본이익률(ROE)"
        rendered = customer_investment_text(text)
        self.assertEqual(rendered, customer_investment_text(rendered))
        self.assertEqual(1, rendered.count("PBR"))
        self.assertNotIn("(순자산 대비 주가(", rendered)

    def test_current_abox_links_to_latest_financial_with_metric_lineage(self):
        from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
        from digital_twin.modules.reasoning.domain.ontology_schema import add_entity
        from digital_twin.modules.reasoning.domain.portfolio_ontology_company_concepts import add_company_knowledge_concepts
        company = self.company()
        company["financials"] = company["latestFinancials"]
        graph = PortfolioOntology("financial-regression")
        stock_id = add_entity(graph, "stock", "TEST", "TEST", {"symbol": "TEST", "tboxClass": "Stock"})
        add_company_knowledge_concepts(graph, stock_id, "TEST", {"companyKnowledge": {"TEST": company}})
        links = [link for link in graph.relations if link.source == stock_id and link.relation_type == "HAS_FINANCIAL_STATE"]
        self.assertEqual(1, len(links))
        state = next(node for node in graph.entities if node.entity_id == links[0].target)
        self.assertEqual("2026-06-30", state.properties["period"])
        self.assertEqual("yfinance", state.properties["metricProvenance"]["operatingIncome"]["provider"])


if __name__ == "__main__":
    unittest.main()
