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

    def test_source_gap_wait_and_plan_are_atomic_idempotent_and_do_not_publish_an_opinion(self):
        from test_insight_source_progress import missing_sources
        from digital_twin.modules.ai_orchestration.domain import insight_progress
        from digital_twin.modules.ai_orchestration.domain.observation_wait import disposition
        from digital_twin.modules.ai_orchestration.application.control import AIControlService
        self.control.runtime_settings.update(aiControlBudgetEnabled="false", aiTokenBudgetEnabled="false")
        subject = self.storage.subject
        previous = missing_sources(); p = previous["input"]; p.update(subject)
        state = insight_progress.assessment(p, previous)
        source_job = insight_progress.research_job(subject, state, stamp())
        wait = disposition(p, "unchanged-source", "data-wait", [n["requirement"] for n in state["needs"]])
        wait.update(sourceProgress=state, reasonCode="insight-source-gaps", authorCalled=False)
        # A later observation may report this same gap, but the durable source
        # planning task remains singular even after completion, scoped by owner.
        with self.control.transaction() as c:
            for key in ("gap-observation-1", "gap-observation-2"):
                self.control.insert(c, {**subject, "taskId": key, "capability": "market-observe", "priority": 10})
        service = AIControlService(self.control, lambda: [subject], None, None, None, None)
        first = self.control.claim()
        original = self.control.insert
        def failed_insert(connection, job):
            original(connection, job)
            if job["capability"] == "research-plan":
                raise RuntimeError("rollback source scheduling")
        self.control.insert = failed_insert
        with self.assertRaisesRegex(RuntimeError, "rollback source scheduling"):
            service.complete_wait(first, p, wait, [])
        self.control.insert = original
        with self.control.connect() as c:
            self.assertIsNone(c.execute("SELECT task_id FROM ai_control_tasks WHERE task_id=%s", (source_job["taskId"],)).fetchone())
            self.assertEqual("processing", c.execute("SELECT status FROM ai_control_tasks WHERE task_id=%s", (first["taskId"],)).fetchone()["status"])
        self.assertEqual("awaiting-evidence", service.complete_wait(first, p, wait, [])["status"])
        self.assertEqual("awaiting-evidence", service.complete_wait(self.control.claim(), p, wait, [])["status"])
        with self.control.connect() as c:
            row = c.execute("SELECT payload_json FROM ai_control_tasks WHERE task_id=%s", (source_job["taskId"],)).fetchone()
            self.assertTrue(insight_progress.source_gap_request(json.loads(row["payload_json"])))
            self.assertEqual(1, c.execute("SELECT COUNT(*) n FROM ai_control_tasks WHERE capability='research-plan'").fetchone()["n"])
            self.assertEqual(0, c.execute("SELECT COUNT(*) n FROM ai_opinion_records").fetchone()["n"])
            self.assertEqual(0, c.execute("SELECT COUNT(*) n FROM notification_jobs WHERE message_type='aiObservation'").fetchone()["n"])
        other = insight_progress.research_job({**subject, "worldId": "another-world"}, state, stamp())
        self.assertNotEqual(source_job["taskId"], other["taskId"])

    def test_opinion_read_receipts_v21_and_v22_survive_progress_storage_and_reject_tampering(self):
        from test_reliable_opinion_completion import packet
        from test_efficient_retrieval import decision
        from digital_twin.modules.ai_orchestration.application.working_retrieval import retrieve_working_evidence
        from digital_twin.modules.ai_orchestration.domain import reliable_retrieval, opinion_retrieval
        from digital_twin.modules.reasoning.contracts import ObservationEvidenceSession, quote_clock_assessment
        from digital_twin.infrastructure.transactions.ai_control_research_knowledge import AIControlResearchKnowledge
        with self.control.transaction() as connection:
            self.control.insert(connection, {**self.storage.subject, "capability": "market-observe", "taskId": "opinion-read-proof"})
        job = self.control.claim()
        self.control.runtime_settings.update(aiDailyTokenBudget=10000000, aiControlDailyCallBudget=32)
        for protocol in (opinion_retrieval, reliable_retrieval):
            value = packet(); value.update(self.storage.subject, taskId=job["taskId"])
            if protocol is opinion_retrieval:
                value.pop("retrievalReliabilityPolicy")
            value["sourceSnapshots"] = {value["worldId"]: value["sourceSnapshotId"]}
            for fact in value["facts"]:
                fact.update(symbol=value["symbol"], sourceWorldId=value["worldId"], sourceSnapshotId=value["sourceSnapshotId"])
                if fact.get("accountId"): fact["accountId"] = value["accountId"]
            value["quoteAssessment"] = quote_clock_assessment(value["facts"], value["capturedAt"])
            def save(env):
                key = self.control.save_execution_input(job, env)
                call = self.control.begin_call("independent-observation", env["promptHash"], job["taskId"], key)
                self.control.finish_call(call)
                return key
            def choose(env, _):
                if not env["retrievalContext"]["trace"]:
                    path = next(p for p in env["retrievalContext"]["readableContextPaths"] if p.endswith("/metricProvenance"))
                    return decision("read", [{"tool": "read_context", "path": path}])
                return {**decision("defer"), "missingEvidence": ["비교 기간 원문 주석이 필요합니다."], "blocker": "required-source"}
            final, _, _, trace = retrieve_working_evidence(ObservationEvidenceSession(value, value["facts"]),
                value, [], [], choose, save, 262144, max_rounds=2,
                record_round=lambda step: self.control.save_retrieval_round(job, step))
            self.assertEqual(protocol.VERSION, final["retrieval"]["version"])
            result = {"input": final, "retrievalInputIds": [step["inputId"] for step in trace]}
            with self.control.connect() as connection:
                self.assertEqual(trace, AIControlResearchKnowledge.read_proof(connection, job, result))
                forged = deepcopy(result); forged["input"]["retrieval"]["traceHash"] = "changed"
                with self.assertRaises(ValueError): AIControlResearchKnowledge.read_proof(connection, job, forged)
                forged = deepcopy(result); forged["input"]["opinionMemoryPolicy"] = "changed"
                with self.assertRaises(ValueError): AIControlResearchKnowledge.read_proof(connection, job, forged)

    def draft(self, reliable=False):
        from digital_twin.modules.ai_orchestration.domain.insight_quality import local_quality
        p, memories, trace, case, raw = fixture()
        self.control.seed(self.storage.subject); job = self.control.claim(); p["taskId"] = job["taskId"]
        case.update(createdAt=stamp(), kind="question", researchAttempts=6,
                    completionCriterion=case["questionContract"]["answerCriterion"], origin={key: "original"
                        for key in ("taskId", "executionInputId", "capturedAt", "summary", "hypothesis", "counterEvidence", "quality")})
        with self.agenda.transaction() as connection: self.agenda.save(connection, case, "initial", "created")
        memories[0]["revision"] = case["revision"]; p["questionEvidenceIndex"][0]["revision"] = case["revision"]
        if reliable:
            from digital_twin.modules.ai_orchestration.domain import reliable_retrieval, gap_read_progress
            p["retrievalReliabilityPolicy"] = reliable_retrieval.MARKER
            p["gapReadWork"] = {"version": "bounded-question-gap-reads-v2", "requirements": [{
                "caseId": case["caseId"], "requirementId": "original-gap", "requirementIndex": 0,
                "requirement": "보고 기간의 원문을 확인합니다.", "nextAction": "연결된 원문을 읽습니다."}],
                "reads": [{"requirementIds": ["original-gap"], "factIds": ["official-document"],
                    "request": {"tool": "read_document", "factId": "official-document"}, "status": "read-needs-assessment"}]}
            p["gapReadProgress"] = gap_read_progress.coverage(p)
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
        job, case, result = self.draft(reliable=True)
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
        self.assertEqual("partial", saved["gapReadProgress"]["answerStatus"])
        self.assertEqual(saved["missingRequirements"], saved["gapReadProgress"]["remainingRequirements"])
        self.assertEqual(saved["researchReviewInputId"], saved["gapReadProgress"]["reviewInputId"])
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
