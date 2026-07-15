from .infrastructure.schedulers import MIN_REALTIME_INTERVAL_SECONDS, RealtimeScheduler
from .infrastructure.notifications import send_events
from .infrastructure.service_factory import build_monitor_runner
from .infrastructure.toss_snapshots import build_snapshot


class MonitorRunner:
    def __new__(cls, accounts, *args, **kwargs):
        from .application.monitoring_service import MonitorRunner as ApplicationMonitorRunner
        from .domain.monitoring import RealtimeMonitor
        from .infrastructure.event_bus import default_event_bus
        from .infrastructure import operational_store as stores
        from .infrastructure.settings import runtime_settings

        settings = runtime_settings()
        store = kwargs.get("store") or stores.monitor_store(settings)
        cycle_recorder = kwargs.get("cycle_recorder") or stores.monitoring_cycle_recorder(settings, store)
        return ApplicationMonitorRunner(
            accounts,
            store=store,
            monitor=kwargs.get("monitor") or RealtimeMonitor(settings),
            snapshot_builder=kwargs.get("snapshot_builder") or build_snapshot,
            event_sender=kwargs.get("event_sender") or send_events,
            event_publisher=kwargs.get("event_publisher") or default_event_bus(),
            cycle_recorder=cycle_recorder,
        )


__all__ = [
    "MIN_REALTIME_INTERVAL_SECONDS",
    "MonitorRunner",
    "RealtimeScheduler",
    "build_monitor_runner",
    "build_snapshot",
    "send_events",
]
