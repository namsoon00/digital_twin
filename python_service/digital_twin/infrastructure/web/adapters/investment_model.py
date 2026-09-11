"""Web investment model boundary."""

from concurrent.futures import ThreadPoolExecutor
from digital_twin.infrastructure.service_factory import build_investment_brain_service
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.stale_read_model import StaleReadModelCache
from digital_twin.infrastructure.web.adapters.notification_storage import notification_queue_store
from digital_twin.infrastructure.web.adapters.ontology_catalog import ontology_catalog_api_payload
from digital_twin.infrastructure.web.adapters.ontology_catalog import ontology_rulebox_summary_payload
from digital_twin.infrastructure.web.adapters.ontology_lab import ontology_experiments_status_payload
from digital_twin.infrastructure.web.adapters.platforms import reasoning_engine_platform_status_payload
from digital_twin.infrastructure.web.adapters.platforms import time_series_platform_status_payload
from digital_twin.infrastructure.web.common import safe_int
from digital_twin.modules.model_registry.domain.investment_model import INVESTMENT_MODEL_VERSION
from digital_twin.modules.model_registry.domain.investment_model import investment_model_projection
from typing import Dict


INVESTMENT_MODEL_READ_MODEL = StaleReadModelCache(
    "investment-model",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)


def _investment_model_source_payload() -> Dict[str, object]:
    loaders = {
        "platform": lambda: reasoning_engine_platform_status_payload({
            "historical": ["1"],
        }),
        "timeSeries": time_series_platform_status_payload,
        "rulebox": ontology_rulebox_summary_payload,
        "catalog": lambda: ontology_catalog_api_payload("summary", {}),
        "experiments": ontology_experiments_status_payload,
        "messageQuality": lambda: notification_queue_store().message_quality_summary(
            "local-owner",
            safe_int(runtime_settings().get("investmentMessageQualityWindowDays"), 90, 7, 365),
        ),
        "learning": lambda: build_investment_brain_service().learning_proposals(
            limit=100
        ),
    }
    results = {}
    errors = []
    with ThreadPoolExecutor(max_workers=len(loaders), thread_name_prefix="investment-model-read") as executor:
        futures = {key: executor.submit(loader) for key, loader in loaders.items()}
        for key, future in futures.items():
            try:
                results[key] = future.result()
            except Exception as error:  # noqa: BLE001 - partial model status remains useful.
                results[key] = {}
                errors.append(key + ": " + str(error)[:180])
    payload = investment_model_projection(
        results.get("platform"),
        results.get("rulebox"),
        results.get("catalog"),
        results.get("experiments"),
        runtime_settings(),
        results.get("timeSeries"),
        results.get("messageQuality"),
        results.get("learning"),
    )
    payload["diagnostics"] = {"partial": bool(errors), "errors": errors}
    return payload


def investment_model_api_payload(force: bool = False) -> Dict[str, object]:
    """Return the last model release immediately while refreshing stale sources."""

    key = "active"
    if force:
        refreshed = INVESTMENT_MODEL_READ_MODEL.refresh(key, _investment_model_source_payload)
        payload = dict(refreshed.get("payload") or {})
        payload["cache"] = {
            "stale": False,
            "ageSeconds": 0,
            "refreshing": False,
            "lastSuccessAt": refreshed.get("lastSuccessAt", ""),
        }
        return payload
    cached = INVESTMENT_MODEL_READ_MODEL.snapshot(key)
    cached_payload = dict(cached.get("payload") or {})
    contract_changed = bool(cached.get("hasData")) and str(
        cached_payload.get("version") or ""
    ) != INVESTMENT_MODEL_VERSION
    if contract_changed:
        refreshed = INVESTMENT_MODEL_READ_MODEL.refresh(key, _investment_model_source_payload)
        payload = dict(refreshed.get("payload") or {})
        if str(payload.get("version") or "") != INVESTMENT_MODEL_VERSION:
            payload = investment_model_projection({}, {}, {}, {}, runtime_settings())
            payload["status"] = "warming"
            payload["diagnostics"] = {
                "partial": True,
                "errors": [
                    str(refreshed.get("lastError") or "투자모델 읽기 계약 갱신을 기다리고 있습니다.")
                ],
            }
        payload["cache"] = {
            "stale": False,
            "ageSeconds": refreshed.get("ageSeconds", 0),
            "refreshing": False,
            "contractMigrated": True,
            "lastSuccessAt": refreshed.get("lastSuccessAt", ""),
        }
        return payload
    if cached.get("hasData"):
        refresh_started = False
        if cached.get("stale"):
            refresh_started = INVESTMENT_MODEL_READ_MODEL.refresh_async(key, _investment_model_source_payload)
        payload = dict(cached.get("payload") or {})
        payload["cache"] = {
            "stale": bool(cached.get("stale")),
            "ageSeconds": cached.get("ageSeconds", 0),
            "refreshing": bool(cached.get("refreshing") or refresh_started),
            "lastSuccessAt": cached.get("lastSuccessAt", ""),
        }
        return payload
    started = INVESTMENT_MODEL_READ_MODEL.refresh_async(key, _investment_model_source_payload)
    current = INVESTMENT_MODEL_READ_MODEL.snapshot(key)
    payload = investment_model_projection({}, {}, {}, {}, runtime_settings())
    payload["status"] = "warming" if started or current.get("refreshing") else "unavailable"
    payload["diagnostics"] = {
        "partial": True,
        "errors": [current.get("lastError")] if current.get("lastError") else [],
    }
    payload["cache"] = {
        "stale": False,
        "ageSeconds": 0,
        "refreshing": bool(started or current.get("refreshing")),
        "lastSuccessAt": "",
    }
    return payload
