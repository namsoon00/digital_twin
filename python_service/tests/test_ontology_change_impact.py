import unittest

from digital_twin.domain.ontology_change_impact import (
    build_inference_impact_plan,
    compact_inference_impact_plan,
    family_for_relation,
    rule_condition_dependency_profile,
    rule_dependency_profile,
    scope_delta,
)
from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_scopes import (
    _scope_fragment_payload,
    _scope_fragment_payloads,
    _scope_semantic_fingerprints,
    _scope_semantic_fingerprints_by_scope,
    apply_scoped_abox_identity,
)
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

    def test_one_pass_scope_identity_helpers_match_per_scope_contract(self):
        graph = self.scope_graph()
        graph.evidence.append(OntologyEvidence(
            "evidence:005930:market-context",
            "stock:005930",
            "market-context",
            "test",
            "시장 배경 근거",
            {"ontologyBox": "ABox", "symbol": "005930"},
        ))
        apply_scoped_abox_identity(graph)
        scope_ids = sorted({
            item.properties.get("aboxScopeId")
            for item in graph.entities
            if item.properties.get("aboxScopeId")
        } | {
            item.properties.get("aboxScopeId")
            for item in graph.relations
            if item.properties.get("aboxScopeId")
        } | {
            item.value.get("aboxScopeId")
            for item in graph.evidence
            if item.value.get("aboxScopeId")
        })
        support_relations = [{
            "scopeId": "symbol:005930:evidence",
            "source": "stock:005930",
            "target": "evidence:005930:market-context",
            "type": "HAS_EVIDENCE",
            "impactFamilies": ["evidence"],
            "properties": {"source": "test"},
        }]

        legacy_payloads = {
            scope_id: _scope_fragment_payload(graph, scope_id, support_relations)
            for scope_id in scope_ids
        }
        one_pass_payloads = _scope_fragment_payloads(graph, scope_ids, support_relations)
        legacy_semantics = {
            scope_id: _scope_semantic_fingerprints(graph, scope_id, support_relations)
            for scope_id in scope_ids
        }
        one_pass_semantics = _scope_semantic_fingerprints_by_scope(graph, scope_ids, support_relations)

        self.assertEqual(legacy_payloads, one_pass_payloads)
        self.assertEqual(legacy_semantics, one_pass_semantics)

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

    def test_changed_relation_scope_uses_its_symbol_context_without_global_impact(self):
        before = [
            {
                "scopeId": "symbol:005930:market",
                "generationId": "market-a",
                "fingerprint": "market-a",
                "semanticFingerprints": {"market": "price-a"},
            },
            {
                "scopeId": "link:apple-news",
                "generationId": "link-a",
                "fingerprint": "link-a",
                "semanticFingerprints": {"evidence": "article-a"},
                "dependencyScopeIds": ["symbol:005930:market"],
            },
        ]
        after = [
            before[0],
            {
                **before[1],
                "generationId": "link-b",
                "fingerprint": "link-b",
                "semanticFingerprints": {"evidence": "article-b"},
            },
        ]

        plan = build_inference_impact_plan(before, after, ["005930", "000660"], rules=[])

        self.assertFalse(plan["globalImpact"])
        self.assertEqual(["005930"], plan["inferenceTargetSymbols"])
        self.assertEqual(["005930"], plan["relationContextSymbols"])

    def test_explicit_target_does_not_expand_to_other_symbol_storage_dependencies(self):
        before = [
            {
                "scopeId": "symbol:PLTR:link",
                "generationId": "link-a",
                "semanticFingerprints": {"exposure": "unchanged"},
                "dependencyScopeIds": ["symbol:NVDA:market"],
            },
            {
                "scopeId": "symbol:NVDA:market",
                "generationId": "market-a",
                "semanticFingerprints": {"market": "before"},
            },
        ]
        after = [
            before[0],
            {
                **before[1],
                "generationId": "market-b",
                "semanticFingerprints": {"market": "after"},
            },
        ]

        plan = build_inference_impact_plan(
            before,
            after,
            ["PLTR", "NVDA"],
            explicit_target_symbols=["PLTR"],
            rules=[],
        )

        self.assertEqual(["PLTR"], plan["inferenceTargetSymbols"])
        self.assertEqual(["NVDA"], plan["scopeDelta"]["directChangedSymbols"])
        self.assertIn("PLTR", plan["scopeDelta"]["dependencyAffectedSymbols"])

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

    def test_quality_only_macro_scope_change_uses_the_quality_rule_subset(self):
        before = [{
            "scopeId": "macro:market",
            "generationId": "market-a",
            "semanticFingerprints": {
                "macro-market": "market-value-stable",
                "quality": "freshness-before",
            },
        }]
        after = [{
            **before[0],
            "generationId": "market-b",
            "semanticFingerprints": {
                "macro-market": "market-value-stable",
                "quality": "freshness-after",
            },
        }]
        rules = [
            {
                "ruleId": "graph.test.quality.v1",
                "conditions": [{
                    "conditionId": "quality",
                    "kind": "relation",
                    "relationType": "HAS_DATA_QUALITY",
                    "targetKind": "data-quality",
                }],
            },
            {
                "ruleId": "graph.test.macro-value.v1",
                "conditions": [{
                    "conditionId": "market",
                    "kind": "relation",
                    "relationType": "HAS_PRICE",
                    "targetKind": "market-proxy-observation",
                }],
            },
        ]

        plan = build_inference_impact_plan(before, after, ["005930", "000660"], rules=rules)

        self.assertFalse(plan["globalImpact"])
        self.assertTrue(plan["qualityScopedGlobalContext"])
        self.assertEqual(["macro:market"], plan["qualityOnlyGlobalScopeIds"])
        self.assertEqual([], plan["globalValueScopeIds"])
        self.assertEqual(["graph.test.quality.v1"], plan["candidateRuleIds"])
        self.assertTrue(plan["nativeRuleSelectionEligible"])
        self.assertEqual(
            "quality-scoped-global-context-native-evaluation",
            plan["ruleExecutionScope"],
        )

    def test_complete_candidate_catalog_skips_unnecessary_reuse_read_and_explains_global_context(self):
        before = [
            {"scopeId": "macro:rates", "generationId": "rates-a"},
            {"scopeId": "portfolio:main", "generationId": "portfolio-a"},
        ]
        after = [
            {"scopeId": "macro:rates", "generationId": "rates-b"},
            {"scopeId": "portfolio:main", "generationId": "portfolio-b"},
        ]
        rules = [{
            "ruleId": "graph.test.broad.v1",
            "conditions": [{
                "conditionId": "macro-rate",
                "kind": "relation",
                "relationType": "HAS_INTEREST_RATE",
                "targetKind": "interest-rate",
            }],
        }]

        plan = build_inference_impact_plan(
            before,
            after,
            ["005930"],
            explicit_target_symbols=["005930"],
            rules=rules,
            requested_fact_families=["macro-rates", "portfolio"],
        )

        self.assertTrue(plan["globalImpact"])
        self.assertTrue(plan["boundedGlobalContext"])
        self.assertEqual(1, plan["candidateRuleCount"])
        self.assertEqual(1, plan["enabledRuleCount"])
        self.assertFalse(plan["nativeRuleSelectionEligible"])
        self.assertEqual(
            "candidate-rules-cover-complete-catalog",
            plan["nativeRuleSelectionEligibilityReason"],
        )
        diagnostics = plan["diagnostics"]
        self.assertEqual("target-scoped-global-context", diagnostics["classification"])
        self.assertEqual(100.0, diagnostics["candidateRuleRatioPct"])
        self.assertIn("candidate-catalog-is-complete", diagnostics["reasonCodes"])
        self.assertEqual("aligned", diagnostics["eventScopeAgreement"])
        self.assertEqual(
            ["macro", "portfolio"],
            [item["type"] for item in diagnostics["globalScopeTypes"]],
        )

        compact = compact_inference_impact_plan(plan)
        self.assertEqual("target-scoped-global-context", compact["diagnostics"]["classification"])
        self.assertEqual(1, compact["diagnostics"]["enabledRuleCount"])
        self.assertEqual("aligned", compact["diagnostics"]["eventScopeAgreement"])

    def test_impact_diagnostics_marks_snapshot_families_broader_than_event(self):
        before = [{"scopeId": "symbol:005930:market", "generationId": "market-a"}]
        after = [{"scopeId": "symbol:005930:market", "generationId": "market-b"}]
        plan = build_inference_impact_plan(
            before,
            after,
            ["005930"],
            rules=[],
            requested_fact_families=["flow"],
        )

        self.assertEqual("snapshot-broader-than-event", plan["diagnostics"]["eventScopeAgreement"])
        self.assertEqual(["market"], plan["diagnostics"]["unexpectedChangedFamilies"])

    def test_compact_impact_plan_ignores_malformed_diagnostics(self):
        compact = compact_inference_impact_plan({
            "version": "test",
            "diagnostics": "not-a-mapping",
        })

        self.assertEqual("", compact["diagnostics"]["classification"])
        self.assertEqual([], compact["diagnostics"]["reasonCodes"])
        self.assertEqual(0, compact["diagnostics"]["globalScopeCount"])

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
            "sourceAsOf": "2026-07-25T05:00:00Z",
            "sourceFetchedAt": "2026-07-25T05:00:10Z",
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

    def test_macro_quote_change_stays_in_its_macro_family(self):
        graph = PortfolioOntology(
            "main",
            entities=[
                OntologyEntity("market-proxy-instrument:QQQ", "나스닥 시장 센서", "market-proxy-instrument", {
                    "ontologyBox": "ABox",
                    "symbol": "QQQ",
                }),
                OntologyEntity("market-proxy-observation:QQQ", "나스닥 시장 관측", "market-proxy-observation", {
                    "ontologyBox": "ABox",
                    "symbol": "QQQ",
                    "currentPrice": 500,
                    "volume": 1000,
                    "sourceAsOf": "2026-07-25T04:00:00Z",
                    "sourceFetchedAt": "2026-07-25T04:00:05Z",
                    "freshnessStatus": "near-live",
                }),
            ],
            relations=[OntologyRelation(
                "market-proxy-instrument:QQQ",
                "market-proxy-observation:QQQ",
                "HAS_PRICE",
                properties={"ontologyBox": "ABox"},
            )],
        )

        first = apply_scoped_abox_identity(graph)
        observation = next(item for item in graph.entities if item.entity_id == "market-proxy-observation:QQQ")
        observation.properties.update({
            "currentPrice": 501,
            "volume": 1100,
            "sourceAsOf": "2026-07-25T04:05:00Z",
            "sourceFetchedAt": "2026-07-25T04:05:05Z",
        })
        second = apply_scoped_abox_identity(graph)
        delta = scope_delta(first["scopePlan"], second["scopePlan"])

        self.assertEqual(["macro-market"], delta["changedScopeFamilies"])
        self.assertNotIn("market", delta["changedScopeFamilies"])
        self.assertNotIn("flow", delta["changedScopeFamilies"])
        self.assertNotIn("profile", delta["changedScopeFamilies"])
        self.assertNotIn("quality", delta["changedScopeFamilies"])

        plan = build_inference_impact_plan(
            first["scopePlan"],
            second["scopePlan"],
            ["QQQ"],
            rules=[
                {
                    "ruleId": "graph.test.macro-price.v1",
                    "conditions": [{
                        "conditionId": "macro-price",
                        "kind": "relation",
                        "relationType": "HAS_PRICE",
                        "targetKind": "market-proxy-observation",
                    }],
                },
                {
                    "ruleId": "graph.test.stock-price.v1",
                    "conditions": [{
                        "conditionId": "stock-price",
                        "kind": "relation",
                        "relationType": "HAS_PRICE",
                        "targetKind": "price-metric",
                    }],
                },
            ],
        )
        self.assertEqual(["graph.test.macro-price.v1"], plan["candidateRuleIds"])
        self.assertEqual(["graph.test.stock-price.v1"], plan["deferredRuleIds"])

        observation.properties["freshnessStatus"] = "stale"
        third = apply_scoped_abox_identity(graph)
        freshness_delta = scope_delta(second["scopePlan"], third["scopePlan"])

        self.assertEqual(["quality"], freshness_delta["changedScopeFamilies"])

    def test_evidence_quote_metadata_does_not_become_stock_market_change(self):
        graph = PortfolioOntology(
            "main",
            entities=[OntologyEntity("news-article:005930:1", "삼성전자 기사", "news-article", {
                "ontologyBox": "ABox",
                "symbol": "005930",
                "summary": "초기 기사",
                "currentPrice": 70000,
                "sourceAsOf": "2026-07-25T04:00:00Z",
                "freshnessStatus": "near-live",
            })],
        )

        first = apply_scoped_abox_identity(graph)
        article = graph.entities[0]
        article.properties.update({
            "summary": "갱신 기사",
            "currentPrice": 71000,
            "sourceAsOf": "2026-07-25T04:05:00Z",
        })
        second = apply_scoped_abox_identity(graph)
        delta = scope_delta(first["scopePlan"], second["scopePlan"])

        self.assertEqual(["evidence"], delta["changedScopeFamilies"])
        self.assertNotIn("market", delta["changedScopeFamilies"])
        self.assertNotIn("profile", delta["changedScopeFamilies"])

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
        self.assertEqual("abox-change-impact-v6", trace.properties["impactPlanVersion"])
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
