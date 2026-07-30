import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from digital_twin.application.ontology_reasoning_service import OntologyReasoningRunner


class EmptyEventReader:
    def events(self, name="", limit=0):
        return []


class EventReader:
    def __init__(self, events):
        self.events_to_return = list(events or [])

    def recent_events(self, **_kwargs):
        return list(self.events_to_return)


class CursorStore:
    def __init__(self, payload=None):
        self.payload = dict(payload or {})

    def load(self):
        return dict(self.payload)

    def save(self, payload):
        self.payload = dict(payload or {})

    def processed_event_ids(self):
        return []

    def mark_processed(self, _event_ids):
        return None


class OntologyProjectionStabilityTests(unittest.TestCase):
    def runner(
        self,
        cursor=None,
        maintenance_runner=None,
        priority_symbols_provider=None,
        projection_coordinator_probe=None,
        projection_lease_recovery=None,
        projection_coordinator_lease_recovery=None,
    ):
        return OntologyReasoningRunner(
            event_reader=EmptyEventReader(),
            cursor_store=cursor or CursorStore(),
            monitor_runner_factory=lambda: None,
            settings={
                "ontologyReasoningMinIntervalSeconds": "180",
                "ontologyReasoningMaxSymbolsPerRun": "1",
                "ontologyReasoningBackpressureEnabled": "1",
                "ontologyReasoningBackpressureFactor": "1.15",
                "ontologyReasoningBackpressureMaxSeconds": "900",
                "ontologyReasoningMaintenanceEnabled": "1",
                "ontologyReasoningMaintenanceIntervalSeconds": "900",
            },
            maintenance_runner=maintenance_runner,
            priority_symbols_provider=priority_symbols_provider,
            projection_coordinator_probe=projection_coordinator_probe,
            projection_lease_recovery=projection_lease_recovery,
            projection_coordinator_lease_recovery=projection_coordinator_lease_recovery,
            now_provider=lambda: datetime(2026, 7, 22, tzinfo=timezone.utc),
        )

    def test_slow_projection_extends_only_nonurgent_coalescing_interval(self):
        cursor = CursorStore({"lastProjectionRuntime": {"durationMs": 300000}})
        runner = self.runner(cursor)

        self.assertEqual(345, runner.effective_projection_min_interval_seconds([], cursor.load()))

    def test_overdue_target_has_no_artificial_projection_recovery_wait_by_default(self):
        cursor = CursorStore({
            "lastSuccessfulProjectionAt": "2026-07-21T23:59:30Z",
            "lastReasonedAtBySymbol": {"MSFT": "2026-07-21T23:30:00Z"},
        })
        runner = self.runner(cursor)
        runner.settings["ontologyReasoningFairnessMaxWaitSeconds"] = "900"
        event = SimpleNamespace(
            event_id="overdue-msft",
            occurred_at="2026-07-21T23:59:00Z",
            payload={"changedCount": 1, "trigger": "market-data-update", "symbols": ["MSFT"]},
        )

        drain = runner.fairness_drain_state(["MSFT"], cursor.load())

        self.assertTrue(drain["active"])
        self.assertEqual(["MSFT"], drain["symbols"])
        self.assertEqual(0, runner.effective_projection_min_interval_seconds([event], cursor.load(), ["MSFT"]))
        self.assertTrue(runner.projection_due([event], cursor.load(), ["MSFT"]))

    def test_normal_target_keeps_global_projection_cooldown(self):
        cursor = CursorStore({
            "lastSuccessfulProjectionAt": "2026-07-21T23:59:00Z",
            "lastReasonedAtBySymbol": {"MSFT": "2026-07-21T23:58:00Z"},
        })
        runner = self.runner(cursor)
        runner.settings["ontologyReasoningFairnessMaxWaitSeconds"] = "900"
        event = SimpleNamespace(
            event_id="fresh-msft",
            occurred_at="2026-07-21T23:59:00Z",
            payload={"changedCount": 1, "trigger": "market-data-update", "symbols": ["MSFT"]},
        )

        self.assertFalse(runner.fairness_drain_state(["MSFT"], cursor.load())["active"])
        self.assertEqual(120, runner.effective_projection_min_interval_seconds([event], cursor.load(), ["MSFT"]))
        self.assertFalse(runner.projection_due([event], cursor.load(), ["MSFT"]))

    def test_verified_actionable_snapshot_fast_drain_has_no_artificial_cooldown(self):
        cursor = CursorStore({
            "lastSuccessfulProjectionAt": "2026-07-21T23:59:30Z",
        })
        runner = self.runner(cursor)
        runner.settings["ontologyReasoningVerifiedSnapshotFastDrainEnabled"] = "1"
        event = SimpleNamespace(
            event_id="verified-msft",
            occurred_at="2026-07-21T23:59:00Z",
            payload={
                "changedCount": 1,
                "trigger": "verified-monitor-snapshot",
                "symbols": ["MSFT"],
                "verifiedSourceSnapshot": {"generatedAt": "2026-07-21T23:59:00Z"},
                "materialityAssessments": [{"subject": "MSFT", "reviewLevel": "act"}],
            },
        )

        self.assertEqual(0, runner.event_min_interval_seconds(event))
        self.assertEqual(0, runner.effective_projection_min_interval_seconds([event], cursor.load(), ["MSFT"]))
        self.assertTrue(runner.projection_due([event], cursor.load(), ["MSFT"]))

    def test_old_backlog_removes_inter_generation_cooldown_without_reclassifying_the_event(self):
        cursor = CursorStore({
            "lastSuccessfulProjectionAt": "2026-07-21T23:59:30Z",
            "lastReasonedAtBySymbol": {"MSFT": "2026-07-21T23:59:30Z"},
        })
        runner = self.runner(cursor)
        runner.settings["ontologyReasoningBacklogDrainNoCooldownEnabled"] = "1"
        runner.settings["ontologyReasoningBacklogDrainNoCooldownAgeSeconds"] = "120"
        event = SimpleNamespace(
            event_id="old-market-update",
            occurred_at="2026-07-21T23:50:00Z",
            payload={"changedCount": 1, "trigger": "market-data-update", "symbols": ["MSFT"]},
        )

        self.assertEqual(120, runner.event_min_interval_seconds(event))
        self.assertTrue(runner.event_symbol_due(event, "MSFT", cursor.load()))
        self.assertEqual(0, runner.effective_projection_min_interval_seconds([event], cursor.load(), ["MSFT"]))
        self.assertTrue(runner.projection_due([event], cursor.load(), ["MSFT"]))

    def test_idle_runner_executes_deferred_maintenance_outside_live_projection(self):
        calls = []
        runner = self.runner(maintenance_runner=lambda: calls.append("maintenance") or {"status": "ok"})

        result = runner.run_once()

        self.assertEqual("idle", result["status"])
        self.assertEqual("ok", result["maintenance"]["status"])
        self.assertEqual(["maintenance"], calls)

    def test_verified_live_projection_runs_bounded_maintenance_without_waiting_for_idle(self):
        calls = []

        class Monitor:
            accounts = []

            def run_once(self, force=False, symbol_filter=None):
                self.symbol_filter = list(symbol_filter or [])
                return []

        cursor = CursorStore()
        monitor = Monitor()
        event = SimpleNamespace(
            event_id="live-projection",
            occurred_at="2026-07-22T00:00:00Z",
            payload={
                "changedCount": 1,
                "symbols": ["AAPL"],
                "trigger": "market-data-update",
                "factTypes": ["MarketQuote"],
            },
        )
        runner = OntologyReasoningRunner(
            event_reader=EventReader([event]),
            cursor_store=cursor,
            monitor_runner_factory=lambda: monitor,
            settings={
                "ontologyReasoningEnabled": "1",
                "ontologyReasoningMinIntervalSeconds": "0",
                "ontologyReasoningMaintenanceEnabled": "1",
                "ontologyReasoningMaintenanceIntervalSeconds": "60",
                "ontologyRuleCandidateAiEnabled": "0",
            },
            maintenance_runner=lambda: calls.append("maintenance") or {"status": "ok"},
            now_provider=lambda: datetime(2026, 7, 22, tzinfo=timezone.utc),
        )

        result = runner.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual(["AAPL"], monitor.symbol_filter)
        self.assertEqual("ok", result["maintenance"]["status"])
        self.assertEqual(["maintenance"], calls)

    def test_live_projection_defers_maintenance_while_native_targets_remain_queued(self):
        calls = []

        class Monitor:
            accounts = []

            def run_once(self, force=False, symbol_filter=None):
                self.symbol_filter = list(symbol_filter or [])
                return []

        cursor = CursorStore()
        monitor = Monitor()
        event = SimpleNamespace(
            event_id="queued-live-projection",
            occurred_at="2026-07-22T00:00:00Z",
            payload={
                "changedCount": 2,
                "symbols": ["AAPL", "MSFT"],
                "trigger": "market-data-update",
                "factTypes": ["MarketQuote"],
            },
        )
        runner = OntologyReasoningRunner(
            event_reader=EventReader([event]),
            cursor_store=cursor,
            monitor_runner_factory=lambda: monitor,
            settings={
                "ontologyReasoningEnabled": "1",
                "ontologyReasoningMaxSymbolsPerRun": "1",
                "ontologyReasoningMaintenanceEnabled": "1",
                "ontologyReasoningMaintenanceIntervalSeconds": "60",
                "ontologyRuleCandidateAiEnabled": "0",
            },
            maintenance_runner=lambda: calls.append("maintenance") or {"status": "ok"},
            now_provider=lambda: datetime(2026, 7, 22, tzinfo=timezone.utc),
        )

        result = runner.run_once(force=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual(["AAPL"], monitor.symbol_filter)
        self.assertEqual(1, result["omittedSymbolCount"])
        self.assertEqual("deferred", result["maintenance"]["status"])
        self.assertEqual([], calls)
        self.assertGreaterEqual(result["stageTiming"]["monitorAndProjectionMs"], 0)
        self.assertGreaterEqual(result["stageTiming"]["postProjectionMs"], 0)

    def test_execution_timeout_guard_blocks_a_new_projection_until_backoff_expires(self):
        cursor = CursorStore()
        runner = self.runner(cursor)

        recorded = runner.record_execution_timeout(240, started_at="2026-07-22T00:00:00Z")

        self.assertEqual("timeout", recorded["status"])
        self.assertEqual(300, recorded["retryAfterSeconds"])
        self.assertEqual(300, runner.execution_timeout_guard_remaining_seconds(cursor.load()))
        self.assertEqual("open", runner.execution_timeout_guard_state(cursor.load())["status"])

    def test_timeout_keeps_server_safe_backoff_after_local_lease_recovery(self):
        cursor = CursorStore()
        calls = []
        runner = self.runner(
            cursor,
            projection_lease_recovery=lambda: calls.append(True) or {
                "status": "cleared",
                "clearedCount": 2,
                "clearedWorldIds": ["portfolio:local:default", "system:typedb-projection-coordinator"],
                "worldCount": 2,
            },
        )
        recorded = runner.record_execution_timeout(240, started_at="2026-07-22T00:00:00Z")

        self.assertEqual(300, recorded["retryAfterSeconds"])
        self.assertEqual("cleared", recorded["typedbDeadLeaseRecovery"]["status"])
        self.assertEqual(2, recorded["executionTelemetry"]["typedbDeadLeaseRecovery"]["clearedCount"])
        self.assertTrue(recorded["executionTelemetry"]["timeoutRetryPolicy"]["deadLeaseRecovered"])
        self.assertEqual([True], calls)

    def test_parent_timeout_guard_defers_without_a_typedb_coordinator_probe(self):
        cursor = CursorStore({
            "executionTimeoutGuard": {
                "status": "open",
                "retryAfterAt": "2026-07-22T00:05:00Z",
                "reason": "native inference timed out",
            },
        })
        runner = self.runner(
            cursor,
            projection_coordinator_probe=lambda: (_ for _ in ()).throw(
                AssertionError("timeout guard must not query TypeDB")
            ),
        )

        result = runner.isolated_timeout_guard_preflight()

        self.assertFalse(result["ready"])
        self.assertEqual("deferred-timeout-guard", result["status"])
        self.assertEqual(300, result["retryAfterSeconds"])

    def test_isolated_preflight_defers_while_a_live_typedb_writer_holds_the_coordinator(self):
        runner = self.runner(
            projection_coordinator_probe=lambda: {
                "status": "held",
                "leaseOwner": "scoped-abox:active",
                "leaseRemainingSeconds": 240,
            },
            projection_lease_recovery=lambda: (_ for _ in ()).throw(
                AssertionError("preflight must not inventory every account-world lease")
            ),
            projection_coordinator_lease_recovery=lambda: {
                "status": "skipped",
            },
        )
        runner.lightweight_queue_state = lambda: {
            "effectivePendingCount": 1,
            "mailboxPendingEntryCount": 1,
        }

        result = runner.isolated_execution_preflight()

        self.assertFalse(result["ready"])
        self.assertEqual("deferred-projection-coordinator", result["status"])
        self.assertEqual(30, result["retryAfterSeconds"])

    def test_elapsed_execution_timeout_guard_is_non_blocking_but_not_false_success(self):
        cursor = CursorStore({
            "executionTimeoutGuard": {
                "status": "open",
                "consecutiveTimeouts": 1,
                "lastTimeoutAt": "2026-07-21T23:00:00Z",
                "retryAfterAt": "2026-07-21T23:05:00Z",
                "retryAfterSeconds": 300,
            },
        })
        runner = self.runner(cursor)

        status = runner.status()
        result = runner.run_once()

        self.assertEqual("cooldown-elapsed", status["executionTimeoutGuard"]["status"])
        self.assertFalse(status["executionTimeoutGuard"]["blocking"])
        self.assertTrue(status["executionTimeoutGuardCooldownElapsed"])
        self.assertEqual("idle", result["status"])
        self.assertEqual("cooldown-elapsed", cursor.payload["executionTimeoutGuard"]["status"])
        self.assertIn("lastCooldownElapsedAt", cursor.payload["executionTimeoutGuard"])

    def test_subjectless_global_event_is_reconciled_one_live_symbol_at_a_time(self):
        cursor = CursorStore()
        runner = self.runner(
            cursor,
            priority_symbols_provider=lambda: {
                "holdingSymbols": ["005930"],
                "watchlistSymbols": ["000660"],
            },
        )
        event = SimpleNamespace(
            event_id="global-market-update",
            occurred_at="2026-07-22T00:00:00Z",
            payload={"changedCount": 1, "trigger": "market-data-update", "symbols": []},
        )

        first_batches, first_symbols, _omitted = runner.request_symbol_batches([event])
        self.assertEqual(["005930"], first_symbols)
        first_progress = runner.mark_requests_processed([event], first_batches)
        self.assertEqual(["global-market-update"], first_progress["partialEventIds"])

        second_batches, second_symbols, _omitted = runner.request_symbol_batches([event])
        self.assertEqual(["000660"], second_symbols)
        second_progress = runner.mark_requests_processed([event], second_batches)
        self.assertEqual(["global-market-update"], second_progress["completedEventIds"])


if __name__ == "__main__":
    unittest.main()
