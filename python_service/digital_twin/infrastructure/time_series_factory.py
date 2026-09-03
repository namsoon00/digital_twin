"""Runtime composition for replaceable time-series backends."""

from typing import Dict

from ..application.time_series_platform import (
    TemporalFeatureSnapshotService,
    TimeSeriesBackendPlatformService,
    TimeSeriesProjectionRunner,
    VersionedMarketTimeSeriesStore,
    truthy,
)
from ..domain.time_series_storage import TimeSeriesBackendDescriptor
from .mysql_market_time_series import MySQLMarketTimeSeriesStore
from .mysql_versioned_runtime import (
    MySQLTemporalFeatureSnapshotStore,
    MySQLTimeSeriesBackendRegistryStore,
    MySQLTimeSeriesProjectionOutboxStore,
)
from .questdb_time_series import QuestDBTimeSeriesAdapter


def build_time_series_adapters(settings: Dict[str, object] = None):
    configured = dict(settings or {})
    mysql = MySQLMarketTimeSeriesStore(configured)
    adapters = {mysql.backend_id: mysql}
    if truthy(configured.get("timeSeriesQuestDbEnabled")):
        questdb = QuestDBTimeSeriesAdapter(configured, "questdb-shadow")
        adapters[questdb.backend_id] = questdb
    return adapters


def initialize_time_series_registry(settings, adapters=None):
    configured = dict(settings or {})
    adapters = dict(adapters or build_time_series_adapters(configured))
    registry = MySQLTimeSeriesBackendRegistryStore(configured)
    for backend_id, adapter in adapters.items():
        descriptor = adapter.descriptor()
        if backend_id == "mysql-primary":
            descriptor = TimeSeriesBackendDescriptor(
                backend_id=descriptor.backend_id,
                adapter_name=descriptor.adapter_name,
                adapter_version=descriptor.adapter_version,
                status="active",
                contract_version=descriptor.contract_version,
                capabilities=descriptor.capabilities,
                settings=descriptor.settings,
            )
        registry.upsert(descriptor)
    control = registry.control()
    # The registry is the runtime source of truth. Settings only bootstrap an
    # empty control row; otherwise a restart would undo failover or candidate state.
    has_runtime_control = bool(str(control.get("activeBackendId") or "").strip())
    active = str(
        control.get("activeBackendId")
        if has_runtime_control else configured.get("timeSeriesActiveBackendId") or "mysql-primary"
    )
    if active not in adapters:
        active = "mysql-primary"
    shadow = str(
        control.get("shadowBackendId")
        if has_runtime_control else configured.get("timeSeriesShadowBackendId") or ""
    )
    if shadow not in adapters or shadow == active:
        shadow = ""
    candidate = str(control.get("candidateBackendId") or "") if has_runtime_control else shadow
    if candidate not in adapters or candidate == active:
        candidate = ""
    if (
        str(control.get("activeBackendId") or "") != active
        or str(control.get("shadowBackendId") or "") != shadow
        or str(control.get("candidateBackendId") or "") != candidate
    ):
        registry.set_control(active, shadow, candidate)
    return registry


def build_versioned_time_series_store(settings: Dict[str, object] = None):
    configured = dict(settings or {})
    adapters = build_time_series_adapters(configured)
    registry = initialize_time_series_registry(configured, adapters)
    return VersionedMarketTimeSeriesStore(
        baseline_store=adapters["mysql-primary"],
        adapters=adapters,
        registry=registry,
        outbox=MySQLTimeSeriesProjectionOutboxStore(configured),
        settings=configured,
        snapshot_store=MySQLTemporalFeatureSnapshotStore(configured),
    )


def build_time_series_projection_runner(settings: Dict[str, object] = None, worker_id: str = ""):
    configured = dict(settings or {})
    adapters = build_time_series_adapters(configured)
    registry = initialize_time_series_registry(configured, adapters)
    return TimeSeriesProjectionRunner(
        adapters,
        MySQLTimeSeriesProjectionOutboxStore(configured),
        registry,
        configured,
        worker_id=worker_id,
    )


def build_temporal_feature_snapshot_service(settings: Dict[str, object] = None):
    configured = dict(settings or {})
    adapters = build_time_series_adapters(configured)
    initialize_time_series_registry(configured, adapters)
    return TemporalFeatureSnapshotService(adapters, MySQLTemporalFeatureSnapshotStore(configured))


def build_time_series_backend_platform(settings: Dict[str, object] = None):
    configured = dict(settings or {})
    adapters = build_time_series_adapters(configured)
    registry = initialize_time_series_registry(configured, adapters)
    snapshots = TemporalFeatureSnapshotService(adapters, MySQLTemporalFeatureSnapshotStore(configured))
    return TimeSeriesBackendPlatformService(
        adapters,
        registry,
        MySQLTimeSeriesProjectionOutboxStore(configured),
        snapshots,
        configured,
    )
