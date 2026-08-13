import unittest
from datetime import datetime, timezone

from digital_twin.application.monitoring_service import MonitorRunner
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.ontology_projection_audit import projection_source_snapshot
from digital_twin.domain.monitoring import RealtimeMonitor
from digital_twin.domain.portfolio import AlertEvent, AccountSnapshot, PortfolioSummary, Position, account_snapshot_from_monitor_state
from digital_twin.domain.repositories import MonitoringCycleRecordResult
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
from digital_twin.infrastructure.reasoning_snapshot_source import LatestMonitorSnapshotReasoningSource


class SnapshotStore:
    def __init__(self, state):
        self.previous = {"acct": state}
        self.sent = {}


class EmptyMonitor:
    def events_for_snapshot(self, _snapshot, _previous):
        return []

    def apply_cadence(self, events, _store, force=False):
        return list(events or [])


class CapturingCycleRecorder:
    def __init__(self):
        self.source_snapshot_replay = None
        self.snapshots = []

    def record_cycle(
        self,
        _account_ids,
        _snapshots,
        _events,
        dry_run=False,
        delivery_guard=None,
        source_snapshot_replay=False,
    ):
        self.source_snapshot_replay = source_snapshot_replay
        self.snapshots = list(_snapshots or [])
        return MonitoringCycleRecordResult(False, 0, "ok")


def account():
    return AccountConfig(
        account_id="acct",
        label="Test",
        provider="toss",
        base_url="",
        client_id="",
        client_secret="",
        account_seq="",
        watchlist_symbols=["AAPL"],
    )


def monitor_state(generated_at="2026-07-29T00:02:00Z"):
    return {
        "accountId": "acct",
        "accountLabel": "Test",
        "provider": "toss",
        "mode": "live",
        "status": "ok",
        "generatedAt": generated_at,
        "portfolio": {
            "total": 1000.0,
            "invested": 700.0,
            "cash": 300.0,
            "markets": [],
            "sectors": [],
            "concentration": 70.0,
        },
        "positions": {
            "AAPL": {"symbol": "AAPL", "name": "Apple", "market": "US", "currency": "USD", "quantity": 1.0, "current_price": 100.0},
        },
        "decisions": {},
        "externalSignals": {},
        "watchlist": {},
        "metadata": {},
    }


class ReasoningSnapshotReplayTests(unittest.TestCase):
    def test_portfolio_scope_keeps_symbol_less_insight_for_the_matching_account(self):
        runner = MonitorRunner.__new__(MonitorRunner)
        portfolio_event = AlertEvent("acct", "Test", "warning", "portfolioOntologySignal", "portfolio", "Portfolio", [])
        other_account_event = AlertEvent("other", "Other", "warning", "portfolioOntologySignal", "other", "Other", [])
        unrelated_event = AlertEvent("acct", "Test", "warning", "systemHealth", "system", "System", [])

        filtered = runner.filter_events_by_symbol(
            [portfolio_event, other_account_event, unrelated_event],
            {"AAPL"},
            account_id="acct",
            reasoning_context={
                "subjectKinds": ["PORTFOLIO"],
                "subjectIds": ["portfolio:acct"],
                "accountIds": ["acct"],
            },
        )

        self.assertEqual([portfolio_event], filtered)

    def test_rehydrates_an_independent_domain_snapshot(self):
        state = monitor_state()
        snapshot = account_snapshot_from_monitor_state(state)

        self.assertEqual("AAPL", snapshot.positions[0].symbol)
        snapshot.positions[0].market_signal_coverage["mutated"] = True
        self.assertNotIn("market_signal_coverage", state["positions"]["AAPL"])

    def test_source_uses_verified_snapshot_when_it_covers_requested_revision(self):
        store = SnapshotStore(monitor_state())
        source = LatestMonitorSnapshotReasoningSource(
            store,
            settings={"monitorAccountIntervalSeconds": "180"},
            now_provider=lambda: datetime(2026, 7, 29, 0, 3, tzinfo=timezone.utc),
        )

        snapshot = source(account(), {"sourceObservedAt": "2026-07-29T00:01:00Z"})

        replay = snapshot.metadata["reasoningSnapshotReplay"]
        self.assertTrue(snapshot.has_live_account_data())
        self.assertEqual("ready", replay["status"])
        self.assertEqual("persisted-verified-monitor-snapshot", replay["mode"])
        self.assertEqual({}, store.previous["acct"]["metadata"])
        self.assertNotIn("reasoningSnapshotReplay", projection_source_snapshot(snapshot)["metadata"])
        self.assertNotIn(
            "reasoningSnapshotReplay",
            PortfolioOntologyProjectionRecorder.factual_runtime_metadata(
                {"previousMonitorState": {"metadata": {"reasoningSnapshotReplay": replay}}}
            )["previousMonitorState"]["metadata"],
        )

    def test_source_defers_when_the_saved_snapshot_predates_the_fact_revision(self):
        source = LatestMonitorSnapshotReasoningSource(
            SnapshotStore(monitor_state()),
            now_provider=lambda: datetime(2026, 7, 29, 0, 3, tzinfo=timezone.utc),
        )

        snapshot = source(account(), {"sourceObservedAt": "2026-07-29T00:04:00Z"})

        replay = snapshot.metadata["reasoningSnapshotReplay"]
        self.assertFalse(snapshot.has_live_account_data())
        self.assertEqual("deferred", replay["status"])
        self.assertIn("predates", replay["reason"])

    def test_preflight_reads_only_the_snapshot_boundary_before_replay(self):
        class MetadataStore(SnapshotStore):
            def __init__(self):
                super().__init__({})
                self.metadata_calls = []

            def snapshot_metadata(self, account_id):
                self.metadata_calls.append(account_id)
                return {
                    "accountId": account_id,
                    "mode": "live",
                    "status": "ok",
                    "generatedAt": "2026-07-29T00:02:00Z",
                }

        store = MetadataStore()
        source = LatestMonitorSnapshotReasoningSource(
            store,
            now_provider=lambda: datetime(2026, 7, 29, 0, 3, tzinfo=timezone.utc),
        )

        result = source.preflight([account()], {"sourceObservedAt": "2026-07-29T00:04:00Z"})

        self.assertFalse(result["ready"])
        self.assertEqual("deferred-source-snapshot", result["status"])
        self.assertEqual(["acct"], store.metadata_calls)
        self.assertIn("predates", result["reason"])

    def test_monitor_replay_does_not_rewrite_source_snapshot_rows(self):
        snapshot = AccountSnapshot(
            account_id="acct",
            account_label="Test",
            provider="toss",
            mode="live",
            status="ok",
            generated_at="2026-07-29T00:02:00Z",
            portfolio=PortfolioSummary(1000.0, 700.0, 300.0, [], [], 70.0),
            positions=[Position(symbol="AAPL", name="Apple", market="US", currency="USD", quantity=1.0)],
            metadata={"reasoningSnapshotReplay": {"status": "ready"}},
        )
        store = SnapshotStore(monitor_state())
        recorder = CapturingCycleRecorder()
        contexts = []

        def snapshot_builder(_account, reasoning_context=None):
            contexts.append(dict(reasoning_context or {}))
            return snapshot

        runner = MonitorRunner(
            [account()],
            store=store,
            monitor=EmptyMonitor(),
            snapshot_builder=snapshot_builder,
            event_sender=lambda *_args, **_kwargs: None,
            cycle_recorder=recorder,
            source_snapshot_replay=True,
        )

        runner.run_once(symbol_filter=["AAPL"], reasoning_context={"sourceObservedAt": "2026-07-29T00:01:00Z"})

        self.assertEqual("2026-07-29T00:01:00Z", contexts[0]["sourceObservedAt"])
        self.assertTrue(recorder.source_snapshot_replay)

    def test_monitor_replay_uses_the_persisted_previous_snapshot(self):
        current_state = monitor_state()
        previous_state = monitor_state("2026-07-29T00:00:00Z")
        previous_state["positions"]["AAPL"]["current_price"] = 95.0
        snapshot = account_snapshot_from_monitor_state(current_state)
        snapshot.metadata.update({
            "previousMonitorState": previous_state,
            "reasoningSnapshotReplay": {"status": "ready"},
        })

        class PreviousCapturingMonitor(EmptyMonitor):
            def __init__(self):
                self.previous = None

            def events_for_snapshot(self, _snapshot, previous):
                self.previous = previous
                return []

        monitor = PreviousCapturingMonitor()
        runner = MonitorRunner(
            [account()],
            store=SnapshotStore(current_state),
            monitor=monitor,
            snapshot_builder=lambda _account, reasoning_context=None: snapshot,
            event_sender=lambda *_args, **_kwargs: None,
            cycle_recorder=CapturingCycleRecorder(),
            ontology_projection_enabled=False,
            source_snapshot_replay=True,
        )

        runner.run_once(
            symbol_filter=["AAPL"],
            reasoning_context={"sourceObservedAt": "2026-07-29T00:01:00Z"},
        )

        self.assertEqual(95.0, monitor.previous["positions"]["AAPL"]["current_price"])

    def test_normal_monitor_ignores_a_stale_replay_marker_and_commits_a_source_snapshot(self):
        snapshot = AccountSnapshot(
            account_id="acct",
            account_label="Test",
            provider="toss",
            mode="live",
            status="ok",
            generated_at="2026-07-29T00:02:00Z",
            portfolio=PortfolioSummary(1000.0, 700.0, 300.0, [], [], 70.0),
            positions=[Position(symbol="AAPL", name="Apple", market="US", currency="USD", quantity=1.0)],
            metadata={"reasoningSnapshotReplay": {"status": "ready"}},
        )
        recorder = CapturingCycleRecorder()
        runner = MonitorRunner(
            [account()],
            store=SnapshotStore(monitor_state()),
            monitor=EmptyMonitor(),
            snapshot_builder=lambda _account: snapshot,
            event_sender=lambda *_args, **_kwargs: None,
            cycle_recorder=recorder,
        )

        runner.run_once()

        self.assertFalse(recorder.source_snapshot_replay)
        self.assertNotIn("reasoningSnapshotReplay", recorder.snapshots[0].metadata)

    def test_monitor_forwards_reasoning_context_when_the_monitor_supports_it(self):
        snapshot = account_snapshot_from_monitor_state(monitor_state())
        recorder = CapturingCycleRecorder()

        class ContextMonitor(EmptyMonitor):
            def __init__(self):
                self.reasoning_context = None

            def events_for_snapshot(self, _snapshot, _previous, reasoning_context=None):
                self.reasoning_context = dict(reasoning_context or {})
                return []

        monitor = ContextMonitor()
        runner = MonitorRunner(
            [account()],
            store=SnapshotStore(monitor_state()),
            monitor=monitor,
            snapshot_builder=lambda _account: snapshot,
            event_sender=lambda *_args, **_kwargs: None,
            cycle_recorder=recorder,
        )

        runner.run_once(
            symbol_filter=["AAPL"],
            reasoning_context={"observationFollowupSymbols": ["AAPL"]},
        )

        self.assertEqual(["AAPL"], monitor.reasoning_context["observationFollowupSymbols"])

    def test_normal_monitor_queues_verified_snapshot_without_inline_typedb(self):
        snapshot = account_snapshot_from_monitor_state(monitor_state())
        recorder = CapturingCycleRecorder()
        projected = []

        class ProjectionRecorder:
            def record_snapshot(self, _snapshot, **_kwargs):
                projected.append(True)

        runner = MonitorRunner(
            [account()],
            store=SnapshotStore(monitor_state()),
            monitor=EmptyMonitor(),
            snapshot_builder=lambda _account: snapshot,
            event_sender=lambda *_args, **_kwargs: None,
            cycle_recorder=recorder,
            ontology_projection_recorder=ProjectionRecorder(),
            ontology_projection_enabled=False,
        )

        runner.run_once()

        self.assertEqual([], projected)
        self.assertEqual("queued-verified-monitor-snapshot", recorder.snapshots[0].metadata["ontology"]["projection"]["status"])

        pending_snapshot = recorder.snapshots[0]
        previous = pending_snapshot.to_monitor_state()
        events = RealtimeMonitor().events_for_snapshot(pending_snapshot, previous)
        pending_state = pending_snapshot.metadata["ontology"]["inferenceMissingState"]
        reason_code, reason, detail = RealtimeMonitor().ontology_inference_missing_reason(pending_snapshot)

        self.assertFalse(any(event.rule == "ontologyInferenceMissing" for event in events))
        self.assertFalse(pending_state["missing"])
        self.assertTrue(pending_state["pending"])
        self.assertEqual("queued-verified-monitor-snapshot", pending_state["status"])
        self.assertEqual("", reason_code)
        self.assertEqual("", reason)
        self.assertTrue(detail["pending"])


if __name__ == "__main__":
    unittest.main()
