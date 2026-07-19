import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.notification_ai_gate_audit import context_with_validated_ai_response
from digital_twin.application.notification_service import NotificationQueueRunner
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.message_types import INVESTMENT_INSIGHT, OPERATOR_REASONING_REPORT
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notification_reasoning_report import (
    build_notification_reasoning_report,
    render_operator_reasoning_report,
)
from digital_twin.domain.notification_rules import default_notification_rule
from digital_twin.domain.notification_templates import NotificationTemplate, render_notification
from digital_twin.domain.notifications import NotificationJob, notification_debug_number


def relation_context():
    return {
        "engineVersion": "typedb-inferencebox-relation-context-v1",
        "source": "typedbInferenceBox",
        "graphStore": "typedb",
        "graphStoreUsed": True,
        "fallbackUsed": False,
        "nativeTypeDbReasoningUsed": True,
        "inferenceGenerationId": "generation-42",
        "inferenceGenerationAt": "2026-07-19T06:00:00Z",
        "ruleboxShortHash": "abc123",
        "ruleboxRuleCount": 12,
        "ruleboxConditionCount": 35,
        "ruleboxDerivationCount": 4,
        "subject": {"symbol": "MSTR", "name": "Strategy", "market": "US"},
        "facts": {
            "symbol": "MSTR",
            "name": "Strategy",
            "currency": "USD",
            "isHolding": True,
            "currentPrice": 97.69,
            "averagePrice": 88.9,
            "profitLossRate": 9.9,
            "ma20": 98.43,
            "ma20Distance": -0.8,
            "ma60": 139.22,
            "ma60Distance": -29.8,
            "valuationRows": [{"sourceType": "ai"}],
            "valuationMethod": "ai-bitcoin-proxy-nav-draft",
            "valuationFormula": "BTC 보유가치/NAV + 추세 보정",
            "valuationSubstitution": "기초 NAV $105 x 추세 보정 0.9 = $94.50",
            "valuationCurrentPrice": 97.69,
            "valuationFairValue": 94.5,
            "valuationMarginOfSafetyPct": -3.38,
            "valuationMinimumMarginOfSafetyPct": 10,
            "valuationSourceLabel": "AI 제안",
            "valuationReliabilityLabel": "AI 초안(사용자 검토 전)",
            "valuationReliabilityScore": 58,
            "valuationApprovalStatus": "ai_applied_pending_review",
            "valuationDataStatus": "partial",
            "valuationExplanation": "비트코인 보유가치를 기준으로 계산한 검토 전 초안입니다.",
            "directNewsCount": 1,
            "btcChange24h": 3.4,
            "btcChange7d": 1.6,
            "dataQualityScore": 88,
            "missingData": [{"key": "tradeStrength", "label": "체결강도", "effect": "실제 매수·매도 힘 판단이 제한됩니다."}],
        },
        "activeRules": [
            {
                "ruleId": "graph.execution.capacity.v1",
                "label": "보유 종목 + 작은 실행 노출 → 실행 가능 용량 확인",
                "strengthScore": 94,
                "confidence": 94,
                "scoreBreakdown": {"actionability": 94, "finalStrength": 94},
                "inferenceTraceId": "trace-execution",
            },
            {
                "ruleId": "graph.disclosure.event_risk.v1",
                "label": "보유 종목 + 공시 이벤트 → 공시 위험 확인",
                "strengthScore": 78,
                "confidence": 86,
                "scoreBreakdown": {"riskPressure": 78, "finalStrength": 78},
                "inferenceTraceId": "trace-disclosure",
            },
        ],
        "referenceRules": [{
            "ruleId": "graph.benchmark.beta.v1",
            "label": "시장 민감도 참고",
            "strengthScore": 72,
            "confidence": 80,
            "referenceOnly": True,
        }],
        "decision": {
            "label": "공시 이벤트 위험 점검",
            "score": 78,
            "selectedRuleId": "graph.disclosure.event_risk.v1",
            "selectedInferenceTraceId": "trace-disclosure",
            "decisionStage": "EVENT_RISK_REVIEW",
            "actionGroup": "eventRisk",
            "scoreBreakdown": {"riskPressure": 78, "finalStrength": 78},
            "targetRole": "holding",
            "actionPolicy": "HOLDING_MANAGEMENT",
        },
        "executionPlan": {
            "decisionLabel": "공시 이벤트 위험 점검",
            "primaryAction": "EVENT_RISK_REVIEW",
            "primaryActionLabel": "보유 이유와 공시 영향 재확인",
            "decisionDrivers": [
                {"category": "trend", "direction": "risk", "summary": "현재가가 20일·60일 평균 아래여서 중기 가격 흐름이 약합니다."},
                {"category": "news", "direction": "risk", "summary": "보유 종목과 직접 관련된 공시 위험이 확인됐습니다."},
            ],
        },
        "scoreBreakdown": {"riskPressure": 78, "supportEvidence": 34, "dataConfidence": 88, "finalStrength": 94},
        "whyNow": {
            "changeDrivers": ["직접 뉴스/리서치 근거 1건이 추론에 포함됐습니다.", "현재 판단 단계는 EVENT_RISK_REVIEW입니다."],
        },
        "graphStoreInference": {"entityCount": 21, "relationCount": 18, "traceCount": 4},
        "evidenceSubgraph": {"nodes": [{"id": "stock:MSTR"}], "edges": [{"type": "HAS_DISCLOSURE"}], "matchedRuleIds": ["graph.disclosure.event_risk.v1"]},
        "missingData": [{"key": "tradeStrength", "label": "체결강도", "effect": "실제 매수·매도 힘 판단이 제한됩니다."}],
    }


def notification_context():
    article_url = "https://example.com/mstr-filing"
    return {
        "messageType": INVESTMENT_INSIGHT,
        "accountId": "main",
        "accountLabel": "기본 계정",
        "headline": "[관찰] Strategy",
        "displayTarget": "스트래티지 / Strategy / MSTR",
        "target": "스트래티지 / Strategy / MSTR",
        "symbol": "MSTR",
        "rawSymbol": "MSTR",
        "messageDeliveryLevel": "absoluteBeginner",
        "investmentStrategyProfileLabel": "공격형",
        "messageDeliveryLevelLabel": "왕초보",
        "rawLines": [
            "현재가: $97.69",
            "평균매입가: $88.9",
            "수익률: +9.9%",
            "추세: 20일선 $98.43보다 0.8% 낮음, 60일선 $139.22보다 29.8% 낮음",
            "수급: 거래량 61,577(0.2x)",
        ],
        "ontologyRelationContext": relation_context(),
        "newsHeadlines": {
            "items": [{
                "title": "Strategy files financing update",
                "summary": "회사는 신규 자금조달 계획을 공시했습니다.",
                "url": article_url,
                "domain": "example.com",
                "publishedAt": "2026-07-19T05:30:00Z",
                "payload": {"sourceReliability": 0.82, "relevanceScore": 91, "materialityScore": 74},
            }]
        },
        "honeyScore": 76,
        "honeyThreshold": 65,
        "honeyDecision": "send",
        "honeyReasons": ["관계 인사이트 기준 통과", "본문 있음"],
        "honeyStateReason": "새 공시 근거가 추가되어 쿨다운을 해제했습니다.",
        "dataFreshnessStatus": "fresh",
        "dataFreshnessReason": "모든 필수 데이터가 허용 시간 안에 수집됐습니다.",
    }, article_url


def validated_response(article_url):
    return NotificationAIValidatedResponse(
        action="HOLD",
        action_label="보유",
        confidence=74,
        precomputed_action="HOLD",
        summary="수익을 지키면서 공시 영향과 가격 회복을 확인합니다.",
        opinion="현재는 보유하되 공시 원문을 확인합니다.",
        evidence=["수익 구간입니다.", "직접 공시가 확인됐습니다."],
        counter_evidence=["60일 평균 아래입니다."],
        next_checks=["공시 원문과 거래량을 확인합니다."],
        missing_data_impact=["체결강도가 없어 실제 매수·매도 힘 판단이 제한됩니다."],
        source_urls=[article_url],
        reference_date="2026-07-19 14:30 KST",
        source="test AI",
    )


class MemoryQueue:
    def __init__(self, jobs=None):
        self.items = list(jobs or [])

    def enqueue(self, job):
        if any(item.dedupe_key and item.dedupe_key == job.dedupe_key for item in self.items):
            return False
        self.items.append(job)
        return True

    def pending(self, limit=10):
        return [item for item in self.items if item.status in {"pending", "failed"}][:limit]

    def mark_processing(self, job):
        job.status = "processing"
        job.attempts += 1

    def mark_done(self, job):
        job.status = "done"

    def mark_failed(self, job, error):
        job.status = "failed"
        job.last_error = str(error)

    def mark_suppressed(self, job, reason):
        job.status = "suppressed"
        job.last_error = str(reason)


class AccountRepository:
    def __init__(self, account):
        self.account = account

    def load_all(self):
        return [self.account]


class NotificationReasoningReportTests(unittest.TestCase):
    def test_operator_message_type_uses_system_delivery_policy_and_raw_template(self):
        rule = default_notification_rule(OPERATOR_REASONING_REPORT)
        rendered = render_notification(
            NotificationTemplate.default(OPERATOR_REASONING_REPORT),
            {
                "messageType": OPERATOR_REASONING_REPORT,
                "telegramMessage": "🛠 운영자 추론 보고서\n• InferenceBox: 정상",
                "jobId": "operator-job-1234",
            },
        )

        self.assertEqual(85, rule.base_score)
        self.assertEqual(20, rule.threshold)
        self.assertFalse(rule.similarity_enabled)
        self.assertTrue(rendered.startswith("🛠 운영자 추론 보고서"))
        self.assertIn("InferenceBox", rendered)
        self.assertNotIn("🔔 새 알림", rendered)
        self.assertIn("알림 추적", rendered)

    def test_customer_message_keeps_valuation_articles_and_adds_reasoning_sections(self):
        context, article_url = notification_context()
        enriched = context_with_validated_ai_response(context, validated_response(article_url))
        message = enriched["telegramMessage"]

        self.assertIn("<b>왜 알림이 왔나요?</b>", message)
        self.assertIn("<b>온톨로지가 새로 확인한 사실</b>", message)
        self.assertIn("<b>신뢰도와 부족 데이터</b>", message)
        self.assertIn("<b>밸류에이션</b>", message)
        self.assertIn("BTC 보유가치/NAV + 추세 보정", message)
        self.assertIn("<b>원문/출처</b>", message)
        self.assertIn(article_url, message)
        self.assertIn("[관계 분석] 높음 (78.0점)", message)
        self.assertNotIn("EVENT_RISK_REVIEW", message)
        self.assertNotIn("graph.disclosure.event_risk.v1", message)

    def test_operator_report_separates_selected_decision_score_from_highest_relation(self):
        context, article_url = notification_context()
        context["notificationNumber"] = "N-ABCDEF12"
        enriched = context_with_validated_ai_response(context, validated_response(article_url))
        report = build_notification_reasoning_report(enriched, "customer-job", enriched["telegramMessage"])
        message = render_operator_reasoning_report(report)

        self.assertEqual(78, report.score_audit["decisionScore"])
        self.assertEqual(78, report.score_audit["selectedRuleScore"])
        self.assertEqual(94, report.score_audit["highestRelationScore"])
        self.assertTrue(report.score_audit["selectedRuleScoreMatches"])
        self.assertIn("판단 점수: 78.0점", message)
        self.assertIn("가장 강한 관계: 보유 종목 + 작은 실행 노출", message)
        self.assertIn("graph.disclosure.event_risk.v1", message)
        self.assertIn("TypeDB InferenceBox 실행", message)
        self.assertIn("BTC 보유가치/NAV + 추세 보정", message)
        self.assertIn(article_url, message)
        self.assertNotIn("telegramBotToken", message)
        self.assertNotIn("clientSecret", message)

    def test_runner_enqueues_operator_report_as_independent_job_for_same_account(self):
        context, article_url = notification_context()
        enriched = context_with_validated_ai_response(context, validated_response(article_url))
        customer_job = NotificationJob.create(
            enriched["telegramMessage"],
            account_id="main",
            account_label="기본 계정",
            message_type=INVESTMENT_INSIGHT,
            context=enriched,
        )
        queue = MemoryQueue([customer_job])
        account = AccountConfig("main", "기본 계정", "toss", "https://example.test", "", "", "", ["MSTR"])
        sent = []

        class FakeNotifier:
            def send(self, message):
                sent.append(message)
                return SimpleNamespace(delivered=True, reason="")

        runner = NotificationQueueRunner(
            queue,
            AccountRepository(account),
            lambda _account: FakeNotifier(),
            now_provider=lambda: datetime(2026, 7, 19, 6, 30, tzinfo=timezone.utc),
            operator_reports_enabled=True,
        )

        self.assertEqual(1, runner.run_once(limit=10))
        self.assertEqual("done", customer_job.status)
        operator_jobs = [item for item in queue.items if item.message_type == OPERATOR_REASONING_REPORT]
        self.assertEqual(1, len(operator_jobs))
        self.assertEqual("main", operator_jobs[0].account_id)
        self.assertEqual("pending", operator_jobs[0].status)
        self.assertEqual(notification_debug_number(customer_job.job_id), operator_jobs[0].context["customerNotificationNumber"])

        self.assertEqual(1, runner.run_once(limit=10))
        self.assertEqual("done", operator_jobs[0].status)
        self.assertEqual(2, len(sent))
        self.assertIn("🛠 운영자 추론 보고서", sent[1])
        self.assertIn(notification_debug_number(customer_job.job_id), sent[1])

    def test_operator_delivery_failure_does_not_change_completed_customer_job(self):
        context, article_url = notification_context()
        enriched = context_with_validated_ai_response(context, validated_response(article_url))
        customer_job = NotificationJob.create(
            enriched["telegramMessage"],
            account_id="main",
            account_label="기본 계정",
            message_type=INVESTMENT_INSIGHT,
            context=enriched,
        )
        queue = MemoryQueue([customer_job])
        account = AccountConfig("main", "기본 계정", "toss", "https://example.test", "", "", "", ["MSTR"])
        calls = []

        class FailingSecondNotifier:
            def send(self, message):
                calls.append(message)
                return SimpleNamespace(delivered=len(calls) == 1, reason="operator failed")

        runner = NotificationQueueRunner(
            queue,
            AccountRepository(account),
            lambda _account: FailingSecondNotifier(),
            now_provider=lambda: datetime(2026, 7, 19, 6, 30, tzinfo=timezone.utc),
            operator_reports_enabled=True,
        )

        runner.run_once(limit=10)
        runner.run_once(limit=10)

        operator_job = next(item for item in queue.items if item.message_type == OPERATOR_REASONING_REPORT)
        self.assertEqual("done", customer_job.status)
        self.assertEqual("failed", operator_job.status)
        self.assertEqual("operator failed", operator_job.last_error)

    def test_operator_enqueue_exception_does_not_retry_customer_message(self):
        context, article_url = notification_context()
        enriched = context_with_validated_ai_response(context, validated_response(article_url))
        customer_job = NotificationJob.create(
            enriched["telegramMessage"],
            account_id="main",
            account_label="기본 계정",
            message_type=INVESTMENT_INSIGHT,
            context=enriched,
        )

        class RaisingQueue(MemoryQueue):
            def enqueue(self, job):
                if job.message_type == OPERATOR_REASONING_REPORT:
                    raise RuntimeError("operator queue unavailable")
                return super().enqueue(job)

        queue = RaisingQueue([customer_job])
        account = AccountConfig("main", "기본 계정", "toss", "https://example.test", "", "", "", ["MSTR"])

        class FakeNotifier:
            def send(self, _message):
                return SimpleNamespace(delivered=True, reason="")

        runner = NotificationQueueRunner(
            queue,
            AccountRepository(account),
            lambda _account: FakeNotifier(),
            now_provider=lambda: datetime(2026, 7, 19, 6, 30, tzinfo=timezone.utc),
            operator_reports_enabled=True,
        )

        self.assertEqual(1, runner.run_once(limit=10))
        self.assertEqual("done", customer_job.status)
        self.assertEqual("error", customer_job.context["operatorReasoningReportStatus"])
        self.assertIn("operator queue unavailable", customer_job.context["operatorReasoningReportError"])


if __name__ == "__main__":
    unittest.main()
