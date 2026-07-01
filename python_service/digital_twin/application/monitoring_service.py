from typing import Callable, Iterable, List

from ..domain.accounts import AccountConfig
from ..domain.events import alerts_detected_event, monitoring_cycle_completed_event, snapshot_collected_event
from ..domain.portfolio import AccountSnapshot, AlertEvent
from ..domain.repositories import MonitorStateRepository, SnapshotMonitor


class MonitorRunner:
    def __init__(
        self,
        accounts: Iterable[AccountConfig],
        store: MonitorStateRepository,
        monitor: SnapshotMonitor,
        snapshot_builder: Callable[[AccountConfig], AccountSnapshot],
        event_sender: Callable,
        event_publisher=None,
    ):
        self.accounts = list(accounts)
        self.account_map = {account.account_id: account for account in self.accounts}
        self.store = store
        self.monitor = monitor
        self.snapshot_builder = snapshot_builder
        self.event_sender = event_sender
        self.event_publisher = event_publisher

    def run_once(self, dry_run: bool = False, force: bool = False) -> List[AlertEvent]:
        all_events: List[AlertEvent] = []
        snapshots = []
        for account in self.accounts:
            snapshot = self.snapshot_builder(account)
            snapshots.append(snapshot)
            self.publish(snapshot_collected_event(snapshot))
            previous = self.store.previous.get(snapshot.account_id) or {}
            events = self.monitor.events_for_snapshot(snapshot, previous)
            events = self.monitor.apply_cadence(events, self.store, force=force)
            all_events.extend(events)
        if all_events:
            self.publish(alerts_detected_event(all_events))
            result = self.event_sender(all_events, dry_run=dry_run, accounts=self.account_map)
            self.publish_cycle_completed(snapshots, all_events, dry_run, result.delivered)
            if dry_run:
                return all_events
            if result.delivered:
                self.store.mark_sent(all_events)
        else:
            print("No Python realtime monitoring events.")
            self.publish_cycle_completed(snapshots, all_events, dry_run, False)
        if not dry_run:
            for snapshot in snapshots:
                self.store.save_snapshot(snapshot)
            self.store.write()
        return all_events

    def publish_cycle_completed(self, snapshots, events, dry_run: bool, delivered: bool) -> None:
        self.publish(monitoring_cycle_completed_event(
            [account.account_id for account in self.accounts],
            len(snapshots),
            len(events),
            dry_run,
            delivered,
        ))

    def publish(self, event) -> None:
        if self.event_publisher:
            self.event_publisher.publish(event)
