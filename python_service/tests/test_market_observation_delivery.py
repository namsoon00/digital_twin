import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from digital_twin import service_manager
from digital_twin.modules.market_data.domain.market_observations import market_observation_delivery_admission, QUOTE_BASELINE_VERSION, quote_witness
from digital_twin.modules.portfolio.domain.portfolio import AlertEvent
from digital_twin.infrastructure.mysql_monitoring_stores import MySQLMonitoringCycleRecorder


def state_with_outbox_anchor(price):
    return {
        "metadata": {
            "marketObservationBaselines": {
                "000660": {
                    "price": 1_676_500,
                    "reasoningPrice": 1_676_500,
                    "initialPrice": 1_676_500,
                    "outboxPrice": price,
                    "quoteBaselineVersion": QUOTE_BASELINE_VERSION,
                    "currency": "KRW",
                    "outboxQueuedAt": "2026-09-06T23:11:50Z",
                }
            }
        }
    }


def market_event(anchor=1_676_500, current=1_729_000, symbol="000660", currency="KRW", stamp="2026-09-06T23:12:09Z"):
    return AlertEvent(
        "default",
        "Test",
        "WATCH",
        "marketObservation",
        "default:market-observation:" + symbol + ":up",
        "SK하이닉스",
        [],
        symbol,
        metadata={
            "deliveryDeferred": False,
            "marketObservation": {
                "outboxBaselinePrice": anchor,
                "baselinePrice": anchor,
                "currentPrice": current,
                "quoteWitness": quote_witness({"symbol": symbol, "currency": currency, "current_price": current,
                    "source_as_of": stamp, "source_timestamp_state": "provider",
                    "freshness_status": "fresh", "data_quality": "actual"}, stamp),
                "deliveryThresholdPct": 3,
                "direction": "up",
                "currency": currency,
            },
        },
        generated_at=stamp,
    )


class FakeCursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class SentRowConnection:
    def __init__(self, row=None):
        self.row = row
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        return FakeCursor(self.row)


class MarketObservationDeliveryTests(unittest.TestCase):
    def assert_price_latency_counts_only_actual_verified_account_receipts(self):
        from digital_twin.modules.notifications.domain.price_delivery_metrics import summarize_price_delivery
        from digital_twin.modules.notifications.application.notification.dispatch import NotificationDispatchService
        from digital_twin.modules.notifications.domain.notifications import NotificationJob
        from types import SimpleNamespace
        # Exercise the actual writer's channel/audience contract, not a handwritten alias.
        queue = MagicMock(spec=['start_delivery_attempt', 'complete_delivery_attempt'])
        queue.start_delivery_attempt.return_value = 'fixture-attempt'
        notifier = SimpleNamespace(send=lambda message: SimpleNamespace(
            delivered=True, label='fixture', metadata={'receiptVerified': True}))
        dispatch = NotificationDispatchService(queue, lambda account: notifier)
        dispatch.deliver(NotificationJob.create('fixture', account_id='fixture', message_type='marketObservation'),
                         {'fixture': object()}, 'fixture')
        _job, channel, audience, _audit = queue.start_delivery_attempt.call_args.args
        self.assertEqual(('accountNotification', 'account'), (channel, audience))
        job = {"job_id": "a", "status": "done", "data_quality": "actual", "is_mock": 0,
               "created_at": "2026-10-23T01:00:10Z", "source_at": "2026-10-23T01:00:00Z"}
        receipt = {"job_id": "a", "status": "delivered", "audience": audience, 'channel': channel,
                   "metadata": queue.complete_delivery_attempt.call_args.kwargs['metadata'],
                   "completed_at": "2026-10-23T01:00:40Z"}
        jobs = [job, {**job, "job_id": "mock", "is_mock": 1}, {**job, "job_id": "b", "status": "pending"}]
        attempts = [receipt, {**receipt, "job_id": "mock"},
                    {**receipt, "job_id": "b", "metadata": {"receiptVerified": False}},
                    {**receipt, "job_id": "b", "audience": "diagnostic"},
                    {**receipt, "job_id": "b", "channel": "operationsTelegram"},
                    {**receipt, "job_id": "b", "status": "failed"},
                    {**receipt, "completed_at": "2026-10-23T01:00:50Z"}]
        result = summarize_price_delivery(jobs, attempts)
        self.assertEqual(1, result["verifiedDeliveredJobs"])
        self.assertEqual(30, result["queueToReceipt"]["medianSeconds"])
        self.assertEqual(40, result["sourceToReceipt"]["p95Seconds"])
        self.assertEqual({"sampleCount": 0}, summarize_price_delivery(jobs, [])['queueToReceipt'])
        self.assertEqual(1, summarize_price_delivery(jobs, [])['doneWithoutVerifiedReceiptJobs'])
        self.assertEqual(0, result['doneWithoutVerifiedReceiptJobs'])
        invalid = summarize_price_delivery([{**job, 'source_at': '2026-10-23T02:00:00Z'}], [receipt])
        self.assertEqual(1, invalid['missingOrInvalidClockCount'])
        self.assertEqual(0, invalid['sourceToReceipt']['sampleCount'])

    def assert_price_metrics_bounds_ids_before_reading_json(self):
        from digital_twin.modules.notifications.infrastructure.price_metrics import PriceMetricsStore
        store = PriceMetricsStore()
        connection = MagicMock()
        store.connect = MagicMock()
        store.connect.return_value.__enter__.return_value = connection
        connection.execute.return_value.fetchall.side_effect = [
            [{'job_id': 'fixture'}],
            [{'job_id': 'fixture', 'status': 'done', 'data_quality': 'actual', 'is_mock': 0,
              'created_at': '2026-10-23T01:00:10Z', 'source_at': '2026-10-23T01:00:00Z'}],
            [{'job_id': 'fixture', 'status': 'delivered', 'audience': 'account', 'channel': 'accountNotification',
              'completed_at': '2026-10-23T01:00:40Z', 'metadata': '{"receiptVerified":true}'}]]
        result = store.price_observation_metrics(500)
        calls = connection.execute.call_args_list
        self.assertNotIn('JSON', calls[0].args[0])
        self.assertEqual((100,), calls[0].args[1])
        self.assertIn('WHERE job_id IN', calls[1].args[0])
        self.assertEqual(('fixture',), calls[1].args[1])
        self.assertIn("audience='account' AND channel='accountNotification'", calls[2].args[0])
        self.assertEqual(1, result['verifiedDeliveredJobs'])

    def assert_guard_exclusion_counts_cover_all_candidates_and_account_scope(self):
        from digital_twin.modules.market_data.domain.price_observation_diagnostics import record_candidates
        recorder = object.__new__(MySQLMonitoringCycleRecorder)
        recorder.runtime_settings = {}
        events = [market_event() for _ in range(25)]
        accepted, result = recorder.guard_market_observation_delivery_with_connection(
            SentRowConnection(), events, {'default': state_with_outbox_anchor(1_729_000)})
        self.assertEqual([], accepted)
        self.assertEqual(20, len(result['suppressions']))
        self.assertEqual(25, result['exclusionCountsByAccount']['default']['stale-outbox-anchor'])
        state = {'accountId': 'default', 'generatedAt': '2026-10-23T01:00:00Z', 'metadata': {
            'marketPriceDiagnostics': {'evaluationCounts': {}, 'candidateCounts': {}, 'exclusionCounts': {}}}}
        saved = record_candidates(state, events, [], result['exclusionCountsByAccount'])
        counters = saved['metadata']['marketPriceDiagnostics']
        self.assertEqual(25, counters['candidateCounts']['candidates'])
        self.assertEqual({'stale-outbox-anchor': 25}, counters['exclusionCounts'])
        self.assertEqual(saved, record_candidates(saved, events, [], result['exclusionCountsByAccount']))

    def test_concurrent_candidate_with_old_outbox_anchor_is_suppressed(self):
        with self.subTest(delivery_diagnostics="guard_exclusion_counts_cover_all_candidates_and_account_scope"):
            self.assert_guard_exclusion_counts_cover_all_candidates_and_account_scope()
        event = market_event(anchor=1_676_500, current=1_728_000)

        result = market_observation_delivery_admission(
            event,
            state_with_outbox_anchor(1_729_000),
        )

        self.assertFalse(result["accepted"])
        self.assertEqual("stale-outbox-anchor", result["reasonCode"])
        self.assertEqual(1_729_000, result["authoritativeAnchorPrice"])

    def test_new_material_move_from_current_outbox_anchor_is_accepted(self):
        with self.subTest(delivery_diagnostics="price_latency_counts_only_actual_verified_account_receipts"):
            self.assert_price_latency_counts_only_actual_verified_account_receipts()
        with self.subTest(delivery_diagnostics="price_metrics_bounds_ids_before_reading_json"):
            self.assert_price_metrics_bounds_ids_before_reading_json()
        event = market_event(anchor=1_729_000, current=1_781_000)

        result = market_observation_delivery_admission(
            event,
            state_with_outbox_anchor(1_729_000),
        )

        self.assertTrue(result["accepted"])
        self.assertEqual("current-outbox-anchor", result["reasonCode"])
        self.assertGreater(result["authoritativeChangePct"], 3)

    def test_raw_quote_cadence_remains_active_when_general_cooldown_is_disabled(self):
        recorder = object.__new__(MySQLMonitoringCycleRecorder)
        recorder.runtime_settings = {
            "notificationCooldownEnabled": "0",
            "marketObservationImmediateCadenceMinutes": "10",
        }
        event = market_event(anchor=1_676_500, current=1_729_000)
        connection = SentRowConnection({
            "sent_key": event.cadence_key(),
            "sent_at": "2026-09-06T23:11:50Z",
        })

        accepted, details = recorder.guard_market_observation_delivery_with_connection(
            connection,
            [event],
            {"default": state_with_outbox_anchor(1_676_500)},
            now=datetime(2026, 9, 6, 23, 12, 9, tzinfo=timezone.utc),
        )

        self.assertEqual([], accepted)
        self.assertEqual(1, details["suppressedCount"])
        self.assertEqual("market-observation-cadence", details["suppressions"][0]["reasonCode"])
        self.assertIn("FOR UPDATE", connection.queries[0][0])

        worker_spec = {
            "label": "Python realtime monitor",
            "command": ["python3", "-u", "python_service/service.py", "monitor", "watch"],
            "needle": "python_service/service.py monitor watch",
        }
        with patch.object(service_manager, "pid_exists", return_value=True), patch.object(
            service_manager,
            "command_for_pid",
            return_value="python3 -u python_service/service.py monitor watch",
        ), patch.object(
            service_manager,
            "process_working_directory",
            return_value=str(Path(service_manager.ROOT_DIR).resolve()),
        ), patch.object(service_manager.os, "getpid", return_value=999):
            self.assertTrue(service_manager.is_owned_project_worker_process(123, worker_spec))
            worker_spec["command"][-2:] = ["market-data", "watch"]
            self.assertFalse(service_manager.is_owned_project_worker_process(123, worker_spec))

        with TemporaryDirectory() as temp:
            stop_spec = {
                "label": "Python realtime monitor",
                "pid": Path(temp) / "monitor.pid",
                "log": Path(temp) / "monitor.log",
                "command": ["python3", "-u", "python_service/service.py", "monitor", "watch"],
                "needle": "python_service/service.py monitor watch",
            }
            cleanup = {"found": [123], "stopped": [123], "remaining": []}
            with patch.object(service_manager, "read_pid", return_value=0), patch.object(
                service_manager,
                "reconcile_owned_project_worker_duplicates",
                return_value=cleanup,
            ) as reconcile:
                self.assertEqual(0, service_manager.stop_worker(stop_spec))
            reconcile.assert_called_once_with(stop_spec, keep_pid=0)


if __name__ == "__main__":
    unittest.main()
