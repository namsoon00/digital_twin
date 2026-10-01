"""Source-to-graph regressions for measured values, clocks and source identity."""
from copy import deepcopy
import unittest

from digital_twin.infrastructure.external_signal_provider_yfinance import (
    bind_yfinance_estimate_metadata, normalized_yfinance_earnings_estimates,
)
from digital_twin.infrastructure.external_api.mysql_stores import MySQLExternalDataStore
from digital_twin.modules.market_data.application.external_data.read_model_service import ExternalSignalsReadModelService
from digital_twin.modules.market_data.domain.rates import interest_rate_facts
from digital_twin.modules.market_data.domain.external_data_fitness import evaluate_external_data_fitness
from digital_twin.modules.reasoning.domain.ontology_projection_input import compact_external_signals_for_ontology
from digital_twin.modules.reasoning.domain.ontology_external_abox import (
    add_portfolio_macro_and_cross_asset_concepts, external_observation_profile,
)
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.modules.portfolio.domain.valuation.dcf_inputs import _revenue_estimates
from digital_twin.modules.portfolio.domain.valuation.evidence import collect_earnings_observations, collect_multiple_observations
from digital_twin.modules.portfolio.domain.valuation.dcf import driver_dcf_valuation_row
from digital_twin.modules.portfolio.domain.portfolio import Position
from digital_twin.modules.portfolio.domain.valuation.projection import add_position_valuation_concepts
from digital_twin.modules.decisions.domain.notification_ai_context_router import _relation_facts


class DataUtilizationContractTests(unittest.TestCase):
    def test_measured_macro_values_and_clocks_survive_projection(self):
        source = {"fetchedAt": "2026-10-01T00:00:00Z", "macro": {
            "sourceAsOf": "2026-08",  # Another provider's aggregate clock.
            "yieldSpread10y2y": 0.3, "yieldSpreadObservationDate": "2026-09-28",
            "series": {"DGS10": {"value": 4.5, "delta5dBp": 28.0, "delta20dBp": 51.0,
                "deltaBp": 0.0, "sourceAsOf": "2026-09-28", "unit": "percent",
                "comparison5dDate": "2026-09-21"},
                "KR_RETAIL_SALES": {"value": 103, "unit": "2020=100", "deltaPct": 0.0,
                    "yearOverYearPct": -2.3, "yearAgoPeriod": "2025-08", "sourceAsOf": "2026-08"}}}}
        original = deepcopy(source)
        compact = compact_external_signals_for_ontology(source)
        self.assertEqual(interest_rate_facts(source), interest_rate_facts(compact))
        facts = interest_rate_facts(compact)
        ai = _relation_facts({"relationFacts": facts}, [], [])
        self.assertEqual(28.0, ai["macroDgs10Delta5dBp"])
        self.assertEqual(51.0, ai["macroDgs10Delta20dBp"])
        self.assertEqual("2026-09-28", ai["macroDgs10ObservationDate"])
        graph = PortfolioOntology("fixture")
        add_portfolio_macro_and_cross_asset_concepts(graph, "fixture", compact)
        rate = next(row.properties for row in graph.entities if row.properties.get("seriesId") == "DGS10")
        retail = next(row.properties for row in graph.entities if row.properties.get("seriesId") == "KR_RETAIL_SALES")
        spread = next(row.properties for row in graph.entities if row.kind == "yield-curve")
        self.assertEqual((28.0, 51.0, 0.0), (rate["delta5dBp"], rate["delta20dBp"], rate["deltaBp"]))
        self.assertEqual(("2020=100", 0.0, -2.3, "2025-08"),
                         (retail["unit"], retail["deltaPct"], retail["yearOverYearPct"], retail["yearAgoPeriod"]))
        self.assertEqual("2026-09-28", spread["sourceAsOf"])
        self.assertEqual(original, source)

    def test_missing_is_not_zero_or_recently_observed(self):
        empty = interest_rate_facts({})
        self.assertNotIn("macroDgs10Delta5dBp", empty)
        self.assertNotIn("macroDgs10", empty)
        zero = interest_rate_facts({"macro": {"series": {"DGS10": {"value": 0, "delta5dBp": 0}}}})
        self.assertEqual(0, zero["macroDgs10Delta5dBp"])
        self.assertTrue(zero["hasInterestRateSignals"])
        observation = external_observation_profile({"fetchedAt": "2026-10-01T00:00:00Z",
            "freshness": {"status": "fresh", "sourceAsOf": "2026-10-01", "ageMinutes": 0}}, {"value": 4})
        self.assertFalse(observation["sourceTimestampPresent"])
        self.assertEqual("", observation["sourceAsOf"])
        self.assertEqual("unknown", observation["freshnessStatus"])
        self.assertIsNone(observation["freshnessAgeMinutes"])

    def test_consensus_preserves_vendor_period_and_currency_without_guessing(self):
        payload = {"earningsEstimate": [{"period": "0y", "avg": 2}],
                   "revenueEstimate": [{"period": "0y", "avg": 100}],
                   "collectedAt": "2026-10-01", "info": {"currency": "KRW"}}
        bind_yfinance_estimate_metadata(payload, [{"period": "0y", "endDate": "2027-03-31",
            "earningsEstimate": {"earningsCurrency": "USD"},
            "revenueEstimate": {"revenueCurrency": {"raw": "USD"}}}])
        row = normalized_yfinance_earnings_estimates(payload, payload["collectedAt"])[0]
        self.assertEqual("2027-03-31", row["targetPeriodEnd"])
        self.assertEqual("USD", row["currency"])
        self.assertEqual("", row["sourceAsOf"])
        self.assertEqual(["sourceAsOf"], row["missingFields"])
        self.assertEqual("USD", payload["revenueEstimate"][0]["currency"])
        changed = deepcopy(payload)
        changed["earningsEstimate"][0]["targetPeriodEnd"] = "2028-03-31"
        self.assertNotEqual(row["observationId"], normalized_yfinance_earnings_estimates(changed, "2026-10-01")[0]["observationId"])
        unbound = normalized_yfinance_earnings_estimates({"earningsEstimate": [{"period": "0y", "avg": 2}],
            "info": {"currency": "KRW"}}, "2026-10-01")[0]
        self.assertEqual("", unbound["currency"])
        revenue = _revenue_estimates({"info": {"currency": "USD"}, "fastInfo": {"currency": "USD"},
            "revenueEstimate": [{"period": "0y", "avg": 100}]})
        self.assertEqual("", revenue["0y"]["currency"])
        self.assertEqual("missing", revenue["0y"]["currencyBasis"])
        reference = {"datasetId": "yfinance.analyst", "revisionId": "original-revision"}
        current = {**reference, "revisionId": "newer-revision"}
        estimate = {**row, "sourceReferences": [reference], "validationState": "observed-partial-inputs"}
        overview = {"provider": "yfinance", "fetchedAt": "2026-10-01", "earningsEstimates": [estimate],
                    "multipleObservations": [{"value": 20, "basis": "historical", "asOf": "2026-09-01"}],
                    "growthData": {"revenueGrowthPct": 3, "asOf": "2026-09-01"}}
        source = {"companyOverviews": {"TEST": overview}, "earningsReports": {"TEST": deepcopy(overview)}}
        compact = compact_external_signals_for_ontology(source)
        before = collect_earnings_observations(overview, overview, source_references=[current])
        after = collect_earnings_observations(compact["companyOverviews"]["TEST"], compact["earningsReports"]["TEST"], source_references=[current])
        self.assertEqual(before, after)
        self.assertEqual("", after[0]["asOf"])
        self.assertEqual([reference], after[0]["sourceReferences"])
        self.assertEqual(["sourceAsOf"], after[0]["missingFields"])
        self.assertEqual(collect_multiple_observations(overview, overview),
                         collect_multiple_observations(compact["companyOverviews"]["TEST"], compact["earningsReports"]["TEST"]))
        self.assertEqual(overview["growthData"], compact["companyOverviews"]["TEST"]["growthData"])
        alternate = {**estimate, "targetPeriodEnd": "2028-03-31"}
        self.assertEqual(2, len(collect_earnings_observations({"earningsEstimates": [estimate, alternate]}, {})))
        compact["companyOverviews"]["TEST"]["earningsEstimates"][0]["base"] = 999
        self.assertEqual(estimate, source["companyOverviews"]["TEST"]["earningsEstimates"][0])

        from test_driver_dcf import DriverDcfTests
        bundle = DriverDcfTests().inputs()
        bundle["modelApprovalState"] = "shadow"
        source = {"driverDcfInputs": {"TEST": bundle, "OTHER": bundle},
                  "driverDcfReadiness": {"TEST": {"status": "ready-for-shadow", "decisionEligible": False}}}
        compact = compact_external_signals_for_ontology(source, target_symbols=["TEST"])
        self.assertEqual({"TEST"}, set(compact["driverDcfInputs"]))
        position = Position("TEST", "Fixture", currency="USD", current_price=12)
        self.assertEqual(driver_dcf_valuation_row(position, source, {}), driver_dcf_valuation_row(position, compact, {}))
        graph = PortfolioOntology("fixture")
        add_position_valuation_concepts(graph, "stock:TEST", position, compact, {})
        dcf = [node for node in graph.entities if node.kind == "valuation-assessment"]
        self.assertTrue(dcf)
        self.assertTrue(all(not node.properties.get("valuationDecisionEligible") for node in dcf))
        from digital_twin.modules.reasoning.domain.projection_facts import graph_for_graph_store_persistence
        from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, OntologyRelation
        graph = PortfolioOntology("persisted-valuation-fixture")
        graph.entities.append(OntologyEntity("stock:TEST", "Fixture", "stock", {"ontologyBox": "ABox"}))
        add_position_valuation_concepts(graph, "stock:TEST", position,
            {**compact, "companyOverviews": {"TEST": {**overview, "currency": "USD"}}}, {})
        model = next(node for node in graph.entities if node.kind == "valuation-model")
        graph.entities.extend([
            OntologyEntity("foreign-assumption", "Foreign", "valuation-assumption", {"ontologyBox": "ABox"}),
            OntologyEntity("foreign-eps", "Foreign", "earnings-scenario-observation", {"ontologyBox": "ABox"}),
        ])
        graph.relations.extend([
            OntologyRelation("foreign-assumption", model.entity_id, "USES_VALUATION_MODEL"),
            OntologyRelation(model.entity_id, "foreign-eps", "USES_EARNINGS_SCENARIO"),
            OntologyRelation("foreign-assumption", "foreign-eps", "USES_EARNINGS_SCENARIO"),
        ])
        persisted = graph_for_graph_store_persistence(graph, {"inputRelationTypes": ["HAS_VALUATION"]})
        by_id = {node.entity_id: node for node in persisted.entities}
        self.assertNotIn("foreign-assumption", by_id)
        self.assertNotIn("foreign-eps", by_id)
        for kind in ("valuation-input-bundle", "earnings-scenario-observation", "valuation-calculation-trace"):
            expected = [node for node in graph.entities if node.kind == kind and node.entity_id != "foreign-eps"]
            self.assertTrue(expected, kind)
            for node in expected:
                self.assertEqual(node.properties, by_id[node.entity_id].properties)
        self.assertTrue(all(not node.properties.get("valuationDecisionEligible") for node in persisted.entities
                            if node.kind == "valuation-assessment"))
        from digital_twin.modules.reasoning.domain.verified_snapshot_reasoning import (
            _external_for_symbol, _changed_external_groups, _reasoning_external_groups, _fact_types_for_change,
        )
        changed = deepcopy(compact)
        changed["driverDcfInputs"]["TEST"]["waccPct"] = 12
        groups = _changed_external_groups(_external_for_symbol(compact, "TEST"), _external_for_symbol(changed, "TEST"))
        self.assertEqual(["driverDcfInputs"], _reasoning_external_groups(groups))
        self.assertEqual(["ValuationObservation"], _fact_types_for_change([], groups))
        refreshed = deepcopy(compact)
        refreshed["driverDcfInputs"]["TEST"].update(valuationAt="2026-10-02", inputBundleId="new-audit-id")
        self.assertEqual([], _changed_external_groups(_external_for_symbol(compact, "TEST"), _external_for_symbol(refreshed, "TEST")))
        self.assertEqual([], _reasoning_external_groups(_changed_external_groups({}, {"companyOverviews": {"TEST": {"currentPrice": 12}}})))
        changed = {"companyOverviews": {"TEST": {"earningsEstimates": [estimate]}}}
        groups = _reasoning_external_groups(_changed_external_groups({}, changed))
        self.assertEqual(["companyOverviews.valuation"], groups)
        self.assertEqual(["ValuationObservation"], _fact_types_for_change([], groups))

    def test_lineage_distinguishes_current_only_from_retained_revision(self):
        rows = [MySQLExternalDataStore._fact_row({"dataset_id": "yfinance.price", "subject_key": "TEST",
            "revision_id": "revision-1", "revision_available": exists,
            "payload_json": '{"equityQuotes":{"TEST":{"price":10}}}',
            "expires_at": "2099-01-01T00:00:00Z"}) for exists in [0, 1]]
        class Store:
            def list_current(self, subjects): return [self.row]
            def provider_statuses(self): return []
        store = Store()
        for row, expected in zip(rows, ["current-only", "retained"]):
            store.row = row
            signals = ExternalSignalsReadModelService(store).signals_for_subjects(["TEST"])
            compact = compact_external_signals_for_ontology(signals)
            self.assertEqual(expected, compact["externalDataLineage"]["yfinance.price:TEST"]["revisionPersistence"])
        lineage = {f"dataset-{i}:{symbol}": {"datasetId": f"dataset-{i}", "subjectKey": symbol,
                    "revisionId": f"revision-{symbol}-{i}"} for i in range(20) for symbol in ["AAA", "BBB", "CCC", "DDD", "EEE"]}
        self.assertEqual(lineage, compact_external_signals_for_ontology({"externalDataLineage": lineage})["externalDataLineage"])
        target = compact_external_signals_for_ontology({"externalDataLineage": lineage}, target_symbols=["EEE"])
        self.assertEqual(20, len(target["externalDataLineage"]))
        self.assertTrue(all(row["subjectKey"] == "EEE" for row in target["externalDataLineage"].values()))

    def test_collection_freshness_does_not_claim_complete_consensus(self):
        fitness = evaluate_external_data_fitness([{"datasetId": "yfinance.analyst", "subjectKey": "TEST",
            "freshnessState": "fresh", "payloadPresent": True,
            "quality": {"dataUsable": True, "missingFields": ["sourceAsOf", "currency"]}}], [],
            [{"datasetId": "yfinance.analyst", "enabled": True}], ["TEST"])
        row = fitness["subjects"]["TEST"]["purposes"]["analyst-consensus"]
        self.assertEqual("partial", row["state"])
        self.assertEqual(["currency", "sourceAsOf"], row["missingFields"])
