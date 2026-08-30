import json
import unittest
from types import SimpleNamespace

from digital_twin.infrastructure.mysql_subject_decision_cases import MySQLSubjectDecisionCaseStore


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, existing=None):
        self.existing = existing
        self.inserts = []

    def execute(self, query, params=()):
        if query.lstrip().startswith("SELECT entry_fingerprint"):
            return Cursor(self.existing)
        if "INSERT INTO investment_decision_audit_entries" in query:
            self.inserts.append(params)
        return Cursor()


class Serializable(SimpleNamespace):
    def to_dict(self):
        return dict(self.payload)


def subject_case():
    return SimpleNamespace(
        subject_case_id="subject-1",
        batch_case_id="batch-1",
        request_id="request-1",
        deployment_id="v2",
        release_fingerprint="release-fingerprint",
        account_id="account-1",
        symbol="NVDA",
        source_abox_snapshot_id="abox-1",
        inference_generation_id="generation-1",
        synthesis=SimpleNamespace(synthesis_id="synthesis-1", graph_candidate_action="BUY"),
        candidate_set=SimpleNamespace(
            candidate_set_id="candidate-1",
            fingerprint="candidate-fingerprint",
            eligible_hypothesis_ids=("hypothesis-1",),
        ),
        stage="VALIDATED",
        version=4,
        ai_request_id="ai-request-1",
        ai_judgment=Serializable(payload={"action": "BUY", "resultId": "result-1"}),
        final_decision=Serializable(payload={"action": "BUY"}, action="BUY"),
        abstention=None,
        publication=Serializable(
            payload={"outcomeKind": "FINAL_DECISION"},
            outcome_kind="FINAL_DECISION",
            fingerprint="publication-fingerprint",
        ),
        notification_job_id="notification-1",
        created_at="2026-08-30T00:00:00Z",
        updated_at="2026-08-30T00:01:00Z",
    )


class DecisionAuditEntryTests(unittest.TestCase):
    def test_compact_audit_entry_preserves_candidate_ai_and_final_decision(self):
        connection = Connection()

        MySQLSubjectDecisionCaseStore.save_audit_entry_with_connection(
            connection,
            subject_case(),
        )

        self.assertEqual(1, len(connection.inserts))
        params = connection.inserts[0]
        payload = json.loads(params[10])
        self.assertEqual("candidate-fingerprint", payload["candidateFingerprint"])
        self.assertEqual("completed", payload["aiStatus"])
        self.assertEqual("BUY", payload["finalDecision"]["action"])
        self.assertEqual("FINAL_DECISION", params[4])

    def test_existing_audit_version_cannot_be_rewritten(self):
        connection = Connection(existing={"entry_fingerprint": "different"})

        with self.assertRaisesRegex(ValueError, "Immutable decision audit fingerprint mismatch"):
            MySQLSubjectDecisionCaseStore.save_audit_entry_with_connection(
                connection,
                subject_case(),
            )


if __name__ == "__main__":
    unittest.main()
