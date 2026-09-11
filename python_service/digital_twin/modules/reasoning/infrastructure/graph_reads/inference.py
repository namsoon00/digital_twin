"""graph_reads: inference through explicit injected capabilities."""

from digital_twin.modules.outcomes.contracts import hypothesis_calibration_snapshot_from_abox_rows
from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.infrastructure.graph_store_inferencebox import inferencebox_snapshot_from_rows
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.backend_constants import (
    TYPEDB_NATIVE_MATERIALIZATION_SOURCE,
    TYPEDB_NATIVE_REASONING_MODE,
    TYPEDB_NATIVE_REQUIRED_MODE,
)
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import (
    json_object,
    typedb_bool,
)
from typing import Dict, Iterable, List
import json
from .inference_ports import GraphReadsInferenceStore, GraphReadsInferenceRuntime


def inferencebox_snapshot_from_graph(
    _store: GraphReadsInferenceStore,
    graph: PortfolioOntology,
    symbols: List[str] = None,
    limit: int = 80,
    *,
    _bindings: GraphReadsInferenceRuntime
) -> Dict[str, object]:
    clean_symbols = sorted(
        set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip())
    )
    safe_limit = max(1, min(500, int(limit or 80)))
    all_entity_rows = [
        row
        for row in _store.node_rows(graph)
        if str(row.get("ontologyBox") or "") == "InferenceBox"
    ]
    all_relation_rows = [
        row
        for row in _store.rows_for_relations(graph) + _store.support_relation_rows(graph)
        if str(row.get("ontologyBox") or "") == "InferenceBox"
    ]
    entity_rows = [
        row
        for row in all_entity_rows
        if not clean_symbols or str(row.get("symbol") or "").upper() in clean_symbols
    ]
    relation_rows = [
        row
        for row in all_relation_rows
        if not clean_symbols
        or any(
            symbol in str(row.get(key) or "").upper()
            for symbol in clean_symbols
            for key in ["source", "target", "symbol"]
        )
    ]
    native_entity_rows = [row for row in entity_rows if bool(row.get("nativeTypeDbReasoned"))]
    native_relation_rows = [row for row in relation_rows if bool(row.get("nativeTypeDbReasoned"))]
    native_trace_rows = [
        row for row in native_entity_rows if str(row.get("kind") or "") == "inference-trace"
    ]
    worldview = dict(graph.worldview or {})
    # An intentionally empty native generation has no materialized rows
    # before its durable marker is written. Seed metadata from the graph
    # worldview so the in-memory result carries the same provenance and
    # completion proof as the marker that will be read back from TypeDB.
    generation_rulebox_metadata = _bindings.inference_rulebox_metadata(
        [{"propertiesJson": json.dumps(worldview, ensure_ascii=False)}] + all_entity_rows,
        all_relation_rows,
    )
    rowsets = {
        "entityCounts": [
            {"entityCount": len(native_entity_rows), "nativeEntityCount": len(native_entity_rows)}
        ],
        "relationCounts": [
            {
                "relationCount": len(native_relation_rows),
                "nativeRelationCount": len(native_relation_rows),
            }
        ],
        "traceCounts": [
            {"traceCount": len(native_trace_rows), "nativeTraceCount": len(native_trace_rows)}
        ],
        "entities": native_entity_rows[:safe_limit],
        "relations": native_relation_rows[:safe_limit],
        "traces": [
            {**row, "matchedConditionIds": _bindings.matched_condition_ids(row)}
            for row in native_trace_rows[:safe_limit]
        ],
    }
    snapshot = inferencebox_snapshot_from_rows(rowsets, "typedbNativeRuleResult", clean_symbols)
    has_native_output = bool(native_relation_rows or native_trace_rows)
    native_evaluation_completed = (
        typedb_bool(generation_rulebox_metadata.get("nativeInferenceEvaluationComplete"))
        or has_native_output
    )
    native_inference_outcome = str(
        generation_rulebox_metadata.get("nativeInferenceOutcome")
        or ("matched" if has_native_output else "")
    )
    generation_id = str((graph.worldview or {}).get("inferenceGenerationId") or "")
    generation_at = str((graph.worldview or {}).get("inferenceGenerationAt") or "")
    snapshot.update(
        {
            "graphStore": "typedb",
            "source": "typedbInferenceBox",
            "status": "ok" if has_native_output else "empty",
            "reasoningMode": str(
                generation_rulebox_metadata.get("reasoningMode") or TYPEDB_NATIVE_REASONING_MODE
            ),
            "materializationSource": str(
                generation_rulebox_metadata.get("materializationSource")
                or TYPEDB_NATIVE_MATERIALIZATION_SOURCE
            ),
            "querySource": "typedb-native-rule-result",
            "typedbReadStatus": "skipped",
            "typedbReadReason": "run_rulebox materialization result reused without opening a second TypeDB read driver.",
            "reason": (
                ""
                if has_native_output
                else (
                    "TypeDB native rules completed successfully, but no current ABox fact matched an enabled RuleBox rule."
                    if native_evaluation_completed
                    else "TypeDB native rules matched no ABox facts."
                )
            ),
            "nativeTypeDbReasoningUsed": has_native_output,
            "typedbNativeRuleReasoningUsed": has_native_output,
            "nativeTypeDbReasoningCompleted": native_evaluation_completed,
            "typedbNativeRuleEvaluationCompleted": native_evaluation_completed,
            "nativeInferenceOutcome": native_inference_outcome,
            "nativeInferenceNoMatch": bool(native_evaluation_completed and not has_native_output),
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "inferenceGenerationId": generation_id,
            "inferenceGenerationAt": generation_at,
            "worldId": str(worldview.get("worldId") or ""),
            "worldType": str(worldview.get("worldType") or ""),
            "tenantId": str(worldview.get("tenantId") or ""),
            "accountId": str(worldview.get("accountId") or ""),
            "generationScoped": bool(generation_id),
            "generationCount": 1 if generation_id else 0,
            "inactiveGenerationEntityCount": 0,
            "inactiveGenerationRelationCount": 0,
            "ignoredNonNativeRelationCount": max(0, len(relation_rows) - len(native_relation_rows)),
            "ignoredNonNativeTraceCount": max(
                0,
                len([row for row in entity_rows if str(row.get("kind") or "") == "inference-trace"])
                - len(native_trace_rows),
            ),
            **generation_rulebox_metadata,
        }
    )
    source_abox_snapshot_id = str(
        generation_rulebox_metadata.get("sourceAboxSnapshotId") or ""
    ).strip()
    if source_abox_snapshot_id:
        snapshot.update(
            {
                "sourceAboxSnapshotId": source_abox_snapshot_id,
                "activeAboxSnapshotId": source_abox_snapshot_id,
                "generationAligned": True,
            }
        )
    _bindings.apply_inference_target_coverage(snapshot, clean_symbols)
    calibration_rows = [
        row
        for row in _store.rows_for_entities(graph)
        if str(row.get("kind") or "") == "hypothesis-calibration"
        or str(row.get("tboxClass") or "") == "HypothesisCalibration"
    ]
    snapshot["hypothesisCalibration"] = hypothesis_calibration_snapshot_from_abox_rows(
        calibration_rows,
        symbols=clean_symbols,
        source_abox_snapshot_id=source_abox_snapshot_id,
        generation_aligned=bool(snapshot.get("generationAligned")),
        limit=min(40, safe_limit),
    )
    return snapshot


def inferencebox_snapshot(
    _store: GraphReadsInferenceStore,
    symbols: List[str] = None,
    limit: int = 80,
    reset_metrics: bool = True,
    world_id: str = "",
    inference_generation_id: str = "",
    source_abox_snapshot_id: str = "",
    *,
    _bindings: GraphReadsInferenceRuntime
) -> Dict[str, object]:
    clean_symbols = sorted(
        set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip())
    )
    safe_limit = max(1, min(500, int(limit or 80)))
    if not _store.address:
        return _bindings.NullTypeDBOntologyGraphRepository().inferencebox_snapshot(
            clean_symbols, safe_limit
        )
    if reset_metrics:
        _store.reset_query_metrics()
    try:
        return _store.inferencebox_snapshot_from_typedb(
            clean_symbols,
            safe_limit,
            world_id=world_id,
            inference_generation_id=inference_generation_id,
            source_abox_snapshot_id=source_abox_snapshot_id,
        )
    except (
        Exception
    ) as error:  # noqa: BLE001 - expose TypeDB read failures to monitoring diagnostics.
        return {
            "configured": True,
            "saved": False,
            "status": "error",
            "source": "typedbInferenceBox",
            "graphStore": "typedb",
            "reasoningMode": "typedb-typeql-read",
            "querySource": "typedb-typeql",
            "typedbReadStatus": "error",
            "reasonCode": _bindings.typedb_error_code(error),
            "typedbReadReason": str(error)[:180],
            "reason": "TypeDB InferenceBox 조회 실패: " + str(error)[:180],
            "symbols": clean_symbols,
            "worldId": world_id,
            "inferenceGenerationId": str(inference_generation_id or ""),
            "sourceAboxSnapshotId": str(source_abox_snapshot_id or ""),
            "entities": [],
            "relations": [],
            "traces": [],
            "entityCount": 0,
            "relationCount": 0,
            "traceCount": 0,
            "nativeEntityCount": 0,
            "nativeRelationCount": 0,
            "nativeTraceCount": 0,
            "nativeTypeDbReasoningUsed": False,
            "typedbBootstrapReasoningUsed": False,
            "hypothesisCalibration": {
                "status": "unavailable",
                "source": "typedb-abox-hypothesis-calibration",
                "reason": "TypeDB InferenceBox 조회가 실패해 ABox 결과 보정도 사용하지 않습니다.",
                "calibrations": [],
                "calibrationCount": 0,
                "generationAligned": False,
                "automaticDeployment": False,
                "decisionEligibility": "historical-review-only",
            },
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
        }


def inferencebox_snapshot_from_typedb(
    _store: GraphReadsInferenceStore,
    clean_symbols: List[str],
    safe_limit: int,
    world_id: str = "",
    inference_generation_id: str = "",
    source_abox_snapshot_id: str = "",
    *,
    _bindings: GraphReadsInferenceRuntime
) -> Dict[str, object]:
    requested_generation_id = str(inference_generation_id or "").strip()
    requested_source_abox_snapshot_id = str(source_abox_snapshot_id or "").strip()
    generation_records = _store.read_inference_generation_records(
        published_only=not bool(requested_generation_id),
        world_id=world_id,
    )
    active_generation = (
        next(
            (
                record
                for record in generation_records
                if str(record.get("generationId") or "").strip() == requested_generation_id
            ),
            {},
        )
        if requested_generation_id
        else (generation_records[0] if generation_records else {})
    )
    generation_id = str((active_generation or {}).get("generationId") or "")
    generation_scoped = bool(generation_id)
    generation_identity_source = (
        "requested-generation-id"
        if requested_generation_id and generation_scoped
        else "active-generation-marker" if generation_scoped else ""
    )
    unresolved_materialized_generation = False
    fallback_active_abox_metadata: Dict[str, object] = {}
    if requested_generation_id and not generation_scoped:
        return {
            "configured": True,
            "saved": True,
            "status": "stale-generation",
            "source": "typedbInferenceBox",
            "graphStore": "typedb",
            "reasoningMode": "typedb-typeql-read",
            "reason": "요청한 TypeDB InferenceBox 세대가 보존되어 있지 않습니다.",
            "symbols": clean_symbols,
            "worldId": world_id,
            "inferenceGenerationId": requested_generation_id,
            "sourceAboxSnapshotId": requested_source_abox_snapshot_id,
            "generationScoped": True,
            "inferenceGenerationIdentitySource": "requested-generation-missing",
            "entities": [],
            "relations": [],
            "traces": [],
            "entityCount": 0,
            "relationCount": 0,
            "traceCount": 0,
            "nativeEntityCount": 0,
            "nativeRelationCount": 0,
            "nativeTraceCount": 0,
            "nativeTypeDbReasoningUsed": False,
            "typedbNativeRuleReasoningUsed": False,
            "nativeTypeDbReasoningCompleted": False,
            "generationAligned": False,
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
        }
    if generation_scoped:
        entity_rows = _store.read_inferencebox_entity_rows(
            generation_id, clean_symbols, safe_limit, world_id=world_id
        )
        relation_rows = _store.read_inferencebox_relation_rows(
            generation_id, clean_symbols, safe_limit, world_id=world_id
        )
        metadata_entity_rows = _store.read_inferencebox_entity_rows(
            generation_id, [], min(40, safe_limit), world_id=world_id
        )
        metadata_relation_rows = _store.read_inferencebox_relation_rows(
            generation_id, [], min(40, safe_limit), world_id=world_id
        )
    else:
        all_entity_rows = _store.read_entity_rows(["InferenceBox"], world_id=world_id)
        all_relation_rows = _store.read_relation_rows(["InferenceBox"], world_id=world_id)
        # Older TypeDB runs can contain fully materialized native facts
        # without an active-generation marker. Do not blend those rows
        # across generations: select only the materialized generation
        # whose declared source ABox is the currently active world.
        materialized_records = _bindings.inference_generation_records(
            all_entity_rows, all_relation_rows
        )
        active_abox = _store.active_abox_metadata(world_id)
        fallback_active_abox_metadata = dict(active_abox or {})
        active_abox_snapshot_id = (
            str(active_abox.get("aboxSnapshotId") or "").strip()
            if str(active_abox.get("status") or "") == "ok"
            else ""
        )
        recovered_generation = _bindings.select_inference_generation_record(
            materialized_records,
            active_abox_snapshot_id=active_abox_snapshot_id,
        )
        if recovered_generation:
            active_generation = recovered_generation
            generation_id = str(recovered_generation.get("generationId") or "")
            generation_scoped = bool(generation_id)
            generation_identity_source = "materialized-row-provenance"
            generation_records = materialized_records
            all_entity_rows = [
                row
                for row in all_entity_rows
                if _bindings.row_inference_generation_id(row) == generation_id
            ]
            all_relation_rows = [
                row
                for row in all_relation_rows
                if _bindings.row_inference_generation_id(row) == generation_id
            ]
        elif materialized_records and active_abox_snapshot_id:
            # The graph has native facts, but none proves that it belongs
            # to the currently active factual world. Failing closed is
            # safer than joining rows from several historical runs.
            unresolved_materialized_generation = True
            generation_records = materialized_records
            generation_identity_source = "materialized-row-provenance-unresolved"
            all_entity_rows = []
            all_relation_rows = []
        entity_rows = [
            row
            for row in all_entity_rows
            if not clean_symbols or str(row.get("symbol") or "").upper() in clean_symbols
        ]
        relation_rows = [
            row
            for row in all_relation_rows
            if not clean_symbols
            or any(
                symbol in str(row.get(key) or "").upper()
                for symbol in clean_symbols
                for key in ["source", "target", "symbol"]
            )
        ]
        metadata_entity_rows = all_entity_rows
        metadata_relation_rows = all_relation_rows
    native_entity_rows = [row for row in entity_rows if bool(row.get("nativeTypeDbReasoned"))]
    native_relation_rows = [row for row in relation_rows if bool(row.get("nativeTypeDbReasoned"))]
    native_trace_rows = [
        row for row in native_entity_rows if str(row.get("kind") or "") == "inference-trace"
    ]
    ignored_relation_count = len(relation_rows) - len(native_relation_rows)
    ignored_trace_count = len(
        [row for row in entity_rows if str(row.get("kind") or "") == "inference-trace"]
    ) - len(native_trace_rows)
    generation_rulebox_metadata = _bindings.inference_rulebox_metadata(
        metadata_entity_rows, metadata_relation_rows
    )
    rowsets = {
        "entityCounts": [
            {"entityCount": len(native_entity_rows), "nativeEntityCount": len(native_entity_rows)}
        ],
        "relationCounts": [
            {
                "relationCount": len(native_relation_rows),
                "nativeRelationCount": len(native_relation_rows),
            }
        ],
        "traceCounts": [
            {"traceCount": len(native_trace_rows), "nativeTraceCount": len(native_trace_rows)}
        ],
        "entities": native_entity_rows[:safe_limit],
        "relations": native_relation_rows[:safe_limit],
        "traces": [
            {**row, "matchedConditionIds": _bindings.matched_condition_ids(row)}
            for row in native_trace_rows[:safe_limit]
        ],
    }
    snapshot = inferencebox_snapshot_from_rows(rowsets, "typedb-typeql", clean_symbols)
    has_native_output = bool(native_relation_rows or native_trace_rows)
    native_evaluation_completed = (
        typedb_bool(generation_rulebox_metadata.get("nativeInferenceEvaluationComplete"))
        or has_native_output
    )
    native_inference_outcome = str(
        generation_rulebox_metadata.get("nativeInferenceOutcome")
        or ("matched" if has_native_output else "")
    )
    reasoning_mode = str(
        generation_rulebox_metadata.get("reasoningMode")
        or (TYPEDB_NATIVE_REASONING_MODE if has_native_output else TYPEDB_NATIVE_REQUIRED_MODE)
    )
    materialization_source = str(
        generation_rulebox_metadata.get("materializationSource")
        or TYPEDB_NATIVE_MATERIALIZATION_SOURCE
    )
    snapshot.update(
        {
            "graphStore": "typedb",
            "source": "typedbInferenceBox",
            "status": "ok" if has_native_output else "empty",
            "reasoningMode": reasoning_mode,
            "materializationSource": materialization_source,
            "querySource": "typedb-typeql",
            "typedbReadStatus": "ok",
            "reason": (
                ""
                if has_native_output
                else (
                    "TypeDB native rules completed successfully, but no current ABox fact matched an enabled RuleBox rule."
                    if native_evaluation_completed
                    else "TypeDB InferenceBox 관계가 아직 없습니다. TypeDB native rule materialization 결과를 확인해야 합니다."
                )
            ),
            "nativeTypeDbReasoningUsed": has_native_output,
            "typedbNativeRuleReasoningUsed": has_native_output,
            "nativeTypeDbReasoningCompleted": native_evaluation_completed,
            "typedbNativeRuleEvaluationCompleted": native_evaluation_completed,
            "nativeInferenceOutcome": native_inference_outcome,
            "nativeInferenceNoMatch": bool(native_evaluation_completed and not has_native_output),
            "typedbBootstrapReasoningUsed": False,
            "pythonBootstrapDisabled": True,
            "inferenceGenerationId": generation_id,
            "inferenceGenerationAt": str((active_generation or {}).get("latestAt") or ""),
            "worldId": world_id,
            "generationScoped": generation_scoped,
            "inferenceGenerationIdentitySource": generation_identity_source,
            "generationCount": len(generation_records),
            "inactiveGenerationEntityCount": (
                max(
                    0,
                    sum(
                        int(item.get("entityCount") or 0)
                        for item in generation_records
                        if str(item.get("generationId") or "") != generation_id
                    ),
                )
                if generation_scoped
                else 0
            ),
            "inactiveGenerationRelationCount": (
                max(
                    0,
                    sum(
                        int(item.get("relationCount") or 0)
                        for item in generation_records
                        if str(item.get("generationId") or "") != generation_id
                    ),
                )
                if generation_scoped
                else 0
            ),
            "ignoredNonNativeRelationCount": ignored_relation_count,
            "ignoredNonNativeTraceCount": ignored_trace_count,
            "typedbQueryMetrics": _store.query_metrics_snapshot(),
            **generation_rulebox_metadata,
        }
    )
    source_abox_snapshot_id = str(
        generation_rulebox_metadata.get("sourceAboxSnapshotId") or ""
    ).strip()
    if source_abox_snapshot_id:
        active_abox_metadata = _store.active_abox_metadata(world_id)
        active_abox_status = str(active_abox_metadata.get("status") or "")
        active_abox_snapshot_id = (
            str(active_abox_metadata.get("aboxSnapshotId") or "").strip()
            if active_abox_status == "ok"
            else ""
        )
        pinned_generation_aligned = bool(
            requested_generation_id
            and requested_source_abox_snapshot_id
            and source_abox_snapshot_id == requested_source_abox_snapshot_id
        )
        generation_aligned = (
            pinned_generation_aligned
            if requested_generation_id
            else bool(
                active_abox_snapshot_id and active_abox_snapshot_id == source_abox_snapshot_id
            )
        )
        snapshot.update(
            {
                "sourceAboxSnapshotId": source_abox_snapshot_id,
                "activeAboxSnapshotId": active_abox_snapshot_id,
                "activeAboxStatus": active_abox_status,
                "generationAligned": generation_aligned,
                "detailGenerationPinned": bool(requested_generation_id),
                "activeAboxAligned": bool(
                    active_abox_snapshot_id and active_abox_snapshot_id == source_abox_snapshot_id
                ),
            }
        )
        if not generation_aligned:
            incomplete_abox = active_abox_status != "ok"
            snapshot.update(
                {
                    "status": "incomplete-abox" if incomplete_abox else "stale-generation",
                    "reason": (
                        "현재 ABox 저장이 완료되지 않아 InferenceBox 결과를 투자 판단에서 제외합니다. "
                        + str(
                            active_abox_metadata.get("reason")
                            or "완료 표식 또는 저장 건수를 확인해야 합니다."
                        )[:180]
                        if incomplete_abox
                        else "현재 ABox와 InferenceBox의 원본 ABox 세대가 달라 투자 판단에서 제외합니다."
                    ),
                    "entities": [],
                    "relations": [],
                    "traces": [],
                    "entityCount": 0,
                    "relationCount": 0,
                    "traceCount": 0,
                    "nativeEntityCount": 0,
                    "nativeRelationCount": 0,
                    "nativeTraceCount": 0,
                    "nativeTypeDbReasoningUsed": False,
                    "typedbNativeRuleReasoningUsed": False,
                }
            )
    if unresolved_materialized_generation:
        snapshot.update(
            {
                "status": "stale-generation",
                "reason": "활성 ABox와 일치하는 TypeDB InferenceBox 세대를 찾지 못해 이전 추론 결과를 제외했습니다.",
                "sourceAboxSnapshotId": "",
                "activeAboxSnapshotId": str(
                    fallback_active_abox_metadata.get("aboxSnapshotId") or ""
                ),
                "activeAboxStatus": str(fallback_active_abox_metadata.get("status") or ""),
                "generationAligned": False,
                "entities": [],
                "relations": [],
                "traces": [],
                "entityCount": 0,
                "relationCount": 0,
                "traceCount": 0,
                "nativeEntityCount": 0,
                "nativeRelationCount": 0,
                "nativeTraceCount": 0,
                "nativeTypeDbReasoningUsed": False,
                "typedbNativeRuleReasoningUsed": False,
            }
        )
    _bindings.apply_inference_target_coverage(snapshot, clean_symbols)
    calibration_eligible = bool(
        snapshot.get("generationAligned")
        and snapshot.get("targetCoverageComplete", True)
        and str(snapshot.get("status") or "") in {"ok", "empty"}
    )
    if calibration_eligible:
        snapshot["hypothesisCalibration"] = _store.hypothesis_calibration_snapshot(
            clean_symbols,
            min(40, safe_limit),
            world_id,
            source_abox_snapshot_id=source_abox_snapshot_id,
            generation_aligned=True,
        )
    else:
        snapshot["hypothesisCalibration"] = {
            "status": "not-eligible",
            "source": "typedb-abox-hypothesis-calibration",
            "reason": "Only an aligned and target-complete InferenceBox generation can load calibration evidence.",
            "calibrations": [],
            "calibrationCount": 0,
            "generationAligned": bool(snapshot.get("generationAligned")),
            "automaticDeployment": False,
            "decisionEligibility": "historical-review-only",
        }
    return snapshot


def load_graph_from_typedb(
    _store: GraphReadsInferenceStore, boxes: Iterable[str] = None, world_id: str = ""
) -> PortfolioOntology:
    graph = PortfolioOntology("typedb-read-model")
    graph.worldview["worldId"] = str(world_id or "")
    for row in _store.read_entity_rows(boxes or ["ABox"], world_id=world_id):
        properties = json_object(row.get("propertiesJson"))
        properties.setdefault("ontologyBox", row.get("ontologyBox") or "ABox")
        if row.get("symbol"):
            properties.setdefault("symbol", row.get("symbol"))
        if row.get("tboxClass"):
            properties.setdefault("tboxClass", row.get("tboxClass"))
        graph.entities.append(
            OntologyEntity(
                str(row.get("id") or ""),
                str(row.get("label") or row.get("id") or ""),
                str(row.get("kind") or ""),
                properties,
            )
        )
    for row in _store.read_relation_rows(boxes or ["ABox"], world_id=world_id):
        properties = json_object(row.get("propertiesJson"))
        properties.setdefault("ontologyBox", row.get("ontologyBox") or "ABox")
        if row.get("ruleId"):
            properties.setdefault("ruleId", row.get("ruleId"))
        graph.relations.append(
            OntologyRelation(
                str(row.get("source") or ""),
                str(row.get("target") or ""),
                str(row.get("type") or ""),
                float(number_or_none(row.get("weight")) or 1.0),
                [],
                properties,
            )
        )
    return graph
