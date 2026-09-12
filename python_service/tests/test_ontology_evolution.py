import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch
from contextlib import nullcontext

from digital_twin.infrastructure.hypothesis_proposal_ai import CommandHypothesisProposalAdvisor, proposal_rows_from_text
from digital_twin.infrastructure.ontology_evolution_runtime import claim_binding, comparison_measurement, OntologyEvolutionRuntime
from digital_twin.infrastructure.transactions.decision_history_parts.evolution_comparison import read_comparison
from digital_twin.modules.model_registry.application.ontology_evolution_service import OntologyEvolutionService
from digital_twin.modules.model_registry.domain.hypothesis_development import HypothesisDevelopmentCase, screen_hypothesis_case
from digital_twin.modules.model_registry.domain.ontology_evolution import create_plan, evaluate_comparison, validate_plan
from digital_twin.modules.model_registry.infrastructure.evolution_policy import evolution_policy
from digital_twin.modules.model_registry.contracts import default_graph_inference_rules, GraphInferenceRule
from digital_twin.modules.reasoning.public import append_rule_to_release_artifact
from digital_twin.infrastructure.graph_store_lifecycle import ontology_release_seed_artifact


START = datetime(2026, 8, 1, tzinfo=timezone.utc)


def case():
    return HypothesisDevelopmentCase("case:evolve", "case-fingerprint", "test", "TEST", "Event recovery", "Recovery after a verified event",
        causal_path=["verified event", "price recovery"], supporting_evidence_ids=["evidence:1"],
        invalidation_conditions=["price reversal"])


def pairs(plan, count=20, candidate_wins=18, baseline_wins=5, start=START):
    return [{"id": "pair:" + str(i), "accountId": "test", "symbol": "TEST",
             "candidateFingerprint": plan["fingerprint"], "sourceSnapshotId": "snapshot:" + str(i),
             "observedFromAt": (start + timedelta(days=i, minutes=1)).isoformat(),
             "observedAt": (start + timedelta(days=i, minutes=61)).isoformat(),
             "eligible": True, "independenceKey": "event:" + str(i),
             "candidateOutcome": "corroborated" if i < candidate_wins else "contradicted",
             "baselineOutcome": "corroborated" if i < baseline_wins else "contradicted"} for i in range(count)]


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.policy = evolution_policy()
        self.plan = create_plan(case(), {"rule_id": "graph.candidate.v1"},
                                {"deploymentId": "baseline", "artifactFingerprint": "frozen"}, self.policy, START.isoformat())

    def evaluate(self, rows):
        return evaluate_comparison(self.plan, {"status": "ok", "pairs": rows}, now=(START + timedelta(days=50)).isoformat())

    def test_exact_forward_cohort_can_qualify_without_trading(self):
        result = self.evaluate(pairs(self.plan))
        self.assertEqual("qualified", result["status"])
        self.assertFalse(result["automaticDeployment"])
        self.assertEqual(20, result["independentPairCount"])

    def test_duplicates_old_data_and_wrong_revision_do_not_count(self):
        rows = pairs(self.plan)
        rows[0]["observedFromAt"] = (START - timedelta(days=1)).isoformat()
        rows[1]["candidateFingerprint"] = "old-revision"
        rows[2]["eligible"] = False
        rows[3]["baselineOutcome"] = "inconclusive"
        rows[4]["sourceSnapshotId"] = ""
        result = self.evaluate(rows + rows)
        self.assertEqual("needs-data", result["status"])
        self.assertEqual(15, result["independentPairCount"])

    def test_identical_predictions_are_not_an_improvement(self):
        result = self.evaluate(pairs(self.plan, candidate_wins=20, baseline_wins=20))
        self.assertEqual("not-better", result["status"])
        self.assertEqual(1, result["pairedTestPValue"])

    def test_later_wins_cannot_replace_failed_preregistered_cohort(self):
        first = pairs(self.plan, candidate_wins=5, baseline_wins=10)
        later = pairs(self.plan, candidate_wins=20, baseline_wins=0, start=START + timedelta(days=20))
        self.assertEqual("not-better", self.evaluate(first + later)["status"])

    def test_pending_early_outcome_cannot_be_replaced_by_a_later_winner(self):
        first = pairs(self.plan)
        first[0]['eligible'] = False
        later = pairs(self.plan, start=START + timedelta(days=20), candidate_wins=20, baseline_wins=0)
        result = self.evaluate(first + later)
        self.assertEqual('needs-data', result['status'])
        self.assertEqual(19, result['independentPairCount'])

    def test_nested_plan_and_policy_tampering_are_rejected(self):
        for path in ("candidateRule", "policy", "baseline"):
            value = copy.deepcopy(self.plan)
            value[path]["extra"] = True
            with self.assertRaises(ValueError):
                validate_plan(value)

    def test_policy_is_not_allowed_to_turn_off_all_evidence(self):
        for key in ("minimumIndependentPairs", "minimumDistinctDays", "independenceMinutes"):
            with self.assertRaises(ValueError):
                evolution_policy({"ontologyEvolutionPolicy": {**self.policy, key: 0}})

    def test_ai_failure_is_retried_not_reported_as_no_hypothesis(self):
        advisor = CommandHypothesisProposalAdvisor(['fixture-command'])
        with patch('digital_twin.infrastructure.hypothesis_proposal_ai.run_background_ai_prompt', side_effect=TimeoutError('unavailable')), self.assertRaises(TimeoutError):
            advisor.propose({})
        with self.assertRaises(RuntimeError):
            CommandHypothesisProposalAdvisor([]).propose({})
        with self.assertRaises(ValueError):
            proposal_rows_from_text("invalid response")
        self.assertEqual([], proposal_rows_from_text('{"proposals": []}'))

    def test_proposal_deduplication_is_private_to_each_account(self):
        from digital_twin.modules.model_registry.application.hypothesis_proposal_service import HypothesisProposalService
        proposal = {'claim': 'Event recovery', 'supportingEvidenceIds': ['evidence:1']}
        store = Mock()
        store.list_hypothesis_proposals.return_value = [{'accountId': 'other-account', **proposal}]
        service = HypothesisProposalService(store, advisor=SimpleNamespace(propose=lambda context: [proposal, proposal]))
        service.known_evidence_ids = lambda context: {'evidence:1'}
        result = service.propose('test', 'TEST', {}, {})
        self.assertEqual(1, result['proposalCount'])
        store.list_hypothesis_proposals.return_value = [{'accountId': 'test', **proposal}]
        self.assertEqual(0, service.propose('test', 'TEST', {}, {})['proposalCount'])

    def test_word_match_does_not_reject_a_structured_hypothesis(self):
        item = case()
        item.claim = "A data gap does not prevent testing independently verified event recovery"
        self.assertEqual("passed", screen_hypothesis_case(item)["status"])
        item.causal_path = []
        self.assertEqual("needs-revision", screen_hypothesis_case(item)["status"])

    def test_complete_lifecycle_and_crash_recovery(self):
        item = case()
        item.candidate_rule = {"rule_id": "graph.candidate.v1"}
        current = [START]
        runtime = SimpleNamespace(
            baseline=Mock(return_value=self.plan["baseline"]),
            stage=Mock(return_value={"status": "staged", "deploymentId": "candidate"}),
            state=Mock(return_value={"status": "shadow"}),
            comparison=Mock(return_value={"status": "ok", "pairs": []}),
            adopt=Mock(), rollback=Mock(return_value={"status": "rolled-back"}),
            retire=Mock(return_value={"status": "retired"}), finish_monitoring=Mock(return_value={"status": "completed"}),
        )
        service = OntologyEvolutionService(runtime, self.policy, lambda: current[0].isoformat())
        persist = Mock()
        service.start(item, persist)
        self.assertEqual("shadow-observing", item.status)
        runtime.adopt.assert_not_called()
        frozen = copy.deepcopy(item.evolution["plan"])
        restored = HypothesisDevelopmentCase.from_dict(item.to_dict())
        current[0] = START + timedelta(days=21)
        runtime.comparison.return_value = {"status": "ok", "pairs": pairs(frozen)}
        runtime.adopt.return_value = {"status": "promoted", "adoptedAt": current[0].isoformat()}
        service.advance(restored, persist)
        self.assertEqual("evolution-monitoring", restored.status)
        runtime.stage.assert_called_once()
        self.assertEqual(frozen, restored.evolution["plan"])
        runtime.state.return_value = {"status": "active", "adoptedAt": current[0].isoformat(), "criticalFailure": True}
        runtime.comparison.side_effect = ConnectionError("Outcome reader unavailable")
        service.advance(restored, persist)
        self.assertEqual("rolled-back", restored.status)

    def test_shadow_mode_and_unverified_review_cannot_promote(self):
        for mode, requirements in (("shadow", []), ("automatic", [{"check": "review", "requirement": "Causal research"}])):
            item = case()
            item.candidate_rule = {"rule_id": "graph.candidate.v1"}
            item.validation_requirements = requirements
            policy = {**self.policy, "mode": mode}
            plan = create_plan(item, item.candidate_rule, self.plan["baseline"], policy, START.isoformat())
            item.evolution = {"plan": plan, "deployment": {"deploymentId": "candidate"}}
            item.status = "shadow-observing"
            runtime = SimpleNamespace(state=Mock(return_value={"status": "shadow"}), comparison=Mock(return_value={"status": "ok", "pairs": pairs(plan)}), adopt=Mock())
            service = OntologyEvolutionService(runtime, policy, lambda: (START + timedelta(days=21)).isoformat())
            service.advance(item, Mock())
            runtime.adopt.assert_not_called()

    def test_expired_candidate_retires_before_result_read_or_promotion(self):
        for mode in ("automatic", "shadow"):
            for status in ("shadow-observing", "adoption-ready"):
                with self.subTest(mode=mode, status=status):
                    item = case()
                    item.status = status
                    item.candidate_rule = {"rule_id": "graph.candidate.v1"}
                    policy = {**self.policy, "mode": mode}
                    plan = create_plan(item, item.candidate_rule, self.plan["baseline"], policy, START.isoformat())
                    item.evolution = {"plan": plan, "deployment": {"deploymentId": "candidate"}}
                    runtime = SimpleNamespace(state=Mock(return_value={"status": "shadow"}),
                        comparison=Mock(side_effect=ConnectionError("Outcome reader unavailable")),
                        retire=Mock(return_value={"status": "waiting"}), adopt=Mock())
                    service = OntologyEvolutionService(runtime, policy, lambda: (START + timedelta(days=30)).isoformat())
                    self.assertEqual("candidate-retirement-pending", service.advance(item, Mock())["reason"])
                    self.assertTrue(item.retry["nextCheckAt"])
                    runtime.retire.return_value = {"status": "retired"}
                    self.assertEqual("observation-window-expired", service.advance(item, Mock())["reason"])
                    self.assertEqual("retired", item.status)
                    self.assertEqual("", item.retry["nextCheckAt"])
                    runtime.comparison.assert_not_called()
                    runtime.adopt.assert_not_called()


class EvolutionArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rule = next(r for r in default_graph_inference_rules() if r.rule_id == "graph.temporal.risk_event_absorption.support.v1")
        cls.artifact = ontology_release_seed_artifact([cls.rule], release_bundle={"release_id": "baseline"})
        cls.candidate = json.loads(json.dumps(cls.rule.to_dict()).replace(cls.rule.rule_id, "graph.test.event_recovery.v1"))
        cls.candidate["enabled"] = False

    def test_baseline_bytes_and_tbox_survive_addition(self):
        original = copy.deepcopy(self.artifact)
        original["graph"]["entities"][0]["label"] = "Saved vocabulary"
        combined = append_rule_to_release_artifact(original, self.candidate)
        self.assertEqual(original["rules"][0], combined["rules"][0])
        self.assertEqual(original["tboxMetadata"], combined["tboxMetadata"])
        self.assertEqual("Saved vocabulary", combined["graph"]["entities"][0]["label"])
        self.assertEqual(2, len(combined["rules"]))
        self.assertTrue(combined["rules"][1]["enabled"])
        self.assertFalse(self.candidate["enabled"])
        self.assertGreater(len(combined["graph"]["relations"]), len(original["graph"]["relations"]))

    def test_existing_rule_id_cannot_be_rewritten(self):
        with self.assertRaises(ValueError):
            append_rule_to_release_artifact(self.artifact, self.rule.to_dict())

    def test_cold_start_restores_authored_artifact_not_default_catalog(self):
        from digital_twin.infrastructure.composition.reasoning_binding import bind_v2_release
        artifact = append_rule_to_release_artifact(self.artifact, self.candidate)
        saved = {"valid": True, "artifact": artifact, "artifactFingerprint": "saved",
                 "ruleboxFingerprint": artifact["ruleboxFingerprint"], "tboxFingerprint": artifact["tboxFingerprint"]}
        manifest = {"status": "ok", "metadata": {"ruleboxRulesHash": artifact["ruleboxFingerprint"], "tboxFingerprint": artifact["tboxFingerprint"]}}
        repository = SimpleNamespace(read_seed_static_manifest=Mock(side_effect=[{}, manifest]),
            seed_release_artifact=Mock(return_value={"saved": True, "runtimeRuleboxFingerprint": "runtime-hash"}),
            active_tbox_metadata=lambda: artifact["tboxMetadata"])
        descriptor = SimpleNamespace(deployment_id="candidate", release_bundle=SimpleNamespace(
            to_dict=lambda: artifact["releaseBundle"], tbox_release_id=artifact["tboxMetadata"]["version"]))
        platform = SimpleNamespace(registry=SimpleNamespace(control=lambda: SimpleNamespace(active_deployment_id="active", delivery_deployment_id="active"),
            release_artifact=lambda key: saved))
        with patch("digital_twin.infrastructure.composition.reasoning_release.prepare_v2_rulebox_release", return_value=({"rulesHash": "runtime-hash"}, {})) as prepare, \
             patch("digital_twin.modules.reasoning.domain.reasoning_engine_versions.reasoning_release_identity", return_value={}):
            result = bind_v2_release(repository, platform, descriptor, {}, {}, {})
        repository.seed_release_artifact.assert_called_once_with(artifact)
        self.assertTrue(prepare.call_args.kwargs["release_guard"]["immutable"])
        self.assertEqual("unchanged", result.release_artifact_persistence["status"])

    def test_authored_action_is_not_silently_changed_to_hold(self):
        from test_hypothesis_candidate_validation import HypothesisCandidateValidationTests
        service, _, _, _, _ = HypothesisCandidateValidationTests().service()
        candidate = copy.deepcopy(self.candidate)
        candidate["derivations"][0]["candidate_action"] = "ADD"
        governed = service.governed_candidate_rule(case(), candidate)
        self.assertEqual("ADD", governed["derivations"][0]["candidate_action"])
        self.assertEqual("support", governed["derivations"][0]["decision_effect"])
        self.assertFalse(governed["enabled"])

    def test_authored_baseline_and_private_scope_survive_serialization(self):
        from test_hypothesis_candidate_validation import HypothesisCandidateValidationTests
        from digital_twin.modules.reasoning.domain.world_partitioned_reasoning import compile_world_partitioned_rules
        service, _, _, _, _ = HypothesisCandidateValidationTests().service()
        service.evolution_service = SimpleNamespace(policy=evolution_policy())
        candidate = copy.deepcopy(self.candidate)
        candidate['model_input_contract']['comparisonBaselineRuleId'] = self.rule.rule_id
        governed = service.governed_candidate_rule(case(), candidate)
        parsed = GraphInferenceRule.from_dict(governed)
        self.assertEqual(self.rule.rule_id, parsed.to_dict()['model_input_contract']['comparisonBaselineRuleId'])
        self.assertEqual('TEST', parsed.model_input_contract['evolutionScope']['symbol'])
        partition = compile_world_partitioned_rules([GraphInferenceRule.from_dict({**governed, 'enabled': True})])
        self.assertEqual([], partition['sharedRules'])
        self.assertEqual(1, len(partition['overlayRules']))

    def test_scoped_typeql_binds_both_requested_and_authorized_world(self):
        from digital_twin.modules.reasoning.infrastructure.typeql.match_queries import typedb_native_match_query
        rule = copy.deepcopy(self.candidate)
        rule['model_input_contract']['evolutionScope'] = {'worldId': 'portfolio:test', 'symbol': 'TEST'}
        query = typedb_native_match_query(rule, ['OTHER'], world_id='portfolio:other')['query']
        self.assertIn('$evolutionWorld == "portfolio:test"', query)
        self.assertIn('$evolutionWorld == "portfolio:other"', query)
        self.assertIn('"TEST"', query)
        self.assertIn('"OTHER"', query)
        parameterized = typedb_native_match_query(rule, ['TEST'], world_id_variable='$inputWorld')['query']
        self.assertIn('$evolutionWorld == $inputWorld', parameterized)

    def test_outcome_criteria_cannot_be_relaxed_for_comparison(self):
        strict = copy.deepcopy(self.candidate)
        strict['claim_contract']['outcomeContract']['criteria'][0]['required'] = False
        self.assertNotEqual(comparison_measurement(self.candidate), comparison_measurement(strict))

    def test_staging_rejects_scope_expansion_before_registry_access(self):
        item = case()
        plan = create_plan(item, self.candidate, {'deploymentId': 'base', 'artifactFingerprint': 'base-hash'}, evolution_policy(), START.isoformat())
        platform = Mock()
        runtime = OntologyEvolutionRuntime(platform, Mock(), Mock())
        with self.assertRaisesRegex(ValueError, 'validated account'):
            runtime.stage(plan)
        platform.registry.control.assert_not_called()

    def test_baseline_is_selected_from_persisted_model_contract(self):
        candidate = copy.deepcopy(self.candidate)
        candidate['model_input_contract']['comparisonBaselineRuleId'] = self.rule.rule_id
        platform = SimpleNamespace(registry=SimpleNamespace(
            control=lambda: SimpleNamespace(active_deployment_id='active', delivery_deployment_id='active'),
            release_artifact=lambda key: {'valid': True, 'artifactFingerprint': 'frozen', 'artifact': self.artifact}))
        runtime = OntologyEvolutionRuntime(platform, Mock(), Mock())
        result = runtime.baseline(GraphInferenceRule.from_dict(candidate).to_dict(), evolution_policy())
        self.assertEqual(self.rule.rule_id, result['comparisonRuleId'])
        self.assertEqual(60, result['comparisonHorizonMinutes'])

    def test_completed_evolution_is_not_reauthored_by_repeated_proposal(self):
        from test_hypothesis_candidate_validation import HypothesisCandidateValidationTests
        service, store, _, _, _ = HypothesisCandidateValidationTests().service()
        item = case()
        item.status = 'strengthened'
        item.evolution = {'plan': {'fingerprint': 'frozen'}}
        store.save(item)
        service._process_case = Mock()
        result = service.process(item.case_id, force=True)
        self.assertEqual('strengthened', result['status'])
        service._process_case.assert_not_called()

    def test_prediction_anchor_is_immutable_across_polling(self):
        from digital_twin.modules.outcomes.domain.hypothesis_observation import ShadowHypothesisObservationEpisode
        from digital_twin.infrastructure.transactions.decision_history_parts.shadow_observations import save_shadow_hypothesis_observations
        frozen = {"episodeId": "episode:1", "accountId": "test", "symbol": "TEST", "observedFromAt": START.isoformat(),
                  "sourceAboxSnapshotId": "first-snapshot", "readiness": {"eligible": True}}
        incoming = {**frozen, "sourceAboxSnapshotId": "later-snapshot", "observedFromAt": (START + timedelta(minutes=1)).isoformat()}
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = {"payload_json": json.dumps(frozen)}
        sync = Mock()
        saved = save_shadow_hypothesis_observations([ShadowHypothesisObservationEpisode.from_dict(incoming)],
            _sync_shadow_hypothesis_observation_targets=sync, _transaction=lambda: nullcontext(connection), utc_now_iso=lambda: START.isoformat())
        self.assertEqual("first-snapshot", saved[0].source_abox_snapshot_id)
        self.assertEqual(1, connection.execute.call_count)
        sync.assert_not_called()

    def test_atomic_release_switch_rejects_stale_owner_before_any_write(self):
        from digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime import MySQLReasoningEngineRegistryStore
        store = object.__new__(MySQLReasoningEngineRegistryStore)
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = {
            "version": 4, "active_deployment_id": "unrelated", "delivery_deployment_id": "unrelated", "candidate_deployment_id": "candidate"}
        store.transaction = lambda: nullcontext(connection)
        with self.assertRaises(RuntimeError):
            store.switch_evolution_release("baseline", "candidate", {}, expected_version=4)
        self.assertEqual(1, connection.execute.call_count)

    def test_atomic_release_switch_commits_receipt_with_both_owners(self):
        from digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime import MySQLReasoningEngineRegistryStore
        store = object.__new__(MySQLReasoningEngineRegistryStore)
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = {
            "version": 4, "active_deployment_id": "baseline", "delivery_deployment_id": "baseline", "candidate_deployment_id": "candidate"}
        connection.execute.return_value.fetchall.return_value = [
            {"deployment_id": "baseline", "deployment_status": "active"},
            {"deployment_id": "candidate", "deployment_status": "candidate", "last_health_json": "{}"}]
        store.transaction = lambda: nullcontext(connection)
        control = store.switch_evolution_release("baseline", "candidate", {"planFingerprint": "frozen", "state": "adopted"}, expected_version=4)
        self.assertEqual("candidate", control.active_deployment_id)
        self.assertEqual("candidate", control.delivery_deployment_id)
        self.assertEqual("baseline", control.candidate_deployment_id)
        calls = connection.execute.call_args_list
        self.assertIn("ontologyEvolution", calls[2].args[1][0])
        self.assertEqual(6, len(calls))

    def test_pair_reader_requires_exact_contract_and_snapshot(self):
        item = case()
        plan = create_plan(item, self.candidate, {"deploymentId": "baseline", "artifactFingerprint": "fingerprint",
                            "candidateClaim": claim_binding(self.candidate), "comparisonClaim": claim_binding(self.rule.to_dict()),
                            "comparisonHorizonMinutes": 60}, evolution_policy(), START.isoformat())
        rows = []
        for rule in (self.candidate, self.rule.to_dict()):
            claim = GraphInferenceRule.from_dict(rule).resolved_claim_contract.to_dict()
            episode = {"accountId": "test", "symbol": "TEST", "claimContractId": claim["claimContractId"],
                       "hypothesis": {"claimContract": claim}, "readiness": {"eligible": True},
                       "sourceAboxSnapshotId": "snapshot:1", "observedFromAt": START.isoformat(),
                       "marketIndependenceKey": "event:1", "outcomeContract": {"contractFingerprint": "outcome:1"}}
            outcome = {"outcomeId": claim["ruleId"], "selectedHypothesisStatus": "corroborated", "observedAt": (START + timedelta(hours=1)).isoformat(),
                       "payload": {"calibrationEligibility": "eligible", "sourceAboxSnapshotId": "snapshot:1",
                                   "contractFingerprint": "outcome:1", "targetAt": (START + timedelta(hours=1)).isoformat(), "horizonMinutes": 60}}
            rows.append({"episode_json": json.dumps(episode), "outcome_json": json.dumps(outcome)})
        connection = Mock()
        connection.execute.return_value.fetchall.return_value = rows
        from contextlib import nullcontext
        connect = lambda: nullcontext(connection)
        self.assertEqual(1, len(read_comparison(plan, connect=connect)["pairs"]))
        changed = json.loads(rows[1]["episode_json"])
        changed["hypothesis"]["claimContract"]["statement"] = "A different revision"
        rows[1]["episode_json"] = json.dumps(changed)
        result = read_comparison(plan, connect=connect)
        self.assertEqual(1, result["unpairedAnchorCount"])
        self.assertFalse(result["pairs"][0]["eligible"])
        self.assertEqual(1, result["provenanceRejectedCount"])


if __name__ == "__main__":
    unittest.main()
