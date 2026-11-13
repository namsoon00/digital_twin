"""Quote-driven source commits preserve accounts, clocks, rules and delivery."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from stabilization_database import StabilizationDatabaseCase, fixture_snapshot
from digital_twin.infrastructure.transactions import monitoring
from digital_twin.infrastructure.mysql_realtime_workload_guard import MySQLRealtimeWorkloadGuard
from digital_twin.modules.market_data.domain.market_source_refresh import (
    merge_market_position, market_position_from_cache, revalue_market_snapshot,
)
from digital_twin.modules.market_data.application.market_source_refresh_service import MarketSourceRefreshService
from digital_twin.modules.market_data.contracts import RealtimeMonitor
from digital_twin.modules.reasoning.domain.reasoning_source_snapshot import build_reasoning_source_snapshot


def stamp(seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def snapshot(account, at=None):
    result = fixture_snapshot(account, at or stamp(-40))
    position = result.positions[0]
    position.source_as_of = result.generated_at
    position.source_fetched_at = result.generated_at
    position.source_timestamp_state = "provider"
    position.freshness_status = "fresh"
    position.freshness_max_age_minutes = 10
    position.market_session = "regular"
    position.quote_source = "fixture"
    position.quote_status = "live"
    position.data_quality = "actual"
    position.average_price = 80
    position.exchange_rate = 1
    position.broker_source_as_of = result.generated_at
    position.broker_market_value = position.broker_market_value_krw = 1000
    position.account_value_basis = "broker-gross"
    result.portfolio.valuation_basis = "broker-gross"
    result.metadata["accountBasisGeneratedAt"] = result.generated_at
    result.external_signals = {"companyKnowledge": {"AAPL": {"asOf": "2026-09-01", "unchanged": "evidence " * 9000}}}
    return result


class MarketSourceRefreshTests(TestCase):
    def test_quote_merge_cannot_change_holdings_or_renew_other_sources(self):
        base = snapshot("scope").positions[0]
        base.indicator_as_of = stamp(-3600)
        base.ma20 = 90
        base.market_signal_coverage = {"investor": {"sourceAsOf": stamp(-100)}}
        incoming = market_position_from_cache(base, {
            "sourceAsOf": stamp(-2), "currentPrice": 110, "quantity": 999, "averagePrice": 1,
            "sourceFetchedAt": stamp(-1), "indicatorAsOf": stamp(-7200), "ma20": 1,
            "volume": 1234, "tradingValue": 9876, "return1d": 10,
        })
        result = merge_market_position(base, incoming, stamp())
        self.assertEqual((110, 10, 80, 1000), (result.current_price, result.quantity, result.average_price, result.broker_market_value))
        self.assertEqual(base.indicator_as_of, result.indicator_as_of)
        self.assertEqual(base.market_signal_coverage, result.market_signal_coverage)
        self.assertEqual((1234, 9876, 10), (result.volume, result.trading_value, result.return_1d))
        self.assertAlmostEqual((110 / 90 - 1) * 100, result.ma20_distance)
        self.assertEqual(base, merge_market_position(base, replace(incoming, source_as_of=stamp(60)), stamp()))
        self.assertEqual(base, merge_market_position(base, replace(incoming, source_as_of=""), stamp()))
        self.assertEqual(base, merge_market_position(base, replace(incoming, source_as_of=stamp(-400)), stamp()))
        flow = market_position_from_cache(base, {
            "foreignNetVolume": 0, "tradeStrength": 123,
            "marketSignalCoverage": {
                "investor": {"sourceAsOf": stamp(-2), "fields": ["foreignNetVolume"], "status": "available"},
                "ccnl": {"sourceAsOf": stamp(-1), "fields": ["tradeStrength"], "status": "available"},
            },
        })
        base.foreign_net_volume = 99
        flowed = merge_market_position(base, flow, stamp())
        self.assertEqual((0, 123), (flowed.foreign_net_volume, flowed.trade_strength))
        self.assertEqual(base.source_as_of, flowed.source_as_of)
        with self.assertRaisesRegex(ValueError, "subject"):
            merge_market_position(base, replace(incoming, symbol="OTHER"), stamp())

    def test_revaluation_keeps_broker_basis_and_cash_distinct_from_marked_values(self):
        current = snapshot("scope")
        before_clock = current.positions[0].broker_source_as_of
        current.positions[0].current_price = 110
        revalue_market_snapshot(current)
        self.assertEqual(100, current.portfolio.cash)
        self.assertEqual(1100, current.positions[0].mark_to_market_value)
        self.assertEqual(1000, current.positions[0].broker_market_value)
        self.assertEqual(before_clock, current.positions[0].broker_source_as_of)
        self.assertEqual(300, current.positions[0].profit_loss)
        self.assertEqual("broker-gross", current.portfolio.valuation_basis)
        self.assertEqual(1100, current.portfolio.total)
        self.assertEqual(1200, current.portfolio.mark_to_market_total)

    def test_market_source_overlaps_collection_but_yields_to_storage_maintenance(self):
        with TemporaryDirectory() as root:
            guard = MySQLRealtimeWorkloadGuard(Path(root) / "work.lock")
            with guard.monitor_cycle():
                with guard.market_source_turn() as source:
                    self.assertTrue(source.acquired)
                    with guard.maintenance_turn() as cleanup:
                        self.assertFalse(cleanup.acquired)
            with guard.maintenance_turn() as cleanup:
                self.assertTrue(cleanup.acquired)
                with guard.market_source_turn() as source:
                    self.assertFalse(source.acquired)


class MarketSourceRefreshStorageTests(StabilizationDatabaseCase):
    def setUp(self):
        super().setUp()
        self.config = dict(self.settings, marketSourceRefreshEnabled="1")
        self.store = monitoring.MySQLMonitorStore(self.config)
        self.recorder = monitoring.MySQLMonitoringCycleRecorder(self.config, monitor_store=self.store)
        self.base = snapshot(self.deployment)
        self.recorder.record_cycle([self.deployment], [self.base], [])
        self.original = deepcopy(self.store.previous[self.deployment])

    def refresh_service(self, proposal):
        return MarketSourceRefreshService(
            accounts=lambda: [SimpleNamespace(account_id=self.deployment, enabled=True)],
            state_reader=self.store.latest_account_state, quote_reader=Mock(return_value={"AAPL": [proposal]}),
            recorder=self.recorder, monitor=RealtimeMonitor(self.config), state_store=self.store,
            authority=lambda: True, deployment_id=self.deployment, settings=self.config,
            guard=MySQLRealtimeWorkloadGuard(Path(self.temp.name) / "work.lock").market_source_turn,
        )

    def test_quote_commit_replays_exact_evidence_and_restart_does_not_duplicate(self):
        proposal = replace(self.base.positions[0], current_price=110, source_as_of=stamp(-1))
        service = self.refresh_service(proposal)
        result = service.run_once()
        self.assertEqual("committed", result["status"])
        state = self.store.latest_account_state(self.deployment)
        self.assertEqual(self.original["externalSignals"], state["externalSignals"])
        self.assertEqual(self.base.generated_at, state["metadata"]["accountBasisGeneratedAt"])
        self.assertEqual(110, state["positions"]["AAPL"]["current_price"])
        source = build_reasoning_source_snapshot(self.deployment, state, previous_state=self.original, settings=self.config)
        restarted = monitoring.MySQLMonitorStore(self.config)
        actual = restarted.reasoning_source_snapshot_state(source["snapshotId"])
        self.assertEqual(source["payload"], actual)
        self.assertEqual(self.base.generated_at, actual["metadata"]["accountBasisGeneratedAt"])
        self.assertEqual(state["metadata"]["marketSourceRefresh"], actual["metadata"]["marketSourceRefresh"])
        self.assertEqual({}, restarted.reasoning_snapshot_state_at("other-account", state["generatedAt"]))
        self.assertEqual(source["fingerprint"], self.sql("SELECT fingerprint FROM verified_reasoning_source_snapshots WHERE snapshot_id=%s", (source["snapshotId"],))["fingerprint"])
        events = self.sql("SELECT event_id, payload_json FROM domain_events WHERE name='ontology.reasoning_requested' AND occurred_at >= %s", (state["generatedAt"],), all_rows=True)
        self.assertTrue(any(source["snapshotId"] in row["payload_json"] for row in events))
        lineage = self.sql("SELECT source_event_id FROM reasoning_engine_job_sources WHERE deployment_id=%s AND account_id=%s UNION SELECT source_event_id FROM reasoning_engine_jobs WHERE deployment_id=%s", (self.deployment, self.deployment, self.deployment), all_rows=True)
        source_events = {row["event_id"] for row in events if source["snapshotId"] in row["payload_json"]}
        self.assertTrue(source_events & {row["source_event_id"] for row in lineage})
        service.next_check = 0
        self.assertEqual("unchanged", service.run_once()["status"])
        self.assertEqual(state["generatedAt"], self.store.latest_account_state(self.deployment)["generatedAt"])

    def test_stale_account_or_shadow_cannot_renew_a_source(self):
        service = self.refresh_service(replace(self.base.positions[0], current_price=110, source_as_of=stamp(-1)))
        service.authority = lambda: False
        self.assertEqual("not-delivery-owner", service.run_once()["status"])
        service.quote_reader.assert_not_called()
        service.authority = lambda: True
        service.next_check = 0
        state = deepcopy(self.original)
        state["metadata"]["accountBasisGeneratedAt"] = stamp(-1000)
        service.state_reader = lambda _: state
        self.assertEqual("account-reconciliation-required", service.run_once()["status"])
        service.quote_reader.assert_not_called()

    def test_account_race_and_ingress_failure_roll_back_source_and_outbox(self):
        incoming = snapshot(self.deployment, stamp(-1))
        incoming.positions[0].current_price = 120
        with self.assertRaisesRegex(RuntimeError, "account basis changed"):
            self.recorder.record_cycle([self.deployment], [incoming], [], expected_previous={self.deployment: "old"})
        with self.assertRaisesRegex(RuntimeError, "ownership changed"):
            self.recorder.record_cycle([self.deployment], [incoming], [], required_deployment_id="shadow")
        with patch.object(monitoring, "ingress_reasoning_event_with_connection", side_effect=RuntimeError("ingress failed")):
            with self.assertRaisesRegex(RuntimeError, "ingress failed"):
                self.recorder.record_cycle([self.deployment], [incoming], [], expected_previous={self.deployment: self.base.generated_at}, required_deployment_id=self.deployment)
        restarted = monitoring.MySQLMonitorStore(self.config)
        self.assertEqual(self.original, restarted.previous[self.deployment])
        self.assertEqual({}, restarted.reasoning_snapshot_state_at(self.deployment, incoming.generated_at))

    def test_late_account_collection_keeps_new_quote_but_applies_new_quantity(self):
        # Account collection started first; its older quote finishes last.
        slow = snapshot(self.deployment, stamp(-5))
        slow.positions[0].quantity = 20
        slow.positions[0].source_as_of = self.base.generated_at
        service = self.refresh_service(replace(self.base.positions[0], current_price=110, source_as_of=stamp(-1)))
        self.assertEqual("committed", service.run_once()["status"])
        account_at = slow.generated_at
        self.recorder.record_cycle([self.deployment], [slow], [])
        state = monitoring.MySQLMonitorStore(self.config).previous[self.deployment]
        self.assertEqual(20, state["positions"]["AAPL"]["quantity"])
        self.assertEqual(110, state["positions"]["AAPL"]["current_price"])
        self.assertEqual(2200, state["positions"]["AAPL"]["mark_to_market_value"])
        self.assertEqual(account_at, state["metadata"]["accountBasisGeneratedAt"])
        self.assertNotIn("marketSourceRefresh", state["metadata"])
