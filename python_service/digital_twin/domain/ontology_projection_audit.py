import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Dict, Iterable, List

from .ontology_change_impact import compact_inference_impact_plan
from .ontology_contracts import PortfolioOntology
from .portfolio import AccountSnapshot, utc_now_iso


def _json_payload(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash_payload(value: object) -> str:
    return hashlib.sha256(_json_payload(value).encode("utf-8")).hexdigest()


def _clean_symbols(symbols: Iterable[object]) -> List[str]:
    return sorted({
        str(symbol or "").upper().strip()
        for symbol in symbols or []
        if str(symbol or "").strip()
    })


def projection_source_snapshot(snapshot: AccountSnapshot) -> Dict[str, object]:
    """Return the source payload needed to reproduce a material ABox generation.

    Projection-only state can contain a previous monitor snapshot, an existing
    graph result, or rendered AI data. Those values are derived after source
    collection and must not become a recursive source-of-truth payload.
    """
    payload = snapshot.to_monitor_state()
    metadata = dict(payload.get("metadata") or {})
    for key in ["previousMonitorState", "monitorStateHistory", "ontology"]:
        metadata.pop(key, None)
    payload["metadata"] = metadata
    return payload


def projection_source_snapshot_fingerprint(snapshot: AccountSnapshot) -> str:
    return _hash_payload(projection_source_snapshot(snapshot))


def projection_result_summary(result: Dict[str, object]) -> Dict[str, object]:
    """Persist a bounded audit payload instead of another full graph copy."""
    values = dict(result or {})
    inference = dict(values.get("inferenceBox") or {})
    execution = dict(values.get("ruleboxExecution") or {})
    verification = dict(values.get("aboxPersistenceVerification") or {})
    active_pointer = dict(verification.get("activePointer") or {})
    activation = dict(verification.get("activation") or {})
    cleanup = dict(verification.get("candidateCleanup") or {})
    retired_cleanup = dict(verification.get("retiredActiveCleanup") or {})
    impact_plan = compact_inference_impact_plan(values.get("inferenceImpactPlan") or {})
    projection_scope = dict(values.get("projectionScope") or {})
    ontology_world = dict(values.get("ontologyWorld") or {})
    market_world = dict(values.get("marketWorld") or {})
    return {
        "saved": bool(values.get("saved")),
        "status": str(values.get("status") or ""),
        "reason": str(values.get("reason") or "")[:500],
        "graphStore": str(values.get("graphStore") or ""),
        "projectionMode": str(values.get("projectionMode") or ""),
        "materialChangeDetected": bool(values.get("materialChangeDetected")),
        "materialFingerprint": str(values.get("materialFingerprint") or ""),
        "aboxSnapshotId": str(values.get("aboxSnapshotId") or ""),
        "world": {
            "tenantId": str(ontology_world.get("tenantId") or ""),
            "worldId": str(ontology_world.get("worldId") or projection_scope.get("worldId") or ""),
            "worldType": str(ontology_world.get("worldType") or ""),
            "marketWorldId": str(market_world.get("worldId") or projection_scope.get("marketWorldId") or ""),
        },
        "scopeTopologyVersion": str(projection_scope.get("scopeTopologyVersion") or ""),
        "scopeFamilyCounts": dict(projection_scope.get("scopeFamilyCounts") or {}),
        "inferenceImpactPlan": impact_plan,
        "entityCount": int(values.get("entityCount") or 0),
        "relationCount": int(values.get("relationCount") or 0),
        "activeAbox": {
            "snapshotId": str(active_pointer.get("aboxSnapshotId") or ""),
            "status": str(active_pointer.get("status") or ""),
            "projectionRunId": str(active_pointer.get("projectionRunId") or ""),
        },
        "activation": {
            "status": str(activation.get("status") or ""),
            "snapshotId": str(activation.get("snapshotId") or ""),
            "atomic": bool(activation.get("atomic")),
        },
        "candidateCleanup": {
            "status": str(cleanup.get("status") or ""),
            "removedCount": len(cleanup.get("removedCandidateSnapshotIds") or []),
            "remainingInactiveCount": int(cleanup.get("remainingInactiveCandidateCount") or 0),
        },
        "retiredActiveCleanup": {
            "status": str(retired_cleanup.get("status") or ""),
            "snapshotId": str(retired_cleanup.get("aboxSnapshotId") or ""),
            "deletedBatchCount": int(retired_cleanup.get("deletedBatchCount") or 0),
        },
        "inferenceBox": {
            "status": str(inference.get("status") or ""),
            "generationId": str(inference.get("inferenceGenerationId") or ""),
            "sourceAboxSnapshotId": str(inference.get("sourceAboxSnapshotId") or ""),
            "targetSymbols": _clean_symbols(inference.get("targetSymbols") or []),
            "relationCount": int(inference.get("relationCount") or len(inference.get("relations") or [])),
            "traceCount": len(inference.get("traces") or []),
            "generationAligned": bool(inference.get("generationAligned")),
            "nativeTypeDbReasoningUsed": bool(inference.get("nativeTypeDbReasoningUsed")),
            "reasoningMode": str(inference.get("reasoningMode") or ""),
        },
        "ruleboxExecution": {
            "status": str(execution.get("status") or ""),
            "reason": str(execution.get("reason") or "")[:500],
            "selectedRuleCount": int(execution.get("selectedRuleCount") or 0),
            "matchedRuleCount": int(execution.get("matchedRuleCount") or 0),
            "typedbNativeRuleExecutedCount": int(execution.get("typedbNativeRuleExecutedCount") or 0),
            "typedbNativeRuleMatchedCount": int(execution.get("typedbNativeRuleMatchedCount") or 0),
            "nativeRuleSelectionApplied": bool(execution.get("nativeRuleSelectionApplied")),
            "nativeRuleSelectionFallbackReason": str(execution.get("nativeRuleSelectionFallbackReason") or ""),
            "nativeRuleSelectionCandidateCount": int(execution.get("nativeRuleSelectionCandidateCount") or 0),
            "nativeRuleSelectionPriorMatchedCount": int(execution.get("nativeRuleSelectionPriorMatchedCount") or 0),
            "nativeRuleSelectionExecutedCount": int(execution.get("nativeRuleSelectionExecutedCount") or 0),
            "nativeRuleSelectionDeferredCount": int(execution.get("nativeRuleSelectionDeferredCount") or 0),
            "sourceAboxGenerationMode": str(execution.get("sourceAboxGenerationMode") or ""),
            "sourceAboxGenerationValid": bool(execution.get("sourceAboxGenerationValid")),
            "sourceAboxMembershipValidation": str(execution.get("sourceAboxMembershipValidation") or ""),
        },
    }


@dataclass(frozen=True)
class OntologyProjectionRun:
    run_id: str
    portfolio_id: str
    account_id: str
    tenant_id: str
    world_id: str
    world_type: str
    market_world_id: str
    source_snapshot_at: str
    source_snapshot_fingerprint: str
    first_observed_at: str
    last_observed_at: str
    started_at: str
    completed_at: str
    activated_at: str
    status: str
    graph_store: str
    projection_mode: str
    material_fingerprint: str
    abox_snapshot_id: str
    active_abox_snapshot_id: str
    tbox_version: str
    tbox_fingerprint: str
    rulebox_rules_hash: str
    entity_count: int
    relation_count: int
    inference_generation_id: str
    inference_status: str
    source_symbols: List[str]
    context_payload: Dict[str, object]
    result_payload: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def projection_run_from_payload(payload: Dict[str, object]) -> OntologyProjectionRun:
    """Rehydrate one durable audit row without making MySQL a domain dependency."""
    values = dict(payload or {})

    def value(snake_case: str, camel_case: str = "", fallback: object = ""):
        return values.get(camel_case or snake_case, values.get(snake_case, fallback))

    def integer(snake_case: str, camel_case: str = "") -> int:
        try:
            return int(value(snake_case, camel_case, 0) or 0)
        except (TypeError, ValueError):
            return 0

    source_symbols = value("source_symbols", "sourceSymbols", [])
    context_payload = value("context_payload", "context", {})
    result_payload = value("result_payload", "result", {})
    return OntologyProjectionRun(
        run_id=str(value("run_id", "runId") or ""),
        portfolio_id=str(value("portfolio_id", "portfolioId") or ""),
        account_id=str(value("account_id", "accountId") or ""),
        tenant_id=str(value("tenant_id", "tenantId") or ""),
        world_id=str(value("world_id", "worldId") or ""),
        world_type=str(value("world_type", "worldType") or ""),
        market_world_id=str(value("market_world_id", "marketWorldId") or ""),
        source_snapshot_at=str(value("source_snapshot_at", "sourceSnapshotAt") or ""),
        source_snapshot_fingerprint=str(value("source_snapshot_fingerprint", "sourceSnapshotFingerprint") or ""),
        first_observed_at=str(value("first_observed_at", "firstObservedAt") or ""),
        last_observed_at=str(value("last_observed_at", "lastObservedAt") or ""),
        started_at=str(value("started_at", "startedAt") or ""),
        completed_at=str(value("completed_at", "completedAt") or ""),
        activated_at=str(value("activated_at", "activatedAt") or ""),
        status=str(value("status") or ""),
        graph_store=str(value("graph_store", "graphStore") or ""),
        projection_mode=str(value("projection_mode", "projectionMode") or ""),
        material_fingerprint=str(value("material_fingerprint", "materialFingerprint") or ""),
        abox_snapshot_id=str(value("abox_snapshot_id", "aboxSnapshotId") or ""),
        active_abox_snapshot_id=str(value("active_abox_snapshot_id", "activeAboxSnapshotId") or ""),
        tbox_version=str(value("tbox_version", "tboxVersion") or ""),
        tbox_fingerprint=str(value("tbox_fingerprint", "tboxFingerprint") or ""),
        rulebox_rules_hash=str(value("rulebox_rules_hash", "ruleboxRulesHash") or ""),
        entity_count=integer("entity_count", "entityCount"),
        relation_count=integer("relation_count", "relationCount"),
        inference_generation_id=str(value("inference_generation_id", "inferenceGenerationId") or ""),
        inference_status=str(value("inference_status", "inferenceStatus") or ""),
        source_symbols=list(source_symbols) if isinstance(source_symbols, list) else [],
        context_payload=dict(context_payload) if isinstance(context_payload, dict) else {},
        result_payload=dict(result_payload) if isinstance(result_payload, dict) else {},
    )


def build_ontology_projection_run(
    snapshot: AccountSnapshot,
    graph: PortfolioOntology,
    material_fingerprint: str,
    abox_snapshot_id: str,
    graph_store: str,
    target_symbols: Iterable[object] = None,
    rulebox_metadata: Dict[str, object] = None,
    started_at: str = "",
) -> OntologyProjectionRun:
    worldview = dict(getattr(graph, "worldview", {}) or {})
    active_tbox = dict(worldview.get("activeTBox") or {})
    source_snapshot = projection_source_snapshot(snapshot)
    source_fingerprint = _hash_payload(source_snapshot)
    symbols = _clean_symbols(target_symbols or [
        getattr(item, "symbol", "")
        for item in list(snapshot.positions or []) + list(snapshot.watchlist or [])
        if not item.is_cash()
    ])
    stamp = str(started_at or utc_now_iso())
    # Material fingerprints can recur after an intervening market move. Keep
    # every activation occurrence for audit, rather than overwriting the old
    # record merely because its facts happen to match again.
    run_seed = "|".join([
        str(worldview.get("worldId") or ""),
        str(snapshot.account_id or "account"),
        str(material_fingerprint or ""),
        str(abox_snapshot_id or ""),
        stamp,
    ])
    run_id = "ontology-projection:" + hashlib.sha256(run_seed.encode("utf-8")).hexdigest()[:24]
    entity_count = len([
        item for item in graph.entities
        if str((item.properties or {}).get("ontologyBox") or "ABox") == "ABox"
    ])
    relation_count = len([
        item for item in graph.relations
        if str((item.properties or {}).get("ontologyBox") or "ABox") == "ABox"
    ])
    rulebox = dict(rulebox_metadata or {})
    return OntologyProjectionRun(
        run_id=run_id,
        portfolio_id=str(graph.portfolio_id or snapshot.account_id or ""),
        account_id=str(snapshot.account_id or ""),
        tenant_id=str(worldview.get("tenantId") or ""),
        world_id=str(worldview.get("worldId") or ""),
        world_type=str(worldview.get("worldType") or ""),
        market_world_id=str(worldview.get("marketWorldId") or ""),
        source_snapshot_at=str(snapshot.generated_at or ""),
        source_snapshot_fingerprint=source_fingerprint,
        first_observed_at=stamp,
        last_observed_at=stamp,
        started_at=stamp,
        completed_at="",
        activated_at="",
        status="projecting",
        graph_store=str(graph_store or "typedb"),
        projection_mode=str(worldview.get("runtimeProjectionMode") or "abox-facts-only"),
        material_fingerprint=str(material_fingerprint or ""),
        abox_snapshot_id=str(abox_snapshot_id or ""),
        active_abox_snapshot_id="",
        tbox_version=str(active_tbox.get("version") or active_tbox.get("tboxVersion") or ""),
        tbox_fingerprint=str(active_tbox.get("fingerprint") or active_tbox.get("tboxFingerprint") or ""),
        rulebox_rules_hash=str(rulebox.get("ruleboxRulesHash") or rulebox.get("rulesHash") or ""),
        entity_count=entity_count,
        relation_count=relation_count,
        inference_generation_id="",
        inference_status="",
        source_symbols=symbols,
        context_payload={
            "sourceSnapshotFingerprint": source_fingerprint,
            "sourceSnapshotReference": {
                "accountId": str(snapshot.account_id or ""),
                "generatedAt": str(snapshot.generated_at or ""),
                "store": "monitor_snapshot_history",
            },
            "world": {
                "tenantId": str(worldview.get("tenantId") or ""),
                "worldId": str(worldview.get("worldId") or ""),
                "worldType": str(worldview.get("worldType") or ""),
                "marketWorldId": str(worldview.get("marketWorldId") or ""),
            },
            "sourceSnapshotSummary": {
                "mode": str(snapshot.mode or ""),
                "status": str(snapshot.status or ""),
                "positionCount": len(snapshot.positions or []),
                "watchlistCount": len(snapshot.watchlist or []),
                "externalSignalKeys": sorted(list((snapshot.external_signals or {}).keys()))[:80],
            },
            "targetSymbols": symbols,
            "scopeTopology": {
                "version": str(worldview.get("scopeTopologyVersion") or ""),
                "scopeCount": len(worldview.get("scopePlan") or []),
                "scopeFamilyCounts": dict(worldview.get("scopeFamilyCounts") or {}),
                "scopeDelta": dict(worldview.get("scopeDelta") or {}),
                "inferenceImpactPlan": compact_inference_impact_plan(worldview.get("inferenceImpactPlan") or {}),
            },
            "tbox": {
                "version": str(active_tbox.get("version") or active_tbox.get("tboxVersion") or ""),
                "fingerprint": str(active_tbox.get("fingerprint") or active_tbox.get("tboxFingerprint") or ""),
            },
        },
        result_payload={},
    )


def complete_ontology_projection_run(
    run: OntologyProjectionRun,
    result: Dict[str, object],
    completed_at: str = "",
) -> OntologyProjectionRun:
    values = dict(result or {})
    summary = projection_result_summary(values)
    inference = dict(values.get("inferenceBox") or {})
    verification = dict(values.get("aboxPersistenceVerification") or {})
    active_pointer = dict(verification.get("activePointer") or {})
    activation = dict(verification.get("activation") or {})
    stamp = str(completed_at or utc_now_iso())
    resolved_status = str(values.get("status") or ("ok" if values.get("saved") else run.status))
    activated = bool(values.get("saved")) and resolved_status == "ok"
    inference_source_abox = str(inference.get("sourceAboxSnapshotId") or "").strip()
    inference_is_aligned = bool(inference.get("generationAligned")) and bool(inference.get("nativeTypeDbReasoningUsed"))
    verified_active_abox_snapshot_id = str(active_pointer.get("aboxSnapshotId") or "").strip()
    if not verified_active_abox_snapshot_id:
        activation_status = str(activation.get("status") or "").strip().lower()
        if activation_status in {"activated", "recovered-after-runtime-interruption"}:
            verified_active_abox_snapshot_id = str(activation.get("snapshotId") or "").strip()
    if not verified_active_abox_snapshot_id and inference_is_aligned:
        verified_active_abox_snapshot_id = inference_source_abox
    return replace(
        run,
        last_observed_at=stamp,
        completed_at=stamp,
        activated_at=stamp if activated else run.activated_at,
        status=resolved_status,
        graph_store=str(values.get("graphStore") or run.graph_store),
        projection_mode=str(values.get("projectionMode") or run.projection_mode),
        active_abox_snapshot_id=str(
            verified_active_abox_snapshot_id
            or values.get("aboxSnapshotId")
            or run.active_abox_snapshot_id
            or ""
        ),
        inference_generation_id=str(inference.get("inferenceGenerationId") or run.inference_generation_id),
        inference_status=str(inference.get("status") or run.inference_status),
        result_payload=summary,
    )


def apply_projection_run_identity(graph: PortfolioOntology, run_id: str) -> PortfolioOntology:
    clean_run_id = str(run_id or "").strip()
    if not clean_run_id:
        return graph
    for item in graph.entities:
        if str((item.properties or {}).get("ontologyBox") or "ABox") == "ABox":
            item.properties["projectionRunId"] = clean_run_id
    for item in graph.relations:
        if str((item.properties or {}).get("ontologyBox") or "ABox") == "ABox":
            item.properties["projectionRunId"] = clean_run_id
    for item in graph.evidence:
        if str((item.value or {}).get("ontologyBox") or "ABox") == "ABox":
            item.value["projectionRunId"] = clean_run_id
    graph.worldview["projectionRunId"] = clean_run_id
    return graph
