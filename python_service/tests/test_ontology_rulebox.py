import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.ontology_prompting import prompt_payload
from digital_twin.domain.ontology_inference_ledger import inference_trace_ledger_payload
from digital_twin.domain.instrument_profiles import market_signal_profiles, parse_instrument_profiles_text
from digital_twin.domain.ontology_rulebox_catalog import (
    default_graph_inference_rules,
    governed_graph_inference_rules,
)
from digital_twin.domain.ontology_tbox import tbox_class_def
from digital_twin.domain.ontology_threshold_policy import default_ontology_threshold_policy
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.domain.portfolio import Position
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.portfolio_ontology_market_concepts import missing_market_microstructure_fields
from digital_twin.domain.portfolio_ontology_temporal_concepts import (
    TemporalWindowDefinition,
    add_temporal_observation_anchors,
    market_session_phase,
    parse_temporal_windows,
    temporal_observation_anchors,
    temporal_window_values,
    window_rows,
)
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.security_lines import related_market_symbols_for_positions, security_lines_for_symbol
from digital_twin.infrastructure.typedb_ontology import (
    TypeDBOntologyGraphRepository,
    inferencebox_snapshot_from_rows,
    NullTypeDBOntologyGraphRepository,
    ontology_seed_graph,
    rulebox_graph_from_rules,
    rulebox_rules_from_payload,
    rulebox_rules_to_payload,
    rulebox_snapshot_from_rows,
    typedb_native_match_query,
    typedb_native_rule_execution_plan,
    typedb_native_rule_target_work_plan,
    typedb_native_rule_profile,
    typedb_planned_candidate_symbols,
)
from digital_twin.infrastructure.graph_store_rulebox import (
    build_rulebox_rules_from_rows,
    derivation_payload_from_row,
)
from digital_twin.domain.ontology_rulebox_governance import (
    rulebox_governance_candidates,
    rulebox_rules_hash,
    rulebox_semantic_violations,
    rulebox_version_payload,
)


class OntologyRuleBoxTests(unittest.TestCase):
    def test_intraday_window_contract_parses_duration_and_session_phase(self):
        definitions = {item.key: item for item in parse_temporal_windows()}
        rows = [
            {
                "bucketAt": "2026-07-20T00:00:00Z",
                "marketSessionDate": "2026-07-20",
                "currentPrice": 100,
                "dataQuality": "actual",
            },
            {
                "bucketAt": "2026-07-20T00:06:00Z",
                "marketSessionDate": "2026-07-20",
                "currentPrice": 98,
                "dataQuality": "actual",
            },
            {
                "bucketAt": "2026-07-20T00:12:00Z",
                "marketSessionDate": "2026-07-20",
                "currentPrice": 101,
                "dataQuality": "actual",
            },
            {
                "bucketAt": "2026-07-20T00:15:00Z",
                "marketSessionDate": "2026-07-20",
                "currentPrice": 102,
                "dataQuality": "actual",
            },
        ]

        selected = window_rows(
            rows,
            definitions["15M"],
            datetime.fromisoformat("2026-07-20T00:15:00+00:00"),
        )
        values = temporal_window_values(selected, definitions["15M"])
        anchors = temporal_observation_anchors(selected)
        phase = market_session_phase("2026-07-20T00:15:00Z", "KR", "KRW")

        self.assertEqual(15, definitions["15M"].lookback_minutes)
        self.assertTrue(definitions["SESSION"].is_session_window)
        self.assertEqual("IntradayWindow", definitions["1H"].tbox_class)
        self.assertTrue(values["hasSufficientHistory"])
        self.assertEqual(1.0, values["timeCoverageRatio"])
        self.assertLessEqual(len(anchors), 5)
        self.assertTrue(any("peak" in role for role, _index, _row in anchors))
        self.assertTrue(any("trough" in role for role, _index, _row in anchors))
        self.assertEqual("opening", phase["phaseKey"])

    def test_rulebox_v3_has_explicit_governance_and_removes_false_generic_paths(self):
        rules = governed_graph_inference_rules()
        rules_by_id = {item.rule_id: item for item in rules}
        executable = default_graph_inference_rules()

        self.assertEqual([], rulebox_semantic_violations(rules))
        self.assertEqual(108, sum(item.enabled for item in executable))
        self.assertEqual(63, sum(
            item.resolved_knowledge_basis.rule_kind == "predictive-hypothesis"
            and item.resolved_knowledge_basis.migration_disposition == "model-signal-production"
            for item in executable
        ))
        awaiting_flow_model = [
            item for item in executable
            if item.resolved_knowledge_basis.migration_disposition == "awaiting-governed-model-scorer"
        ]
        self.assertEqual(11, len(awaiting_flow_model))
        self.assertTrue(all(not item.enabled for item in awaiting_flow_model))
        disclosure = rules_by_id["graph.disclosure.event_risk.v1"]
        self.assertEqual("context-observation", disclosure.resolved_knowledge_basis.rule_kind)
        self.assertEqual("reference-only", disclosure.resolved_knowledge_basis.decision_eligibility)
        self.assertTrue(all(
            not derivation.candidate_action
            for derivation in disclosure.derivations
        ))
        self.assertEqual(
            {
                "group": ["dartDisclosures", "secFilings"],
                "documentVerificationState": "verified",
                "documentAnalysisState": "ready",
                "evidenceEligibilityState": "eligible",
                "eventDecisionEligible": True,
            },
            disclosure.conditions[1].target_property_filters,
        )
        self.assertTrue(rules_by_id["graph.benchmark.beta.context.v1"].enabled)
        self.assertFalse(rules_by_id["graph.data_quality.action_block.v1"].enabled)
        self.assertFalse(rules_by_id["graph.holding.trend_transition.risk.v1"].enabled)
        self.assertTrue(any(
            item.relation_type == "BLOCKS_ACTION"
            for item in rules_by_id["graph.data_quality.microstructure_gap.v1"].derivations
        ))
        self.assertTrue(any(
            item.relation_type == "WEAKENS_THESIS"
            for item in rules_by_id["graph.temporal.downside_acceleration.risk.v1"].derivations
        ))
        self.assertTrue(all(
            condition.hypothesis_scope and condition.evidence_group_key
            for rule in rules
            for condition in rule.conditions
        ))
        self.assertTrue(all(
            rule.hypothesis_family_key
            and rule.resolved_hypothesis_lifecycle().validity_minutes > 0
            and rule.resolved_hypothesis_lifecycle().required_freshness_domains
            for rule in rules
            if rule.enabled
        ))
        self.assertTrue(all(
            not derivation.candidate_action
            for rule in rules
            if rule.resolved_knowledge_basis.rule_kind != "predictive-hypothesis"
            for derivation in rule.derivations
        ))
        self.assertTrue(all(
            derivation.candidate_action
            for rule in rules
            if rule.enabled and rule.resolved_knowledge_basis.rule_kind == "predictive-hypothesis"
            for derivation in rule.derivations
        ))

        profit_harvest = rules_by_id["graph.profit_harvest.path_deceleration.v1"]
        temporal_conditions = [
            item
            for item in profit_harvest.conditions
            if item.relation_type == "HAS_TEMPORAL_WINDOW"
        ]
        self.assertEqual(2, len(temporal_conditions))
        self.assertTrue(all(item.role == "any" for item in temporal_conditions))
        self.assertTrue(any(
            item.tbox_class == "ProfitTakingEligibility"
            and item.candidate_action == "TRIM"
            and item.allowed_actions == ["TRIM", "HOLD"]
            for item in profit_harvest.derivations
        ))
        for rule_id, action_group in (
            ("graph.notification.loss_policy_threshold.v1", "lossControl"),
            ("graph.notification.profit_policy_threshold.v1", "profitTake"),
        ):
            threshold_rule = rules_by_id[rule_id]
            self.assertEqual("notification-policy", threshold_rule.resolved_knowledge_basis.owner)
            self.assertEqual("context-observation", threshold_rule.resolved_knowledge_basis.rule_kind)
            self.assertEqual("reference-only", threshold_rule.resolved_knowledge_basis.decision_eligibility)
            self.assertFalse(threshold_rule.resolved_knowledge_basis.requires_hypothesis)
            self.assertTrue(all(not item.candidate_action for item in threshold_rule.derivations))
            self.assertEqual(action_group, threshold_rule.derivations[0].action_group)
            self.assertEqual("CREATES_NOTIFICATION_INTENT", threshold_rule.derivations[0].relation_type)
        rulebox_rules_from_payload(
            {"rules": rulebox_rules_to_payload(rules)},
            strict_governance=True,
        )
        invalid_rules = rulebox_rules_to_payload(rules)
        invalid_rules[0]["conditions"][0].pop("evidence_group_key", None)
        with self.assertRaisesRegex(ValueError, "evidence_group_key"):
            rulebox_rules_from_payload({"rules": invalid_rules}, strict_governance=True)

    def test_model_input_routing_contract_survives_rulebox_graph_round_trip(self):
        original = next(
            rule
            for rule in default_graph_inference_rules()
            if rule.model_input_contract
        )
        graph = rulebox_graph_from_rules([original], include_tbox=False)
        rule_entity = next(
            item
            for item in graph.entities
            if item.kind == "rule"
            and (item.properties or {}).get("ruleId") == original.rule_id
        )
        condition_entities = [
            item
            for item in graph.entities
            if item.kind == "rule-condition"
            and (item.properties or {}).get("ruleId") == original.rule_id
        ]
        derivation_entities = [
            item
            for item in graph.entities
            if item.kind == "relation-template"
            and (item.properties or {}).get("ruleId") == original.rule_id
        ]

        restored = build_rulebox_rules_from_rows(
            [{
                "ruleId": original.rule_id,
                "propertiesJson": json.dumps(rule_entity.properties),
            }],
            [{
                "ruleId": original.rule_id,
                "conditionId": (item.properties or {}).get("conditionId"),
                "conditionIndex": (item.properties or {}).get("conditionIndex"),
                "propertiesJson": json.dumps(item.properties),
            } for item in condition_entities],
            [{
                "ruleId": original.rule_id,
                "derivationIndex": (item.properties or {}).get("derivationIndex"),
                "propertiesJson": json.dumps(item.properties),
            } for item in derivation_entities],
        )

        self.assertEqual(1, len(restored))
        self.assertEqual(original.model_input_contract, restored[0].model_input_contract)
        self.assertTrue(
            restored[0].model_input_contract.get("conditionProfiles")
        )

    def loss_guard_graph(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            quantity=10,
            sellable_quantity=10,
            average_price=80000,
            current_price=69000,
            market_value=690000,
            profit_loss=-110000,
            profit_loss_rate=-12.4,
            ma20=76000,
            ma60=73000,
            ma20_distance=-9.2,
            ma60_distance=-5.5,
            volume_ratio=1.4,
            trading_value=5000000000,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-test")

    def test_moving_average_distances_are_derived_from_current_price(self):
        position = Position(
            symbol="MSTR",
            name="Strategy",
            market="US",
            currency="USD",
            quantity=1,
            average_price=80,
            current_price=90,
            market_value=90,
            profit_loss=10,
            profit_loss_rate=12.5,
            ma5=100,
            ma20=90,
            ma60=75,
            ma5_distance=0,
            ma20_distance=7,
            ma60_distance=0,
        )
        portfolio = portfolio_summary([position], account_cash=10)
        graph = build_portfolio_ontology([position], portfolio, portfolio_id="ma-distance-test")
        stock = next(item for item in graph.entities if item.entity_id == "stock:MSTR")
        levels = {
            (item.properties or {}).get("levelType"): (item.properties or {}).get("distancePct")
            for item in graph.entities
            if item.kind == "key-level"
        }

        self.assertAlmostEqual(-10.0, stock.properties["ma5Distance"])
        self.assertAlmostEqual(0.0, stock.properties["ma20Distance"])
        self.assertAlmostEqual(20.0, stock.properties["ma60Distance"])
        self.assertAlmostEqual(-10.0, levels["ma5"])
        self.assertAlmostEqual(0.0, levels["ma20"])
        self.assertAlmostEqual(20.0, levels["ma60"])

    def test_sk_hynix_security_lines_materialize_cross_listing_and_leveraged_flow(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            quantity=10,
            sellable_quantity=10,
            average_price=210000,
            current_price=200000,
            market_value=2000000,
            profit_loss=-100000,
            profit_loss_rate=-4.8,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=1000000)
        graph = build_portfolio_ontology(
            [position],
            portfolio,
            external_signals={
                "fxRates": {"USDKRW": {"rate": 1400}},
                "equityQuotes": {
                    "SKHY": {"price": 20, "volume": 120000, "latestTradingDay": "2026-07-16"},
                    "SKHX": {"price": 41.2, "volume": 78000, "latestTradingDay": "2026-07-16"},
                },
            },
            portfolio_id="security-line-test",
            runtime_context={"settings": {"externalAlphaRelatedSymbolsEnabled": "1"}},
        )

        relation_types = {item.relation_type for item in graph.relations}
        entity_by_kind = {}
        for item in graph.entities:
            entity_by_kind.setdefault(item.kind, []).append(item)
        premium = next(item for item in graph.entities if item.kind == "cross-market-premium")
        friction = next(item for item in graph.entities if item.kind == "arbitrage-friction")
        leveraged_flow = next(item for item in graph.entities if item.kind == "leveraged-flow-signal")
        inverse_line = next(
            item
            for item in graph.entities
            if item.kind == "security-line" and (item.properties or {}).get("symbol") == "SKHZ"
        )

        self.assertIn("HAS_SECURITY_LINE", relation_types)
        self.assertIn("REPRESENTS_ECONOMIC_CLAIM", relation_types)
        self.assertIn("HAS_ADR_PREMIUM", relation_types)
        self.assertIn("HAS_LEVERAGED_FLOW_SIGNAL", relation_types)
        self.assertIn("HAS_COVERAGE_GAP", relation_types)
        self.assertEqual(40.0, (premium.properties or {}).get("value"))
        self.assertEqual("InverseETF", (inverse_line.properties or {}).get("tboxClass"))
        self.assertTrue(entity_by_kind.get("leveraged-flow-signal"))
        self.assertNotIn("Risk", friction.properties["tboxClasses"])
        self.assertNotIn("FlowAmplificationRisk", leveraged_flow.properties["tboxClasses"])
        self.assertEqual("context", next(
            item.properties["polarity"]
            for item in graph.relations
            if item.relation_type == "HAS_ADR_PREMIUM"
        ))
        self.assertEqual("context", next(
            item.properties["polarity"]
            for item in graph.relations
            if item.relation_type == "HAS_LEVERAGED_FLOW_SIGNAL"
        ))

    def test_temporal_windows_materialize_from_monitor_state_history(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            quantity=5,
            sellable_quantity=5,
            average_price=105000,
            current_price=90000,
            market_value=450000,
            profit_loss=-75000,
            profit_loss_rate=-17.0,
            ma20=100000,
            ma60=98000,
            ma20_distance=-10.0,
            ma60_distance=-8.2,
            foreign_net_volume=-5000,
            institution_net_volume=-7000,
            individual_net_volume=12000,
            volume_ratio=1.4,
            trade_strength=84,
            bid_ask_imbalance=-12,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        graph = build_portfolio_ontology(
            [position],
            portfolio,
            portfolio_id="temporal-window-test",
            runtime_context={
                "asOf": "2026-07-16T00:00:00Z",
                "settings": {"temporalWindowPeriods": "1D=1:2;3D=3:3;5D=5:4;20D=20:5"},
                "metadata": {
                    "monitorStateHistory": [
                        {
                            "generatedAt": "2026-07-13T00:00:00Z",
                            "positions": {
                                "000660": {
                                    "current_price": 100000,
                                    "profit_loss_rate": -8.0,
                                    "ma20_distance": -2.0,
                                    "ma60_distance": -1.0,
                                    "foreign_net_volume": 1000,
                                    "institution_net_volume": -500,
                                    "individual_net_volume": -500,
                                }
                            },
                        },
                        {
                            "generatedAt": "2026-07-14T00:00:00Z",
                            "positions": {
                                "000660": {
                                    "current_price": 96000,
                                    "profit_loss_rate": -11.0,
                                    "ma20_distance": -5.0,
                                    "ma60_distance": -3.0,
                                    "foreign_net_volume": -1000,
                                    "institution_net_volume": -1500,
                                    "individual_net_volume": 2500,
                                }
                            },
                        },
                        {
                            "generatedAt": "2026-07-15T00:00:00Z",
                            "positions": {
                                "000660": {
                                    "current_price": 93000,
                                    "profit_loss_rate": -14.0,
                                    "ma20_distance": -7.0,
                                    "ma60_distance": -5.0,
                                    "foreign_net_volume": -2500,
                                    "institution_net_volume": -3000,
                                    "individual_net_volume": 5500,
                                }
                            },
                        },
                    ],
                },
            },
        )

        relation_types = {item.relation_type for item in graph.relations}
        temporal_windows = [item for item in graph.entities if item.kind == "temporal-window"]
        temporal_observations = [item for item in graph.entities if item.kind == "temporal-observation"]
        three_day = next(
            item for item in temporal_windows
            if (item.properties or {}).get("windowKey") == "3D"
        )
        payload = prompt_payload(graph)

        self.assertIn("HAS_TEMPORAL_WINDOW", relation_types)
        self.assertIn("WINDOW_CONTAINS_OBSERVATION", relation_types)
        self.assertIn("PRECEDES", relation_types)
        self.assertIn("HAS_COVERAGE_GAP", relation_types)
        self.assertNotIn("HAS_PRICE_PATH_PATTERN", relation_types)
        self.assertNotIn("HAS_FLOW_PATTERN", relation_types)
        self.assertNotIn("DERIVES_TREND_EPISODE", relation_types)
        self.assertGreaterEqual(len(temporal_windows), 4)
        self.assertGreaterEqual(len(temporal_observations), 8)
        self.assertEqual(-10.0, (three_day.properties or {}).get("priceChangePct"))
        self.assertEqual(3, (three_day.properties or {}).get("consecutiveDeclineCount"))
        self.assertNotIn("pricePathPattern", three_day.properties or {})
        self.assertNotIn("trendEpisodeType", three_day.properties or {})
        self.assertIn("temporalWindows", payload)
        self.assertTrue(payload["temporalWindows"])

    def test_current_state_reasoning_keeps_temporal_history_out_of_abox(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            quantity=1,
            sellable_quantity=1,
            average_price=100000,
            current_price=101000,
            market_value=101000,
            profit_loss=1000,
            profit_loss_rate=1.0,
            ma20=100000,
            ma60=99000,
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        graph = build_portfolio_ontology(
            [position],
            portfolio,
            portfolio_id="current-state-temporal-summary-test",
            runtime_context={
                "asOf": "2026-07-16T00:00:00Z",
                "settings": {
                    "ontologyIncrementalCurrentStateReasoningEnabled": "1",
                    "ontologyTemporalObservationAnchorProjectionEnabled": "auto",
                    "temporalWindowPeriods": "1D=1:2",
                },
                "metadata": {
                    "monitorStateHistory": [
                        {
                            "generatedAt": "2026-07-15T00:00:00Z",
                            "positions": {"000660": {"current_price": 100000}},
                        },
                        {
                            "generatedAt": "2026-07-16T00:00:00Z",
                            "positions": {"000660": {"current_price": 101000}},
                        },
                    ],
                },
            },
        )

        self.assertTrue(any(item.kind == "temporal-window" for item in graph.entities))
        self.assertFalse(any(item.kind == "temporal-observation" for item in graph.entities))
        self.assertFalse(any(
            item.relation_type in {"WINDOW_CONTAINS_OBSERVATION", "PRECEDES"}
            for item in graph.relations
        ))

    def test_temporal_window_keeps_raw_trajectory_and_rejects_stale_flow_as_inference_input(self):
        rows = [
            {
                "generatedAt": "2026-07-20T00:00:00Z",
                "marketSessionDate": "2026-07-20",
                "currentPrice": 100,
                "ma20Distance": -1,
                "foreignNetVolume": -100,
                "institutionNetVolume": -200,
                "individualNetVolume": 300,
                "sourceAsOf": "2026-07-20T00:00:00Z",
                "dataQuality": "actual",
            },
            {
                "generatedAt": "2026-07-21T00:00:00Z",
                "marketSessionDate": "2026-07-21",
                "currentPrice": 95,
                "ma20Distance": -4,
                "foreignNetVolume": -100,
                "institutionNetVolume": -200,
                "individualNetVolume": 300,
                "sourceAsOf": "2026-07-20T00:00:00Z",
                "dataQuality": "stale",
            },
            {
                "generatedAt": "2026-07-22T00:00:00Z",
                "marketSessionDate": "2026-07-22",
                "currentPrice": 92,
                "ma20Distance": -7,
                "foreignNetVolume": -100,
                "institutionNetVolume": -200,
                "individualNetVolume": 300,
                "sourceAsOf": "2026-07-20T00:00:00Z",
                "dataQuality": "stale",
            },
        ]

        values = temporal_window_values(rows, TemporalWindowDefinition("3D", 3, 3))

        self.assertEqual(-8.0, values["priceChangePct"])
        self.assertEqual(2, values["consecutiveDeclineCount"])
        self.assertEqual(2, values["staleObservationCount"])
        self.assertEqual(1, values["smartMoneyObservationCount"])
        self.assertEqual(1, values["smartMoneyDistinctObservationCount"])
        self.assertEqual("partial", values["smartMoneyDataState"])
        self.assertEqual(-300.0, values["smartMoneyNetLatest"])
        self.assertAlmostEqual(1 / 3, values["validObservationRatio"], places=3)

    def test_temporal_window_does_not_turn_missing_flow_into_zero_flow(self):
        rows = [
            {
                "generatedAt": "2026-07-21T00:00:00Z",
                "marketSessionDate": "2026-07-21",
                "currentPrice": 100,
                "dataQuality": "actual",
            },
            {
                "generatedAt": "2026-07-22T00:00:00Z",
                "marketSessionDate": "2026-07-22",
                "currentPrice": 101,
                "dataQuality": "actual",
            },
        ]

        values = temporal_window_values(rows, TemporalWindowDefinition("2D", 2, 2))
        graph = PortfolioOntology("missing-flow-test")
        add_temporal_observation_anchors(graph, "window:2D", "005930", TemporalWindowDefinition("2D", 2, 2), rows)

        self.assertEqual("unavailable", values["smartMoneyDataState"])
        self.assertNotIn("smartMoneyNetLatest", values)
        self.assertNotIn("smartMoneyNetChange", values)
        self.assertTrue(all(
            "FlowObservation" not in (item.properties or {}).get("tboxClasses", [])
            and "smartMoneyNetLatest" not in (item.properties or {})
            for item in graph.entities
        ))

    def strategy_threshold_loss_graph(self, strategy_profile: str, pnl_rate: float):
        current_price = 100000 * (1 + pnl_rate / 100)
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            quantity=5,
            sellable_quantity=5,
            average_price=100000,
            current_price=current_price,
            market_value=current_price * 5,
            profit_loss=(current_price - 100000) * 5,
            profit_loss_rate=pnl_rate,
            ma20=110000,
            ma60=108000,
            ma20_distance=-8.5,
            ma60_distance=-6.0,
            volume_ratio=1.2,
            trading_value=5000000000,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology(
            [position],
            portfolio,
            portfolio_id="rulebox-strategy-threshold-" + strategy_profile,
            runtime_context={"account": {"investmentStrategyProfile": strategy_profile}},
        )

    def strategy_threshold_profit_graph(self, strategy_profile: str, pnl_rate: float):
        current_price = 100000 * (1 + pnl_rate / 100)
        position = Position(
            symbol="AAPL",
            name="Apple",
            market="US",
            currency="USD",
            quantity=2,
            sellable_quantity=2,
            average_price=100000,
            current_price=current_price,
            market_value=current_price * 2,
            profit_loss=(current_price - 100000) * 2,
            profit_loss_rate=pnl_rate,
            ma20=128000,
            ma60=124000,
            ma20_distance=-3.0,
            ma60_distance=1.0,
            volume_ratio=1.1,
            trading_value=100000000,
            sector="AI",
        )
        portfolio = portfolio_summary([position], account_cash=200000, fx_rates={"USD": 1400})
        return build_portfolio_ontology(
            [position],
            portfolio,
            portfolio_id="rulebox-profit-threshold-" + strategy_profile,
            runtime_context={"account": {"investmentStrategyProfile": strategy_profile}},
        )

    def flow_pressure_graph(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            quantity=5,
            sellable_quantity=5,
            average_price=210000,
            current_price=208000,
            market_value=1040000,
            profit_loss=-10000,
            profit_loss_rate=-1.0,
            volume_ratio=1.6,
            bid_ask_imbalance=-24.0,
            trade_strength=91.0,
            trading_value=9000000000,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-flow-test")

    def retail_dip_buying_graph(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            quantity=7,
            sellable_quantity=7,
            average_price=2343143,
            current_price=1913000,
            market_value=13391000,
            profit_loss_rate=-18.1,
            ma20=2449050,
            ma60=2015417,
            ma20_distance=-21.9,
            ma60_distance=-5.1,
            change_rate=3.69,
            foreign_net_volume=-665995,
            institution_net_volume=-701427,
            individual_net_volume=1362458,
            volume_ratio=1.3,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-retail-dip-risk")

    def smart_money_accumulation_graph(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            quantity=10,
            sellable_quantity=10,
            average_price=327000,
            current_price=296250,
            market_value=2962500,
            profit_loss_rate=-9.4,
            ma20=324112,
            ma60=289838,
            ma20_distance=-8.6,
            ma60_distance=2.2,
            change_rate=1.6,
            foreign_net_volume=845552,
            institution_net_volume=1107761,
            individual_net_volume=-1739937,
            volume_ratio=0.4,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-smart-money-accumulation")

    def profitable_momentum_graph(self, symbol="MSTR", settings=None, metadata=None):
        position = Position(
            symbol=symbol,
            name="Strategy" if symbol == "MSTR" else "Tesla",
            market="US",
            currency="USD",
            quantity=10,
            sellable_quantity=10,
            average_price=88,
            current_price=105,
            market_value=1050,
            profit_loss=170,
            profit_loss_rate=19.3,
            change_rate=2.2,
            ma5=101,
            ma20=100,
            ma60=95,
            ma5_distance=4.0,
            ma20_distance=5.0,
            ma60_distance=10.5,
            volume_ratio=1.3,
            trade_strength=108,
            bid_ask_imbalance=12,
            trading_value=100000000,
            sector="디지털자산" if symbol == "MSTR" else "모빌리티",
            updated_at="2026-07-16T00:00:00Z",
            source_as_of="2026-07-16T00:00:00Z",
            data_quality="actual",
        )
        portfolio = portfolio_summary([position], account_cash=10000, fx_rates={"USD": 1400})
        return build_portfolio_ontology(
            [position],
            portfolio,
            portfolio_id="rulebox-profile-" + symbol.lower(),
            runtime_context={"settings": settings or {}, "metadata": metadata or {}},
        )

    def data_quality_gap_graph(self):
        position = Position(
            symbol="035420",
            name="NAVER",
            market="KR",
            currency="KRW",
            quantity=3,
            sellable_quantity=3,
            average_price=204000,
            current_price=197200,
            market_value=591600,
            profit_loss=-20400,
            profit_loss_rate=-3.6,
            ma20=213940,
            ma60=217075,
            ma20_distance=-7.8,
            ma60_distance=-9.2,
            volume_ratio=0.8,
            trading_value=3000000000,
            sector="플랫폼",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-data-quality-test")

    def liquid_small_position_graph(self):
        position = Position(
            symbol="005930",
            name="삼성전자",
            market="KR",
            currency="KRW",
            quantity=10,
            sellable_quantity=10,
            average_price=327000,
            current_price=254500,
            market_value=2545000,
            profit_loss=-725000,
            profit_loss_rate=-21.6,
            ma20=319375,
            ma60=290467,
            ma20_distance=-20.3,
            ma60_distance=-12.4,
            volume=31882652,
            volume_ratio=1.6,
            trading_value=8455100000000,
            trade_strength=89.1,
            buy_volume=10445338,
            sell_volume=11162345,
            orderbook_bid_volume=1585913,
            orderbook_ask_volume=205943,
            bid_ask_imbalance=77.0,
            foreign_net_volume=-717007,
            institution_net_volume=-3216316,
            individual_net_volume=4177230,
            sector="반도체",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-execution-safe-test")

    def illiquid_large_position_graph(self):
        position = Position(
            symbol="123450",
            name="테스트소형주",
            market="KR",
            currency="KRW",
            quantity=100000,
            sellable_quantity=100000,
            average_price=1000,
            current_price=1000,
            market_value=100000000,
            profit_loss=-5000000,
            profit_loss_rate=-5.0,
            ma20=1050,
            ma60=1100,
            ma20_distance=-4.8,
            ma60_distance=-9.1,
            volume=50000,
            volume_ratio=0.4,
            trading_value=500000000,
            trade_strength=72.0,
            buy_volume=18000,
            sell_volume=32000,
            orderbook_bid_volume=1000,
            orderbook_ask_volume=8000,
            bid_ask_imbalance=-77.8,
            foreign_net_volume=-2000,
            institution_net_volume=-1500,
            individual_net_volume=3500,
            sector="테스트",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology([position], portfolio, portfolio_id="rulebox-execution-block-test")

    def direct_context_news_graph(self):
        position = Position(
            symbol="AAPL",
            name="Apple",
            market="NASDAQ",
            currency="USD",
            quantity=0,
            current_price=212,
            market_value=0,
            sector="AI",
            source="watchlist",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology(
            [position],
            portfolio,
            external_signals={
                "researchEvidence": {
                    "AAPL": [
                        {
                            "symbol": "AAPL",
                            "kind": "news",
                            "source": "Reuters",
                            "title": "Apple names a new product operations leader",
                            "summary": "Apple disclosed an executive transition that may affect product launch execution.",
                            "url": "https://example.test/apple-operations",
                            "polarity": "context",
                            "impactScore": 4,
                            "confidence": 0.82,
                            "relationScope": "direct",
                            "materialityPassed": True,
                            "materialityScore": 72,
                            "relevanceScore": 94,
                            "sourceReliability": 82,
                            "eventType": "general",
                        }
                    ]
                }
            },
            portfolio_id="rulebox-context-news-test",
        )

    def direct_ai_risk_news_graph(self):
        position = Position(
            symbol="AAPL",
            name="Apple",
            market="NASDAQ",
            currency="USD",
            quantity=2,
            current_price=212,
            market_value=424,
            sector="AI",
        )
        portfolio = portfolio_summary([position], account_cash=200000)
        return build_portfolio_ontology(
            [position],
            portfolio,
            external_signals={
                "researchEvidence": {
                    "AAPL": [
                        {
                            "symbol": "AAPL",
                            "kind": "news",
                            "source": "Reuters",
                            "title": "Apple shares fall on earnings concern",
                            "summary": "실적 우려와 주가 하락 기사입니다.",
                            "url": "https://example.test/apple-risk",
                            "polarity": "risk",
                            "impactScore": 88,
                            "confidence": 0.82,
                            "relationScope": "direct",
                            "materialityPassed": True,
                            "materialityScore": 88,
                            "relevanceScore": 96,
                            "sourceReliability": 90,
                            "eventType": "earnings",
                            "aiAnalysis": {
                                "version": "news-ai-analysis-v1",
                                "status": "ok",
                                "readScope": "title+rss-summary",
                                "relationScope": "direct",
                                "eventType": "earnings",
                                "impactPolarity": "risk",
                                "impactLabelKo": "악재",
                                "confidence": 0.82,
                                "materialityScore": 88,
                                "relevanceScore": 96,
                                "needsReview": True,
                                "summary": {
                                    "briefKo": "실적 우려가 가격 부담으로 작용할 수 있습니다.",
                                    "watchPoints": ["원문 본문 확보", "가격 반응"],
                                },
                                "riskSignals": ["실적 우려", "하락"],
                            },
                        }
                    ]
                }
            },
            portfolio_id="rulebox-ai-news-test",
        )

    def test_portfolio_ontology_builder_defaults_to_abox_only(self):
        graph = self.loss_guard_graph()

        rule_entities = [
            item
            for item in graph.entities
            if item.kind == "rule" and (item.properties or {}).get("ontologyBox") == "RuleBox"
        ]
        inference_relations = [
            item
            for item in graph.relations
            if (item.properties or {}).get("ontologyBox") == "InferenceBox"
        ]
        stock = next(item for item in graph.entities if item.entity_id == "stock:005930")

        self.assertEqual([], rule_entities)
        self.assertEqual([], inference_relations)
        self.assertEqual("ABox", stock.properties["ontologyBox"])
        self.assertEqual("ontology-abox-facts", graph.worldview["model"])
        self.assertEqual("abox-facts-only-typedb-native-rules", graph.worldview["runtimeProjectionMode"])

    def test_microstructure_missing_data_is_market_specific(self):
        us_position = Position(symbol="AAPL", name="Apple", market="NASDAQ", currency="USD")
        kr_position = Position(symbol="005930", name="삼성전자", market="KR", currency="KRW")

        self.assertEqual([], missing_market_microstructure_fields(us_position))
        self.assertIn("tradeStrength", {item["field"] for item in missing_market_microstructure_fields(kr_position)})

    def test_typedb_projection_promotes_rulebox_query_keys(self):
        graph = ontology_seed_graph(default_graph_inference_rules()[:1])
        repository = TypeDBOntologyGraphRepository("http://typedb.example.test")

        rule_row = next(item for item in repository.rows_for_entities(graph) if item["id"] == "rule:graph.loss_guard.breakdown.v1")
        stock_class_row = next(item for item in repository.rows_for_entities(graph) if item["id"] == "tbox-class:Stock")
        holds_relation_row = next(item for item in repository.rows_for_entities(graph) if item["id"] == "tbox-relation:HOLDS")
        condition_row = next(item for item in repository.rows_for_entities(graph) if item["id"] == "rule-condition:graph.loss_guard.breakdown.v1:validated-model-signal:graph.loss_guard.breakdown.v1")
        template_row = next(item for item in repository.rows_for_entities(graph) if item["id"] == "relation-template:graph.loss_guard.breakdown.v1:0")
        schema_text = repository.schema_query()
        query_text = "\n".join(repository.insert_queries(graph))

        self.assertEqual("Stock", stock_class_row["className"])
        self.assertEqual("HOLDS", holds_relation_row["relationTypeName"])
        self.assertEqual("TBox", stock_class_row["ontologyBox"])
        self.assertEqual("TBox", holds_relation_row["ontologyBox"])
        self.assertEqual("RuleBox", rule_row["ontologyBox"])
        self.assertTrue(rule_row["tboxVersion"])
        self.assertEqual("graph.loss_guard.breakdown.v1", rule_row["ruleId"])
        self.assertEqual("relation", condition_row["conditionKind"])
        self.assertEqual("HAS_MODEL_SIGNAL", condition_row["conditionRelationType"])
        self.assertEqual("statistical-model-hypothesis-evidence", condition_row["conditionTargetKind"])
        self.assertIn("signalType", condition_row["conditionTargetFields"])
        self.assertIn("hypothesisFamilyId", condition_row["conditionTargetFields"])
        self.assertIn("hypothesisContractId", condition_row["conditionTargetFields"])
        self.assertIn("graph.loss_guard.breakdown.v1", condition_row["propertiesJson"])
        self.assertEqual("HAS_INFERRED_RISK", template_row["derivationRelationType"])
        self.assertEqual("risk", template_row["derivationTargetKind"])
        self.assertEqual("LOSS_REDUCE", template_row["derivationDecisionStage"])
        self.assertEqual("review", template_row["derivationActionLevel"])
        self.assertEqual("risk", template_row["derivationEvidenceRole"])
        self.assertIn("attribute ontology-rule-id", schema_text)
        self.assertIn("attribute ontology-json", schema_text)
        self.assertIn("attribute ontology-tbox-class", schema_text)
        self.assertIn('has ontology-box "TBox"', query_text)
        self.assertIn('has ontology-box "RuleBox"', query_text)
        self.assertIn('has ontology-tbox-class "Stock"', query_text)
        self.assertIn('has ontology-relation-type "HOLDS"', query_text)

    def test_typedb_run_rulebox_materializes_inferencebox_from_typedb_projection(self):
        class CapturingTypeDBRepository(TypeDBOntologyGraphRepository):
            def __init__(self, graph):
                super().__init__("127.0.0.1:1729")
                self._last_graph = graph
                self.saved_inferencebox_graph = None

            def write_inferencebox_graph(self, graph):
                self.saved_inferencebox_graph = graph
                return {"configured": True, "saved": True, "status": "ok", "graphStore": "typedb"}

            def has_box_rows(self, box):
                return str(box or "") == "ABox"

            def active_abox_metadata(self):
                source_snapshot_id = next(
                    (
                        str(
                            (entity.properties or {}).get("aboxSnapshotId")
                            or (entity.properties or {}).get("snapshotId")
                            or ""
                        )
                        for entity in self._last_graph.entities
                        if str(
                            (entity.properties or {}).get("aboxSnapshotId")
                            or (entity.properties or {}).get("snapshotId")
                            or ""
                        )
                    ),
                    "",
                )
                return {
                    "status": "ok",
                    "aboxSnapshotId": source_snapshot_id or "abox-snapshot:test",
                }

            def load_graph_for_native_matches(self, native_match_result, rules=None):
                # A real TypeDB ABox read always attaches the active legacy
                # snapshot ID to its source facts. Keep this test double on
                # that same persistence contract so generation validation is
                # exercised rather than bypassed.
                return self._last_graph

            def rulebox_snapshot(self):
                return {
                    "configured": True,
                    "saved": True,
                    "status": "ok",
                    "graphStore": "typedb",
                    "rules": [default_graph_inference_rules()[0].to_dict()],
                    "ruleCount": 1,
                }

            def clear_inferencebox(self):
                return {"configured": True, "status": "ok", "graphStore": "typedb", "clearedBox": "InferenceBox"}

            def match_typedb_native_rules(self, rules, target_symbols=None, **_kwargs):
                rule = list(rules or [])[0]
                return {
                    "status": "ok",
                    "graphStore": "typedb",
                    "nativeQueryUsed": True,
                    "executedRuleCount": 1,
                    "skippedRuleCount": 0,
                    "matchedCount": 1,
                    "matches": [{
                        "ruleId": rule.rule_id,
                        "nativeRuleId": "typedb.native." + rule.rule_id,
                        "sourceId": "stock:005930",
                        "matchedConditions": [{"conditionId": "holding-source"}],
                        "evidenceRelationIds": [],
                        "confidence": 0.86,
                    }],
                }

        repository = CapturingTypeDBRepository(self.loss_guard_graph())

        result = repository.run_rulebox({})

        self.assertEqual("ok", result["status"])
        self.assertEqual("typedb", result["graphStore"])
        self.assertEqual("typedb-native-rule-materialized", result["reasoningMode"])
        self.assertFalse(result["typedbBootstrapReasoningUsed"])
        self.assertTrue(result["nativeTypeDbReasoningUsed"])
        self.assertTrue(result["pythonBootstrapDisabled"])
        self.assertGreater(result["statementCount"], 0)
        self.assertIn("HAS_INFERRED_RISK", result["relationTypes"])
        self.assertEqual({}, result["clearResult"])
        self.assertIsNotNone(repository.saved_inferencebox_graph)
        self.assertTrue(repository.saved_inferencebox_graph.entities)
        self.assertTrue(all((item.properties or {}).get("nativeTypeDbReasoned") for item in repository.saved_inferencebox_graph.entities))
        self.assertTrue(all((item.properties or {}).get("typedbNativeRuleReasoned") for item in repository.saved_inferencebox_graph.entities))
        self.assertTrue(repository.saved_inferencebox_graph.relations)
        self.assertTrue(all((item.properties or {}).get("symbol") == "005930" for item in repository.saved_inferencebox_graph.relations))

    def test_default_rulebox_covers_materiality_and_trend_transition_rules(self):
        rules = default_graph_inference_rules()
        governed_rules = governed_graph_inference_rules()
        rule_ids = {item.rule_id for item in rules}
        graph = rulebox_graph_from_rules(rules)
        governed_graph = rulebox_graph_from_rules(governed_rules)
        repository = TypeDBOntologyGraphRepository("http://typedb.example.test")
        condition_rows = repository.rows_for_entities(graph)
        governed_condition_rows = repository.rows_for_entities(governed_graph)
        support_transition = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.watchlist.trend_transition.support.v1:validated-model-signal:graph.watchlist.trend_transition.support.v1"
        )
        risk_transition = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.holding.trend_transition.risk.v1:validated-model-signal:graph.holding.trend_transition.risk.v1"
        )
        direct_news_risk = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.news.direct_material_risk.v1:direct-material-risk"
        )
        direct_news_context = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.news.direct_material_context.v1:direct-material-context"
        )
        fact_change_gate = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.materiality.alert_candidate.v1:price-downside-delta"
        )
        microstructure_gap = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.data_quality.microstructure_gap.v1:microstructure-missing"
        )
        news_analysis_conflict = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.data_quality.news_analysis_conflict.v1:news-analysis-conflict"
        )
        execution_slippage = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.execution.liquidity_or_slippage_block.v1:visible-depth-block"
        )
        price_reclaim_quality = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.price.reclaim.thesis_support.v1:microstructure-data-usable"
        )
        portfolio_concentration = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.portfolio.concentration.review.v1:sector-concentration-ratio"
        )
        strategy_risk_budget = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.loss_guard.breakdown.v1:strategy-risk-budget"
        )
        strategy_profit_policy = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.profit_protect.trend_break.v1:strategy-profit-policy"
        )
        watchlist_strategy_role = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.watchlist.trend_transition.support.v1:watchlist-strategy-role"
        )
        retail_dip_buying = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.investor_flow.retail_dip_buying_risk.v1:retail-dip-buying-risk"
        )
        add_buy_volume = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.loss_smart_money.add_buy_review.v1:volume-confirmation"
        )
        add_buy_gap_guard = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.loss_smart_money.add_buy_review.v1:no-severe-microstructure-gap"
        )
        winner_add_ma5 = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.winner_momentum.add_buy_review.v1:validated-model-signal:graph.winner_momentum.add_buy_review.v1"
        )
        winner_add_profile = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.winner_momentum.add_buy_review.v1:holding-profit"
        )
        winner_add_volume = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.winner_momentum.add_buy_review.v1:validated-model-signal:graph.winner_momentum.add_buy_review.v1"
        )
        loss_rebound_ma5 = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.loss_rebound.trim_moderation.v1:validated-model-signal:graph.loss_rebound.trim_moderation.v1"
        )
        loss_rebound_smart_money = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.loss_rebound.trim_moderation.v1:holding-loss"
        )
        aggressive_profile = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.aggressive.loss_recovery.add_buy_review.v1:aggressive-profile"
        )
        profit_momentum_profile = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.profit_momentum.hold_add_review.v1:growth-or-aggressive-profile"
        )
        profit_momentum_ma20 = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.profit_momentum.hold_add_review.v1:validated-model-signal:graph.profit_momentum.hold_add_review.v1"
        )
        watchlist_direct_role = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.watchlist.direct_momentum.entry.v1:watchlist-role"
        )
        watchlist_direct_volume = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.watchlist.direct_momentum.entry.v1:validated-model-signal:graph.watchlist.direct_momentum.entry.v1"
        )
        profile_averaging_policy = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.instrument_profile.averaging_down_policy.v1:profile-avoid-averaging-down"
        )
        coverage_gap = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.coverage.gap.validation_state.v1:coverage-gap"
        )
        bitcoin_profile = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.instrument_profile.bitcoin_sensitive.crypto_linkage.v1:btc-sensitive-archetype"
        )
        bitcoin_exposure = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.instrument_profile.bitcoin_sensitive.crypto_linkage.v1:btc-exposure"
        )
        preferred_rate_factor = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.instrument_profile.preferred_income.rate_sensitivity.v1:rate-sensitive-factor"
        )
        preferred_rate_signal = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.instrument_profile.preferred_income.rate_sensitivity.v1:preferred-rate-rise"
        )
        cyclical_growth_profile = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.instrument_profile.cyclical_growth.recovery_add_review.v1:growth-cyclical-archetype"
        )
        macro_sensitivity = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.macro.rate.rise.confirmed_risk.v1:rate-factor-sensitivity"
        )
        macro_rate_rise = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.macro.rate.rise.confirmed_risk.v1:rate-five-observation-rise"
        )
        crypto_exposure = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.crypto.exposure.volatility_risk.v1:crypto-exposure-source"
        )
        crypto_downside = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.crypto.exposure.volatility_risk.v1:crypto-24h-downside"
        )
        fx_exposure = next(
            item
            for item in governed_condition_rows
            if item["id"] == "rule-condition:graph.fx.usdkrw.exposure.regime.v1:usdkrw-high-exposure"
        )
        news_quality = next(
            item
            for item in condition_rows
            if item["id"] == "rule-condition:graph.news.quality.validation_state.v1:news-quality-risk"
        )

        self.assertIn("graph.materiality.alert_candidate.v1", rule_ids)
        self.assertIn("graph.notification.loss_policy_threshold.v1", rule_ids)
        self.assertIn("graph.notification.profit_policy_threshold.v1", rule_ids)
        self.assertIn("graph.loss_smart_money.defense.v1", rule_ids)
        self.assertIn("graph.investor_flow.smart_money_accumulation.v1", rule_ids)
        self.assertIn("graph.investor_flow.retail_dip_buying_risk.v1", rule_ids)
        self.assertIn("graph.investor_flow.smart_money_outflow_risk.v1", rule_ids)
        self.assertIn("graph.loss_smart_money.add_buy_review.v1", rule_ids)
        self.assertIn("graph.winner_momentum.add_buy_review.v1", rule_ids)
        self.assertIn("graph.loss_rebound.trim_moderation.v1", rule_ids)
        self.assertIn("graph.aggressive.loss_recovery.add_buy_review.v1", rule_ids)
        self.assertIn("graph.profit_momentum.hold_add_review.v1", rule_ids)
        self.assertIn("graph.instrument_profile.averaging_down_policy.v1", rule_ids)
        self.assertIn("graph.instrument_profile.bitcoin_sensitive.crypto_linkage.v1", rule_ids)
        self.assertIn("graph.instrument_profile.preferred_income.rate_sensitivity.v1", rule_ids)
        self.assertIn("graph.instrument_profile.cyclical_growth.recovery_add_review.v1", rule_ids)
        self.assertIn("graph.averaging_down.risk_guard.v1", rule_ids)
        self.assertIn("graph.holding.trend_transition.risk.v1", rule_ids)
        self.assertIn("graph.watchlist.trend_transition.support.v1", rule_ids)
        self.assertIn("graph.flow.sell_pressure.v1", rule_ids)
        self.assertIn("graph.flow.accumulation.entry.v1", rule_ids)
        self.assertIn("graph.watchlist.direct_momentum.entry.v1", rule_ids)
        self.assertIn("graph.news.direct_material_risk.v1", rule_ids)
        self.assertIn("graph.news.direct_material_support.v1", rule_ids)
        self.assertIn("graph.news.direct_material_context.v1", rule_ids)
        self.assertIn("graph.disclosure.event_risk.v1", rule_ids)
        self.assertIn("graph.data_quality.action_block.v1", rule_ids)
        self.assertIn("graph.data_quality.news_analysis_conflict.v1", rule_ids)
        self.assertIn("graph.execution.liquidity_or_slippage_block.v1", rule_ids)
        self.assertIn("graph.factor.position_crowding.v1", rule_ids)
        self.assertIn("graph.benchmark.beta.context.v1", rule_ids)
        self.assertIn("graph.price.reclaim.thesis_support.v1", rule_ids)
        self.assertIn("graph.coverage.gap.validation_state.v1", rule_ids)
        self.assertNotIn("graph.macro.regime.risk.v1", rule_ids)
        self.assertIn("graph.macro.rate.rise.confirmed_risk.v1", rule_ids)
        self.assertIn("graph.macro.rate.fall.confirmed_support.v1", rule_ids)
        self.assertIn("graph.macro.rate.high_regime_entry.risk.v1", rule_ids)
        self.assertIn("graph.macro.rate.stock_divergence.support.v1", rule_ids)
        self.assertIn("graph.macro.curve.inversion_entry.risk.v1", rule_ids)
        self.assertIn("graph.fx.usdkrw.exposure.regime.v1", rule_ids)
        self.assertIn("graph.crypto.exposure.volatility_risk.v1", rule_ids)
        self.assertIn("graph.earnings.surprise.risk.v1", rule_ids)
        self.assertIn("graph.earnings.surprise.support.v1", rule_ids)
        self.assertIn("graph.regulatory.event.risk.v1", rule_ids)
        self.assertIn("graph.news.quality.validation_state.v1", rule_ids)
        self.assertIn("graph.valuation.high_beta_or_expensive.review.v1", rule_ids)
        self.assertIn("graph.portfolio.concentration.review.v1", rule_ids)
        self.assertEqual("HAS_MODEL_SIGNAL", support_transition["conditionRelationType"])
        self.assertEqual("statistical-model-hypothesis-evidence", support_transition["conditionTargetKind"])
        self.assertEqual("HAS_MODEL_SIGNAL", risk_transition["conditionRelationType"])
        self.assertEqual("statistical-model-hypothesis-evidence", risk_transition["conditionTargetKind"])
        flow_rules = {
            item.rule_id: item
            for item in rules
            if item.rule_id in {
                "graph.flow.sell_pressure.v1",
                "graph.loss_smart_money.defense.v1",
                "graph.investor_flow.smart_money_accumulation.v1",
            }
        }
        self.assertEqual(3, len(flow_rules))
        self.assertTrue(all(not item.enabled for item in flow_rules.values()))
        self.assertTrue(all(
            item.resolved_knowledge_basis.migration_disposition == "awaiting-governed-model-scorer"
            for item in flow_rules.values()
        ))
        self.assertEqual(["direct"], direct_news_risk["conditionTargetRelationScopes"])
        self.assertEqual(["risk"], direct_news_risk["conditionTargetPolarities"])
        self.assertTrue(direct_news_risk["conditionTargetMaterialityPassed"])
        self.assertEqual(["material", "notable"], direct_news_risk["conditionTargetMaterialityStates"])
        self.assertEqual(["direct"], direct_news_context["conditionTargetRelationScopes"])
        self.assertEqual(["context"], direct_news_context["conditionTargetPolarities"])
        self.assertEqual(["material", "notable"], direct_news_context["conditionTargetMaterialityStates"])
        self.assertEqual("HAS_OBSERVATION", fact_change_gate["conditionRelationType"])
        self.assertEqual("fact-change", fact_change_gate["conditionTargetKind"])
        self.assertEqual(["currentPrice"], fact_change_gate["conditionTargetFields"])
        self.assertEqual(["market-microstructure"], microstructure_gap["conditionTargetDataScopes"])
        self.assertEqual(["news-analysis-conflict"], news_analysis_conflict["conditionTargetDataScopes"])
        self.assertEqual(["risk"], news_analysis_conflict["conditionRelationEvidenceRoles"])
        self.assertEqual("any", execution_slippage["conditionRole"])
        self.assertEqual(["positionToBidDepthPct"], execution_slippage["conditionTargetFields"])
        self.assertEqual(30.0, execution_slippage["conditionTargetMinValue"])
        self.assertEqual("required", price_reclaim_quality["conditionRole"])
        self.assertEqual("HAS_DATA_QUALITY", price_reclaim_quality["conditionRelationType"])
        self.assertEqual("data-quality-status", price_reclaim_quality["conditionTargetKind"])
        self.assertEqual(["market-microstructure"], price_reclaim_quality["conditionTargetDataScopes"])
        self.assertEqual("any", portfolio_concentration["conditionRole"])
        self.assertEqual("HAS_EXPOSURE", portfolio_concentration["conditionRelationType"])
        self.assertEqual("sector-exposure", portfolio_concentration["conditionTargetKind"])
        self.assertEqual("HAS_RISK_BUDGET", strategy_risk_budget["conditionRelationType"])
        self.assertEqual("risk-budget", strategy_risk_budget["conditionTargetKind"])
        self.assertEqual("HAS_PROFIT_POLICY", strategy_profit_policy["conditionRelationType"])
        self.assertEqual("profit-policy", strategy_profit_policy["conditionTargetKind"])
        self.assertEqual("HAS_POSITION_ROLE", watchlist_strategy_role["conditionRelationType"])
        self.assertEqual("position-role", watchlist_strategy_role["conditionTargetKind"])
        self.assertEqual("foreignNetVolume", retail_dip_buying["conditionField"])
        self.assertEqual("<", retail_dip_buying["conditionOperator"])
        self.assertEqual("any", add_buy_volume["conditionRole"])
        self.assertEqual(["volumeRatio"], add_buy_volume["conditionTargetFields"])
        self.assertEqual(1.0, add_buy_volume["conditionTargetMinValue"])
        self.assertEqual("not", add_buy_gap_guard["conditionRole"])
        self.assertEqual("HAS_MODEL_SIGNAL", winner_add_ma5["conditionRelationType"])
        self.assertEqual("profitLossRate", winner_add_profile["conditionField"])
        self.assertEqual("HAS_MODEL_SIGNAL", winner_add_volume["conditionRelationType"])
        self.assertEqual("HAS_MODEL_SIGNAL", loss_rebound_ma5["conditionRelationType"])
        self.assertEqual("profitLossRate", loss_rebound_smart_money["conditionField"])
        self.assertEqual("investmentStrategyProfile", aggressive_profile["conditionField"])
        self.assertEqual("aggressive", aggressive_profile["conditionValueString"])
        self.assertEqual("investmentStrategyProfile", profit_momentum_profile["conditionField"])
        self.assertIn("growth", profit_momentum_profile["conditionValueString"])
        self.assertIn("aggressive", profit_momentum_profile["conditionValueString"])
        self.assertEqual("HAS_MODEL_SIGNAL", profit_momentum_ma20["conditionRelationType"])
        self.assertEqual("positionRole", watchlist_direct_role["conditionField"])
        self.assertEqual("watchlist", watchlist_direct_role["conditionValueString"])
        self.assertEqual("HAS_MODEL_SIGNAL", watchlist_direct_volume["conditionRelationType"])
        self.assertEqual("HAS_INSTRUMENT_PROFILE", profile_averaging_policy["conditionRelationType"])
        self.assertEqual(["avoidAveragingDown"], profile_averaging_policy["conditionTargetFields"])
        self.assertEqual("HAS_COVERAGE_GAP", coverage_gap["conditionRelationType"])
        self.assertEqual("coverage-gap", coverage_gap["conditionTargetKind"])
        self.assertEqual(["risk"], coverage_gap["conditionRelationEvidenceRoles"])
        self.assertEqual("HAS_ARCHETYPE", bitcoin_profile["conditionRelationType"])
        self.assertEqual(["BitcoinProxy", "BitcoinSensitiveIncome"], bitcoin_profile["conditionTargetInstrumentArchetypes"])
        self.assertEqual("HAS_CRYPTO_EXPOSURE", bitcoin_exposure["conditionRelationType"])
        self.assertEqual(["BTC"], bitcoin_exposure["conditionTargetCryptoSymbols"])
        self.assertEqual("HAS_FACTOR_SENSITIVITY", preferred_rate_factor["conditionRelationType"])
        self.assertEqual(["rate"], preferred_rate_factor["conditionTargetFactors"])
        self.assertEqual(["high"], preferred_rate_factor["conditionTargetSensitivityLevels"])
        self.assertEqual("HAS_RATE_SENSITIVITY", preferred_rate_signal["conditionRelationType"])
        self.assertEqual(["rateSeriesId", "delta5dBp"], preferred_rate_signal["conditionTargetFields"])
        self.assertEqual(10.0, preferred_rate_signal["conditionTargetMinValue"])
        self.assertEqual("any", preferred_rate_signal["conditionRole"])
        self.assertEqual(["SemiconductorHBM", "CyclicalGrowth", "SemiconductorCyclical", "AIGrowth"], cyclical_growth_profile["conditionTargetInstrumentArchetypes"])
        self.assertEqual("HAS_FACTOR_SENSITIVITY", macro_sensitivity["conditionRelationType"])
        self.assertEqual(["rate"], macro_sensitivity["conditionTargetFactors"])
        self.assertEqual(["medium", "high"], macro_sensitivity["conditionTargetSensitivityLevels"])
        self.assertEqual("HAS_RATE_SENSITIVITY", macro_rate_rise["conditionRelationType"])
        self.assertEqual("interest-rate", macro_rate_rise["conditionTargetKind"])
        self.assertEqual(["rateSeriesId", "delta5dBp"], macro_rate_rise["conditionTargetFields"])
        self.assertEqual(15.0, macro_rate_rise["conditionTargetMinValue"])
        self.assertEqual("required", macro_rate_rise["conditionRole"])
        self.assertEqual("HAS_CRYPTO_EXPOSURE", crypto_exposure["conditionRelationType"])
        self.assertEqual("crypto-exposure", crypto_exposure["conditionTargetKind"])
        self.assertEqual("HAS_CRYPTO_EXPOSURE", crypto_downside["conditionRelationType"])
        self.assertEqual(["change24h"], crypto_downside["conditionTargetFields"])
        self.assertEqual("any", crypto_downside["conditionRole"])
        self.assertEqual("HAS_FX_EXPOSURE", fx_exposure["conditionRelationType"])
        self.assertEqual("fx-rate", fx_exposure["conditionTargetKind"])
        self.assertEqual(1450.0, fx_exposure["conditionTargetMinValue"])
        self.assertEqual("HAS_DATA_QUALITY", news_quality["conditionRelationType"])
        self.assertEqual(["news-quality"], news_quality["conditionTargetDataScopes"])

    def test_partial_data_gaps_constrain_but_primary_quote_failure_blocks_judgement(self):
        rules = {rule.rule_id: rule for rule in default_graph_inference_rules()}
        partial_gap_rules = {
            "graph.security_line.coverage_gap.v1",
            "graph.data_quality.microstructure_gap.v1",
            "graph.data_quality.market_snapshot_degraded.v1",
            "graph.data_quality.news_analysis_conflict.v1",
            "graph.news.ai_body_missing_review.v1",
            "graph.temporal.stale_observation.block.v1",
            "graph.temporal.coverage_gap.v1",
            "graph.coverage.gap.validation_state.v1",
            "graph.news.quality.validation_state.v1",
        }

        self.assertTrue(all(
            derivation.decision_effect != "block"
            for rule_id in partial_gap_rules
            for derivation in rules[rule_id].derivations
        ))
        self.assertTrue(any(
            derivation.decision_effect == "block"
            for derivation in rules["graph.data_quality.market_snapshot_failure_block.v1"].derivations
        ))

    def test_rulebox_payload_rejects_derivation_without_decision_stage(self):
        payload = rulebox_rules_to_payload(default_graph_inference_rules()[:1])
        payload[0]["derivations"][0]["decision_stage"] = ""

        with self.assertRaisesRegex(ValueError, "requires decision_stage"):
            rulebox_rules_from_payload({"rules": payload})

    def test_rulebox_payload_rejects_derivation_without_decision_effect(self):
        payload = rulebox_rules_to_payload(default_graph_inference_rules()[:1])
        payload[0]["derivations"][0]["decision_effect"] = ""

        with self.assertRaisesRegex(ValueError, "requires a valid decision_effect"):
            rulebox_rules_from_payload({"rules": payload})

    def test_rulebox_save_graph_can_skip_tbox_for_lightweight_sync(self):
        graph = rulebox_graph_from_rules(default_graph_inference_rules(), include_tbox=False)

        self.assertTrue(any(item.kind == "rule" and (item.properties or {}).get("ontologyBox") == "RuleBox" for item in graph.entities))
        self.assertFalse(any((item.properties or {}).get("ontologyBox") == "TBox" for item in graph.entities))
        self.assertTrue(all((item.properties or {}).get("ontologyBox") == "RuleBox" for item in graph.entities))
        self.assertTrue(all((item.properties or {}).get("ontologyBox") == "RuleBox" for item in graph.relations))

    def test_market_proxy_relative_performance_blocks_closed_stock_reference(self):
        position = Position(
            symbol="MSTR",
            name="Strategy",
            market="US",
            currency="USD",
            quantity=10,
            average_price=88,
            current_price=105,
            market_value=1050,
            profit_loss_rate=19.3,
            change_rate=2.2,
            sector="디지털자산",
            updated_at="2026-07-16T00:00:00Z",
            source_as_of="2026-07-16T00:00:00Z",
            freshness_status="last-close",
            market_session="closed",
            data_quality="reference",
        )
        portfolio = portfolio_summary([position], account_cash=10000, fx_rates={"USD": 1400})
        graph = build_portfolio_ontology(
            [position],
            portfolio,
            portfolio_id="closed-relative-performance",
            runtime_context={
                "metadata": {
                    "marketProxyQuotes": {
                        "BTC": {
                            "symbol": "BTC",
                            "currentPrice": 64000,
                            "changeRate": -3.2,
                            "updatedAt": "2026-07-16T00:00:00Z",
                            "sourceAsOf": "2026-07-16T00:00:00Z",
                            "judgementEvidenceUsable": True,
                        }
                    }
                }
            },
        )
        relative = next(
            item
            for item in graph.entities
            if item.kind == "relative-performance-observation"
            and (item.properties or {}).get("proxySymbol") == "BTC"
        )

        self.assertFalse(relative.properties["stockEvidenceUsable"])
        self.assertTrue(relative.properties["proxyEvidenceUsable"])
        self.assertFalse(relative.properties["judgementEvidenceUsable"])
        self.assertEqual("partial", relative.properties["dataState"])

    def test_watchlist_rulebox_templates_carry_entry_only_action_policy(self):
        rules = default_graph_inference_rules()
        watchlist_rule = next(item for item in rules if item.rule_id == "graph.watchlist.trend_transition.support.v1")
        watchlist_derivations = watchlist_rule.derivations
        graph = rulebox_graph_from_rules([watchlist_rule])
        repository = TypeDBOntologyGraphRepository("http://typedb.example.test")
        entity_rows = repository.rows_for_entities(graph)
        template_rows = [
            item
            for item in entity_rows
            if item["kind"] == "relation-template" and item["ontologyBox"] == "RuleBox"
        ]

        self.assertTrue(watchlist_derivations)
        for derivation in watchlist_derivations:
            self.assertEqual("watchlist", derivation.target_role)
            self.assertEqual("ENTRY_ONLY", derivation.action_policy)
            self.assertEqual(["BUY", "HOLD", "AVOID"], derivation.allowed_actions)
            self.assertEqual(["ADD", "TRIM", "SELL"], derivation.blocked_actions)
        self.assertTrue(template_rows)
        self.assertEqual("watchlist", template_rows[0]["derivationTargetRole"])
        self.assertEqual("ENTRY_ONLY", template_rows[0]["derivationActionPolicy"])
        self.assertEqual(["BUY", "HOLD", "AVOID"], template_rows[0]["derivationAllowedActions"])
        self.assertEqual(["ADD", "TRIM", "SELL"], template_rows[0]["derivationBlockedActions"])
        self.assertEqual("support", template_rows[0]["derivationDecisionEffect"])

    def test_rulebox_snapshot_fails_closed_when_decision_effect_is_lost_on_read(self):
        default_rules = default_graph_inference_rules()
        graph = ontology_seed_graph(default_rules)
        repository = TypeDBOntologyGraphRepository("http://typedb.example.test")
        entity_rows = repository.rows_for_entities(graph)
        derivations = [
            dict(item)
            for item in entity_rows
            if item["kind"] == "relation-template" and item["ontologyBox"] == "RuleBox"
        ]
        broken = derivations[0]
        broken["decisionEffect"] = ""
        broken["derivationDecisionEffect"] = ""
        properties = json.loads(str(broken["propertiesJson"]))
        properties["derivation"]["decision_effect"] = ""
        broken["propertiesJson"] = json.dumps(properties, ensure_ascii=False)
        rowsets = {
            "rules": [item for item in entity_rows if item["kind"] == "rule" and item["ontologyBox"] == "RuleBox"],
            "conditions": [item for item in entity_rows if item["kind"] == "rule-condition" and item["ontologyBox"] == "RuleBox"],
            "derivations": derivations,
            "relationTypes": [],
            "versions": [],
        }

        snapshot = rulebox_snapshot_from_rows(rowsets, source="test")

        self.assertEqual("invalid-rulebox", snapshot["status"])
        self.assertEqual([], snapshot["rules"])
        self.assertIn("decision_effect", snapshot["reason"])

    def test_inference_trace_ledger_reconstructs_rulebox_audit_path(self):
        rulebox = {
            "status": "ok",
            "rules": [
                {
                    "rule_id": "graph.loss_guard.breakdown.v1",
                    "label": "손실 방어 추론",
                    "prompt_hint": "손실과 기준선 이탈을 함께 확인합니다.",
                    "conditions": [
                        {"condition_id": "holding-loss", "kind": "subject_property", "description": "손실 구간", "field": "profitLossRate", "operator": "<=", "value": -8},
                        {"condition_id": "ma-break", "kind": "relation", "description": "기준선 이탈", "relation_type": "BREAKS_LEVEL", "min_weight": 0.6},
                    ],
                    "derivations": [
                        {"relation_type": "HAS_INFERRED_RISK", "target_kind": "risk", "target_label": "손실 방어 리스크", "polarity": "risk", "evidence_role": "risk", "decision_stage": "LOSS_REDUCE"}
                    ],
                }
            ],
        }
        rowsets = {
            "entityCounts": [{"entityCount": 2, "nativeEntityCount": 2}],
            "relationCounts": [{"relationCount": 1, "nativeRelationCount": 1}],
            "traceCounts": [{"traceCount": 1, "nativeTraceCount": 1}],
            "entities": [
                {
                    "id": "inference-trace:005930:graph.loss_guard.breakdown.v1",
                    "label": "삼성전자 · 손실 방어 추론",
                    "kind": "inference-trace",
                    "symbol": "005930",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "confidence": 0.86,
                    "nativeTypeDbReasoned": True,
                    "propertiesJson": json.dumps({
                        "matchedConditions": [
                            {"conditionId": "holding-loss", "kind": "subject_property", "field": "profitLossRate", "operator": "<=", "value": -8},
                            {"conditionId": "ma-break", "kind": "relation", "relationType": "BREAKS_LEVEL", "relationId": "rel:ma-break"},
                        ],
                        "evidenceRelationIds": ["rel:ma-break"],
                        "promptHint": "손실과 기준선 이탈을 함께 확인합니다.",
                    }),
                },
                {
                    "id": "risk:005930:loss-guard-breakdown",
                    "label": "삼성전자 손실 방어 리스크",
                    "kind": "risk",
                    "symbol": "005930",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "sourceTraceId": "inference-trace:005930:graph.loss_guard.breakdown.v1",
                    "nativeTypeDbReasoned": True,
                },
            ],
            "relations": [
                {
                    "type": "HAS_INFERRED_RISK",
                    "source": "stock:005930",
                    "sourceLabel": "삼성전자",
                    "target": "risk:005930:loss-guard-breakdown",
                    "targetLabel": "삼성전자 손실 방어 리스크",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "symbol": "005930",
                    "decisionStage": "LOSS_REDUCE",
                    "evidenceRole": "risk",
                    "reviewLevel": "check",
                    "dataState": "sufficient",
                    "inferenceTraceId": "inference-trace:005930:graph.loss_guard.breakdown.v1",
                    "nativeTypeDbReasoned": True,
                }
            ],
            "traces": [
                {
                    "id": "inference-trace:005930:graph.loss_guard.breakdown.v1",
                    "label": "삼성전자 · 손실 방어 추론",
                    "symbol": "005930",
                    "ruleId": "graph.loss_guard.breakdown.v1",
                    "confidence": 0.86,
                    "matchedConditionIds": ["holding-loss", "ma-break"],
                    "matchedConditions": [
                        {"conditionId": "holding-loss", "kind": "subject_property"},
                        {"conditionId": "ma-break", "kind": "relation", "relationId": "rel:ma-break"},
                    ],
                    "evidenceRelationIds": ["rel:ma-break"],
                    "nativeTypeDbReasoned": True,
                }
            ],
        }

        inferencebox = inferencebox_snapshot_from_rows(rowsets, source="test", symbols=["005930"])
        payload = inference_trace_ledger_payload(inferencebox, rulebox=rulebox, symbols=["005930"])

        self.assertEqual("ok", payload["status"])
        self.assertEqual(1, payload["summary"]["ledgerCount"])
        self.assertEqual(2, payload["summary"]["matchedConditionCount"])
        row = payload["rows"][0]
        self.assertEqual("complete", row["status"])
        self.assertEqual("LOSS_REDUCE", row["decisionStage"])
        self.assertEqual(["HAS_INFERRED_RISK"], row["relationTypes"])
        self.assertEqual(["matched", "matched"], [item["status"] for item in row["conditions"]])
        self.assertEqual("rel:ma-break", row["conditions"][1]["evidenceRelationId"])
        self.assertEqual("Derived output", row["stages"][3]["label"])


if __name__ == "__main__":
    unittest.main()
