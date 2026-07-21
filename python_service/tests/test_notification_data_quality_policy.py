import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.notification_rules import (
    attach_previous_profit_loss_context,
    apply_similarity_rule,
    apply_state_cooldown_rule,
    default_notification_rule,
    evaluate_notification_rule,
    notification_state_group_key,
)
from digital_twin.domain.data_freshness import (
    evaluate_notification_data_freshness,
    sanitize_notification_context_for_freshness,
)
from digital_twin.application.notification_ai_gate_message import (
    notification_cooldown_release_summary,
    notification_reason_summary,
    notification_topline_change_summary,
    prepend_execution_start_badge,
)
from digital_twin.domain.notification_templates import prepend_message_start_badge
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.message_types import INVESTMENT_INSIGHT, WORK_HANDOFF, is_operations_delivery_message_type
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.strategy_alerts import StrategyAlertMixin
from digital_twin.domain.portfolio import utc_now_iso
from digital_twin.application.notification_service import NotificationQueueRunner
from digital_twin.infrastructure.cli import public_settings_payload
from digital_twin.infrastructure.notifications import NotificationResult, TelegramNotifier, notifier_for_operations


class NotificationDataQualityPolicyTests(unittest.TestCase):
    @staticmethod
    def _typedb_relation_context(context):
        """Give cooldown tests the same TypeDB-backed contract as production."""
        prepared = dict(context or {})
        relation = dict(prepared.get("ontologyRelationContext") or {})
        decision = dict(relation.get("decision") or {})
        relation.setdefault("source", "typedbInferenceBox")
        relation.setdefault("graphStore", "typedb")
        relation["graphStoreUsed"] = True
        relation["fallbackUsed"] = False
        relation.setdefault("reviewLevel", "act")
        relation.setdefault("dataState", "sufficient")
        relation.setdefault("changeState", "new-condition")
        relation.setdefault("conflictState", "risk-only")
        decision.setdefault("basis", "typedbInferenceBox")
        decision.setdefault("reviewLevel", relation["reviewLevel"])
        decision.setdefault("dataState", relation["dataState"])
        decision.setdefault("changeState", relation["changeState"])
        decision.setdefault("conflictState", relation["conflictState"])
        relation["decision"] = decision
        prepared["ontologyRelationContext"] = relation
        return prepared

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._notification_job_create = NotificationJob.create

        def create_with_typedb_context(*args, **kwargs):
            message_type = kwargs.get("message_type")
            if message_type == INVESTMENT_INSIGHT:
                kwargs = dict(kwargs)
                kwargs["context"] = cls._typedb_relation_context(kwargs.get("context") or {})
            return cls._notification_job_create(*args, **kwargs)

        cls._notification_job_create_patcher = patch.object(
            NotificationJob,
            "create",
            side_effect=create_with_typedb_context,
        )
        cls._notification_job_create_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls._notification_job_create_patcher.stop()
        super().tearDownClass()

    def test_dispatch_freshness_recomputes_age_instead_of_trusting_stored_status(self):
        decision = evaluate_notification_data_freshness(
            {
                "messageType": INVESTMENT_INSIGHT,
                "dataFreshness": {
                    "source": "KIS price",
                    "stage": "price",
                    "status": "fresh",
                    "sourceAsOf": "2026-07-20T00:00:00Z",
                    "ageMinutes": 0,
                    "maxAgeMinutes": 3,
                },
            },
            settings={"dataFreshnessEnabled": "1"},
            now=datetime(2026, 7, 20, 0, 4, tzinfo=timezone.utc),
        )

        self.assertFalse(decision.should_send)
        self.assertEqual("stale", decision.status)
        self.assertEqual(4, decision.age_minutes)

    def test_ignored_stale_kis_values_are_removed_before_ai_enrichment(self):
        context = {
            "messageType": INVESTMENT_INSIGHT,
            "rawLines": "현재가: 100원\n외국인: 순매도 10주\n체결강도: 88",
            "ontologyRelationContext": {
                "facts": {
                    "currentPrice": 100,
                    "foreignNetVolume": -10,
                    "tradeStrength": 88,
                    "marketSignalCoverage": {
                        "price": {"status": "available"},
                        "investor": {"status": "stale"},
                    },
                },
                "evidenceState": {"appliedFactFields": ["currentPrice"]},
                "decision": {
                    "basis": "typedbInferenceBox",
                    "reviewLevel": "check",
                    "dataState": "sufficient",
                },
            },
            "dataFreshness": {
                "sources": [
                    {"source": "KIS price", "stage": "price", "status": "fresh", "sourceAsOf": "2026-07-20T00:00:00Z", "maxAgeMinutes": 3},
                    {"source": "KIS investor", "stage": "investor", "status": "stale", "sourceAsOf": "2026-07-19T23:50:00Z", "maxAgeMinutes": 5},
                    {"source": "KIS ccnl", "stage": "ccnl", "status": "stale", "sourceAsOf": "2026-07-19T23:50:00Z", "maxAgeMinutes": 2},
                ],
            },
        }
        decision = evaluate_notification_data_freshness(
            context,
            settings={"dataFreshnessEnabled": "1"},
            now=datetime(2026, 7, 20, 0, 1, tzinfo=timezone.utc),
        )

        cleaned = sanitize_notification_context_for_freshness(
            context,
            decision,
            now=datetime(2026, 7, 20, 0, 1, tzinfo=timezone.utc),
        )

        facts = cleaned["ontologyRelationContext"]["facts"]
        self.assertTrue(decision.should_send)
        self.assertEqual(["ccnl", "investor"], cleaned["dataFreshnessExcludedStages"])
        self.assertEqual(100, facts["currentPrice"])
        self.assertNotIn("foreignNetVolume", facts)
        self.assertNotIn("tradeStrength", facts)
        self.assertNotIn("외국인", cleaned["rawLines"])
        self.assertNotIn("체결강도", cleaned["rawLines"])
        self.assertEqual("stale-at-dispatch", facts["marketSignalCoverage"]["investor"]["status"])

    def test_stale_kis_rest_price_does_not_block_fresh_quote_and_moving_average_alert(self):
        now = datetime(2026, 7, 20, 0, 10, tzinfo=timezone.utc)
        decision = evaluate_notification_data_freshness(
            {
                "messageType": INVESTMENT_INSIGHT,
                "ontologyRelationContext": {
                    "evidenceState": {
                        "appliedFactFields": [
                            "profitLossRate",
                            "positionAccountWeight",
                            "ma20Distance",
                        ],
                    },
                },
                "dataFreshness": {
                    "sources": [
                        {
                            "source": "Toss /api/v1/prices + KIS WebSocket",
                            "status": "fresh",
                            "sourceAsOf": "2026-07-20T00:09:30Z",
                            "maxAgeMinutes": 10,
                        },
                        {
                            "source": "KIS ccnl",
                            "stage": "ccnl",
                            "status": "fresh",
                            "sourceAsOf": "2026-07-20T00:09:50Z",
                            "maxAgeMinutes": 2,
                            "transport": "websocket",
                            "fields": ["currentPrice", "changeRate", "volume"],
                        },
                        {
                            "source": "KIS price",
                            "stage": "price",
                            "status": "fresh",
                            "sourceAsOf": "2026-07-20T00:01:00Z",
                            "maxAgeMinutes": 3,
                            "transport": "rest",
                            "fields": ["currentPrice", "ma20Distance", "peRatio"],
                        },
                    ],
                },
            },
            settings={"dataFreshnessEnabled": "1"},
            now=now,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("fresh", decision.status)
        self.assertEqual([], decision.stale_sources)
        self.assertEqual(["KIS price"], decision.ignored_sources)

    def test_stale_kis_rest_price_remains_required_for_per_based_inference(self):
        decision = evaluate_notification_data_freshness(
            {
                "messageType": INVESTMENT_INSIGHT,
                "ontologyRelationContext": {
                    "evidenceState": {"appliedFactFields": ["peRatio"]},
                },
                "dataFreshness": {
                    "sources": [
                        {
                            "source": "Toss /api/v1/prices + KIS WebSocket",
                            "status": "fresh",
                            "sourceAsOf": "2026-07-20T00:09:30Z",
                            "maxAgeMinutes": 10,
                        },
                        {
                            "source": "KIS price",
                            "stage": "price",
                            "status": "fresh",
                            "sourceAsOf": "2026-07-20T00:01:00Z",
                            "maxAgeMinutes": 3,
                            "transport": "rest",
                            "fields": ["peRatio"],
                        },
                    ],
                },
            },
            settings={"dataFreshnessEnabled": "1"},
            now=datetime(2026, 7, 20, 0, 10, tzinfo=timezone.utc),
        )

        self.assertFalse(decision.should_send)
        self.assertEqual("stale", decision.status)
        self.assertEqual(["KIS price"], decision.stale_sources)

    def test_notification_runner_suppresses_job_that_expired_while_waiting(self):
        job = NotificationJob.create(
            "오래된 투자 알림",
            account_id="main",
            message_type=INVESTMENT_INSIGHT,
            context={
                "messageType": INVESTMENT_INSIGHT,
                "dataFreshness": {
                    "source": "KIS price",
                    "stage": "price",
                    "status": "fresh",
                    "sourceAsOf": "2026-07-20T00:00:00Z",
                    "maxAgeMinutes": 3,
                },
            },
        )

        class Queue:
            def pending(self, limit=10):
                return [job] if job.status == "pending" else []

            def mark_processing(self, target):
                target.status = "processing"

            def mark_suppressed(self, target, reason):
                target.status = "suppressed"
                target.last_error = reason

            def mark_failed(self, target, reason):
                target.status = "failed"
                target.last_error = reason

        sent = []
        runner = NotificationQueueRunner(
            Queue(),
            SimpleNamespace(load_all=lambda: []),
            lambda _account: SimpleNamespace(send=lambda message: sent.append(message)),
            settings={"dataFreshnessEnabled": "1"},
            now_provider=lambda: datetime(2026, 7, 20, 0, 4, tzinfo=timezone.utc),
        )

        self.assertEqual(1, runner.run_once())
        self.assertEqual([], sent)
        self.assertEqual("suppressed", job.status)
        self.assertIn("AI 판단 전", job.last_error)

    def test_operational_delivery_types_are_separate_from_investment_messages(self):
        self.assertTrue(is_operations_delivery_message_type(WORK_HANDOFF))
        self.assertTrue(is_operations_delivery_message_type("ontologyInferenceMissing"))
        self.assertTrue(is_operations_delivery_message_type("monitorConnection"))
        self.assertTrue(is_operations_delivery_message_type("externalDataConnection"))
        self.assertFalse(is_operations_delivery_message_type(INVESTMENT_INSIGHT))
        self.assertFalse(is_operations_delivery_message_type("newsDigest"))
        self.assertFalse(is_operations_delivery_message_type("modelReview"))

    def test_notification_runner_routes_operational_jobs_to_operations_notifier(self):
        account = AccountConfig(
            "main",
            "메인",
            "toss",
            "https://example.test",
            "",
            "",
            "",
            ["AAPL"],
            quiet_hours_enabled=False,
        )
        jobs = [
            NotificationJob.create("투자 알림", account_id="main", message_type=INVESTMENT_INSIGHT),
            NotificationJob.create("작업 완료", account_id="main", message_type=WORK_HANDOFF),
        ]

        class Queue:
            def pending(self, limit=10):
                return [job for job in jobs if job.status == "pending"][:limit]

            def mark_processing(self, job):
                job.status = "processing"

            def mark_done(self, job):
                job.status = "done"

            def mark_failed(self, job, reason):
                job.status = "failed"
                job.last_error = reason

        class Accounts:
            def load_all(self):
                return [account]

        account_messages = []
        operations_messages = []
        account_notifier = SimpleNamespace(send=lambda message: account_messages.append(message) or NotificationResult(True, "Account Telegram"))
        operations_notifier = SimpleNamespace(send=lambda message: operations_messages.append(message) or NotificationResult(True, "Operations Telegram"))
        runner = NotificationQueueRunner(
            Queue(),
            Accounts(),
            lambda _account: account_notifier,
            operations_notifier_factory=lambda _account: operations_notifier,
        )

        self.assertEqual(2, runner.run_once(limit=2))
        self.assertEqual(["투자 알림"], account_messages)
        self.assertEqual(["작업 완료"], operations_messages)
        self.assertEqual("account", jobs[0].context["deliveryAudience"])
        self.assertEqual("operations", jobs[1].context["deliveryAudience"])
        self.assertEqual("Account Telegram", jobs[0].context["deliveryProvider"])
        self.assertEqual("Operations Telegram", jobs[1].context["deliveryProvider"])

    def test_operations_telegram_credentials_are_masked_from_public_settings(self):
        payload = public_settings_payload({
            "operationsTelegramBotToken": "secret-token",
            "operationsTelegramChatId": "123456",
        })

        self.assertEqual("", payload["settings"]["operationsTelegramBotToken"])
        self.assertEqual("", payload["settings"]["operationsTelegramChatId"])
        self.assertTrue(payload["configured"]["operationsTelegramBotToken"])
        self.assertTrue(payload["configured"]["operationsTelegramChatId"])

    def test_operations_notifier_never_falls_back_to_account_bot(self):
        with patch(
            "digital_twin.infrastructure.notifications.runtime_settings",
            return_value={
                "operationsTelegramBotToken": "operations-token",
                "operationsTelegramChatId": "operations-chat",
                "telegramBotToken": "account-token",
                "telegramChatId": "account-chat",
            },
        ):
            notifier = notifier_for_operations()

        self.assertIsInstance(notifier, TelegramNotifier)
        self.assertEqual("Telegram Operations", notifier.label)
        self.assertEqual("operations-token", notifier.bot_token)
        self.assertEqual("operations-chat", notifier.chat_id)

    def test_topline_change_summary_is_separated_from_new_alert_badge(self):
        message = prepend_execution_start_badge(
            "<b>[주의] 🛡️ SK하이닉스: 분할축소 점검</b>",
            {"honeyStateReason": "의미 있는 추가 확대: 손익률 추가 악화 -8.9% -> -10.4%"},
        )

        self.assertTrue(message.startswith("<b>🔔 새 알림</b>\n<code>손익 구간: 손실 관리(-10.4%) · 이전 알림 대비 1.5%p 악화</code>"))
        self.assertEqual(1, message.count("🔔 새 알림"))

    def test_threshold_summary_keeps_full_detected_and_configured_values(self):
        detected = "비트코인 24시간 +1.2%, 7일 +5.0%로 최근 일주일 상승 흐름이 이어지고 있으며 실제 보유 종목의 가격 반응을 함께 확인해야 합니다"
        configured = "비트코인 7일 변동률이 +4% 이상 또는 -4% 이하"
        context = {
            "criterionLines": [
                "감지: " + detected,
                "설정: " + configured,
            ],
        }
        expected = "감지값 " + detected + "이 기준(" + configured + ")을 넘었습니다."

        self.assertEqual(expected, notification_reason_summary(context))
        self.assertEqual(expected, notification_topline_change_summary(context))
        self.assertNotIn("...", notification_topline_change_summary(context))

    def test_message_start_badge_adds_work_handoff_keyword(self):
        message = prepend_message_start_badge("작업 완료\n- 요약: 테스트", context={"messageType": "workHandoff"})

        self.assertTrue(message.startswith("🔔 새 알림 · 작업완료\n\n작업 완료"))

    def test_topline_change_summary_shows_profit_loss_improvement_delta(self):
        summary = notification_topline_change_summary({
            "profitLossRate": -8.9,
            "previousProfitLossRate": -10.4,
            "honeyStateReason": "의미 있는 추가 확대: 관계 강도 변화",
        })

        self.assertEqual("손익 구간: 손실 관리(-8.9%) · 이전 알림 대비 1.5%p 개선", summary)

    def test_topline_change_summary_uses_nested_profit_loss_delta(self):
        summary = notification_topline_change_summary({
            "ontologyInsight": {"facts": {"profitLossRateDeltaPct": -2.25}},
            "honeyStateReason": "의미 있는 추가 확대: 관계 강도 변화",
        })

        self.assertEqual("손익 구간: 이전 알림 대비 2.2%p 악화", summary)

    def test_previous_profit_loss_context_is_used_in_message_topline(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "SK하이닉스 손익 구간",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "000660",
                "rawLines": "수익률: -28.2%",
                "ontologyInsight": {"subject": "000660", "dispatchInsightType": "riskManagement"},
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = attach_previous_profit_loss_context(
            decision,
            job,
            {"rawLines": "수익률: -24.1%", "ontologyInsight": {"subject": "000660"}},
        )
        context = dict(job.context)
        context.update(decision.to_context())
        message = prepend_execution_start_badge("<b>[관찰] 🛡️ SK하이닉스: 매도 우선 점검</b>", context)

        self.assertIn("손익 구간: 큰 손실(-28.2%) · 이전 알림 대비 4.1%p 악화", message)

    def test_previous_profit_loss_topline_shows_no_change(self):
        summary = notification_topline_change_summary({
            "profitLossRate": -20.948,
            "previousProfitLossRate": -20.95,
            "honeyStateReason": "손실률 -20.948%가 필수 발송 구간(-15% 이하)에 있음",
        })

        self.assertEqual("손익 구간: 큰 손실(-20.9%) · 이전 알림 대비 0.0%p 변화 없음", summary)

    def test_topline_change_summary_maps_new_news_disclosure_reason(self):
        summary = notification_topline_change_summary({
            "honeyStateReason": "의미 있는 추가 확대: 새 근거 신호 추가 holdingTiming, externalDartDisclosure",
            "sourceSignalTypes": ["holdingTiming", "externalDartDisclosure"],
        })

        self.assertEqual("새 뉴스·공시", summary)

    def test_cooldown_release_summary_explains_material_change_before_cooldown(self):
        summary = notification_cooldown_release_summary({
            "honeyStateCooldownEnabled": True,
            "honeyStateDecision": "material_change",
            "honeyStateReason": "의미 있는 추가 확대: 손익률 추가 악화 -18.7% -> -20.1%",
            "honeyStateLastSentAgeMinutes": 42,
            "honeyStateCooldownMinutes": 360,
        })

        self.assertEqual(
            "마지막 발송 후 42분으로 기본 쿨다운 360분 전이지만, 의미 있는 추가 확대: 손익률 추가 악화 -18.7% → -20.1% 때문에 다시 보냈습니다.",
            summary,
        )

    def test_cooldown_release_summary_is_empty_for_suppressed_cooldown(self):
        summary = notification_cooldown_release_summary({
            "honeyStateCooldownEnabled": True,
            "honeyStateDecision": "cooldown",
            "honeyStateSuppressed": True,
            "honeyStateReason": "같은 임계값 상태 지속",
            "honeyStateLastSentAgeMinutes": 35,
            "honeyStateCooldownMinutes": 360,
        })

        self.assertEqual("", summary)

    def test_cooldown_release_summary_hides_internal_relation_signature(self):
        summary = notification_cooldown_release_summary({
            "honeyStateCooldownEnabled": True,
            "honeyStateDecision": "material_change",
            "honeyStateReason": "관계 경로 변경: 관계 의미 경로 변경 subject=aapl|dispatchtype=holdingPositionCommon|relationRuleIds=graph.averaging_down.risk_guard.v1",
            "honeyStateLastSentAgeMinutes": 5,
            "honeyStateCooldownMinutes": 360,
        })

        self.assertIn("핵심 판단 축 조합이 달라졌습니다", summary)
        self.assertNotIn("subject=", summary)
        self.assertNotIn("relationRuleIds=", summary)

    def test_watchlist_data_conflict_is_data_quality_signal(self):
        mixin = StrategyAlertMixin()
        signal_type = mixin.watchlist_ontology_signal_type({
            "decision": {"actionGroup": "review"},
            "activeRules": [{"ruleId": "data.conflict.v1"}],
        })

        self.assertEqual("dataQuality", signal_type)

    def test_data_quality_insight_does_not_bypass_cooldown_for_novelty_only(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "데이터 충돌 점검",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "body": "동화약품 데이터 충돌 점검",
                "symbol": "000020",
                "ontologyInsight": {
                    "subject": "000020",
                    "insightType": "dataQualityWarning",
                    "dispatchInsightType": "dataQualityWarning",
                    "score": 61.1,
                    "noveltyScore": 48,
                    "confidence": 75,
                    "sourceEventKeys": [
                        "main:watchlist-ontology:000020:dataQuality:data.conflict.v1",
                        "main:watchlist-quote:000020:0.0%",
                    ],
                },
                "sourceSignalTypes": ["watchlistOntologySignal", "watchlistQuote"],
            },
        )
        previous_context = {
            "severity": "WATCH",
            "ontologyInsight": {
                "subject": "000020",
                "insightType": "dataQualityWarning",
                "dispatchInsightType": "dataQualityWarning",
                "score": 61.1,
                "noveltyScore": 25,
                "confidence": 75,
                "sourceEventKeys": ["main:watchlist-ontology:000020:dataQuality:data.conflict.v1"],
            },
            "sourceSignalTypes": ["watchlistOntologySignal"],
        }
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_context=previous_context,
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=80,
            job=job,
        )

        self.assertFalse(decision.should_send)
        self.assertEqual("cooldown", decision.state_decision)
        self.assertFalse(decision.similarity_bypassed)

    def test_critical_loss_repeat_without_material_change_uses_state_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "SK하이닉스 손실 점검",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "body": "SK하이닉스 손절·분할축소 점검",
                "symbol": "000660",
                "rawLines": "현재가: 2,007,000원\n평균매입가: 2,571,000원\n수익률: -21.7%",
                "ontologyInsight": {
                    "subject": "000660",
                    "dispatchInsightType": "riskManagement",
                    "score": 86,
                    "noveltyScore": 20,
                    "confidence": 70,
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_context={
                "severity": "WATCH",
                "rawLines": "현재가: 2,014,000원\n평균매입가: 2,571,000원\n수익률: -21.8%",
                "ontologyInsight": {"subject": "000660", "dispatchInsightType": "riskManagement"},
                "sourceSignalTypes": ["holdingTiming"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=117,
            job=job,
        )

        self.assertFalse(decision.should_send)
        self.assertEqual("cooldown", decision.state_decision)
        self.assertFalse(decision.similarity_bypassed)
        self.assertIn("같은 임계값 상태 지속", decision.state_reason)

    def test_critical_loss_repeat_with_missing_previous_rate_uses_state_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "SK하이닉스 손실 점검",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "body": "SK하이닉스 손절·분할축소 점검",
                "symbol": "000660",
                "profitLossRate": -18.1,
                "ontologyInsight": {
                    "subject": "000660",
                    "dispatchInsightType": "riskManagement",
                    "score": 86,
                    "noveltyScore": 20,
                    "confidence": 70,
                    "sourceEventKeys": ["default:timing:000660:손실 축소 권장"],
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_context={
                "severity": "WATCH",
                "ontologyInsight": {
                    "subject": "000660",
                    "dispatchInsightType": "riskManagement",
                    "score": 86,
                    "noveltyScore": 20,
                    "sourceEventKeys": ["default:timing:000660:손실 축소 권장"],
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=36,
            job=job,
        )

        self.assertFalse(decision.should_send)
        self.assertEqual("cooldown", decision.state_decision)
        self.assertFalse(decision.similarity_bypassed)
        self.assertIn("같은 임계값 상태 지속", decision.state_reason)

    def test_holding_investment_state_group_key_ignores_minor_dispatch_wording(self):
        risk_job = NotificationJob.create(
            "삼성전자 리스크 증가",
            account_id="main",
            message_type="investmentInsight",
            context={
                "symbol": "005930",
                "ontologyInsight": {
                    "subject": "005930",
                    "dispatchInsightType": "riskIncrease",
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        management_job = NotificationJob.create(
            "삼성전자 리스크 관리",
            account_id="main",
            message_type="investmentInsight",
            context={
                "symbol": "005930",
                "ontologyInsight": {
                    "subject": "005930",
                    "dispatchInsightType": "riskManagement",
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
        )

        self.assertEqual(notification_state_group_key(risk_job), notification_state_group_key(management_job))
        self.assertIn("holdingpositioncommon", notification_state_group_key(risk_job).lower())

    def test_state_group_key_uses_ontology_timeline_and_conflict(self):
        base_context = {
            "symbol": "005930",
            "ontologyInsight": {
                "subject": "005930",
                "dispatchInsightType": "riskManagement",
            },
            "sourceSignalTypes": ["holdingTiming"],
            "ontologyRelationContext": {
                "decision": {"selectedRuleId": "graph.holding.trend_transition.risk.v1"},
                "inferenceTimeline": {
                    "currentStateKey": "TEMPORAL_RISK|graph.holding.trend_transition.risk.v1",
                },
                "signalConflicts": {
                    "conflictType": "risk-dominant-with-support",
                },
                "whyNow": {
                    "shouldEscalate": False,
                },
            },
        }
        same_state = NotificationJob.create(
            "삼성전자 리스크 관리",
            account_id="main",
            message_type="investmentInsight",
            context=base_context,
        )
        same_state_other_dispatch = NotificationJob.create(
            "삼성전자 리스크 증가",
            account_id="main",
            message_type="investmentInsight",
            context={
                **base_context,
                "ontologyInsight": {
                    "subject": "005930",
                    "dispatchInsightType": "riskIncrease",
                },
            },
        )
        changed_state = NotificationJob.create(
            "삼성전자 다른 추론",
            account_id="main",
            message_type="investmentInsight",
            context={
                **base_context,
                "ontologyRelationContext": {
                    **base_context["ontologyRelationContext"],
                    "inferenceTimeline": {
                        "currentStateKey": "TEMPORAL_RISK|graph.disclosure.event_risk.v1",
                    },
                },
            },
        )

        self.assertEqual(notification_state_group_key(same_state), notification_state_group_key(same_state_other_dispatch))
        self.assertNotEqual(notification_state_group_key(same_state), notification_state_group_key(changed_state))
        self.assertIn("timeline=temporal_risk", notification_state_group_key(same_state).lower())
        self.assertIn("conflict=risk-dominant-with-support", notification_state_group_key(same_state).lower())

    def test_critical_loss_new_band_entry_bypasses_state_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "SK하이닉스 손실 점검",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "body": "SK하이닉스 손절·분할축소 점검",
                "symbol": "000660",
                "rawLines": "현재가: 2,007,000원\n평균매입가: 2,571,000원\n수익률: -21.9%",
                "ontologyInsight": {
                    "subject": "000660",
                    "dispatchInsightType": "riskManagement",
                    "score": 86,
                    "noveltyScore": 20,
                    "confidence": 70,
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_context={
                "severity": "WATCH",
                "rawLines": "현재가: 2,122,000원\n평균매입가: 2,571,000원\n수익률: -17.4%",
                "ontologyInsight": {"subject": "000660", "dispatchInsightType": "riskManagement"},
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=117,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("required-profit-loss-change", decision.state_decision)
        self.assertTrue(decision.similarity_bypassed)
        self.assertIn("더 깊은 손실 구간", decision.state_reason)

    def test_critical_loss_additional_worsening_inside_same_band_bypasses_state_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "SK하이닉스 손실 점검",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "body": "SK하이닉스 손절·분할축소 점검",
                "symbol": "000660",
                "rawLines": "수익률: -23.0%",
                "ontologyInsight": {
                    "subject": "000660",
                    "dispatchInsightType": "riskManagement",
                    "score": 86,
                    "noveltyScore": 20,
                    "confidence": 70,
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_context={
                "rawLines": "수익률: -21.7%",
                "ontologyInsight": {"subject": "000660", "dispatchInsightType": "riskManagement"},
                "sourceSignalTypes": ["holdingTiming"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=117,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("meaningful-change", decision.state_decision)
        self.assertTrue(decision.similarity_bypassed)
        self.assertIn("손익률 추가 악화", decision.state_reason)
        self.assertIn("-21.7% -> -23%", decision.state_reason)

    def test_mandatory_profit_band_bypasses_similarity_penalty(self):
        rule = default_notification_rule("investmentInsight")
        rule.similarity_penalty = -100
        job = NotificationJob.create(
            "수익 보호 점검",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "body": "수익 보호 점검",
                "symbol": "MSTR",
                "profitLossRate": 24.5,
                "ontologyInsight": {
                    "subject": "MSTR",
                    "dispatchInsightType": "riskManagement",
                    "score": 70,
                    "noveltyScore": 20,
                    "confidence": 70,
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_similarity_rule(
            decision,
            rule,
            recent_count=1,
            previous_context={"profitLossRate": 19.0},
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertTrue(decision.similarity_bypassed)
        self.assertIn("+24.5%", decision.similarity_bypass_reason)

    def test_profit_loss_worsening_bypasses_investment_insight_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "손익률 추가 악화",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "000660",
                "rawLines": "수익률: -10.4%\n추세: 60일선 2,000,000원보다 3.0% 높음",
                "ontologyInsight": {"subject": "000660", "dispatchInsightType": "riskManagement", "score": 70, "noveltyScore": 20},
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_context={
                "rawLines": "수익률: -8.9%\n추세: 60일선 2,000,000원보다 3.2% 높음",
                "ontologyInsight": {"subject": "000660", "dispatchInsightType": "riskManagement"},
                "sourceSignalTypes": ["holdingTiming"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=80,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("meaningful-change", decision.state_decision)
        self.assertIn("손익률 추가 악화", decision.state_reason)

    def test_profit_loss_improvement_bypasses_investment_insight_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "손익률 개선",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "000660",
                "rawLines": "수익률: -18.0%\n추세: 60일선 2,015,483원보다 4.6% 낮음",
                "ontologyInsight": {"subject": "000660", "dispatchInsightType": "riskManagement", "score": 94, "noveltyScore": 20},
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_context={
                "rawLines": "수익률: -19.2%\n추세: 60일선 2,011,467원보다 16.4% 낮음",
                "ontologyInsight": {"subject": "000660", "dispatchInsightType": "riskManagement"},
                "sourceSignalTypes": ["holdingTiming"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=164,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("meaningful-change", decision.state_decision)
        self.assertTrue(decision.similarity_bypassed)
        self.assertIn("손익률 개선", decision.state_reason)
        self.assertIn("-19.2% -> -18%", decision.state_reason)

    def test_synthetic_timing_source_event_does_not_bypass_investment_insight_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "관계 규칙 관찰",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "MSTR",
                "profitLossRate": 5.0,
                "ontologyInsight": {
                    "subject": "MSTR",
                    "dispatchInsightType": "holdingPositionCommon",
                    "score": 78,
                    "noveltyScore": 20,
                    "sourceEventKeys": ["default:timing:MSTR:관계 규칙 관찰"],
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_context={
                "profitLossRate": 5.0,
                "ontologyInsight": {
                    "subject": "MSTR",
                    "dispatchInsightType": "holdingPositionCommon",
                    "score": 78,
                    "noveltyScore": 20,
                    "sourceEventKeys": ["default:timing:MSTR:분할축소 우선 점검"],
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=45,
            job=job,
        )

        self.assertFalse(decision.should_send)
        self.assertEqual("cooldown", decision.state_decision)
        self.assertFalse(decision.similarity_bypassed)

    def test_split_relation_rule_keys_do_not_count_as_new_source_event(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "관계 규칙 관찰",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "AAPL",
                "profitLossRate": 2.0,
                "ontologyInsight": {
                    "subject": "AAPL",
                    "dispatchInsightType": "relationshipChange",
                    "score": 78,
                    "noveltyScore": 20,
                    "sourceEventKeys": [
                        "graph.news.direct_material_context.v1+graph.news.direct_material_context.v1+graph.holding.position_context.v1"
                    ],
                },
                "sourceSignalTypes": ["watchlistOntologySignal"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_context={
                "profitLossRate": 2.0,
                "ontologyInsight": {
                    "subject": "AAPL",
                    "dispatchInsightType": "relationshipChange",
                    "score": 78,
                    "noveltyScore": 20,
                    "sourceEventKeys": ["graph.news.direct_material_context.v1"],
                },
                "sourceSignalTypes": ["watchlistOntologySignal"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=45,
            job=job,
        )

        self.assertFalse(decision.should_send)
        self.assertEqual("cooldown", decision.state_decision)
        self.assertFalse(decision.similarity_bypassed)

    def test_material_news_source_event_bypasses_investment_insight_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "새 뉴스 원천 근거",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "MSTR",
                "profitLossRate": 4.0,
                "ontologyInsight": {
                    "subject": "MSTR",
                    "dispatchInsightType": "holdingPositionCommon",
                    "score": 78,
                    "noveltyScore": 20,
                    "sourceEventKeys": ["main:news:MSTR:yahoo:202607140001"],
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_context={
                "profitLossRate": 4.0,
                "ontologyInsight": {
                    "subject": "MSTR",
                    "dispatchInsightType": "holdingPositionCommon",
                    "score": 78,
                    "noveltyScore": 20,
                    "sourceEventKeys": ["main:holding:MSTR:risk"],
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=45,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("meaningful-change", decision.state_decision)
        self.assertTrue(decision.similarity_bypassed)
        self.assertIn("새 뉴스·공시 추가", decision.state_reason)

    def test_ma60_cross_down_bypasses_investment_insight_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "60일선 이탈",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "005930",
                "rawLines": "수익률: -4.0%\n추세: 60일선 289,838원보다 1.2% 낮음",
                "ontologyInsight": {"subject": "005930", "dispatchInsightType": "riskManagement", "score": 70, "noveltyScore": 20},
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_context={
                "rawLines": "수익률: -3.8%\n추세: 60일선 289,838원보다 0.4% 높음",
                "ontologyInsight": {"subject": "005930", "dispatchInsightType": "riskManagement"},
                "sourceSignalTypes": ["holdingTiming"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=80,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("meaningful-change", decision.state_decision)
        self.assertIn("60일 평균 아래로 전환", decision.state_reason)

    def test_action_change_bypasses_investment_insight_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "판단 액션 변경",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "MSTR",
                "profitLossRate": 6.0,
                "notificationAiValidatedResponse": {"actionLabel": "분할축소"},
                "ontologyInsight": {"subject": "MSTR", "dispatchInsightType": "riskManagement", "score": 70, "noveltyScore": 20},
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_context={
                "profitLossRate": 6.2,
                "notificationAiValidatedResponse": {"actionLabel": "보유"},
                "ontologyInsight": {"subject": "MSTR", "dispatchInsightType": "riskManagement"},
                "sourceSignalTypes": ["holdingTiming"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=80,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("meaningful-change", decision.state_decision)
        self.assertIn("권장 대응 변경", decision.state_reason)

    def test_new_news_or_disclosure_event_bypasses_investment_insight_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "새 공시 근거",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "005930",
                "profitLossRate": -4.0,
                "ontologyInsight": {
                    "subject": "005930",
                    "dispatchInsightType": "riskManagement",
                    "score": 70,
                    "noveltyScore": 20,
                    "sourceEventKeys": ["main:disclosure:005930:202607130001"],
                },
                "sourceSignalTypes": ["holdingTiming", "externalDartDisclosure"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_context={
                "profitLossRate": -4.0,
                "ontologyInsight": {
                    "subject": "005930",
                    "dispatchInsightType": "riskManagement",
                    "sourceEventKeys": ["main:holding:005930:risk"],
                },
                "sourceSignalTypes": ["holdingTiming"],
            },
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=80,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("meaningful-change", decision.state_decision)
        self.assertIn("새 근거 종류 추가", decision.state_reason)


if __name__ == "__main__":
    unittest.main()
