from copy import deepcopy
import unittest

from digital_twin.domain.investment_brain import hypothesis_set_from_relation_context
from digital_twin.domain.decision_evidence_contract import hypothesis_set_evidence_summary
from digital_twin.domain.ontology_rule_knowledge import (
    knowledge_basis_violations,
    resolved_rule_knowledge_basis,
)
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_rulebox_contracts import GraphInferenceRule
from digital_twin.domain.ontology_rulebox_governance import rulebox_rules_hash, rulebox_rules_payload
from digital_twin.infrastructure.ontology_projection import migrate_typedb_rule_catalog


class OntologyRuleKnowledgeTests(unittest.TestCase):
    def test_every_default_rule_has_a_valid_auditable_knowledge_basis(self):
        rules = default_graph_inference_rules()

        self.assertGreater(len(rules), 100)
        self.assertFalse([
            issue
            for rule in rules
            for issue in knowledge_basis_violations(rule.resolved_knowledge_basis, rule.rule_id)
        ])
        self.assertTrue(all(rule.resolved_knowledge_basis.theory_family for rule in rules))
        self.assertTrue(all(rule.resolved_knowledge_basis.thesis_family for rule in rules))
        self.assertTrue(all(
            rule.resolved_knowledge_basis.requires_hypothesis
            == (rule.resolved_knowledge_basis.rule_kind == "predictive-hypothesis")
            for rule in rules
        ))

    def test_rule_knowledge_basis_round_trips_with_the_rulebox_contract(self):
        original = next(
            rule for rule in default_graph_inference_rules()
            if rule.resolved_knowledge_basis.rule_kind == "predictive-hypothesis"
        )

        restored = GraphInferenceRule.from_dict(original.to_dict())

        self.assertEqual(original.resolved_knowledge_basis, restored.resolved_knowledge_basis)
        self.assertEqual(
            "conditional",
            restored.resolved_knowledge_basis.decision_eligibility,
        )
        self.assertTrue(restored.resolved_knowledge_basis.outcome_validation_required)

    def test_matched_quality_rule_is_a_guardrail_not_a_competing_hypothesis(self):
        predictive_basis = resolved_rule_knowledge_basis({
            "ruleId": "graph.price.trend.test.v1",
            "sourceKind": "stock",
            "actionGroup": "watchlist",
        }).to_dict()
        quality_basis = resolved_rule_knowledge_basis({
            "ruleId": "graph.data_quality.test.v1",
            "sourceKind": "stock",
            "actionGroup": "dataQuality",
        }).to_dict()
        context = {
            "subject": {"symbol": "005930", "name": "삼성전자", "market": "KR"},
            "facts": {"symbol": "005930", "name": "삼성전자", "source": "watchlist"},
            "activeRules": [
                {"ruleId": "graph.price.trend.test.v1", "knowledgeBasis": predictive_basis},
                {"ruleId": "graph.data_quality.test.v1", "knowledgeBasis": quality_basis},
            ],
            "missingData": [],
            "signalConflicts": {"hasConflict": False},
            "inferenceGenerationId": "generation:test",
            "graphStoreInference": {
                "traces": [],
                "relations": [
                    {
                        "id": "relation:predictive",
                        "ruleId": "graph.price.trend.test.v1",
                        "type": "HAS_INFERRED_SUPPORT",
                        "polarity": "support",
                        "knowledgeBasis": predictive_basis,
                    },
                    {
                        "id": "relation:quality",
                        "ruleId": "graph.data_quality.test.v1",
                        "type": "HAS_DATA_QUALITY",
                        "polarity": "risk",
                        "blockedActions": ["BUY", "ADD"],
                        "knowledgeBasis": quality_basis,
                    },
                ],
            },
        }

        result = hypothesis_set_from_relation_context(deepcopy(context))["hypothesisSet"]

        hypothesis_rule_ids = {
            rule_id
            for hypothesis in result["hypotheses"]
            for rule_id in hypothesis.get("supportingRuleIds") or []
        }
        rule_guardrails = [
            item for item in result["decisionGuardrails"]
            if item.get("source") == "typedb-rulebox-guardrail"
        ]
        self.assertIn("graph.price.trend.test.v1", hypothesis_rule_ids)
        self.assertNotIn("graph.data_quality.test.v1", hypothesis_rule_ids)
        self.assertEqual(["graph.data_quality.test.v1"], rule_guardrails[0]["sourceRuleIds"])
        self.assertEqual("data-quality-gate", rule_guardrails[0]["guardrailType"])
        self.assertEqual(["BUY", "ADD"], rule_guardrails[0]["blockedActions"])

    def test_same_thesis_windows_count_as_one_independent_evidence_family(self):
        basis = resolved_rule_knowledge_basis({
            "ruleId": "graph.price.trend.test.v1",
            "sourceKind": "stock",
            "actionGroup": "watchlist",
        }).to_dict()
        hypotheses = []
        for index in range(2):
            hypotheses.append({
                "hypothesisId": "hypothesis:" + str(index),
                "familyId": "window-family:" + str(index),
                "supportingRuleIds": ["trend-window-" + str(index)],
                "evidenceState": "supported",
                "approvalStatus": "approved-active",
                "verificationStatus": "typedb-current-generation",
                "knowledgeBasis": basis,
            })

        summary = hypothesis_set_evidence_summary({"hypotheses": hypotheses})

        self.assertEqual(2, summary["eligibleHypothesisCount"])
        self.assertEqual(1, summary["eligibleFamilyCount"])

    def test_missing_basis_migration_preserves_the_executable_rule_contract(self):
        bootstrap = rulebox_rules_payload(default_graph_inference_rules())
        stored = deepcopy(bootstrap)
        for rule in stored:
            rule.pop("knowledge_basis", None)

        migration = migrate_typedb_rule_catalog(stored, bootstrap)
        executable_contract = deepcopy(migration["rules"])
        for rule in executable_contract:
            rule.pop("knowledge_basis", None)

        self.assertEqual(rulebox_rules_hash(stored), rulebox_rules_hash(executable_contract))
        self.assertEqual(len(stored), len(migration["knowledgeBasisUpdatedRuleIds"]))
        self.assertTrue(all(
            (rule.get("knowledge_basis") or {}).get("ruleKind")
            for rule in migration["rules"]
        ))


if __name__ == "__main__":
    unittest.main()
