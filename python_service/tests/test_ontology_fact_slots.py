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

    def test_market_observation_followup_retains_complete_market_closure(self):
        plan = build_fact_slot_projection_plan(
            ["005930"],
            ["market"],
            requested_fact_families_by_symbol={"005930": ["market"]},
            changed_fields_by_symbol={"005930": ["marketObservationFollowup"]},
        )

        self.assertEqual([], plan["preciseFieldRoutingSymbols"])
        self.assertIn("temporal", plan["slotFamiliesBySymbol"]["005930"])
        self.assertIn("position", plan["slotFamiliesBySymbol"]["005930"])

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

    def test_unclassified_legacy_link_falls_back_to_all_candidates(self):
        plan = build_fact_slot_projection_plan(["005930"], ["market"])
        scopes = {
            "link:symbol:005930:market": {
                "scopeFamily": "link",
            },
            "symbol:005930:market": {
                "scopeFamily": "market",
                "impactScopeFamilies": ["market"],
            },
        }

        selection = select_fact_slot_scope_ids(scopes, scopes.keys(), plan)

        self.assertFalse(selection["enabled"])
        self.assertEqual("fallback-unknown-scope-family", selection["status"])
        self.assertEqual(sorted(scopes), selection["selectedScopeIds"])

    def test_unknown_event_fact_family_never_narrows_a_projection(self):
        plan = build_fact_slot_projection_plan(["005930"], ["future-provider-fact"])

        self.assertFalse(plan["enabled"])
        self.assertEqual("disabled-unknown-event-family", plan["status"])
        self.assertEqual("unknown-event-fact-family", plan["fallbackReason"])

    def test_mixed_batch_routes_fact_slots_by_symbol(self):
        plan = build_fact_slot_projection_plan(
            ["AAPL", "066570"],
            ["market", "evidence"],
            requested_fact_families_by_symbol={
                "AAPL": ["evidence"],
                "066570": ["market"],
            },
        )
        scopes = {
            "symbol:AAPL:evidence": {
                "scopeFamily": "evidence",
                "impactScopeFamilies": ["evidence"],
            },
            "symbol:AAPL:market": {
                "scopeFamily": "market",
                "impactScopeFamilies": ["market"],
            },
            "symbol:066570:market": {
                "scopeFamily": "market",
                "impactScopeFamilies": ["market"],
            },
            "symbol:066570:evidence": {
                "scopeFamily": "evidence",
                "impactScopeFamilies": ["evidence"],
            },
        }

        selection = select_fact_slot_scope_ids(scopes, scopes.keys(), plan)

        self.assertTrue(selection["enabled"])
        self.assertEqual("applied", selection["status"])
        self.assertEqual(
            ["symbol:066570:market", "symbol:AAPL:evidence"],
            selection["selectedScopeIds"],
        )
        self.assertEqual(
            ["symbol:066570:evidence", "symbol:AAPL:market"],
            selection["deferredScopeIds"],
        )

    def test_manifest_selector_uses_fact_slots_for_an_actual_target_graph(self):
        graph = PortfolioOntology(
            "main",
            entities=[
                OntologyEntity("stock:005930", "Samsung", "stock", {
                    "ontologyBox": "ABox", "symbol": "005930", "sector": "technology",
                }),
                OntologyEntity("price:005930", "Price", "price-metric", {
                    "ontologyBox": "ABox", "symbol": "005930", "currentPrice": 70000,
                }),
                OntologyEntity("news:005930:1", "News", "news-article", {
                    "ontologyBox": "ABox", "symbol": "005930", "headline": "old",
                }),
            ],
            relations=[
                OntologyRelation("stock:005930", "price:005930", "HAS_PRICE", properties={
                    "ontologyBox": "ABox", "symbol": "005930",
                }),
                OntologyRelation("stock:005930", "news:005930:1", "HAS_EXTERNAL_SIGNAL", properties={
                    "ontologyBox": "ABox", "symbol": "005930",
                }),
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
        next(item for item in graph.entities if item.entity_id == "price:005930").properties["currentPrice"] = 71000
        next(item for item in graph.entities if item.entity_id == "stock:005930").properties["sector"] = "platform"
        next(item for item in graph.entities if item.entity_id == "news:005930:1").properties["headline"] = "new"
        apply_scoped_abox_identity(graph, world_id="portfolio:local:test")

        selection = select_target_scoped_manifest_patch(
            graph,
            active,
            ["005930"],
            fact_slot_plan=build_fact_slot_projection_plan(
                ["005930"],
                ["market"],
                requested_fact_families_by_symbol={"005930": ["market"]},
                changed_fields_by_symbol={"005930": ["current_price"]},
            ),
        )

        self.assertTrue(selection["applied"])
        self.assertEqual("applied", selection["factSlot"]["status"])
        self.assertTrue(any(":market" in scope_id for scope_id in selection["selectedIncomingScopeIds"]))
        self.assertFalse(any(":evidence" in scope_id for scope_id in selection["selectedIncomingScopeIds"]))
        self.assertTrue(any(":evidence" in scope_id for scope_id in selection["deferredScopeIds"]))
        selected_trace = {
            item["scopeId"]: item
            for item in selection["scopeSelectionTrace"]["selected"]
        }
        market_scope = next(
            scope_id
            for scope_id in selected_trace
            if scope_id.startswith("symbol:005930:market")
        )
        self.assertIn("semantic-value-change", selected_trace[market_scope]["reasons"])
        self.assertIn("event-fact-slot", selected_trace[market_scope]["reasons"])
        self.assertIn("market", selected_trace[market_scope]["semanticChangedFamilies"])
        self.assertTrue(
            any(
                "required-changed-link-endpoint" in item["reasons"]
                for item in selected_trace.values()
            ),
            selected_trace,
        )
        deferred_trace = {
            item["scopeId"]: item
            for item in selection["scopeSelectionTrace"]["deferred"]
        }
        evidence_scope = next(
            scope_id
            for scope_id in deferred_trace
            if scope_id.startswith("symbol:005930:evidence")
        )
        self.assertIn(
            "semantic-value-change",
            deferred_trace[evidence_scope]["reasons"],
        )
        self.assertIn(
            "deferred-unrelated-event-fact-slot",
            deferred_trace[evidence_scope]["reasons"],
        )

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
            ["evidence", "link", "temporal"],
            plan["slotFamiliesBySymbol"]["005930"],
        )
        self.assertNotIn("market", plan["slotFamiliesBySymbol"]["005930"])
        self.assertNotIn("financial", plan["slotFamiliesBySymbol"]["005930"])

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
