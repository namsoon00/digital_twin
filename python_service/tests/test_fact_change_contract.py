import unittest

from digital_twin.application.ontology_reasoning_service import reasoning_request_provenance
from digital_twin.domain.events import DomainEvent, ontology_reasoning_requested_event
from digital_twin.domain.fact_changes import (
    dependency_keys_for_changed_fields,
    fact_change_contract,
)
from digital_twin.domain.ontology_change_impact import (
    DEPENDENCY_FINGERPRINT_VERSION,
    build_inference_impact_plan,
    rule_dependency_profiles,
)
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_reasoning_queue import (
    COMPANY_FACT_TYPES,
    EVIDENCE_FACT_TYPES,
    MACRO_FACT_TYPES,
    MARKET_FACT_TYPES,
    PORTFOLIO_FACT_TYPES,
    event_has_reasoning_work,
    event_work_class,
)


class FactChangeContractTests(unittest.TestCase):
    def source_event(self):
        return DomainEvent(
            name="research.evidence.collected",
            aggregate_id="research:005930",
            occurred_at="2026-08-14T00:00:00Z",
            payload={"sourceObservedAt": "2026-08-14T00:00:00Z"},
        )

    def test_news_transport_types_route_to_evidence_without_global_fallback(self):
        event = ontology_reasoning_requested_event(
            self.source_event(),
            "news-analysis-enrichment",
            symbols=["005930"],
            changed_count=1,
            fact_types=["ResearchEvidence", "NewsEvent", "NewsArticleAnalysis"],
            fact_types_by_symbol={
                "005930": ["ResearchEvidence", "NewsEvent", "NewsArticleAnalysis"],
            },
            changed_fields_by_symbol={"005930": ["external.researchEvidence"]},
        )

        contract = event.payload["factChangeContract"]
        provenance = reasoning_request_provenance([event])

        self.assertEqual("ready", contract["status"])
        self.assertEqual(["evidence"], contract["scopeFamilies"])
        self.assertEqual(["evidence"], contract["scopeFamiliesBySymbol"]["005930"])
        self.assertTrue(contract["dependencyKeysComplete"])
        self.assertEqual(
            [
                "kind:article-ai-analysis",
                "kind:article-analysis-conflict",
                "kind:news-event-type",
                "kind:research-evidence",
            ],
            contract["dependencyKeys"],
        )
        self.assertEqual(
            contract["dependencyKeys"],
            provenance["requestedDependencyKeysBySymbol"]["005930"],
        )
        self.assertTrue(provenance["eventDependencyBoundaryAuthoritative"])
        self.assertEqual("EVIDENCE", event_work_class(event))
        self.assertTrue(event_has_reasoning_work(event))
        self.assertEqual(["evidence"], provenance["requestedScopeFamiliesBySymbol"]["005930"])
        self.assertEqual("ready", provenance["factChangeContracts"][0]["status"])

    def test_unknown_transport_type_is_fail_closed_and_auditable(self):
        event = ontology_reasoning_requested_event(
            self.source_event(),
            "future-provider-update",
            symbols=["005930"],
            changed_count=1,
            fact_types=["FutureProviderPayload"],
            fact_types_by_symbol={"005930": ["FutureProviderPayload"]},
        )

        contract = event.payload["factChangeContract"]

        self.assertEqual("blocked-unclassified", contract["status"])
        self.assertEqual(["FutureProviderPayload"], contract["unclassifiedFactTypes"])
        self.assertEqual(
            ["FutureProviderPayload"],
            contract["unclassifiedFactTypesBySymbol"]["005930"],
        )
        self.assertFalse(event_has_reasoning_work(event))

    def test_calendar_event_declares_exact_earnings_abox_dependency(self):
        contract = fact_change_contract(
            ["InvestmentCalendarEvent"],
            {"NVDA": ["InvestmentCalendarEvent"]},
        )

        self.assertEqual("ready", contract["status"])
        self.assertEqual(["evidence", "temporal"], contract["scopeFamilies"])
        self.assertEqual(
            ["kind:earnings-calendar-event"],
            contract["dependencyKeys"],
        )
        self.assertTrue(contract["dependencyKeysComplete"])
        self.assertTrue(contract["dependencyKeysCompleteBySymbol"]["NVDA"])

    def test_company_and_portfolio_facts_have_stable_scope_families(self):
        contract = fact_change_contract([
            "CompanyProfile",
            "FinancialFact",
            "GovernanceChange",
            "CapitalStructureChange",
            "ValuationObservation",
            "Account",
            "Portfolio",
            "Position",
            "PortfolioRiskSnapshot",
            "RebalanceState",
        ])

        self.assertEqual("ready", contract["status"])
        self.assertEqual(
            ["capital", "company-valuation", "exposure", "fundamental", "governance", "portfolio", "position", "profile"],
            contract["scopeFamilies"],
        )

    def test_every_queue_fact_type_is_declared_in_the_change_contract(self):
        queue_fact_types = set().union(
            COMPANY_FACT_TYPES,
            EVIDENCE_FACT_TYPES,
            MACRO_FACT_TYPES,
            MARKET_FACT_TYPES,
            PORTFOLIO_FACT_TYPES,
        )

        contract = fact_change_contract(queue_fact_types)

        self.assertEqual("ready", contract["status"])
        self.assertEqual([], contract["unclassifiedFactTypes"])

    def test_market_flow_event_uses_exact_changed_field_dependencies(self):
        event = ontology_reasoning_requested_event(
            self.source_event(),
            "verified-monitor-snapshot",
            symbols=["005930"],
            changed_count=1,
            fact_types=["MarketQuote", "TechnicalIndicator", "ExecutionFlow"],
            fact_types_by_symbol={
                "005930": ["MarketQuote", "TechnicalIndicator", "ExecutionFlow"],
            },
            changed_fields_by_symbol={
                "005930": [
                    "current_price",
                    "change_rate",
                    "ma20_distance",
                    "trade_strength",
                    "foreign_net_volume",
                ],
            },
        )

        contract = event.payload["factChangeContract"]
        provenance = reasoning_request_provenance([event])

        self.assertTrue(contract["dependencyKeysComplete"])
        self.assertTrue(contract["dependencyKeysCompleteBySymbol"]["005930"])
        self.assertIn("kind:stock:field:pricechangerate", contract["dependencyKeys"])
        self.assertIn("kind:stock:field:ma20distance", contract["dependencyKeys"])
        self.assertIn("kind:stock:field:tradestrength", contract["dependencyKeys"])
        self.assertIn("kind:stock:field:smartmoneynetvolume", contract["dependencyKeys"])
        self.assertTrue(provenance["eventDependencyBoundaryAuthoritative"])

    def test_exact_ma20_event_selects_only_dependency_intersection(self):
        rules = default_graph_inference_rules()
        keys = dependency_keys_for_changed_fields(["ma20_distance"])
        before = [{
            "scopeId": "symbol:005930:state",
            "generationId": "state-a",
            "impactScopeFamilies": ["market"],
            "semanticFingerprints": {"market": "before"},
            "semanticDependencyFingerprintVersion": DEPENDENCY_FINGERPRINT_VERSION,
            "semanticDependencyFingerprints": {key: "before" for key in keys},
        }]
        after = [{
            **before[0],
            "generationId": "state-b",
            "semanticFingerprints": {"market": "after"},
            "semanticDependencyFingerprints": {key: "after" for key in keys},
        }]

        plan = build_inference_impact_plan(
            before,
            after,
            ["005930"],
            explicit_target_symbols=["005930"],
            rules=rules,
            requested_fact_families=["market"],
            requested_fact_families_by_symbol={"005930": ["market"]},
            requested_dependency_keys=keys,
            requested_dependency_keys_by_symbol={"005930": keys},
            dependency_boundary_authoritative=True,
        )
        expected = {
            profile["ruleId"]
            for profile in rule_dependency_profiles(rules)
            if set(profile.get("dependencyKeys") or []).intersection(keys)
        }

        self.assertTrue(plan["eventDependencyKeyRoutingApplied"])
        self.assertEqual(expected, set(plan["candidateRuleIds"]))
        self.assertLessEqual(plan["candidateRuleCount"], 20)


if __name__ == "__main__":
    unittest.main()
