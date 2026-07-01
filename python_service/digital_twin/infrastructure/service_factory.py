from typing import Iterable

from ..application.monitoring_service import MonitorRunner
from ..domain.accounts import AccountConfig
from ..domain.monitoring import RealtimeMonitor
from .event_bus import default_event_bus
from .json_monitor_state import MonitorStore
from .notifications import send_events
from .settings import runtime_settings
from .toss_snapshots import build_snapshot


def build_monitor_runner(accounts: Iterable[AccountConfig], event_publisher=None) -> MonitorRunner:
    settings = runtime_settings()
    return MonitorRunner(
        accounts,
        store=MonitorStore(),
        monitor=RealtimeMonitor(settings),
        snapshot_builder=build_snapshot,
        event_sender=send_events,
        event_publisher=event_publisher or default_event_bus(),
    )

