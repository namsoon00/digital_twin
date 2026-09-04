import unittest
from copy import deepcopy

from digital_twin.domain.ontology_fact_slots import (
    build_fact_slot_projection_plan,
    select_fact_slot_scope_ids,
)
from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_scopes import (
    SCOPED_ABOX_MANIFEST_VERSION,
    SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
    apply_scoped_abox_identity,
    merge_target_scoped_abox_manifest,
    select_target_scoped_manifest_patch,
)


class OntologyFactSlotTests(unittest.TestCase):
    def test_non_symbol_fact_ownership_is_stable_across_graph_shapes(self):
        factor_id = "factor:rate-sensitive-growth"
        compact = PortfolioOntology(
            "compact",
            entities=[
                OntologyEntity(factor_id, "Rate sensitive growth", "factor", {
                    "ontologyBox": "ABox",
                }),
                OntologyEntity("stock:PLTR", "Palantir", "stock", {
                    "ontologyBox": "ABox", "symbol": "PLTR",
                }),
            ],
            relations=[OntologyRelation(
                "stock:PLTR",
                factor_id,
                "EXPOSED_TO",
                properties={"ontologyBox": "ABox"},
            )],
        )
        complete = PortfolioOntology(
            "complete",
            entities=[
                OntologyEntity(factor_id, "Rate sensitive growth", "factor", {
                    "ontologyBox": "ABox",
                }),
                OntologyEntity("stock:PLTR", "Palantir", "stock", {
                    "ontologyBox": "ABox", "symbol": "PLTR",
                }),
                OntologyEntity("stock:TSLA", "Tesla", "stock", {
                    "ontologyBox": "ABox", "symbol": "TSLA",
                }),
            ],
            relations=[
                OntologyRelation(
                    "stock:PLTR",
                    factor_id,
                    "EXPOSED_TO",
                    properties={"ontologyBox": "ABox"},
                ),
                OntologyRelation(
                    "stock:TSLA",
                    factor_id,
                    "EXPOSED_TO",
                    properties={"ontologyBox": "ABox"},
                ),
            ],
        )

        apply_scoped_abox_identity(compact, account_id="main")
        apply_scoped_abox_identity(complete, account_id="main")

        compact_scope = next(
            entity.properties["aboxScopeId"]
            for entity in compact.entities
            if entity.entity_id == factor_id
        )
        complete_scope = next(
            entity.properties["aboxScopeId"]
            for entity in complete.entities
            if entity.entity_id == factor_id
        )
        self.assertEqual(compact_scope, complete_scope)
        self.assertTrue(compact_scope.startswith("reference:item:"))

    def test_dynamic_account_facts_use_independent_item_scopes(self):
        graph = PortfolioOntology(
            "main",
            entities=[
                OntologyEntity(
                    "inferred-portfolio-activity:main:1",
                    "Portfolio activity",
                    "inferred-portfolio-activity",
                    {"ontologyBox": "ABox"},
                ),
                OntologyEntity(
                    "portfolio-action-candidate:main:1",
                    "Action candidate",
                    "portfolio-action-candidate",
                    {"ontologyBox": "ABox"},
                ),
                OntologyEntity(
                    "position-exposure:main:MSTR",
                    "MSTR exposure",
                    "position-exposure",
                    {"ontologyBox": "ABox", "exposureKey": "MSTR"},
                ),
            ],
        )

        apply_scoped_abox_identity(graph, account_id="main")
        scopes = {
            entity.entity_id: entity.properties["aboxScopeId"]
            for entity in graph.entities
        }

        self.assertTrue(
            scopes["inferred-portfolio-activity:main:1"].startswith(
                "episode:main:item:"
            )
        )
        self.assertTrue(
            scopes["portfolio-action-candidate:main:1"].startswith(
                "episode:main:item:"
            )
        )
        self.assertTrue(
            scopes["position-exposure:main:MSTR"].startswith(
                "symbol:MSTR:"
            )
        )

    def test_company_valuation_section_routes_without_other_company_slots(self):
        plan = build_fact_slot_projection_plan(
            ["MSTR"],
            ["company-valuation"],
            requested_fact_families_by_symbol={"MSTR": ["company-valuation"]},
            changed_fields_by_symbol={
                "MSTR": ["external.companyKnowledge.valuation"],
            },
        )

        self.assertEqual(["MSTR"], plan["preciseFieldRoutingSymbols"])
        self.assertEqual(
            ["company-valuation", "valuation"],
            plan["slotFamiliesBySymbol"]["MSTR"],
        )
        self.assertNotIn("fundamental", plan["slotFamiliesBySymbol"]["MSTR"])
        self.assertNotIn("governance", plan["slotFamiliesBySymbol"]["MSTR"])
        self.assertNotIn("capital", plan["slotFamiliesBySymbol"]["MSTR"])

    def test_portfolio_risk_event_routes_only_portfolio_position_and_exposure_slots(self):
        plan = build_fact_slot_projection_plan(
            ["MSTR"],
            ["portfolio", "position", "exposure"],
            requested_fact_families_by_symbol={
                "MSTR": ["portfolio", "position", "exposure"],
            },
            changed_fields_by_symbol={
                "MSTR": ["portfolioRisk", "positionRisk", "rebalanceScenario"],
            },
        )

        self.assertTrue(plan["enabled"])
        self.assertEqual("ready", plan["status"])
        self.assertEqual(["MSTR"], plan["preciseFieldRoutingSymbols"])
        self.assertEqual(
            ["exposure", "portfolio", "position"],
            plan["slotFamiliesBySymbol"]["MSTR"],
        )

    def test_precise_market_fields_route_only_changed_fact_families(self):
        plan = build_fact_slot_projection_plan(
            ["005930"],
            ["market"],
            requested_fact_families_by_symbol={"005930": ["market"]},
            changed_fields_by_symbol={
                "005930": ["current_price", "profit_loss_rate"],
            },
        )
        scopes = {
            "symbol:005930:market": {
                "scopeFamily": "market",
                "impactScopeFamilies": ["market"],
                "semanticFingerprints": {"market": "changed"},
            },
            "symbol:005930:position": {
                "scopeFamily": "position",
                "impactScopeFamilies": ["position", "market"],
                "semanticFingerprints": {"position": "changed"},
            },
            "symbol:005930:temporal": {
                "scopeFamily": "temporal",
                "impactScopeFamilies": ["temporal", "market"],
                "semanticFingerprints": {"temporal": "changed"},
            },
            "symbol:005930:flow": {
                "scopeFamily": "flow",
                "impactScopeFamilies": ["flow", "market"],
                "semanticFingerprints": {"flow": "changed"},
            },
            "link:symbol:005930:market": {
                "scopeFamily": "market",
                "impactScopeFamilies": ["market"],
                "semanticFingerprints": {"market": "changed"},
            },
            "symbol:005930:valuation": {
                "scopeFamily": "valuation",
                "impactScopeFamilies": ["valuation", "market"],
                "semanticFingerprints": {"valuation": "changed", "market": "dependency-only"},
            },
        }

        selection = select_fact_slot_scope_ids(scopes, scopes.keys(), plan)

        self.assertEqual(["005930"], plan["preciseFieldRoutingSymbols"])
        self.assertEqual(
            ["market", "position"],
            plan["slotFamiliesBySymbol"]["005930"],
        )
        self.assertEqual(["market", "position"], plan["slotFamilies"])
        self.assertEqual(
            [
                "link:symbol:005930:market",
                "symbol:005930:market",
                "symbol:005930:position",
            ],
            selection["selectedScopeIds"],
        )
        self.assertEqual(
            [
                "symbol:005930:flow",
                "symbol:005930:temporal",
                "symbol:005930:valuation",
            ],
            selection["deferredScopeIds"],
        )

    def test_unknown_changed_field_keeps_conservative_family_closure(self):
        plan = build_fact_slot_projection_plan(
            ["005930"],
            ["market"],
            requested_fact_families_by_symbol={"005930": ["market"]},
            changed_fields_by_symbol={"005930": ["future_metric"]},
        )

        self.assertEqual([], plan["preciseFieldRoutingSymbols"])
        self.assertEqual(
            ["future_metric"],
            plan["unclassifiedChangedFieldsBySymbol"]["005930"],
        )
        self.assertIn("temporal", plan["slotFamiliesBySymbol"]["005930"])

    def test_market_event_selects_price_derived_facts_and_defers_evidence(self):
        plan = build_fact_slot_projection_plan(["005930"], ["market"])
        scopes = {
            "symbol:005930:market": {
                "scopeFamily": "market",
                "impactScopeFamilies": ["market"],
                "semanticFingerprints": {"market": "changed"},
            },
            "symbol:005930:temporal": {
                "scopeFamily": "temporal",
                "impactScopeFamilies": ["temporal", "market"],
                "semanticFingerprints": {"temporal": "changed"},
            },
            "symbol:005930:position": {
                "scopeFamily": "position",
                "impactScopeFamilies": ["position", "market"],
                "semanticFingerprints": {"position": "changed"},
            },
            "symbol:005930:evidence": {
                "scopeFamily": "evidence",
                "impactScopeFamilies": ["evidence"],
                "semanticFingerprints": {"evidence": "changed"},
            },
            "symbol:005930:exposure": {
                "scopeFamily": "exposure",
                "impactScopeFamilies": ["exposure"],
                "semanticFingerprints": {"exposure": "changed"},
            },
        }

        selection = select_fact_slot_scope_ids(scopes, scopes.keys(), plan)

        self.assertTrue(selection["enabled"])
        self.assertEqual("applied", selection["status"])
        self.assertEqual(
            [
                "symbol:005930:market",
                "symbol:005930:position",
                "symbol:005930:temporal",
            ],
            selection["selectedScopeIds"],
        )
        self.assertEqual(
            ["symbol:005930:evidence", "symbol:005930:exposure"],
            selection["deferredScopeIds"],
        )

    def test_unknown_event_fact_family_never_narrows_a_projection(self):
        plan = build_fact_slot_projection_plan(["005930"], ["future-provider-fact"])

        self.assertFalse(plan["enabled"])
        self.assertEqual("disabled-unknown-event-family", plan["status"])
        self.assertEqual("unknown-event-fact-family", plan["fallbackReason"])

    def test_authoritative_event_boundary_does_not_expand_calendar_change_to_market_facts(self):
        plan = build_fact_slot_projection_plan(
            ["005930"],
            ["temporal", "evidence"],
            requested_fact_families_by_symbol={"005930": ["temporal", "evidence"]},
            changed_fields_by_symbol={"005930": ["calendar_event"]},
            event_boundary_authoritative=True,
        )

        self.assertTrue(plan["eventBoundaryAuthoritative"])
        self.assertEqual(
            ["evidence", "temporal"],
            plan["slotFamiliesBySymbol"]["005930"],
        )
        self.assertNotIn("market", plan["slotFamiliesBySymbol"]["005930"])
        self.assertNotIn("financial", plan["slotFamiliesBySymbol"]["005930"])

    def test_authoritative_dependency_key_selects_only_matching_event_scopes(self):
        plan = build_fact_slot_projection_plan(
            ["005930"],
            ["temporal", "evidence"],
            requested_fact_families_by_symbol={"005930": ["temporal", "evidence"]},
            event_boundary_authoritative=True,
            requested_dependency_keys=["kind:earnings-calendar-event"],
            requested_dependency_keys_by_symbol={
                "005930": ["kind:earnings-calendar-event"],
            },
            dependency_boundary_authoritative=True,
        )
        scopes = {
            "symbol:005930:evidence:bucket:01": {
                "scopeFamily": "evidence",
                "semanticDependencyFingerprints": {
                    "kind:earnings-calendar-event": "event-v1",
                },
            },
            "link:symbol:005930:evidence:bucket:01": {
                "scopeFamily": "evidence",
                "semanticDependencyFingerprints": {
                    "relation:mentions-instrument": "link-v1",
                },
                "dependencyScopeIds": ["symbol:005930:evidence:bucket:01"],
            },
            "symbol:005930:evidence:bucket:02": {
                "scopeFamily": "evidence",
                "semanticDependencyFingerprints": {
                    "kind:news-article": "news-v1",
                },
            },
            "symbol:005930:temporal:window:20d": {
                "scopeFamily": "temporal",
                "semanticDependencyFingerprints": {
                    "kind:temporal-window": "window-v1",
                },
            },
        }

        selection = select_fact_slot_scope_ids(scopes, scopes.keys(), plan)

        self.assertTrue(selection["enabled"])
        self.assertEqual(
            [
                "link:symbol:005930:evidence:bucket:01",
                "symbol:005930:evidence:bucket:01",
            ],
            selection["selectedScopeIds"],
        )
        self.assertEqual(
            ["symbol:005930:evidence:bucket:01"],
            selection["dependencyMatchedScopeIds"],
        )
        self.assertEqual(
            [
                "symbol:005930:evidence:bucket:02",
                "symbol:005930:temporal:window:20d",
            ],
            selection["deferredScopeIds"],
        )

    def test_missing_authoritative_dependency_index_fails_closed(self):
        plan = build_fact_slot_projection_plan(
            ["005930"],
            ["evidence"],
            event_boundary_authoritative=True,
            requested_dependency_keys=["kind:earnings-calendar-event"],
            dependency_boundary_authoritative=True,
        )
        scopes = {
            "symbol:005930:evidence": {
                "scopeFamily": "evidence",
                "semanticDependencyFingerprints": {"kind:news-article": "news-v1"},
            },
        }

        selection = select_fact_slot_scope_ids(scopes, scopes.keys(), plan)

        self.assertFalse(selection["enabled"])
        self.assertEqual(
            "blocked-dependency-key-no-scope-match",
            selection["status"],
        )
        self.assertEqual([], selection["selectedScopeIds"])
        self.assertEqual(list(scopes), selection["deferredScopeIds"])

    def test_authoritative_dependency_already_current_is_semantic_noop(self):
        plan = build_fact_slot_projection_plan(
            ["MSTR"],
            ["flow", "market", "temporal"],
            requested_dependency_keys=["kind:stock:field:currentprice"],
            requested_dependency_keys_by_symbol={
                "MSTR": ["kind:stock:field:currentprice"],
            },
            dependency_boundary_authoritative=True,
        )
        scopes = {
            "symbol:MSTR:state": {
                "scopeFamily": "state",
                "semanticDependencyFingerprints": {
                    "kind:stock:field:currentprice": "price-v2",
                },
            },
            "symbol:MSTR:evidence": {
                "scopeFamily": "evidence",
                "semanticDependencyFingerprints": {
                    "kind:news-article": "news-v2",
                },
            },
        }

        selection = select_fact_slot_scope_ids(
            scopes,
            ["symbol:MSTR:evidence"],
            plan,
        )

        self.assertTrue(selection["enabled"])
        self.assertEqual(
            "applied-noop-dependency-already-current",
            selection["status"],
        )
        self.assertEqual([], selection["selectedScopeIds"])
        self.assertEqual(
            ["symbol:MSTR:state"],
            selection["unchangedDependencyMatchedScopeIds"],
        )
        self.assertEqual(
            ["symbol:MSTR:evidence"],
            selection["deferredScopeIds"],
        )

    def test_authoritative_dependency_overrides_physical_scope_family(self):
        plan = build_fact_slot_projection_plan(
            ["MSTR"],
            ["flow", "market", "temporal"],
            requested_dependency_keys=["kind:stock:field:currentprice"],
            requested_dependency_keys_by_symbol={
                "MSTR": ["kind:stock:field:currentprice"],
            },
            dependency_boundary_authoritative=True,
        )
        scopes = {
            "symbol:MSTR:state": {
                "scopeFamily": "state",
                "semanticDependencyFingerprints": {
                    "kind:stock:field:currentprice": "price-v3",
                },
            },
        }

        selection = select_fact_slot_scope_ids(
            scopes,
            ["symbol:MSTR:state"],
            plan,
        )

        self.assertTrue(selection["enabled"])
        self.assertEqual("applied", selection["status"])
        self.assertEqual(
            ["symbol:MSTR:state"],
            selection["selectedScopeIds"],
        )

        # A price-only event must not pull a changed news relation back into the
        # semantic write set through its physical dependency on instrument state.
        plan = build_fact_slot_projection_plan(
            ["MSTR"],
            ["flow", "market", "temporal"],
            requested_fact_families_by_symbol={
                "MSTR": ["flow", "market", "temporal"],
            },
            requested_dependency_keys=["kind:stock:field:currentprice"],
            requested_dependency_keys_by_symbol={
                "MSTR": ["kind:stock:field:currentprice"],
            },
            dependency_boundary_authoritative=True,
            event_boundary_authoritative=True,
        )
        scopes = {
            "symbol:MSTR:state": {
                "scopeFamily": "state",
                "semanticDependencyFingerprints": {
                    "kind:stock:field:currentprice": "price-v4",
                },
            },
            "symbol:MSTR:evidence:bucket:47": {
                "scopeFamily": "evidence",
                "semanticDependencyFingerprints": {
                    "kind:news-article": "news-v2",
                },
            },
            "link:symbol:MSTR:evidence:bucket:47": {
                "scopeFamily": "evidence",
                "semanticDependencyFingerprints": {
                    "relation:mentions-instrument": "news-link-v2",
                },
                "dependencyScopeIds": [
                    "symbol:MSTR:state",
                    "symbol:MSTR:evidence:bucket:47",
                ],
            },
        }

        selection = select_fact_slot_scope_ids(scopes, scopes.keys(), plan)

        self.assertEqual(["symbol:MSTR:state"], selection["selectedScopeIds"])
        self.assertEqual(
            [
                "link:symbol:MSTR:evidence:bucket:47",
                "symbol:MSTR:evidence:bucket:47",
            ],
            selection["deferredScopeIds"],
        )
        self.assertEqual(
            ["symbol:MSTR:state"],
            selection["directSelectedScopeIds"],
        )
        self.assertEqual([], selection["reverseDependencySelectedScopeIds"])

    def _assert_crypto_dependency_selects_shared_market_and_symbol_exposure(self):
        plan = build_fact_slot_projection_plan(
            ["BTC", "MSTR"],
            ["market", "macro-crypto", "exposure"],
            requested_dependency_keys=[
                "kind:crypto-market-signal",
                "kind:crypto-exposure",
            ],
            requested_dependency_keys_by_symbol={
                "BTC": ["kind:crypto-market-signal", "kind:crypto-exposure"],
                "MSTR": ["kind:crypto-market-signal", "kind:crypto-exposure"],
            },
            dependency_boundary_authoritative=True,
        )
        scopes = {
            "macro:crypto": {
                "scopeFamily": "macro-crypto",
                "semanticDependencyFingerprints": {
                    "kind:crypto-market-signal": "btc-v2",
                },
            },
            "symbol:MSTR:exposure": {
                "scopeFamily": "exposure",
                "semanticDependencyFingerprints": {
                    "kind:crypto-exposure": "mstr-v2",
                },
            },
            "symbol:MSTR:market": {
                "scopeFamily": "market",
                "semanticDependencyFingerprints": {
                    "kind:price-bar": "price-v1",
                },
            },
        }

        selection = select_fact_slot_scope_ids(scopes, scopes.keys(), plan)

        self.assertTrue(selection["enabled"])
        self.assertEqual("applied", selection["status"])
        self.assertEqual(
            ["macro:crypto", "symbol:MSTR:exposure"],
            selection["selectedScopeIds"],
        )

    def test_authoritative_valuation_event_ignores_cross_family_impact_metadata(self):
        self._assert_crypto_dependency_selects_shared_market_and_symbol_exposure()
        plan = build_fact_slot_projection_plan(
            ["005930"],
            ["company-valuation"],
            requested_fact_families_by_symbol={"005930": ["company-valuation"]},
            changed_fields_by_symbol={
                "005930": ["external.companyKnowledge.valuation"],
            },
            event_boundary_authoritative=True,
        )
        self.assertEqual(
            ["company-valuation", "valuation"],
            plan["slotFamiliesBySymbol"]["005930"],
        )
        scopes = {
            "symbol:005930:company-valuation": {
                "scopeFamily": "company-valuation",
                "impactScopeFamilies": ["company-valuation", "market", "profile"],
                "semanticFingerprints": {"company-valuation": "valuation-v2"},
            },
            "link:symbol:005930:company-valuation": {
                "scopeFamily": "company-valuation",
                "impactScopeFamilies": ["company-valuation", "market", "profile"],
                "dependencyScopeIds": ["symbol:005930:company-valuation"],
            },
            "link:symbol:005930:market": {
                "scopeFamily": "market",
                "impactScopeFamilies": ["company-valuation", "market"],
                "dependencyScopeIds": ["symbol:005930:market"],
            },
            "symbol:005930:market": {
                "scopeFamily": "market",
                "impactScopeFamilies": ["company-valuation", "market"],
            },
        }

        selection = select_fact_slot_scope_ids(scopes, scopes.keys(), plan)

        self.assertEqual(
            [
                "link:symbol:005930:company-valuation",
                "symbol:005930:company-valuation",
            ],
            selection["selectedScopeIds"],
        )
        self.assertEqual(
            ["link:symbol:005930:market", "symbol:005930:market"],
            selection["deferredScopeIds"],
        )

    def test_authoritative_event_defers_unrelated_shared_link_rebind(self):
        graph = PortfolioOntology(
            "main",
            entities=[
                OntologyEntity("temporal:005930", "Temporal", "temporal-observation", {
                    "ontologyBox": "ABox", "symbol": "005930", "return1d": 1,
                }),
                OntologyEntity("portfolio:main", "Portfolio", "portfolio", {
                    "ontologyBox": "ABox", "accountId": "main",
                }),
                OntologyEntity("reference:taxonomy", "Taxonomy", "catalog-entry", {
                    "ontologyBox": "ABox", "version": "1",
                }),
            ],
            relations=[OntologyRelation(
                "portfolio:main",
                "reference:taxonomy",
                "USES_REFERENCE",
                properties={"ontologyBox": "ABox"},
            )],
        )
        first = apply_scoped_abox_identity(graph, world_id="portfolio:local:test")
        active = {
            "status": "ok",
            "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            "scopeTopologyVersion": SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
            "scopePlan": [deepcopy(item) for item in first["scopePlan"]],
            "scopeGenerationIds": dict(first["scopeGenerationIds"]),
            "scopeFingerprints": dict(first["scopeFingerprints"]),
        }
        graph.entities[0].properties["return1d"] = 2
        graph.entities[2].properties["version"] = "2"
        apply_scoped_abox_identity(graph, world_id="portfolio:local:test")

        selection = select_target_scoped_manifest_patch(
            graph,
            active,
            ["005930"],
            fact_slot_plan=build_fact_slot_projection_plan(
                ["005930"],
                ["temporal"],
                requested_fact_families_by_symbol={"005930": ["temporal"]},
                changed_fields_by_symbol={"005930": ["price_path"]},
                event_boundary_authoritative=True,
            ),
        )

        self.assertEqual(1, len(selection["selectedIncomingScopeIds"]))
        self.assertIn(":temporal:", selection["selectedIncomingScopeIds"][0])
        shared_link = next(
            scope_id for scope_id in selection["deferredScopeIds"]
            if scope_id.startswith("link:account:")
        )
        trace = {
            item["scopeId"]: item
            for item in selection["scopeSelectionTrace"]["deferred"]
        }
        self.assertIn("persistence-dependency-rebind", trace[shared_link]["reasons"])
        self.assertIn("deferred-shared-persistence-rebind", trace[shared_link]["reasons"])
        self.assertIn(
            shared_link,
            selection["scopeSelectionTrace"]["deferredPersistenceRebindScopeIds"],
        )
        self.assertEqual(
            1,
            selection["scopeSelectionTrace"]["deferredPersistenceRebindScopeCount"],
        )

    def test_authoritative_event_replaces_changed_relation_and_new_endpoint_together(self):
        state_scope = "symbol:028260:market:bucket:00"
        assessment_scope = "symbol:028260:company-valuation:bucket:03"
        link_scope = "link:symbol:028260:company-valuation:bucket:03"
        old_assessment_id = "model-hypothesis-assessment:028260:old"
        new_assessment_id = "model-hypothesis-assessment:028260:new"
        graph = PortfolioOntology(
            "main",
            entities=[
                OntologyEntity("stock:028260", "Samsung C&T", "stock", {
                    "ontologyBox": "ABox",
                    "symbol": "028260",
                    "aboxScopeId": state_scope,
                }),
                OntologyEntity(new_assessment_id, "New assessment", "assessment", {
                    "ontologyBox": "ABox",
                    "symbol": "028260",
                    "aboxScopeId": assessment_scope,
                }),
            ],
            relations=[OntologyRelation(
                "stock:028260",
                new_assessment_id,
                "HAS_HYPOTHESIS_ASSESSMENT",
                properties={"ontologyBox": "ABox", "aboxScopeId": link_scope},
            )],
        )
        graph.worldview = {
            "scopePlan": [
                {
                    "scopeId": state_scope,
                    "scopeType": "symbol",
                    "scopeFamily": "market",
                    "impactScopeFamilies": ["market"],
                    "baseFingerprint": "state-new",
                    "fingerprint": "state-new-with-dependencies",
                    "generationId": "state-generation-new",
                    "dependencyScopeIds": [],
                    "entityCount": 1,
                    "relationCount": 0,
                },
                {
                    "scopeId": assessment_scope,
                    "scopeType": "symbol",
                    "scopeFamily": "company-valuation",
                    "impactScopeFamilies": ["company-valuation"],
                    "baseFingerprint": "assessment-new",
                    "fingerprint": "assessment-new-with-dependencies",
                    "generationId": "assessment-generation-new",
                    "dependencyScopeIds": [],
                    "entityCount": 1,
                    "relationCount": 0,
                },
                {
                    "scopeId": link_scope,
                    "scopeType": "link",
                    "scopeFamily": "company-valuation",
                    "impactScopeFamilies": ["company-valuation"],
                    "baseFingerprint": "relation-to-new-assessment",
                    "fingerprint": "relation-new-with-dependencies",
                    "generationId": "link-generation-new",
                    "dependencyScopeIds": [state_scope, assessment_scope],
                    "entityCount": 0,
                    "relationCount": 1,
                },
            ],
        }
        active = {
            "status": "ok",
            "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            "scopeTopologyVersion": SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
            "scopePlan": [
                {
                    "scopeId": state_scope,
                    "scopeType": "symbol",
                    "scopeFamily": "market",
                    "impactScopeFamilies": ["market"],
                    "baseFingerprint": "state-old",
                    "fingerprint": "state-old-with-dependencies",
                    "generationId": "state-generation-old",
                    "dependencyScopeIds": [],
                    "entityCount": 1,
                    "relationCount": 0,
                },
                {
                    "scopeId": assessment_scope,
                    "scopeType": "symbol",
                    "scopeFamily": "company-valuation",
                    "impactScopeFamilies": ["company-valuation"],
                    "baseFingerprint": "assessment-old",
                    "fingerprint": "assessment-old-with-dependencies",
                    "generationId": "assessment-generation-old",
                    "dependencyScopeIds": [],
                    "entityCount": 1,
                    "relationCount": 0,
                },
                {
                    "scopeId": link_scope,
                    "scopeType": "link",
                    "scopeFamily": "company-valuation",
                    "impactScopeFamilies": ["company-valuation"],
                    "baseFingerprint": "relation-to-old-assessment",
                    "fingerprint": "relation-old-with-dependencies",
                    "generationId": "link-generation-old",
                    "dependencyScopeIds": [state_scope, assessment_scope],
                    "entityCount": 0,
                    "relationCount": 1,
                },
            ],
        }

        selection = select_target_scoped_manifest_patch(
            graph,
            active,
            ["028260"],
            fact_slot_plan={
                "enabled": True,
                "status": "ready",
                "targetSymbols": ["028260"],
                "requestedFactFamilies": ["market"],
                "requestedFactFamiliesBySymbol": {"028260": ["market"]},
                "slotFamilies": ["market"],
                "slotFamiliesBySymbol": {"028260": ["market"]},
                "eventBoundaryAuthoritative": True,
            },
        )

        self.assertEqual("ready", selection["status"])
        self.assertEqual(
            {state_scope, assessment_scope, link_scope},
            set(selection["selectedIncomingScopeIds"]),
        )
        self.assertNotIn(link_scope, selection["deferredScopeIds"])
        trace = {
            item["scopeId"]: item
            for item in selection["scopeSelectionTrace"]["selected"]
        }
        self.assertIn(
            "required-dependent-link-semantic-replacement",
            trace[link_scope]["reasons"],
        )
        self.assertEqual(new_assessment_id, graph.relations[0].target)
        self.assertNotEqual(old_assessment_id, graph.relations[0].target)

    def test_endpoint_companion_does_not_expand_into_other_owned_relations(self):
        graph = PortfolioOntology(
            "main",
            entities=[
                OntologyEntity("stock:005930", "Samsung", "stock", {
                    "ontologyBox": "ABox", "symbol": "005930", "label": "Samsung",
                }),
                OntologyEntity("valuation:005930", "Valuation", "company-valuation", {
                    "ontologyBox": "ABox", "symbol": "005930", "per": 10,
                }),
                OntologyEntity("price:005930", "Price", "market-observation", {
                    "ontologyBox": "ABox", "symbol": "005930", "price": 70000,
                }),
            ],
            relations=[
                OntologyRelation(
                    "stock:005930",
                    "valuation:005930",
                    "HAS_VALUATION",
                    properties={"ontologyBox": "ABox"},
                ),
                OntologyRelation(
                    "stock:005930",
                    "price:005930",
                    "HAS_OBSERVATION",
                    properties={"ontologyBox": "ABox"},
                ),
            ],
        )
        first = apply_scoped_abox_identity(graph, world_id="portfolio:local:test")
        active = {
            "status": "ok",
            "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            "scopeTopologyVersion": SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
            "scopePlan": [deepcopy(item) for item in first["scopePlan"]],
            "scopeGenerationIds": dict(first["scopeGenerationIds"]),
            "scopeFingerprints": dict(first["scopeFingerprints"]),
        }
        graph.entities[0].properties["label"] = "Samsung Electronics"
        graph.entities[1].properties["per"] = 11
        apply_scoped_abox_identity(graph, world_id="portfolio:local:test")

        selection = select_target_scoped_manifest_patch(
            graph,
            active,
            ["005930"],
            fact_slot_plan=build_fact_slot_projection_plan(
                ["005930"],
                ["company-valuation"],
                requested_fact_families_by_symbol={
                    "005930": ["company-valuation"],
                },
                changed_fields_by_symbol={
                    "005930": ["external.companyKnowledge.valuation"],
                },
                event_boundary_authoritative=True,
            ),
        )

        selected = set(selection["selectedIncomingScopeIds"])
        self.assertTrue(any(
            value.startswith("symbol:005930:company-valuation:")
            for value in selected
        ))
        self.assertTrue(any(
            value.startswith("link:symbol:005930:company-valuation:")
            for value in selected
        ))
        self.assertTrue(any(
            value.startswith("symbol:005930:state:")
            for value in selected
        ))
        self.assertFalse(any(
            value.startswith("symbol:005930:market:")
            for value in selected
        ))
        self.assertFalse(any(
            value.startswith("link:symbol:005930:market:")
            for value in selected
        ))

    def test_v8_online_migration_replaces_only_the_requested_subject(self):
        graph = PortfolioOntology(
            "main",
            entities=[
                OntologyEntity("temporal:005930:5d", "Temporal", "temporal-observation", {
                    "ontologyBox": "ABox", "symbol": "005930", "window": "5d", "return": 1.2,
                }),
                OntologyEntity("news:005930:1", "News", "news-article", {
                    "ontologyBox": "ABox", "symbol": "005930", "headline": "verified",
                }),
            ],
        )
        apply_scoped_abox_identity(graph, world_id="portfolio:local:test")
        active = {
            "status": "ok",
            "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            "scopeTopologyVersion": "granular-v7-persisted-instrument-anchor",
            "scopePlan": [
                {
                    "scopeId": "symbol:005930:temporal",
                    "scopeFamily": "temporal",
                    "generationId": "legacy-temporal",
                    "fingerprint": "legacy-temporal",
                },
                {
                    "scopeId": "symbol:005930:evidence",
                    "scopeFamily": "evidence",
                    "generationId": "legacy-evidence",
                    "fingerprint": "legacy-evidence",
                },
                {
                    "scopeId": "symbol:000660:temporal",
                    "scopeFamily": "temporal",
                    "generationId": "other-subject",
                    "fingerprint": "other-subject",
                },
                {
                    "scopeId": "reference:legacy-catalog",
                    "scopeFamily": "reference",
                    "generationId": "shared-reference",
                    "fingerprint": "shared-reference",
                },
            ],
        }

        result = merge_target_scoped_abox_manifest(graph, active, ["005930"])

        self.assertTrue(result["applied"])
        self.assertTrue(result["scopeTopologyMigration"]["applied"])
        self.assertEqual(
            ["symbol:005930:evidence", "symbol:005930:temporal"],
            result["retiredScopeIds"],
        )
        merged_scope_ids = {
            item["scopeId"]
            for item in result["scopePlan"]
        }
        self.assertIn("symbol:000660:temporal", merged_scope_ids)
        self.assertIn("reference:legacy-catalog", merged_scope_ids)
        self.assertFalse({
            "symbol:005930:evidence",
            "symbol:005930:temporal",
        } & merged_scope_ids)
        self.assertTrue(any(
            scope_id.startswith("symbol:005930:evidence:bucket:")
            for scope_id in merged_scope_ids
        ))
        self.assertTrue(any(
            scope_id.startswith("symbol:005930:temporal:window:5d")
            for scope_id in merged_scope_ids
        ))
        self.assertEqual(
            SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
            graph.worldview["scopeTopologyVersion"],
        )

    def test_manifest_repair_path_keeps_topology_migration_as_structured_metadata(self):
        graph = PortfolioOntology(
            "main",
            entities=[OntologyEntity("stock:005930", "Samsung", "stock", {
                "ontologyBox": "ABox", "symbol": "005930",
            })],
        )
        first = apply_scoped_abox_identity(graph, world_id="portfolio:local:test")
        active_scope_plan = [deepcopy(item) for item in first["scopePlan"]]
        active_scope_plan.append({
            "scopeId": "reference:removed-catalog",
            "scopeFamily": "reference",
            "generationId": "reference:g1",
            "fingerprint": "reference:f1",
        })

        result = select_target_scoped_manifest_patch(
            graph,
            {
                "status": "ok",
                "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
                "scopeTopologyVersion": SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
                "scopePlan": active_scope_plan,
            },
            ["005930"],
        )

        self.assertEqual("skipped-removed-scope-requires-full-refresh", result["status"])
        self.assertIsInstance(result["scopeTopologyMigration"], dict)
        self.assertFalse(result["scopeTopologyMigration"]["applied"])
        self.assertEqual(
            SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
            result["scopeTopologyMigration"]["toVersion"],
        )


if __name__ == "__main__":
    unittest.main()
