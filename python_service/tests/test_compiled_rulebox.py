"""Behavior contracts for portable RuleBox releases; no operational storage."""

from copy import deepcopy
import unittest

from digital_twin.modules.reasoning.domain.compiled_rulebox import (
    CompiledRuleboxEvaluator, RuleboxCompilationError, RuleboxExecutionLimit,
    compile_rulebox, value_matches,
)
from digital_twin.modules.reasoning.domain.ontology_contracts import (
    OntologyEntity, OntologyRelation, PortfolioOntology,
)


def rule(rid="risk", conditions=None, output="HAS_TEST_RISK"):
    return {"rule_id": rid, "source_kind": "stock", "label": rid, "version": "test-v1",
            "conditions": conditions or [{"condition_id": "loss", "kind": "subject_property",
                "field": "profitLossRate", "operator": "<=", "value": {"field": "lossLimit", "default": -8}}],
            "derivations": [{"relation_type": output, "target_kind": "risk", "target_key": "{symbol}:" + rid,
                "target_label": rid, "tbox_class": "RiskSignal", "polarity": "risk"}]}


def graph(pnl=-9, **props):
    return PortfolioOntology("account:test", entities=[OntologyEntity("stock:A", "A", "stock",
        {"symbol": "A", "profitLossRate": pnl, "ontologyBox": "ABox", **props})])


def evaluate(rows, facts, **kwargs):
    return CompiledRuleboxEvaluator(compile_rulebox(rows)).evaluate(facts, **kwargs)


class CompiledRuleboxTests(unittest.TestCase):
    def test_current_catalog_is_fully_compilable_without_skipping_rules(self):
        from digital_twin.modules.model_registry.contracts import default_graph_inference_rules
        rules = default_graph_inference_rules()
        p = compile_rulebox(rules)
        self.assertEqual({r.rule_id for r in rules if r.enabled}, set(p.rule_order))
        self.assertEqual(len(rules), len(p.rules()))

    def test_account_threshold_and_default_are_distinct(self):
        self.assertEqual(1, len(evaluate([rule()], graph(-9))["matches"]))
        self.assertFalse(evaluate([rule()], graph(-9, lossLimit=-12))["matches"])
        self.assertFalse(evaluate([rule()], graph(-9, lossLimit=None))["matches"])
        self.assertTrue(evaluate([rule()], graph(-9, lossLimit=0))["matches"])

    def test_missing_nan_and_infinite_measurements_never_become_zero(self):
        for value in (None, "", float("nan"), float("inf"), "invalid"):
            self.assertFalse(evaluate([rule()], graph(value), materialize=False)["matches"])

    def test_rule_revision_and_deletion_do_not_reuse_previous_match(self):
        original = rule()
        p = compile_rulebox([original])
        facts = graph()
        before = deepcopy(facts)
        self.assertTrue(CompiledRuleboxEvaluator(p).evaluate(facts)["matches"])
        changed = deepcopy(original)
        changed["conditions"][0]["value"] = -20
        other = compile_rulebox([changed])
        self.assertNotEqual(p.fingerprint, other.fingerprint)
        self.assertFalse(CompiledRuleboxEvaluator(other).evaluate(facts)["matches"])
        changed["enabled"] = False
        self.assertFalse(evaluate([changed], facts)["matches"])
        self.assertEqual(before, facts)
        # Mutating the authoring object after compilation cannot mutate a run.
        original["conditions"][0]["value"] = -100
        self.assertTrue(CompiledRuleboxEvaluator(p).evaluate(facts)["matches"])

    def test_alternatives_count_distinct_evidence_groups(self):
        conditions = [{"condition_id": key, "kind": "subject_property", "field": "profitLossRate",
                       "operator": "<", "value": 0, "role": "any", "evidence_group_key": group}
                      for key, group in (("a", "price"), ("b", "price"), ("c", "volume"))]
        conditions[2]["field"] = "volumeRatio"
        conditions[2].update(operator=">", value=2)
        r = rule(conditions=conditions)
        r["any_condition_min_count"] = 2
        self.assertFalse(evaluate([r], graph(volumeRatio=1))["matches"])
        result = evaluate([r], graph(volumeRatio=3))
        self.assertEqual(1, len(result["matches"]))
        self.assertEqual(4, len(result["matches"][0]["matchedConditions"]))

    def test_relation_direction_scope_negation_and_exact_evidence(self):
        c = {"condition_id": "flow", "kind": "relation", "relation_type": "HAS_FLOW", "direction": "in",
             "target_kind": "flow", "target_property_filters": {"minValue": 10}, "relation_property_filters": {"evidenceRole": "support"}}
        facts = graph()
        facts.entities += [OntologyEntity("flow:a", "flow", "flow", {"value": 20}), OntologyEntity("stock:B", "B", "stock", {"symbol": "B"})]
        facts.relations += [OntologyRelation("flow:a", "stock:B", "HAS_FLOW", properties={"evidenceRole": "support"})]
        self.assertFalse(evaluate([rule(conditions=[c])], facts, symbols=["A"])["matches"])
        facts.relations.append(OntologyRelation("flow:a", "stock:A", "HAS_FLOW", properties={"evidenceRole": "support"}))
        result = evaluate([rule(conditions=[c])], facts, symbols=["A"])
        self.assertEqual("stock:A", result["matches"][0]["sourceId"])
        self.assertEqual(1, len(result["matches"][0]["evidenceRelationIds"]))
        c["role"] = "not"
        self.assertFalse(evaluate([rule(conditions=[c])], facts, symbols=["A"])["matches"])

    def test_new_fields_and_ordered_rule_dependencies(self):
        first = rule("first", [{"condition_id": "new", "kind": "subject_property", "field": "newRatio", "value": 7}], "HAS_FIRST")
        second = rule("second", [{"condition_id": "linked", "kind": "relation", "relation_type": "HAS_FIRST", "target_kind": "risk"}])
        result = evaluate([second, first], graph(newRatio=7))
        self.assertEqual(["first", "second"], [m["ruleId"] for m in result["matches"]])
        self.assertFalse(evaluate([second, first], graph(newRatio=8))["matches"])
        readonly = evaluate([second, first], graph(newRatio=7), materialize=False)
        self.assertEqual(["first", "second"], [m["ruleId"] for m in readonly["matches"]])

    def test_absent_alternatives_never_pass_and_legacy_optional_is_an_alternative(self):
        from digital_twin.modules.reasoning.infrastructure.typeql import typedb_native_match_query
        r = rule(conditions=[{"condition_id":"flow", "kind":"relation", "role":"any",
                             "relation_type":"HAS_FLOW", "target_kind":"flow"}])
        self.assertFalse(evaluate([r], graph())["matches"])
        query = typedb_native_match_query(r, active_source_storage_ids=["stock:A"],
                                         active_relation_storage_ids_by_type={})
        self.assertFalse(query["query"])
        self.assertIn("no relation alternatives", query["reason"])
        r["conditions"][0] = {"condition_id":"optional", "kind":"subject_property", "role":"optional",
                              "field":"profitLossRate", "operator":"<", "value":0}
        self.assertFalse(evaluate([r], graph(1))["matches"])
        self.assertTrue(evaluate([r], graph(-1))["matches"])

    def test_explicit_news_attributes_can_be_matched_without_physical_column_mapping(self):
        r=rule(conditions=[{"condition_id":"news", "kind":"relation", "relation_type":"HAS_NEWS",
                "target_kind":"research-evidence", "target_property_filters":{
                    "materialityPassed":True,"materialityState":["material","notable"],"polarity":"context"}}])
        facts=graph()
        facts.entities.append(OntologyEntity("news:1","news","research-evidence",{
            "materialityPassed":True,"materialityState":"material","polarity":"context"}))
        facts.relations.append(OntologyRelation("stock:A","news:1","HAS_NEWS"))
        self.assertTrue(evaluate([r],facts)["matches"])
        facts.entities[-1].properties["materialityPassed"]=False
        self.assertFalse(evaluate([r],facts)["matches"])

    def test_unknown_semantics_and_cycles_fail_whole_release(self):
        for key, value in (("kind", "script"), ("role", "maybe"), ("operator", "eval")):
            invalid = rule()
            invalid["conditions"][0][key] = value
            with self.assertRaises(RuleboxCompilationError):
                compile_rulebox([rule("valid"), invalid])
        invalid = rule(conditions=[{"condition_id":"bad", "kind":"relation", "relation_type":"HAS_FLOW",
            "target_property_filters":{"ratio":{"op":">", "value":1}}}])
        with self.assertRaises(RuleboxCompilationError):
            compile_rulebox([invalid])
        invalid = rule(); invalid["enabled"] = "false"
        with self.assertRaises(RuleboxCompilationError):
            compile_rulebox([invalid])
        cyclic = rule(conditions=[{"condition_id": "cycle", "kind": "relation", "relation_type": "HAS_TEST_RISK"}])
        with self.assertRaisesRegex(RuleboxCompilationError, "cyclic"):
            compile_rulebox([cyclic])

    def test_budget_exhaustion_never_returns_partial_output(self):
        facts = graph()
        with self.assertRaises(RuleboxExecutionLimit):
            CompiledRuleboxEvaluator(compile_rulebox([rule()]), maximum_checks=0).evaluate(facts)
        self.assertEqual(1, len(facts.entities))

    def test_relation_endpoint_absence_is_an_input_error(self):
        facts = graph()
        facts.relations.append(OntologyRelation("stock:A", "missing", "HAS_FLOW"))
        with self.assertRaisesRegex(ValueError, "endpoint"):
            evaluate([rule()], facts)

    def test_boolean_and_list_values_preserve_rulebox_semantics(self):
        self.assertTrue(value_matches("true", "==", True))
        self.assertTrue(value_matches("regular", "==", ["regular", "extended"]))
        self.assertFalse(value_matches(None, "!=", "regular"))
        self.assertTrue(value_matches(0, "==", 0))


if __name__ == "__main__":
    unittest.main()
