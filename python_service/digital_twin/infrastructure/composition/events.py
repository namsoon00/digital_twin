"""Events runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.infrastructure.event_bus import EventBus


def monitor_event_bus(settings=None) -> EventBus:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.event_bus import EventBus, default_event_bus
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.model_registry.infrastructure.model_review_queue import ModelReviewEnqueuer

    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe_all(ModelReviewEnqueuer(stores.model_review_job_store(configured_settings)).handle)
    return bus


def news_event_bus(settings=None) -> EventBus:
    from digital_twin.platform.application.data_pipeline_health_service import DataPipelineHealthNotificationEnqueuer
    from digital_twin.platform.domain.event_types import DATA_PIPELINE_HEALTH_CHANGED
    from digital_twin.modules.news_intelligence.domain.event_types import RESEARCH_EVIDENCE_COLLECTED
    from digital_twin.modules.market_data.domain.market_data import number
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.composition.investment_calendar import build_investment_calendar_service
    from digital_twin.infrastructure.event_bus import EventBus, default_event_bus
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.investment_calendar.public import InvestmentCalendarExtractionService
    from digital_twin.modules.news_intelligence.public import NewsDigestEnqueuer

    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe(
        RESEARCH_EVIDENCE_COLLECTED,
        NewsDigestEnqueuer(
            account_repository=stores.account_reader(configured_settings),
            monitor_store=stores.monitor_store(configured_settings),
            queue=stores.notification_job_store(configured_settings),
            settings=configured_settings,
            max_items=int(number(configured_settings.get("newsDigestMaxItems")) or 3),
            evidence_repository=stores.research_evidence_store(configured_settings),
        ).handle,
    )
    bus.subscribe(
        DATA_PIPELINE_HEALTH_CHANGED,
        DataPipelineHealthNotificationEnqueuer(
            account_repository=stores.account_reader(configured_settings),
            queue=stores.notification_job_store(configured_settings),
            settings=configured_settings,
        ).handle,
    )
    calendar_service = build_investment_calendar_service(configured_settings)
    bus.subscribe(
        RESEARCH_EVIDENCE_COLLECTED,
        InvestmentCalendarExtractionService(
            calendar_service=calendar_service,
            account_repository=stores.account_reader(configured_settings),
            candidate_repository=stores.investment_calendar_candidate_store(configured_settings),
            settings=configured_settings,
        ).handle,
    )
    return bus


def data_pipeline_health_event_bus(settings=None) -> EventBus:
    from digital_twin.platform.application.data_pipeline_health_service import DataPipelineHealthNotificationEnqueuer
    from digital_twin.platform.domain.event_types import DATA_PIPELINE_HEALTH_CHANGED
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.event_bus import EventBus, default_event_bus
    from digital_twin.infrastructure.settings import runtime_settings

    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe(
        DATA_PIPELINE_HEALTH_CHANGED,
        DataPipelineHealthNotificationEnqueuer(
            account_repository=stores.account_reader(configured_settings),
            queue=stores.notification_job_store(configured_settings),
            settings=configured_settings,
        ).handle,
    )
    return bus


def operational_storage_event_bus(settings=None) -> EventBus:
    from digital_twin.platform.application.operational_storage_capacity_service import OperationalStorageCapacityNotificationEnqueuer
    from digital_twin.platform.domain.event_types import OPERATIONAL_STORAGE_CAPACITY_CHANGED
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.event_bus import EventBus, default_event_bus
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.notifications.infrastructure.notification.transport import notifier_for_operations

    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe(
        OPERATIONAL_STORAGE_CAPACITY_CHANGED,
        OperationalStorageCapacityNotificationEnqueuer(
            queue=stores.notification_job_store(configured_settings),
            fallback_notifier_factory=notifier_for_operations,
        ).handle,
    )
    return bus


def ontology_reasoning_event_bus(settings=None) -> EventBus:
    from digital_twin.modules.reasoning.domain.event_types import ONTOLOGY_REASONING_QUEUE_HEALTH_CHANGED
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.event_bus import EventBus, default_event_bus
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.reasoning.public import OntologyReasoningQueueHealthNotificationEnqueuer

    configured_settings = settings or runtime_settings()
    bus = default_event_bus()
    bus.subscribe(
        ONTOLOGY_REASONING_QUEUE_HEALTH_CHANGED,
        OntologyReasoningQueueHealthNotificationEnqueuer(
            queue=stores.notification_job_store(configured_settings),
            settings=configured_settings,
        ).handle,
    )
    return bus
