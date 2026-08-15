import hashlib
import json
import unittest

from digital_twin.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.domain.ontology_native_rule_planning import (
    NATIVE_RULE_PLANNER_RELATION_PROPERTY_FIELDS,
    NATIVE_RULE_PLANNER_RELATION_TARGET_PROPERTY_FIELDS,
    NATIVE_RULE_PLANNER_TOPOLOGY_VERSION,
    merge_native_rule_planner_topology,
    native_rule_planner_manifest_fingerprint,
    native_rule_planner_topology,
    normalize_native_rule_planner_topology,
)
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules


class NativeRulePlannerTopologyTests(unittest.TestCase):
    def sample_graph(self):
        graph = PortfolioOntology("planner-topology")
        graph.entities.extend([
            OntologyEntity("portfolio:default", "Portfolio", "portfolio", {}),
            OntologyEntity("stock:005930", "Samsung", "stock", {
                "symbol": "005930", "source": "holding", "profitLossRate": -4.2,
            }),
            OntologyEntity("stock:000660", "SK hynix", "stock", {
                "symbol": "000660", "source": "watchlist", "ma20Distance": 3.5,
            }),
            OntologyEntity("price:005930", "Price", "price-metric", {
                "symbol": "005930", "value": 3.2, "materialityPassed": True,
            }),
            OntologyEntity("macro:kr", "Macro", "macro-regime", {
                "dataScope": "macro", "dataState": "fresh",
            }),
        ])
        graph.relations.extend([
            OntologyRelation("portfolio:default", "macro:kr", "HAS_RISK_SNAPSHOT"),
            OntologyRelation(
                "stock:005930", "price:005930", "HAS_PRICE",
                properties={"evidenceRole": "support"},
            ),
            OntologyRelation("macro:kr", "stock:005930", "HAS_MACRO_REGIME"),
            OntologyRelation("stock:000660", "macro:kr", "HAS_RATE_SENSITIVITY"),
        ])
        return graph

    def test_topology_contains_rule_subjects_and_incident_relation_types(self):
        topology = native_rule_planner_topology(self.sample_graph())

        self.assertEqual(NATIVE_RULE_PLANNER_TOPOLOGY_VERSION, topology["version"])
        self.assertEqual(["stock:005930"], topology["sourceIdsBySymbol"]["005930"])
        self.assertEqual(
            ["HAS_MACRO_REGIME", "HAS_PRICE"],
            topology["relationTypesBySymbol"]["005930"],
        )
        self.assertEqual(["HAS_RATE_SENSITIVITY"], topology["relationTypesBySymbol"]["000660"])
        self.assertEqual("holding", topology["subjectPropertiesBySymbol"]["005930"]["source"])
        self.assertEqual(-4.2, topology["subjectPropertiesBySymbol"]["005930"]["profitLossRate"])
        evidence = topology["relationEvidenceBySymbol"]["005930"]
        price_evidence = next(item for item in evidence if item["relationType"] == "HAS_PRICE")
        macro_evidence = next(item for item in evidence if item["relationType"] == "HAS_MACRO_REGIME")
        self.assertEqual("out", price_evidence["direction"])
        self.assertEqual("price-metric", price_evidence["targetKind"])
        self.assertEqual(3.2, price_evidence["targetProperties"]["value"])
        self.assertEqual("support", price_evidence["relationProperties"]["evidenceRole"])
        self.assertEqual("in", macro_evidence["direction"])
        self.assertTrue(topology["relationEvidenceCompleteBySymbol"]["005930"])
        self.assertEqual(["portfolio:default"], topology["sourceIdsBySymbol"]["PORTFOLIO:DEFAULT"])
        self.assertEqual(["HAS_RISK_SNAPSHOT"], topology["relationTypesBySymbol"]["PORTFOLIO:DEFAULT"])

    def test_normalized_topology_requires_a_matching_fingerprint_and_can_select_targets(self):
        topology = native_rule_planner_topology(self.sample_graph())

        normalized = normalize_native_rule_planner_topology(topology, target_symbols=["000660"])

        self.assertEqual("ok", normalized["status"])
        self.assertEqual(["000660"], normalized["symbols"])
        self.assertEqual({"000660"}, set(normalized["relationTypesBySymbol"]))
        self.assertTrue(normalized["relationEvidenceIndexAvailable"])
        self.assertEqual({"000660"}, set(normalized["relationEvidenceBySymbol"]))

        topology["fingerprint"] = "native-rule-topology:incorrect"
        invalid = normalize_native_rule_planner_topology(topology)

        self.assertEqual("invalid", invalid["status"])
        self.assertIn("fingerprint", invalid["reason"])

    def test_legacy_topology_without_subject_properties_remains_valid_and_unknown(self):
        topology = native_rule_planner_topology(self.sample_graph())
        topology.pop("subjectPropertiesBySymbol")
        payload = {key: value for key, value in topology.items() if key != "fingerprint"}
        canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        topology["fingerprint"] = (
            "native-rule-topology:"
            + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        )

        normalized = normalize_native_rule_planner_topology(topology, ["005930"])

        self.assertEqual("ok", normalized["status"])
        self.assertFalse(normalized["subjectPropertyIndexAvailable"])
        self.assertEqual({}, normalized["subjectPropertiesBySymbol"])
        normalized_again = normalize_native_rule_planner_topology(
            normalize_native_rule_planner_topology(topology)
        )
        self.assertEqual("ok", normalized_again["status"])
        self.assertFalse(normalized_again["subjectPropertyIndexAvailable"])
        self.assertEqual(topology["fingerprint"], normalized_again["fingerprint"])

    def test_legacy_topology_without_relation_evidence_remains_unknown(self):
        topology = native_rule_planner_topology(self.sample_graph())
        topology.pop("relationEvidenceBySymbol")
        topology.pop("relationEvidenceCompleteBySymbol")
        payload = {key: value for key, value in topology.items() if key != "fingerprint"}
        canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        topology["fingerprint"] = (
            "native-rule-topology:"
            + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        )

        normalized = normalize_native_rule_planner_topology(topology, ["005930"])

        self.assertEqual("ok", normalized["status"])
        self.assertFalse(normalized["relationEvidenceIndexAvailable"])
        self.assertEqual({}, normalized["relationEvidenceBySymbol"])
        normalized_again = normalize_native_rule_planner_topology(
            normalize_native_rule_planner_topology(topology)
        )
        self.assertEqual("ok", normalized_again["status"])
        self.assertFalse(normalized_again["relationEvidenceIndexAvailable"])
        self.assertEqual(topology["fingerprint"], normalized_again["fingerprint"])

    def test_scoped_merge_preserves_complete_relation_evidence_for_other_symbols(self):
        active = native_rule_planner_topology(self.sample_graph())
        incoming_graph = self.sample_graph()
        incoming_graph.entities = [
            item for item in incoming_graph.entities
            if item.entity_id in {"stock:000660", "macro:kr"}
        ]
        incoming_graph.relations = [
            item for item in incoming_graph.relations
            if item.source == "stock:000660"
        ]
        incoming = native_rule_planner_topology(incoming_graph)

        merged = merge_native_rule_planner_topology(active, incoming, ["000660"])
        normalized = normalize_native_rule_planner_topology(merged["topology"])

        self.assertEqual("ok", merged["status"])
        self.assertTrue(normalized["relationEvidenceCompleteBySymbol"]["005930"])
        self.assertTrue(normalized["relationEvidenceCompleteBySymbol"]["000660"])
        self.assertTrue(normalized["relationEvidenceBySymbol"]["005930"])

    def test_manifest_fingerprint_changes_when_the_execution_topology_changes(self):
        graph = self.sample_graph()
        first = native_rule_planner_topology(graph)
        initial_fingerprint = native_rule_planner_manifest_fingerprint("facts", first)

        graph.relations.append(OntologyRelation("stock:005930", "macro:kr", "HAS_TRADE_FLOW"))
        changed = native_rule_planner_topology(graph)

        self.assertNotEqual(initial_fingerprint, native_rule_planner_manifest_fingerprint("facts", changed))

    def test_relation_evidence_fields_cover_active_rulebox_filters(self):
        rules = default_graph_inference_rules()
        target_fields = {
            (
                "value"
                if str(field).startswith("min") or str(field).startswith("max")
                else str(field)
            )
            for rule in rules
            for condition in rule.conditions
            for field in condition.target_property_filters
        }
        relation_fields = {
            str(field)
            for rule in rules
            for condition in rule.conditions
            for field in condition.relation_property_filters
        }

        self.assertEqual(set(), target_fields - NATIVE_RULE_PLANNER_RELATION_TARGET_PROPERTY_FIELDS)
        self.assertEqual(set(), relation_fields - NATIVE_RULE_PLANNER_RELATION_PROPERTY_FIELDS)


if __name__ == "__main__":
    unittest.main()
