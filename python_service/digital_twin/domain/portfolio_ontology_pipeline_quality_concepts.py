from typing import Dict

from .ontology_contracts import PortfolioOntology
from .ontology_schema import add_entity, add_relation
from .portfolio import Position
from .portfolio_ontology_market_concepts import symbol_key


PIPELINE_QUALITY_STATES = {"degraded", "disabled", "failed", "stale"}


def market_snapshot_health(runtime_context: Dict[str, object]) -> Dict[str, object]:
    payload = runtime_context.get("dataPipelineHealth") if isinstance(runtime_context, dict) else {}
    pipelines = payload.get("pipelines") if isinstance(payload, dict) else {}
    item = pipelines.get("marketSnapshot") if isinstance(pipelines, dict) else {}
    return dict(item or {}) if isinstance(item, dict) else {}


def add_position_pipeline_quality_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    position: Position,
    runtime_context: Dict[str, object],
) -> None:
    health = market_snapshot_health(runtime_context)
    state = str(health.get("state") or "").strip().lower()
    if state not in PIPELINE_QUALITY_STATES:
        return
    symbol = symbol_key(position)
    if not symbol:
        return
    failed = state in {"disabled", "failed"}
    stale = state == "stale"
    data_state = "unavailable" if failed else ("insufficient" if stale else "partial")
    evidence_role = "blocking" if failed else "risk"
    review_level = "blocked" if failed else "check"
    label = (position.name or symbol) + " 시장 데이터 수집 상태"
    quality_id = add_entity(graph, "missing-data", symbol + ":market-snapshot", label, {
        "tboxClass": "MissingData",
        "tboxClasses": ["Observation", "DataQuality", "MissingData", "DataQualitySignal", "DataPipelineHealth"],
        "symbol": symbol,
        "pipeline": "marketSnapshot",
        "pipelineState": state,
        "reasonCode": str(health.get("reasonCode") or ""),
        "reason": str(health.get("reason") or ""),
        "targetCount": int(health.get("targetCount") or 0),
        "fetchedCount": int(health.get("fetchedCount") or 0),
        "savedCount": int(health.get("savedCount") or 0),
        "providerFailureCount": int(health.get("providerFailureCount") or 0),
        "freshnessRequired": True,
        "freshnessStatus": "stale" if stale else ("unknown" if failed else "aging"),
        "freshnessGateReason": str(health.get("reason") or "시장 데이터 수집 상태를 확인해야 합니다."),
        "judgementEvidenceUsable": not failed,
        "dataState": data_state,
        "dataScope": "market-snapshot",
    })
    properties = {
        "source": "market-snapshot-pipeline-health",
        "polarity": "blocking" if failed else "risk",
        "evidenceRole": evidence_role,
        "reviewLevel": review_level,
        "dataState": data_state,
        "dataScope": "market-snapshot",
        "scope": "market-snapshot",
        "aiInfluenceLabel": "시장 데이터 수집 " + state,
        "freshnessRequired": True,
        "freshnessStatus": "stale" if stale else ("unknown" if failed else "aging"),
        "freshnessGateReason": str(health.get("reason") or ""),
    }
    add_relation(graph, stock_id, quality_id, "HAS_DATA_QUALITY", weight=1.0, properties=properties)
    add_relation(graph, stock_id, quality_id, "HAS_OBSERVATION", weight=1.0, properties=properties)
    add_relation(graph, quality_id, stock_id, "WEIGHTED_BY_DATA_STATE", weight=1.0, properties=properties)
