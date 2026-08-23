import unittest

from digital_twin.application.investment_case_query_service import InvestmentCaseQueryService
from digital_twin.domain.investment_case import (
    investment_case_id,
    investment_case_snapshot,
    parse_investment_case_id,
)
from digital_twin.domain.investment_analysis import investment_decision_key


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

        self.assertEqual("investment-case-v3", result["version"])
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
        self.assertEqual("reconstructed", result["reasoning"]["snapshotState"])
        self.assertEqual(1, result["reasoning"]["counts"]["rules"])
        rule_node = next(
            item for item in result["explanation"]["causalPaths"][0]["nodes"]
            if item["layer"] == "rule"
        )
        self.assertEqual("rule:ai-demand", rule_node["items"][0]["id"])

    def test_exact_episode_exposes_frozen_current_state_and_integrity(self):
        row = episode()
        row["factsAtDecision"] = {
            "reasoningDetailSnapshot": {
                "version": "investment-reasoning-detail-v2",
                "snapshotState": "exact",
                "snapshotStateLabel": "판단 당시 추론 상세",
                "inferenceGenerationAt": "2026-08-20T01:59:59Z",
                "facts": [{
                    "id": "fact:currentPrice",
                    "field": "currentPrice",
                    "label": "현재가",
                    "observedValue": 226.17,
                    "source": "market provider",
                    "asOf": "2026-08-20T01:59:58Z",
                }],
                "relations": [],
                "rules": [],
                "traces": [],
                "hypotheses": [],
                "counts": {"facts": 1, "relations": 0, "rules": 0, "traces": 0, "hypotheses": 0},
            },
        }

        result = investment_case_snapshot(row)

        self.assertEqual("pass", result.integrity["state"])
        self.assertEqual("exact", result.current_state["snapshotState"])
        self.assertEqual(226.17, result.current_state["groups"][0]["items"][0]["value"])
        self.assertEqual("2026-08-20T01:59:58Z", result.freshness["sourceAsOf"])

    def test_unselected_hypothesis_evidence_is_kept_in_case_summary(self):
        row = episode()
        row["selectedHypothesisId"] = ""
        row["decisionAbstention"] = {
            "abstained": True,
            "reason": "AI가 현재 TypeDB 규칙 가설을 모두 평가하지 못했습니다.",
        }
        row["evidenceIds"] = []
        row["counterEvidenceIds"] = []

        result = investment_case_snapshot(row)

        self.assertEqual(1, result.signals["supportCount"])
        self.assertEqual(1, result.signals["counterCount"])
        self.assertEqual("all-candidate-hypotheses", result.evidence["scope"])
        self.assertEqual("blocked", result.readiness_state)
        self.assertEqual("decision", result.phase)
        dimensions = {item["id"]: item for item in result.status_dimensions}
        self.assertEqual("pass", dimensions["inference"]["state"])
        self.assertEqual("blocked", dimensions["ai"]["state"])
        self.assertEqual(
            "AI_HYPOTHESIS_COMPARISON_INCOMPLETE",
            result.explanation["primaryCause"]["reasonCode"],
        )

    def test_legacy_embedded_gap_payloads_are_rendered_as_plain_language(self):
        row = episode()
        row["unresolvedQuestions"] = [
            "누락 데이터 {'key': 'tradeStrength', 'label': '체결강도', "
            "'effect': '체결 압력을 확인할 수 없습니다.', 'status': 'missing', 'source': 'KIS'}"
        ]

        result = investment_case_snapshot(row)

        self.assertIn("체결강도: 체결 압력을 확인할 수 없습니다.", result.decision["requiredChecks"])
        self.assertNotIn("{'key'", " ".join(result.decision["requiredChecks"]))

    def test_guardrail_reason_and_change_condition_do_not_leak_internal_payloads(self):
        row = episode()
        row["decisionGuardrails"] = [{
            "label": "근거 충분성 제한",
            "reason": "필수 데이터 {'key': 'tradeStrength', 'label': '체결강도', "
            "'effect': '체결 압력을 확인할 수 없습니다.'}",
        }]
        row["hypothesisSet"]["hypotheses"][0]["invalidationConditions"] = [
            "TypeDB 조건 holding-source:graph.test.v1이 다음 추론 세대에서 성립하지 않습니다."
        ]

        result = investment_case_snapshot(row)

        constraint = result.explanation["constraints"][0]
        self.assertIn("체결강도: 체결 압력을 확인할 수 없습니다.", constraint["summary"])
        self.assertNotIn("{'key'", constraint["summary"])
        self.assertEqual(
            "현재 관계 규칙이 다음 추론에서도 유지되는지, 반대 근거가 더 강해지는지 확인합니다.",
            result.explanation["changeConditions"][0],
        )

    def test_case_summary_reports_independent_status_dimensions(self):
        result = InvestmentCaseQueryService(
            FakeDecisionStore([episode()]),
            FakeNotificationStore(),
        ).list_cases()

        self.assertEqual(1, result["summary"]["dimensions"]["decision"]["pass"])
        self.assertEqual(1, result["summary"]["dimensions"]["data"]["pass"])
        self.assertEqual(1, result["summary"]["dimensions"]["inference"]["pass"])
        self.assertEqual(1, result["summary"]["dimensions"]["ai"]["pass"])

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

    def test_detail_resolves_legacy_decision_hash_to_canonical_episode(self):
        row = episode()
        service = InvestmentCaseQueryService(FakeDecisionStore([row]))
        legacy_key = investment_decision_key("default", "AAPL", row["episodeId"])

        result = service.detail(legacy_key)

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["resolvedFromLegacyKey"])
        self.assertEqual("decision-episode:1", result["episodeId"])
        self.assertIn("detailKey=decision-episode:1", result["canonicalUrl"])

    def test_ai_only_episode_does_not_report_false_typedb_agreement(self):
        row = episode()
        row["hypothesisSet"]["hypotheses"][0]["candidateAction"] = ""
        row["source"] = "notification-ai"

        result = investment_case_snapshot(row)
        comparison = result.explanation["comparison"]

        self.assertEqual("ai-only", comparison["state"])
        self.assertFalse(comparison["comparable"])
        self.assertIsNone(comparison["different"])

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
