import unittest

from digital_twin.domain.ontology_change_impact import (
    CHANGE_IMPACT_VERSION,
    DEPENDENCY_FINGERPRINT_VERSION,
    build_dynamic_inference_preflight,
    build_inference_impact_plan,
    compact_inference_impact_plan,
    family_for_entity,
    family_for_relation,
    rule_condition_dependency_profile,
    rule_dependency_profile,
    requested_scope_families_for_event_fact_types,
    scope_family,
    scope_delta,
    scope_symbol,
    unpack_semantic_dependency_fingerprints,
)
from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_scopes import (
    _scope_fragment_payload,
    _scope_fragment_payloads,
    _scope_fragment_payloads_with_semantic_fingerprints,
    _scope_semantic_fingerprints,
    _scope_semantic_fingerprints_by_scope,
    apply_scoped_abox_identity,
    relation_link_scope_id,
)
from digital_twin.domain.ontology_tbox import tbox_class_def, tbox_relation_def
from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.world_partitioned_reasoning import (
    compile_world_partitioned_rules,
)
from digital_twin.infrastructure.typedb_ontology import (
    typedb_inferencebox_graph,
    typedb_native_rule_execution_selection,
)


class OntologyChangeImpactTests(unittest.TestCase):
    def test_model_hypothesis_assessment_family_does_not_follow_hypothesis_name(self):
        self.assertEqual(
            "model-signal",
            family_for_entity(
                "model-hypothesis-assessment",
                {"symbol": "028260", "hypothesisFamilyId": "fundamental-deterioration"},
                "model-hypothesis-assessment:028260:fundamental-deterioration",
            ),
        )
        self.assertEqual(
            "model-signal",
            family_for_relation(
                "HAS_HYPOTHESIS_ASSESSMENT",
                source_family="state",
                target_family="model-signal",
                source_kind="stock",
                target_kind="model-hypothesis-assessment",
            ),
        )

    def test_dynamic_preflight_reuses_shared_generation_for_account_only_change(self):
        rules = [{
            "ruleId": "shared.market.price.v1",
            "enabled": True,
            "conditions": [{
                "conditionId": "price",
                "kind": "subject_property",
                "field": "currentPrice",
            }],
        }]

        plan = build_dynamic_inference_preflight(
            rules=rules,
            target_symbols=["005930"],
            requested_fact_families=["position", "portfolio"],
            requested_fact_families_by_symbol={
                "005930": ["position", "portfolio"],
            },
            event_fact_boundary_authoritative=True,
            event_dependency_boundary_authoritative=True,
            prior_result_slots_reusable=True,
        )

        self.assertEqual("REUSE_SHARED", plan["route"])
        self.assertTrue(plan["sharedReuseEligible"])
        self.assertEqual([], plan["candidateRuleIds"])

    def test_dynamic_preflight_fails_closed_without_authoritative_provenance(self):
        plan = build_dynamic_inference_preflight(
            rules=[{
                "ruleId": "shared.market.price.v1",
                "enabled": True,
                "conditions": [{
                    "conditionId": "price",
                    "kind": "subject_property",
                    "field": "currentPrice",
                }],
            }],
            target_symbols=["005930"],
            requested_fact_families=["position"],
            event_fact_boundary_authoritative=False,
            prior_result_slots_reusable=True,
        )

        self.assertEqual("FULL_SAFE", plan["route"])
        self.assertFalse(plan["sharedReuseEligible"])

    def test_dynamic_preflight_does_not_trust_revision_without_fact_boundary(self):
        revision = {"fact": "quote:7"}

        plan = build_dynamic_inference_preflight(
            rules=[{
                "ruleId": "shared.market.price.v1",
                "enabled": True,
                "conditions": [{
                    "conditionId": "price",
                    "kind": "subject_property",
                    "field": "currentPrice",
                }],
            }],
            target_symbols=["005930"],
            requested_fact_families=["market"],
            event_fact_boundary_authoritative=False,
            revision_vectors_by_symbol={"005930": revision},
            prior_revision_vectors_by_symbol={"005930": revision},
            prior_result_slots_reusable=True,
        )

        self.assertTrue(plan["exactRevisionMatch"])
        self.assertEqual("FULL_SAFE", plan["route"])
        self.assertFalse(plan["sharedReuseEligible"])

    def test_dynamic_preflight_uses_exact_dependency_alias_when_catalog_knows_it(self):
        partition = compile_world_partitioned_rules(
            default_graph_inference_rules()
        )

        plan = build_dynamic_inference_preflight(
            rules=partition["sharedRules"],
            target_symbols=["005930"],
            requested_fact_families=["market"],
            requested_dependency_keys=["kind:stock:field:ma20distance"],
            event_fact_boundary_authoritative=True,
            event_dependency_boundary_authoritative=True,
            prior_result_slots_reusable=True,
        )

        self.assertTrue(plan["exactDependencyRoutingUsed"])
        self.assertGreater(plan["candidateRuleCount"], 0)
        self.assertLess(
            plan["candidateRuleCount"], plan["familyCandidateRuleCount"],
        )

        # A market-data snapshot also carries account-owned P/L fields. The
        # exact dependency key must route the portfolio rule even when the
        # event's coarse family remains ``market``.
        cross_family_plan = build_dynamic_inference_preflight(
            rules=[{
                "ruleId": "graph.test.profit-policy-threshold.v1",
                "enabled": True,
                "conditions": [{
                    "conditionId": "profit-threshold",
                    "kind": "subject_property",
                    "field": "profitLossRate",
                }],
            }],
            target_symbols=["MSTR"],
            requested_fact_families=["market"],
            requested_dependency_keys=["kind:stock:field:profitlossrate"],
            event_fact_boundary_authoritative=True,
            event_dependency_boundary_authoritative=True,
            prior_result_slots_reusable=True,
        )

        self.assertTrue(cross_family_plan["exactDependencyRoutingUsed"])
        self.assertEqual(
            ["graph.test.profit-policy-threshold.v1"],
            cross_family_plan["candidateRuleIds"],
        )

        production_cross_family_plan = build_dynamic_inference_preflight(
            rules=default_graph_inference_rules(),
            target_symbols=["MSTR"],
            requested_fact_families=["market"],
            requested_dependency_keys=["kind:stock:field:profitlossrate"],
            event_fact_boundary_authoritative=True,
            event_dependency_boundary_authoritative=True,
            prior_result_slots_reusable=True,
        )
        self.assertIn(
            "graph.notification.loss_policy_threshold.v1",
            production_cross_family_plan["candidateRuleIds"],
        )
        self.assertIn(
            "graph.notification.profit_policy_threshold.v1",
            production_cross_family_plan["candidateRuleIds"],
        )

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

    def test_market_change_selects_company_market_rule_without_rewriting_company_scope(self):
        graph = PortfolioOntology(
            "main",
            entities=[
                OntologyEntity("stock:005930", "삼성전자", "stock", {
                    "ontologyBox": "ABox", "symbol": "005930", "ma20Distance": -1.0,
                }),
                OntologyEntity("company-financial-state:005930:2025", "삼성전자 재무", "company-financial-state", {
                    "ontologyBox": "ABox", "symbol": "005930", "tboxClass": "FinancialState",
                    "revenueGrowthPct": 8.0, "freeCashFlowMarginPct": 7.0,
                }),
            ],
            relations=[
                OntologyRelation(
                    "stock:005930",
                    "company-financial-state:005930:2025",
                    "HAS_FINANCIAL_STATE",
                    properties={"ontologyBox": "ABox"},
                ),
            ],
        )
        first = apply_scoped_abox_identity(graph)
        stock = next(item for item in graph.entities if item.kind == "stock")
        stock.properties["ma20Distance"] = 1.0
        second = apply_scoped_abox_identity(graph)
        company_rule = next(
            rule.to_dict()
            for rule in default_graph_inference_rules()
            if rule.rule_id == "graph.company.market.fundamental_confirmation.support.v1"
        )

        plan = build_inference_impact_plan(
            first["scopePlan"],
            second["scopePlan"],
            ["005930"],
            explicit_target_symbols=["005930"],
            rules=[company_rule],
            requested_fact_families=["market"],
            requested_fact_families_by_symbol={"005930": ["market"]},
        )

        self.assertEqual(["market"], plan["changedScopeFamilies"])
        self.assertEqual(
            ["graph.company.market.fundamental_confirmation.support.v1"],
            plan["candidateRuleIds"],
        )
        self.assertEqual(
            first["scopeGenerationIds"]["symbol:005930:fundamental"],
            second["scopeGenerationIds"]["symbol:005930:fundamental"],
        )

    def test_scope_identity_ignores_display_label_only_changes(self):
        graph = self.scope_graph()
        first = apply_scoped_abox_identity(graph)
        first_generations = dict(first["scopeGenerationIds"])

        stock = next(item for item in graph.entities if item.entity_id == "stock:005930")
        stock.label = "삼성전자 현재가 70,000원"

        second = apply_scoped_abox_identity(graph)

        self.assertEqual(first_generations, second["scopeGenerationIds"])

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

    def test_dependency_routing_distinguishes_trigger_invalidation_and_context_only(self):
        before = [{
            "scopeId": "symbol:005930:market",
            "generationId": "market-a",
            "semanticFingerprints": {"market": "market-a"},
            "semanticDependencyFingerprintVersion": DEPENDENCY_FINGERPRINT_VERSION,
            "semanticDependencyFingerprints": {"field:currentprice": "price-a"},
        }]
        after = [{
            **before[0],
            "generationId": "market-b",
            "semanticFingerprints": {"market": "market-b"},
            "semanticDependencyFingerprints": {"field:currentprice": "price-b"},
        }]
        rules = [
            {
                "ruleId": "graph.test.trigger.v1",
                "conditions": [{
                    "field": "currentPrice",
                    "changeTrigger": True,
                    "invalidationTrigger": False,
                }],
            },
            {
                "ruleId": "graph.test.invalidation.v1",
                "conditions": [{
                    "field": "currentPrice",
                    "changeTrigger": False,
                    "invalidationTrigger": True,
                }],
            },
            {
                "ruleId": "graph.test.context.v1",
                "conditions": [{
                    "field": "currentPrice",
                    "changeTrigger": False,
                    "invalidationTrigger": False,
                }],
            },
        ]

        plan = build_inference_impact_plan(before, after, ["005930"], rules=rules)

        self.assertEqual(["graph.test.trigger.v1"], plan["triggerRuleIds"])
        self.assertEqual(["graph.test.invalidation.v1"], plan["invalidationRuleIds"])
        self.assertEqual(
            ["graph.test.trigger.v1", "graph.test.invalidation.v1"],
            plan["candidateRuleIds"],
        )
        self.assertEqual(["graph.test.context.v1"], plan["deferredRuleIds"])

    def test_scoped_dependency_kind_hash_ignores_value_only_change(self):
        graph = self.scope_graph()
        first = apply_scoped_abox_identity(graph)
        first_market = next(
            item for item in first["scopePlan"]
            if item["scopeId"] == "symbol:005930:market"
        )
        metric = next(
            item for item in graph.entities
            if item.entity_id == "price-metric:005930:currentPrice"
        )
        metric.properties["currentPrice"] = 70100
        second = apply_scoped_abox_identity(graph)
        second_market = next(
            item for item in second["scopePlan"]
            if item["scopeId"] == "symbol:005930:market"
        )

        self.assertEqual(
            DEPENDENCY_FINGERPRINT_VERSION,
            second_market["semanticDependencyFingerprintVersion"],
        )
        first_dependencies = unpack_semantic_dependency_fingerprints(first_market)
        second_dependencies = unpack_semantic_dependency_fingerprints(second_market)
        self.assertEqual(
            first_dependencies["kind:price-metric"],
            second_dependencies["kind:price-metric"],
        )
        self.assertNotEqual(
            first_dependencies["kind:price-metric:field:currentprice"],
            second_dependencies["kind:price-metric:field:currentprice"],
        )

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
        self.assertTrue(all(
            next(rule for rule in catalog if rule.rule_id == rule_id).enabled
            for rule_id in plan["candidateRuleIds"]
        ))
        self.assertNotIn("graph.liquidity.execution_guard.v1", plan["candidateRuleIds"])

    def test_target_event_routes_its_shared_fact_family_without_reopening_other_global_values(self):
        before = [
            {
                "scopeId": "symbol:005930:market",
                "generationId": "market-a",
                "semanticFingerprints": {"market": "price-a"},
                "semanticDependencyFingerprintVersion": DEPENDENCY_FINGERPRINT_VERSION,
                "semanticDependencyFingerprints": {"field:currentprice": "price-a"},
            },
            {
                "scopeId": "macro:fx",
                "generationId": "fx-a",
                "semanticFingerprints": {"macro-fx": "fx-a"},
                "semanticDependencyFingerprintVersion": DEPENDENCY_FINGERPRINT_VERSION,
                "semanticDependencyFingerprints": {"kind:fx-rate": "fx-a"},
            },
            {
                "scopeId": "portfolio:main",
                "generationId": "portfolio-a",
                "semanticFingerprints": {"position": "portfolio-a"},
                "semanticDependencyFingerprintVersion": DEPENDENCY_FINGERPRINT_VERSION,
                "semanticDependencyFingerprints": {"field:positionweight": "portfolio-a"},
            },
        ]
        after = [
            {
                **before[0],
                "generationId": "market-b",
                "semanticFingerprints": {"market": "price-b"},
                "semanticDependencyFingerprints": {"field:currentprice": "price-b"},
            },
            {
                **before[1],
                "generationId": "fx-b",
                "semanticFingerprints": {"macro-fx": "fx-b"},
                "semanticDependencyFingerprints": {"kind:fx-rate": "fx-b"},
            },
            {
                **before[2],
                "generationId": "portfolio-b",
                "semanticFingerprints": {"position": "portfolio-b"},
                "semanticDependencyFingerprints": {"field:positionweight": "portfolio-b"},
            },
        ]
        rules = [
            {
                "ruleId": "graph.test.market.v1",
                "conditions": [{"field": "currentPrice", "operator": ">", "value": 0}],
            },
            {
                "ruleId": "graph.test.fx.v1",
                "conditions": [{
                    "kind": "relation",
                    "relationType": "HAS_FX_RATE",
                    "targetKind": "fx-rate",
                }],
            },
            {
                "ruleId": "graph.test.portfolio.v1",
                "conditions": [{"field": "positionWeight", "operator": ">", "value": 0}],
            },
        ]

        plan = build_inference_impact_plan(
            before,
            after,
            ["005930"],
            explicit_target_symbols=["005930"],
            rules=rules,
            requested_fact_families=["market"],
        )

        self.assertFalse(plan["globalImpact"])
        self.assertTrue(plan["snapshotGlobalImpact"])
        self.assertTrue(plan["eventBoundaryAuthoritative"])
        self.assertFalse(plan["boundedGlobalContext"])
        self.assertEqual(["SUBJECT"], plan["impactDomains"])
        self.assertTrue(plan["eventScopedRuleSelection"])
        self.assertEqual(["market"], plan["routingScopeFamilies"])
        self.assertEqual(["graph.test.market.v1"], plan["candidateRuleIds"])
        self.assertEqual(
            ["macro:fx", "portfolio:main"],
            plan["deferredSharedContextScopeIds"],
        )
        self.assertEqual(
            ["macro:fx", "portfolio:main"],
            plan["deferredGlobalImpactScopeIds"],
        )
        self.assertIn(
            "event-scoped-shared-context-routing",
            plan["diagnostics"]["reasonCodes"],
        )

    def test_compact_impact_plan_disables_incremental_selection_when_route_is_incomplete(self):
        compact = compact_inference_impact_plan({
            "candidateRuleIds": ["graph.test.only-one"],
            "deferredRuleIds": ["graph.test.deferred"],
            "candidateRuleCount": 2,
            "enabledRuleCount": 3,
            "nativeRuleSelectionEligible": True,
            "ruleRoutingComplete": True,
        })

        self.assertFalse(compact["ruleRoutingComplete"])
        self.assertFalse(compact["nativeRuleSelectionEligible"])

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
        self.assertEqual(CHANGE_IMPACT_VERSION, trace.properties["impactPlanVersion"])
        self.assertEqual(["005930"], trace.properties["inferenceImpactPlan"]["inferenceTargetSymbols"])
        self.assertEqual("subject-dependency-selected-native-evaluation", trace.properties["ruleExecutionScope"])
        self.assertFalse(trace.properties["nativeRuleSelectionApplied"])

if __name__ == "__main__":
    unittest.main()
