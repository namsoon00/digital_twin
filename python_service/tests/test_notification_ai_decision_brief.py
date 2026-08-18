import json
import unittest

from digital_twin.application.notification_ai_decision_context import NotificationAIDecisionContextEnricher
from digital_twin.domain.notification_ai_decision_brief import (
    AI_DECISION_CONTRACT_VERSION,
    AI_DECISION_BRIEF_VERSION,
    build_notification_ai_decision_prompt,
    notification_ai_decision_brief,
    notification_ai_execution_profile,
)
from digital_twin.domain.notifications import NotificationJob


def decision_context(review_level="observe", change_state="unchanged"):
    return {
        "messageType": "investmentInsight",
        "accountId": "main",
        "rawSymbol": "005930",
        "displayTarget": "삼성전자 / 005930",
        "referenceDate": "2026-08-11T01:00:00Z",
        "ontologyRelationContext": {
            "subject": {"symbol": "005930", "name": "삼성전자", "market": "KOSPI"},
            "inferenceGenerationId": "inference:1",
            "inferenceGenerationAt": "2026-08-11T01:00:00Z",
            "reviewLevel": review_level,
            "dataState": "sufficient",
            "changeState": change_state,
            "conflictState": "aligned",
            "allowedActions": ["HOLD", "TRIM"],
            "blockedActions": ["ADD"],
            "facts": {
                "currentPrice": 70000,
                "foreignNetVolume": 12000,
                "institutionNetVolume": -3000,
            },
            "activeRules": [{
                "ruleId": "graph.temporal.support.v1",
                "label": "가격 방어 확인",
                "evidenceRole": "support",
                "ruleRequiredFacts": ["kind:stock:field:currentprice"],
                "contextCompletenessPolicy": {
                    "aboxReadMode": "complete-active-world",
                    "retainUnchangedFacts": True,
                    "retainPriorValidInferences": True,
                },
            }],
            "executionPlan": {
                "primaryAction": "HOLD",
                "allowedActions": ["HOLD", "TRIM"],
                "decisionDrivers": [{"category": "trend", "summary": "5일 가격 방어가 확인됐습니다."}],
            },
            "investmentBrain": {
                "question": {"questionId": "question:1", "text": "보유 판단을 다시 확인한다."},
                "hypothesisSet": {
                    "hypothesisSetId": "set:1",
                    "hypotheses": [{
                        "hypothesisId": "hypothesis:hold",
                        "templateId": "template:hold",
                        "claim": "가격 방어가 이어질 수 있다.",
                        "stance": "support",
                        "supportingRuleIds": ["graph.temporal.support.v1"],
                        "supportingEvidenceIds": ["relation:1"],
                    }],
                },
                "researchPlan": {
                    "planId": "plan:1",
                    "tasks": [{
                        "taskId": "task:1",
                        "question": "다음 실적 가이던스가 방어 가설을 바꾸는가?",
                        "decisionRelevance": "direct",
                        "status": "ready",
                        "requiredEvidenceTypes": ["company-guidance"],
                    }],
                },
                "epistemicState": {"status": "provisional"},
            },
        },
        "notificationAiInternalData": {
            "temporalWindows": [{
                "windowKey": "5D",
                "priceChangePct": -2.4,
                "drawdownFromPeakPct": -4.1,
                "reboundFromTroughPct": 1.2,
                "priceVelocityChangePct": 0.7,
                "hasSufficientHistory": True,
                "coverageRatio": 1.0,
            }],
            "audit": {"status": "ready", "loadMs": 4},
        },
    }


class FakeTimeSeriesStore:
    def __init__(self):
        self.calls = []

    def load_temporal_windows(self, account_id, symbols, definitions, as_of=""):
        self.calls.append((account_id, list(symbols), as_of))
        rows = [
            {
                "currentPrice": 68000,
                "observedAt": "2026-08-08T01:00:00Z",
                "bucketAt": "2026-08-08T01:00:00Z",
                "dataQuality": "actual",
                "ma20Distance": -1.0,
                "foreignNetVolume": 100,
                "institutionNetVolume": 50,
                "sourceAsOf": "2026-08-08T01:00:00Z",
            },
            {
                "currentPrice": 70000,
                "observedAt": "2026-08-11T01:00:00Z",
                "bucketAt": "2026-08-11T01:00:00Z",
                "dataQuality": "actual",
                "ma20Distance": 1.0,
                "foreignNetVolume": 150,
                "institutionNetVolume": 75,
                "sourceAsOf": "2026-08-11T01:00:00Z",
            },
        ]
        return {symbols[0]: {definition.key: rows for definition in definitions}}


class NotificationAIDecisionBriefTests(unittest.TestCase):
    def test_standard_and_deep_profiles_use_different_effort_and_budget(self):
        standard = notification_ai_execution_profile(decision_context(), {})
        deep = notification_ai_execution_profile(decision_context("act", "new-condition"), {})

        self.assertEqual("standard", standard["name"])
        self.assertEqual("max", standard["reasoningEffort"])
        self.assertEqual(16 * 1024, standard["maxPromptBytes"])
        self.assertEqual("deepResearch", deep["name"])
        self.assertEqual("max", deep["reasoningEffort"])
        self.assertGreater(deep["maxPromptBytes"], standard["maxPromptBytes"])

    def test_mixed_competing_evidence_uses_deep_research_profile(self):
        context = decision_context()
        context["ontologyRelationContext"]["conflictState"] = "mixed"

        profile = notification_ai_execution_profile(context, {})

        self.assertEqual("deepResearch", profile["name"])
        self.assertIn("competing-evidence", profile["selectionReasons"])

    def test_brief_contains_exact_temporal_path_and_decision_changing_gap_once(self):
        context = decision_context()
        brief = notification_ai_decision_brief(context, {})
        prompt = build_notification_ai_decision_prompt(context, {}, max_prompt_bytes=16 * 1024)

        self.assertEqual(AI_DECISION_BRIEF_VERSION, brief["schemaVersion"])
        self.assertEqual(AI_DECISION_CONTRACT_VERSION, brief["decisionContractVersion"])
        self.assertEqual(-4.1, brief["currentSituation"]["temporalWindows"][0]["drawdownFromPeakPct"])
        self.assertEqual("task:1", brief["research"]["decisionChangingGaps"][0]["taskId"])
        self.assertEqual(
            ["complete-active-world"],
            brief["inference"]["contextCoverage"]["aboxReadMode"],
        )
        self.assertTrue(brief["inference"]["contextCoverage"]["unchangedFactsRetained"])
        self.assertIn('"schemaVersion":"' + AI_DECISION_BRIEF_VERSION + '"', prompt)
        self.assertIn('"drawdownFromPeakPct":-4.1', prompt)
        self.assertIn("valuationReferenceOnly=true", prompt)
        self.assertIn("시스템 수집기가", prompt)
        self.assertIn("decisionReadiness", prompt)
        self.assertIn("causalChain", prompt)
        self.assertIn("alternativeAction", prompt)
        self.assertNotIn('"promptContext"', prompt)
        self.assertLessEqual(len(prompt.encode("utf-8")), 16 * 1024)

    def test_internal_context_enricher_loads_snapshot_bounded_windows(self):
        store = FakeTimeSeriesStore()
        enricher = NotificationAIDecisionContextEnricher(store, {})
        context = decision_context()
        context.pop("notificationAiInternalData")
        job = NotificationJob.create(
            "test",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )

        enricher(job)

        internal = job.context["notificationAiInternalData"]
        self.assertEqual("ready", internal["audit"]["status"])
        self.assertEqual(7, internal["audit"]["loadedWindowCount"])
        self.assertEqual("2026-08-11T01:00:00Z", store.calls[0][2])
        self.assertIn("priceVelocityChangePct", internal["temporalWindows"][0])

        second = NotificationJob.create(
            "test-2",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )
        enricher(second)
        self.assertTrue(second.context["notificationAiInternalData"]["audit"]["cacheHit"])
        self.assertEqual(1, len(store.calls))

    def test_large_graph_context_stays_within_prompt_budget(self):
        context = decision_context("act", "new-condition")
        context["messageType"] = "portfolioRebalanceReview"
        relation = context["ontologyRelationContext"]
        relation["facts"] = {
            "fact-" + str(index): "x" * 800
            for index in range(500)
        }
        hypothesis_ids = ["hypothesis:" + str(index) for index in range(6)]
        relation["investmentBrain"]["hypothesisSet"]["hypotheses"] = [{
            "hypothesisId": "hypothesis:" + str(index),
            "templateId": "template:" + str(index),
            "claim": "y" * 800,
            "supportingRuleIds": ["graph.temporal.support.v1"],
            "supportingEvidenceIds": ["evidence:" + str(item) for item in range(40)],
        } for index in range(6)]
        window_keys = ["15M", "1H", "SESSION", "1D", "3D", "5D", "20D"]
        context["notificationAiInternalData"]["temporalWindows"] = [{
            "windowKey": key,
            "windowType": "multi-day" if key.endswith("D") else "fixed-intraday",
            "lookbackDays": index + 1,
            "startPrice": 100 + index,
            "currentPrice": 110 + index,
            "priceChangePct": 10,
            "reboundFromTroughPct": 12,
            "hasSufficientHistory": True,
        } for index, key in enumerate(window_keys)]
        context["portfolioLifecycle"] = {
            "status": "ready",
            "portfolioId": "portfolio:main",
            "mandate": {
                "profile": "aggressive",
                "max_position_weight_pct": 45,
                "max_sector_weight_pct": 65,
                "fx_exposure_review_pct": 25,
            },
            "exposureSnapshot": {
                "observedAt": "2026-08-11T01:00:00Z",
                "metrics": [{
                    "exposure_type": "position",
                    "key": "SYMBOL" + str(index),
                    "ratio_pct": index,
                    "policy_limit_pct": 45,
                    "policyDeltaPct": max(0, index - 45),
                    "unusedPayload": "z" * 500,
                } for index in range(100)],
            },
            "portfolioRiskSnapshot": {
                "dataState": "complete",
                "positions": [{
                    "symbol": "SYMBOL" + str(index),
                    "period_return_pct": index,
                    "unusedPayload": "z" * 500,
                } for index in range(100)],
            },
            "rebalanceProposal": {
                "status": "review-required",
                "scenarios": [{"unusedPayload": "z" * 1000} for _ in range(30)],
            },
            "rebalanceState": {
                "status": "POLICY_BREACH",
                "breachKeys": ["position:MSTR"],
                "maximumNotionalBySymbol": {"MSTR": 5_000_000},
                "revision": "rebalance-revision:1",
                "unusedPayload": "z" * 1000,
            },
        }

        prompt = build_notification_ai_decision_prompt(
            context,
            {},
            max_prompt_bytes=16 * 1024,
        )

        self.assertLessEqual(len(prompt.encode("utf-8")), 16 * 1024)
        self.assertIn('"schemaVersion":"' + AI_DECISION_BRIEF_VERSION + '"', prompt)
        payload = json.loads(prompt.split("DecisionBrief:\n", 1)[1])
        self.assertEqual(
            window_keys,
            [item["windowKey"] for item in payload["currentSituation"]["temporalWindows"]],
        )
        self.assertTrue(all("startPrice" in item for item in payload["currentSituation"]["temporalWindows"]))
        self.assertEqual(
            hypothesis_ids,
            [item["hypothesisId"] for item in payload["inference"]["hypothesisSet"]["hypotheses"]],
        )
        self.assertEqual(
            45,
            payload["accountPolicy"]["portfolioLifecycle"]["mandate"]["max_position_weight_pct"],
        )
        self.assertEqual(
            "POLICY_BREACH",
            payload["accountPolicy"]["portfolioLifecycle"]["rebalanceState"]["status"],
        )
        self.assertEqual(
            {"MSTR": 5_000_000},
            payload["accountPolicy"]["portfolioLifecycle"]["rebalanceState"]["maximumNotionalBySymbol"],
        )

    def test_production_shaped_symbol_context_preserves_ontology_contract_at_standard_limit(self):
        context = decision_context()
        relation = context["ontologyRelationContext"]
        rule_ids = ["graph.production.rule." + str(index) for index in range(4)]
        relation["activeRules"] = [{
            "ruleId": rule_id,
            "label": "운영 규칙 " + str(index),
            "relationType": "HAS_INFERENCE_TRACE",
            "evidenceRole": "support" if index % 2 == 0 else "risk",
            "evidence": {"detail": "r" * 2000, "rows": ["r" * 500 for _ in range(10)]},
        } for index, rule_id in enumerate(rule_ids)]
        hypothesis_ids = ["hypothesis-instance:" + (str(index) * 24) for index in range(5)]
        relation["investmentBrain"]["hypothesisSet"]["hypotheses"] = [{
            "hypothesisId": hypothesis_id,
            "templateId": "hypothesis-template:" + (str(index) * 32),
            "claim": "경쟁 가설 " + ("h" * 1000),
            "stance": "support" if index % 2 == 0 else "risk",
            "verificationStatus": "verified-by-current-evidence",
            "supportingRuleIds": [rule_ids[index % len(rule_ids)]],
            "supportingEvidenceIds": ["relation-evidence:" + ("s" * 160)],
            "counterEvidenceIds": ["relation-evidence:" + ("c" * 160)],
        } for index, hypothesis_id in enumerate(hypothesis_ids)]
        window_keys = ["15M", "1H", "SESSION", "1D", "3D", "5D", "20D"]
        context["notificationAiInternalData"]["temporalWindows"] = [{
            "windowKey": key,
            "sampleCount": 100 + index,
            "requiredSampleCount": 10,
            "hasSufficientHistory": True,
            "startPrice": 100 + index,
            "currentPrice": 110 + index,
            "priceChangePct": 10,
            "drawdownFromPeakPct": -5,
            "reboundFromTroughPct": 12,
            "priceVelocityChangePct": 2,
        } for index, key in enumerate(window_keys)]
        relation["companyContext"] = {
            "symbol": "NVDA",
            "companyName": "NVIDIA",
            "factRevision": "company-fact:" + ("f" * 80),
            "judgmentUse": "reference",
            "profile": {"sector": "Technology", "industry": "Semiconductors", "businessSummary": "x" * 5000},
            "valuation": {
                "peRatio": 35.0, "forwardPE": 22.0, "pbr": 20.0,
                "pegRatio": 0.8, "returnOnEquityPct": 80.0,
            },
            "latestFinancials": {
                "annual": [{"period": "2026", "revenue": 1000, "netIncome": 200, "unused": "x" * 5000}],
                "quarterly": [{"period": "2026-Q1", "revenue": 300, "netIncome": 70, "unused": "x" * 5000}],
            },
            "coverage": {"dataState": "sufficient", "officialSource": "SEC EDGAR"},
        }
        relation["assessmentBundle"] = {
            "investmentOpinion": {
                "status": "candidate-ready", "candidateAction": "BUY",
                "selectedRuleId": rule_ids[0], "entries": [{"unused": "a" * 4000} for _ in range(10)],
            },
            "executionReadiness": {"status": "conditional", "entries": [{"unused": "a" * 4000}]},
            "recommendedPlan": {"status": "constrained", "investmentAction": "BUY"},
        }

        prompt = build_notification_ai_decision_prompt(context, {}, max_prompt_bytes=16 * 1024)
        payload = json.loads(prompt.split("DecisionBrief:\n", 1)[1])

        self.assertLessEqual(len(prompt.encode("utf-8")), 16 * 1024)
        self.assertEqual(window_keys, [item["windowKey"] for item in payload["currentSituation"]["temporalWindows"]])
        self.assertEqual(hypothesis_ids, [item["hypothesisId"] for item in payload["inference"]["hypothesisSet"]["hypotheses"]])
        self.assertEqual(rule_ids, [item["ruleId"] for item in payload["inference"]["activeRules"]])

    def test_market_evidence_and_continuity_fit_deep_prompt_budget(self):
        context = decision_context("act", "new-condition")
        relation = context["ontologyRelationContext"]
        relation["conflictState"] = "mixed"
        relation["facts"]["marketEvidenceProfile"] = {
            "profileKey": "KR_EQUITY",
            "label": "국내 주식 증거 프로필",
            "market": "KR",
            "currency": "KRW",
            "dataState": "sufficient",
            "judgementEvidenceUsable": True,
            "observableFollowUpFields": ["currentPrice", "volumeRatio", "tradeStrength"] * 8,
            "capabilities": {
                "capability-" + str(index): {
                    "state": "fresh",
                    "reason": "상세 공급자 상태 " + ("x" * 500),
                    "observedFields": ["field-" + str(item) for item in range(20)],
                }
                for index in range(12)
            },
            "unavailableCapabilities": [{
                "capability": "missing-" + str(index),
                "label": "누락 자료",
                "reason": "공급자 제약 " + ("y" * 500),
            } for index in range(12)],
        }
        context["decisionContinuityPacket"] = {
            "contractVersion": "decision-continuity-packet-v2",
            "status": "available",
            "capturedAt": "2026-08-18T01:00:00Z",
            "previousDecision": {
                "action": "HOLD",
                "decisionReadiness": "conditional",
                "decisionSummary": "직전 판단 " + ("p" * 3000),
            },
            "selectedHypothesis": {
                "hypothesisId": "hypothesis:hold",
                "claim": "직전 가설 " + ("h" * 3000),
                "supportingEvidenceIds": ["evidence:" + str(index) for index in range(20)],
            },
            "observedOutcomes": [{
                "outcomeId": "outcome:" + str(index),
                "observedAt": "2026-08-18T01:00:00Z",
                "priceChangeFromDecisionPct": index,
                "unused": "o" * 2000,
            } for index in range(10)],
            "actionObservations": [{
                "observationId": "observation:" + str(index),
                "observedDirection": "unchanged",
                "unused": "a" * 2000,
            } for index in range(10)],
            "observationState": {
                "userAction": "not-observed",
                "outcome": "observed",
                "causalityClaimed": False,
            },
        }

        prompt = build_notification_ai_decision_prompt(
            context,
            {},
            max_prompt_bytes=20 * 1024,
        )
        payload = json.loads(prompt.split("DecisionBrief:\n", 1)[1])

        self.assertLessEqual(len(prompt.encode("utf-8")), 20 * 1024)
        self.assertEqual(
            "KR_EQUITY",
            payload["currentSituation"]["relationFacts"]["marketEvidenceProfile"]["profileKey"],
        )
        self.assertEqual("HOLD", payload["decisionContinuity"]["previousDecision"]["action"])
        self.assertEqual(
            "hypothesis:hold",
            payload["inference"]["hypothesisSet"]["hypotheses"][0]["hypothesisId"],
        )

    def test_ordinary_symbol_decision_excludes_rebalance_policy_but_scheduled_review_keeps_it(self):
        context = decision_context()
        context["rawLines"] = [
            "현재가: 70,000원",
            "계좌 평가금액: 5,000만 원",
            "현금 비중: 0.1%",
        ]
        context["investmentStrategy"] = {
            "label": "공격형",
            "riskTolerance": "very_high",
            "lossTolerancePct": -15,
            "maxPositionWeightPct": 45,
            "maxSectorWeightPct": 65,
            "fxExposureReviewPct": 25,
            "minCashWeightPct": 3,
            "promptInstruction": "집중도와 현금 하한을 확인합니다.",
            "profile": "aggressive",
        }
        context["investmentStrategyGuidance"] = {
            "label": "공격형",
            "profile": "aggressive",
            "riskChecks": ["최대 종목 비중 45%"],
            "actionBoundaries": ["집중도 초과 시 추가 진입 제한"],
        }
        context["ontologyRelationContext"]["facts"].update({
            "positionWeight": 7.0,
            "sectorRatio": 32.0,
            "fxExposureRatio": 59.0,
            "strategyMaxPositionWeightPct": 45.0,
            "strategyMaxSectorWeightPct": 65.0,
            "strategyFxExposureReviewPct": 25.0,
        })
        context["portfolioLifecycle"] = {
            "status": "ready",
            "portfolioId": "portfolio:main",
            "mandate": {
                "max_position_weight_pct": 45,
                "min_cash_weight_pct": 3,
            },
            "exposureSnapshot": {
                "metrics": [{
                    "exposure_type": "cash",
                    "key": "KRW",
                    "ratio_pct": 0.1,
                    "policy_limit_pct": 3,
                    "policyDeltaPct": 2.9,
                }],
            },
            "rebalanceProposal": {
                "status": "proposed",
                "legs": [{
                    "symbol": "MSTR",
                    "side": "TRIM",
                    "before_weight_pct": 55,
                    "after_weight_pct": 45,
                }],
            },
            "rebalanceState": {"status": "POLICY_BREACH", "breachKeys": ["cash:KRW"]},
            "portfolioState": {
                "cashWeightPct": 0.1,
                "positions": [{
                    "symbol": "005930",
                    "currentWeightPct": 7.0,
                    "profitLossRate": 2.0,
                    "holdingDays": 10,
                }],
            },
        }

        ordinary = notification_ai_decision_brief(context, {})
        context["messageType"] = "portfolioRebalanceReview"
        scheduled = notification_ai_decision_brief(context, {})

        self.assertEqual("instrument-market", ordinary["decisionPolicyScope"]["name"])
        self.assertEqual("excluded", ordinary["decisionPolicyScope"]["portfolioRebalancePolicy"])
        self.assertNotIn("mandate", ordinary["accountPolicy"]["portfolioLifecycle"])
        self.assertNotIn("exposureSnapshot", ordinary["accountPolicy"]["portfolioLifecycle"])
        self.assertNotIn("rebalanceProposal", ordinary["accountPolicy"]["portfolioLifecycle"])
        self.assertNotIn("rebalanceState", ordinary["accountPolicy"]["portfolioLifecycle"])
        self.assertNotIn(
            "currentWeightPct",
            ordinary["accountPolicy"]["portfolioLifecycle"]["portfolioState"]["subjectPositions"][0],
        )
        self.assertNotIn("maxPositionWeightPct", ordinary["accountPolicy"]["investmentStrategy"])
        self.assertNotIn("minCashWeightPct", ordinary["accountPolicy"]["investmentStrategy"])
        self.assertNotIn("riskChecks", ordinary["accountPolicy"]["investmentStrategyGuidance"])
        self.assertNotIn("positionWeight", ordinary["currentSituation"]["relationFacts"])
        self.assertNotIn("fxExposureRatio", ordinary["currentSituation"]["relationFacts"])
        self.assertNotIn("계좌 평가금액: 5,000만 원", ordinary["currentSituation"]["rawAlert"]["rawLines"])
        self.assertNotIn("현금 비중: 0.1%", ordinary["currentSituation"]["rawAlert"]["rawLines"])
        self.assertIn("현재가: 70,000원", ordinary["currentSituation"]["rawAlert"]["rawLines"])
        self.assertTrue(ordinary["guardrails"]["mustIgnorePortfolioRebalancePolicy"])
        self.assertEqual("portfolio-rebalance", scheduled["decisionPolicyScope"]["name"])
        self.assertEqual(45, scheduled["accountPolicy"]["portfolioLifecycle"]["mandate"]["max_position_weight_pct"])
        self.assertEqual(
            "proposed",
            scheduled["accountPolicy"]["portfolioLifecycle"]["rebalanceProposal"]["status"],
        )
        self.assertEqual(
            "MSTR",
            scheduled["accountPolicy"]["portfolioLifecycle"]["rebalanceProposal"]["legs"][0]["symbol"],
        )
        self.assertFalse(scheduled["guardrails"]["mustIgnorePortfolioRebalancePolicy"])

    def test_market_scope_blocks_legacy_action_selected_from_position_weight(self):
        context = decision_context()
        relation = context["ontologyRelationContext"]
        relation["activeRules"] = [
            {
                "rule_id": "graph.profit_momentum.hold_add_review.v1",
                "label": "수익 구간 추가매수 후보",
                "actionGroup": "addBuy",
                "evidenceState": {
                    "appliedFactFields": ["profitLossRate", "positionAccountWeight"],
                },
            },
            {
                "rule_id": "graph.price.recovery.confirmed_by_flow.v1",
                "label": "가격·수급 회복",
                "actionGroup": "recovery",
                "evidenceState": {
                    "appliedFactFields": ["ma20Distance", "smartMoneyNetVolume"],
                },
            },
        ]
        relation["decision"] = {
            "candidateAction": "ADD",
            "selectedRuleId": "graph.profit_momentum.hold_add_review.v1",
            "targetRole": "holding",
        }
        relation["actionEnvelope"] = {
            "preferredAction": "ADD",
            "allowedActions": ["ADD", "HOLD"],
            "selectedRuleId": "graph.profit_momentum.hold_add_review.v1",
            "targetRole": "holding",
        }
        relation["investmentBrain"]["hypothesisSet"]["hypotheses"] = [
            {
                "hypothesisId": "hypothesis:weighted-add",
                "claim": "계좌 비중 여유를 포함한 추가매수 가설",
                "supportingRuleIds": ["graph.profit_momentum.hold_add_review.v1"],
            },
            {
                "hypothesisId": "hypothesis:market-recovery",
                "claim": "가격과 수급 회복 가설",
                "supportingRuleIds": ["graph.price.recovery.confirmed_by_flow.v1"],
            },
        ]

        brief = notification_ai_decision_brief(context, {})
        prompt = build_notification_ai_decision_prompt(context, {}, decision_brief=brief)

        self.assertEqual("HOLD", brief["decisionState"]["decision"]["candidateAction"])
        self.assertTrue(brief["decisionState"]["decision"]["judgementBlocked"])
        self.assertEqual(
            ["graph.price.recovery.confirmed_by_flow.v1"],
            [item["ruleId"] for item in brief["inference"]["activeRules"]],
        )
        self.assertEqual(
            ["hypothesis:market-recovery"],
            [item["hypothesisId"] for item in brief["inference"]["hypothesisSet"]["hypotheses"]],
        )
        self.assertNotIn("positionAccountWeight", prompt)
        self.assertNotIn("계좌 비중 여유", prompt)


if __name__ == "__main__":
    unittest.main()
