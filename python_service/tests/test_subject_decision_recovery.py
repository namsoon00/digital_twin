import unittest
from types import SimpleNamespace

from digital_twin.application.investment_reasoning.orchestrator import (
    InvestmentReasoningOrchestrator,
)
from digital_twin.domain.investment_reasoning.subject_case import (
    ABSTAIN,
    OBSERVATION,
    SUBJECT_ABSTAINED,
    SUBJECT_OBSERVATION,
    SUBJECT_READY,
)
from digital_twin.domain.investment_reasoning import CASE_DECISION_SYNTHESIZED


class Repository:
    def __init__(self, item=None):
        self.item = item

    def save(self, value):
        self.item = value
        return value

    def get(self, case_id):
        if self.item is not None and self.item.case_id == case_id:
            return self.item
        return None


class Candidate:
    fingerprint = "candidate:fingerprint"


class StaleCase:
    def __init__(self):
        self.subject_case_id = "subject:stale"
        self.stage = SUBJECT_READY
        self.publication = None
        self.abstention = None
        self.candidate_set = Candidate()
        self.ai_judgment = None
        self.final_decision = None
        self.notification_job_id = ""
        self.delivery_state = "not-requested"

    def mark(self, stage, reason="", details=None):
        del reason, details
        self.stage = stage

    def mark_delivery(self, state, reason=""):
        del reason
        self.delivery_state = state


class SubjectStore:
    def __init__(self, stale_case):
        self.stale_case = stale_case
        self.saved = []

    def stale_ready(self, max_age_minutes=30, limit=100):
        del max_age_minutes, limit
        return [self.stale_case]

    def get(self, subject_case_id):
        return self.stale_case if subject_case_id == self.stale_case.subject_case_id else None

    def save(self, subject_case):
        self.saved.append(subject_case)
        return subject_case


class SubjectDecisionRecoveryTests(unittest.TestCase):
    def test_batch_context_suppression_resolves_and_closes_the_scoped_subject(self):
        subject = StaleCase()
        subject.subject_case_id = "subject:scoped"
        subject.batch_case_id = "case:batch"
        subject.account_id = "default"
        subject.symbol = "000660"
        subject.final_decision = object()

        class ScopedSubjectStore(SubjectStore):
            def for_batch(self, batch_case_id):
                return [subject] if batch_case_id == subject.batch_case_id else []

        store = ScopedSubjectStore(subject)
        orchestrator = InvestmentReasoningOrchestrator(
            Repository(),
            subject_case_repository=store,
        )

        orchestrator.notification_suppressed({
            "investmentReasoningCaseId": "case:batch",
            "accountId": "default",
            "rawSymbol": "000660",
            "notificationJobId": "notification:1",
        }, "unchanged graph")

        self.assertEqual("suppressed", subject.delivery_state)
        self.assertEqual("notification:1", subject.notification_job_id)

    def test_stale_ready_candidate_becomes_explicit_abstention(self):
        stale_case = StaleCase()
        store = SubjectStore(stale_case)
        orchestrator = InvestmentReasoningOrchestrator(
            Repository(),
            subject_case_repository=store,
        )

        recovered = orchestrator.recover_stale_subject_cases(30, 10)

        self.assertEqual(1, len(recovered))
        self.assertEqual(SUBJECT_ABSTAINED, stale_case.stage)
        self.assertEqual("stale-ready-ai-handoff-missed", stale_case.abstention.reason_code)
        self.assertEqual(ABSTAIN, stale_case.publication.outcome_kind)
        self.assertEqual([stale_case], store.saved)

        orchestrator.notification_published({
            "investmentSubjectDecisionCaseId": stale_case.subject_case_id,
        })
        self.assertEqual(SUBJECT_ABSTAINED, stale_case.stage)
        self.assertEqual("delivered", stale_case.delivery_state)

        observation = StaleCase()
        observation.subject_case_id = "subject:mstr"
        observation.batch_case_id = "case:mixed"
        observation.symbol = "MSTR"
        actionable = StaleCase()
        actionable.subject_case_id = "subject:nvda"
        actionable.batch_case_id = "case:mixed"
        actionable.symbol = "NVDA"

        class MixedSubjectStore:
            def __init__(self, values):
                self.values = values

            def get(self, subject_case_id):
                return next((item for item in self.values if item.subject_case_id == subject_case_id), None)

            def for_batch(self, batch_case_id):
                return [item for item in self.values if item.batch_case_id == batch_case_id]

            def save(self, subject_case):
                return subject_case

        reasoning_case = SimpleNamespace(
            case_id="case:mixed",
            stage=CASE_DECISION_SYNTHESIZED,
            inference_result=SimpleNamespace(trace_complete=True),
            decision_syntheses=(
                SimpleNamespace(
                    symbol="MSTR",
                    graph_candidate_action="NO_ACTION",
                    eligible_hypothesis_ids=(),
                ),
                SimpleNamespace(
                    symbol="NVDA",
                    graph_candidate_action="HOLD",
                    eligible_hypothesis_ids=("hypothesis:nvda",),
                ),
            ),
        )
        orchestrator = InvestmentReasoningOrchestrator(
            Repository(reasoning_case),
            subject_case_repository=MixedSubjectStore([observation, actionable]),
        )

        orchestrator.context_observation_validated(
            reasoning_case.case_id,
            subject_symbols=["MSTR"],
        )

        self.assertEqual(SUBJECT_OBSERVATION, observation.stage)
        self.assertEqual(OBSERVATION, observation.publication.outcome_kind)
        self.assertEqual(SUBJECT_READY, actionable.stage)
        self.assertIsNone(actionable.publication)


if __name__ == "__main__":
    unittest.main()
