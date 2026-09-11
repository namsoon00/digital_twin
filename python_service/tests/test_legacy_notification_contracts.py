"""Focused notification contracts; no database, TypeDB server, or transport I/O."""

import unittest
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from digital_twin.infrastructure.web import events as web_events
from digital_twin.infrastructure.web.adapters import notification_configuration, notification_testing
from digital_twin.modules.accounts.contracts import AccountConfig
from digital_twin.modules.market_data.domain.events import monitoring_cycle_completed_event
from digital_twin.modules.market_data.domain.market_data import normalize_position
from digital_twin.modules.market_data.domain.monitoring import RealtimeMonitor
from digital_twin.modules.notifications.domain.notification_rule_models import (
    NotificationRuleDecision,
    SimilarityBypassCondition,
)
from digital_twin.modules.notifications.domain.notification_rules import (
    apply_similarity_rule,
    default_notification_rule,
    evaluate_notification_rule,
)
from digital_twin.modules.notifications.domain.notification_templates import (
    NotificationTemplate,
    render_notification,
)
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.portfolio.domain.portfolio import AccountSnapshot
from digital_twin.modules.portfolio.domain.portfolio_calculations import portfolio_summary
from digital_twin.shared_kernel.events import DomainEvent


def live_snapshot():
    position = normalize_position({
        "symbol": "005930", "name": "Fixture holding", "market": "KR",
        "currency": "KRW", "quantity": 10, "sellableQuantity": 10,
        "averagePrice": 80000, "currentPrice": 72000,
        "marketValue": 720000, "profitLossRate": -10,
    })
    return AccountSnapshot(
        "fixture", "Fixture account", "toss", "live", "Connected",
        "2026-09-12T01:00:00Z", portfolio_summary([position], 1000000, "KRW"),
        [position], [],
    )


def unqualified_native_projection():
    # Deliberately lacks governed eligibility and decision contracts. A native
    # match is audit evidence, not permission to manufacture an investment event.
    trace_id = "inference-trace:fixture:loss"
    return {
        "saved": True, "status": "ok", "graphStore": "typedb",
        "inferenceBox": {
            "status": "ok", "source": "typedbInferenceBox", "graphStore": "typedb",
            "nativeTypeDbReasoningUsed": True,
            "relations": [{
                "type": "HAS_INFERRED_RISK", "source": "stock:005930",
                "target": "risk:fixture:loss", "targetLabel": "Fixture risk",
                "ruleId": "graph.loss_guard.breakdown.v1", "polarity": "risk",
                "decisionStage": "LOSS_REDUCE", "actionGroup": "lossControl",
                "actionLevel": "review", "inferenceTraceId": trace_id,
                "nativeTypeDbReasoned": True,
            }],
            "traces": [{
                "id": trace_id, "symbol": "005930",
                "ruleId": "graph.loss_guard.breakdown.v1", "nativeTypeDbReasoned": True,
            }],
        },
    }


@contextmanager
def dispatch_boundary(snapshot, record_snapshot=None):
    """Replace I/O ports while retaining real event selection and rule evaluation."""
    requested_events = []
    attempted_jobs = []

    def record_missing(target):
        target.metadata.setdefault("ontology", {})["projection"] = {
            "saved": False, "status": "error", "graphStore": "typedb",
            "reason": "Fixture graph unavailable",
        }

    def new_event(name, aggregate_id, payload):
        event = DomainEvent(name=name, aggregate_id=aggregate_id, payload=payload)
        requested_events.append(event)
        return event

    def enqueue(job):
        attempted_jobs.append(job)
        decision = evaluate_notification_rule(job, default_notification_rule(job.message_type))
        job.context.update(decision.to_context())
        if not decision.should_send:
            job.status = "suppressed"
            job.last_error = decision.gate_reason
        return decision.should_send

    recorder = SimpleNamespace(record_snapshot=Mock(side_effect=record_snapshot or record_missing))
    queue = SimpleNamespace(
        enqueue=Mock(side_effect=enqueue),
        upsert_job=Mock(side_effect=AssertionError("No direct test send permitted in this fixture")),
    )
    runner = SimpleNamespace(
        apply_account_delivery_context=Mock(),
        render=Mock(return_value="Rendered preview"),
        deliver=Mock(side_effect=AssertionError("No transport I/O permitted")),
    )
    templates = SimpleNamespace(render=lambda message_type, context: render_notification(
        NotificationTemplate(message_type, "[{messageType}] {title}\n{rawLines}"), context,
    ))
    account = AccountConfig("fixture", "Fixture account", "toss", "https://example.test", "", "", "", [])
    with ExitStack() as stack:
        stack.enter_context(patch("pymysql.connect", side_effect=AssertionError("No MySQL I/O permitted")))
        stack.enter_context(patch.object(notification_testing, "runtime_settings", return_value={}))
        stack.enter_context(patch.object(notification_testing, "build_snapshot", return_value=snapshot))
        stack.enter_context(patch.object(notification_testing.stores, "account_reader", return_value=SimpleNamespace(load=lambda: [account])))
        repository = stack.enter_context(patch.object(notification_testing, "ontology_repository_from_settings", return_value=object()))
        for store_name in (
            "ontology_quality_sample_store", "ontology_projection_run_store",
            "investment_decision_episode_store", "investment_research_store",
        ):
            stack.enter_context(patch.object(notification_testing.stores, store_name, return_value=object()))
        constructor = stack.enter_context(patch.object(notification_testing, "PortfolioOntologyProjectionRecorder", return_value=recorder))
        build_runner = stack.enter_context(patch.object(notification_testing, "build_notification_queue_runner", return_value=runner))
        stack.enter_context(patch.object(notification_testing, "notification_store", return_value=templates))
        stack.enter_context(patch.object(notification_testing, "notification_queue_store", return_value=queue))
        stack.enter_context(patch.object(notification_testing, "new_domain_event", side_effect=new_event))
        yield SimpleNamespace(
            events=requested_events, jobs=attempted_jobs, queue=queue, runner=runner,
            recorder=recorder, repository=repository, constructor=constructor, build_runner=build_runner,
        )


class LegacyNotificationContractTests(unittest.TestCase):
    def assert_no_dispatch(self, boundary):
        self.assertEqual([], boundary.events)
        self.assertEqual([], boundary.jobs)
        boundary.queue.enqueue.assert_not_called()
        boundary.queue.upsert_job.assert_not_called()
        boundary.build_runner.assert_not_called()
        boundary.runner.deliver.assert_not_called()

    def test_contract_policy_fallback_keeps_factual_and_investment_types_separate(self):
        with patch.object(notification_configuration, "notification_rule_store", side_effect=OSError("fixture offline")):
            public = notification_configuration.list_notification_rules_payload()
            internal = notification_configuration.list_notification_rules_payload(include_internal=True)
        visible = {rule["messageType"] for rule in public["rules"]}
        self.assertEqual({
            "investmentInsight", "marketObservation", "portfolioHoldingsSnapshot",
            "portfolioActivityObservation", "investmentCalendarReminder", "newsDigest",
            "ontologyInferenceMissing", "monitorConnection", "externalDataConnection",
        }, visible)
        self.assertEqual(public["rules"], internal["rules"])
        self.assertNotIn("internalRules", public)
        evidence_types = {rule["messageType"] for rule in internal["internalRules"]}
        self.assertTrue({"holdingTiming", "watchlistOntologySignal"}.issubset(evidence_types))
        self.assertFalse(visible.intersection(evidence_types))
        self.assertTrue({
            "modelBuy", "modelSell", "monitorPnlChange", "monitorTrendChange",
            "externalCryptoMove", "externalDartDisclosure",
        }.isdisjoint(visible | evidence_types))

    def test_contract_similarity_bypass_uses_raw_delta_and_enabled_flag(self):
        rule = default_notification_rule("holdingTiming")
        condition = next(item for item in rule.similarity_bypass_conditions if item.condition_id == "loss_rate_worsened")
        self.assertEqual("profit_loss_worsened_lte", condition.condition_type)
        condition.value = 2
        rule.similarity_bypass_conditions = [condition]
        for current, enabled, expected in [(-11.9, True, False), (-12, True, True), (-12.1, True, True), (-12, False, False), (-8, True, False), (None, True, False)]:
            with self.subTest(current=current, enabled=enabled):
                condition.enabled = enabled
                job = NotificationJob.create("Delivery comparison", message_type="holdingTiming", context={"profitLossRate": current})
                decision = NotificationRuleDecision("holdingTiming", True, True, "send", "eligible", "Prior gate passed")
                result = apply_similarity_rule(decision, rule, 1, {"profitLossRate": -10}, job)
                self.assertEqual(expected, result.should_send)
                self.assertEqual(expected, result.similarity_bypassed)
                self.assertEqual("" if expected else "similar_repeat", result.suppression_reason)

    def test_contract_similarity_cannot_restore_blocked_graph_judgement(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create("Unproven investment event", message_type="investmentInsight", context={"profitLossRate": -30})
        decision = evaluate_notification_rule(job, rule)
        result = apply_similarity_rule(decision, rule, 1, {"profitLossRate": -10}, job)
        self.assertFalse(result.should_send)
        self.assertFalse(result.similarity_bypassed)
        self.assertEqual("missing_graph_inference", result.suppression_reason)
        self.assertEqual("blocked", result.gate_state)

    def test_contract_similarity_rejects_retired_score_fields(self):
        rule = default_notification_rule("holdingTiming")
        self.assertNotIn("holding_score_delta", [item.condition_id for item in rule.similarity_bypass_conditions])
        rule.similarity_bypass_conditions = [SimilarityBypassCondition(
            "holding_score_delta", "Retired score delta", "abs_number_delta_gte",
            field="holdingDecisionScore", value=1,
        )]
        job = NotificationJob.create("Score-only change", message_type="holdingTiming", context={"holdingDecisionScore": 100})
        decision = NotificationRuleDecision("holdingTiming", True, True, "send", "eligible", "Prior gate passed")
        result = apply_similarity_rule(decision, rule, 1, {"holdingDecisionScore": 1}, job)
        self.assertFalse(result.should_send)
        self.assertFalse(result.similarity_bypassed)
        self.assertEqual("similar_repeat", result.suppression_reason)

    def test_contract_live_connection_queues_but_heartbeat_is_audited_suppressed(self):
        with dispatch_boundary(live_snapshot()) as boundary:
            status, suppressed = notification_testing.notification_template_test_payload({"messageType": "monitorHeartbeat"})
            self.assertEqual(202, status)
            self.assertFalse(suppressed["queued"])
            self.assertFalse(suppressed["delivered"])
            self.assertTrue(suppressed["suppressed"])
            self.assertEqual("blocked", suppressed["deliveryGateState"])
            self.assertEqual("status_noise", boundary.jobs[0].context["deliverySuppressionReason"])
            self.assertEqual(["notification.test_requested"], [event.name for event in boundary.events])

            status, queued = notification_testing.notification_template_test_payload({"messageType": "monitorConnection"})
        self.assertEqual(202, status)
        self.assertTrue(queued["queued"])
        self.assertFalse(queued["delivered"])
        self.assertEqual(["suppressed", "pending"], [job.status for job in boundary.jobs])
        job = boundary.jobs[1]
        self.assertEqual("monitorConnection", job.message_type)
        self.assertEqual("fixture", job.account_id)
        self.assertIn("Connected", job.text)
        self.assertFalse(job.context["notificationTestBypassPolicy"])
        self.assertEqual(boundary.events[1].event_id, job.source_event_id)
        self.assertEqual("notification.test_requested", job.source_event_name)
        self.assertEqual("notification.job_queued", boundary.events[2].name)
        self.assertEqual(job.job_id, boundary.events[2].aggregate_id)
        self.assertEqual(job.source_event_id, boundary.events[2].payload["sourceEventId"])
        boundary.runner.deliver.assert_not_called()
        boundary.queue.upsert_job.assert_not_called()

    def test_contract_test_preview_never_queues_or_delivers(self):
        with dispatch_boundary(live_snapshot()) as boundary:
            status, payload = notification_testing.notification_template_test_payload({"messageType": "monitorConnection", "dryRun": True})
        self.assertEqual(200, status)
        self.assertTrue(payload["dryRun"])
        self.assertFalse(payload["direct"])
        self.assertFalse(payload["delivered"])
        self.assertEqual("Rendered preview", payload["message"])
        self.assertEqual(["notification.test_requested"], [event.name for event in boundary.events])
        boundary.queue.enqueue.assert_not_called()
        boundary.queue.upsert_job.assert_not_called()
        boundary.runner.deliver.assert_not_called()

    def test_contract_investment_test_rejects_missing_graph_for_all_delivery_modes(self):
        for options in ({}, {"bypassPolicy": True}, {"directSend": True}, {"dryRun": True}):
            with self.subTest(options=options), dispatch_boundary(live_snapshot()) as boundary:
                status, payload = notification_testing.notification_template_test_payload({"messageType": "investmentInsight", **options})
                self.assertEqual(409, status)
                self.assertFalse(payload["delivered"])
                self.assertEqual("ontologyInferenceMissing", payload["blockedBy"])
                self.assertEqual("ontologyInferenceMissing", payload["event"]["messageType"])
                self.assertIn("TypeDB", " ".join(payload["event"]["lines"]))
                boundary.recorder.record_snapshot.assert_called_once()
                self.assert_no_dispatch(boundary)

    def test_contract_investment_test_keeps_unqualified_native_trace_non_deliverable(self):
        for options in ({}, {"bypassPolicy": True}, {"directSend": True}, {"dryRun": True}):
            snapshot = live_snapshot()
            snapshot.metadata["ontology"] = {"typedb": unqualified_native_projection()}
            with self.subTest(options=options), dispatch_boundary(snapshot) as boundary:
                status, payload = notification_testing.notification_template_test_payload({"messageType": "investmentInsight", **options})
                self.assertEqual(422, status, payload)
                self.assertFalse(payload["delivered"])
                self.assertNotIn("blockedBy", payload)
                self.assertNotIn("jobId", payload)
                decision = snapshot.decisions[0]
                self.assertEqual("typedbInferenceBox", decision.decision_basis)
                envelope = decision.relation_rule_context["actionEnvelope"]
                self.assertTrue(envelope["judgementBlocked"])
                self.assertFalse(envelope["investmentJudgementAvailable"])
                self.assertEqual([], envelope["allowedActions"])
                self.assertEqual([], envelope["coreInferenceSelection"]["eligibleRuleIds"])
                boundary.repository.assert_not_called()
                boundary.recorder.record_snapshot.assert_not_called()
                self.assert_no_dispatch(boundary)

    def test_contract_projection_precedes_event_selection_and_reuses_existing_result(self):
        snapshot = live_snapshot()
        projection = unqualified_native_projection()
        order = []
        original_type_check = RealtimeMonitor.type_check_events_for_snapshot

        def record(target):
            order.append("projection")
            target.metadata["ontology"] = {"typedb": deepcopy(projection)}

        def type_check(monitor, target):
            self.assertEqual(projection, target.metadata["ontology"]["typedb"])
            order.append("type-check")
            return original_type_check(monitor, target)

        with dispatch_boundary(snapshot, record) as boundary, patch.object(RealtimeMonitor, "type_check_events_for_snapshot", type_check):
            first = notification_testing.notification_test_event("monitorConnection", snapshot)
            second = notification_testing.notification_test_event("monitorConnection", snapshot)
        self.assertEqual(["projection", "type-check", "type-check"], order)
        self.assertEqual("monitorConnection", first.rule)
        self.assertEqual("monitorConnection", second.rule)
        boundary.recorder.record_snapshot.assert_called_once_with(snapshot)
        self.assertEqual("notification-test", boundary.constructor.call_args.kwargs["source"])
        self.assert_no_dispatch(boundary)

    def test_contract_failed_projection_cannot_reach_an_investment_runner(self):
        snapshot = live_snapshot()

        def fail_projection(_target):
            raise RuntimeError("Fixture TypeDB transaction unavailable")

        with dispatch_boundary(snapshot, fail_projection) as boundary:
            status, payload = notification_testing.notification_template_test_payload({"messageType": "investmentInsight", "bypassPolicy": True})
        self.assertEqual(409, status)
        self.assertFalse(payload["delivered"])
        self.assertEqual("ontologyInferenceMissing", payload["blockedBy"])
        projection = snapshot.metadata["ontology"]["projection"]
        self.assertEqual("typedb", projection["graphStore"])
        self.assertEqual("error", projection["status"])
        self.assertFalse(projection["saved"])
        self.assertIn("Fixture TypeDB transaction unavailable", projection["reason"])
        self.assert_no_dispatch(boundary)

    def test_contract_realtime_status_preserves_counters_and_nested_health_metadata(self):
        cycle = monitoring_cycle_completed_event(["fixture"], 2, 1, False, True)
        cycle.payload["privatePacket"] = {"credential": "fixture-private-value"}
        queue_summary = {
            "pending": 2, "failed": 3, "suppressed": 4, "actionable_failed": 1,
            "historical_failed": 3, "intentional_suppressed": 4,
            "active_failure_window_minutes": 45,
            "oldest_actionable_failure_at": "2026-09-12T01:00:00Z",
            "suppression_categories": {"data_guard": 1, "unchanged_decision": 3},
        }
        before = deepcopy(queue_summary)
        event_log = SimpleNamespace(
            event_counts=Mock(return_value={cycle.name: 1}),
            latest_events_by_name=Mock(return_value={cycle.name: cycle}),
            latest_events=Mock(return_value=[cycle]),
        )
        with patch.object(web_events, "operational_read_settings", return_value={}), \
                patch.object(web_events.stores, "event_log", return_value=event_log), \
                patch.object(web_events, "notification_queue_store", return_value=SimpleNamespace(summary=lambda: queue_summary)), \
                patch.object(web_events.stores, "ai_inference_queue_store", return_value=SimpleNamespace(summary=lambda: {"pendingCount": 5})):
            result = web_events.realtime_status_payload()
        self.assertEqual(before, queue_summary)
        self.assertEqual(before, result["notificationJobs"])
        self.assertEqual({"pendingCount": 5}, result["aiInferenceQueue"])
        self.assertEqual("", result["storeWarning"])
        self.assertEqual({cycle.name: 1}, result["events"])
        self.assertEqual({"snapshotCount": 2}, result["monitoring"]["cycle"]["payload"])
        self.assertEqual(result["monitoring"]["cycle"], result["latestEvents"][0])
        self.assertNotIn("fixture-private-value", repr(result))
        self.assertEqual(1, cycle.payload["alertCount"])
        event_log.latest_events.assert_called_once_with(limit=12)

    def test_contract_realtime_status_degrades_without_inventing_success(self):
        with patch.object(web_events, "operational_read_settings", return_value={}), \
                patch.object(web_events.stores, "event_log", side_effect=OSError("fixture storage offline")), \
                patch.object(web_events, "notification_queue_store", side_effect=OSError("fixture queue offline")), \
                patch.object(web_events.stores, "ai_inference_queue_store", side_effect=OSError("fixture AI queue offline")), \
                patch.object(web_events.REALTIME_HUB, "latest_events", return_value=[]):
            result = web_events.realtime_status_payload()
        self.assertEqual("fixture storage offline", result["storeWarning"])
        self.assertEqual({}, result["monitoring"])
        self.assertEqual([], result["latestEvents"])
        self.assertEqual({}, result["events"])
        self.assertEqual({
            "pending": 0, "awaiting_ai": 0, "processing": 0, "done": 0,
            "superseded": 0, "suppressed": 0, "failed": 0,
        }, result["notificationJobs"])
        self.assertEqual({"pendingCount": 0, "retryCount": 0, "processingCount": 0, "failedCount": 0}, result["aiInferenceQueue"])


if __name__ == "__main__":
    unittest.main()
