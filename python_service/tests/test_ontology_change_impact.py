import unittest

from digital_twin.domain.ontology_change_impact import (
    build_inference_impact_plan,
    family_for_relation,
    rule_condition_dependency_profile,
    rule_dependency_profile,
    scope_delta,
)
from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_scopes import apply_scoped_abox_identity
from digital_twin.domain.ontology_tbox import tbox_class_def, tbox_relation_def
from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.infrastructure.typedb_ontology import (
    typedb_inferencebox_graph,
    typedb_native_rule_execution_selection,
)


class OntologyChangeImpactTests(unittest.TestCase):
    def scope_graph(self):
        return PortfolioOntology(
            "main",
            entities=[
                OntologyEntity("stock:005930", "삼성전자", "stock", {
                    "ontologyBox": "ABox", "symbol": "005930", "currentPrice": 70000,
                }),
                OntologyEntity("price-metric:005930:currentPrice", "현재가", "price-metric", {
                    "ontologyBox": "ABox", "symbol": "005930", "currentPrice": 70000,
                }),
                OntologyEntity("flow-metric:005930:volume", "거래량", "flow-metric", {
                    "ontologyBox": "ABox", "symbol": "005930", "volume": 100,
                }),
                OntologyEntity("temporal-window:005930:5d", "5일 흐름", "temporal-window", {
                    "ontologyBox": "ABox", "symbol": "005930",
                }),
                OntologyEntity("news-article:005930:1", "기사", "news-article", {
                    "ontologyBox": "ABox", "symbol": "005930",
                }),
                OntologyEntity("market-proxy-instrument:QQQ", "QQQ", "market-proxy-instrument", {
                    "ontologyBox": "ABox", "symbol": "QQQ",
                }),
                OntologyEntity("portfolio:main", "포트폴리오", "portfolio", {
                    "ontologyBox": "ABox",
                }),
            ],
            relations=[
                OntologyRelation("stock:005930", "price-metric:005930:currentPrice", "HAS_PRICE", properties={"ontologyBox": "ABox"}),
                OntologyRelation("stock:005930", "flow-metric:005930:volume", "HAS_TRADE_FLOW", properties={"ontologyBox": "ABox"}),
                OntologyRelation("stock:005930", "temporal-window:005930:5d", "HAS_TEMPORAL_WINDOW", properties={"ontologyBox": "ABox"}),
                OntologyRelation("stock:005930", "news-article:005930:1", "HAS_EXTERNAL_SIGNAL", properties={"ontologyBox": "ABox"}),
                OntologyRelation("portfolio:main", "market-proxy-instrument:QQQ", "OBSERVES_MARKET_PROXY", properties={"ontologyBox": "ABox", "symbol": "QQQ"}),
            ],
        )

    def test_scopes_separate_symbol_fact_families_and_keep_macro_proxies_global(self):
        graph = self.scope_graph()
        first = apply_scoped_abox_identity(graph)
        entity_scopes = {item.entity_id: item.properties["aboxScopeId"] for item in graph.entities}
        relation_scopes = {item.relation_type: item.properties["aboxScopeId"] for item in graph.relations}

        self.assertEqual("symbol:005930:state", entity_scopes["stock:005930"])
        self.assertEqual("symbol:005930:market", entity_scopes["price-metric:005930:currentPrice"])
        self.assertEqual("symbol:005930:flow", entity_scopes["flow-metric:005930:volume"])
        self.assertEqual("symbol:005930:temporal", entity_scopes["temporal-window:005930:5d"])
        self.assertEqual("symbol:005930:evidence", entity_scopes["news-article:005930:1"])
        self.assertEqual("macro:market", entity_scopes["market-proxy-instrument:QQQ"])
        self.assertEqual("link:main", relation_scopes["OBSERVES_MARKET_PROXY"])
        self.assertEqual("symbol:005930:link", relation_scopes["HAS_TRADE_FLOW"])

        first_generations = dict(first["scopeGenerationIds"])
        flow = next(item for item in graph.entities if item.entity_id == "flow-metric:005930:volume")
        flow.properties["volume"] = 125
        second = apply_scoped_abox_identity(graph)
        second_generations = dict(second["scopeGenerationIds"])

        self.assertNotEqual(first_generations["symbol:005930:flow"], second_generations["symbol:005930:flow"])
        self.assertEqual(first_generations["symbol:005930:market"], second_generations["symbol:005930:market"])
        self.assertEqual(first_generations["symbol:005930:state"], second_generations["symbol:005930:state"])
        self.assertEqual(first_generations["macro:market"], second_generations["macro:market"])

    def test_change_impact_limits_symbol_flow_but_expands_macro_change(self):
        before = [
            {"scopeId": "symbol:005930:state", "generationId": "state-a"},
            {"scopeId": "symbol:005930:flow", "generationId": "flow-a"},
            {"scopeId": "symbol:000660:state", "generationId": "state-b"},
            {"scopeId": "macro:rates", "generationId": "rates-a"},
        ]
        after = [
            {"scopeId": "symbol:005930:state", "generationId": "state-a"},
            {"scopeId": "symbol:005930:flow", "generationId": "flow-b"},
            {"scopeId": "symbol:000660:state", "generationId": "state-b"},
            {"scopeId": "macro:rates", "generationId": "rates-a"},
        ]
        rules = [
            {
                "ruleId": "graph.test.flow.v1",
                "conditions": [{
                    "conditionId": "flow",
                    "kind": "relation",
                    "relationType": "HAS_TRADE_FLOW",
                    "targetKind": "flow-metric",
                }],
            },
            {
                "ruleId": "graph.test.market.v1",
                "conditions": [{
                    "conditionId": "price",
                    "kind": "relation",
                    "relationType": "HAS_PRICE",
                    "targetKind": "price-metric",
                }],
            },
            {
                "ruleId": "graph.test.macro-rate.v1",
                "conditions": [{
                    "conditionId": "rate",
                    "kind": "relation",
                    "relationType": "HAS_INTEREST_RATE",
                    "targetKind": "interest-rate",
                }],
            },
        ]
        flow_plan = build_inference_impact_plan(before, after, ["005930", "000660"], rules=rules)

        self.assertFalse(flow_plan["globalImpact"])
        self.assertEqual(["005930"], flow_plan["inferenceTargetSymbols"])
        self.assertEqual(["graph.test.flow.v1"], flow_plan["candidateRuleIds"])
        self.assertTrue(flow_plan["nativeRuleSelectionEligible"])
        self.assertFalse(flow_plan["nativeRuleSelectionApplied"])
        self.assertEqual("dependency-selected-native-evaluation", flow_plan["ruleExecutionScope"])

        after[-1]["generationId"] = "rates-b"
        macro_plan = build_inference_impact_plan(before, after, ["005930", "000660"], rules=rules)
        self.assertTrue(macro_plan["globalImpact"])
        self.assertEqual(["000660", "005930"], macro_plan["inferenceTargetSymbols"])
        self.assertIn("macro-rates", macro_plan["changedScopeFamilies"])

        bounded_macro_plan = build_inference_impact_plan(
            before,
            after,
            ["005930", "000660"],
            explicit_target_symbols=["005930"],
            rules=rules,
        )
        self.assertTrue(bounded_macro_plan["globalImpact"])
        self.assertTrue(bounded_macro_plan["boundedGlobalContext"])
        self.assertTrue(bounded_macro_plan["nativeRuleSelectionEligible"])
        self.assertEqual(["005930"], bounded_macro_plan["inferenceTargetSymbols"])
        self.assertEqual(
            "target-scoped-global-context-native-evaluation",
            bounded_macro_plan["ruleExecutionScope"],
        )

    def test_semantic_scope_delta_routes_stock_anchor_price_change_to_market_rules_only(self):
        before = [{
            "scopeId": "symbol:005930:state",
            "generationId": "state-a",
            "impactScopeFamilies": ["state"],
            "semanticFingerprints": {
                "state": "identity-stable",
                "market": "price-a",
                "position": "position-stable",
            },
        }]
        after = [{
            **before[0],
            "generationId": "state-b",
            "semanticFingerprints": {
                "state": "identity-stable",
                "market": "price-b",
                "position": "position-stable",
            },
        }]
        rules = [
            {
                "ruleId": "graph.test.market.v1",
                "conditions": [{
                    "conditionId": "price",
                    "kind": "relation",
                    "relationType": "HAS_PRICE",
                    "targetKind": "price-metric",
                }],
            },
            {
                "ruleId": "graph.test.flow.v1",
                "conditions": [{
                    "conditionId": "flow",
                    "kind": "relation",
                    "relationType": "HAS_TRADE_FLOW",
                    "targetKind": "flow-metric",
                }],
            },
        ]

        delta = scope_delta(before, after)
        plan = build_inference_impact_plan(before, after, ["005930"], rules=rules)

        self.assertEqual(["symbol:005930:state"], delta["changedScopeIds"])
        self.assertEqual({"symbol:005930:state": ["market"]}, delta["semanticChangedFamiliesByScope"])
        self.assertEqual(["market"], delta["changedScopeFamilies"])
        self.assertEqual(["graph.test.market.v1"], plan["candidateRuleIds"])
        self.assertEqual(["graph.test.flow.v1"], plan["deferredRuleIds"])

    def test_semantic_scope_delta_marks_storage_rebinding_without_a_factual_change(self):
        before = [{
            "scopeId": "symbol:005930:link",
            "generationId": "link-a",
            "impactScopeFamilies": ["link", "market"],
            "semanticFingerprints": {"market": "same-fact"},
        }]
        after = [{
            **before[0],
            "generationId": "link-b",
        }]

        delta = scope_delta(before, after)

        self.assertEqual([], delta["changedScopeIds"])
        self.assertEqual(["symbol:005930:link"], delta["generationChangedScopeIds"])
        self.assertEqual(["symbol:005930:link"], delta["reboundScopeIds"])
        self.assertEqual([], delta["changedScopeFamilies"])

    def test_change_impact_uses_semantic_family_from_a_relation_only_link_scope(self):
        before = [
            {
                "scopeId": "symbol:005930:market",
                "generationId": "market-a",
                "impactScopeFamilies": ["market"],
            },
            {
                "scopeId": "symbol:005930:link",
                "generationId": "link-a",
                "impactScopeFamilies": ["link", "flow"],
            },
        ]
        after = [
            {
                "scopeId": "symbol:005930:market",
                "generationId": "market-a",
                "impactScopeFamilies": ["market"],
            },
            {
                "scopeId": "symbol:005930:link",
                "generationId": "link-b",
                "impactScopeFamilies": ["link", "flow"],
            },
        ]
        rules = [
            {
                "ruleId": "graph.test.flow.v1",
                "conditions": [{
                    "conditionId": "flow",
                    "kind": "relation",
                    "relationType": "HAS_TRADE_FLOW",
                    "targetKind": "flow-metric",
                }],
            },
            {
                "ruleId": "graph.test.market.v1",
                "conditions": [{
                    "conditionId": "price",
                    "kind": "relation",
                    "relationType": "HAS_PRICE",
                    "targetKind": "price-metric",
                }],
            },
        ]

        plan = build_inference_impact_plan(before, after, ["005930"], rules=rules)

        self.assertEqual(["005930"], plan["inferenceTargetSymbols"])
        self.assertIn("flow", plan["changedScopeFamilies"])
        self.assertEqual(["graph.test.flow.v1"], plan["candidateRuleIds"])

    def test_unknown_condition_is_conservative_and_dependency_is_rulebox_graph_data(self):
        profile = rule_condition_dependency_profile({
            "conditionId": "opaque",
            "kind": "relation",
            "relationType": "UNREGISTERED_RELATION",
        })
        self.assertTrue(profile["conservative"])
        self.assertIn("unknown", profile["scopeFamilies"])

        graph = rulebox_graph_from_rules(default_graph_inference_rules(), include_tbox=False)
        dependencies = [item for item in graph.entities if item.kind == "rule-dependency"]
        self.assertTrue(dependencies)
        self.assertTrue(all(item.properties.get("tboxClass") == "RuleDependency" for item in dependencies))
        self.assertTrue(any(item.relation_type == "HAS_RULE_DEPENDENCY" for item in graph.relations))
        self.assertIsNotNone(tbox_class_def("RuleDependency"))
        self.assertIsNotNone(tbox_relation_def("HAS_RULE_DEPENDENCY"))

    def test_dependency_profiles_keep_typed_static_conditions_out_of_state(self):
        rules = {
            rule.rule_id: rule
            for rule in default_graph_inference_rules()
        }

        winner = rule_dependency_profile(rules["graph.winner_momentum.add_buy_review.v1"])
        liquidity = rule_dependency_profile(rules["graph.liquidity.execution_guard.v1"])
        valuation = rule_dependency_profile(rules["graph.valuation.negative_margin.risk.v1"])

        self.assertNotIn("state", winner["scopeFamilies"])
        self.assertIn("profile", winner["scopeFamilies"])
        self.assertNotIn("state", liquidity["scopeFamilies"])
        self.assertIn("flow", liquidity["scopeFamilies"])
        self.assertEqual(["valuation"], valuation["scopeFamilies"])

    def test_market_only_change_selects_a_strict_catalog_subset(self):
        before = [{
            "scopeId": "symbol:005930:state",
            "generationId": "state-a",
            "impactScopeFamilies": ["state"],
            "semanticFingerprints": {
                "state": "same",
                "market": "price-before",
                "position": "same",
                "profile": "same",
            },
        }]
        after = [{
            **before[0],
            "generationId": "state-b",
            "semanticFingerprints": {
                "state": "same",
                "market": "price-after",
                "position": "same",
                "profile": "same",
            },
        }]
        catalog = default_graph_inference_rules()

        plan = build_inference_impact_plan(before, after, ["005930"], rules=catalog)

        self.assertEqual(["market"], plan["changedScopeFamilies"])
        self.assertLess(plan["candidateRuleCount"], len(catalog))
        self.assertIn("graph.price.reclaim.thesis_support.v1", plan["candidateRuleIds"])
        self.assertNotIn("graph.liquidity.execution_guard.v1", plan["candidateRuleIds"])

    def test_unknown_abox_property_keeps_its_entity_fact_family(self):
        graph = self.scope_graph()
        flow = next(item for item in graph.entities if item.entity_id == "flow-metric:005930:volume")
        flow.properties["providerDisplayHint"] = "first"

        first = apply_scoped_abox_identity(graph)
        flow.properties["providerDisplayHint"] = "second"
        second = apply_scoped_abox_identity(graph)
        delta = scope_delta(first["scopePlan"], second["scopePlan"])

        self.assertEqual(["flow"], delta["changedScopeFamilies"])
        self.assertNotIn("state", delta["changedScopeFamilies"])

    def test_observation_clock_does_not_roll_a_scope_or_reopen_rules(self):
        graph = self.scope_graph()
        market = next(item for item in graph.entities if item.entity_id == "price-metric:005930:currentPrice")
        temporal = next(item for item in graph.entities if item.entity_id == "temporal-window:005930:5d")
        market.properties.update({
            "marketSessionLocalTime": "14:00:00",
            "freshnessAgeMinutes": 1,
            "freshnessStatus": "near-live",
        })
        temporal.properties.update({
            "elapsedHours": 1.25,
            "dataState": "sufficient",
        })

        first = apply_scoped_abox_identity(graph)
        market.properties.update({
            "marketSessionLocalTime": "14:05:00",
            "freshnessAgeMinutes": 6,
        })
        temporal.properties["elapsedHours"] = 1.5
        second = apply_scoped_abox_identity(graph)
        delta = scope_delta(first["scopePlan"], second["scopePlan"])

        self.assertEqual(
            first["scopeGenerationIds"]["symbol:005930:market"],
            second["scopeGenerationIds"]["symbol:005930:market"],
        )
        self.assertEqual(
            first["scopeGenerationIds"]["symbol:005930:temporal"],
            second["scopeGenerationIds"]["symbol:005930:temporal"],
        )
        self.assertEqual([], delta["changedScopeIds"])

    def test_sector_exposure_is_owned_by_the_portfolio_not_global_state(self):
        graph = PortfolioOntology(
            "main",
            entities=[OntologyEntity("sector:semiconductor", "반도체", "sector", {
                "ontologyBox": "ABox",
                "ratio": 35,
            })],
        )

        scoped = apply_scoped_abox_identity(graph)
        sector_scope = graph.entities[0].properties["aboxScopeId"]
        sector_plan = next(item for item in scoped["scopePlan"] if item["scopeId"] == sector_scope)

        self.assertEqual("portfolio:main", sector_scope)
        self.assertIn("exposure", sector_plan["semanticFingerprints"])
        self.assertNotIn("state", sector_plan["semanticFingerprints"])

    def test_dynamic_supporting_facts_stay_with_their_symbol_or_portfolio(self):
        graph = PortfolioOntology(
            "main",
            entities=[
                OntologyEntity("slippage-estimate:005930", "삼성전자 슬리피지", "slippage-estimate", {
                    "ontologyBox": "ABox",
                    "volumeRatio": 0.4,
                }),
                OntologyEntity("data-source:KIS:005930", "KIS", "data-source", {
                    "ontologyBox": "ABox",
                    "symbol": "005930",
                    "quoteStatus": "ok",
                }),
                OntologyEntity("market-exposure:main:US", "미국 시장 노출", "market-exposure", {
                    "ontologyBox": "ABox",
                    "invested": 100,
                }),
                OntologyEntity("risk:semiconductors-correlation", "반도체 상관 리스크", "risk", {
                    "ontologyBox": "ABox",
                    "sectorRatio": 42,
                }),
            ],
        )

        apply_scoped_abox_identity(graph)
        scopes = {item.entity_id: item.properties["aboxScopeId"] for item in graph.entities}

        self.assertEqual("symbol:005930:flow", scopes["slippage-estimate:005930"])
        self.assertEqual("symbol:005930:quality", scopes["data-source:KIS:005930"])
        self.assertEqual("portfolio:main", scopes["market-exposure:main:US"])
        self.assertEqual("portfolio:main", scopes["risk:semiconductors-correlation"])

    def test_affects_relation_uses_the_source_fact_family(self):
        self.assertEqual(
            "macro-fx",
            family_for_relation(
                "AFFECTS",
                source_family="macro-fx",
                target_family="state",
                source_kind="fx-rate",
                target_kind="stock",
            ),
        )
        self.assertEqual(
            "evidence",
            family_for_relation(
                "AFFECTS",
                source_family="evidence",
                target_family="state",
                source_kind="article-ai-analysis",
                target_kind="stock",
            ),
        )

    def test_impact_plan_is_preserved_with_the_inference_generation(self):
        source = PortfolioOntology(
            "main",
            entities=[OntologyEntity("trace:test", "추론 경로", "inference-trace", {
                "ontologyBox": "InferenceBox",
                "tboxClass": "InferenceTrace",
                "symbol": "005930",
            })],
        )
        impact_plan = build_inference_impact_plan(
            [{"scopeId": "symbol:005930:flow", "generationId": "flow-a"}],
            [{"scopeId": "symbol:005930:flow", "generationId": "flow-b"}],
            ["005930"],
            rules=default_graph_inference_rules(),
        )

        inference = typedb_inferencebox_graph(
            source,
            generation_id="inference:test",
            generation_at="2026-07-22T00:00:00Z",
            rulebox_metadata={
                "inferenceImpactPlan": impact_plan,
                "impactPlanVersion": impact_plan["version"],
                "ruleExecutionScope": impact_plan["ruleExecutionScope"],
                "nativeRuleSelectionApplied": impact_plan["nativeRuleSelectionApplied"],
            },
        )

        trace = inference.entities[0]
        self.assertEqual("inference:test", trace.properties["inferenceGenerationId"])
        self.assertEqual("abox-change-impact-v4", trace.properties["impactPlanVersion"])
        self.assertEqual(["005930"], trace.properties["inferenceImpactPlan"]["inferenceTargetSymbols"])
        self.assertEqual("dependency-selected-native-evaluation", trace.properties["ruleExecutionScope"])
        self.assertFalse(trace.properties["nativeRuleSelectionApplied"])

    def test_native_rule_selection_rechecks_prior_matches_and_falls_back_without_proof(self):
        rules = default_graph_inference_rules()[:3]
        rule_ids = [rule.rule_id for rule in rules]

        selected = typedb_native_rule_execution_selection(
            rules,
            candidate_rule_ids=[rule_ids[0]],
            prior_matched_rule_ids=[rule_ids[1]],
            eligible=True,
            prior_inference_reusable=True,
        )
        self.assertTrue(selected["selectionApplied"])
        self.assertEqual([rule_ids[0], rule_ids[1]], selected["selectedRuleIds"])
        self.assertEqual([rule_ids[2]], selected["deferredRuleIds"])

        fallback = typedb_native_rule_execution_selection(
            rules,
            candidate_rule_ids=[rule_ids[0]],
            eligible=True,
            prior_inference_reusable=False,
        )
        self.assertFalse(fallback["selectionApplied"])
        self.assertEqual(rule_ids, fallback["selectedRuleIds"])
        self.assertEqual("prior-aligned-inference-unavailable", fallback["fallbackReason"])

        bounded_global = typedb_native_rule_execution_selection(
            rules,
            candidate_rule_ids=[rule_ids[0]],
            prior_matched_rule_ids=[rule_ids[1]],
            eligible=True,
            prior_inference_reusable=True,
            global_impact=True,
            bounded_global_context=True,
        )
        self.assertTrue(bounded_global["selectionApplied"])
        self.assertEqual([rule_ids[0], rule_ids[1]], bounded_global["selectedRuleIds"])

        full_global = typedb_native_rule_execution_selection(
            rules,
            candidate_rule_ids=[rule_ids[0]],
            prior_matched_rule_ids=[rule_ids[1]],
            eligible=True,
            prior_inference_reusable=True,
            global_impact=True,
        )
        self.assertFalse(full_global["selectionApplied"])
        self.assertEqual("global-impact-requires-complete-evaluation", full_global["fallbackReason"])


if __name__ == "__main__":
    unittest.main()
