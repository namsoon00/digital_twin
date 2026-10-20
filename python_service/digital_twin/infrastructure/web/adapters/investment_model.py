"""Web investment model boundary."""

from concurrent.futures import ThreadPoolExecutor, wait
from copy import deepcopy
from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.service_factory import build_investment_brain_service
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


MODEL_COMPONENT_EXECUTOR = ThreadPoolExecutor(max_workers=7, thread_name_prefix="investment-model-read")
MODEL_COMPONENT_BUDGET_SECONDS = 8


def _current_control():
    settings = operational_read_settings()
    settings["mysqlOperationTimeoutSeconds"] = "2"
    return stores.reasoning_engine_registry_store(settings).control().to_dict()


def _investment_model_source_payload() -> Dict[str, object]:
    source_control = _current_control()
    settings = operational_read_settings()
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
            safe_int(settings.get("investmentMessageQualityWindowDays"), 90, 7, 365),
        ),
        "learning": lambda: build_investment_brain_service(settings).learning_proposals(
            limit=100
        ),
    }
    results = {}
    errors = []
    futures = {key: MODEL_COMPONENT_EXECUTOR.submit(loader) for key, loader in loaders.items()}
    _, pending = wait(futures.values(), timeout=MODEL_COMPONENT_BUDGET_SECONDS)
    for key, future in futures.items():
        if future in pending:
            future.cancel()
            results[key] = {}
            errors.append(key + ": 조회 시간이 초과되었습니다.")
            continue
        try:
            results[key] = future.result()
            component = results[key] if isinstance(results[key], dict) else {}
            if (component.get("status") in {"warming", "unavailable", "error"}
                    or (component.get("readCache") or {}).get("stale")
                    or (component.get("readCache") or {}).get("lastError")):
                errors.append(key + ": 최신 자료 확인이 필요합니다.")
        except Exception as error:  # partial diagnostics never certify a release.
            results[key] = {}
            errors.append(key + ": " + str(error)[:180])
    if not results.get("platform"):
        raise RuntimeError("운영 배포 상태를 확인하지 못했습니다.")
    if source_control != _current_control():
        raise RuntimeError("조회 중 운영 배포가 변경되어 다시 확인합니다.")
    payload = investment_model_projection(
        results.get("platform"),
        results.get("rulebox"),
        results.get("catalog"),
        results.get("experiments"),
        settings,
        results.get("timeSeries"),
        results.get("messageQuality"),
        results.get("learning"),
    )
    if (payload["activeRelease"]["deploymentId"] != source_control.get("active_deployment_id")
            or payload["candidate"]["deploymentId"] != source_control.get("candidate_deployment_id")):
        raise RuntimeError("배포 식별자가 일치하지 않아 다시 확인합니다.")
    payload["controlIdentity"] = source_control
    payload["diagnostics"] = {"partial": bool(errors), "errors": errors}
    return payload


def investment_model_api_payload(force: bool = False) -> Dict[str, object]:
    """Check the small live control row; refresh expensive diagnostics off-request."""
    cache = INVESTMENT_MODEL_READ_MODEL
    cached = cache.snapshot("active")
    previous = cached.get("payload") or {}
    error = ""
    try:
        control = _current_control()
    except Exception:
        control = None
        error = "현재 운영 배포 상태를 확인하지 못했습니다."
    same_control = control is not None and previous.get("controlIdentity") == control
    compatible = previous.get("version") == INVESTMENT_MODEL_VERSION
    retry_partial = (previous.get("diagnostics") or {}).get("partial") and cached.get("ageSeconds", 0) >= 10
    refresh_needed = bool(force or cached.get("stale") or cached.get("lastError")
                          or retry_partial or not same_control or not compatible)
    started = cache.refresh_async("active", _investment_model_source_payload) if refresh_needed else False
    current = cache.snapshot("active")
    # A fast background result may already be ready. Never let it bypass the
    # control identity read or mix statistics from a previous deployment.
    previous = current.get("payload") or {}
    same_control = control is not None and previous.get("controlIdentity") == control
    compatible = previous.get("version") == INVESTMENT_MODEL_VERSION
    payload = deepcopy(previous) if same_control and compatible else investment_model_projection({}, {}, {}, {}, {})
    if control is not None and not (same_control and compatible):
        payload["activeRelease"]["deploymentId"] = control.get("active_deployment_id") or ""
        payload["candidate"]["deploymentId"] = control.get("candidate_deployment_id") or ""
    refreshing = bool(started or current.get("refreshing"))
    partial = bool((payload.get("diagnostics") or {}).get("partial"))
    stale = bool(not same_control or not compatible or current.get("stale") or current.get("lastError")
                 or partial or force or refreshing)
    payload["controlIdentity"] = control or {}
    payload["cache"] = {
        "stale": stale,
        "ageSeconds": current.get("ageSeconds", 0),
        "refreshing": refreshing,
        "lastSuccessAt": current.get("lastSuccessAt", "") if same_control else "",
        "lastError": error or current.get("lastError", "") or (
            "일부 검증 자료가 갱신되지 않아 다시 확인합니다." if partial else ""
        ),
        "controlVerified": control is not None,
    }
    if stale:
        payload["status"] = "warming" if refreshing or partial else "unavailable"
        payload["validation"] = {
            **payload.get("validation", {}), "state": "pending", "promotionReady": False,
            "label": "최신 검증 확인 중" if refreshing else "최신 검증 확인 필요",
            "blockers": ["read-model-refresh-pending" if refreshing else "read-model-unavailable"],
        }
        payload["candidate"]["eligibleForPromotion"] = False
        readiness = payload.get("productReadiness") or {}
        payload["productReadiness"] = {
            **readiness, "stage": "checking", "stageLabel": "검증 자료 확인 중",
            "generalAvailabilityReady": False, "closedBetaReady": False, "releaseRecommended": False,
            "gates": [{**gate, "passed": False, "detail": "최신 자료 확인 후 판정합니다."}
                      for gate in readiness.get("gates") or []],
        }
    return payload
