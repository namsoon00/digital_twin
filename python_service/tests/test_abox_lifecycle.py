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
            },
            {
                "scopeId": self.link_scope,
                "scopeType": "link",
                "scopeFamily": "quality",
                "generationId": "quality-old",
                "baseFingerprint": "quality-old",
                "dependencyScopeIds": [self.evidence_scope],
                "relationCount": 2,
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


if __name__ == "__main__":
    unittest.main()
