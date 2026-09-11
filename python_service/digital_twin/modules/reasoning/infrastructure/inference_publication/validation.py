"""Validation for generation-scoped InferenceBox publication."""

from typing import Dict

from digital_twin.modules.reasoning.domain.ontology_contracts import PortfolioOntology
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from .ports import PublicationStore
from .values import json_object, typedb_bool


def inference_generation_candidate_summary(
    store: PublicationStore,
    generation_id: str,
    world_id: str = '',
) -> Dict[str, object]:
    """Validate a staged generation without loading every JSON row.

    Counts and the candidate marker are read in one transaction. The
    marker carries the native evaluation and source ABox proof, while the
    aggregate counts detect partial writes without paying the cost of
    deserializing the complete InferenceBox.
    """
    clean_generation_id = str(generation_id or "").strip()
    clean_world_id = str(world_id or "").strip()
    if not clean_generation_id:
        return {
            "status": "invalid",
            "entityCount": 0,
            "relationCount": 0,
            "traceCount": 0,
            "candidateMarkerPresent": False,
            "metadata": {},
        }
    imported = store.driver_imports()
    if imported[0] is None:
        raise RuntimeError(
            "typedb-driver Python package is not installed: "
            + str(imported[1])[:160]
        )
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    generation_clause = (
        "has ontology-snapshot-id " + typedb_string(clean_generation_id)
    )
    world_clause = (
        ", has ontology-world-id " + typedb_string(clean_world_id)
        if clean_world_id
        else ""
    )

    def count_query(type_label: str, kind: str = "") -> str:
        return (
            "match $item isa " + type_label
            + ', has ontology-box "InferenceBox", '
            + generation_clause
            + world_clause
            + (', has ontology-kind "' + kind + '"' if kind else "")
            + "; reduce $count = count;"
        )

    marker_query = (
        "match $item isa ontology-node, "
        'has ontology-box "InferenceBox", '
        + generation_clause
        + world_clause
        + ', has ontology-kind "inference-generation-candidate", '
        + "has ontology-json $json; limit 1;"
    )

    def operation():
        driver = store.open_driver(imported)
        try:
            store.ensure_database(driver)
            with driver.transaction(
                store.database,
                TransactionType.READ,
                store.read_transaction_options(),
            ) as tx:
                entity_rows = store.read_rows_in_transaction(
                    tx,
                    count_query("ontology-node"),
                    ["count"],
                    label="typedb.inference-candidate-summary.entities",
                )
                relation_rows = store.read_rows_in_transaction(
                    tx,
                    count_query("ontology-assertion"),
                    ["count"],
                    label="typedb.inference-candidate-summary.relations",
                )
                trace_rows = store.read_rows_in_transaction(
                    tx,
                    count_query("ontology-node", "inference-trace"),
                    ["count"],
                    label="typedb.inference-candidate-summary.traces",
                )
                marker_rows = store.read_rows_in_transaction(
                    tx,
                    marker_query,
                    ["json"],
                    label="typedb.inference-candidate-summary.marker",
                )
                marker = json_object(
                    (marker_rows[0] if marker_rows else {}).get("json")
                )
                return {
                    "status": "ok",
                    "entityCount": int(number_or_none(
                        (entity_rows[0] if entity_rows else {}).get("count")
                    ) or 0),
                    "relationCount": int(number_or_none(
                        (relation_rows[0] if relation_rows else {}).get("count")
                    ) or 0),
                    "traceCount": int(number_or_none(
                        (trace_rows[0] if trace_rows else {}).get("count")
                    ) or 0),
                    "candidateMarkerPresent": bool(marker_rows),
                    "metadata": marker,
                    "readTransactionCount": 1,
                    "readQueryCount": 4,
                }
        finally:
            store.close_driver(driver)

    return store.with_typedb_retries(operation)


def validate_inference_generation_candidate(
    store: PublicationStore,
    graph: PortfolioOntology,
    generation_id: str,
    expected_entity_count: int,
    expected_relation_count: int,
    world_id: str = '',
) -> Dict[str, object]:
    summary = store.inference_generation_candidate_summary(
        generation_id,
        world_id=world_id,
    )
    metadata = dict(summary.get("metadata") or {})
    expected_source_abox = str((graph.worldview or {}).get("sourceAboxSnapshotId") or metadata.get("sourceAboxSnapshotId") or "").strip()
    stable_source_alignment = bool(
        typedb_bool((graph.worldview or {}).get("sourceAboxValidatedUnderWriteLease"))
        and typedb_bool((graph.worldview or {}).get("sourceAboxGenerationValid"))
        and expected_source_abox
    )
    active_abox = (
        expected_source_abox
        if stable_source_alignment
        else store.active_abox_snapshot_id(world_id)
    )
    actual_entities = int(number_or_none(summary.get("entityCount")) or 0)
    actual_relations = int(number_or_none(summary.get("relationCount")) or 0)
    actual_traces = int(number_or_none(summary.get("traceCount")) or 0)
    expected_traces = len([item for item in graph.entities if item.kind == "inference-trace"])
    native_evaluation_completed = typedb_bool(metadata.get("nativeInferenceEvaluationComplete"))
    candidate_marker_present = bool(summary.get("candidateMarkerPresent"))
    reasons = []
    # One additional node is the candidate publication marker itself.
    if actual_entities != int(expected_entity_count or 0) + 1:
        reasons.append("candidate-entity-count-mismatch")
    if actual_relations != int(expected_relation_count or 0):
        reasons.append("candidate-relation-count-mismatch")
    if expected_relation_count <= 0 and not native_evaluation_completed:
        reasons.append("candidate-empty-evaluation-not-complete")
    if not candidate_marker_present:
        reasons.append("candidate-generation-marker-missing")
    if actual_traces != expected_traces:
        reasons.append("candidate-trace-count-mismatch")
    if not expected_source_abox:
        reasons.append("candidate-source-abox-missing")
    elif not active_abox or expected_source_abox != active_abox:
        reasons.append("candidate-source-abox-not-active")
    return {
        "status": "ok" if not reasons else "invalid",
        "valid": not reasons,
        "reason": ", ".join(reasons),
        "generationId": generation_id,
        "expectedEntityCount": int(expected_entity_count or 0),
        "actualEntityCount": max(0, actual_entities - (1 if candidate_marker_present else 0)),
        "actualStoredEntityCount": actual_entities,
        "expectedRelationCount": int(expected_relation_count or 0),
        "actualRelationCount": actual_relations,
        "expectedTraceCount": expected_traces,
        "actualTraceCount": actual_traces,
        "sourceAboxSnapshotId": expected_source_abox,
        "activeAboxSnapshotId": active_abox,
        "generationAligned": bool(expected_source_abox and expected_source_abox == active_abox),
        "nativeInferenceEvaluationComplete": native_evaluation_completed,
        "nativeInferenceOutcome": str(metadata.get("nativeInferenceOutcome") or ""),
        "candidateMarkerPresent": candidate_marker_present,
        "validationMode": "aggregate-marker",
        "readTransactionCount": int(number_or_none(summary.get("readTransactionCount")) or 0),
        "readQueryCount": int(number_or_none(summary.get("readQueryCount")) or 0),
        "sourceAboxValidationMode": (
            "stable-write-lease"
            if stable_source_alignment
            else "active-pointer-read"
        ),
    }
