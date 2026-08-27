import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.ontology_inference_context import (
    action_envelope_from_inference,
    decision_from_inference,
    matches_from_inference,
    portfolio_relation_context_from_snapshot,
    relation_contexts_from_snapshot,
)
from digital_twin.domain.ontology_relation_contracts import OntologyRuleMatch
from digital_twin.domain.ontology_relation_execution_plan import decision_drivers_from_relation_context, execution_plan_from_relation_context
from digital_twin.domain.ontology_relation_facts import position_signal_facts
from digital_twin.domain.instrument_profiles import InstrumentProfile, profile_settings
from digital_twin.domain.investment_ubiquitous_language import (
    investment_archetype_label,
    user_facing_investment_language,
)
from digital_twin.domain.investment_research import choose_action
from digital_twin.domain.portfolio import AccountSnapshot, Position
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.strategy import decisions_for_positions
from digital_twin.infrastructure.graph_store_inferencebox import inferencebox_relation_payload


class OntologyInferenceContextTests(unittest.TestCase):
    def test_portfolio_inferencebox_builds_first_class_relation_context(self):
        relation = inferencebox_relation_payload({
            "type": "REQUIRES_NEXT_CHECK",
            "source": "portfolio:acct",
            "target": "next-check:portfolio-risk",
            "ruleId": "graph.portfolio.risk_policy.review.v1",
            "decisionStage": "REBALANCE_REVIEW",
            "nativeTypeDbReasoned": True,
            "propertiesJson": json.dumps({
                "actionGroup": "rebalance",
                "actionLevel": "review",
                "decisionEffect": "defer",
                "decisionLabel": "포트폴리오 위험 축소 점검",
                "decisionTone": "caution",
                "candidateAction": "HOLD",
                "notificationSeverity": "WATCH",
                "aiInfluenceLabel": "포트폴리오 위험 정책 초과",
                "dataState": "sufficient",
            }),
        })
        snapshot = AccountSnapshot(
            "acct",
            "계좌",
            "test",
            "live",
            "ok",
            "2026-08-13T00:00:00Z",
            portfolio_summary([], fx_rates={"USD": 1400}),
            metadata={
                "ontology": {
                    "activeGraphStore": "projection",
                    "projection": {
                        "graphStore": "typedb",
                        "inferenceBox": {
                            "status": "ok",
                            "graphStore": "typedb",
                            "nativeTypeDbReasoningUsed": True,
                            "inferenceGenerationId": "inference:test",
                            "relations": [relation],
                            "traces": [],
                        },
                    },
                },
            },
        )

        context = portfolio_relation_context_from_snapshot(snapshot)

        self.assertEqual("typedbInferenceBox", context["source"])
        self.assertEqual("portfolio", context["subject"]["kind"])
        self.assertEqual("graph.portfolio.risk_policy.review.v1", context["activeRules"][0]["rule_id"])
        self.assertEqual("REBALANCE_REVIEW", context["decision"]["decisionStage"])
        self.assertEqual("portfolioRebalance", context["promptContext"]["promptId"])

    def test_execution_plan_describes_only_typedb_add_buy_relations(self):
        facts = {
            "symbol": "000660",
            "name": "SK하이닉스",
            "market": "KR",
            "source": "holding",
            "isHolding": True,
            "profitLossRate": -6.0,
            "investmentStrategyProfile": "aggressive",
        }
        decision = {
            "decisionStage": "HOLD_KEEP",
            "actionGroup": "holdWatch",
            "actionLevel": "watch",
            "label": "보유 유지",
        }
        matches = [
            OntologyRuleMatch(
                rule_id="graph.aggressive.loss_recovery.add_buy_review.v1",
                label="공격형 손실 회복 추가매수 검토",
                version="typedb",
                relation_type="ALLOWS_ACTION",
                signal_type="holdingTiming",
                matched=True,
                review_level="review",
                review_label="확인 필요",
                data_state="sufficient",
                evidence_role="support",
                decision_stage="ADD_BUY_REVIEW",
                action_group="addBuy",
                action_level="review",
                decision_label="조건부 추가매수 검토",
                candidate_action="ADD",
                allowed_actions=["ADD", "HOLD"],
            ),
        ]

        plan = execution_plan_from_relation_context(facts, decision, matches)

        self.assertEqual("allow", plan["addBuyAssessment"]["state"])
        self.assertIn("TypeDB", plan["addBuyAssessment"]["statusText"])
        self.assertNotIn("addBuyEligibilityStage", plan["sourceFacts"])

    def test_watchlist_entry_action_comes_from_materialized_rulebox_candidate(self):
        position = Position(symbol="AAPL", name="Apple", source="watchlist")
        context = {
            "decision": {
                "candidateAction": "BUY",
                "targetRole": "watchlist",
                "allowedActions": ["BUY", "HOLD", "AVOID"],
                "blockedActions": ["ADD", "TRIM", "SELL"],
            },
            "decisionState": {"dataState": "insufficient"},
        }

        self.assertEqual("BUY", choose_action(position, context, conflict_state="risk-only"))

        # A stage, raw data state, or caller-supplied conflict label may not
        # recreate the old Python investment action mapping.
        context["decision"]["candidateAction"] = "SELL"

        self.assertEqual("HOLD", choose_action(position, context, conflict_state="support-only"))

    def test_entry_defer_narrows_an_entry_support_without_turning_it_into_avoid(self):
        facts = {"symbol": "NVDA", "source": "watchlist", "isWatchlist": True}
        policy = {
            "targetRole": "watchlist",
            "actionPolicy": "ENTRY_ONLY",
            "allowedActions": ["BUY", "HOLD", "AVOID"],
            "blockedActions": ["ADD", "TRIM", "SELL"],
        }
        relations = [
            {
                "type": "HAS_INFERRED_ENTRY_OPPORTUNITY",
                "ruleId": "graph.entry.confirmed.v1",
                "targetLabel": "NVIDIA 진입 조건 확인",
                "polarity": "support",
                "decisionStage": "ENTRY_SPLIT_BUY",
                "decisionLabel": "소액 진입 검토",
                "decisionTone": "caution",
                "actionGroup": "entry",
                "actionLevel": "action",
                "candidateAction": "BUY",
                "decisionEffect": "support",
                **policy,
            },
            {
                "type": "HAS_INFERRED_ENTRY_WAIT",
                "ruleId": "graph.entry.wait.v1",
                "targetLabel": "NVIDIA 확인 자료 부족",
                "polarity": "context",
                "decisionStage": "ENTRY_WAIT",
                "decisionLabel": "진입 전 추가 확인",
                "decisionTone": "watch",
                "actionGroup": "entryWait",
                "actionLevel": "watch",
                "candidateAction": "HOLD",
                "decisionEffect": "defer",
                **policy,
            },
        ]

        matches = matches_from_inference(relations, [], facts)
        envelope = action_envelope_from_inference(facts, matches, relations)

        self.assertEqual("ENTRY_DEFERRED", envelope["status"])
        self.assertEqual("HOLD", envelope["preferredAction"])
        self.assertEqual(["graph.entry.wait.v1"], envelope["deferRuleIds"])

    def test_missing_data_driver_preserves_stale_value_reason(self):
        drivers = decision_drivers_from_relation_context(
            {
                "missingData": [
                    {
                        "label": "체결강도 (오래된 값)",
                        "effect": "체결강도는 확인됐지만 이전 조회와 같아 최신 변화 신호로 보지는 않습니다.",
                    },
                    {
                        "label": "투자자별 수급 (오래된 값)",
                        "effect": "KIS 투자자별 수급이 이전 조회와 같아 실시간 변화 신호는 아닙니다.",
                    },
                ]
            },
            {},
            [],
        )

        summary = next(item["summary"] for item in drivers if item["category"] == "dataQuality")
        self.assertIn("체결강도 (오래된 값)", summary)
        self.assertIn("이전 조회와 같아 최신 변화 신호로 보지는 않습니다", summary)
        self.assertNotIn("부족 데이터가 있어 판단 강도를 낮춥니다: 체결강도, 투자자별 수급", summary)

    def test_position_signal_facts_mark_missing_valuation_inputs(self):
        position = Position(
            symbol="NVDA",
            name="엔비디아",
            market="US",
            currency="USD",
            source="watchlist",
            current_price=164.25,
            ma20=160.0,
            ma60=150.0,
            sector="AI",
        )
        facts = position_signal_facts(
            position,
            portfolio_summary([], account_cash=1000000, fx_rates={"USD": 1400}),
            settings={"aiValuationAutoProposalEnabled": "0"},
        )

        self.assertEqual("unavailable", facts["valuationDataStatus"])
        self.assertEqual("missing", facts["valuationSourceType"])
        self.assertIn("사용자 입력 없음", facts["valuationSourceLabel"])
        self.assertEqual("판단 보류", facts["valuationDataStateLabel"])
        self.assertEqual(["적정가", "예상 EPS", "목표 PER"], facts["valuationMissingInputs"])
        self.assertEqual(164.25, facts["valuationCurrentPrice"])

    def test_position_signal_facts_use_kis_domestic_fundamentals_for_kr_stock(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            source="holding",
            current_price=210000,
            average_price=230000,
            quantity=7,
            market_value=1470000,
            ma20=220000,
            ma60=205000,
            sector="반도체",
        )
        facts = position_signal_facts(
            position,
            portfolio_summary([position], account_cash=1000000, fx_rates={"KRW": 1}),
            external_signals={
                "companyOverviews": {
                    "000660": {
                        "provider": "KIS Open API",
                        "peRatio": 12,
                        "pbr": 1.6,
                    }
                },
                "earningsReports": {
                    "000660": {
                        "provider": "KIS Open API",
                        "latestQuarter": {
                            "reportedEPS": 10000,
                            "epsPeriod": "annual",
                        },
                    }
                },
            },
            settings={},
        )

        self.assertEqual("partial", facts["valuationDataStatus"])
        self.assertEqual("ai", facts["valuationSourceType"])
        self.assertEqual("ai-semiconductor-eps-per-scenarios", facts["valuationMethod"])
        self.assertEqual(120000, facts["valuationFairValue"])
        self.assertEqual(10000, facts["valuationExpectedEPS"])
        self.assertEqual(12, facts["valuationTargetPER"])
        self.assertIn("연간 EPS 시나리오", facts["valuationFormula"])
        self.assertEqual("provisional", facts["valuationPerStatus"])
        self.assertIn("PER 표본이 부족", facts["valuationPerReason"])
        self.assertEqual("연간 EPS 시나리오 x 과거·피어 PER 사분위", facts["valuationPreferredMetric"])
        self.assertEqual(80000, facts["valuationFairValueLow"])
        self.assertEqual(160000, facts["valuationFairValueHigh"])
        self.assertEqual("fundamental-evidence-per-v3", facts["valuationModelVersion"])
        self.assertEqual("insufficient", facts["valuationConfidence"])
        self.assertFalse(facts["valuationDecisionEligible"])

    def test_missing_typedb_decision_stage_blocks_python_policy_fallback(self):
        relations = [{
            "type": "HAS_INFERRED_RISK",
            "source": "stock:005930",
            "target": "risk:005930:test",
            "ruleId": "graph.loss_guard.breakdown.v1",
            "derivationIndex": 0,
            "polarity": "risk",
            "actionGroup": "lossControl",
            "actionLevel": "review",
            "nativeTypeDbReasoned": True,
        }]
        matches = matches_from_inference(relations, [], facts={"symbol": "005930"})

        decision = decision_from_inference({}, matches, relations, [], source_name="typedbInferenceBox")

        self.assertTrue(decision["judgementBlocked"])
        self.assertEqual("missingTypeDbDecisionStage", decision["stagePolicySource"])
        self.assertEqual("", decision["decisionStage"])

    def test_no_eligible_hypothesis_is_not_mislabeled_as_missing_data(self):
        facts = {
            "symbol": "TSLA",
            "source": "watchlist",
            "isWatchlist": True,
            "currentPrice": 332.85,
        }

        envelope = action_envelope_from_inference(facts, [], [])
        decision = decision_from_inference(
            facts,
            [],
            [],
            [],
            source_name="typedbInferenceBox",
        )

        self.assertEqual("NO_ELIGIBLE_THESIS", envelope["status"])
        self.assertEqual("partial", envelope["dataReadiness"]["dataState"])
        self.assertFalse(envelope["judgementBlocked"])
        self.assertEqual("typedbNoEligibleHypothesis", decision["stagePolicySource"])
        self.assertEqual("NO_ELIGIBLE_THESIS", decision["hypothesisState"])
        self.assertTrue(decision["aiInterpretationEligible"])
        self.assertFalse(decision["judgementBlocked"])

    def test_missing_typedb_decision_effect_blocks_action_envelope(self):
        relations = [{
            "type": "HAS_INFERRED_RISK",
            "source": "stock:005930",
            "target": "risk:005930:test",
            "ruleId": "graph.loss_guard.breakdown.v1",
            "derivationIndex": 0,
            "polarity": "risk",
            "decisionStage": "LOSS_REDUCE",
            "actionGroup": "lossControl",
            "actionLevel": "review",
            "decisionTone": "caution",
            "candidateAction": "TRIM",
            "nativeTypeDbReasoned": True,
        }]
        facts = {"symbol": "005930", "source": "holding", "isHolding": True}
        matches = matches_from_inference(relations, [], facts=facts)

        envelope = action_envelope_from_inference(facts, matches, relations)
        decision = decision_from_inference(facts, matches, relations, [], source_name="typedbInferenceBox")

        self.assertTrue(matches[0].reference_only)
        self.assertEqual("missing-decision-effect", matches[0].evidence_state["policyReasonCode"])
        self.assertEqual("JUDGEMENT_BLOCKED", envelope["status"])
        self.assertTrue(envelope["judgementBlocked"])
        self.assertEqual(["graph.loss_guard.breakdown.v1"], envelope["missingDecisionEffectRuleIds"])
        self.assertEqual("missingTypeDbDecisionEffect", decision["stagePolicySource"])
        self.assertTrue(decision["judgementBlocked"])

    def test_stale_auxiliary_rule_does_not_override_fresh_core_inference(self):
        stale_rule = {
            "type": "HAS_INFERRED_RISK",
            "ruleId": "graph.cross_listing.adr_premium_risk.v1",
            "decisionStage": "HOLD_REVIEW",
            "decisionEffect": "constrain",
            "actionGroup": "marketStructure",
            "actionLevel": "review",
            "decisionTone": "caution",
            "candidateAction": "TRIM",
            "derivationIndex": 0,
        }
        fresh_rule = {
            "type": "HAS_INFERRED_ACTION",
            "ruleId": "graph.temporal.recovery.hold.v1",
            "decisionStage": "HOLD_KEEP",
            "decisionEffect": "support",
            "actionGroup": "holdWatch",
            "actionLevel": "watch",
            "decisionTone": "hold",
            "candidateAction": "HOLD",
            "derivationIndex": 0,
        }
        traces = [
            {
                "ruleId": stale_rule["ruleId"],
                "dataState": "partial",
                "freshnessStatus": "stale",
                "evidenceUsableForJudgement": False,
            },
            {
                "ruleId": fresh_rule["ruleId"],
                "dataState": "sufficient",
                "freshnessStatus": "fresh",
                "evidenceUsableForJudgement": True,
            },
        ]
        facts = {"symbol": "000660", "source": "holding", "isHolding": True}

        matches = matches_from_inference([stale_rule, fresh_rule], traces, facts=facts)
        envelope = action_envelope_from_inference(facts, matches, [stale_rule, fresh_rule])

        self.assertEqual("graph.temporal.recovery.hold.v1", envelope["selectedRuleId"])
        self.assertEqual("HOLD", envelope["preferredAction"])
        self.assertEqual(["graph.cross_listing.adr_premium_risk.v1"], envelope["dataReadiness"]["excludedRuleIds"])
        self.assertFalse(envelope["judgementBlocked"])

    def test_strict_decision_path_blocks_python_relation_rule_fallback(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            quantity=10,
            current_price=70000,
            market_value=700000,
            profit_loss_rate=-12.5,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], fx_rates={"KRW": 1})

        decisions = decisions_for_positions(
            [position],
            portfolio,
            require_inference_context=True,
        )

        self.assertEqual(1, len(decisions))
        self.assertEqual("ontologyInferenceRequired", decisions[0].decision_basis)
        self.assertEqual("blocked", decisions[0].review_level)
        self.assertEqual("unavailable", decisions[0].data_state)
        self.assertTrue(decisions[0].relation_rule_context["blocked"])
        self.assertFalse(decisions[0].relation_rule_context["fallbackUsed"])

    def test_unknown_typedb_stage_is_blocked_instead_of_becoming_hold(self):
        watch = Position(
            symbol="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            current_price=210,
            source="watchlist",
        )
        snapshot = AccountSnapshot(
            "acct",
            "계좌",
            "test",
            "live",
            "ok",
            "2026-07-10T00:00:00Z",
            portfolio_summary([], fx_rates={"KRW": 1, "USD": 1400}),
            watchlist=[watch],
            metadata={
                "ontology": {
                    "typedb": {
                        "inferenceBox": {
                            "status": "ok",
                            "nativeTypeDbReasoningUsed": True,
                            "relations": [{
                                "type": "HAS_INFERRED_ENTRY_OPPORTUNITY",
                                "source": "stock:AAPL",
                                "target": "entry:AAPL:unknown",
                                "ruleId": "entry.unknown-stage.v1",
                                "decisionStage": "FUTURE_STAGE",
                                "actionGroup": "entry",
                                "nativeTypeDbReasoned": True,
                            }],
                            "traces": [{
                                "id": "inference-trace:AAPL:entry.unknown-stage.v1",
                                "symbol": "AAPL",
                                "ruleId": "entry.unknown-stage.v1",
                            }],
                        }
                    }
                }
            },
        )

        context = relation_contexts_from_snapshot(snapshot)["AAPL"]

        self.assertTrue(context["decision"]["judgementBlocked"])
        self.assertEqual("blocked", context["decision"]["reviewLevel"])
        self.assertNotEqual("보유 유지", context["decision"]["label"])

    def test_watchlist_entry_only_policy_rewrites_holding_only_inference_stage(self):
        watch = Position(
            symbol="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            current_price=196,
            ma20=210,
            ma60=208,
            ma20_distance=-6.7,
            ma60_distance=-5.8,
            source="watchlist",
            sector="AI/플랫폼",
        )
        snapshot = AccountSnapshot(
            "acct",
            "계좌",
            "test",
            "live",
            "ok",
            "2026-07-10T00:00:00Z",
            portfolio_summary([], fx_rates={"KRW": 1, "USD": 1400}),
            watchlist=[watch],
            metadata={
                "ontology": {
                    "typedb": {
                        "inferenceBox": {
                            "status": "ok",
                            "nativeTypeDbReasoningUsed": True,
                            "relations": [
                                {
                                    "type": "HAS_INFERRED_RISK",
                                    "source": "stock:AAPL",
                                    "target": "risk:AAPL:trend-break",
                                    "targetLabel": "Apple 가격 흐름 약화",
                                    "ruleId": "graph.trend.breakdown_acceleration.v1",
                                    "polarity": "risk",
                                    "riskImpact": 12,
                                    "weight": 0.84,
                                    "decisionStage": "LOSS_REDUCE",
                                    "decisionEffect": "constrain",
                                    "decisionLabel": "손실 축소 기준 점검",
                                    "decisionTone": "caution",
                                    "primaryAction": "TRIM_REVIEW",
                                    "candidateAction": "TRIM",
                                    "stagePriority": 44,
                                    "actionGroup": "lossControl",
                                    "actionLevel": "review",
                                    "nativeTypeDbReasoned": True,
                                }
                            ],
                            "traces": [
                                {
                                    "id": "inference-trace:AAPL:graph.trend.breakdown_acceleration.v1",
                                    "label": "Apple · 가격 흐름 약화",
                                    "symbol": "AAPL",
                                    "ruleId": "graph.trend.breakdown_acceleration.v1",
                                    "confidence": 0.84,
                                }
                            ],
                        }
                    }
                }
            },
        )

        context = relation_contexts_from_snapshot(snapshot)["AAPL"]

        self.assertEqual("watchlist", context["targetRole"])
        self.assertEqual("ENTRY_ONLY", context["actionPolicy"])
        self.assertTrue(context["decision"]["actionPolicyApplied"])
        self.assertEqual("LOSS_REDUCE", context["decision"]["decisionStage"])
        self.assertEqual("lossControl", context["decision"]["actionGroup"])
        self.assertEqual("신규 진입 보류", context["decision"]["label"])
        self.assertEqual("AVOID_OR_WAIT", context["executionPlan"]["primaryAction"])
        self.assertNotIn("TRIM", context["executionPlan"]["primaryAction"])
        self.assertIn("SELL", context["executionPlan"]["blockedActionCodes"])

    def test_inference_keeps_fact_magnitude_without_creating_an_aggregate_score(self):
        def context_for(position: Position):
            snapshot = AccountSnapshot(
                "acct",
                "계좌",
                "test",
                "live",
                "ok",
                "2026-07-10T00:00:00Z",
                portfolio_summary([position], fx_rates={"KRW": 1}),
                positions=[position],
                metadata={
                    "ontology": {
                        "typedb": {
                            "inferenceBox": {
                                "status": "ok",
                                "nativeTypeDbReasoningUsed": True,
                                "relations": [
                                    {
                                        "type": "HAS_INFERRED_RISK",
                                        "source": "stock:000660",
                                        "target": "risk:000660:loss-guard-breakdown",
                                        "targetLabel": "SK하이닉스 손실 방어 리스크",
                                        "ruleId": "graph.loss_guard.breakdown.v1",
                                        "polarity": "risk",
                                        "riskImpact": 13,
                                        "supportImpact": 0,
                                        "weight": 0.86,
                                        "decisionStage": "LOSS_REDUCE",
                                        "decisionEffect": "constrain",
                                        "decisionTone": "caution",
                                        "stagePriority": 40,
                                        "actionGroup": "lossControl",
                                        "actionLevel": "review",
                                        "nativeTypeDbReasoned": True,
                                    }
                                ],
                                "traces": [
                                    {
                                        "id": "inference-trace:000660:graph.loss_guard.breakdown.v1",
                                        "label": "SK하이닉스 · 손실 보유 + 주요 평균선 아래",
                                        "symbol": "000660",
                                        "ruleId": "graph.loss_guard.breakdown.v1",
                                        "confidence": 0.86,
                                    }
                                ],
                            }
                        }
                    }
                },
            )
            return relation_contexts_from_snapshot(snapshot)["000660"]

        mild = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            quantity=7,
            sellable_quantity=7,
            average_price=2343143,
            current_price=2240000,
            market_value=15680000,
            profit_loss_rate=-4.4,
            ma5=2220000,
            ma20=2320000,
            ma60=2100000,
            change_rate=0.8,
            sector="반도체",
        )
        severe = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            quantity=7,
            sellable_quantity=7,
            average_price=2343143,
            current_price=1913000,
            market_value=13391000,
            profit_loss_rate=-18.4,
            ma5=2045000,
            ma20=2449050,
            ma60=2015417,
            change_rate=-3.7,
            trade_strength=94,
            bid_ask_imbalance=-35,
            sector="반도체",
        )

        mild_context = context_for(mild)
        severe_context = context_for(severe)

        self.assertEqual(-4.4, mild_context["facts"]["profitLossRate"])
        self.assertEqual(-18.4, severe_context["facts"]["profitLossRate"])
        self.assertEqual("risk-only", severe_context["conflictState"])
        self.assertNotIn("scoreBreakdown", severe_context)

if __name__ == "__main__":
    unittest.main()
