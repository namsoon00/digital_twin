"""Delivery audit regressions: pure policy, fake Telegram and isolated MySQL."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
from digital_twin.modules.notifications.domain.delivery_recovery import notification_failure_retry_at
from digital_twin.modules.notifications.domain.external_signal_alerts import ExternalSignalAlertMixin
from digital_twin.modules.notifications.domain.notifications import NotificationJob
from digital_twin.modules.notifications.domain.notification_rules import (
    apply_similarity_rule, apply_state_cooldown_rule, default_notification_rule,
    evaluate_notification_rule, notification_fingerprint,
)
from digital_twin.modules.notifications.infrastructure.notification.transport import TelegramNotifier, NotificationResult
from digital_twin.infrastructure.web.adapters import notification_configuration
from digital_twin.infrastructure.mysql_retention import (
    _delete_delivered_notification_rows_over_keep_count, _delete_terminal_notification_rows,
)
from stabilization_database import StabilizationDatabaseCase
from test_ai_insight_delivery_boundary import completed_insight_context, MemoryQueue
from digital_twin.modules.notifications.application.notification.workflow import NotificationQueueRunner


def receipt(message_id="1"):
    return NotificationResult(True, "fake", metadata={"messageIds": [message_id],
        "receiptVerified": True, "destinationVerified": True})


class DeliveryReliabilityTests(unittest.TestCase):
    def test_verified_ai_and_profit_loss_cannot_undo_repeat_floor(self):
        rule = default_notification_rule("investmentInsight")
        job = NotificationJob.create("verified", account_id="fixture", message_type=rule.message_type,
                                     context=completed_insight_context())
        for age, sent, expected in ((1, "sent", False), (59, "sent", False), (60, "sent", True), (0, "", False)):
            with self.subTest(age=age, sent=sent):
                decision = apply_state_cooldown_rule(evaluate_notification_rule(job, rule), rule, 1,
                    job.context, sent, age, job)
                decision = apply_similarity_rule(decision, rule, 1, job.context, job)
                self.assertEqual(expected, decision.should_send)
        job.context = {"symbol": "FIXTURE", "ontologyInsight": {"changeState": "worsening"},
            "ontologyRelationContext": {"source": "typedbInferenceBox", "graphStoreUsed": True,
                "fallbackUsed": False, "decision": {"basis": "typedbInferenceBox", "actionGroup": "lossControl"},
                "decisionState": {"reviewLevel": "check", "dataState": "sufficient", "changeState": "worsening"}}}
        for age, allowed in ((1, False), (9, False), (10, True)):
            decision = apply_state_cooldown_rule(evaluate_notification_rule(job, rule), rule, 1,
                job.context, "sent", age, job)
            decision = apply_similarity_rule(decision, rule, 1, job.context, job)
            self.assertEqual(allowed, decision.should_send)
        queued = NotificationJob.create("verified", account_id="fixture", message_type=rule.message_type,
                                        context=completed_insight_context())
        queue = MemoryQueue(queued)
        queue.recheck_delivery_cadence = Mock(return_value={"allowed": False, "reason": "another job delivered after admission"})
        transport = Mock()
        account = SimpleNamespace(account_id="fixture", quiet_hours_active=lambda *args: False)
        runner = NotificationQueueRunner(queue, SimpleNamespace(load_all=lambda: [account]), lambda _: transport)
        runner.run_once()
        self.assertEqual("suppressed", queued.status)
        queue.recheck_delivery_cadence.assert_called_once()
        transport.send.assert_not_called()

    def test_distinct_account_changes_are_not_text_duplicates(self):
        rule = default_notification_rule("portfolioActivityObservation")
        def event(episode, symbol):
            return NotificationJob.create("quantity changed", account_id="fixture", message_type=rule.message_type,
                context={"title": "보유 수량 변화", "portfolioActivityEpisode": {"episodeId": episode, "symbols": [symbol]}})
        first, second, repeat = event("a", "AAA"), event("b", "BBB"), event("a", "AAA")
        self.assertNotEqual(notification_fingerprint(first, rule), notification_fingerprint(second, rule))
        self.assertEqual(notification_fingerprint(first, rule), notification_fingerprint(repeat, rule))

    def test_partial_chunks_resume_across_dispatcher_restart_with_frozen_body(self):
        class Queue:
            def update(self, job):
                self.saved = deepcopy(job.to_dict())
        queue = Queue()
        body = "a" * 2500 + "\n" + "b" * 2500
        job = NotificationJob.create(body, account_id="fixture", message_type="newsDigest")
        first = TelegramNotifier("123:fake", "456")
        first.post_message = Mock(side_effect=[receipt("one"), NotificationResult(False, "fake", "HTTP 503")])
        with self.assertRaisesRegex(RuntimeError, "503"):
            NotificationDispatchService(queue, lambda _: first).deliver(job, {"fixture": object()}, body)
        restarted = NotificationJob.from_dict(queue.saved)
        self.assertEqual(["one"], restarted.context["transportDelivery"]["checkpoint"]["messageIds"])
        second = TelegramNotifier("123:rotated", "456")
        second.post_message = Mock(return_value=receipt("two"))
        service = NotificationDispatchService(queue, lambda _: second)
        service.deliver(restarted, {"fixture": object()}, "render changed during retry")
        self.assertEqual(1, second.post_message.call_count)
        self.assertTrue(second.post_message.call_args.args[0]["text"].startswith("(2/2)"))
        service.deliver(NotificationJob.from_dict(queue.saved), {"fixture": object()}, body)
        self.assertEqual(1, second.post_message.call_count, "completion persistence retry must not resend")

    def test_resume_rejects_changed_destination_or_artifact(self):
        checkpoint = {}
        notifier = TelegramNotifier("123:fake", "456")
        notifier.post_message = Mock(return_value=receipt())
        notifier.send_resumable("original", on_checkpoint=lambda value: checkpoint.update(value))
        for token, destination, text in (("123:fake", "789", "original"), ("123:fake", "456", "changed"), ("999:fake", "456", "original")):
            other = TelegramNotifier(token, destination)
            other.post_message = Mock()
            self.assertFalse(other.send_resumable(text, checkpoint=checkpoint).delivered)
            other.post_message.assert_not_called()

    def test_only_format_errors_fallback_and_retry_after_is_honored(self):
        notifier = TelegramNotifier("123:fake", "456")
        for code, description, count in ((400, "Bad Request: can't parse entities", 2), (503, "unavailable", 1), (429, "Too Many Requests", 1)):
            failure = notifier.api_failure({"error_code": code, "description": description}, "failed")
            notifier.post_message = Mock(side_effect=[failure, receipt()])
            notifier.send("<b>test</b>")
            self.assertEqual(count, notifier.post_message.call_count)
        failure = notifier.http_failure(HTTPError("https://example.invalid", 429, "limited", {},
            BytesIO(b'{"error_code":429,"parameters":{"retry_after":120}}')))
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.assertEqual("2026-10-01T00:02:00Z", notification_failure_retry_at(failure.reason, 1,
            now=now, retry_after_seconds=failure.metadata["retryAfterSeconds"]))

    def test_external_incidents_are_per_provider_and_recovery_is_observed(self):
        monitor = ExternalSignalAlertMixin()
        monitor.criteria = lambda *args: list(args)
        def snapshot(statuses, stamp):
            return SimpleNamespace(account_id="fixture", account_label="fixture", generated_at=stamp,
                metadata={}, external_signals={"statuses": statuses})
        failed = {"source": "A", "ok": False, "message": "timeout"}
        first = snapshot([failed, {**failed, "source": "B"}], "t1")
        events = monitor.external_signal_events(first, {})
        repeated = snapshot([failed, failed], "t2")
        again = monitor.external_signal_events(repeated, {"metadata": first.metadata})
        self.assertEqual(events[0].metadata["connectionIncidentId"], again[0].metadata["connectionIncidentId"])
        self.assertIn("B", repeated.metadata["externalConnectionIncidents"], "missing provider is not recovery")
        rule = default_notification_rule("externalDataConnection")
        def fingerprint(event):
            return notification_fingerprint(NotificationJob.create("failure", account_id="fixture", message_type=rule.message_type, context=event.metadata), rule)
        self.assertEqual(fingerprint(events[0]), fingerprint(again[0]))
        self.assertNotEqual(fingerprint(events[0]), fingerprint(events[1]))
        recovered = snapshot([{"source": "A", "ok": True}], "t3")
        recovery = monitor.external_signal_events(recovered, {"metadata": repeated.metadata})
        self.assertEqual("recovered", recovery[0].metadata["connectionState"])
        self.assertNotEqual(fingerprint(events[0]), fingerprint(recovery[0]))
        relapse = snapshot([failed], "t4")
        new_incident = monitor.external_signal_events(relapse, {"metadata": recovered.metadata})
        self.assertNotEqual(fingerprint(events[0]), fingerprint(new_incident[0]))

    def test_schedule_describes_event_and_actual_quote_policy(self):
        with patch.object(notification_configuration, "runtime_settings", return_value={"marketObservationImmediateCadenceMinutes": "20"}), \
             patch.object(notification_configuration.stores, "monitor_store", return_value=SimpleNamespace(sent={})), \
             patch.object(notification_configuration.stores, "account_reader", return_value=SimpleNamespace(load=lambda: [])):
            rows = {item["messageType"]: item for item in notification_configuration.notification_schedules_payload()["schedules"]}
        self.assertEqual(20, rows["marketObservation"]["cadenceMinutes"])
        for kind in ("investmentInsight", "newsDigest", "portfolioActivityObservation", "portfolioHoldingsSnapshot", "investmentCalendarReminder"):
            self.assertEqual("event", rows[kind]["cadenceMode"])
            self.assertEqual("", rows[kind]["nextEligibleAt"])
            self.assertEqual(0, rows[kind]["cadenceMinutes"])


class DeliveryHistoryDatabaseTests(StabilizationDatabaseCase):
    def test_receipt_clock_and_scoped_history_survive_other_subject_traffic(self):
        store = self.notifications
        rule = default_notification_rule("monitorConnection")
        now = datetime.now(timezone.utc)
        def make(account="fixture", symbol="AAA", title="connection"):
            return NotificationJob.create(title, account_id=account, message_type=rule.message_type,
                context={"symbol": symbol, "title": title})
        old = make()
        old.created_at = (now - timedelta(hours=4)).isoformat().replace("+00:00", "Z")
        old.context["deliveryFingerprint"] = notification_fingerprint(old, rule)
        store.upsert_job(old)
        attempt = store.start_delivery_attempt(old, "test", "account")
        store.complete_delivery_attempt(old, attempt, True)
        store.mark_done(old)
        for i in range(30):
            noise = make("other" if i % 3 == 0 else "fixture", "BBB" if i % 3 == 1 else "AAA", "unrelated")
            noise.context["deliveryFingerprint"] = notification_fingerprint(noise, rule)
            store.upsert_job(noise)
        unconfirmed = make()
        unconfirmed.context["deliveryFingerprint"] = notification_fingerprint(unconfirmed, rule)
        store.mark_done(unconfirmed)
        current = make()
        with store.connect() as connection:
            count, _, last_sent = store.similar_history_with_connection(connection, current, rule, notification_fingerprint(current, rule))
        self.assertEqual(1, count)
        self.assertNotEqual(old.created_at, last_sent)
        self.assertGreaterEqual(datetime.fromisoformat(last_sent.replace("Z", "+00:00")), now)
        earlier = NotificationJob.create("verified", account_id="fixture", message_type="investmentInsight",
            context={"symbol": "AAA", "ontologyInsight": {"subject": "AAA"}, "deliveryCadenceTier": "material"})
        queued = NotificationJob.create("waiting", account_id="fixture", message_type="investmentInsight", context=deepcopy(earlier.context))
        store.upsert_job(earlier)
        attempt = store.start_delivery_attempt(earlier, "test", "account")
        store.complete_delivery_attempt(earlier, attempt, True)
        store.mark_done(earlier)
        self.assertFalse(store.recheck_delivery_cadence(queued)["allowed"])
        # Cleanup must not remove the successful baseline even when newer
        # messages have exhausted the count budget or enqueue time is old.
        expired = make("expired-fixture")
        expired.created_at = old.created_at
        store.mark_done(expired)
        with store.connect() as connection:
            _delete_delivered_notification_rows_over_keep_count(connection, 0, 100)
            _delete_terminal_notification_rows(connection, now.isoformat().replace("+00:00", "Z"), 100)
            remaining = {row["job_id"] for row in connection.execute(
                "SELECT job_id FROM notification_jobs WHERE job_id IN (%s, %s, %s)",
                (old.job_id, earlier.job_id, expired.job_id)).fetchall()}
        self.assertEqual({old.job_id, earlier.job_id}, remaining)
        self.assertFalse(store.recheck_delivery_cadence(queued)["allowed"])
        for row in store.jobs():
            if row.account_id in {"fixture", "other"}:
                store.mark_suppressed(row, "fixture cleanup")

    def test_due_retries_are_not_starved_by_new_pending_jobs(self):
        store = self.notifications
        kind = "fixture-retry-fairness"
        retry = NotificationJob.create("retry", message_type=kind)
        retry.attempts = 1
        store.mark_failed(retry, "HTTP 503")
        now = datetime.now(timezone.utc)
        with store.connect() as connection:
            connection.execute("UPDATE notification_jobs SET retry_at=%s WHERE job_id=%s",
                ((now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"), retry.job_id))
        for _ in range(5):
            store.upsert_job(NotificationJob.create("new", message_type=kind))
        claimed = store.claim_pending(limit=1, include_message_types=(kind,))
        self.assertEqual([retry.job_id], [row.job_id for row in claimed])
