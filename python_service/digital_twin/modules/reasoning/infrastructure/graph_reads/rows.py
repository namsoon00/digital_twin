"""graph_reads: rows through explicit injected capabilities."""

from digital_twin.domain.hypothesis_calibration import (
    hypothesis_calibration_snapshot_from_abox_rows,
)
from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
from digital_twin.infrastructure.graph_store_payloads import (
    condition_relation_filter_values,
    number_or_none,
)
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import json_object
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    normalize_native_rule_evidence_read_index,
)
from digital_twin.modules.reasoning.infrastructure.typeql.constants import (
    NATIVE_RULE_EVIDENCE_READ_INDEX_BATCH_SIZE,
)
from digital_twin.modules.reasoning.infrastructure.typeql.literals import (
    typedb_string,
    typedb_value_match,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
    typedb_native_rule_id,
)
from typing import Dict, Iterable, List
import json
from .rows_ports import GraphReadsRowsStore, GraphReadsRowsRuntime


def has_box_rows(_store: GraphReadsRowsStore, box: str, world_id: str = "") -> bool:
    clean_box = str(box or "").strip()
    if clean_box == "ABox":
        # A scoped Worldview Manifest is published only after its ABox
        # generations have been validated.  Expanding every active scope
        # pointer merely to answer this existence probe can become the
        # most expensive query in the native-rule path.  Reuse the
        # durable completion marker instead; incomplete or legacy worlds
        # continue through the stricter membership query below.
        try:
            active = _store.active_abox_metadata(world_id)
        except Exception:
            active = {}
        if (
            str(active.get("status") or "") == "ok"
            and str(active.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION
            and bool(active.get("scopePlan") or active.get("scopeGenerationIds"))
        ):
            return True
        query = (
            "match "
            + _store.active_abox_members_clause([("$n", "boxProbe")], world_id)
            + " "
            + "$n isa ontology-node; limit 1;"
        )
    else:
        query = (
            "match $n isa ontology-node, has ontology-box "
            + typedb_string(clean_box)
            + (
                ", has ontology-world-id " + typedb_string(world_id)
                if clean_box == "InferenceBox" and str(world_id or "").strip()
                else ""
            )
            + "; limit 1;"
        )
    return bool(_store.read_rows(query, []))


def read_entity_rows(
    _store: GraphReadsRowsStore,
    boxes: Iterable[str] = None,
    limit: int = 0,
    world_id: str = "",
    snapshot_id: str = "",
    *,
    _bindings: GraphReadsRowsRuntime
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    safe_limit = int(limit or 0)
    normalized = _bindings.normalized_boxes(boxes)
    static_generations: Dict[str, str] = {}
    if any(box in _store.seed_static_box_names() for box in normalized):
        manifest = _store.read_seed_static_manifest()
        if str(manifest.get("status") or "") == "ok":
            static_generations = _store.static_seed_generation_ids(manifest.get("metadata") or {})
    for box in normalized:
        active_scope = ""
        active_snapshot = ""
        if box == "ABox":
            active_scope = _store.active_abox_members_clause([("$n", "entity")], world_id) + " "
        elif box == "InferenceBox" and str(world_id or "").strip():
            active_scope = "$n has ontology-world-id " + typedb_string(world_id) + "; "
        elif box in _store.seed_static_box_names():
            resolved_snapshot = str(snapshot_id or static_generations.get(box) or "").strip()
            if resolved_snapshot:
                active_snapshot = ", has ontology-snapshot-id " + typedb_string(resolved_snapshot)
        query = (
            "match " + active_scope + "$n isa ontology-node, "
            "has ontology-id $id, "
            "has ontology-label $label, "
            "has ontology-kind $kind, "
            "has ontology-box " + typedb_string(box) + active_snapshot + ", "
            "has ontology-updated-at $updatedAt, "
            "has ontology-json $json; " + _bindings.typeql_limit_clause(safe_limit)
        )
        rows.extend(
            _store.entity_rows_from_typeql(
                _store.read_rows(
                    query,
                    ["id", "label", "kind", "updatedAt", "json"],
                ),
                box,
            )
        )
        if safe_limit > 0 and len(rows) >= safe_limit:
            break
    rows = sorted(
        rows,
        key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("id") or "")),
        reverse=True,
    )
    return rows[:safe_limit] if safe_limit > 0 else rows


def read_active_hypothesis_calibration_rows(
    _store: GraphReadsRowsStore,
    symbols: Iterable[str] = None,
    limit: int = 40,
    world_id: str = "",
    *,
    _bindings: GraphReadsRowsRuntime
) -> List[Dict[str, object]]:
    """Read the active ABox calibration facts without reading all ABox rows."""
    clean_symbols = sorted(
        {str(item or "").upper().strip() for item in symbols or [] if str(item or "").strip()}
    )
    query = (
        "match "
        + _store.active_abox_members_clause([("$n", "hypothesisCalibration")], world_id)
        + " $n isa ontology-node, "
        + "has ontology-id $id, "
        + "has ontology-label $label, "
        + 'has ontology-kind "hypothesis-calibration", '
        + 'has ontology-box "ABox", '
        + "has ontology-symbol $symbol, "
        + "has ontology-updated-at $updatedAt, "
        + "has ontology-json $json; "
    )
    if clean_symbols:
        query += typedb_value_match(
            "$n", "ontology-symbol", clean_symbols, "==", "hypothesisCalibrationSymbol"
        )
    query += _bindings.typeql_limit_clause(max(1, min(100, int(limit or 40))))
    rows = _store.entity_rows_from_typeql(
        _store.read_rows(
            query,
            ["id", "label", "kind", "symbol", "updatedAt", "json"],
        ),
        "ABox",
    )
    return sorted(rows, key=lambda item: (str(item.get("symbol") or ""), str(item.get("id") or "")))


def hypothesis_calibration_snapshot(
    _store: GraphReadsRowsStore,
    symbols: Iterable[str] = None,
    limit: int = 40,
    world_id: str = "",
    source_abox_snapshot_id: str = "",
    generation_aligned: bool = False,
) -> Dict[str, object]:
    source_snapshot_id = str(source_abox_snapshot_id or "").strip()
    if not generation_aligned or not source_snapshot_id:
        return hypothesis_calibration_snapshot_from_abox_rows(
            [],
            symbols=symbols,
            source_abox_snapshot_id=source_snapshot_id,
            generation_aligned=False,
        )
    try:
        rows = _store.read_active_hypothesis_calibration_rows(symbols, limit, world_id)
    except (
        Exception
    ) as error:  # noqa: BLE001 - outcome history cannot invalidate a usable current inference generation.
        return {
            "version": "hypothesis-calibration-context-v1",
            "status": "error",
            "source": "typedb-abox-hypothesis-calibration",
            "reason": "TypeDB ABox 가설 결과 보정 조회 실패: " + str(error)[:180],
            "sourceAboxSnapshotId": source_snapshot_id,
            "generationAligned": True,
            "scope": "same-account-symbol-template",
            "decisionEligibility": "historical-review-only",
            "automaticDeployment": False,
            "symbols": sorted(
                {
                    str(item or "").upper().strip()
                    for item in symbols or []
                    if str(item or "").strip()
                }
            ),
            "calibrations": [],
            "calibrationCount": 0,
        }
    return hypothesis_calibration_snapshot_from_abox_rows(
        rows,
        symbols=symbols,
        source_abox_snapshot_id=source_snapshot_id,
        generation_aligned=True,
        # The TypeQL query is restricted by the current active ABox
        # membership pointer. Scoped facts can retain their original
        # manifest ID while still being part of this live worldview.
        active_membership_verified=True,
        limit=limit,
    )


def hypothesis_calibration_snapshot_for_native_result(
    _store: GraphReadsRowsStore,
    matched_graph: PortfolioOntology,
    symbols: Iterable[str],
    source_abox_snapshot_id: str,
    generation_aligned: bool,
    scoped_active_abox: bool,
    limit: int = 40,
    world_id: str = "",
) -> Dict[str, object]:
    """Load calibration from the complete active subject boundary.

    Native materialization intentionally narrows ``matched_graph`` to the
    facts used by matched RuleBox conditions. Calibration is historical
    reasoning state rather than a direct rule premise, so a scoped ABox
    must read that small entity class from active membership separately.
    """
    if scoped_active_abox:
        return _store.hypothesis_calibration_snapshot(
            symbols,
            limit,
            world_id,
            source_abox_snapshot_id=source_abox_snapshot_id,
            generation_aligned=generation_aligned,
        )
    return hypothesis_calibration_snapshot_from_abox_rows(
        [
            row
            for row in _store.rows_for_entities(matched_graph)
            if str(row.get("kind") or "") == "hypothesis-calibration"
            or str(row.get("tboxClass") or "") == "HypothesisCalibration"
        ],
        symbols=symbols,
        source_abox_snapshot_id=source_abox_snapshot_id,
        generation_aligned=generation_aligned,
        limit=limit,
    )


def read_entity_rows_by_ids(
    _store: GraphReadsRowsStore,
    ids: Iterable[str],
    boxes: Iterable[str] = None,
    world_id: str = "",
    *,
    _bindings: GraphReadsRowsRuntime
) -> List[Dict[str, object]]:
    clean_ids = sorted(
        set(str(item or "").strip() for item in ids or [] if str(item or "").strip())
    )
    if not clean_ids:
        return []
    rows: List[Dict[str, object]] = []
    id_filter = typedb_value_match("$n", "ontology-id", clean_ids, "==", "idFilter")
    for box in _bindings.normalized_boxes(boxes):
        active_scope = ""
        active_snapshot = ""
        if box == "ABox":
            active_scope = _store.active_abox_members_clause([("$n", "entityById")], world_id) + " "
        elif box == "InferenceBox" and str(world_id or "").strip():
            active_scope = "$n has ontology-world-id " + typedb_string(world_id) + "; "
        query = (
            "match " + active_scope + "$n isa ontology-node, "
            "has ontology-id $id, "
            "has ontology-label $label, "
            "has ontology-kind $kind, "
            "has ontology-box " + typedb_string(box) + active_snapshot + ", "
            "has ontology-updated-at $updatedAt, "
            "has ontology-json $json; " + id_filter
        )
        rows.extend(
            _store.entity_rows_from_typeql(
                _store.read_rows(
                    query,
                    ["id", "label", "kind", "updatedAt", "json"],
                ),
                box,
            )
        )
    return rows


def read_abox_entity_rows_by_storage_ids(
    _store: GraphReadsRowsStore, storage_ids: Iterable[str], world_id: str = ""
) -> List[Dict[str, object]]:
    """Read exact active ABox entities without expanding scoped pointers.

    ``ontology-storage-id`` includes the immutable scope generation.  A
    verified Manifest index can therefore name one physical stock row
    directly and avoid the high-cardinality Manifest/scope join used by
    general-purpose ABox readers.
    """
    clean_storage_ids = sorted(
        {str(item or "").strip() for item in storage_ids or [] if str(item or "").strip()}
    )
    if not clean_storage_ids:
        return []
    rows: List[Dict[str, object]] = []
    for offset in range(0, len(clean_storage_ids), NATIVE_RULE_EVIDENCE_READ_INDEX_BATCH_SIZE):
        batch = clean_storage_ids[offset : offset + NATIVE_RULE_EVIDENCE_READ_INDEX_BATCH_SIZE]
        query = (
            "match $n isa ontology-node, "
            "has ontology-id $id, "
            "has ontology-label $label, "
            "has ontology-kind $kind, "
            'has ontology-box "ABox", '
            + (
                "has ontology-world-id " + typedb_string(world_id) + ", "
                if str(world_id or "").strip()
                else ""
            )
            + "has ontology-updated-at $updatedAt, "
            "has ontology-json $json; "
            + typedb_value_match("$n", "ontology-storage-id", batch, "==", "storageIdFilter")
        )
        rows.extend(
            _store.entity_rows_from_typeql(
                _store.read_rows(
                    query,
                    ["id", "label", "kind", "updatedAt", "json"],
                    label="typedb.native-evidence.entity-by-storage-id",
                ),
                "ABox",
            )
        )
    return list(
        {str(row.get("id") or ""): row for row in rows if str(row.get("id") or "").strip()}.values()
    )


def read_abox_relation_rows_by_storage_ids(
    _store: GraphReadsRowsStore,
    storage_ids: Iterable[str],
    relation_types: Iterable[str] = None,
    world_id: str = "",
    *,
    _bindings: GraphReadsRowsRuntime
) -> List[Dict[str, object]]:
    """Read exact active ABox evidence edges from Manifest storage IDs."""
    clean_storage_ids = sorted(
        {str(item or "").strip() for item in storage_ids or [] if str(item or "").strip()}
    )
    if not clean_storage_ids:
        return []
    clean_relation_types = sorted(
        {
            str(item or "").upper().strip()
            for item in relation_types or []
            if str(item or "").strip()
        }
    )
    rows: List[Dict[str, object]] = []
    for offset in range(0, len(clean_storage_ids), NATIVE_RULE_EVIDENCE_READ_INDEX_BATCH_SIZE):
        batch = clean_storage_ids[offset : offset + NATIVE_RULE_EVIDENCE_READ_INDEX_BATCH_SIZE]
        query = (
            "match "
            "$source isa ontology-node, has ontology-id $sourceId, has ontology-label $sourceLabel, "
            "has ontology-kind $sourceKind, has ontology-updated-at $sourceUpdatedAt, has ontology-json $sourceJson; "
            "$target isa ontology-node, has ontology-id $targetId, has ontology-label $targetLabel, "
            "has ontology-kind $targetKind, has ontology-updated-at $targetUpdatedAt, has ontology-json $targetJson; "
            "$r isa ontology-assertion, links (source: $source, target: $target), "
            "has ontology-id $id, "
            "has ontology-relation-type $type, "
            'has ontology-box "ABox", '
            + (
                "has ontology-world-id " + typedb_string(world_id) + ", "
                if str(world_id or "").strip()
                else ""
            )
            + "has ontology-updated-at $updatedAt, "
            "has ontology-json $json, "
            "has ontology-weight $weight; "
            + typedb_value_match(
                "$r", "ontology-storage-id", batch, "==", "relationStorageIdFilter"
            )
            + typedb_value_match(
                "$r", "ontology-relation-type", clean_relation_types, "==", "relationTypeFilter"
            )
        )
        raw_rows = _store.read_rows(
            query,
            [
                "id",
                "sourceId",
                "sourceLabel",
                "sourceKind",
                "sourceUpdatedAt",
                "sourceJson",
                "targetId",
                "targetLabel",
                "targetKind",
                "targetUpdatedAt",
                "targetJson",
                "type",
                "updatedAt",
                "json",
                "weight",
            ],
            label="typedb.native-evidence.relation-by-storage-id",
        )
        mapped_rows = _store.relation_rows_from_typeql(raw_rows, "ABox")
        for mapped, raw in zip(mapped_rows, raw_rows):
            mapped["sourceNode"] = _bindings.endpoint_node_row(raw, "source", "ABox")
            mapped["targetNode"] = _bindings.endpoint_node_row(raw, "target", "ABox")
        rows.extend(mapped_rows)
    return list(
        {str(row.get("id") or ""): row for row in rows if str(row.get("id") or "").strip()}.values()
    )


def read_relation_rows_by_source_ids(
    _store: GraphReadsRowsStore,
    source_ids: Iterable[str],
    boxes: Iterable[str] = None,
    relation_types: Iterable[str] = None,
    include_incoming: bool = True,
    world_id: str = "",
    *,
    _bindings: GraphReadsRowsRuntime
) -> List[Dict[str, object]]:
    clean_ids = sorted(
        set(str(item or "").strip() for item in source_ids or [] if str(item or "").strip())
    )
    if not clean_ids:
        return []
    clean_relation_types = sorted(
        set(
            str(item or "").upper().strip()
            for item in relation_types or []
            if str(item or "").strip()
        )
    )
    rows: List[Dict[str, object]] = []
    endpoint_filters = [
        typedb_value_match("$source", "ontology-id", clean_ids, "==", "sourceIdFilter"),
    ]
    if include_incoming:
        endpoint_filters.append(
            typedb_value_match("$target", "ontology-id", clean_ids, "==", "targetIdFilter")
        )
    for box in _bindings.normalized_boxes(boxes):
        active_scope = ""
        active_snapshot = ""
        endpoint_scope = ""
        if box == "ABox":
            active_scope = (
                _store.active_abox_members_clause(
                    [
                        ("$source", "sourceById"),
                        ("$target", "targetById"),
                        ("$r", "relationById"),
                    ],
                    world_id,
                )
                + " "
            )
        elif box == "InferenceBox" and str(world_id or "").strip():
            active_scope = "$r has ontology-world-id " + typedb_string(world_id) + "; "
        for endpoint_filter in endpoint_filters:
            query = (
                "match "
                + active_scope
                + "$source isa ontology-node, has ontology-id $sourceId"
                + endpoint_scope
                + ", has ontology-label $sourceLabel, "
                "has ontology-kind $sourceKind, has ontology-updated-at $sourceUpdatedAt, has ontology-json $sourceJson; "
                "$target isa ontology-node, has ontology-id $targetId"
                + endpoint_scope
                + ", has ontology-label $targetLabel, "
                "has ontology-kind $targetKind, has ontology-updated-at $targetUpdatedAt, has ontology-json $targetJson; "
                "$r isa ontology-assertion, links (source: $source, target: $target), "
                "has ontology-id $id, "
                "has ontology-relation-type $type, "
                "has ontology-box " + typedb_string(box) + active_snapshot + ", "
                "has ontology-updated-at $updatedAt, "
                "has ontology-json $json, "
                "has ontology-weight $weight; "
                + typedb_value_match(
                    "$r", "ontology-relation-type", clean_relation_types, "==", "relationTypeFilter"
                )
                + endpoint_filter
            )
            raw_rows = _store.read_rows(
                query,
                [
                    "id",
                    "sourceId",
                    "sourceLabel",
                    "sourceKind",
                    "sourceUpdatedAt",
                    "sourceJson",
                    "targetId",
                    "targetLabel",
                    "targetKind",
                    "targetUpdatedAt",
                    "targetJson",
                    "type",
                    "updatedAt",
                    "json",
                    "weight",
                ],
            )
            mapped_rows = _store.relation_rows_from_typeql(raw_rows, box)
            for mapped, raw in zip(mapped_rows, raw_rows):
                mapped["sourceNode"] = _bindings.endpoint_node_row(raw, "source", box)
                mapped["targetNode"] = _bindings.endpoint_node_row(raw, "target", box)
            rows.extend(mapped_rows)
    return list(
        {str(row.get("id") or ""): row for row in rows if str(row.get("id") or "").strip()}.values()
    )


def active_abox_relation_types_by_symbol(
    _store: GraphReadsRowsStore,
    symbols: Iterable[str] = None,
    timeout_seconds: float = None,
    world_id: str = "",
    active_abox_metadata: Dict[str, object] = None,
) -> Dict[str, object]:
    """Read a compact active-ABox topology index for RuleBox subjects.

    Native rule planning only needs each stock's available relation types.
    Loading every endpoint's JSON payload for that purpose made the planner
    compete with ABox projection writes and could exceed the realtime read
    deadline. This query keeps the TypeDB-owned topology while returning
    only stock id, symbol, and relation type.
    """
    clean_symbols = clean_symbols_from_payload(list(symbols or []))
    # The active scoped Manifest already contains an integrity-checked
    # index of each stock source and its relation storage rows.  Reading
    # that index avoids repeating a high-cardinality active-scope join for
    # every diagnostic or native-rule planning cycle.  TypeDB remains the
    # source: this is persisted ABox topology, not Python inference.
    active_metadata = dict(active_abox_metadata or {})
    if active_metadata:
        indexed = normalize_native_rule_evidence_read_index(
            active_metadata.get("nativeRuleEvidenceReadIndex"),
            planner_topology=active_metadata.get("nativeRulePlannerTopology"),
            target_symbols=clean_symbols,
        )
    else:
        indexed = {}
    indexed_payload = dict(indexed or {})
    typed_relation_ids = indexed_payload.get("relationStorageIdsBySymbolAndType")
    if (
        str(indexed_payload.get("status") or "") == "ok"
        and isinstance(typed_relation_ids, dict)
        and typed_relation_ids
    ):
        source_ids_by_symbol = {
            str(symbol or "").upper(): sorted(
                {
                    str(source_id or "").strip()
                    for source_id in values or []
                    if str(source_id or "").strip()
                }
            )
            for symbol, values in dict(indexed_payload.get("sourceIdsBySymbol") or {}).items()
            if str(symbol or "").strip()
        }
        relation_types_by_symbol = {
            symbol: sorted(
                {
                    str(relation_type or "").upper().strip()
                    for relation_type in dict(typed_relation_ids.get(symbol) or {})
                    if str(relation_type or "").strip()
                }
            )
            for symbol in source_ids_by_symbol
        }
        relation_ids = {
            str(storage_id or "").strip()
            for relation_types in typed_relation_ids.values()
            if isinstance(relation_types, dict)
            for storage_ids in relation_types.values()
            for storage_id in storage_ids or []
            if str(storage_id or "").strip()
        }
        return {
            "status": "ok",
            "source": "active-manifest-evidence-index",
            "symbols": clean_symbols or sorted(source_ids_by_symbol),
            "sourceIdsBySymbol": source_ids_by_symbol,
            "sourceStorageIdsBySourceId": dict(
                indexed_payload.get("sourceStorageIdsBySourceId") or {}
            ),
            "relationTypesBySymbol": relation_types_by_symbol,
            "relationCount": len(relation_ids),
        }
    source_ids_by_symbol: Dict[str, set] = {symbol: set() for symbol in clean_symbols}
    relation_types_by_symbol: Dict[str, set] = {symbol: set() for symbol in clean_symbols}
    relation_ids = set()
    symbol_filter = typedb_value_match(
        "$stock",
        "ontology-symbol",
        clean_symbols,
        "==",
        "stockSymbolFilter",
    )
    for role, links_clause in [
        ("source", "links (source: $stock, target: $other)"),
        ("target", "links (source: $other, target: $stock)"),
    ]:
        active_scope = (
            _store.active_abox_members_clause(
                [
                    ("$stock", "topologyStock" + role.title()),
                    ("$other", "topologyOther" + role.title()),
                    ("$r", "topologyRelation" + role.title()),
                ],
                world_id,
            )
            + " "
        )
        active_snapshot = ""
        query = (
            "match "
            + active_scope
            + "$stock isa ontology-node, has ontology-id $sourceId, has ontology-kind $sourceKind, "
            'has ontology-box "ABox"' + active_snapshot + ", "
            "has ontology-symbol $symbol; "
            + typedb_value_match(
                "$stock",
                "ontology-kind",
                ["stock", "crypto-asset"],
                "==",
                "topologySourceKindFilter",
            )
            + "$r isa ontology-assertion, "
            + links_clause
            + ", has ontology-id $relationId, "
            'has ontology-box "ABox"' + active_snapshot + ", "
            "has ontology-relation-type $relationType; " + symbol_filter
        )
        rows = _store.read_rows(
            query,
            ["sourceId", "symbol", "relationId", "relationType"],
            label="typedb.active-abox-relation-types:" + role,
            timeout_seconds=timeout_seconds,
        )
        for row in rows:
            symbol = str(row.get("symbol") or "").upper().strip()
            source_id = str(row.get("sourceId") or "").strip()
            relation_type = str(row.get("relationType") or "").upper().strip()
            relation_id = str(row.get("relationId") or "").strip()
            if not symbol:
                continue
            source_ids_by_symbol.setdefault(symbol, set())
            relation_types_by_symbol.setdefault(symbol, set())
            if source_id:
                source_ids_by_symbol[symbol].add(source_id)
            if relation_type:
                relation_types_by_symbol[symbol].add(relation_type)
            if relation_id:
                relation_ids.add(relation_id)
    symbols_out = clean_symbols or sorted(source_ids_by_symbol)
    return {
        "status": "ok",
        "symbols": symbols_out,
        "sourceIdsBySymbol": {
            symbol: sorted(source_ids_by_symbol.get(symbol, set())) for symbol in symbols_out
        },
        "relationTypesBySymbol": {
            symbol: sorted(relation_types_by_symbol.get(symbol, set())) for symbol in symbols_out
        },
        "relationCount": len(relation_ids),
    }


def active_abox_rule_context(
    _store: GraphReadsRowsStore, symbols: Iterable[str], world_id: str = ""
) -> Dict[str, object]:
    """Load only TypeDB facts needed to plan direct TypeQL calls.

    This remains a topology-only execution planner. Direct TypeQL rules
    still evaluate every rule condition and decide whether the rule matches.
    """
    clean_symbols = clean_symbols_from_payload(list(symbols or []))
    if not clean_symbols:
        return {
            "status": "empty",
            "symbols": [],
            "sourceIdsBySymbol": {},
            "relationTypesBySymbol": {},
            "relationCount": 0,
        }
    return _store.active_abox_relation_types_by_symbol(
        clean_symbols,
        timeout_seconds=_store.native_rule_query_timeout_seconds(),
        world_id=world_id,
    )


def read_relation_rows(
    _store: GraphReadsRowsStore,
    boxes: Iterable[str] = None,
    limit: int = 0,
    world_id: str = "",
    snapshot_id: str = "",
    *,
    _bindings: GraphReadsRowsRuntime
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    safe_limit = int(limit or 0)
    normalized = _bindings.normalized_boxes(boxes)
    static_generations: Dict[str, str] = {}
    if any(box in _store.seed_static_box_names() for box in normalized):
        manifest = _store.read_seed_static_manifest()
        if str(manifest.get("status") or "") == "ok":
            static_generations = _store.static_seed_generation_ids(manifest.get("metadata") or {})
    for box in normalized:
        active_scope = ""
        active_snapshot = ""
        endpoint_scope = ""
        if box == "ABox":
            active_scope = (
                _store.active_abox_members_clause(
                    [
                        ("$source", "relationSource"),
                        ("$target", "relationTarget"),
                        ("$r", "relation"),
                    ],
                    world_id,
                )
                + " "
            )
        elif box == "InferenceBox" and str(world_id or "").strip():
            active_scope = "$r has ontology-world-id " + typedb_string(world_id) + "; "
        elif box in _store.seed_static_box_names():
            resolved_snapshot = str(snapshot_id or static_generations.get(box) or "").strip()
            if resolved_snapshot:
                active_snapshot = ", has ontology-snapshot-id " + typedb_string(resolved_snapshot)
        query = (
            "match "
            + active_scope
            + "$source isa ontology-node, has ontology-id $sourceId"
            + endpoint_scope
            + ", has ontology-label $sourceLabel; "
            + "$target isa ontology-node, has ontology-id $targetId"
            + endpoint_scope
            + ", has ontology-label $targetLabel; "
            "$r isa ontology-assertion, links (source: $source, target: $target), "
            "has ontology-id $id, "
            "has ontology-relation-type $type, "
            "has ontology-box " + typedb_string(box) + active_snapshot + ", "
            "has ontology-updated-at $updatedAt, "
            "has ontology-json $json, "
            "has ontology-weight $weight; " + _bindings.typeql_limit_clause(safe_limit)
        )
        rows.extend(
            _store.relation_rows_from_typeql(
                _store.read_rows(
                    query,
                    [
                        "id",
                        "sourceId",
                        "sourceLabel",
                        "targetId",
                        "targetLabel",
                        "type",
                        "updatedAt",
                        "json",
                        "weight",
                    ],
                ),
                box,
            )
        )
        if safe_limit > 0 and len(rows) >= safe_limit:
            break
    rows = sorted(
        rows,
        key=lambda item: (
            str(item.get("updatedAt") or ""),
            str(item.get("source") or ""),
            str(item.get("target") or ""),
        ),
        reverse=True,
    )
    return rows[:safe_limit] if safe_limit > 0 else rows


def read_inferencebox_entity_rows(
    _store: GraphReadsRowsStore,
    generation_id: str = "",
    symbols: Iterable[str] = None,
    limit: int = 0,
    world_id: str = "",
    *,
    _bindings: GraphReadsRowsRuntime
) -> List[Dict[str, object]]:
    safe_limit = int(limit or 0)
    clean_symbols = sorted(
        set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip())
    )
    query = (
        "match $n isa ontology-node, "
        "has ontology-id $id, "
        "has ontology-label $label, "
        "has ontology-kind $kind, "
        'has ontology-box "InferenceBox", '
        + (
            "has ontology-world-id " + typedb_string(world_id) + ", "
            if str(world_id or "").strip()
            else ""
        )
        + "has ontology-updated-at $updatedAt, "
        + "has ontology-json $json; "
    )
    if generation_id:
        query += typedb_value_match(
            "$n", "ontology-snapshot-id", generation_id, "==", "generationFilter"
        )
    if clean_symbols:
        query += typedb_value_match("$n", "ontology-symbol", clean_symbols, "==", "symbolFilter")
    query += _bindings.typeql_limit_clause(safe_limit)
    rows = _store.entity_rows_from_typeql(
        _store.read_rows(
            query,
            ["id", "label", "kind", "updatedAt", "json"],
        ),
        "InferenceBox",
    )
    rows = sorted(
        rows,
        key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("id") or "")),
        reverse=True,
    )
    return rows[:safe_limit] if safe_limit > 0 else rows


def read_inferencebox_relation_rows(
    _store: GraphReadsRowsStore,
    generation_id: str = "",
    symbols: Iterable[str] = None,
    limit: int = 0,
    world_id: str = "",
    *,
    _bindings: GraphReadsRowsRuntime
) -> List[Dict[str, object]]:
    safe_limit = int(limit or 0)
    clean_symbols = sorted(
        set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip())
    )
    query = (
        "match "
        "$source isa ontology-node, has ontology-id $sourceId, has ontology-label $sourceLabel; "
        "$target isa ontology-node, has ontology-id $targetId, has ontology-label $targetLabel; "
        "$r isa ontology-assertion, links (source: $source, target: $target), "
        "has ontology-id $id, "
        "has ontology-relation-type $type, "
        'has ontology-box "InferenceBox", '
        + (
            "has ontology-world-id " + typedb_string(world_id) + ", "
            if str(world_id or "").strip()
            else ""
        )
        + "has ontology-updated-at $updatedAt, "
        + "has ontology-json $json, "
        + "has ontology-weight $weight; "
    )
    if generation_id:
        query += typedb_value_match(
            "$r", "ontology-snapshot-id", generation_id, "==", "generationFilter"
        )
    if clean_symbols:
        query += typedb_value_match("$r", "ontology-symbol", clean_symbols, "==", "symbolFilter")
    query += _bindings.typeql_limit_clause(safe_limit)
    rows = _store.relation_rows_from_typeql(
        _store.read_rows(
            query,
            [
                "id",
                "sourceId",
                "sourceLabel",
                "targetId",
                "targetLabel",
                "type",
                "updatedAt",
                "json",
                "weight",
            ],
        ),
        "InferenceBox",
    )
    rows = sorted(
        rows,
        key=lambda item: (
            str(item.get("updatedAt") or ""),
            str(item.get("source") or ""),
            str(item.get("target") or ""),
        ),
        reverse=True,
    )
    return rows[:safe_limit] if safe_limit > 0 else rows


def entity_row_from_typeql(
    _store: GraphReadsRowsStore,
    row: Dict[str, object],
    box: str,
    *,
    _bindings: GraphReadsRowsRuntime
) -> Dict[str, object]:
    props = json_object(row.get("json"))
    node_kind = str(row.get("kind") or props.get("kind") or "")
    merged = _bindings.merge_flat_properties(
        {
            "id": row.get("id"),
            "label": row.get("label"),
            "kind": node_kind,
            "ontologyBox": box,
            "symbol": row.get("symbol"),
            "ruleId": row.get("ruleId"),
            "tboxClass": row.get("tboxClass"),
            "updatedAt": row.get("updatedAt"),
        },
        props,
    )
    condition = merged.get("condition") if isinstance(merged.get("condition"), dict) else {}
    derivation = merged.get("derivation") if isinstance(merged.get("derivation"), dict) else {}
    proposed = merged.get("proposedRule") if isinstance(merged.get("proposedRule"), dict) else None
    payload = {
        **merged,
        "id": str(row.get("id") or merged.get("id") or ""),
        "label": str(row.get("label") or merged.get("label") or row.get("id") or ""),
        "nodeKind": node_kind,
        "kind": (
            str(condition.get("kind") or merged.get("conditionKind") or node_kind)
            if node_kind == "rule-condition"
            else node_kind
        ),
        "ontologyBox": str(box or merged.get("ontologyBox") or "ABox"),
        "symbol": str(row.get("symbol") or merged.get("symbol") or ""),
        "ruleId": str(row.get("ruleId") or merged.get("ruleId") or ""),
        "sourceRuleId": str(
            merged.get("sourceRuleId") or row.get("ruleId") or merged.get("ruleId") or ""
        ),
        "nativeRuleId": str(
            merged.get("nativeRuleId")
            or typedb_native_rule_id(row.get("ruleId") or merged.get("ruleId"))
        ),
        "semanticRuleId": str(
            merged.get("semanticRuleId")
            or merged.get("nativeRuleId")
            or typedb_native_rule_id(row.get("ruleId") or merged.get("ruleId"))
        ),
        "reasoningLayer": str(merged.get("reasoningLayer") or ""),
        "reasoningMode": str(merged.get("reasoningMode") or ""),
        "materializationSource": str(merged.get("materializationSource") or ""),
        "typedbNativeRuleReasoned": bool(merged.get("typedbNativeRuleReasoned")),
        "tboxClass": str(row.get("tboxClass") or merged.get("tboxClass") or ""),
        "updatedAt": str(row.get("updatedAt") or merged.get("updatedAt") or ""),
        "propertiesJson": json.dumps(props, ensure_ascii=False, sort_keys=True),
        "version": str(merged.get("version") or ""),
        "sourceKind": str(merged.get("sourceKind") or ""),
        "actionGroup": str(merged.get("actionGroup") or merged.get("action_group") or ""),
        "actionLevel": str(merged.get("actionLevel") or merged.get("action_level") or ""),
        "promptHint": str(merged.get("promptHint") or ""),
        "anyConditionMinCount": int(number_or_none(merged.get("anyConditionMinCount")) or 1),
        "enabled": bool(merged.get("enabled", True)),
        "conditionId": str(merged.get("conditionId") or condition.get("condition_id") or ""),
        "conditionIndex": int(number_or_none(merged.get("conditionIndex")) or 0),
        "conditionKind": str(condition.get("kind") or merged.get("conditionKind") or ""),
        "conditionField": str(condition.get("field") or merged.get("conditionField") or ""),
        "conditionOperator": str(
            condition.get("operator") or merged.get("conditionOperator") or ""
        ),
        "conditionRole": str(condition.get("role") or merged.get("conditionRole") or "required"),
        "conditionValueString": str(
            condition.get("value") or merged.get("conditionValueString") or ""
        ),
        "conditionValueNumber": number_or_none(
            condition.get("value") if "value" in condition else merged.get("conditionValueNumber")
        ),
        "conditionRelationType": str(
            condition.get("relation_type") or merged.get("conditionRelationType") or ""
        ).upper(),
        "conditionDirection": str(
            condition.get("direction") or merged.get("conditionDirection") or "out"
        ),
        "conditionTargetKind": str(
            condition.get("target_kind") or merged.get("conditionTargetKind") or ""
        ),
        "conditionRelationEvidenceRoles": condition_relation_filter_values(
            condition, "evidenceRole"
        ),
        "derivationIndex": int(number_or_none(merged.get("derivationIndex")) or 0),
        "derivationRelationType": str(
            derivation.get("relation_type") or merged.get("derivationRelationType") or ""
        ).upper(),
        "derivationTargetKind": str(
            derivation.get("target_kind") or merged.get("derivationTargetKind") or ""
        ),
        "derivationTargetKey": str(
            derivation.get("target_key") or merged.get("derivationTargetKey") or ""
        ),
        "derivationTargetLabel": str(
            derivation.get("target_label") or merged.get("derivationTargetLabel") or ""
        ),
        "derivationTboxClass": str(
            derivation.get("tbox_class") or merged.get("derivationTboxClass") or ""
        ),
        "derivationTboxClasses": _bindings.list_of_strings(
            derivation.get("tbox_classes") or merged.get("derivationTboxClasses")
        ),
        "derivationPolarity": str(
            derivation.get("polarity") or merged.get("derivationPolarity") or ""
        ),
        "derivationEvidenceRole": str(
            derivation.get("evidence_role")
            or derivation.get("evidenceRole")
            or merged.get("derivationEvidenceRole")
            or derivation.get("polarity")
            or "context"
        ),
        "derivationBeliefLabel": str(
            derivation.get("belief_label") or merged.get("derivationBeliefLabel") or ""
        ),
        "derivationAiInfluenceLabel": str(
            derivation.get("ai_influence_label") or merged.get("derivationAiInfluenceLabel") or ""
        ),
        "derivationActionGroup": str(
            derivation.get("action_group") or merged.get("derivationActionGroup") or ""
        ),
        "derivationActionLevel": str(
            derivation.get("action_level") or merged.get("derivationActionLevel") or ""
        ),
        "derivationDecisionStage": str(
            derivation.get("decision_stage")
            or derivation.get("decisionStage")
            or merged.get("derivationDecisionStage")
            or ""
        ),
        "derivationDecisionEffect": str(
            derivation.get("decision_effect")
            or derivation.get("decisionEffect")
            or merged.get("derivationDecisionEffect")
            or ""
        ),
        "derivationDecisionLabel": str(
            derivation.get("decision_label")
            or derivation.get("decisionLabel")
            or merged.get("derivationDecisionLabel")
            or ""
        ),
        "derivationDecisionTone": str(
            derivation.get("decision_tone")
            or derivation.get("decisionTone")
            or merged.get("derivationDecisionTone")
            or ""
        ),
        "derivationTargetRole": str(
            derivation.get("target_role")
            or derivation.get("targetRole")
            or merged.get("derivationTargetRole")
            or ""
        ),
        "derivationActionPolicy": str(
            derivation.get("action_policy")
            or derivation.get("actionPolicy")
            or merged.get("derivationActionPolicy")
            or ""
        ),
        "derivationAllowedActions": _bindings.list_of_strings(
            derivation.get("allowed_actions")
            or derivation.get("allowedActions")
            or merged.get("derivationAllowedActions")
        ),
        "derivationBlockedActions": _bindings.list_of_strings(
            derivation.get("blocked_actions")
            or derivation.get("blockedActions")
            or merged.get("derivationBlockedActions")
        ),
        "derivationPrimaryAction": str(
            derivation.get("primary_action")
            or derivation.get("primaryAction")
            or merged.get("derivationPrimaryAction")
            or ""
        ),
        "derivationPrimaryActionLabel": str(
            derivation.get("primary_action_label")
            or derivation.get("primaryActionLabel")
            or merged.get("derivationPrimaryActionLabel")
            or ""
        ),
        "derivationCandidateAction": str(
            derivation.get("candidate_action")
            or derivation.get("candidateAction")
            or merged.get("derivationCandidateAction")
            or ""
        ),
        "derivationCandidateActionLabel": str(
            derivation.get("candidate_action_label")
            or derivation.get("candidateActionLabel")
            or merged.get("derivationCandidateActionLabel")
            or ""
        ),
        "derivationBlockedActionLabels": _bindings.list_of_strings(
            derivation.get("blocked_action_labels")
            or derivation.get("blockedActionLabels")
            or merged.get("derivationBlockedActionLabels")
        ),
        "derivationStrengthenConditions": _bindings.list_of_strings(
            derivation.get("strengthen_conditions")
            or derivation.get("strengthenConditions")
            or merged.get("derivationStrengthenConditions")
        ),
        "derivationWeakenConditions": _bindings.list_of_strings(
            derivation.get("weaken_conditions")
            or derivation.get("weakenConditions")
            or merged.get("derivationWeakenConditions")
        ),
        "derivationNextChecks": _bindings.list_of_strings(
            derivation.get("next_checks")
            or derivation.get("nextChecks")
            or merged.get("derivationNextChecks")
        ),
        "derivationNotificationCategory": str(
            derivation.get("notification_category")
            or derivation.get("notificationCategory")
            or merged.get("derivationNotificationCategory")
            or ""
        ),
        "derivationNotificationSeverity": str(
            derivation.get("notification_severity")
            or derivation.get("notificationSeverity")
            or merged.get("derivationNotificationSeverity")
            or ""
        ),
        "polarity": str(merged.get("polarity") or ""),
        "evidenceRole": str(merged.get("evidenceRole") or "context"),
        "decisionStage": str(merged.get("decisionStage") or ""),
        "decisionEffect": str(merged.get("decisionEffect") or merged.get("decision_effect") or ""),
        "reviewLevel": str(merged.get("reviewLevel") or "observe"),
        "reviewLevelLabel": str(merged.get("reviewLevelLabel") or ""),
        "dataState": str(merged.get("dataState") or "partial"),
        "dataStateLabel": str(merged.get("dataStateLabel") or ""),
        "conflictState": str(merged.get("conflictState") or "context-only"),
        "nativeTypeDbReasoned": bool(merged.get("nativeTypeDbReasoned")),
        "title": str(merged.get("title") or row.get("label") or ""),
        "status": str(merged.get("status") or ""),
        "priority": number_or_none(merged.get("priority")) or 0,
        "source": str(merged.get("source") or ""),
        "rationale": str(merged.get("rationale") or ""),
        "expectedEffect": str(merged.get("expectedEffect") or ""),
        "risk": str(merged.get("risk") or ""),
        "action": str(merged.get("action") or ""),
        "requiresData": _bindings.list_of_strings(merged.get("requiresData")),
        "proposedRuleJson": (
            json.dumps(proposed, ensure_ascii=False, sort_keys=True)
            if proposed
            else str(merged.get("proposedRuleJson") or "")
        ),
        "validationWarnings": _bindings.list_of_strings(merged.get("validationWarnings")),
        "promptVersion": str(merged.get("promptVersion") or ""),
        "createdAt": str(merged.get("createdAt") or ""),
        "symbols": _bindings.list_of_strings(merged.get("symbols")),
    }
    return payload


def relation_row_from_typeql(
    _store: GraphReadsRowsStore,
    row: Dict[str, object],
    box: str,
    *,
    _bindings: GraphReadsRowsRuntime
) -> Dict[str, object]:
    props = json_object(row.get("json"))
    merged = _bindings.merge_flat_properties(
        {
            "source": row.get("sourceId"),
            "sourceLabel": row.get("sourceLabel"),
            "target": row.get("targetId"),
            "targetLabel": row.get("targetLabel"),
            "type": row.get("type"),
            "ruleId": row.get("ruleId"),
            "ontologyBox": box,
            "updatedAt": row.get("updatedAt"),
            "weight": row.get("weight"),
        },
        props,
    )
    return {
        **merged,
        "id": str(row.get("id") or merged.get("id") or ""),
        "source": str(row.get("sourceId") or merged.get("source") or ""),
        "sourceLabel": str(row.get("sourceLabel") or merged.get("sourceLabel") or ""),
        "target": str(row.get("targetId") or merged.get("target") or ""),
        "targetLabel": str(row.get("targetLabel") or merged.get("targetLabel") or ""),
        "type": str(row.get("type") or merged.get("type") or ""),
        "relationType": str(
            row.get("type") or merged.get("relationType") or merged.get("type") or ""
        ),
        "ontologyBox": str(box or merged.get("ontologyBox") or "ABox"),
        "symbol": str(merged.get("symbol") or ""),
        "ruleId": str(row.get("ruleId") or merged.get("ruleId") or ""),
        "sourceRuleId": str(
            merged.get("sourceRuleId") or row.get("ruleId") or merged.get("ruleId") or ""
        ),
        "nativeRuleId": str(
            merged.get("nativeRuleId")
            or typedb_native_rule_id(row.get("ruleId") or merged.get("ruleId"))
        ),
        "semanticRuleId": str(
            merged.get("semanticRuleId")
            or merged.get("nativeRuleId")
            or typedb_native_rule_id(row.get("ruleId") or merged.get("ruleId"))
        ),
        "reasoningLayer": str(merged.get("reasoningLayer") or ""),
        "reasoningMode": str(merged.get("reasoningMode") or ""),
        "materializationSource": str(merged.get("materializationSource") or ""),
        "typedbNativeRuleReasoned": bool(merged.get("typedbNativeRuleReasoned")),
        "weight": number_or_none(
            row.get("weight") if row.get("weight") is not None else merged.get("weight")
        ),
        "updatedAt": str(row.get("updatedAt") or merged.get("updatedAt") or ""),
        "propertiesJson": json.dumps(props, ensure_ascii=False, sort_keys=True),
        "polarity": str(merged.get("polarity") or ""),
        "evidenceRole": str(merged.get("evidenceRole") or "context"),
        "decisionStage": str(merged.get("decisionStage") or ""),
        "decisionEffect": str(merged.get("decisionEffect") or merged.get("decision_effect") or ""),
        "reviewLevel": str(merged.get("reviewLevel") or "observe"),
        "dataState": str(merged.get("dataState") or "partial"),
        "targetRole": str(merged.get("targetRole") or ""),
        "actionPolicy": str(merged.get("actionPolicy") or ""),
        "allowedActions": _bindings.list_of_strings(merged.get("allowedActions")),
        "blockedActions": _bindings.list_of_strings(merged.get("blockedActions")),
        "aiInfluenceLabel": str(merged.get("aiInfluenceLabel") or ""),
        "inferenceTraceId": str(merged.get("inferenceTraceId") or ""),
        "nativeTypeDbReasoned": bool(merged.get("nativeTypeDbReasoned")),
    }
