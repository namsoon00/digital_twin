import unittest

from digital_twin.application.investment_case_query_service import InvestmentCaseQueryService
from digital_twin.domain.investment_case import (
    investment_case_id,
    investment_case_snapshot,
    parse_investment_case_id,
)


def episode(
    episode_id="decision-episode:1",
    *,
    account_id="default",
    action="HOLD",
    validation_state="ready",
    decided_at="2026-08-20T02:00:00Z",
    outcomes=None,
):
    return {
        "episodeId": episode_id,
        "accountId": account_id,
        "symbol": "AAPL",
        "subjectName": "Apple",
        "action": action,
        "reviewLevel": "observe",
        "dataState": "sufficient",
        "validationState": validation_state,
        "sourceAboxSnapshotId": "abox:1",
        "inferenceGenerationId": "generation:1",
        "selectedHypothesisId": "hypothesis:1",
        "evidenceIds": ["evidence:1"],
        "counterEvidenceIds": ["evidence:counter"],
        "decisionSummary": "수요 가설과 반대 근거를 비교해 현재 행동을 정했습니다.",
        "decidedAt": decided_at,
        "updatedAt": decided_at,
        "hypothesisSet": {
            "hypotheses": [{
                "hypothesisId": "hypothesis:1",
                "templateLabel": "AI 수요 지속",
                "claim": "AI 수요가 실적을 지지합니다.",
                "supportingRuleIds": ["rule:ai-demand"],
                "causalPathIds": ["relation:path:ai-demand"],
                "supportingEvidenceIds": ["evidence:1"],
                "counterEvidenceIds": ["evidence:counter"],
                "invalidationConditions": ["수요 증가가 다음 분기에 확인되지 않습니다."],
            }],
        },
        "outcomes": list(outcomes or []),
    }


class FakeDecisionStore:
    def __init__(self, rows):
        self.rows = list(rows)
        self.head_reads = 0
        self.history_reads = 0

    def list_flow_heads(self, account_id="", symbol="", limit=200):
        self.head_reads += 1
        rows = self.rows
        if account_id:
            rows = [row for row in rows if row.get("accountId", "") == account_id]
        if symbol:
            rows = [row for row in rows if row["symbol"] == symbol]
        seen = set()
        result = []
        for row in rows:
            key = (row.get("accountId", ""), row["symbol"])
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
        return result[:limit]

    def list(self, account_id="", symbol="", limit=50):
        self.history_reads += 1
        rows = self.rows
        if account_id:
            rows = [row for row in rows if row.get("accountId", "") == account_id]
        if symbol:
            rows = [row for row in rows if row["symbol"] == symbol]
        return rows[:limit]

    def get(self, episode_id):
        return next((row for row in self.rows if row["episodeId"] == episode_id), None)


class FakeNotificationStore:
    def job_summaries_for_decision_episodes(self, episode_ids, limit=200):
        return [{
            "jobId": "job:1",
            "decisionEpisodeId": episode_ids[0],
            "status": "done",
        }] if episode_ids else []


class InvestmentCaseQueryServiceTests(unittest.TestCase):
    def test_snapshot_has_stable_case_id_and_five_user_stages(self):
        current = investment_case_snapshot(episode())
        newer = investment_case_snapshot(episode("decision-episode:2", action="BUY"))

        self.assertEqual(current.case_id, newer.case_id)
        self.assertEqual(
            {"accountId": "default", "symbol": "AAPL"},
            parse_investment_case_id(current.case_id),
        )
        self.assertEqual(
            ["fact", "signal", "case", "decision", "outcome"],
            [item["id"] for item in current.stages],
        )
        self.assertEqual(1, current.signals["supportCount"])
        self.assertEqual(1, current.signals["counterCount"])

    def test_list_uses_compact_heads_without_history_hydration(self):
        store = FakeDecisionStore([episode()])
        result = InvestmentCaseQueryService(store, FakeNotificationStore()).list_cases()

        self.assertEqual("investment-case-v2", result["version"])
        self.assertEqual(1, result["count"])
        self.assertEqual(investment_case_id("default", "AAPL"), result["items"][0]["caseId"])
        self.assertEqual(1, store.head_reads)
        self.assertEqual(0, store.history_reads)
        self.assertNotIn("scenarios", result["items"][0])
        self.assertFalse(result["operatorView"]["loaded"])

    def test_operator_diagnostics_are_built_only_when_requested(self):
        result = InvestmentCaseQueryService(
            FakeDecisionStore([episode()]),
            FakeNotificationStore(),
        ).list_cases(include_operator=True)

        self.assertTrue(result["operatorView"]["loaded"])
        self.assertEqual(6, len(result["operatorView"]["stages"]))

    def test_detail_separates_evidence_scenarios_and_trace_references(self):
        row = episode()
        row["decisionGuardrails"] = [{
            "label": "근거 충분성 제한",
            "missingData": ["{'label': '밸류에이션 입력값', 'effect': '피어 표본 3개가 필요합니다.'}"],
        }]
        store = FakeDecisionStore([row])
        service = InvestmentCaseQueryService(store, FakeNotificationStore())
        case_id = investment_case_id("default", "AAPL")

        result = service.detail(case_id)

        self.assertEqual("ok", result["status"])
        self.assertEqual("AI 수요 지속", result["scenarios"][0]["title"])
        self.assertEqual(1, result["evidence"]["supportCount"])
        self.assertEqual(
            ["밸류에이션 입력값: 피어 표본 3개가 필요합니다."],
            result["evidence"]["missingData"],
        )
        self.assertEqual("generation:1", result["traceRefs"]["inferenceGenerationId"])

    def test_detail_resolves_default_case_for_legacy_empty_account_rows(self):
        row = episode(account_id="")
        store = FakeDecisionStore([row])
        service = InvestmentCaseQueryService(store)

        result = service.detail(investment_case_id("default", "AAPL"))
        history = service.history(investment_case_id("default", "AAPL"))

        self.assertEqual("ok", result["status"])
        self.assertEqual("default", result["accountId"])
        self.assertEqual(1, history["count"])

    def test_detail_resolves_legacy_flow_link(self):
        row = episode()
        row["flowId"] = "flow:legacy-link"
        service = InvestmentCaseQueryService(FakeDecisionStore([row]))

        result = service.detail("flow:legacy-link")

        self.assertEqual("ok", result["status"])
        self.assertEqual(investment_case_id("default", "AAPL"), result["caseId"])

    def test_missing_case_returns_actionable_error(self):
        result = InvestmentCaseQueryService(FakeDecisionStore([])).detail("case:missing")

        self.assertEqual("not-found", result["status"])
        self.assertIn("목록을 새로고침", result["error"])

    def test_history_reports_action_and_validation_transitions_latest_first(self):
        latest = episode("decision-episode:new", action="BUY", validation_state="ready", decided_at="2026-08-20T03:00:00Z")
        older = episode("decision-episode:old", action="HOLD", validation_state="conditional", decided_at="2026-08-20T01:00:00Z")
        service = InvestmentCaseQueryService(FakeDecisionStore([latest, older]))

        result = service.history(investment_case_id("default", "AAPL"))

        self.assertEqual(2, result["count"])
        self.assertEqual("decision-episode:new", result["items"][0]["episodeId"])
        self.assertTrue(result["items"][0]["change"]["actionChanged"])
        self.assertTrue(result["items"][0]["change"]["validationChanged"])

    def test_trace_is_lazy_and_keeps_internal_pipeline_for_operators(self):
        service = InvestmentCaseQueryService(FakeDecisionStore([episode()]))

        result = service.trace(investment_case_id("default", "AAPL"))

        self.assertEqual("operator", result["audience"])
        self.assertEqual(6, len(result["trace"]["lineage"]["nodes"]))
        self.assertEqual("decision-episode:1", result["episodeId"])


if __name__ == "__main__":
    unittest.main()
