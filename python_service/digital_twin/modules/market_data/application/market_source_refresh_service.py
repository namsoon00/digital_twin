"""Publish newly observed quotes without collecting the account again."""
from datetime import datetime, timezone
from time import monotonic

from digital_twin.modules.portfolio.contracts import account_snapshot_from_monitor_state, MonitoringCycleRecorder
from digital_twin.modules.reasoning.contracts import VERIFIED_MONITOR_SNAPSHOT_QUEUED
from ..domain.data_freshness import parse_datetime
from ..domain.market_source_refresh import merge_market_position, revalue_market_snapshot


class MarketSourceRefreshService:
    def __init__(self, *, accounts, state_reader, quote_reader, recorder: MonitoringCycleRecorder, monitor,
                 state_store, authority, deployment_id, settings, guard):
        self.accounts, self.state_reader, self.quote_reader = accounts, state_reader, quote_reader
        self.recorder, self.monitor, self.state_store = recorder, monitor, state_store
        self.authority, self.deployment_id, self.settings, self.guard = authority, deployment_id, settings, guard
        self.cursor = 0
        self.next_check = 0.0

    def run_once(self):
        if monotonic() < self.next_check:
            return {"status": "not-due"}
        self.next_check = monotonic() + 2
        if not self.authority():
            return {"status": "not-delivery-owner"}
        accounts = [a for a in self.accounts() if a.enabled]
        if not accounts:
            return {"status": "no-accounts"}
        account = accounts[self.cursor % len(accounts)]
        self.cursor += 1
        started = monotonic()
        with self.guard() as lease:
            if not lease.acquired:
                return {"status": "storage-busy"}
            state = self.state_reader(account.account_id)
            snapshot = account_snapshot_from_monitor_state(state)
            if not snapshot or not snapshot.has_live_account_data():
                return {"status": "account-basis-missing"}
            now = datetime.now(timezone.utc)
            stamp = now.isoformat().replace("+00:00", "Z")
            base = snapshot.generated_at
            basis = str(snapshot.metadata.get("accountBasisGeneratedAt") or base)
            basis_time = parse_datetime(basis)
            limit = max(60, min(3600, int(self.settings.get("monitorAccountIntervalSeconds") or 120) * 2))
            completeness = snapshot.metadata.get("accountSnapshotCompleteness") or {}
            if (not basis_time or not 0 <= (now - basis_time).total_seconds() <= limit
                    or completeness.get("holdings") != "complete" or completeness.get("cash") != "complete"
                    or snapshot.portfolio.valuation_basis == "legacy-unknown"):
                return {"status": "account-reconciliation-required", "accountBasisGeneratedAt": basis}
            candidates = self.quote_reader(account.account_id, snapshot.positions + snapshot.watchlist)
            changed = []
            for group in ("positions", "watchlist"):
                positions = getattr(snapshot, group)
                for index, position in enumerate(positions):
                    if position.is_cash():
                        continue
                    updated = position
                    for proposal in candidates.get(position.symbol, []):
                        updated = merge_market_position(updated, proposal, stamp)
                    if updated != position:
                        positions[index] = updated
                        changed.append(position.symbol)
            if not changed:
                return {"status": "unchanged"}
            snapshot.generated_at = stamp
            snapshot.decisions = []
            snapshot.metadata["accountBasisGeneratedAt"] = basis
            snapshot.metadata["marketSourceRefresh"] = {
                "contractVersion": "market-source-refresh-v1", "baseGeneratedAt": base,
                "accountBasisGeneratedAt": basis, "capturedAt": stamp, "symbols": sorted(set(changed)),
                "inputPolicy": "latest-durable-market-observation",
            }
            snapshot.metadata.pop("reasoningSnapshotReplay", None)
            snapshot.metadata.setdefault("ontology", {})["projection"] = {"saved": False, "status": VERIFIED_MONITOR_SNAPSHOT_QUEUED}
            revalue_market_snapshot(snapshot)
            events = self.monitor.apply_cadence(self.monitor.events_for_snapshot(snapshot, state), self.state_store)
            result = self.recorder.record_cycle(
                [account.account_id], [snapshot], events,
                expected_previous={account.account_id: base}, required_deployment_id=self.deployment_id,
            )
            return {"status": "committed", "generatedAt": stamp, "symbols": sorted(set(changed)),
                    "accountBasisGeneratedAt": basis, "elapsedMs": round((monotonic() - started) * 1000),
                    "queuedNotifications": result.queued_count if hasattr(result, "queued_count") else 0}
