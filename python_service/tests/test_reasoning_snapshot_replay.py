import unittest
from datetime import datetime, timezone

from digital_twin.application.monitoring_service import MonitorRunner
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.ontology_projection_audit import projection_source_snapshot
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position, account_snapshot_from_monitor_state
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
        )

        runner.run_once(symbol_filter=["AAPL"], reasoning_context={"sourceObservedAt": "2026-07-29T00:01:00Z"})

        self.assertEqual("2026-07-29T00:01:00Z", contexts[0]["sourceObservedAt"])
        self.assertTrue(recorder.source_snapshot_replay)


if __name__ == "__main__":
    unittest.main()
