"""Question evidence must advance once, preserve scope and never fund itself."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

from test_business_research import business_packet
from test_research_knowledge import context
from test_scoped_knowledge_research import research_session, scoped_decision
from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
from digital_twin.modules.ai_orchestration.domain import question_evidence as work, scoped_retrieval, research_repeat
from digital_twin.modules.ai_orchestration.domain.observation_wait import retry_key, disposition
from digital_twin.modules.ai_orchestration.domain.execution_input import validate_execution_input
from digital_twin.modules.ai_orchestration.infrastructure.mysql_question_evidence import record_question_evidence


def question(packet, key="cash"):
    return {**{key: packet[key] for key in ("accountId", "symbol", "worldId")},
        "caseId": key, "kind": "brain-case", "question": "동일 기간 현금 회수가 개선되었는가?",
        "capability": "research", "status": "waiting", "revision": 1, "reviewDue": False,
        "nextCheckAt": "2026-10-19T00:00:00Z", "researchAttempts": 2,
        "questionContract": {"target": "영업현금흐름 비교", "period": "공식 연간 보고 기간",
                             "answerCriterion": "동일 기간과 단위의 공식 현금흐름 원문 비교"},
        "answerResolution": {"status": "partial", "missingRequirements": ["영업현금흐름 비교 원문"]},
        "latestSourceAnswer": {"status": "partial", "text": "매출 원문은 확인했지만 현금흐름 표는 없습니다.",
            "sources": [{"evidenceId": "report-2025", "materialFingerprint": "body-one"}],
            "missingRequirements": ["영업현금흐름 비교 원문"]}}


def waiting_scope(case):
    return {"caseId": case["caseId"], "status": "waiting", "selectedFactIds": ["report-2025"],
            "missingEvidence": ["원래 연간 보고 기간의 현금흐름 비교 원문이 필요합니다."]}


class QuestionEvidenceFlowTests(unittest.TestCase):
    def test_original_question_moves_from_source_index_to_scoped_review_and_persistent_gap(self):
        from question_assessment_checks import check_question_assessment
        check_question_assessment(self)
        from question_recovery_checks import check_source_references, check_finite_followup, check_review_dispatch
        check_source_references(self)
        check_finite_followup(self)
        check_review_dispatch(self)
        for check in (self.assert_question_cohort_preserves_original_requirements,
                      self.assert_ready_question_requires_finish_in_new_policy,
                      self.assert_read_checkpoint_preserves_answer_scope_and_budget):
            with self.subTest(stage=check.__name__):
                check()

    def test_internal_research_requires_changed_source_or_original_contract_before_paid_work(self):
        self.assert_retry_identity_ignores_internal_progress()
        self.assert_reviewed_internal_research_skips_paid_stages_until_source_changes()

    def assert_question_cohort_preserves_original_requirements(self):
        p = business_packet(); p["researchKnowledge"] = context(p)
        q = question(p)
        unrelated = [{**q, "caseId": "price-" + str(i), "capability": "observe", "reviewDue": True,
                      "nextCheckAt": "2020-01-01T00:00:00Z", "answerResolution": {}} for i in range(3)]
        frozen = []
        selected, _, _, trace = retrieve_working_evidence(research_session(p), p, [], unrelated + [q],
            Mock(return_value=scoped_decision("research", ["report-2025"], [waiting_scope(q)], ["현금흐름 원문 필요"])),
            lambda envelope: frozen.append(deepcopy(envelope)) or "read-input", 262144)
        index = frozen[0]["retrievalContext"]["questionIndex"]
        self.assertEqual(q["questionContract"], index[0]["questionContract"])
        self.assertEqual("partial", index[0]["sourceAnswer"]["status"])
        self.assertEqual(["영업현금흐름 비교 원문"], index[0]["missingRequirements"])
        self.assertEqual("report-2025", index[0]["sourceAnswer"]["sources"][0]["evidenceId"])
        self.assertIn("cash", selected["researchWindow"]["includedCaseIds"])
        self.assertEqual(1, selected["researchWindow"]["excludedCaseCount"])
        self.assertEqual(q["questionContract"], work.checkpoints(selected, trace)[0]["questionContract"])
        validate_execution_input(frozen[0])

    def assert_ready_question_requires_finish_in_new_policy(self):
        p = business_packet(); q = question(p)
        facts = {row["id"]: row for row in p["facts"]}
        scope = {"caseId": q["caseId"], "status": "ready", "selectedFactIds": ["report-2025"], "missingEvidence": []}
        decision = scoped_decision("research", ["report-2025"], [scope], ["다른 질문의 자료 부족"])
        old = {"questionIndex": [{"caseId": q["caseId"]}]}
        scoped_retrieval.validate_scope(decision, old, facts)
        from digital_twin.modules.ai_orchestration.domain.retrieval import ReadRequestError
        for action in ("research", "defer"):
            with self.assertRaises(ReadRequestError):
                scoped_retrieval.validate_scope({**decision, "action": action}, {**old, "questionEvidencePolicy": work.VERSION}, facts)
        scoped_retrieval.validate_scope(scoped_decision("finish", ["report-2025"], [scope]),
            {**old, "questionEvidencePolicy": work.VERSION}, facts)
        # No new marker means no rewritten historical prompt.
        legacy = scoped_retrieval.freeze(p, {**old, "trace": [], "knownFacts": [], "reportIndex": []}, 262144)
        self.assertNotIn(work.INSTRUCTIONS, legacy["prompt"])
        validate_execution_input(legacy)

    def assert_read_checkpoint_preserves_answer_scope_and_budget(self):
        p = business_packet(); p.update(taskId="read-task", researchKnowledge=context(p))
        q = question(p); original = deepcopy(q)
        selected, _, _, trace = retrieve_working_evidence(research_session(p), p, [], [q],
            Mock(return_value=scoped_decision("defer", ["report-2025"], [waiting_scope(q)], ["현금흐름 원문 필요"])),
            lambda _: "read-input", 262144)
        job = {**{key: p[key] for key in ("accountId", "symbol", "worldId", "taskId")}, "capability": "observe"}
        store = Mock(); store.read.return_value = q
        receipt = record_question_evidence(store, Mock(), job, selected, trace)
        self.assertEqual([q["caseId"]], receipt["caseIds"])
        self.assertEqual(original, {key: value for key, value in q.items() if key != "evidenceProgress"})
        self.assertEqual(waiting_scope(q)["missingEvidence"], q["evidenceProgress"]["missingRequirements"])
        self.assertEqual("not-an-answer-or-thesis", q["evidenceProgress"]["qualification"])
        from digital_twin.modules.ai_orchestration.domain.research_progress import research_progress
        self.assertIsNone(research_progress(q, {"stage": "research-evidence-progress"}))
        self.assertEqual([], record_question_evidence(store, Mock(), job, selected, trace)["caseIds"])
        self.assertEqual(1, store.save.call_count)
        research_repeat.initialize(q); research_repeat.remember(q, "planned")
        self.assertEqual("unchanged-research-material", research_repeat.admission(q, "planned"))
        q["evidenceProgress"]["missingRequirements"] = ["다르게 표현한 같은 부족분"]
        self.assertEqual("unchanged-research-material", research_repeat.admission(q, "planned"))
        compact = research_repeat.planning_memory(q)
        from digital_twin.modules.ai_orchestration.domain.research_work import freeze
        envelope = freeze({**job, "name": "Test"}, [compact])
        validate_execution_input(envelope)
        self.assertIn("다르게 표현한 같은 부족분", envelope["prompt"])
        self.assertEqual(original["questionContract"], envelope["researchCases"][0]["questionContract"])
        store.save.reset_mock(); q["revision"] += 1
        self.assertEqual("case-changed-after-capture", record_question_evidence(store, Mock(), job, selected, trace)["deferred"][0]["reason"])
        store.save.assert_not_called()

    def assert_retry_identity_ignores_internal_progress(self):
        p = business_packet(); q = question(p)
        key = retry_key(p, [q])
        altered = deepcopy(q)
        altered.update(revision=99, status="blocked", nextCheckAt="2099-01-01", reason="새 표현",
            evidenceProgress={"missingRequirements": ["같은 항목의 새 표현"]}, researchRequest={"queryTerms": ["새 검색어"]},
            lastResearch={"taskId": "failed", "result": {"status": "failed"}})
        altered["latestSourceAnswer"]["text"] = "같은 자료의 재서술"
        altered["latestSourceAnswer"]["sources"][0].update(evidenceId="new-fetch-id", inputFingerprint="new-fetch-fingerprint")
        self.assertEqual(key, retry_key(p, [altered]))
        changed = deepcopy(altered); changed["questionContract"]["period"] = "다른 보고 기간"
        self.assertNotEqual(key, retry_key(p, [changed]))
        changed = deepcopy(altered); changed["latestSourceAnswer"]["sources"][0]["materialFingerprint"] = "new-body"
        self.assertNotEqual(key, retry_key(p, [changed]))
        changed = deepcopy(altered); changed["answerResolution"]["status"] = "answered"
        self.assertNotEqual(key, retry_key(p, [changed]))

    def assert_reviewed_internal_research_skips_paid_stages_until_source_changes(self):
        from test_ai_control import AIControlTests, PACKET, select_evidence
        for status in ("research-reviewed", "research-only", "research-review-required"):
            service, store, planner = AIControlTests().runner()
            service.read_planner = Mock(side_effect=AssertionError("unchanged input must not read"))
            store.last_processing_outcome.return_value = disposition(PACKET, retry_key(PACKET, []), status, [])
            self.assertEqual("unchanged-wait", service.run_once()["status"])
            planner.assert_not_called(); service.read_planner.assert_not_called()
            store.save_execution_input.assert_not_called()
            self.assertEqual(["observe"], [child["capability"] for child in store.complete.call_args.args[2]])
            source = deepcopy(PACKET); source["facts"][0]["currentPrice"] += 1
            source["facts"], source["coverage"] = select_evidence(source["facts"])
            service.evidence.return_value = source
            service.read_planner = None
            self.assertEqual("completed", service.run_once()["status"])
            planner.assert_called_once()
