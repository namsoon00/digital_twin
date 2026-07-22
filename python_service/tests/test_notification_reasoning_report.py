import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.application.notification_ai_gate_audit import context_with_validated_ai_response
from digital_twin.application.notification_service import NotificationQueueRunner
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.message_types import INVESTMENT_INSIGHT, OPERATOR_REASONING_REPORT
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.notification_ai_gate_validation import (
    build_notification_ai_gate_prompt,
    validated_response_from_payload,
)
from digital_twin.domain.ontology_relation_execution_plan import execution_plan_from_relation_context
from digital_twin.domain.notification_reasoning_report import (
    build_notification_reasoning_report,
    render_operator_reasoning_report,
)
from digital_twin.domain.notification_rules import default_notification_rule
from digital_twin.domain.notification_templates import NotificationTemplate, render_notification
from digital_twin.domain.notifications import NotificationJob, notification_debug_number
from digital_twin.infrastructure.model_reviewer import codex_command


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
            "valuationDataStateLabel": "일부 자료만 있음",
            "valuationApprovalStatus": "ai_applied_pending_review",
            "valuationDataStatus": "partial",
            "valuationExplanation": "비트코인 보유가치를 기준으로 계산한 검토 전 초안입니다.",
            "directNewsCount": 1,
            "btcChange24h": 3.4,
            "btcChange7d": 1.6,
            "missingData": [{"key": "tradeStrength", "label": "체결강도", "effect": "실제 매수·매도 힘 판단이 제한됩니다."}],
        },
        "activeRules": [
            {
                "ruleId": "graph.execution.capacity.v1",
                "label": "보유 종목 + 작은 실행 노출 → 실행 가능 용량 확인",
                "reviewLevel": "act",
                "reviewLabel": "대응 준비",
                "dataState": "sufficient",
                "dataStateLabel": "판단에 필요한 자료 있음",
                "evidenceRole": "context",
                "evidenceState": {"dataState": "sufficient", "evidenceRole": "context"},
                "inferenceTraceId": "trace-execution",
            },
            {
                "ruleId": "graph.disclosure.event_risk.v1",
                "label": "보유 종목 + 공시 이벤트 → 공시 위험 확인",
                "reviewLevel": "act",
                "reviewLabel": "대응 준비",
                "dataState": "sufficient",
                "dataStateLabel": "판단에 필요한 자료 있음",
                "evidenceRole": "risk",
                "evidenceState": {"dataState": "sufficient", "evidenceRole": "risk"},
                "inferenceTraceId": "trace-disclosure",
            },
        ],
        "referenceRules": [{
            "ruleId": "graph.benchmark.beta.v1",
            "label": "시장 민감도 참고",
            "reviewLevel": "observe",
            "dataState": "partial",
            "evidenceRole": "context",
            "referenceOnly": True,
        }],
        "decision": {
            "label": "공시 이벤트 위험 점검",
            "selectedRuleId": "graph.disclosure.event_risk.v1",
            "selectedInferenceTraceId": "trace-disclosure",
            "decisionStage": "EVENT_RISK_REVIEW",
            "actionGroup": "eventRisk",
            "reviewLevel": "act",
            "reviewLabel": "대응 준비",
            "dataState": "sufficient",
            "dataStateLabel": "판단에 필요한 자료 있음",
            "changeState": "new-evidence",
            "changeStateLabel": "새 뉴스·공시·근거",
            "conflictState": "mixed",
            "conflictStateLabel": "위험과 반대 근거가 함께 있음",
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
        "reviewLevel": "act",
        "reviewLevelLabel": "대응 준비",
        "dataState": "sufficient",
        "dataStateLabel": "판단에 필요한 자료 있음",
        "changeState": "new-evidence",
        "changeStateLabel": "새 뉴스·공시·근거",
        "conflictState": "mixed",
        "conflictStateLabel": "위험과 반대 근거가 함께 있음",
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
                "payload": {"sourceTrustState": "trusted", "relevanceState": "direct", "materialityState": "notable"},
            }]
        },
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
        validation_state="ready",
        data_state="sufficient",
        review_level="act",
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
    def test_notification_ai_can_pin_a_cli_compatible_model(self):
        with mock.patch("digital_twin.infrastructure.model_reviewer.shutil.which", return_value="/usr/local/bin/codex"):
            command = codex_command("gpt-5.4")

        self.assertIn("--model gpt-5.4", command)
        self.assertIn("exec --skip-git-repo-check", command)

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

        self.assertTrue(rule.enabled)
        self.assertFalse(rule.state_cooldown_enabled)
        self.assertFalse(rule.similarity_enabled)
        self.assertTrue(rendered.startswith("🛠 운영자 추론 보고서"))
        self.assertIn("InferenceBox", rendered)
        self.assertNotIn("🔔 새 알림", rendered)
        self.assertIn("알림 추적", rendered)

    def test_customer_message_keeps_valuation_articles_and_adds_reasoning_sections(self):
        context, article_url = notification_context()
        context.update({
            "testDispatch": True,
            "notificationNumber": "N-TEST1234",
        })
        enriched = context_with_validated_ai_response(context, validated_response(article_url))
        message = render_notification(NotificationTemplate.default(INVESTMENT_INSIGHT), enriched)

        self.assertIn("<b>왜 알림이 왔나요?</b>", message)
        self.assertIn("<b>핵심 근거</b>", message)
        self.assertIn("<b>다음 조건</b>", message)
        self.assertIn("<b>밸류에이션</b>", message)
        self.assertIn("ai-bitcoin-proxy-nav-draft", message)
        self.assertIn("<b>뉴스·공시 요약</b>", message)
        self.assertIn("핵심 사실: 회사는 신규 자금조달 계획을 공시했습니다", message)
        self.assertIn(article_url, message)
        self.assertIn("대응 준비", message)
        self.assertIn("판단에 필요한 자료 있음", message)
        self.assertNotIn("/100점", message)
        self.assertNotIn("점수 안내", message)
        self.assertNotIn("<b>관계 분석으로 새로 확인한 사실</b>", message)
        self.assertNotIn("<b>전략 가이드</b>", message)
        self.assertIn("분석: [AI] 제목/RSS 요약 기반", message)
        self.assertIn("🧪 테스트 알림", message)
        self.assertIn("일부 수급·추세 조건은 메시지 검증용 테스트값", message)
        self.assertIn("N-TEST1234", message)
        self.assertNotIn("EVENT_RISK_REVIEW", message)
        self.assertNotIn("graph.disclosure.event_risk.v1", message)

    def test_investment_alert_excludes_other_symbol_news_from_research_refresh_snapshot(self):
        hynix_url = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260716000582"
        strategy_url = "https://news.example.com/strategy-strc-cross-symbol-leak"
        context = {
            "messageType": "investmentInsight",
            "messageDeliveryLevel": "absoluteBeginner",
            "displayTarget": "SK하이닉스 / 000660",
            "target": "000660",
            "symbol": "000660",
            "rawSymbol": "000660",
            "title": "SK하이닉스",
            "referenceDate": "2026-07-20 08:04 KST",
            "rawLines": ["현재가: 1,842,000원", "수익률: -25.5%"],
            "ontologyRelationContext": {
                "source": "typedbInferenceBox",
                "graphStore": "typedb",
                "graphStoreUsed": True,
                "nativeTypeDbReasoningUsed": True,
                "subject": {"symbol": "000660", "name": "SK하이닉스", "market": "KR"},
                "facts": {
                    "symbol": "000660",
                    "name": "SK하이닉스",
                    "isHolding": True,
                    "researchEvidence": [{
                        "evidenceId": "research:000660:dart:20260716000582",
                        "symbol": "000660",
                        "kind": "disclosure",
                        "source": "OpenDART",
                        "title": "임원ㆍ주요주주특정증권등소유상황보고서",
                        "url": hynix_url,
                    }],
                },
                "researchCycle": {
                    "symbol": "000660",
                    "status": "reasoning-refresh-failed",
                    "reasoningRefresh": {
                        "status": "error",
                        "refreshed": False,
                        "state": {
                            "externalSignals": {
                                "researchEvidence": {
                                    "MSTR": [{
                                        "evidenceId": "research:MSTR:news:cross-symbol",
                                        "symbol": "MSTR",
                                        "kind": "news",
                                        "title": "Cross-symbol Strategy STRC article",
                                        "url": strategy_url,
                                    }]
                                }
                            }
                        },
                    },
                },
            },
        }
        payload = {
            "action": "HOLD",
            "confidence": 60,
            "summary": "보유 상태를 다시 확인합니다.",
            "opinion": "가격과 공시를 확인합니다.",
            "evidence": ["손실 구간입니다.", "공시가 있습니다."],
            "counterEvidence": ["추가 수급은 확인되지 않았습니다."],
            "invalidationCondition": "가격이 회복되면 다시 판단합니다.",
            "nextChecks": ["공시 원문을 확인합니다."],
            "sourceUrls": [strategy_url],
            "referenceDate": "2026-07-20 08:04 KST",
        }

        prompt = build_notification_ai_gate_prompt(context)
        response = validated_response_from_payload(context, payload, source="test AI")
        message = context_with_validated_ai_response(context, response)["telegramMessage"]

        self.assertNotIn("Cross-symbol Strategy STRC article", prompt)
        self.assertNotIn(strategy_url, prompt)
        self.assertEqual([hynix_url], response.source_urls)
        self.assertIn(hynix_url, message)
        self.assertNotIn(strategy_url, message)
        self.assertNotIn("외 1건은 웹 상세에서 확인", message)

    def test_beginner_message_preserves_nested_article_summary_when_duplicate_headline_is_sparse(self):
        context, _article_url = notification_context()
        article_url = "https://news.example.com/tesla-founder-led"
        context.update({
            "displayTarget": "Tesla / TSLA",
            "target": "Tesla / TSLA",
            "symbol": "TSLA",
            "newsHeadlines": {
                "items": [{
                    "kind": "news",
                    "title": "Tesla remains founder-led ahead of earnings",
                    "url": article_url,
                    "domain": "Yahoo Finance",
                    "publishedAt": "2026-07-20T04:25:00+09:00",
                    "payload": {"sourceReliability": 0.68, "relevanceScore": 97, "materialityScore": 82.2},
                }]
            },
            "researchEvidence": [{
                "kind": "news",
                "title": "Tesla remains founder-led ahead of earnings",
                "url": article_url,
                "source": "Yahoo Finance",
                "publishedAt": "2026-07-20T04:25:00+09:00",
                "rawPayload": {
                    "articleFacts": {
                        "readStatus": "body",
                        "readStatusLabel": "전체 본문 읽음",
                        "summaryKo": "기사는 일론 머스크의 창업자 중심 경영이 테슬라의 빠른 의사결정에는 도움이 되지만, 실적 발표를 앞두고 높은 기업가치와 자동차 판매 둔화가 부담이라고 설명합니다.",
                    },
                    "stockImpactLabel": "혼재",
                    "stockImpactReasonKo": "경영 일관성은 긍정적이지만 실적과 밸류에이션 부담이 함께 있어 방향은 혼재입니다.",
                },
            }],
        })
        response = validated_response(article_url)
        response.source_urls = [article_url]

        message = context_with_validated_ai_response(context, response)["telegramMessage"]

        self.assertIn("<b>뉴스·공시 요약</b>", message)
        self.assertIn("기사는 일론 머스크의 창업자 중심 경영", message)
        self.assertIn("투자 영향: 경영 일관성은 긍정적", message)
        self.assertNotIn("기사 본문 요약이 아직 준비되지 않았습니다", message)
        self.assertEqual(1, message.count("기사는 일론 머스크의 창업자 중심 경영"))

    def test_watchlist_strategy_guide_does_not_describe_holdings(self):
        context, article_url = notification_context()
        context["ontologyRelationContext"]["facts"].update({"isHolding": False, "isWatchlist": True})
        response = validated_response(article_url)
        response.strategy_guide = {"invalidationCondition": "가격이 약해지면 보유 의견이 약해집니다."}

        message = context_with_validated_ai_response(context, response)["telegramMessage"]

        self.assertIn("관심 유지 의견이 약해집니다", message)
        self.assertNotIn("보유 의견이 약해집니다", message)

    def test_watchlist_relation_watch_plan_uses_entry_language(self):
        plan = execution_plan_from_relation_context(
            {"symbol": "TSLA", "name": "Tesla", "isWatchlist": True},
            {"label": "관계 규칙 관찰", "targetRole": "watchlist", "actionGroup": "valuation"},
            [],
        )

        self.assertEqual("WAIT_FOR_ENTRY_CONFIRMATION", plan["primaryAction"])
        self.assertEqual("관심 유지, 다음 진입 조건 확인", plan["primaryActionLabel"])
        self.assertNotIn("보유 유지", plan["primaryActionLabel"])

    def test_account_delivery_context_includes_strategy_profile(self):
        account = AccountConfig(
            "main",
            "기본 계정",
            "toss",
            "https://example.test",
            "",
            "",
            "",
            ["MSTR"],
            investment_strategy_profile="aggressive",
        )

        context = account.message_delivery_context()

        self.assertEqual("공격형", context["investmentStrategyProfileLabel"])
        self.assertEqual("aggressive", context["investmentStrategyProfile"])

    def test_operator_report_connects_selected_decision_to_categorical_states(self):
        context, article_url = notification_context()
        context["notificationNumber"] = "N-ABCDEF12"
        enriched = context_with_validated_ai_response(context, validated_response(article_url))
        report = build_notification_reasoning_report(enriched, "customer-job", enriched["telegramMessage"])
        message = render_operator_reasoning_report(report)

        self.assertEqual("act", report.state_audit["reviewLevel"])
        self.assertEqual("sufficient", report.state_audit["dataState"])
        self.assertEqual("new-evidence", report.state_audit["changeState"])
        self.assertEqual("mixed", report.state_audit["conflictState"])
        self.assertTrue(report.state_audit["selectedRuleIsActive"])
        self.assertIn("확인 단계: 대응 준비", message)
        self.assertNotIn("판단 점수", message)
        self.assertIn("graph.disclosure.event_risk.v1", message)
        self.assertIn("TypeDB InferenceBox 실행", message)
        self.assertIn("BTC 보유가치/NAV + 추세 보정", message)
        self.assertIn(article_url, message)
        self.assertNotIn("telegramBotToken", message)
        self.assertNotIn("clientSecret", message)

    def test_operator_report_omits_non_finite_empty_fact_rows(self):
        context, _article_url = notification_context()
        context["ontologyRelationContext"]["facts"]["averagePrice"] = float("nan")

        report = build_notification_reasoning_report(context, "customer-job", "밸류에이션")

        self.assertFalse(any(item["key"] == "averagePrice" for item in report.input_facts))

    def test_direct_test_operator_audit_explains_policy_bypass_and_no_articles(self):
        context, _article_url = notification_context()
        context.update({
            "testDispatch": True,
            "notificationTestBypassPolicy": True,
            "newsHeadlines": {"items": []},
        })
        report = build_notification_reasoning_report(context, "customer-job", "밸류에이션")

        article_check = next(item for item in report.validation_checks if item["name"] == "사용자 메시지 기사·공시 보존")
        self.assertEqual("해당 없음", article_check["status"])
        self.assertEqual("test-direct-send", report.delivery_audit["decision"])
        self.assertIn("정책을 우회", report.delivery_audit["reasons"][0])

    def test_runner_enqueues_operator_report_as_independent_job_for_same_account(self):
        context, article_url = notification_context()
        context["testDispatch"] = True
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
        self.assertTrue(sent[1].startswith("🧪 테스트 발송 · 운영자 검증용"))
        self.assertIn("🛠 운영자 추론 보고서", sent[1])
        self.assertIn(notification_debug_number(customer_job.job_id), sent[1])

    def test_runner_suppresses_pending_operator_report_when_disabled(self):
        operator_job = NotificationJob.create(
            "🛠 운영자 추론 보고서",
            account_id="main",
            account_label="기본 계정",
            message_type=OPERATOR_REASONING_REPORT,
        )
        queue = MemoryQueue([operator_job])
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
            operator_reports_enabled=False,
        )

        self.assertEqual(1, runner.run_once(limit=10))
        self.assertEqual("suppressed", operator_job.status)
        self.assertIn("비활성화", operator_job.last_error)
        self.assertEqual([], sent)

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
