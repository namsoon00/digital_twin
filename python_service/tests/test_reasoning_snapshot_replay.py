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
from digital_twin.infrastructure.service_factory import (
    ActiveDeploymentWorldProjectionSink,
    FrozenReasoningSnapshotSource,
    active_versioned_reasoning_queue_state,
)


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
    def test_background_workers_ignore_rollback_candidate_backlog(self):
        class Registry:
            def control(self):
                return SimpleNamespace(
                    active_deployment_id="v2-active",
                    delivery_deployment_id="v2-active",
                    candidate_deployment_id="v2-rollback",
                )

            def get(self, _deployment_id):
                return {"engineVersion": "v2", "status": "active"}

        class Jobs:
            def live_queue_state(self, deployment_id):
                return {
                    "deploymentId": deployment_id,
                    "effectivePendingCount": 9 if deployment_id == "v2-rollback" else 0,
                    "processingCount": 0,
                    "queuedCount": 0,
                }

        state = active_versioned_reasoning_queue_state(
            Registry(), Jobs(), configured_v2_deployment_id="v2-active"
        )

        self.assertEqual(["v2-active"], state["deploymentIds"])
        self.assertEqual(0, state["effectivePendingCount"])

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

    def test_v2_request_without_an_exact_source_boundary_is_rejected(self):
        source = LatestMonitorSnapshotReasoningSource(SnapshotStore(monitor_state()))

        result = source.preflight([account()], {
            "reasoningEngineDeploymentId": "ontology-v2-production-r4",
            "sourceObservedAt": "2026-07-29T00:02:00Z",
        })

        self.assertFalse(result["ready"])
        self.assertTrue(result["permanent"])
        self.assertEqual("rejected-source-snapshot", result["status"])

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
