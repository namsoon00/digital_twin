"""Scenario continuity is evidence history, never delivery or trading authority."""
from copy import deepcopy
from functools import lru_cache
from unittest import TestCase

from digital_twin.modules.model_registry.contracts import default_graph_inference_rules, scenario_successor_rules
from digital_twin.modules.model_registry.domain.rule_strategy_contract import parse_strategy_contract
from digital_twin.modules.decisions.domain.strategy_scenario import observation_input, project_scenario
from digital_twin.modules.decisions.domain.investment_reasoning.strategy_observations import native_strategy_observations
from digital_twin.modules.notifications.domain.rule_strategy_event import strategy_phase_events, strategy_summary
from digital_twin.modules.notifications.domain.rule_notification_event import carry_intent_receipts
from stabilization_database import StabilizationDatabaseCase


@lru_cache(maxsize=1)
def scenario_rules():
    return scenario_successor_rules(default_graph_inference_rules())


def contract(market="KR"):
    return deepcopy(next(r.resolved_claim_contract.strategy_contract for r in scenario_rules()
                         if r.resolved_claim_contract.strategy_contract["market"] == market))


def fixture(phases, stamp="2026-10-23T01:00:00Z", market="KR", **scope):
    identity = {"deploymentId": "scenario-test", "accountId": "account", "symbol": "TEST", "worldId": "world:test",
                "inferenceGenerationId": "generation:" + stamp, "sourceAboxSnapshotId": "abox:" + stamp, **scope}
    relation = {"graphStoreInference": {**identity, "traces": []}, "matchedRules": []}
    current = {**identity, "market": market, "proofIdentityVerified": True, "observedAt": stamp, "rules": []}
    for rule in scenario_rules():
        claim = rule.resolved_claim_contract
        if claim.strategy_phase not in phases or claim.strategy_contract["market"] != market:
            continue
        conditions = [{"conditionId": c.condition_id, "matchedByTypeDB": True, "judgementEvidenceUsable": True,
            "freshnessStatus": "fresh" if c.relation_type == "HAS_MODEL_SIGNAL" else "not-applicable",
            "freshnessRequired": c.relation_type == "HAS_MODEL_SIGNAL", "observedAt": stamp, "maxAgeMinutes": 10}
            for c in rule.conditions]
        proof = {"ruleId": rule.rule_id, "matched": True, "strategyResponse": rule.derivations[0].strategy_response,
                 "matchedConditions": conditions, "claimContract": claim.to_dict(), "nextChecks": rule.derivations[0].next_checks}
        relation["matchedRules"].append(proof)
        relation["graphStoreInference"]["traces"].append(proof)
        current["rules"].append({"id": rule.rule_id, "matched": True, "nativeProof": True,
            "claimContract": claim.to_dict(), "conditions": conditions, "nextChecks": rule.derivations[0].next_checks})
    native = native_strategy_observations(identity, relation)
    return identity, relation, current, native


def observation(phases, stamp="2026-10-23T01:00:00Z", market="KR", **scope):
    identity, _, _, native = fixture(phases, stamp, market, **scope)
    return observation_input(contract(market), identity, native[0], stamp)


def packet(saved, phases=None):
    _, _, current, _ = fixture(phases or saved["confirmedPhases"], saved["sourceObservedAt"],
        inferenceGenerationId=saved["inferenceGenerationId"], sourceAboxSnapshotId=saved["sourceAboxSnapshotId"])
    current["decisionBrief"] = {"judgment": {"strategyScenarios": [saved]}}
    return current


class StrategyScenarioTests(TestCase):
    def test_native_synthesis_and_frozen_subject_case_render_the_scenario_message(self):
        from test_typedb_independent_observation import readable_fixture
        from digital_twin.modules.decisions.domain.investment_reasoning.synthesis import decision_synthesis_from_relation_context
        from digital_twin.modules.notifications.domain.relation_change import relation_change_evidence
        from digital_twin.modules.notifications.application.typedb_observation_message import typedb_observation_telegram_message
        context = readable_fixture()
        relation = context["ontologyRelationContext"]
        graph = relation["graphStoreInference"]
        identity = {"deploymentId": "integration", "accountId": context["accountId"],
            "symbol": relation["subject"]["symbol"], "worldId": relation["worldId"],
            **{k: graph[k] for k in ("inferenceGenerationId", "sourceAboxSnapshotId")}}
        _, native_relation, _, _ = fixture(["entry"], **identity)
        evidence_id = "stock:MSTR|HAS_TEMPORAL_WINDOW|temporal-window:MSTR:3D"
        native_relation["matchedRules"][0]["matchedConditions"][-1]["matchedTargetProperties"] = {
            "contractMatched": True, "sourceFeatureSnapshotId": "features", "modelEvidenceIds": [evidence_id],
            "knowledgeCutoffAt": "2026-10-23T01:00:00Z", "sourceTemporalWindows": [{
                "evidenceId": evidence_id, "symbol": "MSTR", "sourceFeatureSnapshotId": "features",
                "knowledgeCutoffAt": "2026-10-23T01:00:00Z", "windowKey": "3D",
                "hasSufficientHistory": True, "priceChangePct": -2.34}]}
        relation.update(hypothesisSet={"hypotheses": []}, hypothesisLifecycle={"transitions": []},
            matchedRules=native_relation["matchedRules"], activeRules=native_relation["matchedRules"], referenceRules=[])
        relation.pop("investmentBrain", None)
        graph.update(worldId=relation["worldId"], traces=native_relation["graphStoreInference"]["traces"], relations=[])
        synthesis = decision_synthesis_from_relation_context(context["accountId"], relation)
        value = observation_input(contract(), identity, synthesis.rulebox_judgment["strategyObservations"][0], "2026-10-23T01:00:00Z")
        saved, _ = project_scenario(contract(), value)
        relation["strategyScenarios"] = [saved]
        # The publication transports the immutable subject synthesis, not a
        # notification-time recomputation of the preceding scenario state.
        rebuilt = decision_synthesis_from_relation_context(context["accountId"], relation)
        context["investmentSubjectDecisionCase"] = {**identity, "subjectCaseId": "integration", "synthesis": rebuilt.to_dict()}
        context["investmentSubjectDecisionCaseId"] = "integration"
        context["relationChangeEvidence"] = relation_change_evidence(context)
        self.assertEqual(1, len(context["relationChangeEvidence"]["customerEvents"]))
        message = typedb_observation_telegram_message(context)
        self.assertIn("가격 회복과 장중 반전 조건이 함께 확인", message)
        self.assertIn("이전 확인 없음", message)
        self.assertIn("11,000", message)
        self.assertIn("-2.34%", message)
        self.assertIn("반등 실패", message)
        self.assertIn("진입 근거를 다시 판단", message)
        self.assertEqual("passed", context["customerInvestmentDocumentQuality"]["status"])

    def test_release_preserves_predecessor_and_freezes_all_transition_semantics(self):
        from digital_twin.infrastructure.graph_store_lifecycle import ontology_release_seed_artifact
        from digital_twin.infrastructure.composition.strategy_release import scenario_strategy_release_artifact
        from digital_twin.modules.reasoning.domain.composition_upgrade_review import declared_composition_additions
        from digital_twin.modules.model_registry.domain.ontology_rulebox_governance import rulebox_semantic_violations
        from digital_twin.modules.model_registry.domain.ontology_rule_manifest import validate_rule_domain_manifests
        baseline = ontology_release_seed_artifact(default_graph_inference_rules())
        record = {"status": "ready", "valid": True, "deploymentId": "baseline", "artifact": baseline,
                  "artifactFingerprint": "baseline-artifact", "ruleboxFingerprint": baseline["ruleboxFingerprint"]}
        result = scenario_strategy_release_artifact(record, {"deploymentId": "baseline", "graphStoreBinding": "baseline-db"})
        retired = set(result["compositionUpgradeContract"]["retiredRuleIds"])
        self.assertEqual([{**r, "enabled": False} if r["rule_id"] in retired else r for r in baseline["rules"]],
                         result["rules"][:len(baseline["rules"])])
        self.assertTrue(all(r["enabled"] for r in baseline["rules"] if r["rule_id"] in retired))
        self.assertEqual(8, len(declared_composition_additions(baseline, result, result["compositionUpgradeContract"])))
        self.assertEqual([], rulebox_semantic_violations(scenario_rules()))
        from digital_twin.modules.model_registry.contracts import GraphInferenceRule
        self.assertEqual([], rulebox_semantic_violations([GraphInferenceRule.from_dict(r) for r in result["rules"]]))
        wrong = deepcopy(result)
        wrong["rules"][0]["enabled"] = not wrong["rules"][0]["enabled"]
        with self.assertRaisesRegex(ValueError, "existing-rules-changed"):
            declared_composition_additions(baseline, wrong, wrong["compositionUpgradeContract"])
        wrong = deepcopy(result)
        wrong["compositionUpgradeContract"]["retiredRuleIds"].pop()
        with self.assertRaises(ValueError): declared_composition_additions(baseline, wrong, wrong["compositionUpgradeContract"])
        self.assertTrue(validate_rule_domain_manifests(default_graph_inference_rules() + scenario_rules())["valid"])
        changed = contract()
        changed["scenarioContract"]["tradingAuthority"] = "buy"
        with self.assertRaises(ValueError): parse_strategy_contract(changed)

    def test_sequence_connects_multiple_rules_and_reentry_does_not_clear_ending(self):
        head, states = None, []
        for i, phases in enumerate((["setup"], ["entry"], ["invalidation"], ["entry"])):
            saved, head = project_scenario(contract(), observation(phases, f"2026-10-23T01:0{i}:00Z"), head)
            states.append(saved["state"])
            self.assertFalse(saved["orderAuthorized"])
            self.assertFalse(saved["tradeTimingQualified"])
        self.assertEqual(["observing", "entry-review", "invalidated", "reentry-review"], states)
        self.assertFalse(saved["endingConditionsResolved"])
        self.assertIn("해제는 아직", saved["transition"]["meaning"])

    def test_cold_start_and_conflict_do_not_invent_entry_or_trade_history(self):
        for phase in ("entry", "invalidation", "exit"):
            saved, _ = project_scenario(contract(), observation([phase]))
            self.assertEqual("unobserved", saved["previousState"])
            self.assertEqual("", saved["transition"]["previousObservationId"])
        saved, _ = project_scenario(contract(), observation(["entry", "exit", "setup"]))
        self.assertEqual("conflicted", saved["state"])
        self.assertIn("방향을 확정할 수 없습니다", saved["transition"]["meaning"])
        still_conflicted, _ = project_scenario(contract(), observation(["exit"], "2026-10-23T01:01:00Z"), saved)
        self.assertEqual("conflicted", still_conflicted["state"])
        self.assertFalse(still_conflicted["notifyEligible"])
        entry, _ = project_scenario(contract(), observation(["entry"]))
        ending, _ = project_scenario(contract(), observation(["exit"], "2026-10-23T01:01:00Z"), entry)
        invalidated, _ = project_scenario(contract(), observation(["invalidation"], "2026-10-23T01:02:00Z"), ending)
        self.assertNotIn("이력은 없습니다", invalidated["transition"]["meaning"])

    def test_missing_and_stale_data_cannot_release_previous_cancellation(self):
        identity, _, _, native = fixture(["entry"])
        self.assertIsNone(observation_input(contract(), identity, native[0], "2026-10-23T01:11:00Z"))
        self.assertIsNone(observation_input(contract(), identity, native[0], "2026-10-23T00:59:00Z"))
        native[0]["phaseProofs"][0]["conditions"][-1]["observedAt"] = ""
        self.assertIsNone(observation_input(contract(), identity, native[0], "2026-10-23T01:00:00Z"))
        before, _ = project_scenario(contract(), observation(["invalidation"]))
        after, _ = project_scenario(contract(), observation(["setup"], "2026-10-23T01:01:00Z"), before)
        self.assertEqual("invalidated", after["state"])
        self.assertFalse(after["notifyEligible"])

    def test_same_source_recalculation_and_reordered_input_do_not_advance(self):
        before, _ = project_scenario(contract(), observation(["setup"]))
        changed, head = project_scenario(contract(), observation(["entry"]), before)
        self.assertEqual("same-source-different-phases", changed["status"])
        self.assertIsNone(head)
        older, head = project_scenario(contract(), observation(["exit"], "2026-10-23T00:59:00Z"), before)
        self.assertEqual("out-of-order-source", older["status"])
        self.assertIsNone(head)
        self.assertIsNone(older["transition"])
        self.assertEqual("", older["previousObservationId"])
        identity, _, _, native = fixture(["entry"], "2026-10-23T01:01:00Z")
        native[0]["phaseProofs"][0]["conditions"][1]["observedAt"] = "2026-10-23T00:59:00Z"
        mixed = observation_input(contract(), identity, native[0], "2026-10-23T01:01:00Z")
        advanced, _ = project_scenario(contract(), mixed, before)
        self.assertEqual("entry-review", advanced["state"])
        self.assertNotEqual(mixed["oldestSourceObservedAt"], mixed["sourceObservedAt"])

    def test_session_uses_exchange_date_and_retains_only_a_history_pointer(self):
        first, _ = project_scenario(contract("US"), observation(["setup"], "2026-10-22T23:50:00Z", "US"))
        second, _ = project_scenario(contract("US"), observation(["entry"], "2026-10-23T00:01:00Z", "US"), first)
        self.assertEqual("observing", second["previousState"])
        third, _ = project_scenario(contract("US"), observation(["entry"], "2026-10-23T14:00:00Z", "US"), second)
        self.assertEqual("unobserved", third["previousState"])
        self.assertEqual(second["observationId"], third["historyBeforeSession"])

    def test_shared_proof_requires_exact_lineage_and_preserves_source_clock(self):
        import json
        from digital_twin.infrastructure.graph_store_inferencebox import inferencebox_trace_payload
        identity, relation, _, _ = fixture(["entry"])
        proof = relation["matchedRules"][0]
        shared = {**deepcopy(proof), "ruleId": "shared.premise." + proof["ruleId"], "inferenceTraceId": "shared-trace"}
        proof["matchedConditions"] = [{**proof["matchedConditions"][0],
            "conditionId": "shared-market-premise:" + proof["ruleId"],
            "premiseLineage": {"originalRuleId": proof["ruleId"], "status": "available", "premiseProofId": "shared-trace"}}]
        # Exercise the actual storage reader: positive traces have id and no
        # matched flag, unlike manually invented proof dictionaries.
        relation["graphStoreInference"]["traces"] = [inferencebox_trace_payload({
            "id": row.get("inferenceTraceId") or "parent-trace", "ruleId": row["ruleId"],
            "symbol": identity["symbol"], "propertiesJson": json.dumps(row)}) for row in (proof, shared)]
        shared = relation["graphStoreInference"]["traces"][-1]
        self.assertNotIn("matched", shared)
        native = native_strategy_observations(identity, relation)
        self.assertIsNotNone(observation_input(contract(), identity, native[0], "2026-10-23T01:01:00Z"))
        shared["id"] = "unrelated"
        self.assertEqual([], native_strategy_observations(identity, relation))

    def test_receipt_dedup_does_not_define_state_or_lose_unsent_change(self):
        first, _ = project_scenario(contract(), observation(["setup"]))
        second, _ = project_scenario(contract(), observation(["entry"], "2026-10-23T01:01:00Z"), first)
        repeated, _ = project_scenario(contract(), observation(["entry"], "2026-10-23T01:02:00Z"), second)
        event = strategy_phase_events(packet(repeated), {})[0]
        self.assertEqual("observing", event["scenarioTransition"]["from"])
        from datetime import datetime, timezone
        from digital_twin.modules.notifications.domain.rule_dispatch_validity import rule_dispatch_validity
        dispatch = {"current": packet(repeated), "customerEvents": [event]}
        self.assertTrue(rule_dispatch_validity(dispatch, now=datetime(2026, 10, 23, 1, 3, tzinfo=timezone.utc))["allowed"])
        self.assertFalse(rule_dispatch_validity(dispatch, now=datetime(2026, 10, 23, 1, 20, tzinfo=timezone.utc))["allowed"])
        prior = packet(repeated)
        carry_intent_receipts(prior, {}, [event], "receipt")
        prior.update(receiptJobId="receipt", deliveredAt="2026-10-23T01:02:30Z")
        self.assertEqual([], strategy_phase_events(packet(repeated), prior))
        summary = strategy_summary({"current": packet(repeated), "customerEvents": [event]}, "가격 100원", "회복 +1%")
        self.assertIn("추가로", summary["lead"])
        self.assertLess(len(str(summary)), 1200)

    def test_successor_suppresses_old_notice_without_durable_history_fallback(self):
        from test_rule_strategy_contract import live_phase_snapshot
        _, _, current, _ = fixture(["entry"])
        current["rules"].extend(live_phase_snapshot(["entry"])["rules"])
        self.assertEqual([], strategy_phase_events(current, {}))
        saved, _ = project_scenario(contract(), observation(["entry"]))
        current["decisionBrief"] = packet(saved)["decisionBrief"]
        self.assertEqual(1, len(strategy_phase_events(current, {})))
        current["rules"][0]["conditions"][-1]["freshnessStatus"] = "stale"
        self.assertEqual([], strategy_phase_events(current, {}))

    def test_scope_and_versions_cannot_borrow_state(self):
        saved, _ = project_scenario(contract(), observation(["setup"]))
        for key in ("accountId", "symbol", "worldId", "deploymentId"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                project_scenario(contract(), observation(["entry"], "2026-10-23T01:01:00Z", **{key: "other"}), saved)


class StrategyScenarioDatabaseTests(StabilizationDatabaseCase):
    def test_candidate_builder_saves_quiet_observation_before_delivery_filter(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from digital_twin.modules.decisions.infrastructure.mysql_strategy_scenarios import MySQLStrategyScenarioStore
        from digital_twin.modules.decisions.application.strategy_scenario_service import StrategyScenarioService
        from digital_twin.modules.reasoning.application.investment_reasoning.decision_synthesis import V2GraphDecisionCandidateBuilder
        identity, relation, _, native = fixture(["setup"], accountId="quiet")
        store = MySQLStrategyScenarioStore(self.settings)
        service = StrategyScenarioService(store, identity["deploymentId"], scenario_rules())
        synthesis = SimpleNamespace(rulebox_judgment={**identity, "strategyObservations": native})
        builder = V2GraphDecisionCandidateBuilder({}, SimpleNamespace(sent={}), strategy_scenario_service=service)
        builder._base_event = Mock(return_value=None)
        prefix = "digital_twin.modules.reasoning.application.investment_reasoning.decision_synthesis."
        request = SimpleNamespace(symbols=(), account_ids=(), source_events=[], context={})
        snapshot = SimpleNamespace(account_id="quiet", generated_at="2026-10-23T01:00:00Z")
        projection = {"quiet": {"inferenceBox": {"generationAligned": True,
            "sourceAboxSnapshotId": identity["sourceAboxSnapshotId"], "nativeTypeDbReasoningCompleted": True}}}
        with patch(prefix + "relation_contexts_from_snapshot", return_value={"TEST": relation}), \
             patch(prefix + "decision_synthesis_from_relation_context", return_value=synthesis), \
             patch(prefix + "_notification_contract_failure", return_value={}):
            result = builder.build(request, [snapshot], {}, projection)
        self.assertEqual([], result["detected"])
        saved = result["hypothesisCandidates"][0]["context"]["ontologyRelationContext"]["strategyScenarios"][0]
        self.assertEqual(saved, store.current(saved["scenarioKey"]))
        self.assertEqual(saved, synthesis.rulebox_judgment["strategyScenarios"][0])

    def test_atomic_history_survives_no_delivery_restart_and_idempotent_retry(self):
        from digital_twin.modules.decisions.infrastructure.mysql_strategy_scenarios import MySQLStrategyScenarioStore
        from digital_twin.modules.decisions.application.strategy_scenario_service import StrategyScenarioService
        store = MySQLStrategyScenarioStore(self.settings)
        identity, _, _, native = fixture(["setup"])
        service = StrategyScenarioService(store, identity["deploymentId"], scenario_rules())
        first = service.observe({**identity, "strategyObservations": native}, "2026-10-23T01:00:00Z")[0]
        self.assertEqual(first, service.observe({**identity, "strategyObservations": native}, "2026-10-23T01:00:00Z")[0])
        reopened = MySQLStrategyScenarioStore(self.settings)
        second = reopened.record(contract(), observation(["entry"], "2026-10-23T01:01:00Z"))
        self.assertEqual("observing", second["previousState"])
        self.assertEqual(2, len(store.history(first["scenarioKey"])))
        self.assertEqual(second, store.current(first["scenarioKey"]))
        older = reopened.record(contract(), observation(["exit"], "2026-10-23T00:59:00Z"))
        self.assertFalse(older["notifyEligible"])
        self.assertEqual(second, store.current(first["scenarioKey"]))
        with store.connect() as connection:
            count = connection.execute("SELECT COUNT(*) AS n FROM domain_events WHERE aggregate_id = %s",
                                       (first["scenarioKey"],)).fetchone()["n"]
        self.assertEqual(2, count)

    def test_concurrent_identical_observations_emit_one_change(self):
        from concurrent.futures import ThreadPoolExecutor
        from digital_twin.modules.decisions.infrastructure.mysql_strategy_scenarios import MySQLStrategyScenarioStore
        store = MySQLStrategyScenarioStore(self.settings)
        value = observation(["entry"], accountId="concurrent")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: store.record(contract(), value), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(1, len(store.history(value["scenarioKey"])))

    def test_failed_event_append_rolls_back_both_head_and_observation(self):
        from unittest.mock import patch
        from digital_twin.modules.decisions.infrastructure.mysql_strategy_scenarios import MySQLStrategyScenarioStore
        store = MySQLStrategyScenarioStore(self.settings)
        value = observation(["entry"], accountId="rollback")
        with patch("digital_twin.modules.decisions.infrastructure.mysql_strategy_scenarios.insert_domain_event_with_connection",
                   side_effect=RuntimeError("injected event failure")), self.assertRaises(RuntimeError):
            store.record(contract(), value)
        self.assertEqual({}, store.current(value["scenarioKey"]))
        self.assertEqual([], store.history(value["scenarioKey"]))
