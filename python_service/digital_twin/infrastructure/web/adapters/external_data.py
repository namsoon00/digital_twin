"""Web external data boundary."""

from digital_twin.infrastructure.service_factory import build_external_data_collection_runner
from digital_twin.infrastructure.service_factory import build_news_analysis_enrichment_runner
from digital_twin.infrastructure.stale_read_model import StaleReadModelCache
from digital_twin.infrastructure.web.cache import cached_api_payload
from typing import Dict


EXTERNAL_DATA_STATUS_READ_MODEL = StaleReadModelCache(
    "external-data-status",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)


def _external_data_status_source_payload() -> Dict[str, object]:
    try:
        payload = dict(build_external_data_collection_runner().status() or {})
        try:
            payload["newsAnalysis"] = build_news_analysis_enrichment_runner().status()
        except Exception as error:  # noqa: BLE001 - official collection status remains independently useful.
            payload["newsAnalysis"] = {"status": "unavailable", "error": str(error)[:240]}
        return payload
    except Exception as error:  # noqa: BLE001 - status remains inspectable while MySQL starts.
        return {
            "enabled": False,
            "status": "unavailable",
            "error": str(error)[:500],
        }


def external_data_status_payload(force: bool = False) -> Dict[str, object]:
    def load() -> Dict[str, object]:
        payload = _external_data_status_source_payload()
        if str(payload.get("status") or "").lower() in {"error", "unavailable"}:
            raise RuntimeError(str(payload.get("error") or "외부 데이터 상태를 읽지 못했습니다."))
        return payload

    return cached_api_payload(
        EXTERNAL_DATA_STATUS_READ_MODEL,
        "all-providers",
        load,
        force=force,
    )
