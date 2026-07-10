from .domain.monitoring import (
    DEFAULT_ALERT_RULES,
    DEFAULT_CADENCE,
    DEFAULT_THRESHOLDS,
    MIN_CADENCE_MINUTES,
    RealtimeMonitor,
    money,
    now_ms,
    pct_delta,
    signed_pct,
)


class MonitorStore:
    def __new__(cls, *args, **kwargs):
        from .infrastructure.operational_store import monitor_store

        return monitor_store()

__all__ = [
    "DEFAULT_ALERT_RULES",
    "DEFAULT_CADENCE",
    "DEFAULT_THRESHOLDS",
    "MIN_CADENCE_MINUTES",
    "MonitorStore",
    "RealtimeMonitor",
    "money",
    "now_ms",
    "pct_delta",
    "signed_pct",
]
