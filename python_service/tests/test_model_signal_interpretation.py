import unittest

from digital_twin.domain.model_signal_interpretation import (
    is_batchable_model_signal_interpretation_rule,
    is_model_signal_interpretation_rule,
    model_signal_bridge_conditions,
    model_signal_bridge_groups,
    model_signal_bridge_manifest,
    model_signal_interpretation_execution_partition,
    model_signal_interpretation_contract_id,
    model_signal_interpretation_policies,
    model_signal_residual_conditions,
)
from digital_twin.domain.ontology_contracts import entity_id
from digital_twin.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.world_partitioned_reasoning import compile_world_partitioned_rules
from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules
from digital_twin.infrastructure.typedb_ontology import (
    typedb_native_matched_conditions,
    materialize_typedb_native_matches,
    typedb_dispatch_model_signal_bridge_rows,
    typedb_model_signal_bridge_batch_plan,
    typedb_model_signal_bridge_batch_query,
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

    def test_runtime_partition_batches_simple_policies_and_keeps_constraints(self):
        partition = model_signal_interpretation_execution_partition(
            self.rules,
            enabled_only=True,
        )

        self.assertEqual(74, partition["logicalModelSignalPolicyCount"])
        self.assertEqual(59, partition["batchedSimplePolicyCount"])
        self.assertEqual(15, partition["constrainedPolicyCount"])
        self.assertEqual(3, partition["modelSignalBridgeReadCount"])
        self.assertEqual(56, partition["eliminatedModelSignalPolicyQueryCount"])
        self.assertEqual({"stock", "holding", "watchlist"}, set(
            partition["bridgeSourceScopes"]
        ))
        self.assertTrue(all(
            is_batchable_model_signal_interpretation_rule(rule)
            for rule in self.model_rules
            if rule.enabled and rule.rule_id in partition["batchableRuleIds"]
        ))

    def test_shared_premise_compilation_preserves_model_signal_bridge_routing(self):
        compiled = compile_world_partitioned_rules(self.rules)
        shared_rules = list(compiled["sharedRules"])
        partition = model_signal_interpretation_execution_partition(
            shared_rules,
            enabled_only=True,
        )

        self.assertEqual("ready", compiled["status"])
        self.assertEqual(74, partition["logicalModelSignalPolicyCount"])
        self.assertEqual(74, partition["batchedSimplePolicyCount"])
        self.assertEqual(0, partition["constrainedPolicyCount"])
        self.assertEqual(1, partition["modelSignalBridgeReadCount"])
        self.assertEqual(73, partition["eliminatedModelSignalPolicyQueryCount"])
        self.assertEqual(["stock"], partition["bridgeSourceScopes"])

        plan = typedb_model_signal_bridge_batch_plan(
            [{
                "rule": rule,
                "candidateSymbols": ["005930"],
                "executionStage": "shared-premise",
            } for rule in shared_rules],
            ["005930"],
        )
        self.assertEqual(74, plan["logicalModelSignalPolicyCount"])
        self.assertEqual(1, plan["modelSignalBridgeReadCount"])
        self.assertEqual(73, plan["eliminatedModelSignalPolicyQueryCount"])

    def test_runtime_batch_plan_replaces_59_reads_with_three_bridge_reads(self):
        entries = [{
            "rule": rule,
            "candidateSymbols": ["005930"],
            "executionStage": "core",
        } for rule in self.active_rules]
        plan = typedb_model_signal_bridge_batch_plan(
            entries,
            ["005930"],
        )

        self.assertEqual(59, plan["batchedSimplePolicyCount"])
        self.assertEqual(15, plan["constrainedPolicyCount"])
        self.assertEqual(3, plan["modelSignalBridgeReadCount"])
        self.assertEqual(56, plan["eliminatedModelSignalPolicyQueryCount"])
        self.assertEqual(18, plan["plannedModelSignalQueryCount"])
        self.assertEqual(
            len(self.active_rules) - 59,
            len(plan["regularEntries"]),
        )
        self.assertTrue(all(
            "schemaFunctionQuery" not in batch for batch in plan["batches"]
        ))
        for batch in plan["batches"]:
            query = typedb_model_signal_bridge_batch_query(
                batch,
                world_id="portfolio:local:default",
            )
            self.assertEqual("ok", query["status"])
            self.assertIn("HAS_MODEL_SIGNAL", query["query"])
            self.assertIn("$hypothesisContractId", query["query"])
            self.assertIn("$signalEvidenceId", query["query"])

    def test_bridge_dispatch_is_exact_fail_closed_and_excludes_disabled_policy(self):
        batchable = next(
            rule for rule in self.model_rules
            if rule.enabled and is_batchable_model_signal_interpretation_rule(rule)
        )
        disabled = next(rule for rule in self.model_rules if not rule.enabled)
        plan = typedb_model_signal_bridge_batch_plan([{
            "rule": batchable,
            "candidateSymbols": ["005930"],
        }, {
            "rule": disabled,
            "candidateSymbols": ["005930"],
        }], ["005930"])
        batch = next(
            item for item in plan["batches"]
            if batchable.rule_id in item["ruleIds"]
        )
        signal = next(
            condition for condition in batchable.conditions
            if condition.relation_type == "HAS_MODEL_SIGNAL"
        )
        filters = dict(signal.target_property_filters or {})
        valid_row = {
            "sourceId": "stock:005930",
            "sourceLabel": "삼성전자",
            "sourceSymbol": "005930",
            "signalEvidenceId": "model-signal:test",
            **filters,
        }

        dispatched = typedb_dispatch_model_signal_bridge_rows(batch, [
            valid_row,
            {**valid_row, "hypothesisContractId": "unknown.contract.v1"},
        ])
        self.assertEqual("ok", dispatched["status"])
        self.assertEqual(1, len(dispatched["matches"]))
        self.assertEqual(
            batchable.rule_id,
            dispatched["matches"][0]["entry"]["rule"].rule_id,
        )
        self.assertEqual(["unknown.contract.v1"], dispatched["ignoredContractIds"])
        self.assertNotIn(disabled.rule_id, plan["batchableRuleIds"])

        invalid = typedb_dispatch_model_signal_bridge_rows(batch, [{
            **valid_row,
            "releaseId": "unexpected-release",
        }])
        self.assertEqual("invalid", invalid["status"])
        self.assertEqual([], invalid["matches"])
        self.assertIn("releaseId", invalid["failures"][0])

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
            "typeqlExecutionMode": "direct-typeql",
        }]})

        trace = next(item for item in graph.entities if item.kind == "inference-trace")
        self.assertTrue(trace.properties["modelSignalInterpretationPolicy"])
        self.assertTrue(trace.properties["sharedModelSignalBridge"])
        self.assertEqual("holding", trace.properties["bridgeSourceScope"])
        self.assertEqual(rule.rule_id, trace.properties["ruleId"])


if __name__ == "__main__":
    unittest.main()
