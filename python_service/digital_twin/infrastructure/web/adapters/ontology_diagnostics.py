"""Web ontology diagnostics boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.ontology_graph_store import ontology_repository_from_settings
from digital_twin.infrastructure.runtime_identity import runtime_identity
from digital_twin.infrastructure.service_factory import build_investment_strategy_proposal_service
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.stale_read_model import StaleReadModelCache
from digital_twin.infrastructure.web.adapters.ontology_access import ontology_world_id_from_query
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import now
from digital_twin.infrastructure.web.common import request_bool
from digital_twin.modules.read_models.public import OntologyDiagnosticsService
from typing import Dict
from typing import List
import json


ONTOLOGY_DIAGNOSTICS_READ_MODEL = StaleReadModelCache(
    "ontology-diagnostics",
    ttl_seconds=60,
    retry_cooldown_seconds=20,
)


def ontology_diagnostics_source_payload(
    symbols: List[str],
    limit: int,
    world_id: str,
) -> Dict[str, object]:
    settings = runtime_settings()
    return OntologyDiagnosticsService(
        ontology_repository=ontology_repository_from_settings(settings),
        settings=settings,
        event_log=stores.event_log(settings),
        notification_queue=stores.notification_job_store(settings),
        strategy_proposal_service=build_investment_strategy_proposal_service(settings),
        decision_episode_store=stores.investment_decision_episode_store(settings),
        projection_run_store=stores.ontology_projection_run_store(settings),
        world_projection_outbox=stores.ontology_world_projection_outbox_store(settings),
        inference_detail_outbox=stores.ontology_inference_detail_outbox_store(settings),
        maintenance_state_store=stores.ontology_maintenance_state_store(settings),
        runtime_identity_provider=runtime_identity,
        alert_coverage_provider=lambda: stores.investment_alert_coverage_store(settings).summary(),
    ).status(symbols=symbols, limit=limit, world_id=world_id)


def ontology_diagnostics_cache_key(symbols: List[str], limit: int, world_id: str) -> str:
    return json.dumps({
        "symbols": sorted(str(item or "").upper() for item in symbols or []),
        "limit": int(limit or 0),
        "worldId": str(world_id or ""),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ontology_diagnostics_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    if request_bool(first_query(query, "quick"), False):
        return {
            "status": "deferred",
            "mode": "quick",
            "generatedAt": now(),
            "graphStore": "typedb",
            "reason": "초기 화면은 TypeDB 전체 진단을 실행하지 않습니다. 상세 진단 버튼에서 저장소 행과 추론 상태를 확인합니다.",
            "detailAvailable": True,
        }
    symbols = [
        item.strip().upper()
        for item in str(first_query(query, "symbols") or first_query(query, "symbol") or "").split(",")
        if item.strip()
    ]
    limit = max(1, min(500, int(first_query(query, "limit") or 80)))
    world_id = ontology_world_id_from_query(query)
    key = ontology_diagnostics_cache_key(symbols, limit, world_id)
    cached = ONTOLOGY_DIAGNOSTICS_READ_MODEL.snapshot(key)
    refresh_requested = request_bool(first_query(query, "refresh"), False)
    refresh_started = False
    if refresh_requested or cached.get("stale") or not cached.get("hasData"):
        refresh_started = ONTOLOGY_DIAGNOSTICS_READ_MODEL.refresh_async(
            key,
            lambda: ontology_diagnostics_source_payload(symbols, limit, world_id),
        )
    refreshing = bool(cached.get("refreshing") or refresh_started)
    cache_payload = {
        "stale": bool(cached.get("stale")),
        "ageSeconds": int(cached.get("ageSeconds") or 0),
        "refreshing": refreshing,
        "lastSuccessAt": str(cached.get("lastSuccessAt") or ""),
        "lastAttemptAt": str(cached.get("lastAttemptAt") or ""),
        "lastError": str(cached.get("lastError") or ""),
        "retryAfterSeconds": int(cached.get("retryAfterSeconds") or 0),
    }
    if cached.get("hasData"):
        payload = dict(cached.get("payload") or {})
        payload["cache"] = cache_payload
        return payload
    return {
        "contract": "typedb-ontology-diagnostics-v1",
        "status": "warming" if refreshing else "unavailable",
        "mode": "stale-while-revalidate",
        "generatedAt": now(),
        "activeGraphStore": "typedb",
        "worldId": world_id,
        "reason": (
            "TypeDB 상세 진단을 백그라운드에서 읽고 있습니다. 화면은 완료를 기다리지 않고 자동으로 갱신됩니다."
            if refreshing
            else "TypeDB 상세 진단을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요."
        ),
        "detailAvailable": True,
        "cache": cache_payload,
    }
