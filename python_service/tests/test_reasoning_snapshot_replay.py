import unittest
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from digital_twin.application.monitoring_service import MonitorRunner
from digital_twin.domain.accounts import AccountConfig
from digital_twin.domain.ontology_projection_audit import projection_source_snapshot
from digital_twin.domain.monitoring import RealtimeMonitor
from digital_twin.domain.portfolio import AlertEvent, AccountSnapshot, PortfolioSummary, Position, account_snapshot_from_monitor_state
from digital_twin.domain.repositories import MonitoringCycleRecordResult
from digital_twin.domain.reasoning_source_snapshot import build_reasoning_source_snapshot
from digital_twin.infrastructure.mysql_monitoring_stores import MySQLMonitoringCycleRecorder
from digital_twin.infrastructure.ontology_projection import PortfolioOntologyProjectionRecorder
from digital_twin.infrastructure.reasoning_snapshot_source import LatestMonitorSnapshotReasoningSource
from digital_twin.infrastructure.service_factory import FrozenReasoningSnapshotSource


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
    def test_cycle_recorder_does_not_rewrite_time_series_for_source_replay(self):
        recorder = object.__new__(MySQLMonitoringCycleRecorder)
        recorder.runtime_settings = {}
        recorder.monitor_store = SimpleNamespace(previous={}, sent={})
        recorder.market_time_series_store = object()
        recorder.market_observation_anchor_store = SimpleNamespace()
        recorder.notification_ingress = SimpleNamespace()
        recorder.transaction = lambda: nullcontext(object())

        with patch(
            "digital_twin.infrastructure.mysql_monitoring_stores.MySQLNotificationJobStore"
        ), patch(
            "digital_twin.infrastructure.mysql_monitoring_stores.MySQLModelReviewJobStore"
        ), patch(
            "digital_twin.infrastructure.mysql_monitoring_stores.insert_domain_event_with_connection"
        ):
            result = recorder.record_cycle(
                ["acct"],
                [SimpleNamespace(account_id="acct")],
                [],
                source_snapshot_replay=True,
            )

        self.assertEqual("queued=0", result.reason)

    def test_reasoning_source_packet_is_deterministic_and_detached_from_live_state(self):
        state = monitor_state()
        first = build_reasoning_source_snapshot("acct", state)
        second = build_reasoning_source_snapshot("acct", state)

        self.assertEqual(first["snapshotId"], second["snapshotId"])
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertTrue(first["payload"]["metadata"]["reasoningSnapshotReplay"]["immutableInput"])
        first["payload"]["positions"]["AAPL"]["current_price"] = 1.0
        self.assertEqual(100.0, state["positions"]["AAPL"]["current_price"])

    def test_source_prefers_the_named_immutable_packet_over_a_newer_snapshot(self):
        frozen = build_reasoning_source_snapshot("acct", monitor_state("2026-07-29T00:02:00Z"))

        class PacketStore(SnapshotStore):
            def reasoning_source_snapshot_metadata(self, snapshot_id):
                self.assert_snapshot_id = snapshot_id
                return {
                    "snapshotId": snapshot_id,
                    "accountId": "acct",
                    "mode": "live",
                    "status": "ok",
                    "generatedAt": "2026-07-29T00:02:00Z",
                }

            def reasoning_source_snapshot_state(self, snapshot_id, target_symbols=None):
                self.assert_snapshot_id = snapshot_id
                self.target_symbols = list(target_symbols or [])
                return deepcopy(frozen["payload"])

        store = PacketStore(monitor_state("2026-07-29T00:09:00Z"))
        source = LatestMonitorSnapshotReasoningSource(
            store,
            now_provider=lambda: datetime(2026, 7, 29, 0, 3, tzinfo=timezone.utc),
        )
        context = {
            "sourceObservedAt": "2026-07-29T00:02:00Z",
            "targetSymbols": ["AAPL"],
            "verifiedSourceSnapshot": {
                "snapshotId": frozen["snapshotId"],
                "accountId": "acct",
                "generatedAt": "2026-07-29T00:02:00Z",
            },
        }

        self.assertTrue(source.preflight([account()], context)["ready"])
        snapshot = source(account(), context)

        self.assertEqual("2026-07-29T00:02:00Z", snapshot.generated_at)
        self.assertEqual(["AAPL"], store.target_symbols)

    def test_missing_named_packet_is_permanently_rejected(self):
        class MissingPacketStore(SnapshotStore):
            def reasoning_source_snapshot_metadata(self, _snapshot_id):
                return {}

            def reasoning_source_snapshot_state(self, _snapshot_id, target_symbols=None):
                return {}

        source = LatestMonitorSnapshotReasoningSource(MissingPacketStore(monitor_state()))
        result = source.preflight([account()], {
            "verifiedSourceSnapshot": {
                "snapshotId": "reasoning-source:missing",
                "accountId": "acct",
                "generatedAt": "2026-07-29T00:02:00Z",
            },
        })

        self.assertFalse(result["ready"])
        self.assertTrue(result["permanent"])
        self.assertEqual("rejected-source-snapshot", result["status"])

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

    def test_source_reads_the_exact_verified_history_boundary(self):
        class PointInTimeStore(SnapshotStore):
            def __init__(self):
                super().__init__(monitor_state("2026-07-29T00:05:00Z"))
                self.requested = []

            def reasoning_snapshot_metadata_at(self, account_id, generated_at):
                self.requested.append((account_id, generated_at, "metadata"))
                return {
                    "accountId": account_id,
                    "mode": "live",
                    "status": "ok",
                    "generatedAt": generated_at,
                }

            def reasoning_snapshot_state_at(self, account_id, generated_at, target_symbols=None):
                self.requested.append((account_id, generated_at, tuple(target_symbols or [])))
                return monitor_state(generated_at)

        store = PointInTimeStore()
        source = LatestMonitorSnapshotReasoningSource(
            store,
            now_provider=lambda: datetime(2026, 7, 29, 0, 6, tzinfo=timezone.utc),
        )
        context = {
            "sourceObservedAt": "2026-07-29T00:02:00Z",
            "targetSymbols": ["AAPL"],
            "verifiedSourceSnapshots": [{
                "accountId": "acct",
                "generatedAt": "2026-07-29T00:02:00Z",
            }],
        }

        self.assertTrue(source.preflight([account()], context)["ready"])
        snapshot = source(account(), context)

        self.assertEqual("2026-07-29T00:02:00Z", snapshot.generated_at)
        self.assertIn(("acct", "2026-07-29T00:02:00Z", ("AAPL",)), store.requested)

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

    def test_immutable_shadow_input_is_not_compacted_a_second_time(self):
        state = monitor_state()
        state["metadata"] = {
            "ontology": {"previousStateAvailable": True},
            "monitorStateHistory": [
                {"generatedAt": "2026-07-28T%02d:00:00Z" % hour}
                for hour in range(12)
            ],
            "reasoningSnapshotReplay": {
                "status": "ready",
                "mode": "immutable-shadow-input",
                "immutableInput": True,
                "snapshotGeneratedAt": state["generatedAt"],
            },
        }
        immutable_state = deepcopy(state)
        runner = MonitorRunner(
            [account()],
            store=SnapshotStore(monitor_state("2026-07-29T00:00:00Z")),
            monitor=EmptyMonitor(),
            snapshot_builder=lambda _account, reasoning_context=None: account_snapshot_from_monitor_state(
                deepcopy(immutable_state)
            ),
            event_sender=lambda *_args, **_kwargs: None,
            cycle_recorder=CapturingCycleRecorder(),
            ontology_projection_enabled=False,
            source_snapshot_replay=True,
        )

        runner.run_once(symbol_filter=["AAPL"])

        replayed = runner.last_reasoning_source_states["acct"]
        self.assertEqual(
            12,
            len((replayed.get("metadata") or {}).get("monitorStateHistory") or []),
        )
        self.assertTrue(
            ((replayed.get("metadata") or {}).get("reasoningSnapshotReplay") or {}).get(
                "immutableInput"
            )
        )

    def test_shadow_source_marks_the_v1_packet_as_immutable(self):
        source = FrozenReasoningSnapshotSource({"acct": monitor_state()})

        snapshot = source(account())

        replay = snapshot.metadata["reasoningSnapshotReplay"]
        self.assertEqual("immutable-shadow-input", replay["mode"])
        self.assertTrue(replay["immutableInput"])
        self.assertEqual(snapshot.generated_at, replay["snapshotGeneratedAt"])

    def test_shadow_projection_replays_v1_input_symbols_not_expanded_output_symbols(self):
        captured = []

        class ProjectionRecorder:
            def __init__(self):
                self.last_runtime_contexts = {}

            def record_snapshot(self, snapshot, target_symbols=None, **_kwargs):
                captured.append(list(target_symbols or []))
                self.last_runtime_contexts[snapshot.account_id] = {
                    "investorFlows": {"AAPL": {"foreignNetVolume": 10}},
                }
                snapshot.metadata.setdefault("ontology", {})["projection"] = {
                    "saved": True,
                    "status": "ok",
                }

        runner = MonitorRunner(
            [account()],
            store=SnapshotStore(monitor_state()),
            monitor=EmptyMonitor(),
            snapshot_builder=lambda _account: account_snapshot_from_monitor_state(
                monitor_state()
            ),
            event_sender=lambda *_args, **_kwargs: None,
            ontology_projection_recorder=ProjectionRecorder(),
            ontology_projection_enabled=True,
            projection_symbol_filters_by_account={"acct": ["AAPL"]},
        )

        runner.run_once(dry_run=True, symbol_filter=["MSFT"])

        self.assertEqual([["AAPL"]], captured)
        self.assertEqual(
            10,
            runner.last_projection_runtime_contexts["acct"]["investorFlows"]["AAPL"][
                "foreignNetVolume"
            ],
        )

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
