from .application.scheduler import MIN_REALTIME_INTERVAL_SECONDS, RealtimeScheduler
from .infrastructure.notifications import send_events
from .infrastructure.service_factory import build_monitor_runner
from .infrastructure.toss_snapshots import build_snapshot


class MonitorRunner:
    def __new__(cls, accounts, *args, **kwargs):
        from .application.monitoring_service import MonitorRunner as ApplicationMonitorRunner
        from .domain.monitoring import RealtimeMonitor
        from .infrastructure.event_bus import default_event_bus
        from .infrastructure.settings import runtime_settings
        from .infrastructure.sqlite_monitoring import SQLiteMonitorStore

        return ApplicationMonitorRunner(
            accounts,
            store=kwargs.get("store") or SQLiteMonitorStore(),
            monitor=kwargs.get("monitor") or RealtimeMonitor(runtime_settings()),
            snapshot_builder=kwargs.get("snapshot_builder") or build_snapshot,
            event_sender=kwargs.get("event_sender") or send_events,
            event_publisher=kwargs.get("event_publisher") or default_event_bus(),
        )


__all__ = [
    "MIN_REALTIME_INTERVAL_SECONDS",
    "MonitorRunner",
    "RealtimeScheduler",
    "build_monitor_runner",
    "build_snapshot",
    "send_events",
]
