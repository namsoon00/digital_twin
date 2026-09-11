"""Row queries implementation; facade-independent dependencies."""

from __future__ import annotations
from .row_queries_ports import (
    RowQueriesPort,
    NodeInsertClauseBindings,
    RelationMatchClauseBindings,
    RelationInsertClauseBindings,
    InferenceboxInsertQueriesBindings,
    InferenceboxGivenRelationInsertPlansBindings,
)
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.abox_candidates.identity import (
    ontology_row_content_fingerprint,
    ontology_storage_id,
    relation_row_id,
)
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import (
    json_object,
)
from digital_twin.modules.reasoning.infrastructure.typeql.constants import (
    TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES,
    TYPEDB_PROMOTED_TEXT_ATTRIBUTES,
)
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from digital_twin.modules.reasoning.infrastructure.typeql.storage_schema import (
    typedb_entity_storage_type,
    typedb_relation_storage_type,
)
from typing import Dict, Iterable, List


def node_insert_clause(
    _store: RowQueriesPort,
    row: Dict[str, object],
    updated_at: str,
    variable: str,
    *,
    _bindings: NodeInsertClauseBindings,
) -> str:
    properties = json_object(row.get("propertiesJson"))
    semantic_properties = {
        "tboxClass": row.get("tboxClass"),
        "tboxClasses": row.get("tboxClasses") or [],
    }
    node_type = typedb_entity_storage_type(
        semantic_properties,
        row.get("kind"),
        fallback=str(row.get("nodeType") or "ontology-entity"),
    )
    allowed_attributes = _bindings.typedb_node_allowed_attributes(
        semantic_properties,
        row.get("kind"),
    )

    def node_has(attribute: str, value: object, numeric: bool = False) -> str:
        if allowed_attributes is not None and attribute not in allowed_attributes:
            return ""
        return _bindings.typeql_has(attribute, value, numeric=numeric)

    def node_has_bool(attribute: str, value: object) -> str:
        if allowed_attributes is not None and attribute not in allowed_attributes:
            return ""
        return _bindings.typeql_has_bool_string(attribute, value)

    node_id = str(row.get("id") or "")
    return (
        str(variable or "$n")
        + " isa "
        + node_type
        + ", has ontology-id "
        + typedb_string(node_id)
        + ", has ontology-storage-id "
        + typedb_string(ontology_storage_id(row, node_id, "node"))
        + node_has(
            "ontology-content-fingerprint",
            row.get("contentFingerprint")
            or ontology_row_content_fingerprint(row, "node"),
        )
        + node_has("ontology-label", row.get("label"))
        + node_has("ontology-kind", row.get("kind"))
        + node_has("ontology-box", row.get("ontologyBox") or "ABox")
        + node_has("ontology-symbol", row.get("symbol"))
        + node_has("ontology-rule-id", row.get("ruleId"))
        + node_has("ontology-account-id", row.get("accountId"))
        + node_has("ontology-tenant-id", row.get("tenantId"))
        + node_has("ontology-world-id", row.get("worldId"))
        + node_has("ontology-world-type", row.get("worldType"))
        + node_has(
            "ontology-snapshot-id", row.get("snapshotId") or row.get("aboxSnapshotId")
        )
        + node_has("ontology-scope-id", row.get("scopeId"))
        + node_has("ontology-scope-type", row.get("scopeType"))
        + node_has("ontology-manifest-id", row.get("manifestId"))
        + node_has("ontology-tbox-class", row.get("tboxClass"))
        + node_has("ontology-semantic-type", node_type)
        + node_has("ontology-relation-type", row.get("relationTypeName"))
        + node_has("ontology-updated-at", updated_at)
        + node_has("ontology-json", row.get("propertiesJson"))
        + node_has("ontology-source-value", row.get("sourceValue"))
        + node_has("ontology-field", row.get("field"))
        + node_has("ontology-level-type", row.get("levelType"))
        + node_has("ontology-data-scope", row.get("dataScope"))
        + node_has("ontology-domain-scope", row.get("domainScope"))
        + node_has("ontology-relation-scope", row.get("relationScope"))
        + node_has("ontology-group", row.get("group"))
        + node_has("ontology-polarity", row.get("polarity"))
        + node_has("ontology-evidence-role", row.get("evidenceRole"))
        + node_has("ontology-review-level", row.get("reviewLevel"))
        + node_has("ontology-data-state", row.get("dataState"))
        + node_has("ontology-change-state", row.get("changeState"))
        + node_has("ontology-conflict-state", row.get("conflictState"))
        + node_has("ontology-validation-state", row.get("validationState"))
        + node_has("ontology-event-type", row.get("eventType"))
        + node_has_bool("ontology-materiality-passed", row.get("materialityPassed"))
        + node_has("ontology-value-number", row.get("valueNumber"), numeric=True)
        + node_has("ontology-profit-loss-rate", row.get("profitLossRate"), numeric=True)
        + node_has_bool("ontology-allow-add-on-strength", row.get("allowAddOnStrength"))
        + node_has_bool("ontology-trim-on-trend-break", row.get("trimOnTrendBreak"))
        + node_has_bool("ontology-avoid-averaging-down", row.get("avoidAveragingDown"))
        + node_has("ontology-impact-polarity", row.get("impactPolarity"))
        + node_has_bool("ontology-needs-review", row.get("needsReview"))
        + node_has("ontology-read-scope", row.get("readScope"))
        + node_has("ontology-pe-ratio", row.get("peRatio"), numeric=True)
        + node_has("ontology-beta", row.get("beta"), numeric=True)
        + "".join(
            node_has(
                attribute,
                _bindings.promoted_node_value(row, properties, field),
                numeric=True,
            )
            for field, attribute in TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES.items()
        )
        + "".join(
            node_has(
                attribute, _bindings.promoted_node_text_value(row, properties, field)
            )
            for field, attribute in TYPEDB_PROMOTED_TEXT_ATTRIBUTES.items()
        )
    )


def relation_insert_query(
    _store: RowQueriesPort, row: Dict[str, object], updated_at: str
) -> str:
    return (
        "match "
        + _store.relation_match_clause(row, "$source", "$target")
        + "insert "
        + _store.relation_insert_clause(row, updated_at, "$r", "$source", "$target")
        + ";"
    )


def relation_match_clause(
    _store: RowQueriesPort,
    row: Dict[str, object],
    source_variable: str,
    target_variable: str,
    *,
    _bindings: RelationMatchClauseBindings,
) -> str:
    source_storage_id = str(row.get("sourceStorageId") or "").strip()
    target_storage_id = str(row.get("targetStorageId") or "").strip()
    if source_storage_id and target_storage_id:
        return (
            str(source_variable or "$source")
            + " isa ontology-node, has ontology-storage-id "
            + typedb_string(source_storage_id)
            + "; "
            + str(target_variable or "$target")
            + " isa ontology-node, has ontology-storage-id "
            + typedb_string(target_storage_id)
            + "; "
        )
    snapshot_id = row.get("snapshotId") or row.get("aboxSnapshotId")
    ontology_box = str(row.get("ontologyBox") or "ABox").strip() or "ABox"
    # A live ABox generation can contain the same public ontology ID as
    # its predecessor while activation is still pending. Match its
    # endpoints by the generation-scoped unique storage identity instead
    # of scanning all nodes with the public ID and snapshot attribute.
    # Static TBox/RuleBox relations may cross boxes, so they retain the
    # public-ID lookup below.
    if ontology_box == "ABox" and str(snapshot_id or "").strip():
        return (
            str(source_variable or "$source")
            + " isa ontology-node, has ontology-storage-id "
            + typedb_string(ontology_storage_id(row, row.get("source"), "node"))
            + "; "
            + str(target_variable or "$target")
            + " isa ontology-node, has ontology-storage-id "
            + typedb_string(ontology_storage_id(row, row.get("target"), "node"))
            + "; "
        )
    snapshot_match = _bindings.typeql_has("ontology-snapshot-id", snapshot_id)
    return (
        str(source_variable or "$source")
        + " isa ontology-node, has ontology-id "
        + typedb_string(row.get("source"))
        + snapshot_match
        + "; "
        + str(target_variable or "$target")
        + " isa ontology-node, has ontology-id "
        + typedb_string(row.get("target"))
        + snapshot_match
        + "; "
    )


def relation_insert_clause(
    _store: RowQueriesPort,
    row: Dict[str, object],
    updated_at: str,
    relation_variable: str,
    source_variable: str,
    target_variable: str,
    *,
    _bindings: RelationInsertClauseBindings,
) -> str:
    relation_id = relation_row_id(row)
    relation_type = typedb_relation_storage_type(row.get("type"))
    return (
        str(relation_variable or "$r")
        + " isa "
        + relation_type
        + ", links (source: "
        + str(source_variable or "$source")
        + ", target: "
        + str(target_variable or "$target")
        + ")"
        + ", has ontology-id "
        + typedb_string(relation_id)
        + ", has ontology-storage-id "
        + typedb_string(ontology_storage_id(row, relation_id, "relation"))
        + _bindings.typeql_has(
            "ontology-content-fingerprint",
            row.get("contentFingerprint")
            or ontology_row_content_fingerprint(row, "relation"),
        )
        + _bindings.typeql_has("ontology-relation-type", row.get("type"))
        + _bindings.typeql_has("ontology-box", row.get("ontologyBox") or "ABox")
        + _bindings.typeql_has("ontology-symbol", row.get("symbol"))
        + _bindings.typeql_has("ontology-rule-id", row.get("ruleId"))
        + _bindings.typeql_has("ontology-account-id", row.get("accountId"))
        + _bindings.typeql_has("ontology-tenant-id", row.get("tenantId"))
        + _bindings.typeql_has("ontology-world-id", row.get("worldId"))
        + _bindings.typeql_has("ontology-world-type", row.get("worldType"))
        + _bindings.typeql_has(
            "ontology-snapshot-id", row.get("snapshotId") or row.get("aboxSnapshotId")
        )
        + _bindings.typeql_has("ontology-scope-id", row.get("scopeId"))
        + _bindings.typeql_has("ontology-scope-type", row.get("scopeType"))
        + _bindings.typeql_has("ontology-manifest-id", row.get("manifestId"))
        + _bindings.typeql_has("ontology-tbox-class", row.get("tboxClass"))
        + _bindings.typeql_has("ontology-semantic-type", relation_type)
        + _bindings.typeql_has("ontology-updated-at", updated_at)
        + _bindings.typeql_has("ontology-json", row.get("propertiesJson"))
        + _bindings.typeql_has("ontology-weight", row.get("weight"), numeric=True)
        + _bindings.typeql_has("ontology-field", row.get("field"))
        + _bindings.typeql_has("ontology-polarity", row.get("polarity"))
        + _bindings.typeql_has("ontology-evidence-role", row.get("evidenceRole"))
        + _bindings.typeql_has("ontology-review-level", row.get("reviewLevel"))
        + _bindings.typeql_has("ontology-data-state", row.get("dataState"))
        + _bindings.typeql_has("ontology-change-state", row.get("changeState"))
        + _bindings.typeql_has("ontology-conflict-state", row.get("conflictState"))
        + _bindings.typeql_has("ontology-validation-state", row.get("validationState"))
        + _bindings.typeql_has("ontology-transition-type", row.get("transitionType"))
        + _bindings.typeql_has("ontology-signal-group", row.get("signalGroup"))
        + _bindings.typeql_has_bool_string(
            "ontology-materiality-passed", row.get("materialityPassed")
        )
        + _bindings.typeql_has(
            "ontology-materiality-state", row.get("materialityState")
        )
        + _bindings.typeql_has("ontology-relevance-state", row.get("relevanceState"))
        + _bindings.typeql_has(
            "ontology-source-trust-state", row.get("sourceTrustState")
        )
    )


def batched_node_insert_queries(
    _store: RowQueriesPort,
    rows: Iterable[Dict[str, object]],
    updated_at: str,
    batch_size: int = 40,
    max_query_bytes: int = 0,
) -> List[str]:
    items = [row for row in rows or [] if str((row or {}).get("id") or "").strip()]
    maximum_count = max(1, int(batch_size or 40))
    maximum_bytes = max(0, int(max_query_bytes or 0))
    queries: List[str] = []
    clauses: List[str] = []
    query_bytes = _store.query_byte_size("insert ")
    for row in items:
        clause = (
            _store.node_insert_clause(row, updated_at, "$n" + str(len(clauses))) + ";"
        )
        clause_bytes = _store.query_byte_size(clause)
        candidate_bytes = query_bytes + clause_bytes + (1 if clauses else 0)
        if clauses and (
            len(clauses) >= maximum_count
            or (maximum_bytes and candidate_bytes > maximum_bytes)
        ):
            queries.append("insert " + " ".join(clauses))
            clauses = []
            query_bytes = _store.query_byte_size("insert ")
            clause = _store.node_insert_clause(row, updated_at, "$n0") + ";"
            clause_bytes = _store.query_byte_size(clause)
        clauses.append(clause)
        query_bytes += clause_bytes + (1 if len(clauses) > 1 else 0)
    if clauses:
        queries.append("insert " + " ".join(clauses))
    return queries


def node_batch_insert_query(
    _store: RowQueriesPort, rows: Iterable[Dict[str, object]], updated_at: str
) -> str:
    inserts = [
        _store.node_insert_clause(row, updated_at, "$n" + str(index)) + ";"
        for index, row in enumerate(rows or [])
    ]
    return "insert " + " ".join(inserts)


def batched_relation_insert_queries(
    _store: RowQueriesPort,
    rows: Iterable[Dict[str, object]],
    updated_at: str,
    batch_size: int = 25,
    max_query_bytes: int = 0,
) -> List[str]:
    items = [
        row
        for row in rows or []
        if str((row or {}).get("source") or "").strip()
        and str((row or {}).get("target") or "").strip()
    ]
    maximum_count = max(1, int(batch_size or 25))
    maximum_bytes = max(0, int(max_query_bytes or 0))
    queries: List[str] = []
    matches: List[str] = []
    inserts: List[str] = []
    query_bytes = _store.query_byte_size("match ") + _store.query_byte_size(" insert ")
    for row in items:
        index = len(matches)
        source_var = "$source" + str(index)
        target_var = "$target" + str(index)
        relation_var = "$r" + str(index)
        match = _store.relation_match_clause(row, source_var, target_var)
        insert = (
            _store.relation_insert_clause(
                row, updated_at, relation_var, source_var, target_var
            )
            + ";"
        )
        candidate_bytes = (
            query_bytes
            + _store.query_byte_size(match)
            + _store.query_byte_size(insert)
            + (2 if matches else 0)
        )
        if matches and (
            len(matches) >= maximum_count
            or (maximum_bytes and candidate_bytes > maximum_bytes)
        ):
            queries.append(
                "match " + " ".join(matches) + " insert " + " ".join(inserts)
            )
            matches = []
            inserts = []
            query_bytes = _store.query_byte_size("match ") + _store.query_byte_size(
                " insert "
            )
            source_var = "$source0"
            target_var = "$target0"
            relation_var = "$r0"
            match = _store.relation_match_clause(row, source_var, target_var)
            insert = (
                _store.relation_insert_clause(
                    row, updated_at, relation_var, source_var, target_var
                )
                + ";"
            )
        matches.append(match)
        inserts.append(insert)
        query_bytes += (
            _store.query_byte_size(match)
            + _store.query_byte_size(insert)
            + (2 if len(matches) > 1 else 0)
        )
    if matches:
        queries.append("match " + " ".join(matches) + " insert " + " ".join(inserts))
    return queries


def relation_batch_insert_query(
    _store: RowQueriesPort, rows: Iterable[Dict[str, object]], updated_at: str
) -> str:
    matches = []
    inserts = []
    for index, row in enumerate(rows or []):
        source_var = "$source" + str(index)
        target_var = "$target" + str(index)
        relation_var = "$r" + str(index)
        matches.append(_store.relation_match_clause(row, source_var, target_var))
        inserts.append(
            _store.relation_insert_clause(
                row, updated_at, relation_var, source_var, target_var
            )
            + ";"
        )
    return "match " + " ".join(matches) + " insert " + " ".join(inserts)


def _given_relation_value(value: object, value_type: str) -> object:
    if value_type == "double":
        return float(value)
    return str(value)


def given_relation_row_values(
    _store: RowQueriesPort, row: Dict[str, object]
) -> List[tuple]:
    """Return the stable typed inputs for a relation ``given`` query."""
    relation_id = relation_row_id(row)
    values = [
        (
            "source-storage-id",
            "ontology-storage-id",
            "string",
            str(
                row.get("sourceStorageId")
                or ontology_storage_id(row, row.get("source"), "node")
            ),
        ),
        (
            "target-storage-id",
            "ontology-storage-id",
            "string",
            str(
                row.get("targetStorageId")
                or ontology_storage_id(row, row.get("target"), "node")
            ),
        ),
        ("relation-id", "ontology-id", "string", relation_id),
        (
            "relation-storage-id",
            "ontology-storage-id",
            "string",
            ontology_storage_id(row, relation_id, "relation"),
        ),
        (
            "content-fingerprint",
            "ontology-content-fingerprint",
            "string",
            row.get("contentFingerprint")
            or ontology_row_content_fingerprint(row, "relation"),
        ),
        ("relation-type", "ontology-relation-type", "string", row.get("type")),
        ("ontology-box", "ontology-box", "string", row.get("ontologyBox") or "ABox"),
        ("ontology-symbol", "ontology-symbol", "string", row.get("symbol")),
        ("ontology-rule-id", "ontology-rule-id", "string", row.get("ruleId")),
        ("ontology-account-id", "ontology-account-id", "string", row.get("accountId")),
        ("ontology-tenant-id", "ontology-tenant-id", "string", row.get("tenantId")),
        ("ontology-world-id", "ontology-world-id", "string", row.get("worldId")),
        ("ontology-world-type", "ontology-world-type", "string", row.get("worldType")),
        (
            "ontology-snapshot-id",
            "ontology-snapshot-id",
            "string",
            row.get("snapshotId") or row.get("aboxSnapshotId"),
        ),
        ("ontology-scope-id", "ontology-scope-id", "string", row.get("scopeId")),
        ("ontology-scope-type", "ontology-scope-type", "string", row.get("scopeType")),
        (
            "ontology-manifest-id",
            "ontology-manifest-id",
            "string",
            row.get("manifestId"),
        ),
        ("ontology-tbox-class", "ontology-tbox-class", "string", row.get("tboxClass")),
        ("ontology-json", "ontology-json", "string", row.get("propertiesJson")),
        ("ontology-weight", "ontology-weight", "double", row.get("weight")),
        ("ontology-field", "ontology-field", "string", row.get("field")),
        ("ontology-polarity", "ontology-polarity", "string", row.get("polarity")),
        (
            "ontology-evidence-role",
            "ontology-evidence-role",
            "string",
            row.get("evidenceRole"),
        ),
        (
            "ontology-review-level",
            "ontology-review-level",
            "string",
            row.get("reviewLevel"),
        ),
        ("ontology-data-state", "ontology-data-state", "string", row.get("dataState")),
        (
            "ontology-change-state",
            "ontology-change-state",
            "string",
            row.get("changeState"),
        ),
        (
            "ontology-conflict-state",
            "ontology-conflict-state",
            "string",
            row.get("conflictState"),
        ),
        (
            "ontology-validation-state",
            "ontology-validation-state",
            "string",
            row.get("validationState"),
        ),
        (
            "ontology-transition-type",
            "ontology-transition-type",
            "string",
            row.get("transitionType"),
        ),
        (
            "ontology-signal-group",
            "ontology-signal-group",
            "string",
            row.get("signalGroup"),
        ),
        (
            "ontology-materiality-passed",
            "ontology-materiality-passed",
            "string",
            row.get("materialityPassed"),
        ),
        (
            "ontology-materiality-state",
            "ontology-materiality-state",
            "string",
            row.get("materialityState"),
        ),
        (
            "ontology-relevance-state",
            "ontology-relevance-state",
            "string",
            row.get("relevanceState"),
        ),
        (
            "ontology-source-trust-state",
            "ontology-source-trust-state",
            "string",
            row.get("sourceTrustState"),
        ),
    ]
    return [item for item in values if _store._given_relation_has_value(item[3])]


def given_relation_insert_plans(
    _store: RowQueriesPort,
    rows: Iterable[Dict[str, object]],
    updated_at: str,
    settings: Dict[str, object] = None,
) -> List[Dict[str, object]]:
    """Build TypeDB 3.12 ``given`` relation inserts grouped by query shape.

    Rows with different optional attributes require different TypeQL
    shapes. Grouping by relation type and attribute presence keeps each
    query plan stable and avoids the old cross-product of independent
    endpoint matches.
    """
    items = [
        dict(row)
        for row in rows or []
        if str((row or {}).get("source") or "").strip()
        and str((row or {}).get("target") or "").strip()
    ]
    if not items:
        return []
    if not _store.given_relation_writes_enabled(settings):
        return [
            {"query": query, "rows": [], "givenRows": []}
            for query in _store.batched_relation_insert_queries(
                items,
                updated_at,
                _store.abox_relation_batch_size(settings),
                _store.write_query_max_bytes(settings),
            )
        ]

    grouped: Dict[tuple, List[tuple]] = {}
    for row in items:
        fields = _store.given_relation_row_values(row)
        relation_type = typedb_relation_storage_type(row.get("type"))
        signature = tuple(
            (name, attribute, value_type)
            for name, attribute, value_type, _value in fields
        )
        grouped.setdefault((relation_type, signature), []).append((row, fields))

    plans: List[Dict[str, object]] = []
    maximum = _store.given_relation_batch_size(settings)
    for (relation_type, signature), grouped_rows in grouped.items():
        declarations = ", ".join(
            "$" + name + ": " + value_type for name, _attribute, value_type in signature
        )
        relation_attributes = "".join(
            ", has " + attribute + " == $" + name
            for name, attribute, _value_type in signature
            if name not in {"source-storage-id", "target-storage-id"}
        )
        query = (
            "given " + declarations + "; "
            "match $source isa ontology-node, has ontology-storage-id == $source-storage-id; "
            "$target isa ontology-node, has ontology-storage-id == $target-storage-id; "
            "insert $r isa "
            + relation_type
            + ", links (source: $source, target: $target)"
            + relation_attributes
            + ", has ontology-semantic-type "
            + typedb_string(relation_type)
            + ", has ontology-updated-at "
            + typedb_string(updated_at)
            + ";"
        )
        for offset in range(0, len(grouped_rows), maximum):
            chunk = grouped_rows[offset : offset + maximum]
            plans.append(
                {
                    "query": query,
                    "rows": [row for row, _fields in chunk],
                    "givenRows": [
                        {
                            name: _store._given_relation_value(value, value_type)
                            for name, _attribute, value_type, value in fields
                        }
                        for _row, fields in chunk
                    ],
                    "relationType": relation_type,
                    "rowCount": len(chunk),
                }
            )
    return plans


def inferencebox_insert_queries(
    _store: RowQueriesPort,
    node_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
    updated_at: str,
    *,
    _bindings: InferenceboxInsertQueriesBindings,
) -> List[str]:
    settings = _bindings.runtime_settings()
    node_batch_size = int(
        number_or_none(settings.get("typedbInferenceBoxNodeBatchSize")) or 25
    )
    relation_batch_size = _store.inferencebox_relation_batch_size(settings)
    max_query_bytes = _store.write_query_max_bytes(settings)
    return [
        *_store.batched_node_insert_queries(
            node_rows, updated_at, node_batch_size, max_query_bytes
        ),
        *_store.batched_relation_insert_queries(
            relation_rows, updated_at, relation_batch_size, max_query_bytes
        ),
    ]


def inferencebox_given_relation_insert_plans(
    _store: RowQueriesPort,
    rows: Iterable[Dict[str, object]],
    updated_at: str,
    settings: Dict[str, object] = None,
    *,
    _bindings: InferenceboxGivenRelationInsertPlansBindings,
) -> List[Dict[str, object]]:
    values = dict(_bindings.runtime_settings() if settings is None else settings or {})
    values["typedbABoxGivenRelationWritesEnabled"] = (
        "1" if _store.inferencebox_given_relation_writes_enabled(values) else "0"
    )
    values["typedbABoxGivenRelationBatchSize"] = str(
        _store.inferencebox_given_relation_batch_size(values)
    )
    values["typedbABoxRelationBatchSize"] = str(
        _store.inferencebox_relation_batch_size(values)
    )
    return _store.given_relation_insert_plans(rows, updated_at, settings=values)
