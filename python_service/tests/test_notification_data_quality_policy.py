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
from digital_twin.domain.notification_rule_evaluator import (
    similarity_bypass_match,
    typedb_profit_loss_delivery_reason,
)
from digital_twin.domain.notification_rule_models import SimilarityBypassCondition
from digital_twin.domain.data_freshness import (
    evaluate_notification_data_freshness,
    freshness_from_position,
    sanitize_notification_context_for_freshness,
)
from digital_twin.application.notification_ai_gate_message import (
    notification_cooldown_release_summary,
    notification_reason_summary,
    notification_topline_change_summary,
    prepend_execution_start_badge,
)
from digital_twin.domain.notification_templates import prepend_message_start_badge
from digital_twin.domain.notification_ai import opinion_lines_for_type
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.message_types import (
    INVESTMENT_INSIGHT,
    MARKET_OBSERVATION,
    NEWS_DIGEST,
    ONTOLOGY_OBSERVATION_FOLLOWUP,
    WORK_HANDOFF,
    is_operations_delivery_message_type,
)
from digital_twin.domain.notifications import NotificationJob
from digital_twin.domain.strategy_alerts import StrategyAlertMixin
from digital_twin.domain.portfolio import utc_now_iso
from digital_twin.application.notification_service import NotificationQueueRunner
from digital_twin.application.notification.dispatch import NotificationDispatchService
from digital_twin.application.notification.eligibility import NotificationDispatchEligibilityService
from digital_twin.infrastructure.cli import public_settings_payload
from digital_twin.infrastructure.notifications import NotificationResult, TelegramNotifier, notifier_for_operations
from digital_twin.infrastructure.mysql_notification_config import (
    MySQLNotificationRuleStore,
    notification_rule_defaults_fingerprint,
)


class NotificationDataQualityPolicyTests(unittest.TestCase):
    def assert_dispatch_persists_verified_transport_receipt(self):
        completed = {}

        class Queue:
            def start_delivery_attempt(self, *_args, **_kwargs):
                return "attempt-1"

            def complete_delivery_attempt(
                self,
                _job,
                _attempt_id,
                delivered,
                provider="",
                reason="",
                metadata=None,
            ):
                completed.update({
                    "delivered": delivered,
                    "provider": provider,
                    "reason": reason,
                    "metadata": dict(metadata or {}),
                })

        service = NotificationDispatchService(
            Queue(),
            notifier_factory=lambda _account: SimpleNamespace(
                send=lambda _message: NotificationResult(
                    True,
                    "Telegram",
                    metadata={
                        "receiptVerified": True,
                        "destinationVerified": True,
                        "messageIds": ["321"],
                    },
                )
            ),
        )
        job = NotificationJob.create(
            "실제 알림",
            account_id="main",
            message_type="newsDigest",
        )

        service.deliver(job, {"main": object()}, job.text)

        self.assertTrue(completed["delivered"])
        self.assertEqual("Telegram", completed["provider"])
        self.assertTrue(completed["metadata"]["receiptVerified"])
        self.assertEqual(["321"], completed["metadata"]["messageIds"])
        self.assertEqual(len("실제 알림".encode("utf-8")), completed["metadata"]["messageBytes"])

    def test_typedb_profit_loss_conditions_report_both_improvement_and_worsening(self):
        def job(action_group, change_state):
            return NotificationJob.create(
                "손익 조건 변화",
                account_id="main",
                message_type="investmentInsight",
                context={
                    "symbol": "MSTR",
                    "ontologyInsight": {"changeState": change_state},
                    "ontologyRelationContext": {
                        "source": "typedbInferenceBox",
                        "graphStoreUsed": True,
                        "fallbackUsed": False,
                        "decision": {
                            "basis": "typedbInferenceBox",
                            "actionGroup": action_group,
                        },
                        "decisionState": {
                            "reviewLevel": "check",
                            "dataState": "sufficient",
                        },
                    },
                },
            )

        self.assertIn("개선", typedb_profit_loss_delivery_reason(job("lossControl", "improving")))
        self.assertIn("악화", typedb_profit_loss_delivery_reason(job("lossControl", "worsening")))
        self.assertIn("개선", typedb_profit_loss_delivery_reason(job("profitTake", "improving")))
        self.assertIn("악화", typedb_profit_loss_delivery_reason(job("profitTake", "worsening")))

    def test_actionable_transition_does_not_wait_for_initial_baseline_confirmation(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create(
            "NVIDIA 회피 검토",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "ALERT",
                "symbol": "NVDA",
                "ontologyInsight": {"subject": "NVDA", "dispatchInsightType": "watchlist"},
                "ontologyRelationDiff": {
                    "material": True,
                    "changeClass": "material",
                    "decisionTransition": {"kind": "action-changed", "material": True, "currentAction": "avoid"},
                },
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=0,
            previous_context={
                "_relationBaselineObservedAt": "2026-08-11T00:00:00Z",
                "_relationBaselineAgeMinutes": 5,
            },
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("new-condition", decision.state_decision)

        holding_job = NotificationJob.create(
            "SK하이닉스 보유 점검",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "000660",
                "body": "SK하이닉스 보유 판단",
                "ontologyRelationDiff": {
                    "material": False,
                    "decisionTransition": {
                        "kind": "unchanged",
                        "material": False,
                        "currentAction": "hold",
                    },
                },
                "ontologyRelationContext": {
                    "source": "typedbInferenceBox",
                    "graphStoreUsed": True,
                    "fallbackUsed": False,
                    "targetRole": "holding",
                    "decisionState": {"reviewLevel": "check", "dataState": "sufficient"},
                    "actionEnvelope": {"targetRole": "holding", "preferredAction": "HOLD"},
                },
            },
        )
        holding_decision = apply_state_cooldown_rule(
            evaluate_notification_rule(holding_job, rule),
            rule,
            sent_count=0,
            previous_context={"deliverySuppressionReason": "initial_graph_baseline"},
            job=holding_job,
        )
        self.assertTrue(holding_decision.should_send)
        self.assertEqual("new-condition", holding_decision.state_decision)
        self.assertIn("첫 TypeDB 판단", holding_decision.state_reason)
        holding_job.context.update(holding_decision.to_context())
        self.assertTrue(
            NotificationDispatchEligibilityService(queue=None).apply_inference_change_gate(
                holding_job
            )
        )
        self.assertEqual("send", holding_job.context["inferenceChangeGate"]["decision"])

        profit_loss_job = NotificationJob.create(
            "SK하이닉스 손익 조건 변화",
            account_id="main",
            message_type="investmentInsight",
            context={
                **holding_job.context,
                "cooldownDecision": "typedb-profit-loss-change",
                "cooldownReason": "TypeDB 손익 관리 조건이 새로 성립",
                "ontologyRelationDiff": {
                    "material": False,
                    "decisionTransition": {"kind": "unchanged", "material": False},
                },
            },
        )
        self.assertTrue(
            NotificationDispatchEligibilityService(queue=None).apply_inference_change_gate(
                profit_loss_job
            )
        )
        self.assertEqual(
            "typedb-profit-loss-change",
            profit_loss_job.context["inferenceChangeGate"]["deliveryAuthorization"],
        )

        watchlist_job = NotificationJob.create(
            "카카오 관심 점검",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "035720",
                "body": "카카오 관심 판단",
                "ontologyRelationDiff": {
                    "material": False,
                    "decisionTransition": {
                        "kind": "initial",
                        "material": False,
                        "currentAction": "hold",
                    },
                },
                "ontologyRelationContext": {
                    "source": "typedbInferenceBox",
                    "graphStoreUsed": True,
                    "fallbackUsed": False,
                    "targetRole": "watchlist",
                    "decisionState": {"reviewLevel": "check", "dataState": "sufficient"},
                    "actionEnvelope": {"targetRole": "watchlist", "preferredAction": "HOLD"},
                },
            },
        )
        watchlist_decision = apply_state_cooldown_rule(
            evaluate_notification_rule(watchlist_job, rule),
            rule,
            sent_count=0,
            previous_context={},
            job=watchlist_job,
        )
        self.assertFalse(watchlist_decision.should_send)
        self.assertEqual("baseline", watchlist_decision.state_decision)
        self.assertEqual("initial_graph_baseline", watchlist_decision.suppression_reason)
        self.assert_dispatch_persists_verified_transport_receipt()

    def test_investment_insight_uses_semantic_state_cooldown_without_text_similarity(self):
        default_rule = default_notification_rule("investmentInsight")
        self.assertFalse(default_rule.similarity_enabled)
        self.assertTrue(default_rule.state_cooldown_enabled)

        legacy = default_notification_rule("investmentInsight")
        legacy.similarity_enabled = True
        legacy.similarity_window_minutes = 360
        changed = MySQLNotificationRuleStore._migrate_legacy_investment_insight_similarity(
            legacy,
            default_rule,
        )

        self.assertTrue(changed)
        self.assertFalse(legacy.similarity_enabled)
        self.assertTrue(legacy.state_cooldown_enabled)

    def test_reference_only_typedb_observation_is_not_blocked_as_an_action_judgement(self):
        rule_id = "graph.news.direct_material_context.v1"
        context = self._typedb_relation_context({
            "messageType": INVESTMENT_INSIGHT,
            "ontologyInsight": {
                "subject": "035720",
                "reviewLevel": "blocked",
                "dataState": "sufficient",
                "changeState": "new-condition",
                "conflictState": "mixed",
                "validationState": "blocked",
            },
            "ontologyRelationContext": {
                "source": "typedbInferenceBox",
                "graphStore": "typedb",
                "graphStoreUsed": True,
                "fallbackUsed": False,
                "sourceAboxSnapshotId": "abox:kakao:1",
                "inferenceGenerationId": "generation:kakao:1",
                "subject": {"symbol": "035720", "market": "KR"},
                "facts": {"symbol": "035720", "market": "KR"},
                "decisionState": {
                    "reviewLevel": "blocked",
                    "dataState": "sufficient",
                    "changeState": "new-condition",
                    "conflictState": "mixed",
                    "validationState": "blocked",
                },
                "decision": {
                    "basis": "typedbInferenceBox",
                    "selectedRuleId": rule_id,
                },
                "activeRules": [{
                    "ruleId": rule_id,
                    "label": "직접 중요 맥락 뉴스 확인",
                    "knowledgeBasis": {
                        "ruleKind": "context-observation",
                        "decisionEligibility": "reference-only",
                        "requiresHypothesis": False,
                    },
                }],
                "graphStoreInference": {
                    "graphStore": "typedb",
                    "sourceAboxSnapshotId": "abox:kakao:1",
                    "inferenceGenerationId": "generation:kakao:1",
                    "relations": [{"ruleId": rule_id}],
                    "traces": [{"id": "trace:kakao:1", "ruleId": rule_id}],
                },
            },
        })
        job = NotificationJob.create(
            "카카오 참고용 관계 변화",
            account_id="main",
            message_type=INVESTMENT_INSIGHT,
            context=context,
        )

        decision = evaluate_notification_rule(job, default_notification_rule(INVESTMENT_INSIGHT))

        self.assertTrue(decision.should_send)
        self.assertEqual("conditional", decision.gate_state)
        self.assertEqual("", decision.suppression_reason)
        self.assertIn("참고용 관계 변화", decision.gate_reason)

        rule_id = "graph.notification.profit_policy_threshold.v1"
        context = self._typedb_relation_context({
            "severity": "WATCH",
            "symbol": "MSTR",
            "ontologyRelationDiff": {
                "material": False,
                "decisionTransition": {
                    "kind": "initial",
                    "material": False,
                    "currentAction": "hold",
                },
            },
            "ontologyRelationContext": {
                "source": "typedbInferenceBox",
                "graphStore": "typedb",
                "graphStoreUsed": True,
                "fallbackUsed": False,
                "targetRole": "holding",
                "subject": {"symbol": "MSTR", "market": "US"},
                "facts": {"symbol": "MSTR", "market": "US", "profitLossRate": 36.6},
                "decisionState": {"reviewLevel": "check", "dataState": "sufficient"},
                "actionEnvelope": {"targetRole": "holding", "preferredAction": "HOLD"},
                "decision": {
                    "basis": "typedbInferenceBox",
                    "actionGroup": "profitTake",
                    "selectedRuleId": rule_id,
                },
                "activeRules": [{
                    "ruleId": rule_id,
                    "label": "수익 정책 기준 관찰",
                    "knowledgeBasis": {
                        "ruleKind": "context-observation",
                        "decisionEligibility": "reference-only",
                        "requiresHypothesis": False,
                    },
                }],
            },
        })
        job = NotificationJob.create(
            "MSTR 자료 변화 관찰",
            account_id="main",
            message_type=INVESTMENT_INSIGHT,
            context=context,
        )
        rule = default_notification_rule(INVESTMENT_INSIGHT)

        decision = apply_state_cooldown_rule(
            evaluate_notification_rule(job, rule),
            rule,
            sent_count=0,
            previous_context={},
            job=job,
        )

        self.assertFalse(decision.should_send)
        self.assertEqual("baseline", decision.state_decision)
        self.assertEqual("initial_graph_baseline", decision.suppression_reason)

    def test_blocked_action_judgement_remains_suppressed(self):
        context = self._typedb_relation_context({
            "messageType": INVESTMENT_INSIGHT,
            "ontologyInsight": {
                "subject": "035720",
                "reviewLevel": "blocked",
                "dataState": "sufficient",
                "changeState": "new-condition",
                "conflictState": "mixed",
                "validationState": "blocked",
            },
            "ontologyRelationContext": {
                "source": "typedbInferenceBox",
                "graphStore": "typedb",
                "graphStoreUsed": True,
                "fallbackUsed": False,
                "decisionState": {
                    "reviewLevel": "blocked",
                    "dataState": "sufficient",
                    "changeState": "new-condition",
                    "conflictState": "mixed",
                    "validationState": "blocked",
                },
                "decision": {
                    "basis": "typedbInferenceBox",
                    "selectedRuleId": "graph.watchlist.entry.confirmed.v1",
                },
                "activeRules": [{
                    "ruleId": "graph.watchlist.entry.confirmed.v1",
                    "knowledgeBasis": {
                        "ruleKind": "decision",
                        "decisionEligibility": "action-candidate",
                        "requiresHypothesis": True,
                    },
                }],
            },
        })
        job = NotificationJob.create(
            "카카오 행동 판단 보류",
            account_id="main",
            message_type=INVESTMENT_INSIGHT,
            context=context,
        )

        decision = evaluate_notification_rule(job, default_notification_rule(INVESTMENT_INSIGHT))

        self.assertFalse(decision.should_send)
        self.assertEqual("validation_blocked", decision.suppression_reason)

    def test_no_eligible_thesis_can_reach_ai_as_non_action_interpretation(self):
        context = self._typedb_relation_context({
            "messageType": INVESTMENT_INSIGHT,
            "ontologyInsight": {
                "subject": "TSLA",
                "reviewLevel": "blocked",
                "dataState": "partial",
                "changeState": "new-condition",
                "validationState": "blocked",
            },
            "ontologyRelationContext": {
                "source": "typedbInferenceBox",
                "graphStore": "typedb",
                "graphStoreUsed": True,
                "fallbackUsed": False,
                "decision": {
                    "basis": "typedbInferenceBox",
                    "hypothesisState": "NO_ELIGIBLE_THESIS",
                    "aiInterpretationEligible": True,
                    "judgementBlocked": False,
                },
                "actionEnvelope": {
                    "status": "NO_ELIGIBLE_THESIS",
                    "judgementBlocked": False,
                },
            },
        })
        job = NotificationJob.create(
            "Tesla 가설 미성립 상태 해석",
            account_id="main",
            message_type=INVESTMENT_INSIGHT,
            context=context,
        )

        decision = evaluate_notification_rule(job, default_notification_rule(INVESTMENT_INSIGHT))

        self.assertTrue(decision.should_send)
        self.assertEqual("conditional", decision.gate_state)
        self.assertIn("행동을 만들지 않고", decision.gate_reason)

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

    def test_blocked_review_level_never_bypasses_cooldown_as_an_upgrade(self):
        condition = SimilarityBypassCondition(
            "review_level_upgrade",
            "확인 단계 상승",
            "review_level_upgrade",
            field="ontologyInsight.reviewLevel",
        )
        job = SimpleNamespace(context={"ontologyInsight": {"reviewLevel": "blocked"}})

        matched, reason = similarity_bypass_match(
            condition,
            job,
            {"ontologyInsight": {"reviewLevel": "immediate"}},
            SimpleNamespace(),
        )

        self.assertFalse(matched)
        self.assertEqual("", reason)

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

    def test_fresh_fetch_does_not_rescue_an_old_open_market_quote(self):
        now = datetime(2026, 8, 24, 6, 24, tzinfo=timezone.utc)
        freshness = freshness_from_position(
            {
                "symbol": "035720",
                "quoteSource": "Toss /api/v1/prices + KIS WebSocket",
                "sourceAsOf": "2026-08-24T06:00:00Z",
                "sourceFetchedAt": "2026-08-24T06:23:30Z",
                "freshnessStatus": "realtime",
                "latencyStatus": "provider-observation",
                "marketSession": "open",
                "dataQuality": "actual",
            },
            INVESTMENT_INSIGHT,
            settings={"dataFreshnessQuoteMaxAgeMinutes": "10"},
            now=now,
        )
        decision = evaluate_notification_data_freshness(
            {"messageType": INVESTMENT_INSIGHT, "dataFreshness": freshness},
            settings={"dataFreshnessEnabled": "1", "dataFreshnessQuoteMaxAgeMinutes": "10"},
            now=now,
        )

        self.assertFalse(decision.should_send)
        self.assertEqual("stale", decision.status)

    def test_notification_runner_allows_stale_investment_insight_and_requests_refresh(self):
        job = NotificationJob.create(
            "오래된 투자 알림",
            account_id="main",
            message_type=INVESTMENT_INSIGHT,
            context={
                "messageType": INVESTMENT_INSIGHT,
                "rawSymbol": "005930",
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

            def mark_done(self, target):
                target.status = "done"
                target.last_error = ""

        rechecks = []
        runner = NotificationQueueRunner(
            Queue(),
            SimpleNamespace(load_all=lambda: []),
            lambda _account: None,
            settings={"dataFreshnessEnabled": "1"},
            now_provider=lambda: datetime(2026, 7, 20, 0, 4, tzinfo=timezone.utc),
            fresh_data_recheck_requester=lambda account_id, symbol, job_id: rechecks.append(
                (account_id, symbol, job_id)
            ) or {"requested": True, "scheduledAt": "2026-07-20T00:04:00Z"},
        )

        self.assertTrue(runner.apply_dispatch_freshness_gate(job, "발송 직전"))
        self.assertEqual("pending", job.status)
        self.assertEqual([("main", "005930", job.job_id)], rechecks)
        self.assertEqual("advisory", job.context["dataFreshnessDecision"])
        self.assertTrue(job.context["notificationFreshnessAdvisory"]["blockingDisabled"])
        self.assertNotIn("deliverySuppressionReason", job.context)
        self.assertEqual("stale", job.context["dataFreshnessStatus"])

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

    def test_raw_profit_loss_value_cannot_bypass_cooldown_without_typedb_action_group(self):
        rule = default_notification_rule("investmentInsight")
        rule.similarity_bypass_conditions = []
        job = NotificationJob.create(
            "손실 구간 점검",
            account_id="main",
            message_type="investmentInsight",
            context={
                "severity": "WATCH",
                "symbol": "000660",
                "profitLossRate": -25.0,
                "ontologyInsight": {"subject": "000660", "dispatchInsightType": "riskManagement"},
                "sourceSignalTypes": ["holdingTiming"],
            },
        )
        decision = evaluate_notification_rule(job, rule)

        decision = apply_state_cooldown_rule(
            decision,
            rule,
            sent_count=1,
            previous_context={"profitLossRate": -20.0},
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=5,
            job=job,
        )

        self.assertFalse(decision.should_send)
        self.assertEqual("cooldown", decision.state_decision)
        self.assertFalse(decision.similarity_bypassed)

    def test_action_change_bypasses_investment_insight_cooldown(self):
        self._assert_profit_loss_change_is_checked_before_unchanged_graph_suppression()
        self._assert_unchanged_graph_emits_scheduled_summary_after_summary_cooldown()
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

    def _assert_profit_loss_change_is_checked_before_unchanged_graph_suppression(self):
        rule = default_notification_rule("investmentInsight")
        context = {
            "severity": "WATCH",
            "symbol": "000660",
            "profitLossRate": -11.8,
            "ontologyInsight": {"subject": "000660", "dispatchInsightType": "riskManagement"},
            "sourceSignalTypes": ["holdingTiming"],
            "ontologyRelationDiff": {
                "material": False,
                "decisionTransition": {"kind": "unchanged", "material": False},
            },
            "ontologyRelationContext": {
                "source": "typedbInferenceBox",
                "graphStore": "typedb",
                "graphStoreUsed": True,
                "fallbackUsed": False,
                "sourceAboxSnapshotId": "abox:hynix:2",
                "inferenceGenerationId": "generation:hynix:2",
                "generationAligned": True,
                "decision": {"basis": "typedbInferenceBox", "actionGroup": "lossControl"},
                "decisionState": {"reviewLevel": "act", "dataState": "sufficient"},
                "actionEnvelope": {"preferredAction": "TRIM"},
            },
        }
        job = NotificationJob.create(
            "손실 변화 점검",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )
        decision = apply_state_cooldown_rule(
            evaluate_notification_rule(job, rule),
            rule,
            sent_count=1,
            previous_context={**context, "profitLossRate": -10.82},
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=11,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("immediate", decision.delivery_cadence_tier)
        self.assertEqual(10, decision.delivery_cadence_minutes)
        self.assertNotEqual("unchanged-inference", decision.state_decision)

        pending_decision = apply_state_cooldown_rule(
            evaluate_notification_rule(job, rule),
            rule,
            sent_count=1,
            previous_context={**context, "profitLossRate": -10.82},
            last_sent_at="",
            last_sent_age_minutes=0,
            job=job,
        )
        self.assertFalse(pending_decision.should_send)
        self.assertEqual("in-flight", pending_decision.state_decision)
        self.assertEqual("in_flight_duplicate", pending_decision.suppression_reason)

    def _assert_unchanged_graph_emits_scheduled_summary_after_summary_cooldown(self):
        rule = default_notification_rule("investmentInsight")
        context = {
            "severity": "WATCH",
            "symbol": "MSTR",
            "profitLossRate": 2.0,
            "ontologyInsight": {"subject": "MSTR", "dispatchInsightType": "holdingPositionCommon"},
            "sourceSignalTypes": ["holdingTiming"],
            "ontologyRelationDiff": {
                "material": False,
                "decisionTransition": {"kind": "unchanged", "material": False},
            },
            "ontologyRelationContext": {
                "source": "typedbInferenceBox",
                "graphStore": "typedb",
                "graphStoreUsed": True,
                "fallbackUsed": False,
                "sourceAboxSnapshotId": "abox:mstr:2",
                "inferenceGenerationId": "generation:mstr:2",
                "generationAligned": True,
                "decision": {"basis": "typedbInferenceBox", "actionGroup": "positionReview"},
                "decisionState": {"reviewLevel": "check", "dataState": "sufficient"},
                "actionEnvelope": {"preferredAction": "HOLD", "targetRole": "holding"},
            },
        }
        job = NotificationJob.create(
            "보유 상태 재확인",
            account_id="main",
            message_type="investmentInsight",
            context=context,
        )
        decision = apply_state_cooldown_rule(
            evaluate_notification_rule(job, rule),
            rule,
            sent_count=1,
            previous_context=dict(context),
            last_sent_at=utc_now_iso(),
            last_sent_age_minutes=361,
            job=job,
        )

        self.assertTrue(decision.should_send)
        self.assertEqual("scheduled-summary", decision.state_decision)
        self.assertEqual("summary", decision.delivery_cadence_tier)
        self.assertEqual(360, decision.delivery_cadence_minutes)

if __name__ == "__main__":
    unittest.main()
