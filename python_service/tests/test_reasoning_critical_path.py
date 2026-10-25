"""Execution preparation must preserve exact routing, evidence and storage."""

from copy import deepcopy
import random
import unittest
from unittest.mock import patch

from digital_twin.modules.reasoning.domain import ontology_change_impact as impact
from digital_twin.modules.reasoning.domain import ontology_native_rule_planning as planning
from digital_twin.modules.reasoning.domain.dependency_index import StructuralDependencyIndex
from digital_twin.modules.reasoning.domain.ontology_contracts import (
    OntologyEntity, OntologyRelation, PortfolioOntology,
)
from digital_twin.infrastructure.typedb_ontology import TypeDBOntologyGraphRepository


class ReferenceDependencyIndex:
    """Independent exhaustive oracle for the pre-existing structural contract."""

    def __init__(self, keys):
        self.keys = frozenset(str(key or "").strip().lower() for key in keys or []
                              if str(key or "").strip())

    def related_keys(self, key):
        return {value for value in self.keys if impact._dependency_key_matches(value, key)}

    def covers(self, key):
        return bool(self.related_keys(key))

    def matching(self, keys):
        return {str(key or "").strip().lower() for key in keys or [] if self.covers(key)}

    def intersects(self, keys):
        return any(self.covers(key) for key in keys or [])


class ReasoningCriticalPathTests(unittest.TestCase):
    def test_index_preserves_ancestors_descendants_and_exact_segment_boundaries(self):
        keys = ["Price:current", "price::field", "price:", ":value", "기관:순매수", "x:y:z"]
        index = StructuralDependencyIndex(keys)
        self.assertTrue(index.covers(" PRICE "))
        self.assertTrue(index.covers("price:current:value"))
        self.assertFalse(index.covers("prices:current"))
        self.assertFalse(index.covers("price-current"))
        self.assertFalse(index.covers(None))
        self.assertEqual({"price:current", "price::field", "price:"}, index.related_keys("price"))
        keys.append("new:key")
        self.assertFalse(index.covers("new"))
        self.assertTrue(StructuralDependencyIndex(keys).covers("new"))

    def test_index_matches_exhaustive_oracle_for_unusual_and_overlapping_keys(self):
        rng = random.Random(77)
        parts = ["stock", "STOCK", "price", "price2", "field", "값", "", " ", "1", "_"]
        keys = [None, False, 0, 12, " "] + [
            ":".join(rng.choice(parts) for _ in range(rng.randint(1, 5))) for _ in range(300)
        ]
        for boundary in [keys[:10], keys[::2], keys, [], iter(keys)]:
            frozen = list(boundary)
            index, reference = StructuralDependencyIndex(frozen), ReferenceDependencyIndex(frozen)
            self.assertEqual(reference.matching(keys), index.matching(keys))
            for key in keys:
                self.assertEqual(reference.related_keys(key), index.related_keys(key), repr(key))
                self.assertEqual(reference.covers(key), index.covers(key), repr(key))

    def test_chain_routing_keeps_cycles_invalidation_and_all_downstream_consumers(self):
        profiles = [
            {"ruleId": "a", "derivedOutputDependencyKeys": ["relation:RECOVERY"],
             "conditionProfiles": [{"dependencyKeys": ["relation:EXIT:field:value"]}]},
            {"ruleId": "b", "derivedOutputDependencyKeys": ["relation:ENTRY"],
             "derivedConditionProfiles": [{"dependencyKeys": ["relation:RECOVERY:field:score"]}]},
            {"ruleId": "c", "derivedOutputDependencyKeys": ["relation:EXIT"],
             "conditionProfiles": [{"dependencyKeys": ["relation:ENTRY"], "canTriggerEvaluation": False}]},
            {"ruleId": "unrelated", "conditionProfiles": [{"dependencyKeys": ["relation:RECOVERY_OTHER"]}]},
        ]
        actual = impact.expand_rule_dependency_closure(profiles, ["a"])
        with patch.object(impact, "StructuralDependencyIndex", ReferenceDependencyIndex):
            expected = impact.expand_rule_dependency_closure(profiles, ["a"])
        self.assertEqual(expected, actual)
        self.assertEqual(["a", "b", "c"], actual["candidateRuleIds"])
        self.assertEqual({"a": ["b"], "b": ["c"], "c": ["a"]}, actual["dependencyEdges"])

    def test_per_symbol_and_shared_event_boundaries_match_exhaustive_routing(self):
        scopes = [f"symbol:{symbol}:market" for symbol in ("AAA", "BBB", "CCC")]
        scopes += ["macro:fx", "reference:calendar", "symbol:AAA:model-signal"]
        delta = {
            "directChangedScopeIds": scopes,
            "semanticChangedFamiliesByScope": {
                scope: ["model-signal"] if scope.endswith("model-signal") else ["market", "flow"]
                for scope in scopes
            },
            "semanticChangedDependencyKeysByScope": {
                scope: ["field:price", "kind:stock:field:volume", "relation:HAS_FLOW:field:value"]
                for scope in scopes
            },
            "sourceSymbolsByScope": {"reference:calendar": ["AAA", "BBB"]},
        }
        args = (delta, ["AAA", "BBB"], ["market"], {"AAA": ["market"], "BBB": ["flow"]},
                ["field:price"], {"AAA": ["kind:stock"], "BBB": ["relation:HAS_FLOW"]})
        for authoritative in (False, True):
            actual = impact.event_scoped_routing_inputs(*args, authoritative)
            with patch.object(impact, "StructuralDependencyIndex", ReferenceDependencyIndex):
                expected = impact.event_scoped_routing_inputs(*args, authoritative)
            self.assertEqual(expected, actual)
        changed = deepcopy(delta)
        changed["semanticChangedDependencyKeysByScope"][scopes[0]] = ["new:dependency"]
        self.assertNotEqual(impact.event_scoped_routing_inputs(*args, True),
                            impact.event_scoped_routing_inputs(changed, *args[1:], True))

    def test_dependency_closure_matches_all_pairs_reference_for_generated_catalogs(self):
        rng = random.Random(89)
        vocabulary = ["relation:r" + str(i) + suffix for i in range(12)
                      for suffix in ("", ":score", ":score:value", "_other")]
        for _ in range(8):
            profiles = [{
                "ruleId": "rule:" + str(i),
                "derivedOutputDependencyKeys": rng.sample(vocabulary, rng.randrange(4)),
                "conditionProfiles": [{"dependencyKeys": rng.sample(vocabulary, rng.randrange(4))}],
                "derivedConditionProfiles": [{"dependencyKeys": rng.sample(vocabulary, rng.randrange(3))}],
            } for i in range(25)]
            # This intentionally retains the original all-pairs algorithm,
            # independent of the candidate's inverse index and traversal.
            edges = {}
            for producer in profiles:
                targets = []
                for consumer in profiles:
                    if producer["ruleId"] == consumer["ruleId"]:
                        continue
                    inputs = [key for condition in consumer["conditionProfiles"]
                              + consumer["derivedConditionProfiles"] for key in condition["dependencyKeys"]]
                    if any(impact._dependency_key_matches(output, key)
                           for output in producer["derivedOutputDependencyKeys"] for key in inputs):
                        targets.append(consumer["ruleId"])
                if targets:
                    edges[producer["ruleId"]] = sorted(targets)
            expected = {profiles[0]["ruleId"]}
            for _ in profiles:
                expected |= {child for parent in list(expected) for child in edges.get(parent, [])}
            result = impact.expand_rule_dependency_closure(profiles, [profiles[0]["ruleId"]])
            self.assertEqual(edges, result["dependencyEdges"])
            self.assertEqual([p["ruleId"] for p in profiles if p["ruleId"] in expected],
                             result["candidateRuleIds"])

    def test_sparse_evidence_keeps_nested_overrides_and_rechecks_changed_vocabulary(self):
        values = {"score": 1, "unused": "ignore", "flag": False,
                  "properties": {"score": 2, "missing": None, "text": "ok", "array": [1, None, "x"]}}
        original = deepcopy(values)
        allowed = {"score", "flag", "missing", "text", "array", "never"}
        self.assertEqual({"array": [1, "x"], "flag": False, "score": 2, "text": "ok"},
                         planning._planner_filtered_properties(values, allowed))
        self.assertEqual(original, values)
        allowed.remove("score")
        values["properties"]["text"] = "changed"
        self.assertEqual({"array": [1, "x"], "flag": False, "text": "changed"},
                         planning._planner_filtered_properties(values, allowed))

    def test_persistence_preparation_keeps_external_endpoints_without_inserting_them(self):
        repo = TypeDBOntologyGraphRepository("")
        graph = PortfolioOntology("fixture", entities=[
            OntologyEntity("source", "Source", "stock", {"ontologyBox": "ABox", "symbol": "AAA"}),
            OntologyEntity("external", "External", "concept", {"ontologyBox": "TBox"}),
        ], relations=[OntologyRelation("source", "external", "INSTANCE_OF"),
                       OntologyRelation("source", "missing", "INSTANCE_OF")])
        with patch.object(repo, "external_relation_endpoint_ids", return_value={"external"}):
            expected_nodes = repo.node_rows(graph)
            with patch.object(repo, "node_rows", wraps=repo.node_rows) as prepare:
                nodes, relations = repo.graph_persistence_rows(graph)
            self.assertEqual(1, prepare.call_count)
            self.assertEqual(expected_nodes, nodes)
            self.assertEqual(["external"], [row["target"] for row in relations])
            self.assertTrue(relations[0]["targetStorageId"])


if __name__ == "__main__":
    unittest.main()
