import inspect
from typing import Callable, Iterable, List

from ..domain.accounts import AccountConfig
from ..domain.events import alerts_detected_event, monitoring_cycle_completed_event, snapshot_collected_event
from ..domain.portfolio import AccountSnapshot, AlertEvent
from ..domain.repositories import MonitorStateRepository, MonitoringCycleRecorder, OntologyProjectionRecorder, SnapshotMonitor


class MonitorRunner:
    def __init__(
        self,
        accounts: Iterable[AccountConfig],
        store: MonitorStateRepository,
        monitor: SnapshotMonitor,
        snapshot_builder: Callable[[AccountConfig], AccountSnapshot],
        event_sender: Callable,
        event_publisher=None,
        cycle_recorder: MonitoringCycleRecorder = None,
        ontology_projection_recorder: OntologyProjectionRecorder = None,
    ):
        self.accounts = list(accounts)
        self.account_map = {account.account_id: account for account in self.accounts}
        self.store = store
        self.monitor = monitor
        self.snapshot_builder = snapshot_builder
        self.event_sender = event_sender
        self.event_publisher = event_publisher
        self.cycle_recorder = cycle_recorder
        self.ontology_projection_recorder = ontology_projection_recorder

    def run_once(self, dry_run: bool = False, force: bool = False, symbol_filter: Iterable[str] = None) -> List[AlertEvent]:
        all_events: List[AlertEvent] = []
        snapshots = []
        allowed_symbols = set(str(symbol or "").upper().strip() for symbol in (symbol_filter or []) if str(symbol or "").strip())
        for account in self.accounts:
            snapshot = self.snapshot_builder(account)
            snapshots.append(snapshot)
            if not self.use_cycle_recorder(dry_run):
                self.publish(snapshot_collected_event(snapshot))
            previous = self.store.previous.get(snapshot.account_id) or {}
            snapshot.metadata["previousMonitorState"] = self.compact_previous_state(previous)
            snapshot.metadata.setdefault("ontology", {})["previousStateAvailable"] = bool(previous)
            events = self.monitor.events_for_snapshot(snapshot, previous)
            if allowed_symbols:
                events = self.filter_events_by_symbol(events, allowed_symbols)
            if not dry_run:
                self.record_ontology_projection(snapshot)
            events = self.monitor.apply_cadence(events, self.store, force=force)
            all_events.extend(events)
        if self.use_cycle_recorder(dry_run):
            for snapshot in snapshots:
                snapshot.metadata.pop("previousMonitorState", None)
            self.cycle_recorder.record_cycle(
                [account.account_id for account in self.accounts],
                snapshots,
                all_events,
                dry_run=dry_run,
            )
            if not all_events:
                print("No Python realtime monitoring events.")
            return all_events
        if all_events:
            alert_event = alerts_detected_event(all_events)
            self.publish(alert_event)
            result = self.send_alert_events(all_events, dry_run=dry_run, source_event=alert_event)
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
                snapshot.metadata.pop("previousMonitorState", None)
                self.store.save_snapshot(snapshot)
            self.store.write()
        return all_events

    def filter_events_by_symbol(self, events: List[AlertEvent], allowed_symbols: set) -> List[AlertEvent]:
        return [
            event
            for event in events
            if str(event.symbol or "").upper().strip() in allowed_symbols
        ]

    def use_cycle_recorder(self, dry_run: bool) -> bool:
        return self.cycle_recorder is not None and not dry_run

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

    def record_ontology_projection(self, snapshot: AccountSnapshot) -> None:
        if not self.ontology_projection_recorder or not self.has_ontology_projection_data(snapshot):
            return
        try:
            self.ontology_projection_recorder.record_snapshot(snapshot)
        except Exception as error:  # noqa: BLE001 - graph persistence must not block monitoring.
            result = {"saved": False, "status": "error", "reason": str(error)[:180]}
            snapshot.metadata.setdefault("ontology", {})["projection"] = result

    def persist_ontology(self, snapshot: AccountSnapshot) -> None:
        self.record_ontology_projection(snapshot)

    def has_ontology_projection_data(self, snapshot: AccountSnapshot) -> bool:
        if snapshot.has_live_account_data():
            return True
        if any(item for item in snapshot.watchlist or [] if not item.is_cash()):
            return True
        if isinstance(snapshot.external_signals, dict) and any(
            value not in ({}, [], "", None, False)
            for key, value in snapshot.external_signals.items()
            if key not in {"quality", "freshness", "provenance", "statuses"}
        ):
            return True
        return False

    def compact_previous_state(self, previous: dict) -> dict:
        if not isinstance(previous, dict) or not previous:
            return {}
        return {
            "generatedAt": previous.get("generatedAt"),
            "portfolio": previous.get("portfolio") if isinstance(previous.get("portfolio"), dict) else {},
            "positions": previous.get("positions") if isinstance(previous.get("positions"), dict) else {},
            "watchlist": previous.get("watchlist") if isinstance(previous.get("watchlist"), dict) else {},
            "decisions": previous.get("decisions") if isinstance(previous.get("decisions"), dict) else {},
            "externalSignals": previous.get("externalSignals") if isinstance(previous.get("externalSignals"), dict) else {},
        }

    def send_alert_events(self, events, dry_run: bool, source_event):
        parameters = inspect.signature(self.event_sender).parameters
        if "source_event" in parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        ):
            return self.event_sender(
                events,
                dry_run=dry_run,
                accounts=self.account_map,
                source_event=source_event,
            )
        return self.event_sender(events, dry_run=dry_run, accounts=self.account_map)
