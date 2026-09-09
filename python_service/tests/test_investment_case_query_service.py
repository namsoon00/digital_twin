import unittest

from digital_twin.application.investment_case_query_service import InvestmentCaseQueryService
from digital_twin.domain.investment_case import (
    investment_case_id,
    investment_case_snapshot,
    parse_investment_case_id,
)
from digital_twin.domain.investment_analysis import investment_decision_key
from digital_twin.domain.investment_decision_actionability import (
    investment_decision_actionability,
)


def episode(
    episode_id="decision-episode:1",
    *,
    account_id="default",
    action="HOLD",
    validation_state="ready",
    decided_at="2026-08-20T02:00:00Z",
    outcomes=None,
    symbol="AAPL",
):
    return {
        "episodeId": episode_id,
        "accountId": account_id,
        "symbol": symbol,
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


def authorized_executable_episode(action="BUY", symbol="AAPL"):
    row = episode("decision-episode:authorized", action=action, symbol=symbol)
    candidate = row["hypothesisSet"]["hypotheses"][0]
    candidate.update({
        "templateId": "hypothesis-template:verified-demand",
        "familyId": "verified-demand",
        "candidateAction": action,
        "evidenceState": "supported",
        "verificationStatus": "verified-current-generation",
        "approvalStatus": "approved-active",
        "status": "active",
        "scopeState": "market-shared",
        "marketHypothesisId": "market-hypothesis:verified-demand",
        "inferenceGenerationId": "generation:1",
        "knowledgeBasis": {
            "requiresHypothesis": True,
            "decisionEligibility": "investment-evidence",
        },
        "claimContract": {
            "claimContractId": "claim:verified-demand",
            "claimType": "market-hypothesis",
            "ruleId": "rule:ai-demand",
        },
        "qualification": {"status": "active"},
    })
    row.update({
        "decisionReadiness": "ready",
        "decisionAssurance": {"executionEligibility": "eligible"},
        "currentActionPlan": (
            "소액 분할매수만 검토하고 한 번에 큰 주문은 하지 않습니다."
            if action == "BUY" else
            "보유 수량의 일부를 분할매도하고 추가매수는 하지 않습니다."
        ),
        "changeAnalysis": "가격과 확인된 수급이 직전 판단보다 개선됐습니다.",
        "nextActionPlan": "다음 정규장에서 거래량과 외국인 수급을 다시 확인합니다.",
        "invalidationCondition": "현재가가 20일선 아래로 내려가면 현재 판단을 취소합니다.",
        "causalChain": [{
            "status": "supported",
            "evidenceIds": ["evidence:1"],
        }],
    })
    row["decisionActionability"] = investment_decision_actionability(row, row)
    return row


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


class FakeSubjectCaseStore:
    def __init__(self, rows):
        self.rows = list(rows)

    def latest(self, account_id="", symbol="", limit=20):
        rows = self.rows
        if account_id:
            rows = [item for item in rows if item.get("accountId") == account_id]
        if symbol:
            rows = [item for item in rows if item.get("symbol") == symbol]
        return rows[:limit]


class FakeAIInsightStore:
    def __init__(self, rows):
        self.rows = list(rows)

    def latest_insight_episodes(self, account_id="", symbol="", limit=200):
        rows = self.rows
        if account_id:
            rows = [item for item in rows if item.get("accountId") == account_id]
        if symbol:
            rows = [item for item in rows if item.get("symbol") == symbol]
        return rows[:limit]


class FakeMonitorStore:
    def load_previous(self):
        return {
            "default": {
                "generatedAt": "2026-08-20T03:00:00Z",
                "positions": {
                    "AAPL": {
                        "current_price": 120.0,
                        "profit_loss_rate": 4.5,
                        "quantity": 3,
                        "ma20": 115.0,
                        "quote_source": "test market",
                        "source_as_of": "2026-08-20T02:59:59Z",
                        "freshness_status": "fresh",
                    },
                },
            },
        }


class FakeEvidenceStore:
    def latest(self, symbol="", kind="", limit=50, include_inactive=False):
        return [{
            "evidenceId": "evidence:1",
            "symbol": symbol,
            "kind": "disclosure",
            "source": "official filing",
            "title": "실적 공시",
            "summary": "매출이 증가했습니다.",
            "url": "https://example.com/filing",
            "publishedAt": "2026-08-20T01:00:00Z",
            "dataState": "sufficient",
            "validationState": "verified",
            "lifecycleState": "active",
        }]


class FakeInvestmentDomainStore:
    def execution_feedback_for_decisions(self, episode_ids):
        return {episode_ids[0]: {
            "actionPlans": [{"planId": "plan:1"}],
            "executionEpisodes": [{"executionEpisodeId": "execution:1"}],
            "fills": [{"executedAt": "2026-08-20T02:30:00Z", "side": "BUY", "quantity": 1, "price": 118}],
        }}

    def lifecycle_feedback_for_decisions(self, episode_ids):
        return {episode_ids[0]: {
            "performanceAttributions": [{"observedAt": "2026-08-20T03:00:00Z", "horizonMinutes": 60}],
            "decisionReviews": [{"reviewedAt": "2026-08-20T03:01:00Z", "selectedHypothesisStatus": "supported"}],
        }}

    def decision_continuity_context(self, portfolio_id, account_id, symbol, decision_episode_id):
        return {
            "actionObservations": [{
                "observedAt": "2026-08-20T02:40:00Z",
                "observedDirection": "increased",
                "previousQuantity": 2,
                "observedQuantity": 3,
            }],
            "currentPosition": {"symbol": symbol, "quantity": 3, "observationState": "observed"},
        }


class InvestmentCaseQueryServiceTests(unittest.TestCase):
    def test_list_uses_compact_heads_without_history_hydration(self):
        store = FakeDecisionStore([episode()])
        result = InvestmentCaseQueryService(store, FakeNotificationStore()).list_cases()

        self.assertEqual("investment-case-v5", result["version"])
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

    def test_newer_subject_inference_replaces_stale_episode_as_review_not_trade(self):
        subject_case = {
            "subjectCaseId": "subject:1",
            "batchCaseId": "batch:1",
            "accountId": "default",
            "symbol": "AAPL",
            "stage": "SUPPRESSED",
            "sourceAboxSnapshotId": "abox:2",
            "inferenceGenerationId": "generation:2",
            "updatedAt": "2026-08-20T03:00:00Z",
            "candidateSet": {
                "fingerprint": "candidate:1",
                "eligibleHypothesisIds": ["hypothesis:2"],
                "allowedActions": ["BUY", "HOLD"],
                "hypotheses": [{
                    "hypothesisId": "hypothesis:2",
                    "label": "가격 회복",
                    "candidateAction": "BUY",
                    "supportingRuleIds": ["rule:recovery"],
                    "supportingEvidenceIds": ["evidence:2"],
                }],
            },
            "synthesis": {
                "graphCandidateAction": "BUY",
                "investmentViewAction": "BUY",
                "eligibleHypothesisIds": ["hypothesis:2"],
                "selectedRuleId": "rule:recovery",
                "dataState": "sufficient",
                "nextChecks": ["다음 관측에서도 회복이 유지되는지 확인"],
            },
        }
        result = InvestmentCaseQueryService(
            FakeDecisionStore([episode()]),
            subject_case_repository=FakeSubjectCaseStore([subject_case]),
            ai_insight_repository=FakeAIInsightStore([{
                "episodeId": "ai-insight:1",
                "subjectCaseId": "subject:1",
                "accountId": "default",
                "symbol": "AAPL",
                "sourceAboxSnapshotId": "abox:2",
                "inferenceGenerationId": "generation:2",
                "candidateFingerprint": "candidate:1",
                "model": "gpt-5.6-sol",
                "reasoningEffort": "max",
                "promptVersion": "investment-ai-judge-v17",
                "validationState": "conditional",
                "createdAt": "2026-08-20T03:01:00Z",
                "insight": {
                    "action": "BUY",
                    "actionLabel": "매수 검토",
                    "summary": "가격 회복을 확인하되 TypeDB 행동 권한을 따릅니다.",
                    "nextChecks": ["다음 관측에서도 회복이 유지되는지 확인"],
                    "evidence": ["가격 회복 규칙이 성립했습니다."],
                    "hypotheses": [{
                        "hypothesisId": "hypothesis:2",
                        "claim": "가격 회복이 이어집니다.",
                        "verdict": "supported",
                        "reasoning": "현재 세대의 가격 근거가 지지합니다.",
                    }],
                    "researchLeadHypothesisId": "hypothesis:2",
                    "hypothesisComparisonState": "research-reviewed",
                    "epistemicSummary": "가격 회복은 확인됐지만 지속 여부는 미확인입니다.",
                },
                "reconciliation": {
                    "notificationDecision": "suppress",
                    "reason": "중요 상태 변화가 없어 웹 이력에만 저장합니다.",
                    "deliveryPolicy": {
                        "actionAuthority": "observe",
                        "aiAdoptionState": "narrative-adopted-action-not-applicable",
                    },
                },
            }]),
        ).list_cases()

        item = result["items"][0]
        insight = item["subjectDecisionCase"]["aiInsight"]
        ai_dimension = next(row for row in item["statusDimensions"] if row["id"] == "ai")
        self.assertEqual("subject-decision-case", item["detailType"])
        self.assertEqual("review", item["attention"]["state"])
        self.assertTrue(item["attention"]["userReviewable"])
        self.assertFalse(item["attention"]["userActionable"])
        self.assertEqual("BUY", item["decision"]["candidateAction"])
        self.assertEqual("NO_ACTION", item["decision"]["action"])
        self.assertEqual(1, result["summary"]["reviewRequired"])
        self.assertEqual("completed", insight["status"])
        self.assertTrue(insight["currentGeneration"])
        self.assertEqual("gpt-5.6-sol", insight["model"])
        self.assertEqual("max", insight["reasoningEffort"])
        self.assertEqual("investment-ai-judge-v17", insight["promptVersion"])
        self.assertEqual("hypothesis:2", insight["researchLeadHypothesisId"])
        self.assertEqual("supported", insight["hypotheses"][0]["verdict"])
        self.assertEqual("suppress", insight["notificationDecision"])
        self.assertEqual("AI 해석", ai_dimension["label"])
        self.assertEqual("해석 완료", ai_dimension["stateLabel"])
        self.assertIn("AI 해석 완료", item["phaseLabel"])
        projected = InvestmentCaseQueryService._ai_insight_projection(
            {
                "subjectCaseId": "subject:current",
                "stage": "REVIEW_ONLY",
                "inferenceGenerationId": "generation:current",
                "candidateSet": {"fingerprint": "candidate:current"},
            },
            {
                "episodeId": "ai-insight:previous",
                "subjectCaseId": "subject:previous",
                "inferenceGenerationId": "generation:previous",
                "candidateFingerprint": "candidate:previous",
                "model": "gpt-5.6-sol",
                "reasoningEffort": "max",
                "insight": {"summary": "이전 세대 해석"},
                "reconciliation": {"notificationDecision": "suppress"},
            },
        )

        self.assertEqual("previous-generation", projected["status"])
        self.assertFalse(projected["currentGeneration"])
        self.assertIn("이전", projected["reason"])

        stale_only = InvestmentCaseQueryService(
            FakeDecisionStore([episode()]),
            subject_case_repository=FakeSubjectCaseStore([subject_case]),
            ai_insight_repository=FakeAIInsightStore([{
                "episodeId": "ai-insight:stale-only",
                "subjectCaseId": "subject:previous",
                "accountId": "default",
                "symbol": "AAPL",
                "inferenceGenerationId": "generation:previous",
                "candidateFingerprint": "candidate:previous",
                "insight": {"summary": "이전 세대 해석"},
            }]),
        ).list_cases()
        stale_item = stale_only["items"][0]
        stale_ai = stale_item["subjectDecisionCase"]["aiInsight"]
        stale_dimension = next(
            row for row in stale_item["statusDimensions"] if row["id"] == "ai"
        )
        self.assertEqual("not-run", stale_ai["status"])
        self.assertEqual("warning", stale_dimension["state"])
        self.assertNotIn("이전 AI 해석 있음", stale_item["phaseLabel"])

        fallback = InvestmentCaseQueryService._ai_insight_projection(
            {
                "subjectCaseId": "subject:current",
                "stage": "AI_COMPLETED",
                "inferenceGenerationId": "generation:current",
                "candidateSet": {"fingerprint": "candidate:current"},
            },
            {
                "episodeId": "ai-insight:fallback",
                "subjectCaseId": "subject:current",
                "inferenceGenerationId": "generation:current",
                "candidateFingerprint": "candidate:current",
                "publicationMode": "typedb-fallback",
                "aiAuthored": False,
                "publicationContractPassed": False,
                "contractFailureCode": "prompt-contract-budget",
                "insight": {"summary": "TypeDB 추론만 보존했습니다."},
            },
        )

        self.assertEqual("fallback", fallback["status"])
        self.assertFalse(fallback["aiAuthored"])
        self.assertFalse(fallback["publicationContractPassed"])
        self.assertEqual("prompt-contract-budget", fallback["contractFailureCode"])
        self.assertIn("TypeDB", fallback["reason"])

    def test_subject_case_explains_rule_gap_without_requesting_user_action(self):
        subject_case = {
            "subjectCaseId": "subject:coverage-gap",
            "batchCaseId": "batch:coverage-gap",
            "accountId": "default",
            "symbol": "AAPL",
            "stage": "SUPPRESSED",
            "updatedAt": "2026-08-20T03:00:00Z",
            "candidateSet": {
                "fingerprint": "candidate:coverage-gap",
                "dispositionCode": "RULE_COVERAGE_GAP_CANDIDATE",
                "ruleCoverageState": "candidate-gap",
                "dataGaps": [{
                    "code": "investorFlow",
                    "label": "투자자 수급",
                    "state": "missing",
                    "decisionImpact": "advisory",
                }],
            },
            "synthesis": {
                "selectedRuleId": "graph.rule.without.hypothesis.v1",
                "graphCandidateAction": "NO_ACTION",
                "dispositionCode": "RULE_COVERAGE_GAP_CANDIDATE",
            },
        }
        result = InvestmentCaseQueryService(
            FakeDecisionStore([episode()]),
            subject_case_repository=FakeSubjectCaseStore([subject_case]),
        ).list_cases()

        item = result["items"][0]
        self.assertEqual("내부 가설 보완 중", item["attention"]["label"])
        self.assertFalse(item["attention"]["userAttentionRequired"])
        self.assertIn("내부 보완 작업", item["headline"])
        self.assertEqual(
            "RULE_COVERAGE_GAP_CANDIDATE",
            item["subjectDecisionCase"]["dispositionCode"],
        )
        self.assertEqual("missing", item["subjectDecisionCase"]["dataGaps"][0]["state"])

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

    def test_generic_abstention_explains_the_missing_relation_and_hypothesis(self):
        row = episode()
        row["dataState"] = "unavailable"
        row["selectedHypothesisId"] = ""
        row["hypothesisSet"]["hypotheses"] = []
        row["evidenceIds"] = []
        row["counterEvidenceIds"] = []
        row["decisionAbstention"] = {
            "abstained": True,
            "reason": "No validated final hypothesis selection.",
        }

        result = investment_case_snapshot(row)
        dimensions = {item["id"]: item for item in result.status_dimensions}

        self.assertEqual("관계와 비교 가설 부족", result.explanation["primaryCause"]["title"])
        self.assertIn("관계 경로와 비교 가설이 없어", result.headline)
        self.assertNotIn("No validated", result.headline)
        self.assertEqual(
            "No validated final hypothesis selection.",
            result.decision["abstention"]["technicalReason"],
        )
        self.assertEqual(result.headline, dimensions["decision"]["reason"])
        self.assertEqual("blocked", dimensions["data"]["state"])
        self.assertEqual("판단 자료 부족", dimensions["data"]["stateLabel"])
        self.assertEqual("blocked", result.stages[0]["state"])
        self.assertIn("사용할 수", result.stages[0]["detail"])
        self.assertEqual(result.headline, result.stages[3]["detail"])

    def test_missing_source_snapshot_blocks_dependent_stages_and_keeps_plain_detail(self):
        row = episode()
        row["sourceAboxSnapshotId"] = ""
        row["factsAtDecision"] = {"currentPrice": 220.0}

        result = investment_case_snapshot(row)
        compact = result.to_dict(compact=True)

        self.assertEqual("blocked", result.readiness_state)
        self.assertEqual(
            ["blocked", "blocked", "blocked", "blocked"],
            [item["state"] for item in result.stages[:4]],
        )
        self.assertIn("원천 스냅샷", result.stages[0]["detail"])
        self.assertIn("현재 사용할 수 없습니다", result.stages[3]["detail"])
        self.assertIn("앞 단계가 차단", compact["stages"][2]["detail"])
        self.assertEqual(result.stages[3]["detail"], compact["stages"][3]["detail"])
        self.assertNotIn("HOLD 판단", compact["stages"][3]["detail"])

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

    def test_action_attention_does_not_count_blocked_judgement_as_user_action(self):
        actionable = authorized_executable_episode(action="BUY", symbol="AAPL")
        blocked = episode("decision-episode:blocked", action="SELL", symbol="TSLA")
        blocked["decisionAbstention"] = {"abstained": True, "reason": "가설 비교가 끝나지 않았습니다."}

        result = InvestmentCaseQueryService(FakeDecisionStore([actionable, blocked])).list_cases()
        by_symbol = {item["symbol"]: item for item in result["items"]}

        self.assertTrue(by_symbol["AAPL"]["attention"]["userActionable"])
        self.assertNotIn("outcome", {item["id"] for item in by_symbol["AAPL"]["attention"]["issues"]})
        self.assertFalse(by_symbol["TSLA"]["attention"]["userActionable"])
        self.assertEqual("blocked", by_symbol["TSLA"]["attention"]["state"])
        self.assertEqual(1, result["summary"]["actionRequired"])

    def test_legacy_executable_opinion_is_shown_as_blocked_not_current_action(self):
        row = episode("decision-episode:legacy-buy", action="BUY", symbol="AAPL")
        row["source"] = "notification-ai-hypothesis-competition"

        result = investment_case_snapshot(row)

        self.assertEqual("NO_ACTION", result.decision["action"])
        self.assertEqual("BUY", result.decision["recordedAction"])
        self.assertFalse(result.decision["authorization"]["authorized"])
        self.assertEqual("blocked", result.readiness_state)
        self.assertEqual("decision", result.phase)
        self.assertFalse(result.attention["userActionable"])
        self.assertIn("현재 실행 판단으로 사용할 수 없습니다", result.headline)

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

    def test_detail_joins_live_state_resolved_evidence_and_activity_without_typedb(self):
        row = episode()
        row["portfolioId"] = "portfolio:default"
        row["factsAtDecision"] = {
            "reasoningDetailSnapshot": {
                "version": "investment-reasoning-detail-v2",
                "snapshotState": "exact",
                "snapshotStateLabel": "판단 당시 추론 상세",
                "inferenceGenerationAt": "2026-08-20T01:59:59Z",
                "facts": [{
                    "id": "fact:currentPrice", "field": "currentPrice", "label": "현재가",
                    "observedValue": 100.0, "source": "test market", "asOf": "2026-08-20T01:59:58Z",
                }],
                "relations": [], "rules": [], "traces": [], "hypotheses": [],
                "counts": {"facts": 1, "relations": 0, "rules": 0, "traces": 0, "hypotheses": 0},
            },
        }
        service = InvestmentCaseQueryService(
            FakeDecisionStore([row]),
            FakeNotificationStore(),
            monitor_store=FakeMonitorStore(),
            evidence_repository=FakeEvidenceStore(),
            investment_domain_store=FakeInvestmentDomainStore(),
        )

        result = service.detail(investment_case_id("default", "AAPL"))

        self.assertEqual("current", result["liveComparison"]["status"])
        price = next(item for item in result["liveComparison"]["rows"] if item["field"] == "currentPrice")
        self.assertEqual(20.0, price["delta"])
        self.assertEqual("resolved", result["evidence"]["records"][0]["resolutionState"])
        self.assertEqual(1, result["activity"]["summary"]["fillCount"])
        self.assertEqual(4, len(result["activity"]["timeline"]))
        self.assertFalse(result["activity"]["causalityClaimed"])

if __name__ == "__main__":
    unittest.main()
