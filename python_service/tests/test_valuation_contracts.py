import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.modules.instruments.domain.instrument_profiles import instrument_profile_for_position
from digital_twin.modules.news_intelligence.domain.company_knowledge import build_company_knowledge
from digital_twin.modules.market_data.domain.market_data import known_stock, normalize_position
from digital_twin.modules.portfolio.domain.portfolio import Position
from digital_twin.modules.reasoning.domain.ontology_relation_facts import position_signal_facts
from digital_twin.modules.reasoning.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.modules.portfolio.domain.portfolio_calculations import portfolio_summary
from digital_twin.modules.portfolio.domain.valuation.projection import external_valuation_rows, valuation_values
from digital_twin.modules.portfolio.domain.valuation_ai_proposals import ai_valuation_proposal_rows
from digital_twin.modules.notifications.application.notification_ai_gate_message import compact_valuation_detail_rows
from digital_twin.modules.portfolio.domain.valuation_contracts import (
    annual_eps_observation,
    fair_value_scenarios,
    valuation_decision_eligible,
)
from digital_twin.modules.portfolio.domain.valuation_model_evidence import (
    FUNDAMENTAL_MODEL_VERSION,
    collect_earnings_observations,
    collect_multiple_observations,
    earnings_scenario,
    fair_value_from_evidence,
    multiple_evidence_band,
)
from digital_twin.modules.portfolio.domain.valuation import (
    VALUATION_MODEL_SERVICE_VERSION,
    ValuationModelRequest,
    ValuationModelService,
    apply_valuation_quality_gate,
    normalize_dividend_yield,
)
from digital_twin.modules.portfolio.domain.valuation.models import convert_cross_listed_valuation_inputs
from digital_twin.modules.portfolio.domain.valuation.projection import add_valuation_row_concepts, quality_checked_valuation_row


class ValuationContractTests(unittest.TestCase):
    def _assert_dividend_yield_requires_an_explicit_and_valid_unit(self):
        percent = normalize_dividend_yield(1.23, "percent")
        ratio = normalize_dividend_yield(0.0123, "ratio")
        invalid = normalize_dividend_yield(1.23, "ratio")

        self.assertEqual("valid", percent.status)
        self.assertEqual(0.0123, percent.ratio)
        self.assertEqual(1.23, percent.percent)
        self.assertEqual(percent.ratio, ratio.ratio)
        self.assertEqual("invalid", invalid.status)

    def _assert_company_knowledge_preserves_canonical_dividend_yield_units(self):
        knowledge = build_company_knowledge(
            "AAPL",
            overview={
                "provider": "yfinance",
                "fetchedAt": "2026-09-08T00:00:00Z",
                "dividendYield": 1.23,
                "dividendYieldUnit": "percent",
                "peRatio": 28.0,
            },
        )

        self.assertEqual(0.0123, knowledge["valuation"]["dividendYield"])
        self.assertEqual(1.23, knowledge["valuation"]["dividendYieldPct"])
        self.assertEqual("ratio", knowledge["valuationUnits"]["dividendYield"])
        self.assertEqual("percent", knowledge["valuationUnits"]["dividendYieldSourceUnit"])

    def _assert_quality_gate_blocks_invalid_scenario_order(self):
        checked = apply_valuation_quality_gate({
            "currentPrice": 100,
            "fairValueLow": 130,
            "fairValue": 120,
            "fairValueHigh": 160,
            "valuationInputState": "sufficient",
            "valuationFreshnessStatus": "fresh",
            "valuationDecisionEligible": True,
        })

        self.assertEqual("blocked", checked["valuationQualityStatus"])
        self.assertFalse(checked["valuationDecisionEligible"])
        self.assertIn(
            "invalid-scenario-order",
            {issue["code"] for issue in checked["valuationQualityIssues"]},
        )
        bootstrap = apply_valuation_quality_gate({
            "currentPrice": 100,
            "fairValueLow": 80,
            "fairValue": 120,
            "fairValueHigh": 160,
            "valuationInputState": "sufficient",
            "valuationFreshnessStatus": "fresh",
            "valuationDecisionEligible": True,
            "valuationReferenceOnly": True,
            "multipleBand": {
                "basis": "bootstrap-prior", "evidenceBacked": False,
                "comparabilityState": "insufficient-comparable-samples",
            },
        })
        self.assertFalse(bootstrap["valuationDecisionEligible"])
        self.assertEqual(
            {"unverified-target-multiple", "reference-only-valuation"},
            {issue["code"] for issue in bootstrap["valuationQualityIssues"]},
        )

    def _assert_projection_quality_gate_blocks_stale_eligible_valuation(self):
        position = Position(symbol="AAPL", name="Apple", current_price=100, currency="USD")
        _row, values = quality_checked_valuation_row({
            "currentPrice": 100,
            "fairValueLow": 110,
            "fairValue": 120,
            "fairValueHigh": 130,
            "valuationInputState": "sufficient",
            "valuationFreshnessStatus": "stale",
            "valuationDecisionEligible": True,
        }, position)

        self.assertEqual("blocked", values["valuationQualityStatus"])
        self.assertFalse(values["valuationDecisionEligible"])
        self.assertIn(
            "unusable-freshness",
            {issue["code"] for issue in values["valuationQualityIssues"]},
        )

    def _assert_model_service_owns_calculation_but_never_emits_an_action(self):
        position = Position(
            symbol="035720",
            name="카카오",
            market="KR",
            currency="KRW",
            current_price=34900,
        )
        result = ValuationModelService().evaluate(ValuationModelRequest(
            position=position,
            external_signals={
                "companyOverviews": {
                    "035720": {
                        "provider": "KIS Open API",
                        "trailingEPS": 1110,
                        "epsPeriod": "annual",
                        "peRatio": 31.13,
                    }
                }
            },
        ))

        self.assertEqual("calculated", result.status)
        self.assertEqual(VALUATION_MODEL_SERVICE_VERSION, result.model_service_version)
        self.assertEqual(1, len(result.rows))
        self.assertEqual("growth-quality-earnings", result.rows[0]["valuationModelId"])
        self.assertEqual("growth", result.rows[0]["valuationModelFamily"])
        self.assertEqual("valuation-bounded-context", result.rows[0]["calculationOwner"])
        self.assertEqual(VALUATION_MODEL_SERVICE_VERSION, result.rows[0]["valuationModelServiceVersion"])
        for action_key in ("action", "recommendedAction", "decision", "buy", "sell"):
            self.assertNotIn(action_key, result.rows[0])

    def test_quarterly_eps_is_not_combined_with_annual_per(self):
        observation = annual_eps_observation(
            {},
            {
                "latestQuarter": {
                    "reportedEPS": 2500,
                    "epsPeriod": "quarterly",
                    "fiscalDateEnding": "2026-06-30",
                }
            },
        )

        self.assertEqual({}, observation)
        self.assertEqual({}, fair_value_scenarios(2500, "quarterly", [8, 12, 16]))

    def test_annual_eps_creates_bear_base_bull_range(self):
        values = fair_value_scenarios(10000, "annual", [8, 12, 16])

        self.assertEqual(80000, values["fairValueLow"])
        self.assertEqual(120000, values["fairValueBase"])
        self.assertEqual(160000, values["fairValueHigh"])
        self._assert_reported_consensus_range_is_used_without_synthetic_eps_stress()

    def _assert_reported_consensus_range_is_used_without_synthetic_eps_stress(self):
        scenario = earnings_scenario([
            {
                "observationId": "consensus:fy1",
                "provider": "yfinance",
                "period": "fy1",
                "low": 900,
                "base": 1000,
                "high": 1150,
                "analystCount": 18,
                "sourceReferences": [{"datasetId": "yfinance.analyst", "revisionId": "consensus-r1"}],
            }
        ])

        self.assertEqual(900, scenario["low"])
        self.assertEqual(1000, scenario["base"])
        self.assertEqual(1150, scenario["high"])
        self.assertEqual("reported-consensus-range", scenario["method"])
        self.assertTrue(scenario["scenarioComplete"])
        self.assertEqual("consensus-r1", scenario["sourceReferences"][0]["revisionId"])
        point_range = earnings_scenario([
            {"observationId": "vendor:a", "provider": "a", "period": "fy1", "base": 900},
            {"observationId": "vendor:b", "provider": "b", "period": "fy1", "base": 1100},
        ])
        self.assertEqual("observed-point-range", point_range["method"])
        self.assertEqual("not-provided", point_range["analystCountState"])
        self.assertNotIn("analystCount", point_range)

    def test_target_multiple_band_requires_historical_or_peer_evidence(self):
        observations = [
            {
                "observationId": f"per:{value}", "provider": "Primary Research", "basis": "peer", "value": value,
                "issuer": f"PEER{index}", "securityLine": "common", "multipleMetric": "per",
                "earningsHorizon": "fy1", "accountingBasis": "US-GAAP", "epsBasis": "diluted",
                "priceAsOf": "2026-09-24", "earningsAsOf": "2026-12-31",
                "upstreamOrigin": f"peer-dataset:{index}", "comparabilityState": "verified",
                "freshnessState": "fresh", "currency": "USD",
                "comparisonFactors": {"revenueGrowthPct": 20 + index, "operatingMarginPct": 18 + index},
            }
            for index, value in enumerate((8, 10, 12, 16), start=1)
        ]
        earnings = {
            "period": "fy1", "epsBasis": "diluted", "accountingBasis": "US-GAAP",
            "securityLine": "common", "currency": "USD",
        }
        historical = [
            {
                **copy.deepcopy(observations[index]),
                "observationId": f"historical:{index}", "basis": "historical",
                "issuer": "TARGET", "value": value,
                "priceAsOf": f"202{index + 2}-12-31", "earningsAsOf": f"202{index + 2}-12-31",
                "upstreamOrigin": f"historical-dataset:{index}",
            }
            for index, value in enumerate((6, 7, 9))
        ]
        band = multiple_evidence_band(observations + historical, {"SemiconductorHBM"}, earnings=earnings)

        self.assertEqual(9.5, band["low"])
        self.assertEqual(11, band["base"])
        self.assertEqual(13, band["high"])
        self.assertEqual(4, band["sampleCount"])
        self.assertEqual("peer", band["basis"])
        self.assertEqual("comparable", band["comparabilityState"])
        self.assertEqual(4, band["componentBands"]["peer"]["sampleCount"])
        self.assertEqual(3, band["componentBands"]["historical"]["sampleCount"])
        self.assertEqual(["peer", "historical"], band["selectionAssumption"]["priority"])
        self.assertTrue(all(item["state"] == "included" for item in band["selectionLedger"]))
        self.assertTrue(band["evidenceBacked"])

        incompatible = copy.deepcopy(observations)
        incompatible[0]["earningsHorizon"] = "ttm"
        incompatible[0]["priceAsOf"] = "not-a-date"
        incompatible[1]["freshnessState"] = "stale"
        incompatible[2]["epsBasis"] = "basic"
        incompatible[2]["freshnessState"] = "unknown"
        incompatible.append({**copy.deepcopy(observations[3]), "observationId": "duplicate", "provider": "Mirror API"})
        blocked = multiple_evidence_band(incompatible, {"SemiconductorHBM"}, earnings=earnings)
        self.assertFalse(blocked["evidenceBacked"])
        exclusion_reasons = {
            reason for item in blocked["selectionLedger"] for reason in item["reasons"]
        }
        self.assertIn("incompatible-earnings-horizon", exclusion_reasons)
        self.assertIn("invalid-price-as-of", exclusion_reasons)
        self.assertIn("stale-multiple-observation", exclusion_reasons)
        self.assertIn("incompatible-eps-basis", exclusion_reasons)
        self.assertIn("unverified-freshness", exclusion_reasons)
        self.assertIn("duplicate-observation", exclusion_reasons)

        conflict = copy.deepcopy(observations)
        conflict.append({**copy.deepcopy(observations[0]), "observationId": "conflict", "value": 99})
        conflict_band = multiple_evidence_band(conflict, {"SemiconductorHBM"}, earnings=earnings)
        self.assertTrue(conflict_band["evidenceBacked"])
        self.assertEqual(3, conflict_band["sampleCount"])
        self.assertEqual(
            2,
            sum(
                "conflicting-duplicate-observation" in item["reasons"]
                for item in conflict_band["selectionLedger"]
            ),
        )
        raw_conflicts = collect_multiple_observations({
            "provider": "Primary Research", "symbol": "PEER1", "securityLine": "common",
            "multipleObservations": [
                {key: value for key, value in observations[0].items() if key != "observationId"},
                {
                    **{key: value for key, value in observations[0].items() if key != "observationId"},
                    "value": 99,
                },
            ],
        }, {})
        self.assertEqual(2, len(raw_conflicts))
        raw_conflict_band = multiple_evidence_band(raw_conflicts, {"SemiconductorHBM"}, earnings=earnings)
        self.assertEqual(
            2,
            sum(
                "conflicting-duplicate-observation" in item["reasons"]
                for item in raw_conflict_band["selectionLedger"]
            ),
        )

        prior = multiple_evidence_band([], {"SemiconductorHBM"}, earnings=earnings)
        self.assertEqual([8, 12, 16], [prior["low"], prior["base"], prior["high"]])
        self.assertEqual("bootstrap-prior", prior["basis"])
        self.assertFalse(prior["evidenceBacked"])
        self.assertTrue(prior["referenceOnly"])
        self.assertEqual("insufficient", prior["confidence"])

    def test_evidence_model_multiplies_observed_eps_and_per_bounds(self):
        values = fair_value_from_evidence(
            {"low": 900, "base": 1000, "high": 1100},
            {"low": 20, "base": 25, "high": 30},
        )

        self.assertEqual(18000, values["fairValueLow"])
        self.assertEqual(25000, values["fairValue"])
        self.assertEqual(33000, values["fairValueHigh"])
        self.assertEqual(
            ["bear", "base", "bull"],
            [item["scenario"] for item in values["scenarioAssumptions"]],
        )
        self.assertEqual(900, values["scenarioAssumptions"][0]["eps"])
        self.assertEqual(20, values["scenarioAssumptions"][0]["multiple"])

    def test_official_company_knowledge_can_derive_annual_eps(self):
        observations = collect_earnings_observations(
            {},
            {},
            {
                "financials": {"annual": [{"period": "2025", "netIncome": 1200000}]},
                "capital": {"sharesOutstanding": 1000},
                "provenance": [{"provider": "OpenDART", "asOf": "2026-03-31"}],
            },
        )

        self.assertEqual(1, len(observations))
        self.assertEqual(1200, observations[0]["base"])
        self.assertEqual("official", observations[0]["sourceType"])
        self.assertEqual("companyKnowledge.netIncome/currentSharesOutstanding", observations[0]["source"])
        self.assertEqual("approximate-current-share-count", observations[0]["calculationMethod"])
        self.assertEqual("reference-only", observations[0]["validationState"])
        self.assertFalse(observations[0]["positivePerEligible"])
        self.assertIn("current-share-count-is-not-weighted-average", observations[0]["excludedReasons"])
        self.assertEqual({}, earnings_scenario(observations))
        self.assertNotIn("analystCount", observations[0])
        self.assertNotIn("revision30dPct", observations[0])
        self._assert_verified_reported_and_reconstructed_eps_are_distinct()
        self._assert_dividend_yield_requires_an_explicit_and_valid_unit()
        self._assert_company_knowledge_preserves_canonical_dividend_yield_units()
        self._assert_legacy_consensus_without_exact_revision_remains_unverified()
        self._assert_same_upstream_consensus_is_not_counted_as_independent_evidence()

    def _assert_verified_reported_and_reconstructed_eps_are_distinct(self):
        reference = {
            "contractVersion": "external-source-reference-v1",
            "datasetId": "sec.company_facts",
            "revisionId": "sec-r1",
        }
        common_source = {
            "provider": "SEC EDGAR", "period": "2025-12-31", "scope": "consolidated",
            "durationBasis": "annual", "official": True,
            "securityLine": "issuer-common", "splitAdjustmentState": "source-adjusted", "adrRatio": 1,
        }
        annual = {
            "period": "2025-12-31", "periodEnd": "2025-12-31", "frequency": "annual",
            "provider": "SEC EDGAR", "netIncomeCommon": 1200000,
            "weightedAverageSharesDiluted": 1000, "dilutedEPS": 1200,
            "metricProvenance": {
                "netIncomeCommon": {
                    **common_source, "currency": "USD", "attributionScope": "common-stockholders",
                },
                "weightedAverageSharesDiluted": {
                    **common_source, "currency": "shares", "shareCountBasis": "weighted-average-diluted",
                },
                "dilutedEPS": {
                    **common_source, "currency": "USD/shares", "perShareBasis": "diluted",
                },
            },
            "reportContract": {
                "contractVersion": "financial-report-observation-v1", "frequency": "annual",
                "periodEnd": "2025-12-31", "revisionState": "immutable-source-bound",
                "sourceReferences": [reference],
            },
        }
        company = {
            "financials": {"annual": [annual]},
            "capital": {"sharesOutstanding": 700},
            "provenance": [{"provider": "SEC EDGAR", "asOf": "2026-02-01"}],
        }
        observations = collect_earnings_observations({}, {}, company)
        by_method = {item["calculationMethod"]: item for item in observations}
        self.assertEqual({"reported", "derived-reported-components"}, set(by_method))
        self.assertEqual("diluted", by_method["reported"]["epsBasis"])
        self.assertEqual("verified-reported", by_method["reported"]["validationState"])
        self.assertEqual("verified-reconstruction", by_method["derived-reported-components"]["validationState"])
        self.assertEqual("reconciled", by_method["derived-reported-components"]["reportedComparison"]["status"])
        self.assertEqual("netIncomeCommon / weightedAverageSharesDiluted", by_method["derived-reported-components"]["formula"])
        self.assertEqual("sec-r1", by_method["reported"]["sourceReferences"][0]["revisionId"])
        self.assertEqual(1200, earnings_scenario(observations)["base"])

        legacy_report = collect_earnings_observations({}, {
            "provider": "Alpha Vantage",
            "sourceReferences": [reference],
            "latestAnnual": {"reportedEPS": 5, "epsPeriod": "annual", "fiscalDateEnding": "2025-12-31"},
        })
        self.assertEqual("reference-only", legacy_report[0]["validationState"])
        self.assertIn("unverified-per-share-basis", legacy_report[0]["excludedReasons"])
        self.assertEqual({}, earnings_scenario(legacy_report))
        qualified_report = collect_earnings_observations({}, {
            "provider": "Alpha Vantage",
            "sourceReferences": [reference],
            "latestAnnual": {
                "reportedEPS": 5, "epsPeriod": "annual", "epsBasis": "diluted",
                "fiscalDateEnding": "2025-12-31",
            },
        })
        self.assertEqual("verified-reported", qualified_report[0]["validationState"])
        self.assertTrue(qualified_report[0]["positivePerEligible"])

        for label, mutate, expected_reason in (
            (
                "scope", lambda row: row["metricProvenance"]["weightedAverageSharesDiluted"].update({"scope": "parent-only"}),
                "incompatible-scope",
            ),
            (
                "adr", lambda row: row["metricProvenance"]["weightedAverageSharesDiluted"].update({"adrRatio": 2}),
                "incompatible-adrRatio",
            ),
            (
                "split", lambda row: row["metricProvenance"]["weightedAverageSharesDiluted"].update({"splitAdjustmentState": "unadjusted"}),
                "incompatible-splitAdjustmentState",
            ),
        ):
            with self.subTest(component_mismatch=label):
                invalid = copy.deepcopy(company)
                mutate(invalid["financials"]["annual"][0])
                derived = next(
                    item for item in collect_earnings_observations({}, {}, invalid)
                    if item.get("calculationMethod") == "derived-reported-components"
                )
                self.assertEqual("reference-only", derived["validationState"])
                self.assertFalse(derived["positivePerEligible"])
                self.assertIn(expected_reason, derived["excludedReasons"])

        mismatch = copy.deepcopy(company)
        mismatch["financials"]["annual"][0]["dilutedEPS"] = 1000
        derived = next(
            item for item in collect_earnings_observations({}, {}, mismatch)
            if item.get("calculationMethod") == "derived-reported-components"
        )
        self.assertEqual("mismatch", derived["reportedComparison"]["status"])
        self.assertIn("reported-eps-mismatch", derived["excludedReasons"])

        negative = copy.deepcopy(company)
        negative["financials"]["annual"][0]["netIncomeCommon"] = -1200000
        negative["financials"]["annual"][0]["dilutedEPS"] = -1200
        negative_rows = collect_earnings_observations({}, {}, negative)
        self.assertTrue(all(item["base"] == -1200 for item in negative_rows))
        self.assertTrue(all(not item["positivePerEligible"] for item in negative_rows))
        self.assertTrue(all("non-positive-eps-for-per" in item["excludedReasons"] for item in negative_rows))

        converted = convert_cross_listed_valuation_inputs(
            {"fairValueLow": 180000, "fairValue": 200000, "fairValueHigh": 220000},
            {"low": 9000, "base": 10000, "high": 11000, "currency": "KRW"},
            source_symbol="000660", target_symbol="SKHY", source_currency="KRW",
            target_currency="USD", adr_ratio=0.1, fx_rate=1000,
        )
        self.assertEqual("applied", converted["status"])
        self.assertEqual(20, converted["scenarios"]["fairValue"])
        self.assertEqual(1, converted["eps"]["base"])
        self.assertEqual("localValue * adrRatio / usdkrw", converted["trace"]["formula"])
        self.assertEqual("applied", converted["eps"]["securityAdjustmentState"])

        for label, eps, source_currency, target_currency, reason in (
            ("double", {"base": 1, "securityAdjustmentState": "applied"}, "KRW", "USD", "security-adjustment-already-applied"),
            ("direction", {"base": 1}, "USD", "KRW", "unsupported-currency-direction"),
            ("split", {"base": 1, "splitAdjustmentState": "requires-adjustment"}, "KRW", "USD", "split-adjustment-unresolved"),
        ):
            with self.subTest(security_conversion=label):
                blocked = convert_cross_listed_valuation_inputs(
                    {"fairValue": 20}, eps,
                    source_symbol="000660", target_symbol="SKHY",
                    source_currency=source_currency, target_currency=target_currency,
                    adr_ratio=0.1, fx_rate=1000,
                )
                self.assertEqual("blocked", blocked["status"])
                self.assertEqual({}, blocked["scenarios"])
                self.assertIn(reason, blocked["blockedReasons"])

    def _assert_legacy_consensus_without_exact_revision_remains_unverified(self):
        observations = collect_earnings_observations(
            {
                "provider": "yfinance",
                "earningsEstimates": [{"period": "fy1", "base": 2.0, "isEstimate": True}],
            },
            {},
        )

        self.assertEqual(1, len(observations))
        self.assertEqual("legacy-unverified", observations[0]["validationState"])
        self.assertEqual("missing-source-revision", observations[0]["revisionState"])

    def _assert_same_upstream_consensus_is_not_counted_as_independent_evidence(self):
        scenario = earnings_scenario([
            {
                "observationId": "api-a", "provider": "api-a", "upstreamOrigin": "same consensus",
                "period": "fy1", "base": 2.0, "analystCount": 12,
                "sourceReferences": [{"datasetId": "api.a", "revisionId": "r1", "providerRevision": "upstream-r1"}],
            },
            {
                "observationId": "api-b", "provider": "api-b", "upstreamOrigin": "same consensus",
                "period": "fy1", "base": 2.0, "analystCount": 12,
                "sourceReferences": [{"datasetId": "api.b", "revisionId": "r2", "providerRevision": "upstream-r1"}],
            },
        ])

        self.assertEqual(1, scenario["observationCount"])
        self.assertEqual(1, scenario["sourceCount"])
        self.assertEqual(2.0, scenario["base"])

    def test_semiconductor_ai_valuation_uses_eps_not_moving_average(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            current_price=210000,
            ma5=100000,
            ma20=500000,
            ma60=900000,
        )
        rows = ai_valuation_proposal_rows(
            position,
            {
                "companyOverviews": {
                    "000660": {"provider": "KIS Open API", "trailingEPS": 10000, "epsPeriod": "annual"}
                }
            },
            {},
        )

        self.assertEqual(1, len(rows))
        self.assertEqual(120000, rows[0]["fairValue"])
        self.assertEqual("ai-semiconductor-eps-per-scenarios", rows[0]["valuationMethod"])
        self.assertNotIn("이동평균", rows[0]["formula"])
        self.assertFalse(rows[0]["valuationDecisionEligible"])

    def test_kakao_uses_platform_profile_and_kis_eps_per_valuation(self):
        info = known_stock("035720")
        position = normalize_position({
            "symbol": "035720",
            "name": "카카오",
            "market": "KR",
            "currency": "KRW",
            "currentPrice": 34900,
        })
        rows = ai_valuation_proposal_rows(
            position,
            {
                "companyOverviews": {
                    "035720": {
                        "provider": "KIS Open API",
                        "trailingEPS": 1110,
                        "epsPeriod": "annual",
                        "peRatio": 31.13,
                        "pbr": 1.35,
                        "bps": 25625,
                    }
                }
            },
            {},
        )

        self.assertEqual("AI/플랫폼", info["sector"])
        self.assertIn("PlatformGrowth", instrument_profile_for_position(position).archetypes)
        self.assertEqual(1, len(rows))
        self.assertEqual("ai-growth-eps-per-scenarios", rows[0]["valuationMethod"])
        self.assertEqual(1110, rows[0]["expectedEPS"])
        self.assertEqual(31.13, rows[0]["peRatio"])
        self.assertEqual(26, rows[0]["targetPER"])
        self.assertGreater(rows[0]["fairValue"], 0)
        self.assertGreater(rows[0]["fairValueHigh"], rows[0]["fairValueLow"])

        facts = position_signal_facts(
            position,
            portfolio_summary([], account_cash=1000000),
            external_signals={
                "companyOverviews": {
                    "035720": {
                        "provider": "KIS Open API",
                        "trailingEPS": 1110,
                        "epsPeriod": "annual",
                        "peRatio": 31.13,
                        "pbr": 1.35,
                        "bps": 25625,
                    }
                }
            },
        )
        message_rows = compact_valuation_detail_rows(
            {"ontologyRelationContext": {"facts": facts}},
            "absolute_beginner",
        )
        message = "\n".join(message_rows)
        self.assertEqual(31.13, facts["valuationCurrentPER"])
        self.assertEqual(1110, facts["valuationExpectedEPS"])
        self.assertEqual(26, facts["valuationTargetPER"])
        self.assertIn("현재 PER 31.13배", message)
        self.assertIn("사용 EPS 1,110원", message)
        self.assertIn("기준 PER 26배", message)
        self._assert_model_service_owns_calculation_but_never_emits_an_action()

        position = Position(
            symbol="MSTR",
            name="Strategy",
            market="US",
            currency="USD",
            current_price=124.14,
        )

        values = valuation_values(
            {
                "currentPrice": 124.14,
                "expectedEPS": 3.07,
                "peRatio": 40.44,
                "missingInputs": ["expectedEPS", "현재 PER", "targetPER", "fairValue"],
            },
            position,
        )

        self.assertEqual(3.07, values["expectedEPS"])
        self.assertEqual(40.44, values["peRatio"])
        self.assertNotIn("expectedEPS", values["missingInputs"])
        self.assertNotIn("현재 PER", values["missingInputs"])
        self.assertIn("targetPER", values["missingInputs"])
        self.assertIn("fairValue", values["missingInputs"])

    def test_bitcoin_proxy_without_treasury_inputs_has_no_fair_value(self):
        position = Position(symbol="MSTR", name="Strategy", market="US", currency="USD", current_price=100)
        rows = ai_valuation_proposal_rows(
            position,
            {"cryptoMarkets": {"bitcoin": {"price": 65000, "change24h": 2.0}}},
            {},
        )

        self.assertEqual(1, len(rows))
        self.assertEqual(0, rows[0].get("fairValue", 0))
        self.assertIn("BTC 보유량", rows[0]["missingInputs"])

    def test_analyst_target_abox_does_not_create_margin_of_safety(self):
        position = Position(symbol="AAPL", name="Apple", market="US", currency="USD", current_price=100)
        graph = build_portfolio_ontology(
            [position],
            portfolio_summary([position], account_cash=1000, fx_rates={"USD": 1400}),
            external_signals={
                "companyOverviews": {
                    "AAPL": {
                        "provider": "yfinance",
                        "analystTargetPrice": 125,
                        "analystOpinionCount": 31,
                        "fetchedAt": "2026-08-12T00:00:00Z",
                    }
                }
            },
            portfolio_id="analyst-target-reference-test",
            runtime_context={"settings": {"aiValuationAutoProposalEnabled": "0"}},
        )

        self.assertFalse(any(entity.kind == "margin-of-safety" for entity in graph.entities))
        analyst = next(entity for entity in graph.entities if entity.kind == "analyst-revision")
        self.assertTrue(analyst.properties["valuationReferenceOnly"])
        self.assertFalse(analyst.properties["valuationDecisionEligible"])
        stock = next(entity for entity in graph.entities if entity.kind == "stock")
        add_valuation_row_concepts(graph, stock.entity_id, position, {
            "assumptionKey": "AAPL:invalid-scenario",
            "currentPrice": 100,
            "fairValueLow": 130,
            "fairValue": 120,
            "fairValueHigh": 160,
            "valuationInputState": "sufficient",
            "valuationFreshnessStatus": "fresh",
            "valuationDecisionEligible": True,
        })
        quality = next(entity for entity in graph.entities if entity.kind == "valuation-data-quality")
        self.assertEqual("blocked", quality.properties["valuationQualityStatus"])
        self.assertTrue(any(
            relation.relation_type == "HAS_DATA_QUALITY" and relation.target == quality.entity_id
            for relation in graph.relations
        ))
        self._assert_quality_gate_blocks_invalid_scenario_order()
        self._assert_projection_quality_gate_blocks_stale_eligible_valuation()


if __name__ == "__main__":
    unittest.main()
