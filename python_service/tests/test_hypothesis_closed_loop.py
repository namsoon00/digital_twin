import copy
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
            "candidates": [{"requiresData": ["분기 매출", "수요 지표"]}]
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
        candidate.propose_hypothesis.side_effect = TimeoutError("test unavailable")
        result = service.process_pending()
        self.assertEqual("error", result["results"][0]["status"])
        self.assertEqual("dependency-error", store.get("case:1").retry["state"])
        self.assertEqual(0, service.process_pending()["processedCount"])

    def test_newest_terminal_rows_do_not_starve_old_data_gap(self):
        service, _, candidate = self.development([hypothesis("closed:" + str(i), "retired") for i in range(20)] + [hypothesis()])
        self.assertEqual(1, service.process_pending(limit=1)["processedCount"])
        candidate.propose_hypothesis.assert_called_once()

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
