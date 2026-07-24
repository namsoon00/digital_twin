import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from digital_twin.application.ontology_reasoning_service import OntologyReasoningRunner


class EmptyEventReader:
    def events(self, name="", limit=0):
        return []


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
    def runner(self, cursor=None, maintenance_runner=None, priority_symbols_provider=None):
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
            now_provider=lambda: datetime(2026, 7, 22, tzinfo=timezone.utc),
        )

    def test_slow_projection_extends_only_nonurgent_coalescing_interval(self):
        cursor = CursorStore({"lastProjectionRuntime": {"durationMs": 300000}})
        runner = self.runner(cursor)

        self.assertEqual(345, runner.effective_projection_min_interval_seconds([], cursor.load()))

    def test_overdue_target_drains_without_waiting_for_global_projection_cooldown(self):
        cursor = CursorStore({
            "lastSuccessfulProjectionAt": "2026-07-21T23:59:00Z",
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
        self.assertEqual(180, runner.effective_projection_min_interval_seconds([event], cursor.load(), ["MSFT"]))
        self.assertFalse(runner.projection_due([event], cursor.load(), ["MSFT"]))

    def test_idle_runner_executes_deferred_maintenance_outside_live_projection(self):
        calls = []
        runner = self.runner(maintenance_runner=lambda: calls.append("maintenance") or {"status": "ok"})

        result = runner.run_once()

        self.assertEqual("idle", result["status"])
        self.assertEqual("ok", result["maintenance"]["status"])
        self.assertEqual(["maintenance"], calls)

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
