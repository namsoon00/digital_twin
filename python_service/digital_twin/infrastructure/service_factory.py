from typing import Iterable

from ..application.flow_lens_service import FlowLensService
from ..application.model_review_service import ModelReviewRunner
from ..application.monitoring_service import MonitorRunner
from ..application.notification_service import NotificationQueueRunner
from ..application.symbol_universe_service import SymbolUniverseService
from ..domain.accounts import AccountConfig
from ..domain.monitoring import RealtimeMonitor
from .event_bus import EventBus, default_event_bus
from .model_review_queue import ModelReviewEnqueuer
from .model_reviewer import reviewer_from_settings
from .notifications import queued_notifier_for_account
from .notifications import send_events
from .notifications import notifier_for_account
from .settings import currency_rates, runtime_settings
from .sqlite_model_review import SQLiteModelReviewJobStore
from .sqlite_monitoring import SQLiteMonitorStore
from .sqlite_notifications import SQLiteNotificationJobStore, SQLiteNotificationTemplateStore
from .sqlite_symbols import SQLiteSymbolUniverseStore
from .sqlite_accounts import AccountRegistry
from .symbol_sources import RemoteSymbolSourceGateway
from .toss_snapshots import build_snapshot, demo_positions


def monitor_event_bus() -> EventBus:
    bus = default_event_bus()
    bus.subscribe_all(ModelReviewEnqueuer(SQLiteModelReviewJobStore()).handle)
    return bus


def build_monitor_runner(accounts: Iterable[AccountConfig], event_publisher=None) -> MonitorRunner:
    settings = runtime_settings()
    store = SQLiteMonitorStore()
    return MonitorRunner(
        accounts,
        store=store,
        monitor=RealtimeMonitor(settings),
        snapshot_builder=build_snapshot,
        event_sender=send_events,
        event_publisher=event_publisher or monitor_event_bus(),
        cycle_recorder=store,
    )


def build_model_review_runner(dry_run: bool = False) -> ModelReviewRunner:
    settings = runtime_settings()
    return ModelReviewRunner(
        queue=SQLiteModelReviewJobStore(),
        reviewer=reviewer_from_settings(settings),
        account_repository=AccountRegistry(),
        notifier_factory=lambda account: queued_notifier_for_account(account, message_type="modelReview"),
        dry_run=dry_run,
    )


def build_notification_queue_runner(dry_run: bool = False) -> NotificationQueueRunner:
    settings = runtime_settings()
    return NotificationQueueRunner(
        queue=SQLiteNotificationJobStore(),
        account_repository=AccountRegistry(),
        notifier_factory=notifier_for_account,
        dry_run=dry_run,
        send_gap_seconds=float(settings.get("notificationSendGapSeconds") or 0),
        template_renderer=SQLiteNotificationTemplateStore().render_job,
    )


def build_symbol_universe_service(settings=None) -> SymbolUniverseService:
    return SymbolUniverseService(
        store=SQLiteSymbolUniverseStore(),
        source_gateway=RemoteSymbolSourceGateway(),
        settings=settings or runtime_settings(),
    )


def build_flow_lens_service(settings=None) -> FlowLensService:
    configured_settings = settings or runtime_settings()
    symbol_service = build_symbol_universe_service(configured_settings)
    return FlowLensService(
        account_repository=AccountRegistry(),
        snapshot_builder=build_snapshot,
        demo_positions_provider=demo_positions,
        settings_provider=lambda: configured_settings,
        fx_rates_provider=currency_rates,
        symbol_enricher=symbol_service.enrich,
    )


def flow_lens_snapshot(mock: bool = False, watchlist_symbols: str = ""):
    return build_flow_lens_service().snapshot(mock=mock, watchlist_symbols=watchlist_symbols)
