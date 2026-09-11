"""Create the durable source audit before changing any active generation."""

from __future__ import annotations
from typing import Optional, Union
from digital_twin.modules.portfolio.contracts import AccountSnapshot
from typing import Dict, List
import time


from .stage_results import CompletedProjection, CreateAuditResult
from .create_audit_ports import CreateAuditPort
from digital_twin.modules.reasoning.domain.ontology_validator import OntologyValidationReport
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology


def create_audit(
    _store: CreateAuditPort,
    compact_reasoning_context: Dict[str, object],
    inference_symbols: List[str],
    material_fingerprint: str,
    material_snapshot_id: str,
    persistence_graph: PortfolioOntology,
    rulebox_bootstrap: Dict[str, object],
    runtime_stages: Dict[str, int],
    snapshot: AccountSnapshot,
    validation: OntologyValidationReport,
) -> Union[CreateAuditResult, CompletedProjection]:
    projection_audit_started = time.perf_counter()
    projection_run, audit_error = _store.begin_projection_audit_run(
        snapshot,
        persistence_graph,
        material_fingerprint,
        material_snapshot_id,
        inference_symbols=inference_symbols,
        rulebox_metadata=rulebox_bootstrap,
        reasoning_context=compact_reasoning_context,
    )
    runtime_stages["projectionAuditCreateMs"] = int(
        (time.perf_counter() - projection_audit_started) * 1000
    )
    if audit_error:
        result = {
            "saved": False,
            "status": "source-audit-failed",
            "reason": "MySQL source audit must succeed before the active ABox can change: "
            + audit_error,
            "graphStore": _store.active_graph_store_key(),
            "materialFingerprint": material_fingerprint,
            "aboxSnapshotId": material_snapshot_id,
            "materialChangeDetected": True,
            "preservedActiveGeneration": True,
            "aboxValidation": validation.to_dict(),
        }
        _store.store_projection_result(snapshot, result)
        return CompletedProjection(result)

    return CreateAuditResult(
        projection_run=projection_run,
    )
