"""Native proof eligibility is independent of physical storage and row order."""

from copy import deepcopy
from itertools import permutations
import unittest

from digital_twin.modules.model_registry.contracts import GraphInferenceRule, GraphRuleCondition
from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_inference_materializer import (
    evidence_relation_index, grounded_inference_context,
)


AT = "2026-10-26T01:00:00Z"


def fixture():
    rule = GraphInferenceRule(rule_id="fixture.coverage", label="Coverage", version="1", source_kind="stock",
                             derivations=[], action_group="quality", action_level="reference", prompt_hint="", conditions=[
        GraphRuleCondition(condition_id="gap", description="Coverage gap", kind="relation", relation_type="HAS_COVERAGE_GAP",
                           target_kind="coverage-gap", target_property_filters={"dataScope": "crossListedSecurity"}),
    ])
    stock = OntologyEntity("stock:AAA", "AAA", "stock", {"symbol": "AAA"})
    targets = [OntologyEntity("gap:" + name, name, "coverage-gap", {
        "dataScope": "crossListedSecurity", "sourceAsOf": clock, "maxAgeMinutes": 10,
        "source": "security-line-ontology",
    }) for name, clock in [("adr", "2026-10-25T13:00:00Z"), ("etf", AT), ("other", AT)]]
    targets[-1].properties["dataScope"] = "other"
    relations = [OntologyRelation(stock.entity_id, target.entity_id, "HAS_COVERAGE_GAP",
                 properties={"_relationId": "physical:" + str(i)}) for i, target in enumerate(targets)]
    graph = PortfolioOntology("fixture", entities=[stock, *targets], relations=relations,
                              worldview={"evidenceEvaluationAsOf": AT})
    context = {"matchedConditions": [{"conditionId": "gap", "matchedByTypeDB": True}],
               "evidenceRelationIds": [r.properties["_relationId"] for r in relations]}
    return graph, rule, stock, context


class NativeEvidenceWitnessTests(unittest.TestCase):
    def grounded(self, graph, rule, stock, context, indexed=True):
        return grounded_inference_context(graph, rule, stock, context,
            evidence_index=evidence_relation_index(graph) if indexed else None)

    def test_exact_usable_witness_is_identical_across_world_hashes_and_row_order(self):
        graph, rule, stock, context = fixture()
        for world in ("baseline", "candidate"):
            for order in permutations(range(3)):
                for indexed in (True, False):
                    current = deepcopy(graph)
                    for i, relation in enumerate(current.relations):
                        relation.properties["_relationId"] = world + ":" + str(i if world == "baseline" else 3 - i)
                    proof = deepcopy(context)
                    proof["evidenceRelationIds"] = [r.properties["_relationId"] for r in current.relations]
                    current.relations = [current.relations[i] for i in order]
                    result = self.grounded(current, rule, stock, proof, indexed)
                    self.assertTrue(result["evidenceUsableForJudgement"])
                    condition = result["matchedConditions"][0]
                    self.assertEqual("gap:etf", condition["targetId"])
                    self.assertEqual(AT, condition["observedAt"])
                    self.assertEqual("fresh", condition["freshnessStatus"])
                    self.assertIn(condition["relationId"], proof["evidenceRelationIds"])

    def test_exact_native_witness_cannot_be_replaced_by_an_unmatched_fresh_relation(self):
        graph, rule, stock, context = fixture()
        context["evidenceRelationIds"] = ["physical:0"]
        result = self.grounded(graph, rule, stock, context)
        self.assertFalse(result["evidenceUsableForJudgement"])
        self.assertEqual("gap:adr", result["matchedConditions"][0]["targetId"])

    def test_unusable_witnesses_never_gain_eligibility_or_new_source_clocks(self):
        graph, rule, stock, context = fixture()
        for override in ({"freshnessStatus": "stale"}, {"sourceAsOf": ""},
                         {"sourceAsOf": "2026-10-26T01:01:00Z", "freshnessStatus": "fresh"},
                         {"judgementEvidenceUsable": False}):
            with self.subTest(override=override):
                current = deepcopy(graph)
                current.entities[2].properties.update(override)
                result = self.grounded(current, rule, stock, context)
                self.assertFalse(result["evidenceUsableForJudgement"])
                self.assertEqual("2026-10-25T13:00:00Z", result["matchedConditions"][0]["observedAt"])
        graph.worldview["evidenceEvaluationAsOf"] = "2026-10-26T01:11:00Z"
        self.assertFalse(self.grounded(graph, rule, stock, context)["evidenceUsableForJudgement"])

    def test_equally_usable_witnesses_use_logical_identity_and_keep_proof_input_immutable(self):
        graph, rule, stock, context = fixture()
        graph.entities[1].properties["sourceAsOf"] = AT
        before_graph, before_context = deepcopy(graph), deepcopy(context)
        for indexed in (False, True):
            result = self.grounded(graph, rule, stock, context, indexed)
            self.assertEqual("gap:adr", result["matchedConditions"][0]["targetId"])
            self.assertTrue(result["evidenceUsableForJudgement"])
        self.assertEqual(before_graph, graph)
        self.assertEqual(before_context, context)


if __name__ == "__main__":
    unittest.main()
