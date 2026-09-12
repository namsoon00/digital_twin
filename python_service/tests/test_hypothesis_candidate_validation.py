import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from test_hypothesis_closed_loop import CaseStore, hypothesis
from digital_twin.modules.model_registry.application.hypothesis_candidate_compilation import capture_compilation, reusable_compilation
from digital_twin.modules.model_registry.application.hypothesis_development_service import HypothesisDevelopmentService
from digital_twin.modules.model_registry.domain.hypothesis_compilation import compilation_blockers, validation_requirements
from digital_twin.modules.model_registry.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.modules.model_registry.domain.ontology_rulebox_governance import normalize_rule_change_candidate
from digital_twin.modules.model_registry.domain.hypothesis_validation import preview_states
from digital_twin.modules.reasoning.infrastructure.native_execution.validation import validate_rulebox_materialization
from digital_twin.infrastructure.typedb_ontology import materialization_preview_diff_payload


class HypothesisCandidateValidationTests(unittest.TestCase):
    def service(self, requirements=None, matched=0):
        case = hypothesis()
        case.created_at = "2026-08-01T00:00:00Z"
        store = CaseStore([case])
        rule = next(row for row in default_graph_inference_rules() if row.rule_id == "graph.temporal.risk_event_absorption.support.v1").to_dict()
        candidate = normalize_rule_change_candidate({"proposedRule": rule, "blockers": [],
                                                    "validationRequirements": requirements or []})
        rulebox = {"status": "ok", "rules": [], "rulesHash": "hash:1", "ruleboxSnapshotId": "seed:1"}
        result = {"candidates": [candidate], "contextSummary": {
            "ruleboxRulesHash": "hash:1", "ruleboxSnapshotId": "seed:1", "observationState": "not-queried",
        }}
        advisor = SimpleNamespace(propose_hypothesis=Mock(return_value=result))
        experiments = {}
        experiment_store = SimpleNamespace(get=lambda key: copy.deepcopy(experiments.get(key)),
                                           save=lambda experiment: experiments.update({experiment.experiment_id: copy.deepcopy(experiment)}))
        repository = SimpleNamespace(rulebox_snapshot=Mock(return_value=rulebox), validate_rulebox_materialization=Mock())
        service = HypothesisDevelopmentService(store, None, experiment_store, advisor, repository)
        service.history_for = lambda case: [{"generatedAt": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}] * 5
        preview = {"status": "ok", "matchedCount": matched, "validationOnly": True,
                   "mutatedOperationalRuleBox": False, "wroteInferenceBox": False,
                   "candidateRuleIds": [rule["rule_id"]], "targetSymbols": [case.symbol],
                   "worldId": service.world_context(case)["worldId"], "nativeTypeDbReasoningUsed": True,
                   "typedbDirectTypeqlUsed": True,
                   "nativeMatchResult": {"status": "ok", "executedRuleCount": 1, "skippedRuleCount": 0}}
        repository.validate_rulebox_materialization.return_value = preview
        return service, store, advisor, repository, experiments

    def test_post_authoring_requirements_do_not_become_compilation_blockers(self):
        candidate = normalize_rule_change_candidate({"blockers": [], "requiresData": [],
            "validationRequirements": [{"check": "current-match", "requirement": "Current TypeDB predicates"},
                                       {"check": "review", "requirement": "Five independent event outcomes"}]})
        self.assertEqual([], compilation_blockers([candidate]))
        self.assertEqual(2, len(candidate["validationRequirements"]))
        legacy = normalize_rule_change_candidate({"requiresData": ["ambiguous legacy requirement"], "blockers": []})
        self.assertEqual("unclassified", compilation_blockers([legacy])[0]["kind"])
        self.assertEqual("review", validation_requirements({"validationRequirements": ["human check"]})[0]["check"])
        with self.assertRaises(ValueError):
            validation_requirements({"validationRequirements": [{}]})

    def test_successful_empty_query_reaches_validation_and_is_not_missing_data(self):
        service, store, advisor, repository, experiments = self.service()
        result = service.process("case:1")
        self.assertEqual("needs-data", result["status"], result)
        case = store.get("case:1")
        self.assertEqual("validation", case.stage)
        self.assertEqual("waiting-condition", case.retry["state"])
        self.assertTrue(case.compilation_draft["candidates"][0]["proposedRule"])
        gates = {row["id"]: row for row in case.validation_gates}
        self.assertEqual("passed", gates["typedb-preview"]["status"])
        self.assertEqual("not-met", gates["current-replay"]["evidence"]["conditionState"])
        self.assertFalse(case.candidate_rule["enabled"])
        self.assertEqual(1, len(experiments))
        self.assertFalse(result["experiment"]["lastResult"]["sandbox"]["mutatedTypeDB"])
        advisor.propose_hypothesis.assert_called_once()
        repository.validate_rulebox_materialization.assert_called_once()
        self.assertIs(False, repository.validate_rulebox_materialization.call_args.args[0]["includeBaseline"])

    def test_type_db_failure_resumes_saved_candidate_without_another_ai_call(self):
        service, store, advisor, repository, _ = self.service()
        success = copy.deepcopy(repository.validate_rulebox_materialization.return_value)
        repository.validate_rulebox_materialization.return_value = {"status": "error", "reason": "test outage"}
        service.process("case:1")
        before = store.get("case:1")
        self.assertEqual("dependency-error", before.retry["state"])
        restarted = HypothesisDevelopmentService(store, None, service.experiment_store, advisor, repository)
        restarted.history_for = service.history_for
        repository.validate_rulebox_materialization.return_value = success
        restarted.process("case:1")
        after = store.get("case:1")
        self.assertEqual(before.compilation_draft, after.compilation_draft)
        self.assertEqual(before.experiment_id, after.experiment_id)
        self.assertEqual(before.retry["compilationContext"], after.retry["compilationContext"])
        advisor.propose_hypothesis.assert_called_once()
        self.assertEqual(2, repository.validate_rulebox_materialization.call_count)

    def test_draft_survives_failure_before_experiment_creation(self):
        service, store, advisor, _, _ = self.service()
        original = service.create_experiment
        service.create_experiment = Mock(side_effect=TimeoutError("experiment store unavailable"))
        service.process("case:1")
        self.assertTrue(store.get("case:1").compilation_draft)
        service.create_experiment = original
        service.process("case:1")
        self.assertEqual("validation", store.get("case:1").stage)
        advisor.propose_hypothesis.assert_called_once()

    def test_explicit_blocked_candidate_is_saved_but_does_not_enter_preview(self):
        service, store, advisor, repository, _ = self.service()
        advisor.propose_hypothesis.return_value["candidates"][0]["blockers"] = [
            {"kind": "unsupported-capability", "requirement": "new causal model not registered"}]
        service.process("case:1")
        case = store.get("case:1")
        self.assertEqual("needs-revision", case.status)
        self.assertTrue(case.compilation_draft["candidates"][0]["proposedRule"])
        self.assertFalse(case.candidate_rule)
        repository.validate_rulebox_materialization.assert_not_called()

    def test_changed_evidence_release_or_scope_invalidates_authoring_reuse(self):
        service, store, advisor, repository, _ = self.service()
        service.process("case:1")
        case = store.get("case:1")
        rulebox = repository.rulebox_snapshot.return_value
        world = service.world_context(case)
        self.assertTrue(reusable_compilation(case, rulebox, world))
        self.assertIsNone(reusable_compilation(case, {**rulebox, "rulesHash": "new-release"}, world))
        self.assertIsNone(reusable_compilation(case, rulebox, {**world, "accountId": "other"}))
        rejected = copy.deepcopy(case)
        rejected.compilation_draft["rejectedReason"] = "semantic contract invalid"
        self.assertIsNone(reusable_compilation(rejected, rulebox, world))
        case.supporting_evidence_ids.append("new-evidence")
        self.assertIsNone(reusable_compilation(case, rulebox, world))

    def test_tampered_draft_is_rejected_without_preview(self):
        service, store, _, repository, _ = self.service()
        service.process("case:1")
        case = store.get("case:1")
        case.compilation_draft["candidates"][0]["proposedRule"]["label"] = "tampered"
        store.save(case)
        result = service.process("case:1")
        self.assertEqual("error", result["status"])
        self.assertIn("fingerprint mismatch", store.get("case:1").blocked_reason)
        repository.validate_rulebox_materialization.assert_called_once()

    def test_new_candidate_content_gets_its_own_experiment(self):
        service, store, advisor, _, experiments = self.service()
        service.process("case:1")
        first = store.get("case:1")
        case = store.get("case:1")
        changed = copy.deepcopy(advisor.propose_hypothesis.return_value)
        changed["candidates"][0]["proposedRule"]["prompt_hint"] = "revised explanation"
        capture_compilation(case, changed, service.world_context(case))
        store.save(case)
        service.process("case:1")
        self.assertNotEqual(first.experiment_id, store.get("case:1").experiment_id)
        self.assertEqual(2, len(experiments))

    def test_old_proposal_observations_are_not_new_candidate_holdout(self):
        service, store, _, _, _ = self.service(matched=1)
        service.process("case:1")
        case = store.get("case:1")
        gate = next(row for row in case.validation_gates if row["id"] == "holdout-observation")
        self.assertEqual("needs-data", gate["status"])
        self.assertEqual(0, gate["evidence"]["snapshotCount"])
        self.assertEqual(case.compilation_draft["capturedAt"], gate["evidence"]["after"])
        self.assertNotEqual("approval-required", case.status)

    def test_real_projection_maps_and_legacy_lists_both_have_history_in_account_scope(self):
        service, store, _, _, _ = self.service()
        case = store.get("case:1")
        older = {"accountId": case.account_id, "generatedAt": "2026-09-12T10:00:00Z",
                 "positions": {case.symbol: {"current_price": 100}}}
        newer = {"accountId": case.account_id, "generatedAt": "2026-09-12T11:00:00Z",
                 "watchlist": [{"symbol": case.symbol, "currentPrice": 110}]}
        wrong_account = {**newer, "accountId": "other-account"}
        wrong_symbol = {**newer, "watchlist": {case.symbol: {"symbol": "OTHER"}}}
        original = copy.deepcopy([older, newer, wrong_account, wrong_symbol])
        service.monitor_store = SimpleNamespace(load_history=Mock(return_value=copy.deepcopy(original)))
        rows = HypothesisDevelopmentService.history_for(service, case)
        self.assertEqual([newer, older], rows)
        self.assertEqual(original, service.monitor_store.load_history.return_value)
        service.monitor_store.load_history.assert_called_once_with(case.account_id, limit=12)

    def test_latest_projection_fingerprint_observes_snake_case_price_fields(self):
        service, store, _, _, _ = self.service()
        case = store.get("case:1")
        case.candidate_rule = {"rule_id": "saved-candidate"}
        older = {"generatedAt": "2026-09-12T10:00:00Z", "positions": {case.symbol: {"current_price": 100}}}
        newer = {"generatedAt": "2026-09-12T11:00:00Z", "positions": {case.symbol: {"current_price": 110}}}
        baseline = service.validation_input_fingerprint(case, [older, newer])
        self.assertEqual(baseline, service.validation_input_fingerprint(case, [newer, older]))
        self.assertEqual(baseline, service.validation_input_fingerprint(case, [older, {
            **newer, "positions": [{"symbol": case.symbol, "currentPrice": 110}]}]))
        newer["positions"][case.symbol]["current_price"] = 120
        self.assertNotEqual(baseline, service.validation_input_fingerprint(case, [older, newer]))

    def test_snapshot_counts_and_current_match_do_not_prove_additional_causal_checks(self):
        service, store, _, _, _ = self.service([
            {"check": "review", "requirement": "5 independent events and research corroboration"}], matched=1)
        service.process("case:1")
        after = datetime.now(timezone.utc).isoformat()
        service.history_for = lambda case: [{"generatedAt": after}] * 100
        service.process("case:1")
        case = store.get("case:1")
        self.assertEqual("needs-data", case.status)
        self.assertEqual("waiting-validation", case.retry["state"])
        gate = next(row for row in case.validation_gates if row["id"] == "additional-validation")
        self.assertEqual("not-run", gate["status"])
        self.assertEqual("verified-review-required", gate["evidence"]["checks"][0]["authority"])

    def test_preview_requires_read_only_scoped_native_execution_not_just_status_ok(self):
        service, store, _, repository, _ = self.service(matched=1)
        preview = repository.validate_rulebox_materialization.return_value
        args = (preview["candidateRuleIds"], preview["targetSymbols"], preview["worldId"])
        self.assertEqual(("passed", "passed"), preview_states(preview, *args))
        for mutation in ({"worldId": "other-world"}, {"targetSymbols": ["other"]},
                         {"matchedCount": True}, {"nativeCandidateExecutionSkipped": True},
                         {"nativeMatchResult": {"status": "ok", "executedRuleCount": 0}},
                         {"mutatedOperationalRuleBox": True}, {"nativeTypeDbReasoningUsed": False}):
            with self.subTest(mutation=mutation):
                self.assertEqual(("blocked", "blocked"), preview_states({**preview, **mutation}, *args))
        self.assertEqual(("blocked", "blocked"), preview_states({"status": "ok"}, *args))

    def test_reference_candidate_has_no_hold_action_and_cannot_validate_causal_claim(self):
        service, store, advisor, repository, _ = self.service(matched=1)
        context_rule = next(row for row in default_graph_inference_rules() if not row.resolved_claim_contract.is_predictive).to_dict()
        advisor.propose_hypothesis.return_value["candidates"][0]["proposedRule"] = context_rule
        repository.validate_rulebox_materialization.return_value["candidateRuleIds"] = [context_rule["rule_id"]]
        result = service.process("case:1")
        self.assertEqual("validation", store.get("case:1").stage, result)
        case = store.get("case:1")
        self.assertTrue(all(not row["candidate_action"] for row in case.candidate_rule["derivations"]))
        self.assertEqual("blocked", case.status)
        self.assertEqual("causal-hypothesis", case.blocked_reason)
        self.assertEqual("unsupported-capability", case.retry["blockers"][0]["kind"])
        self.assertEqual("passed", next(row for row in case.validation_gates if row["id"] == "policy-safety")["status"])

    def test_candidate_preview_can_skip_only_diagnostic_baseline_not_abox_or_execution(self):
        rule = default_graph_inference_rules()[0].to_dict()
        payload = {"rules": [rule], "symbols": ["MSTR"], "worldId": "portfolio:main", "includeBaseline": False}
        store = SimpleNamespace(address="fixture", reset_query_metrics=Mock(), query_metrics_snapshot=Mock(return_value={}),
            has_box_rows=Mock(return_value=True), active_abox_metadata=Mock(return_value={"status": "ok"}),
            inferencebox_snapshot_from_typedb=Mock(return_value={"status": "ok", "relationCount": 4, "traceCount": 2}),
            match_typedb_native_rules=Mock(return_value={"status": "ok", "matchedCount": 0, "executedRuleCount": 1}))
        bindings = SimpleNamespace(materialization_preview_diff_payload=materialization_preview_diff_payload, typedb_error_code=lambda error: "fixture-error")
        result = validate_rulebox_materialization(store, payload, _bindings=bindings)
        self.assertEqual("ok", result["status"])
        self.assertTrue(result["nativeTypeDbReasoningUsed"])
        store.has_box_rows.assert_called_once()
        store.active_abox_metadata.assert_called_once()
        store.match_typedb_native_rules.assert_called_once()
        store.inferencebox_snapshot_from_typedb.assert_not_called()
        self.assertEqual("not-requested", result["diff"]["status"])
        self.assertIsNone(result["diff"]["baselineRelationCount"])
        self.assertIsNone(result["diff"]["matchedMinusBaselineRelations"])
        for extra in ({"includeBaseline": True}, {"policyOnly": True}):
            validate_rulebox_materialization(store, {**payload, **extra}, _bindings=bindings)
        self.assertEqual(2, store.inferencebox_snapshot_from_typedb.call_count)
        self.assertEqual(2, store.match_typedb_native_rules.call_count)
        store.has_box_rows.return_value = False
        self.assertEqual("missing-abox", validate_rulebox_materialization(store, payload, _bindings=bindings)["status"])
        self.assertEqual(2, store.match_typedb_native_rules.call_count)


if __name__ == "__main__":
    unittest.main()
