from typing import Iterable

from ..application.model_review_service import ModelReviewRunner
from ..application.monitoring_service import MonitorRunner
from ..domain.accounts import AccountConfig
from ..domain.monitoring import RealtimeMonitor
from .event_bus import EventBus, default_event_bus
from .model_review_queue import ModelReviewEnqueuer
from .model_reviewer import reviewer_from_settings
from .notifications import send_events
from .notifications import notifier_for_account
from .settings import runtime_settings
from .sqlite_operational import SQLiteModelReviewJobStore, SQLiteMonitorStore
from .sqlite_accounts import AccountRegistry
from .toss_snapshots import build_snapshot


def monitor_event_bus() -> EventBus:
    bus = default_event_bus()
    bus.subscribe_all(ModelReviewEnqueuer(SQLiteModelReviewJobStore()).handle)
    return bus


def build_monitor_runner(accounts: Iterable[AccountConfig], event_publisher=None) -> MonitorRunner:
    settings = runtime_settings()
    return MonitorRunner(
        accounts,
        store=SQLiteMonitorStore(),
        monitor=RealtimeMonitor(settings),
        snapshot_builder=build_snapshot,
        event_sender=send_events,
        event_publisher=event_publisher or monitor_event_bus(),
    )


def build_model_review_runner(dry_run: bool = False) -> ModelReviewRunner:
    settings = runtime_settings()
    return ModelReviewRunner(
        queue=SQLiteModelReviewJobStore(),
        reviewer=reviewer_from_settings(settings),
        account_repository=AccountRegistry(),
        notifier_factory=notifier_for_account,
        dry_run=dry_run,
    )
