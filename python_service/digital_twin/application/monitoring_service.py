import inspect
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, List

from ..domain.accounts import AccountConfig
from ..domain.events import alerts_detected_event, monitoring_cycle_completed_event, snapshot_collected_event
from ..domain.portfolio import AccountSnapshot, AlertEvent
from ..domain.repositories import MonitorAccountJob, MonitorAccountJobRepository, MonitorStateRepository, MonitoringCycleRecorder, OntologyProjectionRecorder, SnapshotMonitor


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
        account_job_store: MonitorAccountJobRepository = None,
        account_job_batch_size: int = 10,
        account_job_interval_seconds: int = 180,
        account_job_lock_seconds: int = 600,
        worker_id: str = "",
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
        self.account_job_store = account_job_store
        self.account_job_batch_size = max(1, int(account_job_batch_size or 10))
        self.account_job_interval_seconds = max(30, int(account_job_interval_seconds or 180))
        self.account_job_lock_seconds = max(60, int(account_job_lock_seconds or 600))
        self.worker_id = worker_id or ("monitor-" + uuid.uuid4().hex[:12])

    def run_once(self, dry_run: bool = False, force: bool = False, symbol_filter: Iterable[str] = None) -> List[AlertEvent]:
        if self.use_account_job_queue(dry_run, force, symbol_filter):
            return self.run_due_account_jobs()
        return self.run_all_accounts_once(dry_run=dry_run, force=force, symbol_filter=symbol_filter)

    def use_account_job_queue(self, dry_run: bool, force: bool, symbol_filter: Iterable[str] = None) -> bool:
        return bool(self.account_job_store) and not dry_run and not force and not list(symbol_filter or [])

    def run_all_accounts_once(self, dry_run: bool = False, force: bool = False, symbol_filter: Iterable[str] = None) -> List[AlertEvent]:
        all_events: List[AlertEvent] = []
        snapshots = []
        allowed_symbols = set(str(symbol or "").upper().strip() for symbol in (symbol_filter or []) if str(symbol or "").strip())
        for account in self.accounts:
            snapshot, events = self.collect_account_events(account, allowed_symbols=allowed_symbols, dry_run=dry_run, force=force)
            snapshots.append(snapshot)
            if not self.use_cycle_recorder(dry_run):
                self.publish(snapshot_collected_event(snapshot))
            all_events.extend(events)
        if self.use_cycle_recorder(dry_run):
            for snapshot in snapshots:
                snapshot.metadata.pop("previousMonitorState", None)
                snapshot.metadata.pop("monitorStateHistory", None)
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
                snapshot.metadata.pop("monitorStateHistory", None)
                self.store.save_snapshot(snapshot)
            self.store.write()
        return all_events

    def run_due_account_jobs(self) -> List[AlertEvent]:
        self.account_job_store.sync_accounts(self.accounts, self.account_job_interval_seconds)
        jobs = self.account_job_store.claim_due(
            limit=self.account_job_batch_size,
            worker_id=self.worker_id,
            lock_seconds=self.account_job_lock_seconds,
            default_interval_seconds=self.account_job_interval_seconds,
        )
        if not jobs:
            print("No due account monitor jobs.")
            return []
        all_events: List[AlertEvent] = []
        for job in jobs:
            account = self.account_map.get(job.account_id)
            if not account:
                self.account_job_store.mark_failed(
                    job.account_id,
                    "계정 설정을 찾을 수 없습니다.",
                    self.next_account_run_at(self.account_job_interval_seconds),
                )
                continue
            try:
                events = self.run_single_account_job(account, job)
                all_events.extend(events)
            except Exception as error:  # noqa: BLE001 - account-level failures must not block other accounts.
                self.account_job_store.mark_failed(
                    job.account_id,
                    str(error)[:500],
                    self.next_account_run_at(self.failure_backoff_seconds(job)),
                )
        if not all_events:
            print("No Python realtime monitoring events.")
        return all_events

    def run_single_account_job(self, account: AccountConfig, job: MonitorAccountJob) -> List[AlertEvent]:
        snapshot, events = self.collect_account_events(account, allowed_symbols=set(), dry_run=False, force=False)
        if self.cycle_recorder:
            snapshot.metadata.pop("previousMonitorState", None)
            snapshot.metadata.pop("monitorStateHistory", None)
            self.cycle_recorder.record_cycle([account.account_id], [snapshot], events, dry_run=False)
        else:
            self.publish(snapshot_collected_event(snapshot))
            if events:
                alert_event = alerts_detected_event(events)
                self.publish(alert_event)
                result = self.send_alert_events(events, dry_run=False, source_event=alert_event)
                self.publish_cycle_completed([snapshot], events, False, result.delivered)
                if result.delivered:
                    self.store.mark_sent(events)
            else:
                self.publish_cycle_completed([snapshot], events, False, False)
            self.store.save_snapshot(snapshot)
            self.store.write()
        self.account_job_store.mark_done(account.account_id, self.next_account_run_at(self.account_job_interval_seconds))
        return events

    def collect_account_events(
        self,
        account: AccountConfig,
        allowed_symbols: set = None,
        dry_run: bool = False,
        force: bool = False,
    ):
        snapshot = self.snapshot_builder(account)
        previous = self.store.previous.get(snapshot.account_id) or {}
        snapshot.metadata["previousMonitorState"] = self.compact_previous_state(previous)
        snapshot.metadata["monitorStateHistory"] = self.compact_monitor_history(
            self.load_snapshot_history(snapshot.account_id)
        )
        snapshot.metadata.setdefault("ontology", {})["previousStateAvailable"] = bool(previous)
        # Dry-run still needs graph inference to simulate the same investment
        # judgement path as live monitoring; delivery and monitor persistence
        # remain gated by dry_run below.
        self.record_ontology_projection(snapshot)
        events = self.monitor.events_for_snapshot(snapshot, previous)
        if allowed_symbols:
            events = self.filter_events_by_symbol(events, allowed_symbols)
        events = self.monitor.apply_cadence(events, self.store, force=force)
        return snapshot, events

    def next_account_run_at(self, seconds: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=max(1, int(seconds or 1)))).isoformat().replace("+00:00", "Z")

    def failure_backoff_seconds(self, job: MonitorAccountJob) -> int:
        attempts = max(1, int(getattr(job, "attempts", 0) or 0))
        return min(self.account_job_interval_seconds * 8, self.account_job_interval_seconds * attempts)

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

    def load_snapshot_history(self, account_id: str) -> List[dict]:
        if hasattr(self.store, "load_history"):
            return list(self.store.load_history(account_id, limit=6) or [])
        return []

    def compact_monitor_history(self, history: List[dict]) -> List[dict]:
        compacted = []
        for item in history or []:
            compacted_item = self.compact_previous_state(item)
            if compacted_item:
                compacted.append(compacted_item)
        return compacted[-6:]

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
