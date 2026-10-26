"""A paid documentary review needs new evidence, not another collection round."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from test_question_research_progress import TARGET, EvidenceStore, Gateway, RunStore, task, evidence
from digital_twin.modules.decisions.contracts import InvestmentQuestion
from digital_twin.modules.news_intelligence.application.hypothesis_research_planner_service import HypothesisResearchPlanningService
from digital_twin.modules.news_intelligence.application.investment_research_orchestration_service import InvestmentResearchOrchestrationService
from digital_twin.modules.ai_orchestration.domain.research_return import research_return


class PartialAdvisor:
    def __init__(self, mode="partial"):
        self.calls, self.mode = [], mode

    def plan(self, context):
        self.calls.append(deepcopy(context))
        if self.mode == "failed":
            raise RuntimeError("fixture transport failure")
        return {"taskReviews": [{"taskId": row["taskId"], "assessmentFingerprint": row["assessmentFingerprint"],
            "status": "partial", "evidenceIds": row["candidateEvidenceIds"] if self.mode != "invalid" else ["unread-source"],
            "counterEvidenceIds": row["candidateEvidenceIds"], "reason": "현금 수치는 확인했으나 전년 비교 원문이 부족합니다.",
            "missingRequirements": ["전년 비교 원문"]} for row in context["researchProgress"]["tasks"]]}


class ResearchReviewReuseTests(unittest.TestCase):
    def run_review(self, advisor, cached, collected=(), resume=None, contract=None, account="test-account", run_id="same-run"):
        question = InvestmentQuestion.create("현금흐름 비교", subject_symbol="AAPL", account_id=account, source="ai-control")
        store = RunStore()
        service = InvestmentResearchOrchestrationService(EvidenceStore(cached), Gateway(collected), research_store=store,
            hypothesis_research_planner=HypothesisResearchPlanningService(advisor))
        brain = {"documentaryReviewOnly": True, "sourceReviewResume": resume,
                 "researchPlan": {"planId": "original-plan", "tasks": [contract or task()], "unresolvedQuestions": [question.text]}}
        return service.run(question, TARGET, brain, account_id=account, run_id=run_id), store

    def test_unchanged_partial_review_skips_paid_call_and_retains_gaps_counters(self):
        advisor = PartialAdvisor(); source = evidence()
        run, store = self.run_review(advisor, [source])
        self.assertEqual(1, len(advisor.calls))
        self.assertEqual("no-new-research-path", run.stop_reason)
        self.assertEqual("partial", run.task_assessments[0]["semanticReviewState"])
        self.assertEqual([source.evidence_id], run.task_assessments[0]["counterEvidenceIds"])
        self.assertIn("전년 비교 원문", run.task_assessments[0]["missingRequirements"])
        result = research_return(run.to_dict(), run.run_id, "cash-flow", {})
        self.assertEqual("partial", result["documentaryAnswer"]["status"])
        self.assertEqual(1, result["planningReuse"]["skippedPlanningCalls"])
        self.assertNotIn("taskReviews", run.executed_plan)
        self.assertTrue(store.rows[-1].round_history[-1]["planningAudit"]["reuse"]["modelCallSkipped"])

    def test_same_run_checkpoint_reuses_partial_but_new_contract_account_or_run_does_not(self):
        source = evidence(); original = PartialAdvisor()
        run, _ = self.run_review(original, [source])
        checkpoint = run.executed_plan["documentaryReviewReuse"]
        again = PartialAdvisor(); resumed, _ = self.run_review(again, [source], resume=checkpoint)
        self.assertEqual(0, len(again.calls))
        self.assertEqual(run.task_assessments[0]["reason"], resumed.task_assessments[0]["reason"])
        for kwargs in ({"contract": {**task(), "requiredPeriodEnds": ["2025-12-31"]}},
                       {"account": "different-account"}, {"run_id": "different-run"},
                       {"contract": {**task(), "maxAgeMinutes": 200}}):
            advisor = PartialAdvisor(); self.run_review(advisor, [source], resume=checkpoint, **kwargs)
            self.assertEqual(1, len(advisor.calls), kwargs)

    def test_new_or_revised_evidence_reopens_review_and_expired_evidence_is_not_answer(self):
        source = evidence(); advisor = PartialAdvisor()
        corrected = deepcopy(source); corrected.summary = "A corrected cash flow statement."
        run, _ = self.run_review(advisor, [source], collected=[[corrected]])
        self.assertEqual(2, len(advisor.calls))
        checkpoint = run.executed_plan["documentaryReviewReuse"]
        old = deepcopy(corrected)
        old.observed_at = old.published_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        again = PartialAdvisor(); expired, _ = self.run_review(again, [old], resume=checkpoint)
        self.assertEqual(0, len(again.calls))  # No eligible evidence is deterministically awaiting collection.
        self.assertEqual("unreviewed", expired.task_assessments[0]["semanticReviewState"])
        self.assertEqual([], expired.task_assessments[0]["resultEvidenceIds"])

    def test_invalid_citations_get_one_repair_and_never_become_a_valid_cached_answer(self):
        source = evidence(); advisor = PartialAdvisor("invalid")
        run, _ = self.run_review(advisor, [source])
        self.assertEqual(2, len(advisor.calls))
        self.assertTrue(run.task_assessments[0]["reviewRejected"])
        self.assertEqual([], run.task_assessments[0]["resultEvidenceIds"])
        again = PartialAdvisor("invalid")
        resumed, _ = self.run_review(again, [source], resume=run.executed_plan["documentaryReviewReuse"])
        self.assertEqual(0, len(again.calls))
        self.assertTrue(resumed.task_assessments[0]["reviewRejected"])
        self.assertNotEqual("addressed", resumed.task_assessments[0]["status"])
        changed = deepcopy(source); changed.summary = "A revised report with new data."
        newer = PartialAdvisor("invalid")
        self.run_review(newer, [changed], resume=run.executed_plan["documentaryReviewReuse"])
        self.assertEqual(1, len(newer.calls))  # The original run has already spent its one citation repair.

    def test_transport_failure_and_tampered_checkpoint_are_not_valid_review_reuse(self):
        source = evidence(); failed = PartialAdvisor("failed")
        run, _ = self.run_review(failed, [source])
        self.assertEqual([], run.executed_plan["documentaryReviewReuse"]["entries"])
        valid, _ = self.run_review(PartialAdvisor(), [source])
        checkpoint = deepcopy(valid.executed_plan["documentaryReviewReuse"])
        checkpoint["entries"][0]["taskReviews"][0]["reason"] = "forged answer"
        again = PartialAdvisor(); self.run_review(again, [source], resume=checkpoint)
        self.assertEqual(1, len(again.calls))
