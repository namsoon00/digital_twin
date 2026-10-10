"""Causal notification threads use durable sources, never ticker proximity."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
import uuid
from unittest.mock import Mock, patch

from stabilization_database import StabilizationDatabaseCase, fixture_snapshot
from test_notification_delivery_reliability import receipt
from test_market_observation_delivery import market_event
from test_independent_reasoning_engine import (
    descriptor, source_event, FakeAssembler, FakeExecutor, FakeCandidateBuilder,
    FakeAIInsightHandoffService,
)
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.domain.investment_conversation import conversation_sources, key
from digital_twin.modules.notifications.domain.research_thread import ResearchDeliveryDeferred
from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
from digital_twin.modules.notifications.application.notification.intake import NotificationIngressService
from digital_twin.modules.notifications.infrastructure.notification.transport import TelegramNotifier
from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import MySQLNotificationJobStore
from digital_twin.modules.reasoning.application.independent_reasoning_engine import V2ReasoningEngine
from digital_twin.infrastructure.transactions.monitoring import MySQLMonitoringCycleRecorder


def job(kind, account="thread-account", symbol="AAPL", snapshot="graph-1", events=(), world="portfolio-world"):
    context = {"accountId": account, "symbol": symbol}
    if kind == "investmentInsight":
        context.update(notificationAnalysisSources={"accountId": account, "symbol": symbol, "worldId": world, "sourceEventIds": list(events)},
                       ontologyRelationContext={"worldId": world, "sourceAboxSnapshotId": snapshot})
    if kind == "aiObservation":
        context["aiControlObservation"] = {"input": {"accountId": account, "symbol": symbol, "worldId": world, "sourceSnapshotId": snapshot}}
    return NotificationJob.create("관찰 내용", account_id=account, message_type=kind, context=context)


class InvestmentConversationTests(unittest.TestCase):
    def test_sources_scope_followup_and_immutable_replays(self):
        value = job("aiObservation")
        packet = value.context["aiControlObservation"]["input"]
        packet.update(lastDeliveredNotification={"jobId": "previous"}, followUpEvaluations=[{"transitionVerified": True, "baselineJobId": "different"}])
        self.assertFalse(conversation_sources(value)["parentJobId"])
        packet["followUpEvaluations"][0]["baselineJobId"] = "previous"
        self.assertEqual("previous", conversation_sources(value)["parentJobId"])
        packet["accountId"] = "other"
        self.assertIsNone(conversation_sources(value))
        for flag in ("notificationReplayPreserveOriginal", "transportDelivery"):
            value = job("marketObservation")
            value.context[flag] = True
            self.assertIsNone(conversation_sources(value))
        self.assertIsNone(conversation_sources(job("aiObservationDiagnostic")))
        value = job("investmentInsight")
        value.context["notificationAnalysisSources"]["worldId"] = "other"
        self.assertIsNone(conversation_sources(value))

    def test_engine_passes_exact_request_sources_through_notification_ingress(self):
        handoff = FakeAIInsightHandoffService()
        engine = V2ReasoningEngine(descriptor(), FakeAssembler(), FakeExecutor(), FakeCandidateBuilder(),
                                  delivery_authorized_provider=lambda: True, ai_insight_handoff_service=handoff)
        engine.consume([source_event()])
        event = handoff.events[0]
        origin = event.metadata["notificationAnalysisSources"]
        self.assertEqual(["event:NVDA"], origin["sourceEventIds"])
        self.assertEqual("acct", origin["accountId"])
        self.assertTrue(origin["analyzedAt"])
        ingress = NotificationIngressService()
        restored = ingress.job_from_request(ingress.request_from_alert(event))
        self.assertEqual(origin, restored.context["notificationAnalysisSources"])


class InvestmentConversationStorageTests(StabilizationDatabaseCase):
    def setUp(self):
        super().setUp()
        self.account = "conversation-" + uuid.uuid4().hex[:12]

    def tearDown(self):
        # Transport-only fixtures must not enter a later test's real worker.
        with self.notifications.transaction() as connection:
            accounts = (self.account, self.account + "other")
            connection.execute("DELETE FROM notification_delivery_attempts WHERE job_id IN "
                "(SELECT job_id FROM notification_jobs WHERE account_id IN (%s,%s))", accounts)
            connection.execute("DELETE FROM notification_jobs WHERE account_id IN (%s,%s)", accounts)

    def register(self, value, event_id=""):
        # Isolate transport membership from independently tested content admission.
        with self.notifications.transaction() as connection:
            self.notifications.register_investment_conversation(connection, value)
            self.notifications.upsert_job_with_connection(connection, value)
            if event_id:
                self.notifications.bind_market_conversation_event(connection, value, event_id)
        return value

    def value(self, kind, **kwargs):
        return job(kind, account=self.account, **kwargs)

    def thread(self, value):
        return value.context["notificationConversation"]["threadKey"]

    def test_market_relation_ai_followup_share_verified_root_across_restart_and_retention(self):
        market = self.register(self.value("marketObservation"), "source-1")
        relation = self.register(self.value("investmentInsight", events=["source-1"]))
        ai = self.register(self.value("aiObservation"))
        self.assertEqual(self.thread(market), self.thread(relation))
        self.assertEqual(self.thread(market), self.thread(ai))
        notifier = TelegramNotifier("123:fake", "456")
        notifier.post_message = Mock(side_effect=[receipt(str(i)) for i in range(10, 16)])
        service = NotificationDispatchService(self.notifications, lambda _: notifier)
        with self.assertRaises(ResearchDeliveryDeferred):
            service.deliver(ai, {self.account: object()}, ai.text)
        notifier.post_message.assert_not_called()
        service.deliver(market, {self.account: object()}, market.text)
        self.notifications.mark_done(market)
        service.deliver(relation, {self.account: object()}, relation.text)
        self.assertEqual(10, notifier.post_message.call_args.args[0]["reply_parameters"]["message_id"])
        with self.notifications.transaction() as connection:
            connection.execute("DELETE FROM notification_jobs WHERE job_id=%s", (market.job_id,))
        restarted = MySQLNotificationJobStore(self.settings)
        NotificationDispatchService(restarted, lambda _: notifier).deliver(ai, {self.account: object()}, ai.text)
        self.assertEqual(10, notifier.post_message.call_args.args[0]["reply_parameters"]["message_id"])
        followup = self.value("aiObservation", snapshot="next-graph")
        followup.context["aiControlObservation"]["input"].update(lastDeliveredNotification={"jobId": ai.job_id},
            followUpEvaluations=[{"baselineJobId": ai.job_id, "transitionVerified": True}])
        self.register(followup)
        self.assertEqual(self.thread(ai), self.thread(followup))
        # A new chat never receives a message id belonging to the former chat.
        other = TelegramNotifier("123:fake", "999")
        other.post_message = Mock(return_value=receipt("99"))
        NotificationDispatchService(restarted, lambda _: other).deliver(followup, {self.account: object()}, followup.text)
        self.assertNotIn("reply_parameters", other.post_message.call_args.args[0])

    def test_unrelated_scope_and_multiple_sources_do_not_merge_conversations(self):
        first = self.register(self.value("investmentInsight", events=["event-1"]))
        unrelated = self.register(self.value("investmentInsight", events=["event-2"], snapshot="graph-2"))
        self.assertNotEqual(self.thread(first), self.thread(unrelated))
        for value in [job("aiObservation", account=self.account + "other"), self.value("aiObservation", world="other-world"), self.value("investmentInsight", events=["event-1"], world="other-world"), self.value("aiObservation", symbol="MSFT")]:
            self.register(value)
            self.assertNotEqual(self.thread(first), self.thread(value))
        ambiguous = self.register(self.value("investmentInsight", snapshot="graph-3", events=["event-1", "event-2"]))
        self.assertEqual("multiple-conversations", ambiguous.context["notificationConversation"]["reason"])
        again = self.register(self.value("aiObservation"))
        self.assertEqual(self.thread(first), self.thread(again))

    def test_registration_rolls_back_without_orphan_sources(self):
        value = self.value("investmentInsight", events=["rollback-event"])
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.notifications.transaction() as connection:
                self.notifications.register_investment_conversation(connection, value)
                raise RuntimeError("rollback")
        with self.notifications.connect() as connection:
            self.assertIsNone(connection.execute("SELECT job_id FROM notification_conversation_jobs WHERE job_id=%s", (value.job_id,)).fetchone())
            self.assertIsNone(connection.execute("SELECT source_key FROM notification_conversation_sources WHERE source_key=%s", (key(self.account, "AAPL", "event", "", "rollback-event"),)).fetchone())

    def test_concurrent_first_sources_use_one_root_despite_older_read_views(self):
        barrier = Barrier(2)
        values = [self.value("aiObservation"), self.value("investmentInsight")]
        def register(value):
            with self.notifications.transaction() as connection:
                connection.execute("SELECT source_key FROM notification_conversation_sources LIMIT 1").fetchall()
                barrier.wait(timeout=10)
                self.notifications.register_investment_conversation(connection, value)
                self.notifications.upsert_job_with_connection(connection, value)
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(register, values))
        self.assertEqual(self.thread(values[0]), self.thread(values[1]))
        # Re-created outbox payloads cannot reassign retained membership.
        repeated = self.value("aiObservation", snapshot="changed-source")
        repeated.job_id = values[0].job_id
        self.register(repeated)
        self.assertEqual(self.thread(values[0]), self.thread(repeated))
        repeated.context["aiControlObservation"]["input"]["worldId"] = "other-world"
        with self.assertRaises(ValueError):
            self.register(repeated)

    def test_monitor_transaction_binds_actual_source_event_and_preserves_dedupe(self):
        snapshot = fixture_snapshot(account=self.account)
        event = market_event()
        event.account_id, event.symbol, event.key = self.account, "AAPL", "market-" + self.account
        recorder = MySQLMonitoringCycleRecorder(self.settings)
        result = recorder.record_cycle([self.account], [snapshot], [event])
        self.assertEqual(1, result.queued)
        with self.notifications.connect() as connection:
            member = connection.execute("SELECT job_id,thread_key FROM notification_conversation_jobs WHERE account_id=%s", (self.account,)).fetchone()
            self.assertIsNotNone(member)
            sources = connection.execute("SELECT source_key FROM notification_conversation_sources WHERE thread_key=%s", (member["thread_key"],)).fetchall()
            self.assertTrue(sources)
            row = connection.execute("SELECT text,payload_json FROM notification_jobs WHERE job_id=%s", (member["job_id"],)).fetchone()
        original = self.notifications.job_from_row(row)
        self.assertEqual(member["thread_key"], self.thread(original))
        self.assertFalse(self.notifications.enqueue(deepcopy(original)))
