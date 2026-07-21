import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Dict, Iterable, List

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
    return {
        "saved": bool(values.get("saved")),
        "status": str(values.get("status") or ""),
        "reason": str(values.get("reason") or "")[:500],
        "graphStore": str(values.get("graphStore") or ""),
        "projectionMode": str(values.get("projectionMode") or ""),
        "materialChangeDetected": bool(values.get("materialChangeDetected")),
        "materialFingerprint": str(values.get("materialFingerprint") or ""),
        "aboxSnapshotId": str(values.get("aboxSnapshotId") or ""),
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
        },
        "ruleboxExecution": {
            "status": str(execution.get("status") or ""),
            "reason": str(execution.get("reason") or "")[:500],
            "selectedRuleCount": int(execution.get("selectedRuleCount") or 0),
            "matchedRuleCount": int(execution.get("matchedRuleCount") or 0),
        },
    }


@dataclass(frozen=True)
class OntologyProjectionRun:
    run_id: str
    portfolio_id: str
    account_id: str
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
            "sourceSnapshotSummary": {
                "mode": str(snapshot.mode or ""),
                "status": str(snapshot.status or ""),
                "positionCount": len(snapshot.positions or []),
                "watchlistCount": len(snapshot.watchlist or []),
                "externalSignalKeys": sorted(list((snapshot.external_signals or {}).keys()))[:80],
            },
            "targetSymbols": symbols,
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
    stamp = str(completed_at or utc_now_iso())
    resolved_status = str(values.get("status") or ("ok" if values.get("saved") else run.status))
    activated = bool(values.get("saved")) and resolved_status == "ok"
    return replace(
        run,
        last_observed_at=stamp,
        completed_at=stamp,
        activated_at=stamp if activated else run.activated_at,
        status=resolved_status,
        graph_store=str(values.get("graphStore") or run.graph_store),
        projection_mode=str(values.get("projectionMode") or run.projection_mode),
        active_abox_snapshot_id=str(
            active_pointer.get("aboxSnapshotId")
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
