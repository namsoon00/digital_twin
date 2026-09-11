"""Web operations boundary."""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait
from digital_twin.infrastructure.stale_read_model import StaleReadModelCache
from digital_twin.infrastructure.web.adapters.console import console_read_model_service
from digital_twin.infrastructure.web.adapters.external_data import external_data_status_payload
from digital_twin.infrastructure.web.adapters.platforms import ontology_reasoning_status_payload
from digital_twin.infrastructure.web.adapters.platforms import reasoning_engine_platform_status_payload
from digital_twin.infrastructure.web.adapters.platforms import time_series_platform_status_payload
from digital_twin.infrastructure.web.cache import cached_api_payload
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.events import realtime_status_payload
from typing import Dict


OPERATIONS_HEALTH_READ_MODEL = StaleReadModelCache(
    "operations-health",
    ttl_seconds=30,
    retry_cooldown_seconds=15,
)


def _console_operations_health_source_payload() -> Dict[str, object]:
    settings = operational_read_settings()

    def storage_payload():
        from digital_twin.platform.domain.mysql_minimal_retention import mysql_minimal_retention_policy
        from digital_twin.platform.domain.operational_storage_capacity import operational_storage_capacity_read_model
        from digital_twin.infrastructure.operational_store import operational_storage_capacity_state_store
        from digital_twin.infrastructure.operational_storage_guard import operational_storage_inventory

        inventory = operational_storage_inventory(settings)
        try:
            stored = dict(
                operational_storage_capacity_state_store(settings).load() or {}
            )
            observation = dict(stored.get("operationalStorageCapacity") or {})
        except Exception:  # Capacity inventory remains useful during state-store recovery.
            observation = {}
        inventory.update(
            operational_storage_capacity_read_model(inventory, observation)
        )
        policy = mysql_minimal_retention_policy(settings)
        inventory["retentionPolicy"] = {
            "typedbActiveHours": int(settings.get("typedbDataRetentionHours") or 72),
            "typedbWalTriggerMb": int(settings.get("typedbCapacityAutoRotateWalMb") or 4096),
            "typedbRollbackMinutes": int(settings.get("typedbBlueGreenRetiredRetentionMinutes") or 120),
            "notificationPayloadDays": round(policy.terminal_notification_retention_hours / 24),
            "completedWorldProjectionHours": policy.completed_world_projection_retention_hours,
            "completedInferenceDetailDays": round(policy.completed_inference_detail_retention_hours / 24),
            "reasoningCaseDays": round(policy.investment_reasoning_case_retention_hours / 24),
            "statisticalSignalDays": round(policy.statistical_model_signal_snapshot_retention_hours / 24),
            "timeSeriesDays": dict(policy.market_time_series_retention_days),
        }
        return inventory

    readers = {
        "realtime": realtime_status_payload,
        "external": external_data_status_payload,
        "reasoning": ontology_reasoning_status_payload,
        "engine": reasoning_engine_platform_status_payload,
        "timeSeries": time_series_platform_status_payload,
        "storage": storage_payload,
    }
    payloads = {}
    executor = ThreadPoolExecutor(max_workers=len(readers), thread_name_prefix="console-health")
    futures = {key: executor.submit(reader) for key, reader in readers.items()}
    _completed, pending = wait(futures.values(), timeout=8)
    for key, future in futures.items():
        if future in pending:
            payloads[key] = {"status": "unavailable", "error": "status read timed out after 8 seconds"}
            future.cancel()
            continue
        try:
            payloads[key] = future.result()
        except Exception as error:  # noqa: BLE001 - one status source cannot hide the others.
            payloads[key] = {"status": "unavailable", "error": str(error)[:240]}
    executor.shutdown(wait=False, cancel_futures=True)
    return console_read_model_service().operations_health(payloads)


def console_operations_health_api_payload(force: bool = False) -> Dict[str, object]:
    return cached_api_payload(
        OPERATIONS_HEALTH_READ_MODEL,
        "all",
        _console_operations_health_source_payload,
        force=force,
    )
