"""Independent AI closes the research loop without acquiring release authority."""
import copy
import hashlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ai_insight_fixtures import packet, plan
from digital_twin.modules.ai_orchestration.domain.planning import validate_plan, bounded_planning_prompt
from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
from digital_twin.modules.ai_orchestration.domain.insight_schema import bounded_planning_schema
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    freeze_execution_input, freeze_repair_input, validate_execution_input, PREVIOUS_PROMPT_VERSION,
    BOUNDED_PROMPT_VERSION, LEGACY_REPAIR_PROMPT_VERSION,
)
from digital_twin.modules.model_registry.domain.observation_development import (
    observation_development_request, validate_observation_development_context,
)
from digital_twin.modules.model_registry.application.hypothesis_proposal_service import HypothesisProposalService, HypothesisProposalQueueRunner
from digital_twin.infrastructure.transactions.ai_control_development import AIControlDevelopment


QUESTION = {"question": "단기 회복과 중기 약세의 충돌을 구분하는 가설을 어떻게 검증할 수 있는가?", "capability": "develop-hypothesis"}


def development_result(task):
    evidence = {**packet(), "taskId": task["taskId"]}
    draft = plan()
    draft["notification"]["send"] = False
    draft["questions"] = [QUESTION]
    result = {**validate_plan(draft, evidence), "input": evidence, "executionInputId": "input",
              "inputFingerprint": "material-input", "followUpEvaluations": []}
    result["quality"] = local_quality(result)
    return result


class AIObservationDevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.task = {**{key: packet()[key] for key in ("accountId", "symbol", "worldId", "name")}, "taskId": "development-task"}

    def test_capability_is_bounded_and_preserves_historical_frozen_prompts(self):
        from question_resolution_checks import assert_resolution_contracts
        assert_resolution_contracts(self)
        result = development_result(self.task)
        self.assertEqual([QUESTION["question"]], result["developmentQuestions"])
        self.assertEqual([], result["questions"])  # Internal design work is not a customer follow-up question.
        self.assertEqual([], result["researchQuestions"])
        for questions in ([QUESTION, QUESTION], [{**QUESTION, "capability": "execute-code"}], [{**QUESTION, "accountId": "other"}]):
            with self.assertRaises(ValueError):
                validate_plan({**plan(), "questions": questions}, result["input"])
        envelope = freeze_execution_input(result["input"], [], [])
        validate_execution_input(envelope)
        self.assertIn("develop-hypothesis", envelope["outputSchema"]["properties"]["questions"]["items"]["properties"]["capability"]["enum"])
        for version in (PREVIOUS_PROMPT_VERSION, BOUNDED_PROMPT_VERSION, LEGACY_REPAIR_PROMPT_VERSION):
            old = copy.deepcopy(envelope)
            old["promptVersion"] = version
            old["prompt"] = bounded_planning_prompt(old["current"], [], [])
            old["outputSchema"] = bounded_planning_schema(old["current"])
            if version == LEGACY_REPAIR_PROMPT_VERSION:
                from digital_twin.modules.ai_orchestration.domain.insight_repair import repair_prompt
                old["repair"] = {"parentInputId": "parent", "errors": ["citation"], "rejectedDraft": plan()}
                old["prompt"] = repair_prompt(old["prompt"], old["repair"])
            old["promptHash"] = hashlib.sha256(old["prompt"].encode()).hexdigest()
            validate_execution_input(old)
            self.assertNotIn("develop-hypothesis", old["outputSchema"]["properties"]["questions"]["items"]["properties"]["capability"]["enum"])
        validate_execution_input(freeze_repair_input(envelope, plan(), ["citation"], "parent"))
        self.assert_request_freezes_evidence_and_daily_identity_without_qualification()
        self.assert_reference_only_evidence_cannot_seed_a_hypothesis()

    def assert_request_freezes_evidence_and_daily_identity_without_qualification(self):
        result = development_result(self.task)
        request = observation_development_request(self.task, result)
        context = request["observationContext"]
        self.assertEqual({"quote-1"}, validate_observation_development_context(context, self.task["accountId"], "TEST"))
        self.assertEqual("unverified", context["empiricalQualification"])
        self.assertEqual({}, request["hypothesisSet"])
        result["input"]["facts"][0]["currentPrice"] = 200
        result["developmentQuestions"] = ["새 가격에서도 같은 가설 검증을 추가로 요청할 수 있는가?"]
        self.assertEqual(100, context["packet"]["facts"][0]["currentPrice"])
        self.assertEqual(request["requestId"], observation_development_request(self.task, result)["requestId"])
        for account, symbol in (("another", "TEST"), (self.task["accountId"], "OTHER")):
            with self.assertRaises(ValueError):
                validate_observation_development_context(context, account, symbol)
        context["packet"]["facts"][0]["currentPrice"] = 101
        with self.assertRaises(ValueError):
            validate_observation_development_context(context, self.task["accountId"], "TEST")

    def test_existing_proposal_worker_receives_captured_facts_and_development_ingress(self):
        from digital_twin.modules.model_registry.domain.proposal_shape import proposal_rows
        for malformed in ({"causalPath": "외국인 순매도 → 흡수 실패"}, {"supportingEvidenceIds": "quote-1"},
                          {"invalidationConditions": [1]}, {"causalPath": ["단계"] * 13}):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                proposal_rows([{}, malformed])
        request = observation_development_request(self.task, development_result(self.task))
        advisor, proposals, development = Mock(), Mock(), Mock()
        proposals.list_hypothesis_proposals.return_value = []
        advisor.propose.return_value = [{"claim": "기간별 흐름 충돌은 다음 관찰에서 구분할 수 있다.", "supportingEvidenceIds": ["quote-1"],
                                        "causalPath": ["단기 회복", "중기 흐름"], "invalidationConditions": ["중기 가격 회복"]}]
        development.ingest_proposal.return_value = {"status": "shadow-observing", "caseId": "case"}
        service = HypothesisProposalService(proposals, advisor=advisor, development_service=development)
        queue = Mock()
        queue.claim_hypothesis_proposal_requests.return_value = [request]
        queue.hypothesis_proposal_request_summary.return_value = {}
        runner = HypothesisProposalQueueRunner(queue, service)
        runner.reconcile_backlog_if_due = lambda: {}
        outcome = runner.run_once()["results"][0]
        self.assertEqual(1, outcome["proposalCount"])
        self.assertEqual(request["observationContext"], advisor.propose.call_args.args[0]["observationContext"])
        self.assertEqual("ai-control-observation", development.ingest_proposal.call_args.args[0]["source"])
        self.assertEqual("", development.ingest_proposal.call_args.args[1])  # No invented inference generation.
        queue.complete_hypothesis_proposal_request.assert_called_once()
        request["observationContext"]["packet"]["symbol"] = "OTHER"
        advisor.reset_mock()
        runner.run_once()
        advisor.propose.assert_not_called()
        queue.fail_hypothesis_proposal_request.assert_called_once()
        self.assert_development_status_returns_to_next_frozen_analysis_and_breaks_unchanged_reuse()
        self.assert_memory_reads_current_case_state_with_exact_subject_scope()

    def assert_reference_only_evidence_cannot_seed_a_hypothesis(self):
        result = development_result(self.task)
        result["input"]["facts"][0]["judgementEvidenceUsable"] = False
        with self.assertRaisesRegex(ValueError, "usable evidence"):
            observation_development_request(self.task, result)

    def assert_development_status_returns_to_next_frozen_analysis_and_breaks_unchanged_reuse(self):
        from test_ai_control import AIControlTests
        from digital_twin.modules.ai_orchestration.domain.planning import observation_fingerprint, stamp
        from digital_twin.modules.ai_orchestration.domain.execution_input import PROMPT_VERSION
        records = [{"kind": "ontology-development", "requestId": "request", "status": "completed", "cases": [{"status": "blocked"}]}]
        service, store, planner = AIControlTests().runner(development_memory=Mock(return_value=records))
        store.memory.return_value = [{"inputFingerprint": observation_fingerprint(service.evidence.return_value, []),
            "observedAt": stamp(), "executionPromptVersion": PROMPT_VERSION, "quality": {"status": "observation-only"}}]
        self.assertEqual("completed", service.run_once()["status"])
        captured = planner.call_args.args[0]["researchResults"]
        self.assertEqual("required-continuity", captured[0]["memoryRole"])
        self.assertEqual(records[0]["cases"], captured[0]["cases"])
        self.assertEqual(records, captured[1:])

    def assert_memory_reads_current_case_state_with_exact_subject_scope(self):
        result = development_result(self.task)
        request = observation_development_request(self.task, result)
        research = Mock()
        research.observation_development_records.return_value = [{"requestId": request["requestId"], "status": "completed", "request": request,
            "result": {"proposalCount": 1, "hypothesisDevelopment": [{"caseId": "case"}]}}]
        case = SimpleNamespace(case_id="case", account_id=self.task["accountId"], symbol="TEST", status="rolled-back",
                               blocked_reason="post-adoption-deterioration", evolution={"state": "rolled-back"}, to_dict=lambda: {})
        bridge = AIControlDevelopment(research, lambda _: case)
        memory = bridge.memory(self.task["accountId"], "TEST")[0]
        self.assertEqual("rolled-back", memory["cases"][0]["status"])
        self.assertEqual("experiment-status-only", memory["authority"])
        case.account_id = "other-account"
        with self.assertRaises(ValueError):
            bridge.memory(self.task["accountId"], "TEST")


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class ObservationDevelopmentStorageTests(unittest.TestCase):
    def setUp(self):
        from digital_twin.infrastructure.settings import runtime_settings
        from digital_twin.modules.ai_orchestration.infrastructure.mysql_control import MySQLAIControlStore
        from digital_twin.modules.news_intelligence.infrastructure.mysql_observation_development import MySQLObservationDevelopmentStore
        settings = {**runtime_settings(), "aiControlBudgetEnabled": "false"}
        self.control = MySQLAIControlStore(settings)
        self.research = MySQLObservationDevelopmentStore(settings)
        self.bridge = AIControlDevelopment(self.research, lambda _: None)
        self.control.development_writer = self.bridge.record
        self.clean()

    def clean(self):
        with self.control.transaction() as connection:
            for table in ("ai_control_input_calls", "ai_control_inputs", "ai_control_tasks", "ai_control_budget", "ai_control_calls"):
                connection.execute("DELETE FROM " + table)
            connection.execute("DELETE FROM investment_hypothesis_proposal_requests WHERE account_id='control-test'")

    def tearDown(self):
        self.clean()

    def claimed(self):
        subject = {key: packet()[key] for key in ("accountId", "symbol", "name", "worldId")}
        self.control.seed(subject)
        task = self.control.claim()
        result = development_result(task)
        envelope = freeze_execution_input(result["input"], [], [])
        input_id = self.control.save_execution_input(task, envelope)
        result["executionInputId"] = input_id
        call = self.control.begin_call("independent-observation", envelope["promptHash"], task["taskId"], input_id)
        self.control.finish_call(call)
        return task, result

    def test_handoff_is_atomic_lease_fenced_and_daily_coalescing_preserves_first_input(self):
        task, result = self.claimed()
        self.assertFalse(self.control.complete({**task, "leaseToken": "lost"}, result, []))
        self.assertEqual([], self.research.observation_development_records("control-test", "TEST"))
        self.control.outbox_writer = Mock(side_effect=RuntimeError("outbox unavailable"))
        with self.assertRaises(RuntimeError):
            self.control.complete(task, result, [])
        self.assertEqual([], self.research.observation_development_records("control-test", "TEST"))
        self.control.outbox_writer = None
        self.assertTrue(self.control.complete(task, result, []))
        self.assertFalse(self.control.complete(task, result, []))
        saved = self.research.observation_development_records("control-test", "TEST")[0]
        changed = copy.deepcopy(result)
        changed["developmentQuestions"] = ["새 근거로 같은 날 다시 개발을 요청하면 어떻게 되는가?"]
        changed["input"]["facts"][0]["currentPrice"] = 999
        with self.control.transaction() as connection:
            self.research.enqueue_observation_development_with_connection(connection, observation_development_request(task, changed))
        self.assertEqual(saved, self.research.observation_development_records("control-test", "TEST")[0])
        self.assertEqual([], self.bridge.memory("other-account", "TEST"))
        self.clean()
        self.assert_rejected_and_unproven_observations_cannot_create_work()

    def assert_rejected_and_unproven_observations_cannot_create_work(self):
        task, result = self.claimed()
        changed = copy.deepcopy(result)
        changed["executionInputId"] = "missing"
        with self.assertRaises(ValueError):
            self.control.complete(task, changed, [])
        changed = copy.deepcopy(result)
        changed["input"]["facts"][0]["currentPrice"] = 101
        with self.assertRaises(ValueError):
            self.control.complete(task, changed, [])
        result["quality"]["status"] = "rejected"
        self.assertTrue(self.control.complete(task, result, []))
        self.assertEqual("blocked", result["development"]["status"])
        self.assertEqual([], self.research.observation_development_records("control-test", "TEST"))


if __name__ == "__main__":
    unittest.main()
