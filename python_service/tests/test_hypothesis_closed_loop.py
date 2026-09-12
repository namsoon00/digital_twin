import copy
import json
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from digital_twin.modules.model_registry.application.hypothesis_development_service import HypothesisDevelopmentService
from digital_twin.modules.model_registry.domain.hypothesis_development import HypothesisDevelopmentCase
from digital_twin.modules.outcomes.application.investment_outcome_observation_service import InvestmentOutcomeObservationService
from digital_twin.modules.outcomes.domain.outcome_recovery import frozen_outcome_facts, outcome_needs_data, validate_outcome_repair
from digital_twin.modules.decisions.domain.decision_continuity import build_decision_continuity_packet, compact_decision_continuity_packet
from digital_twin.modules.decisions.domain.notification_ai_context_router import _continuity_delta
from digital_twin.modules.decisions.domain.notification_ai_decision_brief import _minimum_decision_continuity, build_notification_ai_prompt_bundle
from digital_twin.modules.notifications.application.notification_ai_gate_message import decision_continuity_rows
from digital_twin.modules.decisions.domain.investment_reasoning.ai_insight import AIInsightEpisode, AIInsightHandoff
from digital_twin.modules.read_models.application.investment_case_query_service import InvestmentCaseQueryService
from digital_twin.modules.model_registry.domain.hypothesis_compilation import RULE_DESIGN_VERSION, authoring_capability_index, blocker_state, compilation_blockers, rule_design_context
from digital_twin.modules.model_registry.domain.ontology_rulebox_governance import build_rule_change_candidate_prompt, normalize_rule_change_candidate, rule_change_candidates_from_text
from digital_twin.modules.model_registry.application.ontology_lab_service import OntologyLabService
from digital_twin.modules.model_registry.application.ontology_rule_candidate_service import RuleChangeCandidateProposalService
from digital_twin.infrastructure.rule_change_candidate_ai import CommandRuleChangeCandidateAdvisor, FallbackRuleChangeCandidateAdvisor


class CaseStore:
    def __init__(self, cases):
        self.rows = {case.case_id: case.to_dict() for case in cases}
        self.acquired = True

    def get(self, case_id):
        return HypothesisDevelopmentCase.from_dict(copy.deepcopy(self.rows[case_id]))

    def list(self, limit=100):
        return [self.get(key) for key in list(self.rows)[:limit]]

    def save(self, case, event_type="updated", reason=""):
        self.rows[case.case_id] = case.to_dict()

    @contextmanager
    def processing_lock(self, case_id):
        yield self.acquired


def hypothesis(key="case:1", status="needs-data"):
    return HypothesisDevelopmentCase(
        case_id=key, fingerprint=key, account_id="main", symbol="MSTR", title="수요와 매출",
        claim="수요 증가가 매출에 반영된다", causal_path=["수요 증가", "매출 개선"],
        supporting_evidence_ids=["evidence:1"], invalidation_conditions=["매출 감소"],
        status=status, stage="compilation",
    )


def previous_outcome(eligibility="excluded-criterion-data-gap"):
    return {
        "outcomeId": "outcome:1", "episodeId": "episode:1", "observedAt": "2026-09-11T01:00:00Z",
        "price": 110, "priceChangeFromDecisionPct": 10, "selectedHypothesisStatus": "directionally-corroborated",
        "payload": {"calibrationEligibility": eligibility, "decisionPrice": 100, "contractFingerprint": "contract:1",
                    "horizonMinutes": 60, "missingRequiredMetricIds": ["benchmarkReturnPct"],
                    "dataQuality": "fresh", "observationFacts": {"profitLossRate": 3.2, "ma20Distance": 2}},
    }


class HypothesisClosedLoopTests(unittest.TestCase):
    def development(self, cases=None):
        store = CaseStore(cases or [hypothesis()])
        candidate = SimpleNamespace(propose_hypothesis=Mock(return_value={
            "candidates": [{"requiresData": ["분기 매출", "수요 지표"], "blockers": [
                {"kind": "missing-observation", "requirement": "분기 매출", "dependencyKey": "quarterlyRevenue"},
                {"kind": "missing-observation", "requirement": "수요 지표", "dependencyKey": "demand"},
            ]}]
        }))
        service = HypothesisDevelopmentService(store, None, None, candidate, None)
        service.history_for = lambda case: []
        return service, store, candidate

    def test_precompile_data_gap_retries_and_survives_worker_restart(self):
        service, store, candidate = self.development()
        self.assertEqual(1, service.process_pending()["processedCount"])
        saved = store.get("case:1")
        self.assertEqual("needs-data", saved.status)
        self.assertEqual(["분기 매출", "수요 지표"], saved.retry["requirements"])
        self.assertEqual(1, saved.retry["attemptCount"])
        restarted = HypothesisDevelopmentService(store, None, None, candidate, None)
        self.assertEqual(0, restarted.process_pending()["processedCount"])
        candidate.propose_hypothesis.assert_called_once()

    def test_changed_input_requires_minimum_interval_and_unchanged_has_health_retry(self):
        service, store, candidate = self.development()
        service.process_pending()
        case = store.get("case:1")
        case.supporting_evidence_ids.append("evidence:2")
        self.assertFalse(service.validation_retry_due(case))
        case.retry["lastAttemptAt"] = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
        self.assertTrue(service.validation_retry_due(case))
        case.retry["lastInputFingerprint"] = service.validation_input_fingerprint(case)
        self.assertFalse(service.validation_retry_due(case))
        case.retry["lastAttemptAt"] = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        self.assertTrue(service.validation_retry_due(case))

    def test_terminal_cases_and_processing_lock_do_not_start_ai(self):
        for status in ("approval-required", "deployed", "retired", "rejected", "observing"):
            service, _, candidate = self.development([hypothesis(status=status)])
            self.assertEqual(status, service.process("case:1")["status"])
            candidate.propose_hypothesis.assert_not_called()
        service, store, candidate = self.development()
        store.acquired = False
        self.assertEqual("already-processing", service.process("case:1")["status"])
        candidate.propose_hypothesis.assert_not_called()

    def test_dependency_failure_is_durable_and_throttled(self):
        service, store, candidate = self.development()
        def unavailable(*args, **kwargs):
            kwargs["context_observer"]({"ruleboxSnapshotId": "registered:1", "observationState": "not-queried"})
            raise TimeoutError("test unavailable")
        candidate.propose_hypothesis.side_effect = unavailable
        result = service.process_pending()
        self.assertEqual("error", result["results"][0]["status"])
        self.assertEqual("dependency-error", store.get("case:1").retry["state"])
        self.assertEqual("compilation", store.get("case:1").stage)
        self.assertEqual("registered:1", store.get("case:1").retry["compilationContext"]["ruleboxSnapshotId"])
        self.assertEqual(0, service.process_pending()["processedCount"])

    def test_newest_terminal_rows_do_not_starve_old_data_gap(self):
        service, _, candidate = self.development([hypothesis("closed:" + str(i), "retired") for i in range(20)] + [hypothesis()])
        self.assertEqual(1, service.process_pending(limit=1)["processedCount"])
        candidate.propose_hypothesis.assert_called_once()

    def test_compilation_receives_complete_scoped_rule_syntax_not_just_counts(self):
        rule = {"rule_id": "graph.fixture", "source_kind": "stock", "conditions": [
            {"kind": "relation", "relation_type": "HAS_MODEL_SIGNAL", "target_property_filters": {"field": "fixture"}}
        ], "derivations": [{"decision_stage": "review", "evidence_role": "context", "decision_effect": "defer"}]}
        context = {"symbols": ["MSTR"], "worldId": "portfolio:main", "ruleBox": {"status": "ok", "rules": [rule]},
                   "hypothesisProposal": {"supportingEvidenceIds": ["trace:graph.fixture"]}}
        design = rule_design_context(context)
        self.assertEqual(rule["conditions"], design["examples"][0]["conditions"])
        self.assertEqual(rule["derivations"], design["examples"][0]["derivations"])
        self.assertIn("target_property_filters", design["conditionFields"])
        self.assertIn("candidate_action", design["derivationFields"])
        self.assertEqual("portfolio:main", design["scope"]["worldId"])
        prompt = build_rule_change_candidate_prompt(context)
        prompt_context = json.loads(prompt.split("입력 컨텍스트:\n", 1)[1])
        self.assertEqual("fixture", prompt_context["ruleDesign"]["examples"][0]["conditions"][0]["target_property_filters"]["field"])
        self.assertIn(RULE_DESIGN_VERSION, prompt)
        self.assertEqual([], rule_design_context(context, max_bytes=1)["examples"])
        repository = SimpleNamespace(rulebox_snapshot=Mock(return_value=context["ruleBox"]),
                                     inferencebox_snapshot=Mock(side_effect=TimeoutError("unneeded live detail")))
        advisor = SimpleNamespace(propose=Mock(return_value=[]))
        service = RuleChangeCandidateProposalService(repository, advisor)
        service.propose_hypothesis(hypothesis().to_dict())
        repository.inferencebox_snapshot.assert_not_called()
        supplied = advisor.propose.call_args.args[0]
        self.assertEqual("deferred-validation", supplied["inferenceBox"]["status"])
        self.assertEqual("authoring-only", supplied["inferenceBox"]["decisionEligibility"])
        self.assertTrue(supplied["worldId"])

    def test_compilation_blockers_park_unsupported_and_keep_true_observation_wait_retryable(self):
        for kind in ("schema-mismatch", "unsupported-capability", "unclassified", "missing-observation", "observation-window"):
            with self.subTest(kind=kind):
                service, store, candidate = self.development()
                raw = {"blockers": [{"kind": kind, "requirement": "requirement", "dependencyKey": "key"}]}
                normalized = normalize_rule_change_candidate(raw)
                self.assertEqual(kind, normalized["blockers"][0]["kind"])
                candidate.propose_hypothesis.return_value = {"candidates": [normalized]}
                service.process_pending()
                saved = store.get("case:1")
                self.assertEqual(blocker_state(compilation_blockers([raw]))[0], saved.status)
                if saved.status == "needs-revision":
                    self.assertEqual("", saved.retry["nextCheckAt"])
                    self.assertEqual(0, service.process_pending()["processedCount"])
        legacy = compilation_blockers([{"requiresData": ["unknown legacy requirement"]}])
        self.assertEqual("unclassified", legacy[0]["kind"])
        structured = compilation_blockers([{"requiresData": ["short display label"], "blockers": [
            {"kind": "missing-observation", "requirement": "Detailed observation requirement", "dependencyKey": "revenue"}
        ]}])
        self.assertEqual(1, len(structured))
        self.assertEqual(("needs-data", "waiting-data"), blocker_state(structured))
        case = hypothesis()
        case.supporting_evidence_ids = []
        case.retry = {"blockers": [{"kind": "schema-mismatch", "requirement": "old blocker"}]}
        service, store, candidate = self.development([case])
        service.process("case:1")
        self.assertEqual("missing-observation", store.get("case:1").retry["blockers"][0]["kind"])
        self.assertEqual("waiting-data", store.get("case:1").retry["state"])
        candidate.propose_hypothesis.assert_not_called()

    def test_authoring_retrieves_relevant_contract_beyond_first_thirty_rules(self):
        rules = [{"rule_id": "graph.a." + str(i), "label": "가격 확인", "conditions": [],
                  "claim_contract": {"claimType": "market-hypothesis", "statement": "가격 확인"}}
                 for i in range(40)]
        event_rule = {
            "rule_id": "graph.z.absorption", "label": "위험 이벤트 가격 방어 흡수 가설",
            "claim_contract": {"claimType": "market-hypothesis", "statement": "위험 이벤트 충격 흡수"},
            "conditions": [{"relation_type": "HAS_MODEL_SIGNAL", "target_property_filters": {
                "signalType": "event-abnormal-return-support", "releaseId": "event-production-v2",
                "hypothesisContractId": "graph.z.absorption"}}],
            "model_input_contract": {"conditionProfiles": [{"relationType": "HAS_TEMPORAL_WINDOW"}]},
            "derivations": [{"evidence_role": "support"}],
        }
        context = {"ruleBox": {"rules": rules + [event_rule]},
                   "hypothesisProposal": {"title": "공시 이벤트 충격 흡수 가설", "claim": "가격 방어로 충격 흡수",
                                          "retry": {"compilationContext": {"exampleRuleIds": ["graph.a.0"]}}},
                   "inferenceBox": {"status": "deferred-validation"}}
        design = rule_design_context(context)
        self.assertEqual(event_rule, next(row for row in rules + [event_rule] if row["rule_id"] == design["examples"][0]["rule_id"]))
        indexed = design["capabilityIndex"]["rules"][0]
        self.assertEqual("graph.z.absorption", indexed["modelEvidence"][0]["hypothesisContractId"])
        self.assertIn("HAS_TEMPORAL_WINDOW", indexed["inputRelations"])
        self.assertEqual("partial-loaded-release", design["capabilityIndex"]["coverage"])
        self.assertEqual(41, len(design["capabilityIndex"]["registeredRuleIds"]))
        self.assertEqual(41, design["capabilityIndex"]["totalRuleCount"])
        self.assertEqual("not-queried", design["observationState"])
        tiny = authoring_capability_index(rules + [event_rule], max_bytes=1)
        self.assertEqual("partial-loaded-release", tiny["coverage"])
        self.assertEqual(41, tiny["omittedRuleCount"])

    def test_unqueried_abox_cannot_be_reported_as_verified_missing_data(self):
        payload = json.dumps({"candidates": [{"blockers": [{
            "kind": "missing-observation", "requirement": "event absent", "dependencyKey": "event:1"
        }]}]})
        context = {"hypothesisProposal": {"claim": "fixture"}, "inferenceBox": {"status": "deferred-validation"}}
        candidate = rule_change_candidates_from_text(payload, context)[0]
        blocker = candidate["blockers"][0]
        self.assertEqual("unverified-observation", blocker["kind"])
        self.assertIn("미조회", blocker["requirement"])
        self.assertEqual(("needs-revision", "development-required"), blocker_state([blocker]))
        # The actual validation owner may still report a proven data gap.
        checked = rule_change_candidates_from_text(payload, {"inferenceBox": {"status": "ok"}})[0]
        self.assertEqual("missing-observation", checked["blockers"][0]["kind"])
        self.assertIn("빈 inferenceBox.relations를 결측 증거로 사용하지 않는다", build_rule_change_candidate_prompt(context))

    def test_a_candidate_does_not_override_its_explicit_compilation_blockers(self):
        service, store, advisor = self.development()
        advisor.propose_hypothesis.return_value = {
            "contextSummary": {"observationState": "not-queried", "exampleRuleIds": ["registered:1"]},
            "candidates": [{"proposedRule": {"rule_id": "new-rule"}, "blockers": [{
                "kind": "unsupported-capability", "requirement": "exact model not registered",
                "dependencyKey": "model:new-rule",
            }]}],
        }
        service.create_experiment = Mock(side_effect=AssertionError("blocked candidate entered validation"))
        service.process("case:1")
        saved = store.get("case:1")
        self.assertEqual("needs-revision", saved.status)
        self.assertEqual("not-queried", saved.retry["compilationContext"]["observationState"])
        service.create_experiment.assert_not_called()

    def test_authoring_reads_only_scoped_exact_model_receipts_and_keeps_failed_conditions(self):
        rule = {"rule_id": "graph.absorption", "label": "충격 흡수", "conditions": [{
            "relation_type": "HAS_MODEL_SIGNAL", "target_property_filters": {
                "releaseId": "event-v2", "hypothesisContractId": "graph.absorption",
            }}]}
        model_store = SimpleNamespace(latest=Mock(return_value={
            "accountId": "main", "modelReleaseId": "event-v2", "snapshotId": "snapshot:1",
            "asOf": "2026-09-12T00:00:00Z", "sourceFeatureSnapshotId": "feature:1",
            "assessments": [
                {"subjectId": "MSTR", "hypothesisContractId": "graph.absorption", "status": "not-supported",
                 "failedConditionIds": ["event-response"], "unknownConditionIds": [], "assessmentId": "assessment:1"},
                {"subjectId": "AAPL", "hypothesisContractId": "graph.absorption", "status": "supported"},
                {"subjectId": "MSTR", "hypothesisContractId": "graph.unrelated", "status": "supported"},
            ],
        }))
        repository = SimpleNamespace(rulebox_snapshot=Mock(return_value={"status": "ok", "rules": [rule]}),
                                     inferencebox_snapshot=Mock(side_effect=AssertionError("live detail not needed")))
        advisor = SimpleNamespace(propose=Mock(return_value=[]))
        service = RuleChangeCandidateProposalService(repository, advisor, model_signal_store=model_store)
        result = service.propose_hypothesis(hypothesis().to_dict())
        self.assertIsNone(result["contextSummary"]["inferenceRelationCount"])
        receipt = result["contextSummary"]["modelAssessmentContext"]
        self.assertEqual("available", receipt["status"])
        self.assertEqual("MSTR", receipt["symbol"])
        self.assertEqual(1, len(receipt["snapshots"][0]["assessments"]))
        assessment = receipt["snapshots"][0]["assessments"][0]
        self.assertEqual("not-supported", assessment["status"])
        self.assertEqual(["event-response"], assessment["failedConditionIds"])
        model_store.latest.assert_called_once_with("main", subject_id="MSTR", model_release_id="event-v2")
        repository.inferencebox_snapshot.assert_not_called()
        supplied = advisor.propose.call_args.args[0]
        self.assertIn('"failedConditionIds"', build_rule_change_candidate_prompt(supplied))
        model_store.latest.return_value["accountId"] = "other"
        rejected = service.model_assessment_context(supplied, "main")
        self.assertEqual([], rejected["snapshots"])
        self.assertEqual("partial", rejected["status"])
        self.assertIn("scope mismatch", rejected["errors"][0]["reason"])
        model_store.latest.side_effect = TimeoutError("fixture")
        failed = service.model_assessment_context(supplied, "main")
        self.assertEqual([], failed["snapshots"])
        self.assertEqual("partial", failed["status"])
        model_store.latest.side_effect = None
        model_store.latest.return_value["accountId"] = "main"
        model_store.latest.return_value["assessments"][0]["evidenceIds"] = ["evidence:" + "x" * 13000]
        bounded = service.model_assessment_context(supplied, "main")
        self.assertEqual([], bounded["snapshots"][0]["assessments"])
        self.assertEqual(1, bounded["omittedAssessmentCount"])
        model_store.latest.return_value = {}
        supplied["ruleBox"]["rules"] = [{"rule_id": "graph." + str(index), "conditions": [{
            "relation_type": "HAS_MODEL_SIGNAL", "target_property_filters": {"releaseId": "event-" + str(index)},
        }]} for index in range(4)]
        partial = service.model_assessment_context(supplied, "main")
        self.assertEqual("partial", partial["status"])
        self.assertEqual(1, partial["omittedReleaseCount"])

    def test_price_only_changes_do_not_recompile_missing_schema_or_observation(self):
        service, store, _ = self.development()
        case = store.get("case:1")
        a = [{"positions": [{"symbol": "MSTR", "currentPrice": 100}]}]
        b = [{"positions": [{"symbol": "MSTR", "currentPrice": 120}]}]
        self.assertEqual(service.validation_input_fingerprint(case, a), service.validation_input_fingerprint(case, b))
        case.candidate_rule = {"rule_id": "fixture"}
        self.assertNotEqual(service.validation_input_fingerprint(case, a), service.validation_input_fingerprint(case, b))

    def test_deferred_rows_do_not_consume_hypothesis_batch_execution_slots(self):
        cases = [hypothesis("waiting:" + str(i)) for i in range(70)] + [hypothesis("ready")]
        service, store, advisor = self.development(cases)
        for case in cases[:-1]:
            case.retry = {"attemptCount": 1, "lastAttemptAt": datetime.now(timezone.utc).isoformat(),
                          "designVersion": RULE_DESIGN_VERSION}
            store.save(case)
        result = service.process_pending(limit=1)
        self.assertEqual(1, result["processedCount"])
        self.assertEqual(1, store.get("ready").retry["attemptCount"])
        advisor.propose_hypothesis.assert_called_once()
        self.assertTrue(store.get("waiting:0").retry["nextCheckAt"])

    def test_busy_hypothesis_does_not_consume_the_next_ready_case_slot(self):
        service, store, advisor = self.development([hypothesis("busy"), hypothesis("ready")])
        @contextmanager
        def lock(case_id):
            yield case_id != "busy"
        store.processing_lock = lock
        result = service.process_pending(limit=1)
        self.assertEqual(1, result["processedCount"])
        advisor.propose_hypothesis.assert_called_once()

    def test_hypothesis_ai_outage_is_not_swallowed_as_no_candidates(self):
        self.assertEqual(300, CommandRuleChangeCandidateAdvisor(["fixture"]).timeout_seconds)
        self.assertEqual(120, CommandRuleChangeCandidateAdvisor(["fixture"], timeout_seconds=120).timeout_seconds)
        primary = SimpleNamespace(propose=Mock(side_effect=TimeoutError("offline")))
        advisor = FallbackRuleChangeCandidateAdvisor(primary)
        with self.assertRaises(TimeoutError):
            advisor.propose({"hypothesisProposal": {"claim": "fixture"}})
        self.assertEqual([], advisor.propose({}))

    def test_continuous_realtime_backlog_reserves_one_aged_development_turn(self):
        service = object.__new__(OntologyLabService)
        service.settings = {}
        service.enabled = lambda: True
        service.reasoning_queue_deferral = lambda: {"status": "deferred-reasoning-queue", "runCount": 0}
        development = SimpleNamespace(ready_wait_minutes=Mock(return_value=31), process_pending=Mock(return_value={"processedCount": 1}))
        service.hypothesis_development_service = development
        result = service.run_once()
        self.assertEqual("development-reserved-slot", result["status"])
        development.process_pending.assert_called_once_with(limit=1)
        development.ready_wait_minutes.return_value = 2
        self.assertEqual("deferred-reasoning-queue", service.run_once()["status"])

    def test_outcome_repair_preserves_original_price_time_and_contract(self):
        previous = previous_outcome()
        facts = frozen_outcome_facts(previous)
        self.assertEqual(110, facts["currentPrice"])
        self.assertEqual(3.2, facts["profitLossRate"])
        self.assertEqual(100, facts["decisionPrice"])
        self.assertTrue(outcome_needs_data(previous))
        repaired = copy.deepcopy(previous)
        repaired["payload"]["benchmarkReturnPct"] = 2
        repaired["payload"]["calibrationEligibility"] = "eligible"
        validate_outcome_repair(previous, repaired)
        self.assertFalse(outcome_needs_data(repaired))
        for key in ("price", "observedAt", "episodeId"):
            invalid = copy.deepcopy(repaired)
            invalid[key] = "changed"
            with self.assertRaises(ValueError):
                validate_outcome_repair(previous, invalid)
        for key in ("horizonMinutes", "contractFingerprint", "decisionPrice"):
            invalid = copy.deepcopy(repaired)
            invalid["payload"][key] = "changed"
            with self.assertRaises(ValueError):
                validate_outcome_repair(previous, invalid)

    def test_repair_uses_frozen_quote_and_retained_baseline_not_latest_price(self):
        target = {"requestId": "r", "episodeId": "episode:1", "symbol": "MSTR", "horizonMinutes": 60,
                  "requiresInstrumentBaseline": True, "benchmarkSymbol": "SPY", "previousOutcome": previous_outcome(),
                  "baselineObservations": {"benchmark": {"currentPrice": 200, "sourceAsOf": "2026-09-11T00:00:00Z"}}}
        store = SimpleNamespace(pending_outcome_targets=lambda *a, **kw: [target], record_outcome_observations=Mock(return_value=[]))
        timeseries = SimpleNamespace(load_baseline_observations=lambda *a, **kw: {},
                                     load_outcome_observations=lambda *a, **kw: {"r": {"currentPrice": 999}, "r:benchmark-end": {"currentPrice": 204}})
        service = InvestmentOutcomeObservationService(store, timeseries)
        service.observe_snapshot(SimpleNamespace(account_id="main", generated_at="2026-09-12T00:00:00Z", positions=[], watchlist=[], has_live_account_data=lambda: True))
        record = store.record_outcome_observations.call_args.args[1][0]
        self.assertEqual("2026-09-11T01:00:00Z", record["observedAt"])
        self.assertEqual(110, record["facts"]["currentPrice"])
        self.assertEqual(100, record["facts"]["decisionPrice"])
        self.assertEqual(2, record["facts"]["benchmarkReturnPct"])

    def test_future_baselines_are_captured_once_and_later_quotes_are_rejected(self):
        target = {"requestId": "r", "symbol": "MSTR", "benchmarkSymbol": "SPY", "baselineAt": "2026-09-11T00:00:00Z"}
        store = SimpleNamespace(outcome_collection_targets=lambda *a, **kw: [target], record_outcome_baselines=Mock())
        timeseries = SimpleNamespace(load_baseline_observations=Mock(return_value={
            "r:instrument": {"currentPrice": 100, "sourceAsOf": "2026-09-11T00:00:00Z"},
            "r:benchmark": {"currentPrice": 200, "sourceAsOf": "2026-09-11T00:00:01Z"},
        }))
        service = InvestmentOutcomeObservationService(store, timeseries)
        service.capture_baselines("main")
        records = store.record_outcome_baselines.call_args.args[1]
        self.assertEqual(["instrument"], [item["kind"] for item in records])
        target["baselineObservations"] = {"instrument": records[0]["observation"]}
        timeseries.load_baseline_observations.reset_mock()
        service.capture_baselines("main")
        self.assertEqual(["r:benchmark"], [item["requestId"] for item in timeseries.load_baseline_observations.call_args.args[1]])

    def test_ai_web_and_notification_share_data_gap_not_success(self):
        packet = build_decision_continuity_packet(account_id="main", symbol="MSTR", captured_at="2026-09-12T00:00:00Z",
                                                  previous_decision={"episodeId": "episode:1", "action": "HOLD", "decisionSummary": "매출 개선 확인"},
                                                  observed_outcomes=[previous_outcome()])
        self.assertEqual("data-gap", packet["observationState"]["outcome"])
        summary = packet["reviewSummary"]
        self.assertEqual("data-gap", summary["outcomes"][0]["state"])
        self.assertEqual(summary, compact_decision_continuity_packet(packet)["reviewSummary"])
        routed = _continuity_delta(packet)["reviewSummary"]
        self.assertEqual(summary["state"], routed["state"])
        self.assertEqual(summary["outcomes"][0]["calibrationEligibility"], routed["outcomes"][0]["calibrationEligibility"])
        self.assertEqual("excluded-criterion-data-gap", _minimum_decision_continuity(packet)["observedOutcomes"][0]["calibrationEligibility"])
        rows = decision_continuity_rows({"decisionContinuityPacket": packet})
        self.assertIn("보류", rows[-1])
        self.assertNotIn("지지했습니다", rows[-1])
        from test_notification_ai_inference_packet import investment_context
        context = investment_context()
        context["decisionContinuityPacket"] = build_decision_continuity_packet(
            account_id="main", symbol="035420", captured_at="2026-09-12T00:00:00Z",
            previous_decision={"episodeId": "previous:1", "decisionSummary": "이전 판단입니다."},
            observed_outcomes=[{**previous_outcome(), "outcomeId": "outcome:" + str(i)} for i in range(6)],
        )
        bundle = build_notification_ai_prompt_bundle(context, max_prompt_bytes=16384)
        self.assertLessEqual(bundle["promptBudget"]["renderedPromptBytes"], 16384)
        memory = bundle["decisionCore"]["continuityDelta"]["reviewSummary"]
        self.assertEqual("data-gap", memory["outcomes"][0]["state"])
        self.assertEqual(4, memory["omittedOutcomeCount"])

    def test_unverified_condition_is_not_new_change_and_missing_old_eligibility_is_not_success(self):
        old = previous_outcome()
        old["payload"] = {}
        packet = build_decision_continuity_packet(account_id="main", symbol="MSTR", captured_at="now", observed_outcomes=[old],
                                                  follow_up_conditions=[{"status": "satisfied", "label": "회복", "transitionVerified": False}])
        self.assertEqual([], packet["reviewSummary"]["verifiedChanges"])
        self.assertEqual("excluded", packet["reviewSummary"]["outcomes"][0]["state"])

    def test_saved_ai_memory_drives_web_and_rejects_other_account_or_symbol(self):
        subject = {"subjectCaseId": "subject:1", "batchCaseId": "batch:1", "accountId": "main", "symbol": "MSTR",
                   "sourceAboxSnapshotId": "abox:1", "inferenceGenerationId": "generation:1", "candidateSetId": "candidates:1",
                   "candidateFingerprint": "fingerprint:1"}
        context = {"investmentSubjectDecisionCase": subject}
        context["investmentAIInsightHandoff"] = AIInsightHandoff.create(context, {"jobId": "job:1", "text": "fixture"}).to_dict()
        packet = build_decision_continuity_packet(account_id="main", symbol="MSTR", captured_at="2026-09-12T00:00:00Z",
                                                  previous_decision={"episodeId": "previous:1", "action": "HOLD", "decisionSummary": "매출 개선 확인"},
                                                  observed_outcomes=[previous_outcome()])
        context["decisionContinuityPacket"] = packet
        episode = AIInsightEpisode.create(SimpleNamespace(), SimpleNamespace(response={}), context)
        query = InvestmentCaseQueryService(None)
        query._ai_insight_for_subject = lambda case: episode.to_dict()
        query._reasoning_case_for_subject = lambda case: {}
        query._observations_for_subject = lambda case: []
        detail = query._subject_detail("subject:1", subject)
        self.assertEqual(packet["packetId"], detail["decisionReview"]["packetId"])
        self.assertEqual("data-gap", detail["decisionReview"]["outcomes"][0]["state"])
        for key in ("accountId", "symbol"):
            invalid = copy.deepcopy(context)
            invalid["decisionContinuityPacket"][key] = "different"
            with self.assertRaises(ValueError):
                AIInsightEpisode.create(SimpleNamespace(), SimpleNamespace(response={}), invalid)
            self.assertEqual({}, query._subject_detail("subject:1", {**subject, key: "different"})["decisionReview"])


if __name__ == "__main__":
    unittest.main()
