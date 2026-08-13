import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.instrument_profiles import instrument_profile_for_position
from digital_twin.domain.market_data import known_stock, normalize_position
from digital_twin.domain.portfolio import Position
from digital_twin.domain.ontology_relation_facts import position_signal_facts
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.portfolio_ontology_valuation_concepts import external_valuation_rows, valuation_values
from digital_twin.domain.valuation_ai_proposals import ai_valuation_proposal_rows
from digital_twin.application.notification_ai_gate_message import compact_valuation_detail_rows
from digital_twin.domain.valuation_contracts import (
    annual_eps_observation,
    fair_value_scenarios,
    valuation_decision_eligible,
)
from digital_twin.domain.valuation_model_evidence import (
    FUNDAMENTAL_MODEL_VERSION,
    collect_earnings_observations,
    earnings_scenario,
    fair_value_from_evidence,
    multiple_evidence_band,
)


class ValuationContractTests(unittest.TestCase):
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

    def test_reported_consensus_range_is_used_without_synthetic_eps_stress(self):
        scenario = earnings_scenario([
            {
                "observationId": "consensus:fy1",
                "provider": "yfinance",
                "period": "fy1",
                "low": 900,
                "base": 1000,
                "high": 1150,
                "analystCount": 18,
            }
        ])

        self.assertEqual(900, scenario["low"])
        self.assertEqual(1000, scenario["base"])
        self.assertEqual(1150, scenario["high"])
        self.assertEqual("reported-consensus-range", scenario["method"])
        self.assertTrue(scenario["scenarioComplete"])

    def test_target_multiple_band_requires_historical_or_peer_evidence(self):
        observations = [
            {"observationId": f"per:{value}", "provider": "KIS Open API", "basis": "historical", "value": value}
            for value in (8, 10, 12, 16)
        ]
        band = multiple_evidence_band(observations, {"SemiconductorHBM"})

        self.assertEqual(9.5, band["low"])
        self.assertEqual(11, band["base"])
        self.assertEqual(13, band["high"])
        self.assertEqual(4, band["sampleCount"])
        self.assertTrue(band["evidenceBacked"])

        prior = multiple_evidence_band([], {"SemiconductorHBM"})
        self.assertEqual([8, 12, 16], [prior["low"], prior["base"], prior["high"]])
        self.assertEqual("bootstrap-prior", prior["basis"])
        self.assertFalse(prior["evidenceBacked"])
        self.assertEqual("insufficient", prior["confidence"])

    def test_evidence_model_multiplies_observed_eps_and_per_bounds(self):
        values = fair_value_from_evidence(
            {"low": 900, "base": 1000, "high": 1100},
            {"low": 20, "base": 25, "high": 30},
        )

        self.assertEqual(18000, values["fairValueLow"])
        self.assertEqual(25000, values["fairValue"])
        self.assertEqual(33000, values["fairValueHigh"])

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
        self.assertEqual("companyKnowledge.netIncome/sharesOutstanding", observations[0]["source"])

    def test_unreviewed_ai_proposal_cannot_drive_investment_decision(self):
        self.assertFalse(
            valuation_decision_eligible(
                source_type="ai",
                reliability_state="sufficient",
                approval_status="ai_applied_pending_review",
                freshness_status="fresh",
                period_compatible=True,
                fair_value=120000,
            )
        )
        self.assertTrue(
            valuation_decision_eligible(
                source_type="ai",
                reliability_state="sufficient",
                approval_status="user_approved",
                freshness_status="fresh",
                period_compatible=True,
                fair_value=120000,
            )
        )

    def test_unreviewed_ai_proposal_hides_fair_value_from_notification(self):
        message_rows = compact_valuation_detail_rows(
            {"ontologyRelationContext": {"facts": {
                "valuationIsAiGenerated": True,
                "valuationSourceType": "ai",
                "valuationRequiresUserApproval": True,
                "valuationDecisionEligible": False,
                "valuationFairValue": 61724,
                "valuationFairValueLow": 34962,
                "valuationFairValueHigh": 106473,
                "valuationMarginOfSafetyPct": -67.1,
                "valuationMissingInputs": ["매출 성장률", "영업이익률 전망", "피어 또는 과거 PER 범위"],
            }}},
            "absolute_beginner",
        )
        message = "\n".join(message_rows)

        self.assertIn("사용자 검토 전 AI 초안", message)
        self.assertIn("적정가·안전마진 숫자를 표시하지 않습니다", message)
        self.assertNotIn("61,724", message)

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

    def test_user_approval_cannot_promote_bootstrap_multiple_to_decision_input(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            current_price=210000,
        )
        row = ai_valuation_proposal_rows(
            position,
            {
                "companyOverviews": {
                    "000660": {
                        "provider": "KIS Open API",
                        "fetchedAt": "2026-08-12T00:00:00Z",
                        "forwardEPS": 10000,
                        "cycleData": [{"operatingIncomeGrowthPct": 12}],
                    }
                }
            },
            {"valuationReviewOverrides": "000660=user_approved"},
        )[0]

        self.assertEqual("bootstrap-prior", row["multipleBand"]["basis"])
        self.assertEqual("partial", row["valuationInputState"])
        self.assertFalse(row["valuationDecisionEligible"])

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

    def test_evidence_backed_proposal_is_versioned_and_traceable_in_abox(self):
        position = Position(
            symbol="035720",
            name="카카오",
            market="KR",
            currency="KRW",
            current_price=25000,
        )
        overview = {
            "provider": "KIS Open API",
            "fetchedAt": "2026-08-12T00:00:00Z",
            "peRatio": 30,
            "earningsEstimates": [{
                "observationId": "kis:eps:fy1",
                "provider": "KIS Open API",
                "source": "estimate-perform.output3",
                "period": "fy1",
                "asOf": "2026-08-12T00:00:00Z",
                "low": 900,
                "base": 1000,
                "high": 1100,
                "analystCount": 10,
                "isEstimate": True,
            }],
            "multipleObservations": [
                {
                    "observationId": f"kis:per:{year}",
                    "provider": "KIS Open API",
                    "source": "estimate-perform.output3",
                    "basis": "historical",
                    "period": str(year),
                    "asOf": "2026-08-12T00:00:00Z",
                    "value": value,
                }
                for year, value in ((2022, 20), (2023, 24), (2024, 28), (2025, 32))
            ],
            "growthData": [{
                "provider": "KIS Open API",
                "asOf": "2026-08-12T00:00:00Z",
                "revenueGrowthPct": 10,
                "operatingIncomeGrowthPct": 15,
            }],
        }
        settings = {"valuationReviewOverrides": "035720=user_approved"}
        external_signals = {"companyOverviews": {"035720": overview}}

        row = ai_valuation_proposal_rows(position, external_signals, settings)[0]

        self.assertEqual(FUNDAMENTAL_MODEL_VERSION, row["modelVersion"])
        self.assertEqual(26, row["targetPER"])
        self.assertEqual(26000, row["fairValue"])
        self.assertEqual("historical", row["multipleBand"]["basis"])
        self.assertTrue(row["multipleBand"]["evidenceBacked"])
        self.assertEqual("sufficient", row["valuationInputState"])
        self.assertTrue(row["valuationDecisionEligible"])
        self.assertNotIn("DGS10", str(row["formulaTrace"]))
        self.assertFalse(any(item.get("basis") == "current-market" for item in row["inputObservations"]))
        self.assertTrue(any(
            item.get("reason") == "target-multiple-basis-not-eligible"
            for item in row["formulaTrace"]["excludedObservations"]
        ))

        graph = build_portfolio_ontology(
            [position],
            portfolio_summary([position], account_cash=1000000),
            external_signals=external_signals,
            portfolio_id="valuation-trace-test",
            runtime_context={"settings": settings},
        )
        classes = {entity.properties.get("tboxClass") for entity in graph.entities}
        relation_types = {relation.relation_type for relation in graph.relations}
        self.assertIn("ValuationModelVersion", classes)
        self.assertIn("ValuationInputObservation", classes)
        self.assertIn("EarningsScenarioObservation", classes)
        self.assertIn("MultipleBandObservation", classes)
        self.assertIn("ValuationCalculationTrace", classes)
        self.assertIn("USES_VALUATION_INPUT", relation_types)
        self.assertIn("HAS_VALUATION_CALCULATION_TRACE", relation_types)
        self.assertIn("PRODUCES_VALUATION_ESTIMATE", relation_types)

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

    def test_analyst_target_is_reference_not_reproducible_fair_value(self):
        position = Position(symbol="AAPL", name="Apple", market="US", currency="USD", current_price=100)
        external_signals = {
            "companyOverviews": {
                "AAPL": {
                    "provider": "yfinance",
                    "analystTargetPrice": 300,
                    "analystTargetLowPrice": 250,
                    "analystTargetHighPrice": 350,
                    "analystOpinionCount": 20,
                    "fetchedAt": "2026-07-20T00:00:00Z",
                }
            }
        }
        row = external_valuation_rows(external_signals, "AAPL")[0]
        values = valuation_values(row, position)

        self.assertTrue(values["valuationReferenceOnly"])
        self.assertFalse(values["valuationDecisionEligible"])
        self.assertEqual(0, values["fairValue"])
        self.assertEqual(0, values["marginOfSafetyPct"])
        self.assertEqual(300, values["analystTargetPrice"])
        self.assertEqual(200, values["analystTargetUpsidePct"])

        facts = position_signal_facts(
            position,
            portfolio_summary([], account_cash=1000, fx_rates={"USD": 1400}),
            external_signals=external_signals,
            settings={
                "valuationAssumptions": {
                    "AAPL": {"fairValue": 100, "formula": "사용자 적정가"}
                },
                "aiValuationAutoProposalEnabled": "0",
            },
        )

        reference = facts["valuationAnalystTargetReference"]
        self.assertEqual(300, reference["analystTargetPrice"])
        self.assertIn("안전마진으로 부르지 않고", reference["explanation"])
        self.assertEqual("single-model", facts["valuationConsensusStatus"])
        self.assertEqual(1, facts["valuationModelCount"])
        self.assertTrue(facts["valuationDecisionEligible"])

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


if __name__ == "__main__":
    unittest.main()
