import inspect
from typing import Callable, Iterable, List

from ..domain.accounts import AccountConfig
from ..domain.events import alerts_detected_event, monitoring_cycle_completed_event, snapshot_collected_event
from ..domain.ontology import build_portfolio_ontology
from ..domain.portfolio import AccountSnapshot, AlertEvent
from ..domain.repositories import MonitorStateRepository, MonitoringCycleRecorder, SnapshotMonitor


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
        ontology_repository=None,
        ontology_quality_store=None,
    ):
        self.accounts = list(accounts)
        self.account_map = {account.account_id: account for account in self.accounts}
        self.store = store
        self.monitor = monitor
        self.snapshot_builder = snapshot_builder
        self.event_sender = event_sender
        self.event_publisher = event_publisher
        self.cycle_recorder = cycle_recorder
        self.ontology_repository = ontology_repository
        self.ontology_quality_store = ontology_quality_store

    def run_once(self, dry_run: bool = False, force: bool = False) -> List[AlertEvent]:
        all_events: List[AlertEvent] = []
        snapshots = []
        for account in self.accounts:
            snapshot = self.snapshot_builder(account)
            snapshots.append(snapshot)
            if not self.use_cycle_recorder(dry_run):
                self.publish(snapshot_collected_event(snapshot))
            previous = self.store.previous.get(snapshot.account_id) or {}
            events = self.monitor.events_for_snapshot(snapshot, previous)
            if not dry_run:
                self.persist_ontology(snapshot)
            events = self.monitor.apply_cadence(events, self.store, force=force)
            all_events.extend(events)
        if self.use_cycle_recorder(dry_run):
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
                self.store.save_snapshot(snapshot)
            self.store.write()
        return all_events

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

    def persist_ontology(self, snapshot: AccountSnapshot) -> None:
        if not self.ontology_repository or not snapshot.has_live_account_data():
            return
        try:
            legacy_by_symbol = {
                item.symbol.upper(): item.to_dict()
                for item in snapshot.decisions
            }
            graph = build_portfolio_ontology(
                snapshot.positions,
                snapshot.portfolio,
                legacy_by_symbol=legacy_by_symbol,
                external_signals=snapshot.external_signals,
                portfolio_id=snapshot.account_id,
                runtime_context={
                    "settings": dict(getattr(self.monitor, "settings", {}) or {}),
                    "account": {
                        "accountId": snapshot.account_id,
                        "accountLabel": snapshot.account_label,
                        "provider": snapshot.provider,
                        "mode": snapshot.mode,
                        "status": snapshot.status,
                    },
                    "metadata": dict(snapshot.metadata or {}),
                    "decisionItems": [item.to_dict() for item in snapshot.decisions],
                },
            )
            result = self.ontology_repository.save_graph(graph)
            if self.ontology_quality_store:
                sample = self.ontology_quality_store.record_graph(graph, source="monitoring")
                result["qualitySampleId"] = sample.sample_id
                result["qualityScore"] = sample.overall_score
        except Exception as error:  # noqa: BLE001 - graph persistence must not block monitoring.
            result = {"saved": False, "status": "error", "reason": str(error)[:180]}
        snapshot.metadata.setdefault("ontology", {})["neo4j"] = result

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
