import unittest

from digital_twin.domain.ontology_fact_slots import (
    build_fact_slot_projection_plan,
    select_fact_slot_scope_ids,
)
from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_scopes import (
    SCOPED_ABOX_MANIFEST_VERSION,
    SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
    apply_scoped_abox_identity,
    select_target_scoped_manifest_patch,
)


class OntologyFactSlotTests(unittest.TestCase):
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
                    "ontologyBox": "ABox", "symbol": "005930",
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
            "scopePlan": [dict(item) for item in first["scopePlan"]],
            "scopeGenerationIds": dict(first["scopeGenerationIds"]),
            "scopeFingerprints": dict(first["scopeFingerprints"]),
        }
        next(item for item in graph.entities if item.entity_id == "price:005930").properties["currentPrice"] = 71000
        next(item for item in graph.entities if item.entity_id == "news:005930:1").properties["headline"] = "new"
        apply_scoped_abox_identity(graph, world_id="portfolio:local:test")

        selection = select_target_scoped_manifest_patch(
            graph,
            active,
            ["005930"],
            fact_slot_plan=build_fact_slot_projection_plan(["005930"], ["market"]),
        )

        self.assertTrue(selection["applied"])
        self.assertEqual("applied", selection["factSlot"]["status"])
        self.assertTrue(any(":market" in scope_id for scope_id in selection["selectedIncomingScopeIds"]))
        self.assertFalse(any(":evidence" in scope_id for scope_id in selection["selectedIncomingScopeIds"]))
        self.assertTrue(any(":evidence" in scope_id for scope_id in selection["deferredScopeIds"]))


if __name__ == "__main__":
    unittest.main()
