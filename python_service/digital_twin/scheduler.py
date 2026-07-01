import signal
import time
from typing import Iterable, List

from .config import AccountConfig
from .models import AlertEvent
from .monitor import MonitorStore, RealtimeMonitor
from .notifiers import send_events
from .providers import build_snapshot


MIN_REALTIME_INTERVAL_SECONDS = 10 * 60


class MonitorRunner:
    def __init__(self, accounts: Iterable[AccountConfig], store: MonitorStore = None, monitor: RealtimeMonitor = None):
        self.accounts = list(accounts)
        self.account_map = {account.account_id: account for account in self.accounts}
        self.store = store or MonitorStore()
        self.monitor = monitor or RealtimeMonitor()

    def run_once(self, dry_run: bool = False, force: bool = False) -> List[AlertEvent]:
        all_events: List[AlertEvent] = []
        snapshots = []
        for account in self.accounts:
            snapshot = build_snapshot(account)
            snapshots.append(snapshot)
            previous = self.store.previous.get(snapshot.account_id) or {}
            events = self.monitor.events_for_snapshot(snapshot, previous)
            events = self.monitor.apply_cadence(events, self.store, force=force)
            all_events.extend(events)
        if all_events:
            result = send_events(all_events, dry_run=dry_run, accounts=self.account_map)
            if dry_run:
                return all_events
            if result.delivered:
                self.store.mark_sent(all_events)
        else:
            print("No Python realtime monitoring events.")
        if not dry_run:
            for snapshot in snapshots:
                self.store.save_snapshot(snapshot)
            self.store.write()
        return all_events


class RealtimeScheduler:
    def __init__(self, runner: MonitorRunner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(MIN_REALTIME_INTERVAL_SECONDS, int(interval_seconds or MIN_REALTIME_INTERVAL_SECONDS))
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        print("Python realtime monitor started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                self.runner.run_once()
            except Exception as error:  # noqa: BLE001 - long-running scheduler must continue after a cycle failure.
                print("Python realtime monitor error: " + str(error))
            elapsed = time.monotonic() - started
            sleep_seconds = max(1.0, self.interval_seconds - elapsed)
            end_at = time.monotonic() + sleep_seconds
            while self.running and time.monotonic() < end_at:
                time.sleep(min(1.0, end_at - time.monotonic()))
