import unittest

from digital_twin.domain.model_signal_interpretation import (
    is_model_signal_interpretation_rule,
    model_signal_bridge_conditions,
    model_signal_bridge_groups,
    model_signal_bridge_manifest,
    model_signal_interpretation_policies,
    model_signal_residual_conditions,
)
from digital_twin.domain.ontology_contracts import entity_id
from digital_twin.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules
from digital_twin.infrastructure.typedb_ontology import (
    deduplicate_typedb_schema_function_definitions,
    typedb_native_function_call_query,
    typedb_native_function_definition,
    typedb_native_matched_conditions,
    typedb_schema_function_rule_ids,
    materialize_typedb_native_matches,
)


class ModelSignalInterpretationTests(unittest.TestCase):
    def setUp(self):
        self.rules = default_graph_inference_rules()
        self.model_rules = [
            rule for rule in self.rules
            if is_model_signal_interpretation_rule(rule)
        ]
        self.active_rules = [rule for rule in self.rules if rule.enabled]

    def test_every_model_signal_rule_has_one_policy_and_one_of_three_bridges(self):
        manifest = model_signal_bridge_manifest(self.rules)
        policies = model_signal_interpretation_policies(self.rules)
        groups = model_signal_bridge_groups(self.rules, enabled_only=True)

        self.assertEqual("ok", manifest["status"])
        self.assertEqual(75, len(policies))
        self.assertEqual(74, manifest["activePolicyCount"])
        self.assertEqual(3, manifest["bridgeFunctionCount"])
        self.assertEqual(71, manifest["eliminatedPerRuleFunctionCount"])
        self.assertEqual({"stock", "holding", "watchlist"}, {
            group.source_scope for group in groups
        })
        self.assertEqual(
            {rule.rule_id for rule in self.model_rules},
            {policy.legacy_rule_id for policy in policies},
        )

    def test_bridge_removes_only_shared_source_context(self):
        for rule in self.model_rules:
            bridge = list(model_signal_bridge_conditions(rule))
            residual = list(model_signal_residual_conditions(rule))

            self.assertEqual(len(rule.conditions), len(bridge) + len(residual), rule.rule_id)
            self.assertTrue(any(
                str(getattr(condition, "relation_type", "") or "").upper()
                == "HAS_MODEL_SIGNAL"
                for condition in residual
            ), rule.rule_id)
            self.assertTrue(all(
                str(getattr(condition, "role", "required") or "required").lower()
                == "required"
                for condition in residual
            ), rule.rule_id)
            if bridge:
                self.assertEqual("source", bridge[0].field, rule.rule_id)
                self.assertIn(bridge[0].value, {"holding", "watchlist"}, rule.rule_id)

    def test_active_catalog_compiles_to_45_physical_functions(self):
        generated = []
        for rule in self.active_rules:
            definition = typedb_native_function_definition(
                rule.to_dict(),
                "portfolio:local:default",
            )
            generated.extend(definition.get("functionDefinitions") or [definition])
        unique = deduplicate_typedb_schema_function_definitions(generated)

        self.assertEqual(116, len(generated))
        self.assertEqual(45, len(unique))
        self.assertEqual(3, sum(
            bool(item.get("sharedModelSignalBridge")) for item in unique
        ))
        self.assertEqual(
            {rule.rule_id for rule in self.active_rules},
            set(typedb_schema_function_rule_ids(unique)),
        )

    def test_shared_bridge_keeps_exact_signal_contract_in_typedb_call(self):
        holding_rules = [
            rule for rule in self.model_rules
            if any(condition.field == "source" and condition.value == "holding" for condition in rule.conditions)
        ]
        first, second = holding_rules[:2]
        first_definition = typedb_native_function_definition(
            first.to_dict(), "portfolio:local:default"
        )
        second_definition = typedb_native_function_definition(
            second.to_dict(), "portfolio:local:default"
        )
        call = typedb_native_function_call_query(
            first.to_dict(), ["005930"], "portfolio:local:default"
        )

        self.assertEqual(first_definition["functionName"], second_definition["functionName"])
        self.assertNotIn("ontology-model-signal-type", first_definition["body"])
        self.assertIn("ontology-model-signal-type", call["query"])
        self.assertIn("ontology-hypothesis-contract-id", call["query"])
        self.assertIn(first.rule_id, call["query"])
        self.assertEqual(first.rule_id, call["ruleId"])
        self.assertTrue(call["sharedModelSignalBridge"])

        matched_conditions = typedb_native_matched_conditions(first, {}, call)
        self.assertEqual(len(first.conditions), len(matched_conditions))
        self.assertEqual(1, sum(
            bool(item.get("matchedBySharedModelSignalBridge"))
            for item in matched_conditions
        ))
        self.assertEqual(len(first.conditions) - 1, sum(
            bool(item.get("matchedByInterpretationPolicyQuery"))
            for item in matched_conditions
        ))
        self.assertTrue(all(item.get("matchedByTypeDB") for item in matched_conditions))

    def test_rulebox_projection_exposes_policies_bridges_and_stable_lineage(self):
        graph = rulebox_graph_from_rules(self.rules, include_tbox=False)
        policy_entities = [
            item for item in graph.entities
            if (item.properties or {}).get("tboxClass") == "ModelSignalInterpretationPolicy"
        ]
        bridge_entities = [item for item in graph.entities if item.kind == "model-signal-bridge"]

        self.assertEqual(75, len(policy_entities))
        self.assertEqual(3, len(bridge_entities))
        self.assertEqual(75, sum(
            item.relation_type == "APPLIES_SIGNAL_INTERPRETATION"
            for item in graph.relations
        ))
        sample = self.model_rules[0]
        projected = next(
            item for item in policy_entities
            if item.entity_id == entity_id("rule", sample.rule_id)
        )
        self.assertEqual(sample.rule_id, projected.properties["lineageRuleId"])
        self.assertEqual(
            len(sample.derivations),
            sum(
                relation.relation_type == "PRESERVES_RULE_LINEAGE"
                and relation.source == projected.entity_id
                for relation in graph.relations
            ),
        )

    def test_inference_trace_preserves_interpretation_policy_and_bridge_lineage(self):
        rule = next(
            item for item in self.model_rules
            if any(
                condition.field == "source" and condition.value == "holding"
                for condition in item.conditions
            )
        )
        graph = PortfolioOntology("model-signal-trace")
        stock = OntologyEntity(
            "stock:005930",
            "삼성전자",
            "stock",
            {"ontologyBox": "ABox", "symbol": "005930", "source": "holding"},
        )
        graph.entities.append(stock)
        materialize_typedb_native_matches(graph, [rule], {"matches": [{
            "ruleId": rule.rule_id,
            "sourceId": stock.entity_id,
            "modelSignalInterpretationPolicy": True,
            "modelSignalInterpretationPolicyId": "model-signal-interpretation:" + rule.rule_id,
            "sharedModelSignalBridge": True,
            "modelSignalBridgeVersion": "typedb-model-signal-bridge-v1",
            "bridgeSourceScope": "holding",
            "schemaFunctionName": "orbit_rule_holding_model_signal_bridge",
        }]})

        trace = next(item for item in graph.entities if item.kind == "inference-trace")
        self.assertTrue(trace.properties["modelSignalInterpretationPolicy"])
        self.assertTrue(trace.properties["sharedModelSignalBridge"])
        self.assertEqual("holding", trace.properties["bridgeSourceScope"])
        self.assertEqual(rule.rule_id, trace.properties["ruleId"])


if __name__ == "__main__":
    unittest.main()
