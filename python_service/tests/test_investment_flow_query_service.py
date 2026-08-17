import unittest

from digital_twin.application.investment_flow_query_service import InvestmentFlowQueryService
from digital_twin.domain.investment_flow import decision_flow_projection, investment_flow_id


def episode(
    episode_id="episode:1",
    *,
    account_id="account-1",
    symbol="AAPL",
    action="HOLD",
    validation_state="ready",
    data_state="sufficient",
    decided_at="2026-08-18T01:00:00Z",
    selected=True,
):
    selected_id = "hypothesis:aapl-demand" if selected else ""
    return {
        "episodeId": episode_id,
        "accountId": account_id,
        "symbol": symbol,
        "subjectName": "Apple",
        "action": action,
        "reviewLevel": "observe",
        "dataState": data_state,
        "validationState": validation_state,
        "sourceAboxSnapshotId": "abox:1" if data_state == "sufficient" else "",
        "inferenceGenerationId": "generation:1" if selected else "",
        "selectedHypothesisId": selected_id,
        "evidenceIds": ["evidence:1"] if selected else [],
        "decisionSummary": "AI 수요 가설과 반대 근거를 비교했습니다.",
        "decidedAt": decided_at,
        "hypothesisSet": {
            "hypotheses": [{
                "hypothesisId": selected_id,
                "claim": "AI 수요가 Apple 실적을 지지한다.",
                "supportingRuleIds": ["rule:ai-demand"],
            }] if selected else [],
        },
    }


class FakeDecisionStore:
    def __init__(self, rows):
        self.rows = list(rows)

    def list(self, account_id="", symbol="", limit=50):
        rows = self.rows
        if account_id:
            rows = [row for row in rows if row["accountId"] == account_id]
        if symbol:
            rows = [row for row in rows if row["symbol"] == symbol]
        return rows[:limit]

    def get(self, episode_id):
        return next((row for row in self.rows if row["episodeId"] == episode_id), None)


class FakeNotificationStore:
    def __init__(self, rows):
        self.rows = list(rows)

    def jobs_for_decision_episodes(self, episode_ids, limit=200):
        return [row for row in self.rows if row.get("decisionEpisodeId") in set(episode_ids)][:limit]


class InvestmentFlowQueryServiceTests(unittest.TestCase):
    def test_projection_keeps_the_complete_judgement_chain(self):
        result = decision_flow_projection(
            episode(),
            [{"jobId": "job:1", "decisionEpisodeId": "episode:1", "status": "done"}],
        )

        self.assertEqual("pass", result["validationState"])
        self.assertEqual("pass", result["readinessState"])
        self.assertEqual(
            ["source", "evidence", "relation", "hypothesis", "validation", "inference", "decision", "notification"],
            [item["id"] for item in result["stages"]],
        )
        self.assertEqual(investment_flow_id("account-1", "AAPL", "episode:1"), result["flowId"])

    def test_blocked_projection_explains_the_first_missing_stage(self):
        result = decision_flow_projection(
            episode(validation_state="blocked", data_state="insufficient", selected=False),
        )

        self.assertEqual("blocked", result["readinessState"])
        self.assertEqual("source", result["blockingStage"])
        self.assertIn("최신 데이터", result["nextAction"])

    def test_summary_keeps_only_the_latest_episode_per_account_and_symbol(self):
        latest = episode(episode_id="episode:new", action="BUY", decided_at="2026-08-18T02:00:00Z")
        older = episode(episode_id="episode:old", action="HOLD", decided_at="2026-08-18T01:00:00Z")
        service = InvestmentFlowQueryService(
            FakeDecisionStore([latest, older]),
            FakeNotificationStore([
                {"jobId": "job:new", "decisionEpisodeId": "episode:new", "status": "done"},
            ]),
        )

        result = service.summary(account_id="account-1")

        self.assertEqual(1, result["count"])
        self.assertEqual("episode:new", result["items"][0]["episodeId"])
        self.assertEqual("BUY", result["items"][0]["action"])
        self.assertEqual(0, result["summary"]["attentionRequired"])

    def test_detail_returns_nodes_links_and_validation_gaps(self):
        row = episode(validation_state="conditional", data_state="partial", selected=False)
        service = InvestmentFlowQueryService(FakeDecisionStore([row]), FakeNotificationStore([]))

        result = service.detail("episode:1")

        self.assertEqual("ok", result["status"])
        self.assertEqual(8, len(result["lineage"]["nodes"]))
        self.assertEqual(7, len(result["lineage"]["links"]))
        self.assertTrue(result["gaps"])
        self.assertNotIn("raw", result)

    def test_missing_notification_does_not_lower_judgement_readiness(self):
        result = decision_flow_projection(episode(), [])

        self.assertEqual("pass", result["readinessState"])
        self.assertEqual("warning", result["stages"][-1]["state"])
        self.assertEqual("", result["blockingStage"])
        self.assertEqual("확인 완료", result["blockingStageLabel"])

    def test_partial_data_with_a_snapshot_points_to_validation_not_source(self):
        row = episode(validation_state="conditional", data_state="partial")
        row["sourceAboxSnapshotId"] = "abox:partial"
        projection = decision_flow_projection(row, [])
        detail = InvestmentFlowQueryService(
            FakeDecisionStore([row]),
            FakeNotificationStore([]),
        ).detail(row["episodeId"])

        self.assertEqual("pass", projection["stages"][0]["state"])
        self.assertEqual("validation", projection["blockingStage"])
        self.assertEqual(["validation", "notification"], [item["stage"] for item in detail["gaps"]])


if __name__ == "__main__":
    unittest.main()
