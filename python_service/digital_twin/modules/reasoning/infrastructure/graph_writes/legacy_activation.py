"""Legacy activation implementation; facade-independent dependencies."""

from __future__ import annotations
from .legacy_activation_ports import (
    LegacyActivationPort,
    AboxActivePointerGraphBindings,
    ActivateAboxGenerationBindings,
    AboxProjectionMarkerGraphBindings,
)
from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
from digital_twin.modules.reasoning.infrastructure.abox_persistence.world_calls import (
    typedb_call_for_world,
)
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
)
from typing import Dict, List
import hashlib


def box_instance_exists(
    _store: LegacyActivationPort, driver, imported, box: str, type_label: str
) -> bool:
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[
        0
    ]
    query = (
        "match $item isa "
        + str(type_label)
        + ", has ontology-box "
        + typedb_string(box)
        + "; limit 1;"
    )
    with driver.transaction(_store.database, TransactionType.READ) as tx:
        return bool(
            _store.read_rows_in_transaction(tx, query, [], label="typedb.box-exists")
        )


def box_delete_batch_query(
    _store: LegacyActivationPort, box: str, type_label: str, batch_size: int
) -> str:
    variable = "$r" if str(type_label) == "ontology-assertion" else "$n"
    return (
        "match "
        + variable
        + " isa "
        + str(type_label)
        + ", has ontology-box "
        + typedb_string(box)
        + "; limit "
        + str(max(1, int(batch_size or 1)))
        + "; delete "
        + variable
        + ";"
    )


def abox_candidate_snapshot_ids(_store: LegacyActivationPort) -> List[str]:
    rows = _store.read_rows(
        'match $n isa ontology-node, has ontology-box "ABox", has ontology-snapshot-id $snapshotId;',
        ["snapshotId"],
        label="typedb.abox-candidate-cleanup-audit",
    )
    return sorted(
        {
            str(row.get("snapshotId") or "").strip()
            for row in rows
            if str(row.get("snapshotId") or "").strip()
        }
    )


def abox_candidate_graph(
    _store: LegacyActivationPort, graph: PortfolioOntology
) -> PortfolioOntology:
    """Return one immutable ABox generation ready for pointer activation.

    ABox records stay in their normal box. Their storage identity already
    includes ``snapshotId``, so a verified candidate can coexist with the
    currently active generation without rewriting thousands of records.
    """
    return _store.graph_for_boxes(graph, ["ABox"])


def abox_snapshot_id_from_graph(graph: PortfolioOntology) -> str:
    worldview = dict(getattr(graph, "worldview", {}) or {})
    snapshot_id = str(
        worldview.get("aboxSnapshotId") or worldview.get("snapshotId") or ""
    ).strip()
    if snapshot_id:
        return snapshot_id
    for item in list(getattr(graph, "entities", []) or []):
        properties = dict(getattr(item, "properties", {}) or {})
        snapshot_id = str(
            properties.get("aboxSnapshotId") or properties.get("snapshotId") or ""
        ).strip()
        if snapshot_id:
            return snapshot_id
    return ""


def abox_active_pointer_graph(
    _store: LegacyActivationPort,
    graph: PortfolioOntology,
    previous_snapshot_id: str = "",
    pending_activation: bool = True,
    *,
    _bindings: AboxActivePointerGraphBindings,
) -> PortfolioOntology:
    worldview = dict(getattr(graph, "worldview", {}) or {})
    snapshot_id = _store.abox_snapshot_id_from_graph(graph)
    fingerprint = str(worldview.get("materialFingerprint") or "").strip()
    if not snapshot_id:
        return PortfolioOntology(str(graph.portfolio_id or "typedb-abox-control"))
    as_of = str(
        worldview.get("asOf") or worldview.get("generatedAt") or _bindings.utc_now()
    )
    target_symbols = clean_symbols_from_payload(
        worldview.get("inferenceTargetSymbols") or worldview.get("targetSymbols") or []
    )
    world_id = str(worldview.get("worldId") or "").strip()
    world_context = {
        "worldId": world_id,
        "worldType": str(worldview.get("worldType") or ""),
        "tenantId": str(worldview.get("tenantId") or ""),
        "accountId": str(worldview.get("accountId") or graph.portfolio_id or ""),
    }
    world_suffix = (
        (":world:" + hashlib.sha256(world_id.encode("utf-8")).hexdigest()[:16])
        if world_id
        else ""
    )
    pointer = OntologyEntity(
        entity_id="abox-active-pointer" + world_suffix,
        label="Active ABox generation",
        kind="abox-active-pointer",
        properties={
            "ontologyBox": "ABoxControl",
            **world_context,
            "tboxClass": "ABoxActivePointer",
            "snapshotId": snapshot_id,
            "aboxSnapshotId": snapshot_id,
            "materialFingerprint": fingerprint,
            "projectionRunId": str(worldview.get("projectionRunId") or ""),
            "asOf": as_of,
        },
    )
    entities = [pointer]
    # Store the activation hand-off in the same atomic ABoxControl write as
    # the pointer. This is cleared only after a native InferenceBox is
    # aligned, or after an explicit rollback to the retained predecessor.
    if pending_activation and str(previous_snapshot_id or "").strip() != snapshot_id:
        entities.append(
            OntologyEntity(
                entity_id="abox-activation-pending" + world_suffix,
                label="ABox activation pending native inference",
                kind="abox-activation-pending",
                properties={
                    "ontologyBox": "ABoxControl",
                    **world_context,
                    "tboxClass": "ABoxActivationPending",
                    "snapshotId": snapshot_id,
                    "aboxSnapshotId": snapshot_id,
                    "candidateAboxSnapshotId": snapshot_id,
                    "previousAboxSnapshotId": str(previous_snapshot_id or "").strip(),
                    "materialFingerprint": fingerprint,
                    "projectionRunId": str(worldview.get("projectionRunId") or ""),
                    "asOf": as_of,
                    "targetSymbols": target_symbols,
                    "activationStatus": "pending-native-inference",
                },
            )
        )
    return PortfolioOntology(
        str(graph.portfolio_id or "typedb-abox-control"), entities=entities
    )


def activate_abox_generation(
    _store: LegacyActivationPort,
    snapshot_id: str,
    world_id: str = "",
    *,
    _bindings: ActivateAboxGenerationBindings,
) -> Dict[str, object]:
    """Point the active ABox control record at a verified generation.

    This is used to restore the last aligned ABox when a newly activated
    generation cannot complete TypeDB native inference. It only accepts a
    generation with a complete ABox marker, so it cannot promote a partial
    write left behind by an interrupted worker.
    """
    clean_snapshot_id = str(snapshot_id or "").strip()
    if not clean_snapshot_id:
        return {
            "configured": bool(_store.address),
            "status": "skipped",
            "graphStore": "typedb",
            "reason": "ABox snapshot id is empty.",
        }
    if _store.scoped_manifest_metadata(clean_snapshot_id, world_id):
        return typedb_call_for_world(
            _store.activate_scoped_abox_manifest,
            clean_snapshot_id,
            world_id=world_id,
        )
    marker = next(
        (
            item
            for item in _store.abox_projection_marker_rows(world_id)
            if str(item.get("aboxSnapshotId") or item.get("snapshotId") or "").strip()
            == clean_snapshot_id
        ),
        None,
    )
    metadata = _store.abox_metadata_from_marker(marker or {}) if marker else {}
    if str(metadata.get("status") or "") != "ok":
        return {
            "configured": bool(_store.address),
            "status": "error",
            "graphStore": "typedb",
            "aboxSnapshotId": clean_snapshot_id,
            "reason": str(metadata.get("reason") or "ABox generation is not complete."),
        }
    imported = _store.driver_imports()
    if imported[0] is None:
        return _store.driver_missing_result(
            imported[1], PortfolioOntology("typedb-abox-control")
        )
    pointer_graph = _store.abox_active_pointer_graph(
        PortfolioOntology(
            "typedb-abox-control",
            worldview={
                "aboxSnapshotId": clean_snapshot_id,
                "materialFingerprint": str(metadata.get("materialFingerprint") or ""),
                "projectionRunId": str(metadata.get("projectionRunId") or ""),
                "asOf": str(metadata.get("asOf") or _bindings.utc_now()),
                "worldId": str(world_id or metadata.get("worldId") or ""),
            },
        ),
        pending_activation=False,
    )
    try:

        def operation():
            driver = _store.open_driver(imported)
            try:
                _store.ensure_database(driver)
                _store.delete_world_abox_control_rows(driver, imported, world_id)
                _store.write_graph(driver, imported, pointer_graph, delete_boxes=[])
            finally:
                _store.close_driver(driver)

        _store.with_typedb_retries(operation)
        active = _store.active_abox_metadata(world_id)
        if (
            str(active.get("status") or "") != "ok"
            or str(active.get("aboxSnapshotId") or "") != clean_snapshot_id
        ):
            return {
                "configured": True,
                "status": "error",
                "graphStore": "typedb",
                "aboxSnapshotId": clean_snapshot_id,
                "reason": "ABox control pointer verification failed after activation.",
                "activeAbox": active,
            }
        return {
            "configured": True,
            "status": "ok",
            "graphStore": "typedb",
            "aboxSnapshotId": clean_snapshot_id,
            "activeAbox": active,
        }
    except (
        Exception
    ) as error:  # noqa: BLE001 - caller preserves the diagnostic failure state.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "aboxSnapshotId": clean_snapshot_id,
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": str(error)[:220],
        }


def finalize_abox_generation(
    _store: LegacyActivationPort,
    active_snapshot_id: str,
    previous_snapshot_id: str = "",
    world_id: str = "",
) -> Dict[str, object]:
    """Complete an ABox activation after aligned native inference.

    Clearing the durable activation journal is a correctness boundary;
    deleting the prior generation is storage maintenance. Keeping those
    operations separate prevents one expensive TypeDB delete from making a
    valid realtime inference appear incomplete or retriggering alerts.
    """
    active_id = str(active_snapshot_id or "").strip()
    previous_id = str(previous_snapshot_id or "").strip()
    active_metadata = _store.active_abox_metadata(world_id)
    if (
        str(active_metadata.get("scopedAboxManifestVersion") or "")
        == SCOPED_ABOX_MANIFEST_VERSION
    ):
        return _store.finalize_scoped_abox_manifest(active_id, previous_id, world_id)
    if not active_id:
        return {
            "configured": bool(_store.address),
            "status": "error",
            "graphStore": "typedb",
            "activeAboxSnapshotId": active_id,
            "previousAboxSnapshotId": previous_id,
            "reason": "Active ABox snapshot id is empty.",
        }
    active = _store.active_abox_metadata(world_id)
    if (
        str(active.get("status") or "") != "ok"
        or str(active.get("aboxSnapshotId") or "") != active_id
    ):
        return {
            "configured": bool(_store.address),
            "status": "error",
            "graphStore": "typedb",
            "activeAboxSnapshotId": active_id,
            "previousAboxSnapshotId": previous_id,
            "reason": "Active ABox changed before retained-generation cleanup.",
        }
    control = typedb_call_for_world(
        _store.activate_abox_generation,
        active_id,
        world_id=world_id,
    )
    cleared = str(control.get("status") or "") == "ok"
    cleanup_deferred = bool(previous_id and previous_id != active_id)
    return {
        "configured": True,
        "status": "ok" if cleared else "error",
        "graphStore": "typedb",
        "activeAboxSnapshotId": active_id,
        "previousAboxSnapshotId": previous_id,
        "clearedPendingActivation": cleared,
        "cleanupDeferred": cleanup_deferred,
        "cleanup": {
            "status": "deferred" if cleanup_deferred else "not-required",
            "previousAboxSnapshotId": previous_id,
            "reason": (
                "Inactive ABox cleanup will run in bounded maintenance slices."
                if cleanup_deferred
                else "No prior ABox generation requires cleanup."
            ),
        },
        "control": control,
        "reason": (
            ""
            if cleared
            else str(control.get("reason") or "ABox activation journal clear failed.")
        ),
    }


def abox_projection_marker_graph(
    _store: LegacyActivationPort,
    graph: PortfolioOntology,
    expected_entity_count: int,
    expected_relation_count: int,
    box: str = "ABox",
    *,
    _bindings: AboxProjectionMarkerGraphBindings,
) -> PortfolioOntology:
    worldview = dict(getattr(graph, "worldview", {}) or {})
    snapshot_id = str(
        worldview.get("aboxSnapshotId") or worldview.get("snapshotId") or ""
    ).strip()
    fingerprint = str(worldview.get("materialFingerprint") or "").strip()
    if not snapshot_id or not fingerprint:
        return PortfolioOntology(str(graph.portfolio_id or "typedb-abox-marker"))
    as_of = str(
        worldview.get("asOf") or worldview.get("generatedAt") or _bindings.utc_now()
    )
    marker = OntologyEntity(
        entity_id="abox-projection-marker:" + snapshot_id,
        label="ABox projection completion",
        kind="abox-projection-marker",
        properties={
            "ontologyBox": str(box or "ABox"),
            "tboxClass": "ABoxProjectionMarker",
            "snapshotId": snapshot_id,
            "aboxSnapshotId": snapshot_id,
            "materialFingerprint": fingerprint,
            "projectionRunId": str(worldview.get("projectionRunId") or ""),
            "asOf": as_of,
            "expectedAboxEntityCount": int(expected_entity_count),
            "expectedAboxRelationCount": int(expected_relation_count),
            "projectionStatus": "complete",
        },
    )
    return PortfolioOntology(
        str(graph.portfolio_id or "typedb-abox-marker"), entities=[marker]
    )


def verify_abox_projection(
    _store: LegacyActivationPort,
    graph: PortfolioOntology,
    expected_entity_count: int,
    expected_relation_count: int,
    box: str = "ABox",
) -> Dict[str, object]:
    worldview = dict(getattr(graph, "worldview", {}) or {})
    snapshot_id = str(
        worldview.get("aboxSnapshotId") or worldview.get("snapshotId") or ""
    ).strip()
    if not snapshot_id:
        return {"status": "skipped", "reason": "ABox material identity is unavailable."}
    actual = _store.box_snapshot_row_counts(str(box or "ABox"), snapshot_id)
    complete = actual["entityCount"] == int(expected_entity_count) + 1 and actual[
        "relationCount"
    ] == int(expected_relation_count)
    return {
        "status": "ok" if complete else "incomplete",
        "ontologyBox": str(box or "ABox"),
        "aboxSnapshotId": snapshot_id,
        "expectedEntityCount": int(expected_entity_count),
        "expectedRelationCount": int(expected_relation_count),
        "actualEntityCount": actual["entityCount"] - 1 if actual["entityCount"] else 0,
        "actualRelationCount": actual["relationCount"],
        "completionMarkerCount": 1 if actual["entityCount"] else 0,
    }
