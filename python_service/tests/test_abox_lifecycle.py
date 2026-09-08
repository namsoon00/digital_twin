import unittest

from digital_twin.domain.abox_lifecycle import (
    ABoxChangeSet,
    finalize_manifest_patch_plan,
)
from digital_twin.domain.ontology_contracts import (
    OntologyEntity,
    OntologyRelation,
    PortfolioOntology,
)
from digital_twin.domain.ontology_scopes import apply_scoped_abox_identity
from digital_twin.domain.ontology_scopes import (
    SCOPED_ABOX_MANIFEST_VERSION,
    SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
    plan_target_scoped_manifest_patch,
)


class ABoxLifecycleContractTests(unittest.TestCase):
    def setUp(self):
        self.evidence_scope = "symbol:035420:evidence:bucket:12"
        self.link_scope = "link:symbol:035420:quality:bucket:06"
        self.active = [
            {
                "scopeId": self.evidence_scope,
                "scopeType": "symbol",
                "scopeFamily": "evidence",
                "generationId": "evidence-old",
                "baseFingerprint": "evidence-old",
                "dependencyScopeIds": [],
                "entityCount": 2,
                "nodeInventoryVersion": "scope-node-inventory-v1",
                "nodeIds": ["research:035420:news:1"],
            },
            {
                "scopeId": self.link_scope,
                "scopeType": "link",
                "scopeFamily": "quality",
                "generationId": "quality-old",
                "baseFingerprint": "quality-old",
                "dependencyScopeIds": [self.evidence_scope],
                "relationCount": 2,
                "relationEndpointBindingVersion": "relation-endpoint-binding-v1",
                "relationEndpointNodeIdsByScope": {
                    self.evidence_scope: ["research:035420:news:1"],
                },
            },
        ]
        self.incoming = [
            {
                **self.active[0],
                "generationId": "evidence-new",
                "baseFingerprint": "evidence-new",
                "entityCount": 1,
            },
            {
                **self.active[1],
                "generationId": "quality-new",
                "baseFingerprint": "quality-new",
                "relationCount": 1,
                "relationLifecycle": "derived-companion",
                "deletionSemantics": "complete-source-assertion-presence",
            },
        ]
        self.change_set = ABoxChangeSet.from_inputs(
            ["035420"],
            fact_slot_plan={
                "eventBoundaryAuthoritative": True,
                "requestedFactFamilies": ["evidence"],
                "requestedFactFamiliesBySymbol": {"035420": ["evidence"]},
            },
            source_graph_complete=True,
        )

    def selection(self, selected):
        selected = list(selected)
        return {
            "status": "ready",
            "applied": True,
            "sourceGraphComplete": True,
            "selectedIncomingScopeIds": selected,
            "selectedIncomingScopePlan": [
                item for item in self.incoming if item["scopeId"] in selected
            ],
            "reusedActiveScopeIds": [
                item["scopeId"] for item in self.active if item["scopeId"] not in selected
            ],
            "deferredScopeIds": [
                item["scopeId"] for item in self.incoming if item["scopeId"] not in selected
            ],
            "retiredScopeIds": [],
            "replacementRootScopeIds": [self.evidence_scope],
            "replacementSymbols": ["035420"],
        }

    def test_complete_source_blocks_stale_derived_relation_reuse(self):
        result = finalize_manifest_patch_plan(
            self.selection([self.evidence_scope]),
            self.change_set,
            self.incoming,
            self.active,
        )

        self.assertEqual("blocked-invalid-manifest-patch-plan", result["status"])
        self.assertFalse(result["applied"])
        self.assertEqual(
            ["changed-derived-relation-not-replaced"],
            [item["code"] for item in result["patchPlanViolations"]],
        )

    def test_complete_source_accepts_explicit_derived_relation_replacement(self):
        result = finalize_manifest_patch_plan(
            self.selection([self.evidence_scope, self.link_scope]),
            self.change_set,
            self.incoming,
            self.active,
        )

        self.assertEqual("ready", result["status"])
        self.assertTrue(result["manifestPatchContract"]["validation"]["valid"])
        directive = next(
            item
            for item in result["manifestPatchContract"]["relationDirectives"]
            if item["scopeId"] == self.link_scope
        )
        self.assertEqual("replace", directive["disposition"])

        missing_endpoint_plan = [dict(item) for item in self.incoming]
        missing_endpoint_plan[0]["nodeIds"] = []
        blocked = finalize_manifest_patch_plan(
            self.selection([self.evidence_scope, self.link_scope]),
            self.change_set,
            missing_endpoint_plan,
            self.active,
        )
        self.assertEqual("blocked-invalid-manifest-patch-plan", blocked["status"])
        self.assertIn(
            "relation-endpoint-missing-from-final-scope",
            [item["code"] for item in blocked["patchPlanViolations"]],
        )
        self._assert_complete_source_selector_replaces_every_changed_derived_companion()
        self._assert_complete_source_retires_omitted_derived_quality_companion()

    def _assert_complete_source_selector_replaces_every_changed_derived_companion(self):
        graph = PortfolioOntology(
            "main",
            worldview={
                "scopePlan": self.incoming,
                "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
                "scopeTopologyVersion": SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
                "targetScopeRetentionMode": "incremental-target-patch",
            },
        )
        active = {
            "status": "ok",
            "scopePlan": self.active,
            "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            "scopeTopologyVersion": SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
        }
        result = plan_target_scoped_manifest_patch(
            graph,
            active,
            ["035420"],
            fact_slot_plan={
                "enabled": True,
                "eventBoundaryAuthoritative": True,
                "slotFamilies": ["evidence"],
                "slotFamiliesBySymbol": {"035420": ["evidence"]},
                "requestedFactFamilies": ["evidence"],
                "requestedFactFamiliesBySymbol": {"035420": ["evidence"]},
            },
            source_graph_complete=True,
        )

        self.assertEqual("ready", result["status"])
        self.assertTrue(result["applied"])
        self.assertIn(self.link_scope, result["selectedIncomingScopeIds"])
        self.assertEqual(
            [self.link_scope],
            result["factSlot"]["derivedCompanionReplacementScopeIds"],
        )
        self.assertTrue(result["manifestPatchContract"]["validation"]["valid"])

    def _assert_complete_source_retires_omitted_derived_quality_companion(self):
        market_scope = "symbol:MSTR:market:world:test"
        quality_link_scope = "link:symbol:MSTR:quality:bucket:05:world:test"
        active_market = {
            "scopeId": market_scope,
            "scopeType": "symbol",
            "scopeFamily": "market",
            "generationId": "market-old",
            "baseFingerprint": "market-old",
            "fingerprint": "market-old",
            "dependencyScopeIds": [],
            "entityCount": 2,
            "nodeInventoryVersion": "scope-node-inventory-v1",
            "nodeIds": [
                "stock:MSTR",
                "data-availability-assessment:MSTR:pricePath",
            ],
        }
        incoming_market = {
            **active_market,
            "generationId": "market-new",
            "baseFingerprint": "market-new",
            "fingerprint": "market-new",
            "entityCount": 1,
            "nodeIds": ["stock:MSTR"],
        }
        active_link = {
            "scopeId": quality_link_scope,
            "scopeType": "link",
            "scopeFamily": "quality",
            "generationId": "quality-old",
            "baseFingerprint": "quality-old",
            "fingerprint": "quality-old",
            "dependencyScopeIds": [market_scope],
            "relationCount": 1,
            "relationLifecycle": "derived-companion",
            "deletionSemantics": "complete-source-assertion-presence",
            "relationEndpointBindingVersion": "relation-endpoint-binding-v1",
            "relationEndpointNodeIdsByScope": {
                market_scope: [
                    "stock:MSTR",
                    "data-availability-assessment:MSTR:pricePath",
                ],
            },
        }
        graph = PortfolioOntology(
            "main",
            worldview={
                "scopePlan": [incoming_market],
                "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
                "scopeTopologyVersion": SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
                "targetScopeRetentionMode": "incremental-target-patch",
            },
        )

        result = plan_target_scoped_manifest_patch(
            graph,
            {
                "status": "ok",
                "scopePlan": [active_market, active_link],
                "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
                "scopeTopologyVersion": SCOPED_ABOX_SCOPE_TOPOLOGY_VERSION,
            },
            ["MSTR"],
            fact_slot_plan={
                "enabled": True,
                "eventBoundaryAuthoritative": True,
                "slotFamilies": ["market"],
                "slotFamiliesBySymbol": {"MSTR": ["market"]},
                "requestedFactFamilies": ["market"],
                "requestedFactFamiliesBySymbol": {"MSTR": ["market"]},
            },
            source_graph_complete=True,
        )

        self.assertEqual("ready", result["status"])
        self.assertIn(quality_link_scope, result["retiredScopeIds"])
        self.assertEqual(
            [quality_link_scope],
            result["removedDerivedCompanionScopeIds"],
        )
        self.assertTrue(result["manifestPatchContract"]["validation"]["valid"])

    def test_change_set_distinguishes_partial_source_from_deletion(self):
        change_set = ABoxChangeSet.from_inputs(
            ["035420"],
            fact_slot_plan={"eventBoundaryAuthoritative": True},
            source_graph_complete=False,
        )

        self.assertEqual("partial", change_set.to_dict()["sourceCompleteness"])
        self.assertFalse(change_set.to_dict()["sourceGraphComplete"])
        self.assertEqual([], change_set.to_dict()["explicitRemovedScopeIds"])

    def test_scope_author_declares_derived_relation_lifecycle(self):
        graph = PortfolioOntology(
            "main",
            entities=[
                OntologyEntity(
                    "stock:035420",
                    "NAVER",
                    "stock",
                    {"ontologyBox": "ABox", "symbol": "035420"},
                ),
                OntologyEntity(
                    "article-quality-risk:news-1",
                    "Article quality",
                    "article-quality-risk",
                    {"ontologyBox": "ABox", "symbol": "035420"},
                ),
            ],
            relations=[
                OntologyRelation(
                    "stock:035420",
                    "article-quality-risk:news-1",
                    "HAS_DATA_QUALITY",
                    properties={"ontologyBox": "ABox"},
                )
            ],
        )

        result = apply_scoped_abox_identity(graph)
        relation_scope = next(
            item for item in result["scopePlan"] if item["relationCount"] == 1
        )

        self.assertEqual("derived-companion", relation_scope["relationLifecycle"])
        self.assertEqual(
            relation_scope["scopeId"],
            relation_scope["lifecycleOwnerScopeId"],
        )
        self.assertEqual(
            "complete-source-assertion-presence",
            relation_scope["deletionSemantics"],
        )

        reference_graph = PortfolioOntology(
            "main",
            entities=[
                OntologyEntity(
                    "stock:035420",
                    "NAVER",
                    "stock",
                    {"ontologyBox": "ABox", "symbol": "035420"},
                ),
                OntologyEntity(
                    "news-event-type:general",
                    "general",
                    "news-event-type",
                    {
                        "ontologyBox": "ABox",
                        "symbol": "035420",
                        "materialityPassed": True,
                        "reviewLevel": "check",
                    },
                ),
            ],
            relations=[
                OntologyRelation(
                    "stock:035420",
                    "news-event-type:general",
                    "HAS_EVENT_TYPE",
                    properties={"ontologyBox": "ABox"},
                )
            ],
        )
        reference_result = apply_scoped_abox_identity(reference_graph)
        reference_entity = reference_graph.entities[1]
        reference_scope_id = reference_entity.properties["aboxScopeId"]
        self.assertTrue(reference_scope_id.startswith("reference:item:"))
        self.assertEqual("global", reference_entity.properties["referenceScope"])
        self.assertNotIn("symbol", reference_entity.properties)
        self.assertNotIn("materialityPassed", reference_entity.properties)
        reference_relation_scope = next(
            item
            for item in reference_result["scopePlan"]
            if item["relationCount"] == 1
        )
        self.assertIn(
            reference_scope_id,
            reference_relation_scope["dependencyScopeIds"],
        )
        self.assertEqual(
            ["news-event-type:general"],
            reference_relation_scope["relationEndpointNodeIdsByScope"][
                reference_scope_id
            ],
        )

        second_graph = PortfolioOntology(
            "main",
            entities=[
                OntologyEntity(
                    "stock:TSLA",
                    "Tesla",
                    "stock",
                    {"ontologyBox": "ABox", "symbol": "TSLA"},
                ),
                OntologyEntity(
                    "news-event-type:general",
                    "general",
                    "news-event-type",
                    {"ontologyBox": "ABox", "symbol": "TSLA"},
                ),
            ],
        )
        apply_scoped_abox_identity(second_graph)
        self.assertEqual(
            reference_scope_id,
            second_graph.entities[1].properties["aboxScopeId"],
        )


if __name__ == "__main__":
    unittest.main()
