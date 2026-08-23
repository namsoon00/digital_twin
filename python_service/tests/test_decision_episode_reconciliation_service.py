import unittest

from digital_twin.application.decision_episode_reconciliation_service import (
    DecisionEpisodeReconciliationService,
)
from digital_twin.domain.notifications import NotificationJob


def episode_payload(episode_id="decision-episode:recover-1"):
    return {
        "episodeId": episode_id,
        "accountId": "main",
        "symbol": "035420",
        "subjectName": "NAVER",
        "question": {
            "questionId": "question:recover-1",
            "text": "현재 보유 판단은 무엇입니까?",
            "subjectSymbol": "035420",
            "subjectName": "NAVER",
            "accountId": "main",
        },
        "hypothesisSet": {
            "hypothesisSetId": "hypothesis-set:recover-1",
            "subjectSymbol": "035420",
            "questionId": "question:recover-1",
            "comparisonRequired": False,
            "minimumComparisonCount": 0,
            "hypotheses": [],
        },
        "action": "HOLD",
        "reviewLevel": "observe",
        "dataState": "sufficient",
        "validationState": "ready",
        "sourceAboxSnapshotId": "abox:recover-1",
        "inferenceGenerationId": "generation:recover-1",
        "decidedAt": "2026-08-21T06:14:00Z",
        "factsAtDecision": {"currentPrice": 223500},
    }


class FakeDecisionStore:
    def __init__(self):
        self.rows = {}

    def get(self, episode_id):
        return self.rows.get(episode_id)

    def save(self, episode):
        self.rows[episode.episode_id] = episode
        return episode


class FakeNotificationStore:
    def __init__(self, jobs):
        self.jobs = list(jobs)

    def recent_page(self, limit=40, offset=0, message_type="", status="", **_kwargs):
        rows = [
            job for job in self.jobs
            if (not message_type or job.message_type == message_type)
            and (not status or job.status == status)
        ]
        return rows[offset:offset + limit], len(rows)


class DecisionEpisodeReconciliationServiceTests(unittest.TestCase):
    def test_recovers_exact_episode_payload_without_recomputing_current_data(self):
        job = NotificationJob.create(
            "판단 알림",
            message_type="investmentInsight",
            context={"investmentDecisionEpisode": episode_payload()},
        )
        job.status = "done"
        store = FakeDecisionStore()

        result = DecisionEpisodeReconciliationService(
            store,
            FakeNotificationStore([job]),
        ).reconcile()

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["counts"]["recovered"])
        recovered = store.get("decision-episode:recover-1")
        self.assertEqual(223500, recovered.facts_at_decision["currentPrice"])
        self.assertEqual(
            "notification-audit-payload",
            recovered.facts_at_decision["reconciliationSource"],
        )

    def test_preview_does_not_write_and_incomplete_contract_is_skipped(self):
        valid = NotificationJob.create(
            "판단 알림",
            message_type="investmentInsight",
            context={"investmentDecisionEpisode": episode_payload()},
        )
        valid.status = "sent"
        incomplete_payload = episode_payload("decision-episode:incomplete")
        incomplete_payload["sourceAboxSnapshotId"] = ""
        incomplete = NotificationJob.create(
            "불완전 알림",
            message_type="investmentInsight",
            context={"investmentDecisionEpisode": incomplete_payload},
        )
        incomplete.status = "sent"
        store = FakeDecisionStore()

        result = DecisionEpisodeReconciliationService(
            store,
            FakeNotificationStore([valid, incomplete]),
        ).reconcile(dry_run=True)

        self.assertEqual("preview", result["status"])
        self.assertEqual(1, result["counts"]["recoverable"])
        self.assertEqual(1, result["counts"]["incompleteContract"])
        self.assertEqual({}, store.rows)


if __name__ == "__main__":
    unittest.main()
