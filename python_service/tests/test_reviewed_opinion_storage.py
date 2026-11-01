"""Isolated MySQL: approved projections, scoped archive and atomic completion."""
from copy import deepcopy
import json
import os
import unittest

from digital_twin.modules.ai_orchestration.domain import opinion_memory, question_assessment, research_review
from digital_twin.modules.ai_orchestration.domain.planning import stamp
from digital_twin.modules.ai_orchestration.domain.execution_input import freeze_execution_input
from test_reviewed_opinion_memory import fixture, approved


@unittest.skipUnless(os.environ.get("MYSQL_DATABASE") == "orbit_alpha_test", "isolated MySQL required")
class ReviewedOpinionStorageTests(unittest.TestCase):
    def setUp(self):
        from test_ai_brain_agenda import BrainAgendaStorageTests
        self.storage = BrainAgendaStorageTests(); self.storage.setUp(); self.addCleanup(self.storage.tearDown)
        self.control, self.agenda = self.storage.control, self.storage.brain

    def draft(self):
        from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
        p, memories, trace, case, raw = fixture()
        self.control.seed(self.storage.subject); job = self.control.claim(); p["taskId"] = job["taskId"]
        case.update(createdAt=stamp(), kind="question", researchAttempts=6,
                    completionCriterion=case["questionContract"]["answerCriterion"], origin={key: "original"
                        for key in ("taskId", "executionInputId", "capturedAt", "summary", "hypothesis", "counterEvidence", "quality")})
        with self.agenda.transaction() as connection: self.agenda.save(connection, case, "initial", "created")
        memories[0]["revision"] = case["revision"]; p["questionEvidenceIndex"][0]["revision"] = case["revision"]
        author = freeze_execution_input(p, [], memories, retrieval_trace=trace)
        author_id = self.control.save_execution_input(job, author)
        call = self.control.begin_call("independent-observation", author["promptHash"], job["taskId"], author_id)
        self.control.finish_call(call)
        result = approved(p, memories, raw); result["executionInputId"] = author_id
        result["quality"] = local_quality(result)
        frozen = research_review.freeze(result, author["researchResults"], 262144)
        review_id = self.control.save_execution_input(job, frozen)
        call = self.control.begin_call("independent-observation", frozen["promptHash"], job["taskId"], review_id)
        self.control.finish_call(call)
        result["researchQuality"] = research_review.accept(result, result["researchQuality"]["review"], review_id)
        return job, case, result

    def test_approved_question_is_archived_in_same_transaction_and_rejected_draft_does_not_replace_it(self):
        job, case, result = self.draft()
        original = self.control.record_opinions
        def fail(connection, job, result):
            original(connection, job, result)
            raise RuntimeError("rollback after archive")
        self.control.record_opinions = fail
        with self.assertRaisesRegex(RuntimeError, "rollback after archive"): self.control.complete(job, result, [])
        with self.control.connect() as c:
            self.assertEqual(0, c.execute("SELECT COUNT(*) n FROM ai_opinion_records").fetchone()["n"])
            self.assertEqual("processing", c.execute("SELECT status FROM ai_control_tasks WHERE task_id=%s", (job["taskId"],)).fetchone()["status"])
        self.control.record_opinions = original
        self.assertTrue(self.control.complete(job, result, []))
        self.assertFalse(self.control.complete(job, result, []))
        with self.control.connect() as c:
            rows = c.execute("SELECT payload_json FROM ai_opinion_records WHERE case_id=%s", (case["caseId"],)).fetchall()
        self.assertEqual(1, len(rows)); saved = json.loads(rows[0]["payload_json"])
        self.assertEqual("partial", saved["answerStatus"])
        self.assertEqual(result["executionInputId"], saved["executionInputId"])
        self.assertEqual("new", saved["judgmentImpact"]["opinionChange"])
        # Several newer rejected completions must not occupy the LIMIT slots.
        with self.control.transaction() as c:
            for i in range(5):
                key = "rejected-" + str(i)
                self.control.insert(c, {**job, "taskId": key})
                c.execute("UPDATE ai_control_tasks SET status='completed',updated_at='2090',result_json=%s WHERE task_id=%s",
                    (json.dumps({"summary": "rejected", "input": result["input"], "quality": {"status": "rejected"},
                                 "researchQuality": {"status": "partially-accepted", "reviewInputId": "review", "admissions": {"assessment": False, "knowledge": True}}}), key))
        memory = self.control.reviewed_memory(job["accountId"], job["symbol"], job["worldId"])
        self.assertEqual(result["executionInputId"], memory[0]["executionInputId"])
        self.assertEqual([], self.control.reviewed_memory(job["accountId"], job["symbol"], "another-world"))
        found = self.control.search_opinions(job["accountId"], job["symbol"], job["worldId"], stamp(), "매출")
        self.assertEqual(saved["recordId"], found["records"][0]["recordId"])
        self.assertEqual([], self.control.search_opinions(job["accountId"], job["symbol"], job["worldId"], "2000", "매출")["records"])
        self.assertEqual([], self.control.search_opinions("other-account", job["symbol"], job["worldId"], stamp(), "매출")["records"])

    def test_legacy_approved_answers_and_older_opinions_are_searchable_without_rewriting_them(self):
        job, case, result = self.draft()
        self.assertTrue(self.control.complete(job, result, []))
        with self.control.transaction() as c:
            original = c.execute("SELECT result_json FROM ai_control_tasks WHERE task_id=%s", (job["taskId"],)).fetchone()["result_json"]
            c.execute("DELETE FROM ai_opinion_records")
        found = self.control.search_opinions(job["accountId"], job["symbol"], job["worldId"], stamp(), "매출")
        self.assertEqual("legacy-reviewed-question-page", found["coverage"])
        self.assertEqual(case["caseId"], found["records"][0]["caseId"])
        historical = self.control.search_opinions(job["accountId"], job["symbol"], job["worldId"], stamp(), "중기", 200000)
        self.assertTrue(historical["records"])
        self.assertEqual(result["executionInputId"], historical["records"][0]["executionInputId"])
        with self.control.connect() as c:
            self.assertEqual(original, c.execute("SELECT result_json FROM ai_control_tasks WHERE task_id=%s", (job["taskId"],)).fetchone()["result_json"])
            self.assertEqual(0, c.execute("SELECT COUNT(*) n FROM ai_opinion_records").fetchone()["n"])


if __name__ == "__main__": unittest.main()
