"""Graph save implementation; facade-independent dependencies."""

from __future__ import annotations
from .graph_save_ports import GraphSavePort, SaveGraphBindings
from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.infrastructure.graph_store_lifecycle import (
    graph_box_entity_counts,
    graph_box_relation_counts,
)
from typing import Dict
import copy
import json
import time


def save_graph(
    _store: GraphSavePort, graph: PortfolioOntology, *, _bindings: SaveGraphBindings
) -> Dict[str, object]:
    if not _store.address:
        return _bindings.NullTypeDBOntologyGraphRepository().save_graph(graph)
    imported = _store.driver_imports()
    if imported[0] is None:
        return _store.driver_missing_result(imported[1], graph)
    boxes = _bindings.node_boxes(graph)
    if "ABox" in boxes and _store.is_scoped_abox_graph(graph):
        return _store.save_scoped_abox_graph(graph, boxes)
    abox_projection_verification: Dict[str, object] = {}
    abox_persistence_timing: Dict[str, object] = {}
    try:

        def operation():
            nonlocal abox_projection_verification, abox_persistence_timing
            with _bindings.typedb_operation_timeout(
                _store.write_operation_timeout_seconds(), "TypeDB graph save"
            ):
                driver = _store.open_driver(imported)
            try:
                _store.ensure_database(driver)
                _store.ensure_schema(driver, imported)
                expected_entity_count = 0
                expected_relation_count = 0
                if "ABox" in boxes:
                    abox_started_at = time.monotonic()
                    abox_persistence_timing = {"startedAt": _bindings.utc_now()}
                    node_rows, relation_rows = _store.graph_persistence_rows(graph)
                    expected_entity_count = len(node_rows)
                    expected_relation_count = len(relation_rows)
                    candidate_graph = _store.abox_candidate_graph(graph)
                    snapshot_id = _store.abox_snapshot_id_from_graph(candidate_graph)
                    abox_persistence_timing["candidateAboxSnapshotId"] = snapshot_id
                    if not snapshot_id:
                        abox_projection_verification = {
                            "status": "skipped",
                            "reason": "ABox material identity is unavailable.",
                        }
                    else:
                        active_before = _store.active_abox_metadata()
                        active_snapshot_id = str(
                            active_before.get("aboxSnapshotId") or ""
                        ).strip()
                        if active_snapshot_id != snapshot_id:
                            cleanup_started_at = time.monotonic()
                            try:
                                incremental_cleanup = _store.drain_inactive_abox_generations_incrementally(
                                    driver,
                                    imported,
                                    active_snapshot_id,
                                    excluded_snapshot_ids=[snapshot_id],
                                )
                            except (
                                Exception
                            ) as error:  # noqa: BLE001 - maintenance cannot block a new live generation.
                                incremental_cleanup = {
                                    "status": "deferred",
                                    "reason": str(error)[:180],
                                    "activeAboxSnapshotId": active_snapshot_id,
                                }
                            abox_persistence_timing["incrementalCleanupMs"] = round(
                                (time.monotonic() - cleanup_started_at) * 1000,
                                1,
                            )
                            abox_persistence_timing["incrementalCleanup"] = (
                                incremental_cleanup
                            )
                            # A candidate shares the physical ABox box with the
                            # active generation, but storage IDs include the
                            # snapshot. Clear only an interrupted retry of this
                            # exact candidate; never touch the live generation.
                            clear_started_at = time.monotonic()
                            _store.delete_box_snapshot_rows_in_batches(
                                driver,
                                imported,
                                "ABox",
                                snapshot_id,
                            )
                            abox_persistence_timing["candidateRetryClearMs"] = round(
                                (time.monotonic() - clear_started_at) * 1000,
                                1,
                            )
                            candidate_write_started_at = time.monotonic()
                            _store.write_graph(
                                driver, imported, candidate_graph, delete_boxes=[]
                            )
                            abox_persistence_timing["candidateWriteMs"] = round(
                                (time.monotonic() - candidate_write_started_at) * 1000,
                                1,
                            )
                            marker_graph = _store.abox_projection_marker_graph(
                                candidate_graph,
                                expected_entity_count,
                                expected_relation_count,
                            )
                            if not marker_graph.entities:
                                raise RuntimeError(
                                    "ABox completion marker is unavailable."
                                )
                            marker_write_started_at = time.monotonic()
                            _store.write_graph(
                                driver, imported, marker_graph, delete_boxes=[]
                            )
                            abox_persistence_timing["markerWriteMs"] = round(
                                (time.monotonic() - marker_write_started_at) * 1000,
                                1,
                            )
                        verification_started_at = time.monotonic()
                        candidate_verification = _store.verify_abox_projection(
                            candidate_graph,
                            expected_entity_count,
                            expected_relation_count,
                        )
                        abox_persistence_timing["candidateVerificationMs"] = round(
                            (time.monotonic() - verification_started_at) * 1000,
                            1,
                        )
                        if candidate_verification.get("status") != "ok":
                            raise RuntimeError(
                                "ABox candidate verification failed: "
                                + json.dumps(
                                    candidate_verification,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                )
                            )
                        if active_snapshot_id != snapshot_id:
                            pointer_graph = _store.abox_active_pointer_graph(
                                candidate_graph,
                                previous_snapshot_id=active_snapshot_id,
                            )
                            pointer_write_started_at = time.monotonic()
                            _store.write_graph(
                                driver,
                                imported,
                                pointer_graph,
                                delete_boxes=["ABoxControl"],
                            )
                            abox_persistence_timing["pointerWriteMs"] = round(
                                (time.monotonic() - pointer_write_started_at) * 1000,
                                1,
                            )
                            # Keep the prior active generation until the
                            # new ABox has produced an aligned native
                            # InferenceBox. The projection recorder either
                            # finalizes this retention after success or
                            # restores this pointer after a rule failure.
                        abox_projection_verification = {
                            **_store.verify_abox_projection(
                                candidate_graph,
                                expected_entity_count,
                                expected_relation_count,
                            ),
                            "activePointer": _store.active_abox_metadata(),
                            "activation": {
                                "status": (
                                    "unchanged"
                                    if active_snapshot_id == snapshot_id
                                    else "activated"
                                ),
                                "snapshotId": snapshot_id,
                                "previousSnapshotId": active_snapshot_id,
                                "atomic": True,
                                "finalizationRequired": bool(
                                    active_snapshot_id
                                    and active_snapshot_id != snapshot_id
                                ),
                            },
                        }
                        abox_persistence_timing["totalMs"] = round(
                            (time.monotonic() - abox_started_at) * 1000,
                            1,
                        )
                        abox_projection_verification["timing"] = dict(
                            abox_persistence_timing
                        )
                        if abox_projection_verification.get("status") != "ok":
                            raise RuntimeError(
                                "ABox activation verification failed: "
                                + json.dumps(
                                    abox_projection_verification,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                )
                            )
                non_abox_boxes = [box for box in boxes if box != "ABox"]
                if non_abox_boxes:
                    _store.write_graph(
                        driver,
                        imported,
                        _store.graph_for_boxes(graph, non_abox_boxes),
                        delete_boxes=non_abox_boxes,
                    )
            finally:
                _store.close_driver(driver)

        _store.with_typedb_retries(operation)
    except (
        Exception
    ) as error:  # noqa: BLE001 - graph-store persistence must not block monitoring.
        # Candidate writes never replace the active pointer until their
        # own marker and row counts verify. Preserve both the active ABox
        # and a failed candidate for diagnosis; a retry clears only that
        # candidate snapshot before writing it again.
        cleanup = (
            {
                "status": "preserved-active-generation",
                "activeAboxSnapshotId": str(
                    _store.active_abox_metadata().get("aboxSnapshotId") or ""
                ),
            }
            if "ABox" in boxes
            else {}
        )
        return {
            "configured": True,
            "saved": False,
            "status": "error",
            "graphStore": "typedb",
            "reason": str(error)[:240],
            "partialWriteCleanup": cleanup,
            "entityCount": len(graph.entities),
            "relationCount": len(graph.relations),
            "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
            "aboxPersistenceVerification": abox_projection_verification,
            "aboxPersistenceTiming": abox_persistence_timing,
        }
    _store._last_graph = copy.deepcopy(graph)
    box_entity_counts = graph_box_entity_counts(graph)
    box_relation_counts = graph_box_relation_counts(graph)
    return {
        "configured": True,
        "saved": True,
        "status": "ok",
        "graphStore": "typedb",
        "schemaPrepared": True,
        "address": _store.address,
        "database": _store.database,
        "entityCount": len(graph.entities),
        "relationCount": len(graph.relations),
        "tboxEntityCount": box_entity_counts.get("TBox", 0),
        "aboxEntityCount": box_entity_counts.get("ABox", 0),
        "ruleBoxEntityCount": box_entity_counts.get("RuleBox", 0),
        "languageGovernanceEntityCount": box_entity_counts.get("LanguageGovernance", 0),
        "inferenceBoxEntityCount": box_entity_counts.get("InferenceBox", 0),
        "tboxRelationCount": box_relation_counts.get("TBox", 0),
        "aboxRelationCount": box_relation_counts.get("ABox", 0),
        "ruleBoxRelationCount": box_relation_counts.get("RuleBox", 0),
        "languageGovernanceRelationCount": box_relation_counts.get(
            "LanguageGovernance", 0
        ),
        "inferenceBoxRelationCount": box_relation_counts.get("InferenceBox", 0),
        "evidenceCount": len(graph.evidence),
        "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
        "aboxPersistenceVerification": abox_projection_verification,
        "aboxPersistenceTiming": abox_persistence_timing,
    }


def driver_missing_result(
    _store: GraphSavePort, error: Exception, graph: PortfolioOntology
) -> Dict[str, object]:
    return {
        "configured": True,
        "saved": False,
        "status": "driver-missing",
        "graphStore": "typedb",
        "reason": "typedb-driver Python package is not installed: " + str(error)[:160],
        "entityCount": len(graph.entities),
        "relationCount": len(graph.relations),
        "reasoningCardCount": len(getattr(graph, "reasoning_cards", []) or []),
    }


def fresh_candidate_world_bootstrap_required(
    _store: GraphSavePort, world_id: str = ""
) -> bool:
    """Return whether this world still has no durable candidate Manifest.

    The blue-green control plane sets the fresh-candidate flag while a
    database is provisioned. A long-lived worker can process many later
    target patches, so the flag must stop bypassing active metadata after
    the first successful Manifest write.
    """
    if not _store._fresh_candidate_rebuild:
        return False
    try:
        active = dict(_store.active_abox_metadata(str(world_id or "")) or {})
    except Exception:
        return True
    return not bool(
        str(active.get("status") or "") == "ok"
        and str(
            active.get("worldviewManifestId") or active.get("aboxSnapshotId") or ""
        ).strip()
    )
