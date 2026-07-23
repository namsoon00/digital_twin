import unittest

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_native_rule_planning import (
    NATIVE_RULE_PLANNER_TOPOLOGY_VERSION,
    native_rule_planner_manifest_fingerprint,
    native_rule_planner_topology,
    normalize_native_rule_planner_topology,
)


class NativeRulePlannerTopologyTests(unittest.TestCase):
    def sample_graph(self):
        graph = PortfolioOntology("planner-topology")
        graph.entities.extend([
            OntologyEntity("stock:005930", "Samsung", "stock", {"symbol": "005930"}),
            OntologyEntity("stock:000660", "SK hynix", "stock", {"symbol": "000660"}),
            OntologyEntity("price:005930", "Price", "price-metric", {"symbol": "005930"}),
            OntologyEntity("macro:kr", "Macro", "macro-regime", {}),
        ])
        graph.relations.extend([
            OntologyRelation("stock:005930", "price:005930", "HAS_PRICE"),
            OntologyRelation("macro:kr", "stock:005930", "HAS_MACRO_REGIME"),
            OntologyRelation("stock:000660", "macro:kr", "HAS_RATE_SENSITIVITY"),
        ])
        return graph

    def test_topology_contains_only_stock_subjects_and_incident_relation_types(self):
        topology = native_rule_planner_topology(self.sample_graph())

        self.assertEqual(NATIVE_RULE_PLANNER_TOPOLOGY_VERSION, topology["version"])
        self.assertEqual(["stock:005930"], topology["sourceIdsBySymbol"]["005930"])
        self.assertEqual(
            ["HAS_MACRO_REGIME", "HAS_PRICE"],
            topology["relationTypesBySymbol"]["005930"],
        )
        self.assertEqual(["HAS_RATE_SENSITIVITY"], topology["relationTypesBySymbol"]["000660"])

    def test_normalized_topology_requires_a_matching_fingerprint_and_can_select_targets(self):
        topology = native_rule_planner_topology(self.sample_graph())

        normalized = normalize_native_rule_planner_topology(topology, target_symbols=["000660"])

        self.assertEqual("ok", normalized["status"])
        self.assertEqual(["000660"], normalized["symbols"])
        self.assertEqual({"000660"}, set(normalized["relationTypesBySymbol"]))

        topology["fingerprint"] = "native-rule-topology:incorrect"
        invalid = normalize_native_rule_planner_topology(topology)

        self.assertEqual("invalid", invalid["status"])
        self.assertIn("fingerprint", invalid["reason"])

    def test_manifest_fingerprint_changes_when_the_execution_topology_changes(self):
        graph = self.sample_graph()
        first = native_rule_planner_topology(graph)
        initial_fingerprint = native_rule_planner_manifest_fingerprint("facts", first)

        graph.relations.append(OntologyRelation("stock:005930", "macro:kr", "HAS_TRADE_FLOW"))
        changed = native_rule_planner_topology(graph)

        self.assertNotEqual(initial_fingerprint, native_rule_planner_manifest_fingerprint("facts", changed))


if __name__ == "__main__":
    unittest.main()
