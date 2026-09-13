import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import test_hypothesis_candidate_validation as validation_fixtures
from digital_twin.modules.model_registry.domain.hypothesis_authoring import authoring_catalog, assemble_hypothesis_design
from digital_twin.modules.model_registry.domain.hypothesis_compilation import authoring_input_fingerprint
from digital_twin.modules.model_registry.domain.hypothesis_recovery import REPAIR_FROM_VERSION, development_progress
from digital_twin.modules.model_registry.domain.ontology_evolution import comparison_measurement
from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.modules.model_registry.domain.ontology_rulebox_contracts import GraphInferenceRule
from digital_twin.modules.model_registry.domain.ontology_rulebox_governance import (
    build_rule_change_candidate_prompt, rule_change_candidates_from_text, rulebox_semantic_violations,
)


class RegisteredHypothesisAuthoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = [row.to_dict() for row in default_graph_inference_rules()]

    def context(self):
        context = {"ruleBox": {"status": "ok", "rules": copy.deepcopy(self.rules)},
                   "hypothesisProposal": {"caseId": "case:1", "claim": "공시 이벤트 충격 흡수",
                                          "validationRequirements": [{"check": "review", "requirement": "인과 효과 별도 검증"}]},
                   "inferenceBox": {"status": "deferred-validation"}}
        context["authoringContract"] = authoring_catalog(context)
        return context

    def candidate(self):
        return {"title": "공시 이후 충격 흡수 검토", "blockers": [], "hypothesisDesign": {
            "modelRuleId": "graph.temporal.risk_event_absorption.support.v1",
            "comparisonRuleId": "graph.disclosure.financing_or_dilution.risk.v1",
            "conditionRefs": [{"ruleId": "graph.disclosure.event_risk.v1", "conditionId": "symbol-disclosure-signal"}],
            "additionalObservations": [], "unverifiedClaims": [],
        }}

    def test_assembly_preserves_registered_measurement_actions_and_unique_claim_identity(self):
        context = self.context()
        original = copy.deepcopy(context)
        assembled = assemble_hypothesis_design(self.candidate(), context)
        rule = assembled["proposedRule"]
        baseline = next(row for row in self.rules if row["rule_id"] == self.candidate()["hypothesisDesign"]["modelRuleId"])
        self.assertEqual(original, context)
        self.assertFalse(rule["enabled"])
        self.assertEqual(baseline["conditions"], rule["conditions"][:-1])
        self.assertEqual(baseline["claim_contract"]["outcomeContract"], rule["claim_contract"]["outcomeContract"])
        self.assertEqual(baseline["claim_contract"]["qualificationPolicy"], rule["claim_contract"]["qualificationPolicy"])
        self.assertNotEqual(baseline["claim_contract"]["claimContractId"], rule["claim_contract"]["claimContractId"])
        self.assertEqual(baseline["derivations"][0]["candidate_action"], rule["derivations"][0]["candidate_action"])
        self.assertEqual([], rulebox_semantic_violations([GraphInferenceRule.from_dict({**rule, "enabled": True})]))
        self.assertIn({"check": "review", "requirement": "인과 효과 별도 검증", "dependencyKey": ""}, assembled["validationRequirements"])
        self.assertEqual(self.candidate()["hypothesisDesign"]["comparisonRuleId"], rule["model_input_contract"]["comparisonBaselineRuleId"])
        changed = next(row for row in context["ruleBox"]["rules"] if row["rule_id"] == self.candidate()["hypothesisDesign"]["comparisonRuleId"])
        changed["label"] += " revised definition"
        self.assertNotEqual(rule["rule_id"], assemble_hypothesis_design(self.candidate(), context)["proposedRule"]["rule_id"])

    def test_design_rejects_internal_fields_unknown_conditions_and_reserved_observations(self):
        for change in ("internal", "unknown", "duplicate", "override", "source", "cadence", "metric", "comparison"):
            with self.subTest(change=change):
                candidate = self.candidate()
                design = candidate["hypothesisDesign"]
                if change == "internal":
                    candidate["proposedRule"] = {"enabled": True}
                elif change == "unknown":
                    design["conditionRefs"][0]["conditionId"] = "invented-condition"
                elif change == "duplicate":
                    design["conditionRefs"] *= 2
                elif change == "override":
                    design["outcomeContract"] = {"threshold": 0}
                elif change == "comparison":
                    design["comparisonRuleId"] = "graph.benchmark.beta.context.v1"
                else:
                    design["additionalObservations"] = [{"metric": {"source": "source-packet", "cadence": "price", "metric": "invented"}[change],
                                                          "minimumSamples": 2, "cadenceSeconds": 60}]
                normalized = rule_change_candidates_from_text(json.dumps({"candidates": [candidate]}), self.context())[0]
                self.assertIsNone(normalized["proposedRule"])
                self.assertEqual("schema-mismatch", normalized["blockers"][0]["kind"])
                self.assertIn("hypothesisDesign", normalized)

    def test_authoring_prompt_exposes_selection_contract_and_retains_explicit_capability_blockers(self):
        context = self.context()
        prompt = build_rule_change_candidate_prompt(context)
        self.assertIn("comparisonRuleId", prompt)
        self.assertIn("systemOwned", prompt)
        self.assertNotIn('"proposedRule": {', prompt)
        row = rule_change_candidates_from_text(json.dumps({"candidates": [{
            "hypothesisDesign": None, "blockers": [{"kind": "unsupported-capability", "requirement": "새 인과 모델 필요"}]
        }]}), context)[0]
        self.assertEqual("unsupported-capability", row["blockers"][0]["kind"])
        self.assertIsNone(row["proposedRule"])

    def test_comparison_is_separate_from_model_and_preserves_exact_measurement_contract(self):
        context = self.context()
        rule = assemble_hypothesis_design(self.candidate(), context)["proposedRule"]
        compare = next(row for row in self.rules if row["rule_id"] == self.candidate()["hypothesisDesign"]["comparisonRuleId"])
        self.assertEqual(comparison_measurement(rule), comparison_measurement(compare))
        self.assertNotEqual(rule["claim_contract"]["expectedDirection"], compare["claim_contract"]["expectedDirection"])
        compare = copy.deepcopy(compare)
        compare["claim_contract"]["outcomeContract"]["criteria"][0]["threshold"] = 0.1
        self.assertNotEqual(comparison_measurement(rule), comparison_measurement(compare))

    def repair_service(self):
        service, store, advisor, repository, _ = validation_fixtures.HypothesisCandidateValidationTests().service()
        service.validate_rule_structure = Mock(return_value=["The source-packet identity requirement cannot be overridden"])
        service.process("case:1")
        case = store.get("case:1")
        case.compilation_draft["designVersion"] = REPAIR_FROM_VERSION
        case.compilation_draft["inputFingerprint"] = authoring_input_fingerprint(case, design_version=REPAIR_FROM_VERSION)
        case.retry["authoringAttempts"] = 3
        store.save(case)
        service.evolution_service = SimpleNamespace(policy={"mode": "automatic", "maximumAuthoringAttempts": 3, "retryMinutes": 60})
        return service, store, advisor, repository

    def test_explicit_contract_repair_preserves_history_budget_and_is_only_once(self):
        service, store, advisor, _ = self.repair_service()
        before = store.get("case:1")
        self.assertEqual("scheduled", service.schedule_contract_repair("case:1")["status"])
        scheduled = store.get("case:1")
        self.assertEqual(before.compilation_draft, scheduled.retry["contractRepair"]["previousDraft"])
        self.assertEqual(3, scheduled.retry["authoringAttempts"])
        service.process("case:1", force=False)
        after = store.get("case:1")
        self.assertEqual(4, after.retry["authoringAttempts"])
        self.assertEqual(1, after.retry["contractRepair"]["attemptsUsed"])
        self.assertEqual("repair-not-applicable", service.schedule_contract_repair("case:1")["status"])
        self.assertEqual("development-required", service.process("case:1")["status"])
        self.assertEqual(2, advisor.propose_hypothesis.call_count)
        self.assertEqual(after.blocked_reason, store.get("case:1").blocked_reason)

    def test_contract_repair_refuses_tampering_changed_evidence_world_and_frozen_plan(self):
        for change in ("tamper", "evidence", "world", "plan", "capability"):
            with self.subTest(change=change):
                service, store, advisor, _ = self.repair_service()
                case = store.get("case:1")
                if change == "tamper":
                    case.compilation_draft["contentFingerprint"] = "bad"
                elif change == "evidence":
                    case.supporting_evidence_ids.append("new-evidence")
                elif change == "world":
                    case.compilation_draft["world"]["worldId"] = "other-account"
                elif change == "plan":
                    case.evolution["plan"] = {"frozen": True}
                else:
                    case.retry["blockers"] = [{"kind": "unsupported-capability"}]
                store.save(case)
                if change in {"tamper", "evidence"}:
                    with self.assertRaises(ValueError):
                        service.schedule_contract_repair("case:1")
                else:
                    self.assertNotEqual("scheduled", service.schedule_contract_repair("case:1")["status"])
                self.assertEqual(3, store.get("case:1").retry["authoringAttempts"])
                self.assertEqual(1, advisor.propose_hypothesis.call_count)

    def test_unsupported_capability_does_not_retry_identical_authoring(self):
        service, store, advisor, _ = self.repair_service()
        case = store.get("case:1")
        case.retry.update({"authoringAttempts": 1, "blockers": [{"kind": "unsupported-capability", "requirement": "new model"}]})
        store.save(case)
        service.recover_authoring_backlog()
        saved = store.get("case:1")
        self.assertEqual("development-required", saved.retry["state"])
        self.assertEqual("", saved.retry["nextCheckAt"])
        self.assertEqual("capability-required", development_progress(saved)["phase"])
        self.assertEqual("development-required", service.process("case:1", force=False)["status"])
        self.assertEqual(1, advisor.propose_hypothesis.call_count)

    def test_progress_does_not_treat_plan_or_empty_condition_as_observed_performance(self):
        service, store, _, _ = self.repair_service()
        case = store.get("case:1")
        case.retry["state"] = "waiting-condition"
        self.assertEqual("condition-wait", development_progress(case)["phase"])
        case.evolution = {"plan": {"frozen": True}, "reason": "observation-future-collection"}
        progress = development_progress(case)
        self.assertFalse(progress["experimentStarted"])
        self.assertEqual("experiment", progress["phase"])
        case.evolution["deployment"] = {"deploymentId": "isolated"}
        for status in ("retired", "superseded", "rolled-back", "strengthened"):
            case.status = status
            progress = development_progress(case)
            self.assertEqual("completed", progress["phase"])
            self.assertNotIn("관측 중", progress["experimentStateLabel"])
        case.status = "evolution-monitoring"
        self.assertEqual("monitoring", development_progress(case)["phase"])
