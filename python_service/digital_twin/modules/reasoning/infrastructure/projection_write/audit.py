"""Audit implementation; facade-independent dependencies."""

from __future__ import annotations
from .audit_ports import AuditPort
from dataclasses import replace
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_projection_audit import (
    OntologyProjectionRun,
    apply_projection_run_identity,
    build_ontology_projection_run,
    complete_ontology_projection_run,
)
from digital_twin.domain.ontology_runtime_operations import (
    build_projection_runtime_observation,
)
from digital_twin.domain.portfolio import AccountSnapshot
from digital_twin.infrastructure.runtime_identity import runtime_identity
from typing import Dict, List


def projection_coordinator_summary(lease: Dict[str, object]) -> Dict[str, object]:
    """Keep TypeDB coordination visible without leaking control JSON."""
    allowed = {
        "acquired",
        "status",
        "coordinator",
        "coordinatorVersion",
        "requestedWorldId",
        "leaseOwner",
        "leaseExpiresAtEpoch",
        "leaseRemainingSeconds",
        "recommendedRetryAfterSeconds",
        "reason",
    }
    return {
        key: value
        for key, value in dict(lease or {}).items()
        if key in allowed and value not in (None, "", [], {})
    }


def begin_projection_audit_run(
    _store: AuditPort,
    snapshot: AccountSnapshot,
    graph: PortfolioOntology,
    material_fingerprint: str,
    abox_snapshot_id: str,
    inference_symbols: List[str],
    rulebox_metadata: Dict[str, object],
    reasoning_context: Dict[str, object] = None,
):
    """Persist source facts before replacing the active TypeDB generation."""
    if not _store.projection_run_store:
        return None, ""
    run = build_ontology_projection_run(
        snapshot,
        graph,
        material_fingerprint,
        abox_snapshot_id,
        _store.active_graph_store_key(),
        target_symbols=inference_symbols,
        rulebox_metadata=rulebox_metadata,
        reasoning_context=reasoning_context,
        execution_namespace=_store.execution_namespace(),
    )
    try:
        _store.projection_run_store.begin(run)
    except (
        Exception
    ) as error:  # noqa: BLE001 - an un-audited generation must not replace the active ABox.
        return None, str(error)[:180]
    apply_projection_run_identity(graph, run.run_id)
    return run, ""


def store_projection_result(
    _store: AuditPort,
    snapshot: AccountSnapshot,
    result: Dict[str, object],
    projection_run: OntologyProjectionRun = None,
) -> None:
    ontology = snapshot.metadata.setdefault("ontology", {})
    # Runtime identity is audit metadata only. It never becomes an ABox
    # fact and therefore cannot influence an investment inference.
    # Cached snapshots may carry the identity of the process that produced
    # the previous generation. This result belongs to the current writer,
    # so stale audit metadata must never win through ``setdefault``.
    result["runtimeIdentity"] = runtime_identity()
    active_key = _store.active_graph_store_key(result)
    result.setdefault("graphStore", active_key)
    result.setdefault("activeGraphStore", active_key)
    if projection_run and _store.projection_run_store:
        try:
            _store.attach_inference_reuse_proof(projection_run, result)
            completed_run = complete_ontology_projection_run(projection_run, result)
            # Keep projection cost, scope impact, native trace coverage,
            # and scoped ABox cleanup in the same durable audit row as
            # the factual source snapshot. This is operational telemetry;
            # it never participates in investment rule evaluation.
            result["runtimeObservation"] = build_projection_runtime_observation(
                completed_run,
                result,
                _store.settings,
            )
            completed_run = replace(
                completed_run,
                result_payload={
                    **dict(completed_run.result_payload or {}),
                    "runtimeObservation": dict(result["runtimeObservation"]),
                },
            )
            complete_with_trace = getattr(
                _store.projection_run_store,
                "complete_with_execution_trace",
                None,
            )
            if callable(complete_with_trace):
                complete_with_trace(completed_run, result)
            else:
                _store.projection_run_store.complete(completed_run)
            result["currentStateFinalization"] = (
                _store.finalize_current_state_transition(
                    completed_run,
                    result,
                )
            )
            result["projectionAudit"] = {
                "status": "recorded",
                "runId": completed_run.run_id,
                "sourceSnapshotRecorded": True,
                "activeAboxSnapshotId": completed_run.active_abox_snapshot_id,
            }
        except (
            Exception
        ) as error:  # noqa: BLE001 - TypeDB state stays observable when final audit sync is retried.
            result["projectionAudit"] = {
                "status": "pending-sync",
                "runId": projection_run.run_id,
                "sourceSnapshotRecorded": True,
                "reason": str(error)[:180],
            }
    result.pop("_ruleResultSlotCatalogRuleIds", None)
    result.pop("_ruleResultSlotRulesHash", None)
    result.pop("_priorRuleStatesBySymbol", None)
    ontology[active_key] = result
    ontology["projection"] = result
    ontology["activeGraphStore"] = active_key
