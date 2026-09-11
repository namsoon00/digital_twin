"""Web cache boundary."""

from digital_twin.infrastructure.stale_read_model import StaleReadModelCache
from typing import Dict


def cached_api_payload(
    cache: StaleReadModelCache,
    key: str,
    loader,
    force: bool = False,
    unavailable_status: str = "unavailable",
    blocking_first_load: bool = True,
) -> Dict[str, object]:
    """Attach freshness metadata to a persistent stale-while-revalidate read model."""

    snapshot = cache.get_or_refresh(
        str(key or "default"),
        loader,
        force=force,
        blocking_first_load=blocking_first_load,
    )
    payload = dict(snapshot.get("payload") or {})
    if not payload:
        payload = {
            "status": "warming" if snapshot.get("refreshing") else unavailable_status,
            "error": str(snapshot.get("lastError") or "읽기 모델을 준비하고 있습니다."),
        }
    payload["readCache"] = {
        "stale": bool(snapshot.get("stale")),
        "ageSeconds": int(snapshot.get("ageSeconds") or 0),
        "refreshing": bool(snapshot.get("refreshing")),
        "lastSuccessAt": str(snapshot.get("lastSuccessAt") or ""),
        "lastError": str(snapshot.get("lastError") or ""),
    }
    if snapshot.get("stale"):
        reported_freshness = payload.get("dataFreshness")
        if isinstance(reported_freshness, dict):
            payload["dataFreshness"] = {
                **reported_freshness,
                "sourceStatus": str(reported_freshness.get("status") or ""),
                "status": "stale",
                "stale": True,
                "reason": "읽기 캐시가 갱신 기한을 초과해 내부 최신성 표기를 사용할 수 없습니다.",
            }
        elif reported_freshness not in (None, ""):
            payload["dataFreshness"] = {
                "sourceStatus": str(reported_freshness),
                "status": "stale",
                "stale": True,
                "reason": "읽기 캐시가 갱신 기한을 초과했습니다.",
            }
        payload["effectiveFreshness"] = {
            "status": "stale",
            "ageSeconds": int(snapshot.get("ageSeconds") or 0),
            "lastSuccessAt": str(snapshot.get("lastSuccessAt") or ""),
            "reason": "마지막 성공 응답을 제공하고 백그라운드에서 갱신 중입니다.",
        }
    return payload
