import unittest
from unittest.mock import patch
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from digital_twin.modules.read_models.application.console_read_model_service import ConsoleReadModelService
from digital_twin.modules.read_models.domain.investment_reading import investment_reading


class ConsoleDecisionReadModelTest(unittest.TestCase):
    def test_subject_case_keeps_bounded_detail_identity_and_payload(self):
        subject_case = {
            "version": "investment-case-v5",
            "detailType": "subject-decision-case",
            "subjectCaseId": "subject-decision-case:abc123",
            "batchCaseId": "reasoning-case:batch123",
            "accountId": "default",
            "symbol": "TSLA",
            "name": "Tesla",
            "phase": "case",
            "readinessState": "warning",
            "headline": "TypeDB candidate",
            "statusDimensions": [{
                "id": "ai",
                "label": "AI 해석",
                "state": "pass",
                "stateLabel": "해석 완료",
                "reason": "현재 TypeDB 세대와 일치하는 AI 해석입니다.",
            }],
            "subjectDecisionCase": {
                "stage": "SYNTHESIZED",
                "sourceAboxSnapshotId": "abox-manifest:1",
                "inferenceGenerationId": "inference-generation:1",
                "hypotheses": [{"hypothesisId": "hypothesis-instance:1"}],
                "aiInsight": {
                    "status": "completed",
                    "model": "gpt-5.6-sol",
                    "reasoningEffort": "max",
                },
            },
        }

        result = ConsoleReadModelService().decision_heads({
            "version": "investment-case-v5",
            "items": [subject_case],
        })

        self.assertEqual(result["count"], 1)
        item = result["items"][0]
        self.assertEqual(item["detailType"], "subject-decision-case")
        self.assertEqual(item["subjectCaseId"], "subject-decision-case:abc123")
        self.assertEqual(item["batchCaseId"], "reasoning-case:batch123")
        self.assertEqual(item["subjectDecisionCase"]["stage"], "SYNTHESIZED")
        self.assertEqual(
            item["subjectDecisionCase"]["hypotheses"][0]["hypothesisId"],
            "hypothesis-instance:1",
        )
        self.assertEqual(item["subjectDecisionCase"]["aiInsight"]["status"], "completed")
        self.assertEqual(item["statusDimensions"][0]["stateLabel"], "해석 완료")

        now = datetime.now(timezone.utc)
        cases = {
            "status": "ok",
            "items": [
                {
                    "caseId": "case:fresh",
                    "symbol": "AAPL",
                    "name": "Apple",
                    "updatedAt": (now - timedelta(hours=2)).isoformat(),
                    "decision": {"action": "BUY"},
                    "attention": {"userActionable": True},
                },
                {
                    "caseId": "case:old",
                    "symbol": "SKHY",
                    "name": "SK하이닉스(ADR)",
                    "updatedAt": (now - timedelta(days=8)).isoformat(),
                    "decision": {"action": "HOLD"},
                    "attention": {"userReviewable": True},
                },
            ],
        }
        dashboard = ConsoleReadModelService().dashboard_summary({}, {}, cases, {})
        self.assertEqual(["case:fresh"], [row["id"] for row in dashboard["tasks"]])
        self.assertEqual(1, dashboard["taskSummary"]["actionable"])
        self.assertEqual(1, dashboard["taskSummary"]["historical"])
        self.assertEqual(96, dashboard["taskSummary"]["freshnessWindowHours"])

    def test_episode_head_does_not_invent_subject_case_identity(self):
        result = ConsoleReadModelService().decision_heads({
            "version": "investment-case-v5",
            "items": [{
                "version": "investment-case-v5",
                "caseId": "case:default.TSLA",
                "episodeId": "decision:episode1",
                "accountId": "default",
                "symbol": "TSLA",
                "name": "Tesla",
            }],
        })

        item = result["items"][0]
        self.assertFalse(item.get("subjectCaseId"))
        self.assertEqual(item["subjectDecisionCase"], {})

    def test_unfinished_hypothesis_is_not_a_hold_opinion(self):
        case = {"headline": "TypeDB 추론 완료", "decision": {
            "action": "NO_ACTION", "dispositionCode": "HYPOTHESIS_QUALIFICATION_PENDING"}}
        saved = deepcopy(case)
        reading = investment_reading(case)
        self.assertEqual("awaiting", reading["kind"])
        self.assertEqual("투자 의견 미확정", reading["status"])
        self.assertIn("실제 결과", reading["headline"])
        self.assertNotIn("TypeDB", reading["headline"])
        self.assertEqual([], reading["nextChecks"])
        self.assertEqual(saved, case)

    def test_current_validated_interpretation_does_not_grant_an_action(self):
        case = {"decision": {"action": "NO_ACTION"}, "subjectDecisionCase": {"aiInsight": {
            "status": "completed", "currentGeneration": True, "aiAuthored": True,
            "publicationContractPassed": True,
            "insightAssessment": {"publishable": True, "dominantThesis": "수요 증가가 확인됐습니다.",
                "causalMechanism": "주문 증가가 매출로 이어질 수 있습니다.",
                "investmentImplication": "다음 분기 매출 전망을 다시 확인할 이유가 있습니다."},
            "insightTransition": {"kind": "initial-insight", "reason": "첫 투자 해석이 기록됐습니다."},
        }}}
        reading = investment_reading(case)
        self.assertEqual("interpretation", reading["kind"])
        self.assertIn("매매 의견 없음", reading["status"])
        self.assertIn("다음 분기", reading["meaning"])
        self.assertEqual(["첫 투자 해석이 기록됐습니다."], reading["changes"])
        self.assertEqual("NO_ACTION", case["decision"]["action"])

    def test_old_failed_and_unattributed_ai_never_supply_customer_meaning(self):
        for status, current, author, passed in [
            ("previous-generation", False, True, True), ("contract-failed", True, True, False),
            ("completed", True, False, True), ("completed", False, True, True),
        ]:
            with self.subTest(status=status, current=current, author=author):
                reading = investment_reading({"decision": {"action": "NO_ACTION"}, "reasoningLineage": {"ai": {
                    "status": status, "currentGeneration": current, "aiAuthored": author,
                    "publicationContractPassed": passed, "insightAssessment": {
                        "publishable": True, "dominantThesis": "UNSAFE", "investmentImplication": "UNSAFE",
                        "causalMechanism": "UNSAFE", "risks": ["UNSAFE"],
                    }, "insightTransition": {"kind": "material-insight-change", "reason": "UNSAFE"},
                }}})
                self.assertNotIn("UNSAFE", str(reading))
                self.assertEqual("awaiting", reading["kind"])

    def test_absent_data_is_separate_from_counterevidence_and_numeric_facts(self):
        reading = investment_reading({"evidence": {"missingDataItems": [{"detail": "매출 자료 없음"}]},
            "currentState": {"groups": [{"items": [
                {"field": "positionAccountWeight", "value": 0, "id": "weight", "sourceAsOf": "2026-09-13T00:00:00Z"},
                {"field": "profitLossRate", "value": -0.43884, "id": "pnl"},
                {"field": "currentPrice", "value": True},
                {"field": "price", "value": float("nan")},
                {"field": "internal", "value": {"qualification": "shadow"}},
            ]}]}})
        self.assertEqual(["매출 자료 없음"], reading["gaps"])
        self.assertEqual([], reading["counters"])
        self.assertEqual(2, len(reading["facts"]))
        self.assertEqual(0, reading["facts"][0]["value"])
        self.assertEqual("2026-09-13T00:00:00Z", reading["facts"][0]["asOf"])
        self.assertEqual(-0.43884, reading["facts"][1]["value"])

    def test_compact_heads_and_dashboard_retain_question_contract_and_subject_link(self):
        row = {"subjectCaseId": "subject-decision-case:readable", "caseId": "", "symbol": "TEST",
            "accountId": "account-a", "updatedAt": datetime.now(timezone.utc).isoformat(),
            "decision": {"action": "NO_ACTION"}, "attention": {"userReviewable": True}}
        service = ConsoleReadModelService()
        head = service.decision_heads({"items": [row]})["items"][0]
        self.assertEqual("investment-reading-v1", head["reading"]["version"])
        self.assertNotIn("facts", head["reading"])
        task = service.dashboard_summary({}, {}, {"items": [row]}, {})["tasks"][0]
        self.assertEqual(row["subjectCaseId"], task["id"])
        self.assertEqual("account-a", task["accountId"])
        self.assertEqual("awaiting", task["reading"]["kind"])
        self.assertIn(row["subjectCaseId"], task["detailPath"])
        # A process restart must not serve the pre-reading disk cache first.
        from digital_twin.infrastructure.web.adapters import console
        with patch.object(console, "cached_api_payload", return_value={}) as cached:
            console.console_decisions_api_payload({"accountId": ["account-a"], "symbol": ["TEST"], "limit": ["3"]})
            decision_cache_key = cached.call_args.args[1]
            console.console_dashboard_api_payload({"accountId": ["account-a"]})
            dashboard_cache_key = cached.call_args.args[1]
        self.assertEqual("investment-reading-v1|account-a|TEST|3|user", decision_cache_key)
        self.assertEqual("investment-reading-v1|account-a|", dashboard_cache_key)

    def test_blocked_saved_opinion_and_technical_checks_remain_read_only(self):
        case = {"decision": {"action": "HOLD", "state": "blocked", "requiredChecks": [
            "TypeDB 조건 validated-model-signal:graph.test이 성립하는지 확인", "다음 실적의 매출 확인"]},
            "scenarios": [{"id": "model-1", "claim": "매출 회복 가능성", "selected": True,
                "plainLanguageBasis": "이익 개선이 가격 회복으로 이어질 수 있다는 가설입니다. 원시 숫자 조건 대신 시점 고정 모델 신호를 TypeDB가 해석합니다.",
                "claimContract": {"expectedOutcome": "이익 개선과 가격 회복", "falsificationContract": "현금흐름 악화"},
                "qualification": {"status": "shadow", "reason": "확인된 사후 결과가 부족합니다."}}]}
        saved = deepcopy(case)
        reading = investment_reading(case)
        self.assertEqual("unavailable", reading["kind"])
        self.assertNotEqual("보유 유지", reading["status"])
        self.assertEqual(["다음 실적의 매출 확인"], reading["nextChecks"])
        self.assertFalse(reading["explanations"][0]["selected"])
        self.assertEqual("shadow", reading["explanations"][0]["qualification"])
        self.assertEqual("이익 개선과 가격 회복", reading["explanations"][0]["title"])
        self.assertNotIn("시점 고정", reading["explanations"][0]["claim"])
        self.assertIn("가능한 설명", reading["explanations"][0]["claim"])
        self.assertEqual(["현금흐름 악화"], reading["explanations"][0]["conditions"])
        self.assertEqual("확인된 사후 결과가 부족합니다.", reading["explanations"][0]["reason"])
        self.assertEqual("HOLD", case["decision"]["action"])
        self.assertEqual(saved, case)


if __name__ == "__main__":
    unittest.main()
