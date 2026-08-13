import json
import unittest

from digital_twin.application.notification_ai_decision_context import NotificationAIDecisionContextEnricher
from digital_twin.domain.notification_ai_decision_brief import (
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
        self.assertEqual("high", standard["reasoningEffort"])
        self.assertEqual(28 * 1024, standard["maxPromptBytes"])
        self.assertEqual("deepResearch", deep["name"])
        self.assertEqual("max", deep["reasoningEffort"])
        self.assertGreater(deep["maxPromptBytes"], standard["maxPromptBytes"])

    def test_brief_contains_exact_temporal_path_and_decision_changing_gap_once(self):
        context = decision_context()
        brief = notification_ai_decision_brief(context, {})
        prompt = build_notification_ai_decision_prompt(context, {}, max_prompt_bytes=28 * 1024)

        self.assertEqual(AI_DECISION_BRIEF_VERSION, brief["schemaVersion"])
        self.assertEqual(-4.1, brief["currentSituation"]["temporalWindows"][0]["drawdownFromPeakPct"])
        self.assertEqual("task:1", brief["research"]["decisionChangingGaps"][0]["taskId"])
        self.assertIn('"schemaVersion":"investment-ai-decision-brief-v1"', prompt)
        self.assertIn('"drawdownFromPeakPct":-4.1', prompt)
        self.assertIn("valuationReferenceOnly=true", prompt)
        self.assertIn("시스템 수집기가", prompt)
        self.assertNotIn('"promptContext"', prompt)
        self.assertLessEqual(len(prompt.encode("utf-8")), 28 * 1024)

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
            max_prompt_bytes=24 * 1024,
        )

        self.assertLessEqual(len(prompt.encode("utf-8")), 24 * 1024)
        self.assertIn('"schemaVersion":"investment-ai-decision-brief-v1"', prompt)
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


if __name__ == "__main__":
    unittest.main()
