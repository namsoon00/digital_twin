import unittest
from types import SimpleNamespace

from digital_twin.application.investment_reasoning.orchestrator import (
    InvestmentReasoningOrchestrator,
)
from digital_twin.domain.investment_reasoning.subject_case import (
    ABSTAIN,
    SUBJECT_ABSTAINED,
    SUBJECT_READY,
)


class Repository:
    def save(self, value):
        return value


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


if __name__ == "__main__":
    unittest.main()
