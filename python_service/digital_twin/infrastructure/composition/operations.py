"""Operations runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.application.operational_storage_capacity_service import OperationalStorageCapacityService


def build_operational_storage_capacity_service(settings=None) -> OperationalStorageCapacityService:
    from digital_twin.application.operational_storage_capacity_service import OperationalStorageCapacityService
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings

    configured_settings = settings or runtime_settings()
    return OperationalStorageCapacityService(
        store=stores.operational_storage_capacity_state_store(configured_settings),
        settings=configured_settings,
    )


def observe_operational_storage_capacity(
    settings=None,
    snapshot=None,
    force_alert: bool = False,
    force_alert_kind: str = "runtime-write-failure",
):
    """Record one bounded capacity observation and dispatch any state alert.

    The helper is intentionally usable from error paths.  When MySQL cannot
    accept the event or notification job, the subscribed enqueuer sends the
    operations notifier directly instead.
    """
    from digital_twin.infrastructure.composition.events import operational_storage_event_bus
    from digital_twin.infrastructure.operational_storage_guard import operational_storage_inventory
    from digital_twin.infrastructure.settings import runtime_settings

    configured_settings = settings or runtime_settings()
    observed = dict(snapshot or operational_storage_inventory(configured_settings))
    health, event = build_operational_storage_capacity_service(configured_settings).record(
        observed,
        force_alert=force_alert,
        force_alert_kind=force_alert_kind,
    )
    if event:
        operational_storage_event_bus(configured_settings).publish(event)
    return health
