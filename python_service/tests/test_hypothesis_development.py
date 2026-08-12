import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.hypothesis_development_service import HypothesisDevelopmentService
from digital_twin.application.ontology_rule_candidate_service import RuleChangeCandidateProposalService
from digital_twin.domain.hypothesis_development import HypothesisDevelopmentCase


class MemoryCaseStore:
    def __init__(self):
        self.rows = {}
        self.event_rows = []

    def get(self, case_id):
        return self.rows.get(case_id)

    def get_by_fingerprint(self, fingerprint):
        return next((item for item in self.rows.values() if item.fingerprint == fingerprint), None)

    def list(self, status="", symbol="", limit=100):
        rows = list(self.rows.values())
        if status:
            rows = [item for item in rows if item.status == status]
        if symbol:
            rows = [item for item in rows if item.symbol == symbol]
        return rows[:limit]

    def save(self, case, event_type="updated", reason=""):
        self.rows[case.case_id] = case
        self.event_rows.append({
            "caseId": case.case_id,
            "eventType": event_type,
            "status": case.status,
            "reason": reason,
        })

    def events(self, case_id="", limit=200):
        rows = [item for item in self.event_rows if not case_id or item["caseId"] == case_id]
        return list(reversed(rows[-limit:]))


class MemoryExperimentStore:
    def __init__(self):
        self.rows = {}

    def get(self, experiment_id):
        return self.rows.get(experiment_id)

    def list(self):
        return list(self.rows.values())

    def save(self, experiment):
        self.rows[experiment.experiment_id] = experiment


class FakeRuleCandidateService:
    def __init__(self):
        self.calls = []

    def propose_hypothesis(self, proposal, account_id="", tenant_id=""):
        self.calls.append(dict(proposal or {}))
        return {
            "status": "ok",
            "candidateCount": 1,
            "candidates": [{
                "id": "candidate:test-demand-margin",
                "proposedRule": {
                    "rule_id": "graph.ai.demand.margin.review.v1",
                    "label": "수요 둔화와 마진 확인",
                    "version": "test-v1",
                    "source_kind": "research-evidence",
                    "action_group": "hypothesis-review",
                    "action_level": "watch",
                    "prompt_hint": "수요 둔화가 마진에 전이되는지 확인합니다.",
                    "conditions": [{
                        "condition_id": "demand-slowdown",
                        "kind": "subject_property",
                        "description": "수요 둔화 근거가 확인됨",
                        "field": "symbol",
                        "operator": "==",
                        "value": "AAPL",
                    }],
                    "derivations": [{
                        "relation_type": "REQUIRES_NEXT_CHECK",
                        "target_kind": "next-check",
                        "target_key": "{symbol}:demand-margin-followup",
                        "target_label": "다음 실적에서 수요와 마진 확인",
                        "tbox_class": "NextCheck",
                        "tbox_classes": ["NextCheck", "AIValidation"],
                        "polarity": "context",
                        "evidence_role": "context",
                        "decision_effect": "defer",
                        "decision_stage": "HYPOTHESIS_REVIEW",
                        "target_role": "holding",
                        "candidate_action": "HOLD",
                    }],
                },
            }],
        }


class FakeOntologyRepository:
    def __init__(self, matched_count=1, status="ok", validation_error=""):
        self.matched_count = matched_count
        self.status = status
        self.validation_error = validation_error
        self.validation_payloads = []
        self.saved_ruleboxes = []
        self.rules = []
        self.saved_candidates = []

    def rulebox_snapshot(self):
        return {
            "status": "ok",
            "configured": True,
            "rules": list(self.rules),
            "ruleCount": len(self.rules),
            "rulesHash": "baseline",
        }

    def save_rule_change_candidates(self, candidates, context):
        self.saved_candidates.extend(candidates or [])
        return {"status": "ok", "savedCount": len(candidates or [])}

    def validate_rulebox_materialization(self, payload):
        self.validation_payloads.append(dict(payload or {}))
        if self.validation_error:
            raise RuntimeError(self.validation_error)
        return {
            "status": self.status,
            "reason": "test-preview",
            "validationOnly": True,
            "mutatedOperationalRuleBox": False,
            "wroteInferenceBox": False,
            "candidateRuleCount": len((payload or {}).get("rules") or []),
            "matchedCount": self.matched_count,
        }


class FakeMonitorStore:
    def __init__(self, times):
        self.times = list(times)

    def load_history(self, account_id, limit=12):
        return [{
            "accountId": account_id,
            "generatedAt": stamp,
            "positions": [{"symbol": "AAPL"}],
        } for stamp in self.times[:limit]]


class FakeCandidateAdvisor:
    def propose(self, context):
        return FakeRuleCandidateService().propose_hypothesis({})["candidates"]


class FakeStrategyProposalService:
    def __init__(self):
        self.calls = []

    def propose_from_rule_candidates(self, result, context):
        self.calls.append((result, context))
        return {"status": "created"}


def proposal(proposal_id="proposal:1", claim="서비스 수요 둔화가 다음 분기 마진을 낮출 수 있다"):
    return {
        "proposalId": proposal_id,
        "accountId": "acct-1",
        "symbol": "AAPL",
        "title": "수요 둔화와 마진 전이",
        "claim": claim,
        "causalPath": ["서비스 수요 둔화", "매출 성장률 둔화", "영업마진 하락"],
        "supportingEvidenceIds": ["evidence:1"],
        "counterEvidenceIds": ["evidence:2"],
        "requiredEvidenceTypes": ["financial-results"],
        "invalidationConditions": ["다음 분기 서비스 성장률과 마진이 함께 개선됨"],
        "sourceQuestionId": "question:1",
    }


def build_service(times, matched_count=1):
    case_store = MemoryCaseStore()
    experiment_store = MemoryExperimentStore()
    candidate_service = FakeRuleCandidateService()
    repository = FakeOntologyRepository(matched_count=matched_count)
    service = HypothesisDevelopmentService(
        case_store=case_store,
        proposal_store=None,
        experiment_store=experiment_store,
        rule_candidate_service=candidate_service,
        ontology_repository=repository,
        monitor_store=FakeMonitorStore(times),
        settings={
            "ontologyTenantId": "tenant-1",
            "hypothesisDevelopmentMinimumHistoricalSnapshots": "3",
            "hypothesisDevelopmentMinimumHoldoutSnapshots": "1",
        },
    )
    return service, case_store, experiment_store, candidate_service, repository


class HypothesisDevelopmentServiceTest(unittest.TestCase):
    def test_automatically_compiles_and_validates_without_deploying_rulebox(self):
        service, case_store, experiment_store, candidate_service, repository = build_service([
            "2026-01-01T00:00:00Z",
            "2026-02-01T00:00:00Z",
            "2099-01-01T00:00:00Z",
        ])

        result = service.ingest_proposal(proposal(), "inference-generation:1")

        self.assertEqual(result["status"], "approval-required")
        case = next(iter(case_store.rows.values()))
        self.assertEqual(case.validation_summary_payload["status"], "validated")
        self.assertEqual(case.candidate_rule["enabled"], False)
        self.assertEqual(case.candidate_rule["derivations"][0]["candidate_action"], "HOLD")
        self.assertEqual(case.decision_impact["influence"], "action-disambiguation")
        self.assertEqual(len(candidate_service.calls), 1)
        self.assertEqual(len(repository.validation_payloads), 1)
        self.assertEqual(repository.saved_ruleboxes, [])
        experiment = next(iter(experiment_store.rows.values()))
        self.assertEqual(experiment.status, "completed")
        self.assertFalse(experiment.last_result["sandbox"]["mutatedOperationalRuleBox"])
        self.assertEqual(experiment.last_result["historicalCoverage"]["holdoutSnapshotCount"], 1)

    def test_waits_for_post_proposal_holdout_and_resumes_without_recompiling(self):
        service, case_store, _, candidate_service, repository = build_service([
            "2026-01-01T00:00:00Z",
            "2026-02-01T00:00:00Z",
            "2026-03-01T00:00:00Z",
        ])
        result = service.ingest_proposal(proposal(), "inference-generation:1")
        self.assertEqual(result["status"], "needs-data")
        case = next(iter(case_store.rows.values()))
        self.assertIn("holdout-observation", case.validation_summary_payload["pendingGateIds"])

        service.monitor_store.times.append("2099-01-01T00:00:00Z")
        resumed = service.process_pending(limit=5)

        self.assertEqual(resumed["processedCount"], 1)
        self.assertEqual(case.status, "approval-required")
        self.assertEqual(len(candidate_service.calls), 1)
        self.assertEqual(len(repository.validation_payloads), 2)

    def test_duplicate_proposals_merge_into_one_lineage(self):
        service, case_store, _, candidate_service, _ = build_service([
            "2026-01-01T00:00:00Z",
            "2026-02-01T00:00:00Z",
            "2099-01-01T00:00:00Z",
        ])
        service.ingest_proposal(proposal("proposal:1"), "inference-generation:1")
        result = service.ingest_proposal(proposal("proposal:2"), "inference-generation:2")

        self.assertTrue(result["merged"])
        self.assertEqual(len(case_store.rows), 1)
        case = next(iter(case_store.rows.values()))
        self.assertEqual(case.source_proposal_ids, ["proposal:1", "proposal:2"])
        self.assertEqual(case.inference_generation_ids, ["inference-generation:1", "inference-generation:2"])
        self.assertEqual(len(candidate_service.calls), 1)

    def test_non_causal_constraint_is_rejected_before_ai_compilation(self):
        service, case_store, _, candidate_service, repository = build_service([])
        body = proposal(claim="자료 부족 때문에 근거 충분성을 확인해야 한다")

        result = service.ingest_proposal(body, "inference-generation:1")

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(next(iter(case_store.rows.values())).classification, "data-or-verification-constraint")
        self.assertEqual(candidate_service.calls, [])
        self.assertEqual(repository.validation_payloads, [])

    def test_existing_operational_rule_id_blocks_candidate(self):
        service, case_store, _, _, repository = build_service([
            "2026-01-01T00:00:00Z",
            "2026-02-01T00:00:00Z",
            "2099-01-01T00:00:00Z",
        ])
        repository.rules = [{"rule_id": "graph.ai.demand.margin.review.v1"}]

        result = service.ingest_proposal(proposal(), "inference-generation:1")

        self.assertEqual(result["status"], "needs-revision")
        case = next(iter(case_store.rows.values()))
        self.assertIn("rule_id", case.blocked_reason)
        self.assertEqual(repository.validation_payloads, [])

    def test_needs_data_without_candidate_is_not_polled_repeatedly(self):
        service, case_store, _, candidate_service, _ = build_service([])
        case = HypothesisDevelopmentCase.from_proposal(proposal(), "inference-generation:1")
        case.transition("needs-data", "compilation", "financial-results")
        case_store.save(case)

        result = service.process_pending(limit=5)

        self.assertEqual(result["processedCount"], 0)
        self.assertEqual(candidate_service.calls, [])

    def test_typedb_failure_keeps_candidate_pending_for_retry(self):
        service, case_store, _, _, repository = build_service([
            "2026-01-01T00:00:00Z",
            "2026-02-01T00:00:00Z",
            "2099-01-01T00:00:00Z",
        ])
        repository.validation_error = "temporary TypeDB outage"

        result = service.ingest_proposal(proposal(), "inference-generation:1")

        self.assertEqual(result["status"], "needs-data")
        case = next(iter(case_store.rows.values()))
        self.assertIn("typedb-preview", case.validation_summary_payload["pendingGateIds"])
        self.assertTrue(case.candidate_rule)
        self.assertTrue(case.experiment_id)

    def test_hypothesis_candidate_is_not_saved_as_general_rule_change(self):
        repository = FakeOntologyRepository()
        strategy = FakeStrategyProposalService()
        service = RuleChangeCandidateProposalService(
            ontology_repository=repository,
            advisor=FakeCandidateAdvisor(),
            strategy_proposal_service=strategy,
        )

        result = service.propose_hypothesis(proposal())

        self.assertEqual(result["candidateCount"], 1)
        self.assertEqual(result["savedCount"], 0)
        self.assertEqual(repository.saved_candidates, [])
        self.assertEqual(strategy.calls, [])

    def test_case_contract_round_trips_validation_lineage(self):
        case = HypothesisDevelopmentCase.from_proposal(proposal(), "inference-generation:1")
        restored = HypothesisDevelopmentCase.from_dict(case.to_dict())
        self.assertEqual(restored.case_id, case.case_id)
        self.assertEqual(restored.source_proposal_ids, ["proposal:1"])
        self.assertIn("holdout-observation", [item["id"] for item in restored.validation_gates])


if __name__ == "__main__":
    unittest.main()
