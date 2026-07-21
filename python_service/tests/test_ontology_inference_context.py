import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.ontology_inference_context import decision_from_inference, matches_from_inference, relation_contexts_from_snapshot
from digital_twin.domain.ontology_relation_execution_plan import decision_drivers_from_relation_context
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


class OntologyInferenceContextTests(unittest.TestCase):
    def test_instrument_profile_driver_uses_tbox_domain_language(self):
        profile = InstrumentProfile(
            symbol="CPNG",
            label="이커머스 플랫폼 성장주",
            archetypes=["PlatformGrowth", "HighVolatilityGrowth"],
            position_intent="growth",
            policies={
                "allowAddOnStrength": True,
                "trimOnTrendBreak": True,
                "avoidAveragingDown": True,
            },
        )
        payload = profile.to_dict()
        drivers = decision_drivers_from_relation_context(
            {
                "instrumentProfileLabel": profile.label,
                "instrumentArchetypes": profile.archetypes,
                "instrumentArchetypeLabels": payload["archetypeLabels"],
                "instrumentPositionIntent": profile.position_intent,
                "instrumentPositionIntentDescription": payload["positionIntentDescription"],
                "allowAddOnStrength": True,
                "trimOnTrendBreak": True,
                "avoidAveragingDown": True,
            },
            {},
            [],
        )

        driver = next(item for item in drivers if item["category"] == "instrumentProfile")
        summary = driver["summary"]
        self.assertEqual("종목 성격", driver["label"])
        self.assertIn("세부 성격은 플랫폼 성장주, 가격 변동이 큰 성장주입니다", summary)
        self.assertIn("계좌에서는 성장 가능성을 보고 투자하는 종목으로 관리합니다", summary)
        self.assertIn("관리 원칙은", summary)
        self.assertNotIn("PlatformGrowth", summary)
        self.assertNotIn("HighVolatilityGrowth", summary)
        self.assertNotIn("계좌 안 역할", summary)
        self.assertNotIn(" 타입:", summary)

    def test_every_default_profile_archetype_has_a_tbox_domain_label(self):
        profiles = profile_settings()
        identifiers = sorted({item for profile in profiles.values() for item in profile.archetypes})

        for identifier in identifiers:
            label = investment_archetype_label(identifier)
            self.assertTrue(label, identifier)
            self.assertNotEqual("사용자 정의 종목 성격", label, identifier)
            self.assertNotEqual(identifier, label)

    def test_user_facing_language_removes_internal_profile_identifiers(self):
        text = user_facing_investment_language(
            "종목 타입: PlatformGrowth, HighVolatilityGrowth. 계좌 안 역할: growth."
        )

        self.assertEqual(
            "종목 성격: 플랫폼 성장주, 가격 변동이 큰 성장주. 계좌에서의 역할: 성장 투자.",
            text,
        )

    def test_watchlist_entry_requires_sufficient_support_state(self):
        position = Position(symbol="AAPL", name="Apple", source="watchlist")
        context = {
            "decision": {"actionGroup": "entry", "decisionStage": "ENTRY_READY"},
            "decisionState": {"dataState": "insufficient"},
        }

        self.assertEqual("AVOID", choose_action(position, context, conflict_state="support-only"))

        context["decisionState"]["dataState"] = "sufficient"

        self.assertEqual("BUY", choose_action(position, context, conflict_state="support-only"))

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

        self.assertEqual("sufficient", facts["valuationDataStatus"])
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

        self.assertEqual("unavailable", facts["valuationDataStatus"])
        self.assertEqual("missing", facts["valuationSourceType"])
        self.assertIn("사용자 입력 없음", facts["valuationSourceLabel"])
        self.assertEqual("판단 보류", facts["valuationDataStateLabel"])
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

        self.assertEqual("partial", facts["valuationDataStatus"])
        self.assertEqual("ai", facts["valuationSourceType"])
        self.assertEqual("AI 제안", facts["valuationSourceLabel"])
        self.assertEqual("ai-preferred-income-yield-scenarios", facts["valuationMethod"])
        self.assertEqual("ai_applied_pending_review", facts["valuationApprovalStatus"])
        self.assertEqual("ai_applied_pending_review", facts["valuationReviewStatus"])
        self.assertTrue(facts["valuationAutoApplied"])
        self.assertTrue(facts["valuationRequiresUserApproval"])
        self.assertTrue(facts["valuationIsAiGenerated"])
        self.assertEqual(9.0, facts["valuationAnnualDividend"])
        self.assertEqual(9.5, facts["valuationRequiredYieldPct"])
        self.assertAlmostEqual(94.7368, facts["valuationFairValue"], places=4)
        self.assertIn("연간 배당", facts["valuationSubstitution"])
        self.assertEqual("not_applicable", facts["valuationPerStatus"])
        self.assertIn("배당", facts["valuationPerReason"])
        self.assertEqual("배당수익률/요구수익률", facts["valuationPreferredMetric"])

    def test_position_signal_facts_use_bitcoin_proxy_ai_valuation_for_mstr(self):
        position = Position(
            symbol="MSTR",
            name="스트래티지",
            market="NASDAQ",
            currency="USD",
            source="holding",
            current_price=90.75,
            average_price=88.9,
            quantity=200,
            market_value=18150,
            ma20=95.69,
            ma60=136.72,
            sector="디지털자산",
        )
        facts = position_signal_facts(
            position,
            portfolio_summary([position], account_cash=1000000, fx_rates={"USD": 1400}),
            external_signals={
                "cryptoMarkets": {
                    "bitcoin": {
                        "provider": "CoinGecko",
                        "symbol": "BTC",
                        "price": 64000,
                        "change24h": -3.1,
                        "change7d": -1.9,
                    }
                }
            },
            settings={},
        )

        self.assertEqual("partial", facts["valuationDataStatus"])
        self.assertEqual("ai", facts["valuationSourceType"])
        self.assertEqual("ai-bitcoin-treasury-nav-scenarios", facts["valuationMethod"])
        self.assertEqual("ai_applied_pending_review", facts["valuationApprovalStatus"])
        self.assertTrue(facts["valuationAutoApplied"])
        self.assertTrue(facts["valuationRequiresUserApproval"])
        self.assertEqual(0, facts["valuationFairValue"])
        self.assertIn("BTC 보유량", facts["valuationMissingInputs"])
        self.assertIn("BTC 보유량", facts["valuationFormula"])
        self.assertFalse(facts["valuationDecisionEligible"])
        self.assertEqual("not_applicable", facts["valuationPerStatus"])
        self.assertIn("비트코인", facts["valuationPerReason"])
        self.assertEqual("비트코인 보유가치/NAV", facts["valuationPreferredMetric"])

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
        self.assertIn("연간/TTM EPS", facts["valuationFormula"])
        self.assertEqual("available", facts["valuationPerStatus"])
        self.assertIn("같은 기간", facts["valuationPerReason"])
        self.assertEqual("연간/TTM EPS x 유형별 PER 범위", facts["valuationPreferredMetric"])
        self.assertEqual(68000, facts["valuationFairValueLow"])
        self.assertEqual(184000, facts["valuationFairValueHigh"])
        self.assertFalse(facts["valuationDecisionEligible"])

    def test_position_signal_facts_convert_underlying_kis_fundamentals_for_adr(self):
        position = Position(
            symbol="SKHY",
            name="SK하이닉스 ADR",
            market="NASDAQ",
            currency="USD",
            source="holding",
            current_price=9.0,
            exchange_rate=1400,
            average_price=10.0,
            quantity=10,
            market_value=90,
            ma20=9.5,
            ma60=8.5,
            sector="반도체",
        )
        facts = position_signal_facts(
            position,
            portfolio_summary([position], account_cash=1000000, fx_rates={"USD": 1400, "KRW": 1}),
            external_signals={
                "companyOverviews": {
                    "000660": {
                        "provider": "KIS Open API",
                        "peRatio": 12,
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
        self.assertEqual("ai-semiconductor-eps-per-scenarios", facts["valuationMethod"])
        self.assertAlmostEqual(8.5714, facts["valuationFairValue"], places=4)
        self.assertIn("연간/TTM EPS", facts["valuationFormula"])
        self.assertIn("메모리 가격/업황 지표", facts["valuationMissingInputs"])
        self.assertEqual("available", facts["valuationPerStatus"])
        self.assertIn("같은 기간", facts["valuationPerReason"])
        self.assertIn("KIS/공식 연간 EPS", facts["valuationFundamentalDataSourcePriority"])

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
                                    "decisionStage": "LOSS_REDUCE",
                                    "actionGroup": "lossControl",
                                    "actionLevel": "review",
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
        self.assertEqual("WhyNow", contexts["005930"]["whyNow"]["tboxClass"])
        self.assertEqual("SignalConflict", contexts["005930"]["signalConflicts"]["tboxClass"])
        self.assertEqual("InferenceTimeline", contexts["005930"]["inferenceTimeline"]["tboxClass"])
        self.assertIn("whyNow", contexts["005930"]["promptContext"])
        self.assertIn("signalConflicts", contexts["005930"]["promptContext"])
        self.assertIn("inferenceTimeline", contexts["005930"]["promptContext"])

        decisions = decisions_for_positions(
            [position],
            portfolio,
            relation_contexts_by_symbol=contexts,
        )
        self.assertEqual(1, len(decisions))
        self.assertEqual("typedbInferenceBox", decisions[0].relation_rule_context["decision"]["basis"])
        self.assertTrue(decisions[0].relation_rule_context["graphStoreUsed"])

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

    def test_unrelated_flow_facts_do_not_change_loss_rule_pressure(self):
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

        context = relation_contexts_from_snapshot(snapshot)["000660"]
        evidence_state = context["evidenceState"]

        self.assertIn("risk", evidence_state["evidenceRoles"])
        self.assertIn("SK하이닉스 손실 방어 리스크", context["signalConflicts"]["riskDrivers"])
        self.assertNotIn("체결강도 우위", context["signalConflicts"]["supportDrivers"])
        active_evidence = context["activeRules"][0]["evidenceState"]
        self.assertNotIn("tradeStrength", active_evidence["appliedFactFields"])
        self.assertNotIn("investorFlowScore", active_evidence["appliedFactFields"])


if __name__ == "__main__":
    unittest.main()
