import signal
import time
from typing import Iterable

from .application.monitoring_service import MonitorRunner as ApplicationMonitorRunner
from .config import AccountConfig
from .infrastructure.event_bus import default_event_bus
from .monitor import MonitorStore, RealtimeMonitor
from .notifiers import send_events
from .providers import build_snapshot


MIN_REALTIME_INTERVAL_SECONDS = 10 * 60


class MonitorRunner(ApplicationMonitorRunner):
    def __init__(
        self,
        accounts: Iterable[AccountConfig],
        store: MonitorStore = None,
        monitor: RealtimeMonitor = None,
        snapshot_builder=None,
        event_sender=None,
        event_publisher=None,
    ):
        super().__init__(
            accounts,
            store=store or MonitorStore(),
            monitor=monitor or RealtimeMonitor(),
            snapshot_builder=snapshot_builder or build_snapshot,
            event_sender=event_sender or send_events,
            event_publisher=event_publisher or default_event_bus(),
        )


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
