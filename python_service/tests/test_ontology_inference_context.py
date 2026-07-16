import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.ontology_inference_context import relation_contexts_from_snapshot
from digital_twin.domain.ontology_relation_facts import position_signal_facts
from digital_twin.domain.portfolio import AccountSnapshot, Position
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.strategy import decisions_for_positions


class OntologyInferenceContextTests(unittest.TestCase):
    def test_position_signal_facts_include_runtime_valuation_formula(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            source="watchlist",
            current_price=80000,
            ma20=79000,
            ma60=76000,
            volume=1200000,
            volume_ratio=1.1,
            sector="반도체",
        )
        facts = position_signal_facts(
            position,
            portfolio_summary([], account_cash=1000000, fx_rates={"KRW": 1}),
            settings={
                "valuationAssumptions": {
                    "005930": {
                        "expectedEPS": 9000,
                        "targetPER": 11,
                        "minimumMarginOfSafetyPct": 20,
                        "formula": "적정가 = 예상 EPS x 목표 PER",
                    }
                }
            },
        )

        self.assertEqual("available", facts["valuationDataStatus"])
        self.assertEqual("user", facts["valuationSourceType"])
        self.assertEqual("사용자 입력", facts["valuationSourceLabel"])
        self.assertEqual("적정가 = 예상 EPS x 목표 PER", facts["valuationFormula"])
        self.assertEqual("9,000원 x 11배 = 99,000원", facts["valuationSubstitution"])
        self.assertEqual(99000, facts["valuationFairValue"])
        self.assertEqual(23.75, facts["valuationMarginOfSafetyPct"])
        self.assertEqual([], facts["valuationMissingInputs"])

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

        self.assertEqual("missing", facts["valuationDataStatus"])
        self.assertEqual("missing", facts["valuationSourceType"])
        self.assertIn("사용자 입력 없음", facts["valuationSourceLabel"])
        self.assertEqual("판단 보류", facts["valuationReliabilityLabel"])
        self.assertEqual(["적정가", "예상 EPS", "목표 PER"], facts["valuationMissingInputs"])
        self.assertEqual(164.25, facts["valuationCurrentPrice"])

    def test_position_signal_facts_use_ai_preferred_income_valuation_when_no_user_value(self):
        position = Position(
            symbol="STRC",
            name="스트래티지 스트레치 우선주(9.00%)",
            market="NASDAQ",
            currency="USD",
            source="holding",
            current_price=87.76,
            market_value=2106,
            quantity=24,
            ma20=85.7,
            ma60=94.1,
            sector="디지털자산",
        )
        facts = position_signal_facts(
            position,
            portfolio_summary([position], account_cash=1000000, fx_rates={"USD": 1400}),
            external_signals={
                "macro": {
                    "series": {
                        "DGS10": {"provider": "FRED", "value": 4.5},
                    },
                },
            },
            settings={},
        )

        self.assertEqual("available", facts["valuationDataStatus"])
        self.assertEqual("ai", facts["valuationSourceType"])
        self.assertEqual("AI 제안", facts["valuationSourceLabel"])
        self.assertEqual("ai-preferred-income-yield", facts["valuationMethod"])
        self.assertEqual("suggested", facts["valuationApprovalStatus"])
        self.assertTrue(facts["valuationRequiresUserApproval"])
        self.assertTrue(facts["valuationIsAiGenerated"])
        self.assertEqual(9.0, facts["valuationAnnualDividend"])
        self.assertEqual(9.5, facts["valuationRequiredYieldPct"])
        self.assertAlmostEqual(94.7368, facts["valuationFairValue"], places=4)
        self.assertIn("연간 배당", facts["valuationSubstitution"])

    def test_typedb_inferencebox_context_replaces_python_relation_rule_path(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            quantity=10,
            sellable_quantity=10,
            average_price=80000,
            current_price=70000,
            market_value=700000,
            profit_loss_rate=-12.5,
            ma20=76000,
            ma60=72000,
            ma20_distance=-7.9,
            ma60_distance=-2.8,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], fx_rates={"KRW": 1})
        snapshot = AccountSnapshot(
            "acct",
            "계좌",
            "test",
            "live",
            "ok",
            "2026-07-10T00:00:00Z",
            portfolio,
            positions=[position],
            metadata={
                "ontology": {
                    "typedb": {
                        "inferenceBox": {
                            "status": "ok",
                            "nativeTypeDbReasoningUsed": True,
                            "entityCount": 2,
                            "relationCount": 1,
                            "traceCount": 1,
                            "nativeRelationCount": 1,
                            "relations": [
                                {
                                    "type": "HAS_INFERRED_RISK",
                                    "source": "stock:005930",
                                    "sourceLabel": "삼성전자",
                                    "target": "risk:005930:loss-guard-breakdown",
                                    "targetLabel": "삼성전자 손실 방어 리스크",
                                    "ruleId": "graph.loss_guard.breakdown.v1",
                                    "polarity": "risk",
                                    "riskImpact": 13,
                                    "supportImpact": 0,
                                    "weight": 0.86,
                                    "aiInfluenceLabel": "손실 방어 추론",
                                    "inferenceTraceId": "inference-trace:005930:graph.loss_guard.breakdown.v1",
                                    "nativeTypeDbReasoned": True,
                                }
                            ],
                            "traces": [
                                {
                                    "id": "inference-trace:005930:graph.loss_guard.breakdown.v1",
                                    "label": "삼성전자 · 손실 보유 + 기준선 이탈 -> 손실 방어 추론",
                                    "symbol": "005930",
                                    "ruleId": "graph.loss_guard.breakdown.v1",
                                    "confidence": 0.86,
                                    "matchedConditionIds": ["holding-source", "holding-loss", "ma-break"],
                                    "nativeTypeDbReasoned": True,
                                }
                            ],
                        }
                    }
                }
            },
        )

        contexts = relation_contexts_from_snapshot(snapshot)
        self.assertIn("005930", contexts)
        self.assertEqual("typedbInferenceBox", contexts["005930"]["source"])
        self.assertEqual("typedbInferenceBox", contexts["005930"]["decision"]["basis"])
        self.assertEqual("graph.loss_guard.breakdown.v1", contexts["005930"]["decision"]["selectedRuleId"])
        self.assertEqual("stock:005930", contexts["005930"]["evidenceSubgraph"]["target"]["id"])
        self.assertEqual(["graph.loss_guard.breakdown.v1"], contexts["005930"]["evidenceSubgraph"]["matchedRuleIds"])
        self.assertTrue(any(item["type"] == "HAS_INFERRED_RISK" for item in contexts["005930"]["evidenceSubgraph"]["edges"]))
        self.assertIn("evidenceSubgraph", contexts["005930"]["promptContext"])

        decisions = decisions_for_positions(
            [position],
            portfolio,
            relation_contexts_by_symbol=contexts,
        )
        self.assertEqual(1, len(decisions))
        self.assertEqual("typedbInferenceBox", decisions[0].relation_rule_context["decision"]["basis"])
        self.assertTrue(decisions[0].relation_rule_context["graphStoreUsed"])

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
        self.assertEqual(0, decisions[0].exit_pressure)
        self.assertTrue(decisions[0].relation_rule_context["blocked"])
        self.assertFalse(decisions[0].relation_rule_context["fallbackUsed"])

    def test_typedb_bootstrap_inferencebox_is_not_valid_graph_decision_source(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            current_price=70000,
            market_value=700000,
            profit_loss_rate=-12.5,
            sector="반도체",
        )
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
                    "activeGraphStore": "typedb",
                    "typedb": {
                        "graphStore": "typedb",
                        "inferenceBox": {
                            "status": "ok",
                            "source": "typedbInferenceBox",
                            "graphStore": "typedb",
                            "typedbBootstrapReasoningUsed": True,
                            "relations": [
                                {
                                    "type": "HAS_INFERRED_RISK",
                                    "source": "stock:005930",
                                    "target": "risk:005930:loss-guard-breakdown",
                                    "targetLabel": "삼성전자 손실 방어 리스크",
                                    "ruleId": "graph.loss_guard.breakdown.v1",
                                    "polarity": "risk",
                                    "riskImpact": 13,
                                    "weight": 0.86,
                                    "decisionStage": "LOSS_REDUCE",
                                    "stagePriority": 90,
                                }
                            ],
                        },
                    },
                }
            },
        )

        contexts = relation_contexts_from_snapshot(snapshot)
        self.assertEqual({}, contexts)

    def test_typedb_entry_wait_inference_maps_to_entry_wait_stage(self):
        watch = Position(
            symbol="NVDA",
            name="NVIDIA",
            market="US",
            currency="USD",
            current_price=201.84,
            ma5=201.7,
            ma20=201.5,
            ma60=208.2,
            volume_ratio=0,
            source="watchlist",
            sector="반도체",
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
                                    "type": "HAS_INFERRED_ENTRY_WAIT",
                                    "source": "stock:NVDA",
                                    "target": "entry:NVDA:wait",
                                    "targetLabel": "NVIDIA 신규 진입 대기",
                                    "ruleId": "entry.wait_for_confirmation.v1",
                                    "polarity": "risk",
                                    "riskImpact": 8,
                                    "weight": 0.72,
                                    "decisionStage": "ENTRY_WAIT",
                                    "stagePriority": 31,
                                    "actionGroup": "entryWait",
                                    "actionLevel": "review",
                                    "nativeTypeDbReasoned": True,
                                }
                            ],
                            "traces": [
                                {
                                    "id": "inference-trace:NVDA:entry.wait_for_confirmation.v1",
                                    "label": "NVIDIA · 관심종목 + 확인 부족/거시 부담 -> 신규 진입 대기",
                                    "symbol": "NVDA",
                                    "ruleId": "entry.wait_for_confirmation.v1",
                                    "confidence": 0.72,
                                }
                            ],
                        }
                    }
                }
            },
        )

        contexts = relation_contexts_from_snapshot(snapshot)

        self.assertEqual("신규 진입 대기", contexts["NVDA"]["decision"]["label"])
        self.assertEqual("ENTRY_WAIT", contexts["NVDA"]["decision"]["decisionStage"])
        self.assertEqual("entryWait", contexts["NVDA"]["decision"]["actionGroup"])
        self.assertEqual("watchlist", contexts["NVDA"]["targetRole"])
        self.assertEqual("ENTRY_ONLY", contexts["NVDA"]["actionPolicy"])
        self.assertEqual(["BUY", "HOLD", "AVOID"], contexts["NVDA"]["allowedActions"])
        self.assertEqual(["ADD", "TRIM", "SELL"], contexts["NVDA"]["blockedActions"])
        self.assertEqual("ENTRY_ONLY", contexts["NVDA"]["executionPlan"]["actionPolicy"])
        self.assertIn("BUY", contexts["NVDA"]["executionPlan"]["allowedActions"])
        self.assertIn("SELL", contexts["NVDA"]["executionPlan"]["blockedActionCodes"])

    def test_typedb_entry_momentum_inference_maps_to_entry_ready_stage(self):
        watch = Position(
            symbol="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            current_price=210,
            ma5=207,
            ma20=205,
            ma60=202,
            volume_ratio=1.3,
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
                                    "type": "HAS_INFERRED_ENTRY_OPPORTUNITY",
                                    "source": "stock:AAPL",
                                    "target": "entry:AAPL:momentum",
                                    "targetLabel": "Apple 신규 진입 후보",
                                    "ruleId": "entry.momentum.confirmed.v1",
                                    "polarity": "support",
                                    "supportImpact": 12,
                                    "weight": 0.82,
                                    "decisionStage": "ENTRY_READY",
                                    "stagePriority": 37,
                                    "actionGroup": "entry",
                                    "actionLevel": "action",
                                    "nativeTypeDbReasoned": True,
                                }
                            ],
                            "traces": [
                                {
                                    "id": "inference-trace:AAPL:entry.momentum.confirmed.v1",
                                    "label": "Apple · 5/20/60일선 회복 + 거래 증가 + 거시 확인 -> 신규 진입 후보",
                                    "symbol": "AAPL",
                                    "ruleId": "entry.momentum.confirmed.v1",
                                    "confidence": 0.82,
                                }
                            ],
                        }
                    }
                }
            },
        )

        contexts = relation_contexts_from_snapshot(snapshot)

        self.assertEqual("소액 분할매수 검토", contexts["AAPL"]["decision"]["label"])
        self.assertEqual("ENTRY_READY", contexts["AAPL"]["decision"]["decisionStage"])
        self.assertEqual("entry", contexts["AAPL"]["decision"]["actionGroup"])
        self.assertEqual("typedbInferenceRelation", contexts["AAPL"]["decision"]["stagePolicySource"])

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
        self.assertEqual("ADD_BUY_BLOCKED", context["decision"]["decisionStage"])
        self.assertEqual("entryRisk", context["decision"]["actionGroup"])
        self.assertEqual("신규 진입 보류", context["decision"]["label"])
        self.assertEqual("AVOID_OR_WAIT", context["executionPlan"]["primaryAction"])
        self.assertNotIn("TRIM", context["executionPlan"]["primaryAction"])
        self.assertIn("SELL", context["executionPlan"]["blockedActionCodes"])

    def test_inference_score_uses_fact_magnitude_not_rule_weight_only(self):
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

        mild_score = context_for(mild)["scoreBreakdown"]
        severe_score = context_for(severe)["scoreBreakdown"]

        self.assertGreater(severe_score["riskPressure"], mild_score["riskPressure"])
        self.assertGreater(severe_score["finalStrength"], mild_score["finalStrength"])
        self.assertIn("손실률 확대", severe_score["drivers"])

    def test_support_evidence_is_kept_separate_from_risk_pressure(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            quantity=7,
            sellable_quantity=7,
            average_price=2343143,
            current_price=2118000,
            change_rate=10.7,
            market_value=14826000,
            profit_loss_rate=-9.6,
            ma5=2068200,
            ma20=2431000,
            ma60=2031967,
            trade_strength=106,
            bid_ask_imbalance=63.5,
            foreign_buy_volume=3683043,
            foreign_sell_volume=3017048,
            institution_buy_volume=3531063,
            institution_sell_volume=2829636,
            individual_buy_volume=3143652,
            individual_sell_volume=4506110,
            sector="반도체",
        )
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
                                    "weight": 0.86,
                                    "decisionStage": "LOSS_REDUCE",
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

        breakdown = relation_contexts_from_snapshot(snapshot)["000660"]["scoreBreakdown"]

        self.assertGreater(breakdown["riskPressure"], 0)
        self.assertGreater(breakdown["supportEvidence"], 0)
        self.assertLess(breakdown["netRiskPressure"], breakdown["riskPressure"])
        self.assertGreater(breakdown["opposingPressurePenalty"], 0)


if __name__ == "__main__":
    unittest.main()
