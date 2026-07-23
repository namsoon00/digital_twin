import json
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.notification_reverse_reasoning import (  # noqa: E402
    TRACE_VERSION,
    build_notification_reverse_reasoning_trace,
)
from digital_twin.domain.notifications import NotificationJob  # noqa: E402
from digital_twin.infrastructure.web_server import (  # noqa: E402
    notification_job_detail_payload,
    notification_job_list_payload,
)


def notification_context():
    return {
        "messageType": "investmentInsight",
        "accountId": "main",
        "displayTarget": "삼성전자 / 005930",
        "referenceDate": "2026-07-23T01:30:00Z",
        "clientSecret": "must-not-leak",
        "deliveryDecision": "send",
        "deliveryGateState": "passed",
        "deliveryGateReason": "새 공시 근거와 위험 관계가 함께 확인됐습니다.",
        "deliveryReasons": ["관계 변화가 재알림 기준을 넘었습니다."],
        "cooldownReason": "새 근거로 반복 보류를 해제했습니다.",
        "dataFreshnessStatus": "fresh",
        "dataFreshnessReason": "필수 시장 데이터가 허용 시간 안에 수집됐습니다.",
        "newsHeadlines": {
            "items": [{
                "title": "삼성전자 공시 관련 기사",
                "url": "https://example.test/samsung-disclosure",
                "domain": "example.test",
                "publishedAt": "2026-07-23T01:20:00Z",
                "stockImpactLabel": "주의",
            }],
        },
        "notificationAiValidatedResponse": {
            "action": "TRIM",
            "actionLabel": "분할축소",
            "summary": "가격 흐름과 공시 위험이 함께 약해져 일부 비중 축소를 우선 검토합니다.",
            "validationState": "ready",
            "validationLabel": "검증 완료",
            "dataState": "sufficient",
            "dataStateLabel": "판단에 필요한 자료 있음",
            "reviewLevel": "act",
            "reviewLabel": "대응 준비",
            "precomputedAction": "HOLD",
            "disagreementReason": "매수 호가 우위보다 공시와 중기 추세 약화의 영향이 더 컸습니다.",
            "selectedHypothesisId": "hypothesis:risk",
            "hypothesisComparisonState": "completed",
            "hypothesisSelectionSource": "ai-comparison",
            "unresolvedQuestions": ["공시 원문에서 발행 조건을 확인합니다."],
            "hypotheses": [
                {"hypothesisId": "hypothesis:risk", "verdict": "supported", "reasoning": "공시와 하락 추세가 같은 위험 방향입니다."},
                {"hypothesisId": "hypothesis:support", "verdict": "weakened", "reasoning": "호가 우위는 단기 신호여서 중기 위험을 뒤집기 부족합니다."},
                {"hypothesisId": "hypothesis:safety", "verdict": "supported", "reasoning": "반대 근거가 있어 전량 매도 대신 분할축소가 적절합니다."},
            ],
            "rawResponse": "must-not-leak",
        },
        "ontologyRelationContext": {
            "engineVersion": "typedb-inferencebox-relation-context-v1",
            "graphStore": "typedb",
            "graphStoreUsed": True,
            "nativeTypeDbReasoningUsed": True,
            "fallbackUsed": False,
            "inferenceGenerationId": "generation:20260723:005930",
            "inferenceGenerationAt": "2026-07-23T01:30:00Z",
            "ruleboxShortHash": "rulebox-abc123",
            "portfolioWorldId": "portfolio:local:main",
            "marketWorldId": "market:shared:KR",
            "subject": {"symbol": "005930", "name": "삼성전자", "market": "KR"},
            "facts": {
                "currentPrice": 70000,
                "averagePrice": 78000,
                "profitLossRate": -10.2,
                "ma20": 76000,
                "ma20Distance": -7.9,
                "foreignNetVolume": -500000,
                "institutionNetVolume": -220000,
                "missingData": [{"label": "공시 본문", "effect": "발행 조건 확인 전 위험 강도를 제한합니다."}],
            },
            "decision": {
                "label": "공시 이벤트 위험 점검",
                "selectedRuleId": "graph.disclosure.event-risk.v1",
            },
            "executionPlan": {"primaryActionLabel": "분할축소 우선 검토"},
            "activeRules": [{
                "ruleId": "graph.disclosure.event-risk.v1",
                "label": "보유 종목 + 공시 이벤트 → 공시 위험 확인",
                "inferenceTraceId": "trace:disclosure",
                "reviewLabel": "대응 준비",
                "dataStateLabel": "판단에 필요한 자료 있음",
                "evidenceRole": "risk",
                "evidence": ["신규 공시", "보유 종목"],
            }],
            "graphStoreInference": {
                "entityCount": 24,
                "relationCount": 18,
                "traceCount": 1,
                "traces": [{
                    "id": "trace:disclosure",
                    "ruleId": "graph.disclosure.event-risk.v1",
                    "matchedConditions": [
                        {"conditionId": "holding", "summary": "삼성전자 보유 수량 10주"},
                        {"conditionId": "disclosure", "summary": "신규 자금조달 공시 확인"},
                    ],
                }],
            },
            "investmentBrain": {
                "hypothesisSet": {
                    "hypotheses": [
                        {
                            "hypothesisId": "hypothesis:risk",
                            "templateLabel": "공시와 추세가 겹친 위험 경로",
                            "claim": "공시 이벤트와 중기 추세 약화가 함께 위험을 설명합니다.",
                            "stance": "risk",
                            "evidenceState": "supported",
                            "supportingRuleIds": ["graph.disclosure.event-risk.v1"],
                            "supportingEvidenceIds": ["evidence:disclosure"],
                            "counterEvidenceIds": ["evidence:bid"],
                            "causalPathIds": ["trace:disclosure"],
                            "assumptions": ["공시 영향이 아직 가격에 반영 중입니다."],
                            "invalidationConditions": ["공시 내용이 위험하지 않고 가격이 회복하면 약화됩니다."],
                            "horizon": "short-term",
                            "verificationStatus": "typedb-current-generation",
                        },
                        {
                            "hypothesisId": "hypothesis:support",
                            "templateLabel": "단기 호가 방어 경로",
                            "claim": "매수 호가 우위가 단기 반등을 지지합니다.",
                            "stance": "support",
                            "evidenceState": "contested",
                            "supportingRuleIds": ["graph.orderbook.support.v1"],
                        },
                        {
                            "hypothesisId": "hypothesis:safety",
                            "templateLabel": "증거 충분성 안전 경로",
                            "claim": "반대 근거가 있어 전량 처분 판단은 보수적으로 봅니다.",
                            "stance": "context",
                            "evidenceState": "supported",
                            "supportingRuleIds": [],
                        },
                    ],
                },
            },
        },
    }


class NotificationReverseReasoningTests(unittest.TestCase):
    def test_trace_reconstructs_the_saved_inference_chain(self):
        trace = build_notification_reverse_reasoning_trace(notification_context(), job_id="job-1", job_status="done")

        self.assertEqual(TRACE_VERSION, trace["version"])
        self.assertEqual("ready", trace["status"])
        self.assertTrue(trace["snapshotBound"])
        self.assertEqual("generation:20260723:005930", trace["snapshot"]["inferenceGenerationId"])
        self.assertEqual("분할축소", trace["finalDecision"]["actionLabel"])
        self.assertTrue(trace["aiComparison"]["changed"])
        self.assertEqual("hypothesis:risk", trace["selectedHypothesis"]["hypothesisId"])
        self.assertTrue(trace["matchedRules"][0]["selected"])
        self.assertEqual("holding", trace["inferenceTraces"][0]["conditions"][0]["label"])
        self.assertEqual(2, len(trace["alternativeHypotheses"]))
        self.assertEqual("https://example.test/samsung-disclosure", trace["sources"][0]["url"])
        self.assertIn("공시 본문: 발행 조건 확인 전 위험 강도를 제한합니다.", trace["missingData"])

        serialized = json.dumps(trace, ensure_ascii=False)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("rawResponse", serialized)
        self.assertNotIn("clientSecret", serialized)

    def test_trace_makes_missing_historical_context_explicit(self):
        trace = build_notification_reverse_reasoning_trace({"title": "이전 알림"}, job_id="legacy-job")

        self.assertEqual("unavailable", trace["status"])
        self.assertFalse(trace["snapshotBound"])
        self.assertIn("저장되지 않았습니다", trace["reason"])

    def test_detail_endpoint_exposes_trace_without_bloating_list_payload(self):
        job = NotificationJob.create(
            "알림 본문",
            account_id="main",
            account_label="기본 계정",
            message_type="investmentInsight",
            context=notification_context(),
        )

        class Queue:
            def get(self, job_id):
                return job if job_id == job.job_id else None

        with mock.patch("digital_twin.infrastructure.web_server.notification_queue_store", return_value=Queue()):
            detail = notification_job_detail_payload(job.job_id)

        self.assertIn("reasoningTrace", detail["job"])
        self.assertEqual("ready", detail["job"]["reasoningTrace"]["status"])
        self.assertNotIn("reasoningTrace", notification_job_list_payload(job, stale_minutes=30))


if __name__ == "__main__":
    unittest.main()
