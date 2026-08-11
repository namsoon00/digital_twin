import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.company_knowledge import (
    build_company_knowledge,
    company_prompt_context,
    merge_company_overview_rows,
    merge_company_knowledge_rows,
)
from digital_twin.domain.notification_ai_gate_validation import ai_decision_input_packet
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_prompting import build_ai_inference_packet, prompt_payload
from digital_twin.domain.ontology_relation_facts import position_signal_facts
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.portfolio import PortfolioSummary, Position
from digital_twin.domain.portfolio_ontology_company_concepts import add_company_knowledge_concepts
from digital_twin.infrastructure.graph_store_payloads import (
    PROMOTED_NUMERIC_ENTITY_FIELDS,
    PROMOTED_TEXT_ENTITY_FIELDS,
)
from digital_twin.infrastructure.typedb_ontology import (
    TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES,
    TYPEDB_PROMOTED_TEXT_ATTRIBUTES,
    TypeDBOntologyGraphRepository,
    typedb_native_match_query,
    typedb_native_rule_profile,
)
from digital_twin.infrastructure.external_signals import ExternalSignalProvider


class MemoryStore:
    def __init__(self, payload=None):
        self.payload = dict(payload or {})

    def load(self):
        return dict(self.payload)

    def replace(self, payload):
        self.payload = dict(payload or {})


def sample_yfinance(collected_at="2026-08-11T00:00:00Z"):
    return {
        "provider": "yfinance",
        "collectedAt": collected_at,
        "info": {
            "longName": "Example Corp",
            "returnOnEquity": 0.18,
            "priceToBook": 1.7,
            "companyOfficers": [
                {"name": "Jane Kim", "title": "Chief Executive Officer"},
                {"name": "Min Park", "title": "Chief Financial Officer"},
            ],
        },
        "incomeStatement": [
            {"metric": "Total Revenue", "values": {"2025-12-31": 1200, "2024-12-31": 1000}},
            {"metric": "Operating Income", "values": {"2025-12-31": 180, "2024-12-31": 120}},
            {"metric": "Net Income", "values": {"2025-12-31": 140, "2024-12-31": 100}},
        ],
        "balanceSheet": [
            {"metric": "Total Assets", "values": {"2025-12-31": 2500, "2024-12-31": 2200}},
            {"metric": "Total Liabilities", "values": {"2025-12-31": 900, "2024-12-31": 850}},
            {"metric": "Stockholders Equity", "values": {"2025-12-31": 1600, "2024-12-31": 1350}},
            {"metric": "Total Debt", "values": {"2025-12-31": 400, "2024-12-31": 450}},
            {"metric": "Ordinary Shares Number", "values": {"2025-12-31": 105, "2024-12-31": 100}},
        ],
        "cashFlow": [
            {"metric": "Operating Cash Flow", "values": {"2025-12-31": 190, "2024-12-31": 130}},
            {"metric": "Capital Expenditure", "values": {"2025-12-31": -40, "2024-12-31": -30}},
        ],
    }


class CompanyKnowledgeTests(unittest.TestCase):
    def test_field_level_merge_keeps_global_profile_and_prefers_kis_ratios(self):
        merged = merge_company_overview_rows(
            {
                "provider": "yfinance",
                "name": "Example Corp",
                "sector": "Technology",
                "peRatio": 0,
                "pbr": 0,
            },
            {
                "provider": "KIS Open API",
                "peRatio": 12.4,
                "pbr": 1.6,
                "trailingEPS": 3200,
            },
        )

        self.assertEqual("Technology", merged["sector"])
        self.assertEqual(12.4, merged["peRatio"])
        self.assertEqual(1.6, merged["pbr"])
        self.assertEqual("KIS Open API", merged["fieldSources"]["peRatio"])

    def test_company_knowledge_extracts_periods_ratios_governance_and_stable_revision(self):
        first = build_company_knowledge("TEST", yfinance=sample_yfinance())
        refreshed = build_company_knowledge(
            "TEST",
            yfinance=sample_yfinance("2026-08-11T00:30:00Z"),
        )

        latest = first["financials"]["annual"][0]
        self.assertEqual(20.0, latest["revenueGrowthPct"])
        self.assertEqual(50.0, latest["operatingIncomeGrowthPct"])
        self.assertEqual(150.0, latest["freeCashFlow"])
        self.assertEqual(2, first["governance"]["executiveCount"])
        self.assertEqual(0.18, first["valuation"]["returnOnEquity"])
        self.assertEqual(18.0, first["valuation"]["returnOnEquityPct"])
        self.assertEqual(first["factRevision"], refreshed["factRevision"])

    def test_company_context_reaches_relation_facts_and_final_ai_packet(self):
        knowledge = build_company_knowledge("TEST", yfinance=sample_yfinance())
        external_signals = {"companyKnowledge": {"TEST": knowledge}}
        bounded = company_prompt_context(external_signals, "test")

        self.assertEqual("active-company-rule-only", bounded["judgmentUse"])
        self.assertEqual(1, len(bounded["latestFinancials"]["annual"]))
        self.assertEqual(20.0, bounded["latestFinancials"]["annual"][0]["revenueGrowthPct"])
        self.assertEqual(2, len(bounded["governance"]["executives"]))

        facts = position_signal_facts(
            Position(symbol="TEST", name="Example Corp", current_price=100, source="watchlist"),
            PortfolioSummary(total=1000, invested=0, cash=1000, markets=[], sectors=[], concentration=0),
            external_signals,
        )
        self.assertEqual(knowledge["factRevision"], facts["companyContext"]["factRevision"])

        context = {
            "messageType": "investmentInsight",
            "ontologyRelationContext": {
                "facts": facts,
                "activeRules": [{"ruleId": "graph.company.market.fundamental_confirmation.support.v1"}],
            },
        }
        packet = ai_decision_input_packet(context, {"facts": facts}, {"level": "beginner"})
        ai_company = packet["relationshipDatabaseInference"]["companyContext"]

        self.assertEqual("TEST", ai_company["symbol"])
        self.assertEqual(18.0, ai_company["valuation"]["returnOnEquityPct"])
        self.assertEqual(1, len(ai_company["latestFinancials"]["annual"]))

    def test_company_abox_is_bounded_and_connects_stock_to_current_company_states(self):
        graph = PortfolioOntology(portfolio_id="test")
        knowledge = build_company_knowledge("TEST", yfinance=sample_yfinance())

        add_company_knowledge_concepts(
            graph,
            "stock:TEST",
            "TEST",
            {"companyKnowledge": {"TEST": knowledge}},
        )

        kinds = [item.kind for item in graph.entities]
        relation_types = [item.relation_type for item in graph.relations]
        self.assertLessEqual(kinds.count("company-financial-state"), 8)
        self.assertEqual(2, kinds.count("executive-role"))
        self.assertIn("REPRESENTS_COMPANY", relation_types)
        self.assertIn("HAS_FINANCIAL_STATE", relation_types)
        self.assertIn("HAS_GOVERNANCE_STATE", relation_types)
        self.assertIn("HAS_CAPITAL_STATE", relation_types)
        current_relations = [
            item
            for item in graph.relations
            if item.source == "stock:TEST" and item.relation_type == "HAS_FINANCIAL_STATE"
        ]
        self.assertEqual(1, len(current_relations))
        self.assertTrue(any(
            item.source == "stock:TEST" and item.relation_type == "HAS_FINANCIAL_STATE"
            for item in graph.relations
        ))
        packet = build_ai_inference_packet(graph)
        ai_payload = prompt_payload(graph)
        self.assertEqual(5, packet["graphInputs"]["companyContextCount"])
        self.assertTrue(any(item["kind"] == "company-financial-state" for item in ai_payload["companyContext"]))

    def test_dart_interim_statement_does_not_replace_annual_series(self):
        knowledge = build_company_knowledge(
            "005930",
            yfinance=sample_yfinance(),
            dart_disclosure={
                "provider": "OpenDART",
                "receiptDate": "20260811",
                "financialStatementBasis": {"businessYear": "2026", "reportCode": "11012", "scope": "CFS"},
                "financialStatements": [
                    {
                        "account_nm": "매출액",
                        "thstrm_dt": "2026.01.01 ~ 2026.06.30",
                        "thstrm_amount": "1200",
                        "frmtrm_dt": "2025.01.01 ~ 2025.06.30",
                        "frmtrm_amount": "1000",
                    },
                    {
                        "account_nm": "영업이익",
                        "thstrm_dt": "2026.01.01 ~ 2026.06.30",
                        "thstrm_amount": "180",
                        "frmtrm_dt": "2025.01.01 ~ 2025.06.30",
                        "frmtrm_amount": "120",
                    },
                ],
            },
        )

        self.assertEqual(2, len(knowledge["financials"]["annual"]))
        self.assertEqual(2, len(knowledge["financials"]["interim"]))
        self.assertEqual(20.0, knowledge["financials"]["interim"][0]["revenueGrowthPct"])

    def test_interim_only_report_counts_as_financial_coverage_and_current_capital(self):
        knowledge = build_company_knowledge(
            "005930",
            dart_disclosure={
                "provider": "OpenDART",
                "receiptDate": "20260811",
                "financialStatementBasis": {"businessYear": "2026", "reportCode": "11012", "scope": "CFS"},
                "financialStatements": [
                    {
                        "account_nm": "자산총계",
                        "thstrm_dt": "2026.01.01 ~ 2026.06.30",
                        "thstrm_amount": "2500",
                        "frmtrm_dt": "2025.01.01 ~ 2025.06.30",
                        "frmtrm_amount": "2200",
                    },
                    {
                        "account_nm": "현금및현금성자산",
                        "thstrm_dt": "2026.01.01 ~ 2026.06.30",
                        "thstrm_amount": "500",
                        "frmtrm_dt": "2025.01.01 ~ 2025.06.30",
                        "frmtrm_amount": "400",
                    },
                ],
            },
        )

        self.assertEqual(2, knowledge["coverage"]["financialPeriods"])
        self.assertNotIn("financial-statements", knowledge["coverage"]["missing"])
        self.assertEqual(500.0, knowledge["capital"]["cash"])

    def test_company_merge_keeps_statement_history_and_applies_fresher_kis_ratios(self):
        existing = build_company_knowledge("TEST", yfinance=sample_yfinance())
        current = build_company_knowledge(
            "TEST",
            overview={"provider": "KIS Open API", "peRatio": 11.2, "pbr": 1.4, "trailingEPS": 3500},
        )

        merged = merge_company_knowledge_rows(existing, current)

        self.assertEqual(2, len(merged["financials"]["annual"]))
        self.assertEqual(2, len(merged["governance"]["executives"]))
        self.assertEqual(11.2, merged["valuation"]["peRatio"])
        self.assertEqual(1.4, merged["valuation"]["pbr"])
        self.assertEqual("sufficient", merged["coverage"]["dataState"])

    def test_empty_provider_payload_does_not_create_phantom_company_knowledge(self):
        self.assertEqual({}, build_company_knowledge("TEST"))

    def test_shared_company_cache_backfills_existing_external_cache_entries(self):
        external_payload = {
            "entries": {
                "one": {"signals": {"yfinanceData": {"TEST": sample_yfinance()}}},
            },
        }
        company_cache = MemoryStore()
        provider = ExternalSignalProvider(
            settings={},
            cache=MemoryStore(external_payload),
            company_cache=company_cache,
        )

        shared = provider.load_shared_company_knowledge(external_payload, persist_backfill=True)

        self.assertEqual(2, len(shared["TEST"]["financials"]["annual"]))
        self.assertEqual("company-knowledge-cache-v1", company_cache.payload["schemaVersion"])
        self.assertIn("TEST", company_cache.payload["symbols"])

    def test_company_rules_compile_to_native_typedb_functions(self):
        rules = {item.rule_id: item for item in default_graph_inference_rules()}
        expected = {
            "graph.company.market.fundamental_confirmation.support.v1",
            "graph.company.market.structural_decline.risk.v1",
            "graph.company.market.overreaction_candidate.support.v1",
            "graph.company.market.fragile_rally.risk.v1",
            "graph.company.capital.dilution.risk.v1",
            "graph.company.market.quality_valuation.support.v1",
            "graph.company.market.valuation_stretch.risk.v1",
            "graph.company.governance.coverage_gap.v1",
        }
        for rule_id in expected:
            profile = typedb_native_rule_profile(rules[rule_id].to_dict())
            self.assertEqual("ready", profile["status"], rule_id)
            self.assertEqual([], profile["blockers"], rule_id)

        query = typedb_native_match_query(
            rules["graph.company.market.structural_decline.risk.v1"].to_dict(),
            target_symbols=["TEST"],
        )["query"]
        self.assertIn("ontology-company-revenue-growth-pct", query)
        self.assertIn("ontology-company-operating-income-growth-pct", query)
        self.assertIn("ontology-ma20-distance", query)

    def test_company_abox_fields_are_promoted_into_typedb_attributes(self):
        self.assertEqual(
            set(TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES),
            set(PROMOTED_NUMERIC_ENTITY_FIELDS),
        )
        self.assertEqual(
            set(TYPEDB_PROMOTED_TEXT_ATTRIBUTES),
            set(PROMOTED_TEXT_ENTITY_FIELDS),
        )
        repository = TypeDBOntologyGraphRepository("")
        query = repository.node_insert_query(
            {
                "id": "company-financial-state:TEST:2025",
                "label": "TEST 2025 financial state",
                "kind": "company-financial-state",
                "ontologyBox": "ABox",
                "symbol": "TEST",
                "tboxClass": "FinancialState",
                "propertiesJson": json.dumps(
                    {
                        "revenueGrowthPct": -12.5,
                        "operatingIncomeGrowthPct": -8.25,
                        "period": "2025",
                        "reportingFrequency": "annual",
                    }
                ),
            },
            "2026-08-11T00:00:00Z",
        )

        self.assertIn("has ontology-company-revenue-growth-pct -12.5", query)
        self.assertIn("has ontology-company-operating-income-growth-pct -8.25", query)
        self.assertIn('has ontology-reporting-period "2025"', query)
        self.assertIn('has ontology-reporting-frequency "annual"', query)


if __name__ == "__main__":
    unittest.main()
