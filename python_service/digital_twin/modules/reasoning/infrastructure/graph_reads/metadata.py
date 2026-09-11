"""graph_reads: metadata through explicit injected capabilities."""

from digital_twin.domain.ontology_schema import default_tbox_metadata
from digital_twin.domain.ontology_scopes import (
    SCOPED_ABOX_MANIFEST_VERSION,
    SCOPED_ABOX_PERSISTENCE_MODE,
)
from digital_twin.domain.ontology_semantics import (
    SEMANTIC_STORAGE_CONTRACT_VERSION,
    semantic_class_types,
    semantic_relation_types,
)
from .tbox_metadata import active_tbox_metadata_from_rows, active_tbox_metadata_unavailable
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.abox_persistence.world_calls import (
    typedb_call_for_world,
)
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import (
    json_object,
    typedb_bool,
)
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    native_rule_manifest_index_required,
)
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
)
from typing import Dict, List
import hashlib
from .metadata_ports import GraphReadsMetadataStore, GraphReadsMetadataRuntime


def active_tbox_metadata(
    _store: GraphReadsMetadataStore, *, _bindings: GraphReadsMetadataRuntime
) -> Dict[str, object]:
    if not _store.address:
        return _bindings.NullTypeDBOntologyGraphRepository().active_tbox_metadata()
    # A static-seed manifest is a keyed, content-addressed record written
    # only after the matching TBox generation and schema contract are
    # ready. Reading every static TBox node and relation here made each
    # live ABox projection pay for an unbounded TypeQL graph scan.
    manifest = _store.read_seed_static_manifest()
    manifest_metadata = dict(manifest.get("metadata") or {})
    manifest_status = str(manifest.get("status") or "")
    tbox_version = str(manifest_metadata.get("tboxVersion") or "")
    tbox_fingerprint = str(manifest_metadata.get("tboxFingerprint") or "")
    if manifest_status == "ok" and tbox_version and tbox_fingerprint:
        box_counts = manifest_metadata.get("boxCounts")
        tbox_counts = dict(box_counts.get("TBox") or {}) if isinstance(box_counts, dict) else {}
        fallback = default_tbox_metadata()
        expected_schema = _store.base_schema_contract_metadata()
        stored_schema_version = str(manifest_metadata.get("schemaContractVersion") or "")
        stored_schema_fingerprint = str(manifest_metadata.get("schemaContractFingerprint") or "")
        schema_current = stored_schema_version == str(
            expected_schema.get("schemaContractVersion") or ""
        ) and stored_schema_fingerprint == str(
            expected_schema.get("schemaContractFingerprint") or ""
        )
        metadata = active_tbox_metadata_from_rows(
            {
                "entities": [
                    {
                        "entityCount": int(
                            tbox_counts.get("entityCount") or fallback.get("entityCount") or 1
                        ),
                        "version": tbox_version,
                        "fingerprint": tbox_fingerprint,
                        "updatedAt": str(manifest_metadata.get("updatedAt") or ""),
                    }
                ],
                "relations": [
                    {
                        "relationCount": int(
                            tbox_counts.get("relationCount") or fallback.get("relationCount") or 0
                        ),
                    }
                ],
            },
            "typedb-static-seed-manifest",
        )
        metadata.update(
            {
                "graphStore": "typedb",
                "source": "typedb-static-seed-manifest",
                "storeSource": "typedb-static-seed-manifest",
                "semanticStorage": {
                    "contractVersion": SEMANTIC_STORAGE_CONTRACT_VERSION,
                    "physicalStorage": "typedb-logical-tbox-subtypes",
                    "physicalClassTypeCount": len(semantic_class_types()),
                    "physicalRelationTypeCount": len(semantic_relation_types()),
                    "schemaContractStatus": "current" if schema_current else "stale",
                    "schemaContractFingerprint": str(
                        expected_schema.get("schemaContractFingerprint") or ""
                    ),
                },
            }
        )
        return metadata
    try:
        entity_rows = _store.read_entity_rows(["TBox"])
        relation_rows = _store.read_relation_rows(["TBox"])
    except Exception as error:  # noqa: BLE001 - metadata must be safe for UI/bootstrap.
        metadata = active_tbox_metadata_unavailable("error", str(error)[:180], "typedb")
        metadata.update({"graphStore": "typedb", "storeSource": "typedb-typeql"})
        return metadata
    version = ""
    fingerprint = ""
    updated_at = ""
    for row in entity_rows:
        props = json_object(row.get("propertiesJson"))
        version = version or str(
            row.get("version") or props.get("version") or props.get("tboxVersion") or ""
        )
        fingerprint = fingerprint or str(
            row.get("fingerprint") or props.get("fingerprint") or props.get("tboxFingerprint") or ""
        )
        updated_at = max(updated_at, str(row.get("updatedAt") or props.get("updatedAt") or ""))
    metadata = active_tbox_metadata_from_rows(
        {
            "entities": [
                {
                    "entityCount": len(entity_rows),
                    "version": version,
                    "fingerprint": fingerprint,
                    "updatedAt": updated_at,
                }
            ],
            "relations": [{"relationCount": len(relation_rows)}],
        },
        "typedb-typeql",
    )
    try:
        schema_contract = _store.base_schema_contract_state()
    except (
        Exception
    ) as error:  # noqa: BLE001 - TBox metadata remains useful when the seed marker is temporarily unavailable.
        schema_contract = {
            "status": "unavailable",
            "reason": str(error)[:180],
        }
    metadata.update(
        {
            "graphStore": "typedb",
            "source": "typedb-typeql",
            "storeSource": "typedb-typeql",
            "semanticStorage": {
                "contractVersion": SEMANTIC_STORAGE_CONTRACT_VERSION,
                "physicalStorage": "typedb-logical-tbox-subtypes",
                "physicalClassTypeCount": len(semantic_class_types()),
                "physicalRelationTypeCount": len(semantic_relation_types()),
                "schemaContractStatus": str(schema_contract.get("status") or "unavailable"),
                "schemaContractFingerprint": str(
                    schema_contract.get("schemaContractFingerprint") or ""
                ),
            },
        }
    )
    return metadata


def box_snapshot_row_counts(
    _store: GraphReadsMetadataStore, box: str, snapshot_id: str, world_id: str = ""
) -> Dict[str, int]:
    clean_box = str(box or "").strip()
    clean_snapshot_id = str(snapshot_id or "").strip()
    if not clean_box or not clean_snapshot_id:
        return {"entityCount": 0, "relationCount": 0}

    def count(type_label: str) -> int:
        query = (
            "match $item isa "
            + type_label
            + ", has ontology-box "
            + typedb_string(clean_box)
            + ", has ontology-snapshot-id "
            + typedb_string(clean_snapshot_id)
            + (
                ", has ontology-world-id " + typedb_string(world_id)
                if str(world_id or "").strip()
                else ""
            )
            + "; reduce $count = count;"
        )
        rows = _store.read_rows(query, ["count"], label="typedb.box-snapshot-count")
        return int(number_or_none((rows[0] if rows else {}).get("count")) or 0)

    return {
        "entityCount": count("ontology-node"),
        "relationCount": count("ontology-assertion"),
    }


def box_row_counts(_store: GraphReadsMetadataStore, box: str, world_id: str = "") -> Dict[str, int]:
    """Count one ontology box without loading its full JSON payloads."""
    clean_box = str(box or "").strip()
    if not clean_box:
        return {"entityCount": 0, "relationCount": 0}

    def count(type_label: str) -> int:
        query = (
            "match $item isa "
            + type_label
            + ", has ontology-box "
            + typedb_string(clean_box)
            + (
                ", has ontology-world-id " + typedb_string(world_id)
                if str(world_id or "").strip()
                else ""
            )
            + "; reduce $count = count;"
        )
        rows = _store.read_rows(query, ["count"], label="typedb.box-count")
        return int(number_or_none((rows[0] if rows else {}).get("count")) or 0)

    return {
        "entityCount": count("ontology-node"),
        "relationCount": count("ontology-assertion"),
    }


def abox_projection_marker_rows(
    _store: GraphReadsMetadataStore,
    world_id: str = "",
    snapshot_id: str = "",
    limit: int = 0,
    *,
    _bindings: GraphReadsMetadataRuntime
) -> List[Dict[str, object]]:
    snapshot_clause = (
        "has ontology-snapshot-id " + typedb_string(snapshot_id) + ", "
        if str(snapshot_id or "").strip()
        else ""
    )
    query = (
        "match $n isa ontology-node, "
        "has ontology-id $id, "
        "has ontology-label $label, "
        'has ontology-kind "abox-projection-marker", '
        'has ontology-box "ABox", '
        + (
            "has ontology-world-id " + typedb_string(world_id) + ", "
            if str(world_id or "").strip()
            else ""
        )
        + snapshot_clause
        + "has ontology-updated-at $updatedAt, "
        "has ontology-json $json;" + _bindings.typeql_limit_clause(limit)
    )
    return _store.entity_rows_from_typeql(
        _store.read_rows(
            query, ["id", "label", "kind", "updatedAt", "json"], label="typedb.abox-marker"
        ),
        "ABox",
    )


def active_worldview_manifest_pointer_rows(
    _store: GraphReadsMetadataStore,
    world_id: str = "",
    limit: int = 0,
    *,
    _bindings: GraphReadsMetadataRuntime
) -> List[Dict[str, object]]:
    query = (
        "match $n isa ontology-node, "
        "has ontology-id $id, "
        "has ontology-label $label, "
        'has ontology-kind "worldview-manifest-active-pointer", '
        'has ontology-box "ABoxControl", '
        + (
            "has ontology-world-id " + typedb_string(world_id) + ", "
            if str(world_id or "").strip()
            else ""
        )
        + "has ontology-snapshot-id $snapshotId, "
        "has ontology-updated-at $updatedAt, "
        "has ontology-json $json;" + _bindings.typeql_limit_clause(limit)
    )
    return _store.entity_rows_from_typeql(
        _store.read_rows(
            query,
            ["id", "label", "kind", "snapshotId", "updatedAt", "json"],
            label="typedb.worldview-manifest-active-pointer",
        ),
        "ABoxControl",
    )


def active_worldview_manifest_pointer_identity_rows(
    _store: GraphReadsMetadataStore,
    world_id: str = "",
    limit: int = 0,
    *,
    _bindings: GraphReadsMetadataRuntime
) -> List[Dict[str, object]]:
    """Read just enough active-pointer state to validate a cached Manifest.

    The active pointer used to duplicate the full scoped Manifest payload.
    Deserialising that JSON on each native-rule stage made the control
    plane more expensive than a one-subject inference.  Its TypeQL
    attributes already expose the immutable Manifest id and revision, so
    keep the hot path free of ``ontology-json`` entirely.
    """
    clean_world_id = str(world_id or "").strip()
    query = (
        "match $n isa ontology-node, "
        "has ontology-id $id, "
        'has ontology-kind "worldview-manifest-active-pointer", '
        'has ontology-box "ABoxControl", '
        + (
            "has ontology-world-id " + typedb_string(clean_world_id) + ", "
            if clean_world_id
            else ""
        )
        + "has ontology-snapshot-id $snapshotId, "
        "has ontology-updated-at $updatedAt;" + _bindings.typeql_limit_clause(limit)
    )
    rows = _store.read_rows(
        query,
        ["id", "snapshotId", "updatedAt"],
        label="typedb.worldview-manifest-active-pointer-identity",
    )
    return [
        {
            "id": str(row.get("id") or ""),
            "snapshotId": str(row.get("snapshotId") or ""),
            "worldviewManifestId": str(row.get("snapshotId") or ""),
            "updatedAt": str(row.get("updatedAt") or ""),
            "worldId": clean_world_id,
        }
        for row in rows or []
        if str(row.get("id") or "")
    ]


def worldview_manifest_marker_count(_store: GraphReadsMetadataStore, world_id: str = "") -> int:
    """Count Manifest markers without materializing their large JSON bodies."""
    clean_world_id = str(world_id or "").strip()
    query = (
        "match $n isa ontology-node, "
        'has ontology-kind "worldview-manifest-marker", '
        'has ontology-box "ABox"'
        + (", has ontology-world-id " + typedb_string(clean_world_id) if clean_world_id else "")
        + "; reduce $count = count;"
    )
    rows = _store.read_rows(
        query,
        ["count"],
        label="typedb.worldview-manifest-marker-count",
    )
    return max(0, int(number_or_none((rows[0] if rows else {}).get("count")) or 0))


def worldview_manifest_marker_rows(
    _store: GraphReadsMetadataStore,
    world_id: str = "",
    manifest_id: str = "",
    limit: int = 0,
    *,
    _bindings: GraphReadsMetadataRuntime
) -> List[Dict[str, object]]:
    manifest_clause = (
        "has ontology-snapshot-id " + typedb_string(manifest_id) + ", "
        if str(manifest_id or "").strip()
        else ""
    )
    query = (
        "match $n isa ontology-node, "
        "has ontology-id $id, "
        "has ontology-label $label, "
        'has ontology-kind "worldview-manifest-marker", '
        'has ontology-box "ABox", '
        + (
            "has ontology-world-id " + typedb_string(world_id) + ", "
            if str(world_id or "").strip()
            else ""
        )
        + manifest_clause
        + "has ontology-snapshot-id $snapshotId, "
        "has ontology-updated-at $updatedAt, "
        "has ontology-json $json;" + _bindings.typeql_limit_clause(limit)
    )
    return _store.entity_rows_from_typeql(
        _store.read_rows(
            query,
            ["id", "label", "kind", "snapshotId", "updatedAt", "json"],
            label="typedb.worldview-manifest-marker",
        ),
        "ABox",
    )


def worldview_manifest_marker_identity_rows(
    _store: GraphReadsMetadataStore,
    world_id: str = "",
    manifest_id: str = "",
    limit: int = 0,
    *,
    _bindings: GraphReadsMetadataRuntime
) -> List[Dict[str, object]]:
    """Read a Manifest revision without loading its large JSON payload."""
    clean_world_id = str(world_id or "").strip()
    clean_manifest_id = str(manifest_id or "").strip()
    manifest_clause = (
        "has ontology-snapshot-id " + typedb_string(clean_manifest_id) + ", "
        if clean_manifest_id
        else ""
    )
    query = (
        "match $n isa ontology-node, "
        "has ontology-id $id, "
        'has ontology-kind "worldview-manifest-marker", '
        'has ontology-box "ABox", '
        + (
            "has ontology-world-id " + typedb_string(clean_world_id) + ", "
            if clean_world_id
            else ""
        )
        + manifest_clause
        + "has ontology-snapshot-id $snapshotId, "
        "has ontology-updated-at $updatedAt;" + _bindings.typeql_limit_clause(limit)
    )
    rows = _store.read_rows(
        query,
        ["id", "snapshotId", "updatedAt"],
        label="typedb.worldview-manifest-marker-identity",
    )
    return [
        {
            "id": str(row.get("id") or ""),
            "snapshotId": str(row.get("snapshotId") or ""),
            "worldviewManifestId": str(row.get("snapshotId") or ""),
            "updatedAt": str(row.get("updatedAt") or ""),
            "worldId": clean_world_id,
        }
        for row in rows or []
        if str(row.get("id") or "")
    ]


def scoped_abox_metadata_from_manifest_marker(marker: Dict[str, object]) -> Dict[str, object]:
    payload = dict(marker or {})
    manifest_id = str(
        payload.get("worldviewManifestId")
        or payload.get("aboxSnapshotId")
        or payload.get("snapshotId")
        or ""
    ).strip()
    scope_plan = payload.get("scopePlan") if isinstance(payload.get("scopePlan"), list) else []
    generations = (
        payload.get("scopeGenerationIds")
        if isinstance(payload.get("scopeGenerationIds"), dict)
        else {}
    )
    fingerprints = (
        payload.get("scopeFingerprints")
        if isinstance(payload.get("scopeFingerprints"), dict)
        else {}
    )
    if not manifest_id or not scope_plan or not generations:
        return {}
    return {
        "configured": True,
        "status": "ok",
        "graphStore": "typedb",
        "aboxSnapshotId": manifest_id,
        "worldviewManifestId": manifest_id,
        "worldId": str(payload.get("worldId") or ""),
        "worldType": str(payload.get("worldType") or ""),
        "tenantId": str(payload.get("tenantId") or ""),
        "accountId": str(payload.get("accountId") or ""),
        "materialFingerprint": str(payload.get("materialFingerprint") or ""),
        "projectionRunId": str(payload.get("projectionRunId") or ""),
        "asOf": str(payload.get("asOf") or ""),
        "lastFullScopeReconcileAt": str(payload.get("lastFullScopeReconcileAt") or ""),
        "scopedAboxManifestVersion": str(
            payload.get("scopedAboxManifestVersion") or SCOPED_ABOX_MANIFEST_VERSION
        ),
        "persistenceMode": str(
            payload.get("persistenceMode")
            or payload.get("physicalStateMode")
            or SCOPED_ABOX_PERSISTENCE_MODE
        ),
        "physicalStateMode": str(
            payload.get("physicalStateMode")
            or payload.get("persistenceMode")
            or SCOPED_ABOX_PERSISTENCE_MODE
        ),
        "scopePlan": list(scope_plan),
        "scopeGenerationIds": dict(generations),
        "logicalScopeGenerationIds": dict(payload.get("logicalScopeGenerationIds") or {}),
        "scopeFingerprints": dict(fingerprints),
        "scopeTopologyVersion": str(payload.get("scopeTopologyVersion") or ""),
        "scopeFamilyCounts": dict(payload.get("scopeFamilyCounts") or {}),
        "scopeDelta": dict(payload.get("scopeDelta") or {}),
        "inferenceImpactPlan": dict(payload.get("inferenceImpactPlan") or {}),
        "nativeRulePlannerTopology": dict(payload.get("nativeRulePlannerTopology") or {}),
        "nativeRuleEvidenceReadIndex": dict(payload.get("nativeRuleEvidenceReadIndex") or {}),
        "nativeRuleEvidenceReadIndexRequired": bool(
            payload.get("nativeRuleEvidenceReadIndexRequired")
            if "nativeRuleEvidenceReadIndexRequired" in payload
            else native_rule_manifest_index_required(payload)
        ),
        "nativeRuleEvidenceReadIndexStatus": str(
            payload.get("nativeRuleEvidenceReadIndexStatus") or ""
        ),
        "activeScopeCount": len(generations),
        "manifestMarkerId": str(payload.get("id") or ""),
        "marketScopeObservedAt": dict(payload.get("marketScopeObservedAt") or {}),
        "marketScopeObservedAtVersion": str(payload.get("marketScopeObservedAtVersion") or ""),
        "marketWorldProjectionMode": str(payload.get("marketWorldProjectionMode") or ""),
        "sharedWorldProjection": str(payload.get("sharedWorldProjection") or ""),
        "sharedWorldProjectionContractVersion": str(
            payload.get("sharedWorldProjectionContractVersion") or ""
        ),
        "sharedWorldFullRebuild": bool(payload.get("sharedWorldFullRebuild")),
        "accountOverlayProjectionContractVersion": str(
            payload.get("accountOverlayProjectionContractVersion") or ""
        ),
        "worldPartitionedReasoningVersion": str(
            payload.get("worldPartitionedReasoningVersion") or ""
        ),
        "marketContextMode": str(payload.get("marketContextMode") or ""),
        "marketReadMirrorRemoved": bool(payload.get("marketReadMirrorRemoved")),
        "sharedPremiseWorldId": str(payload.get("sharedPremiseWorldId") or ""),
        "sharedPremiseInferenceGenerationId": str(
            payload.get("sharedPremiseInferenceGenerationId") or ""
        ),
        "sharedPremiseSourceAboxSnapshotId": str(
            payload.get("sharedPremiseSourceAboxSnapshotId") or ""
        ),
    }


def active_abox_pointer_rows(
    _store: GraphReadsMetadataStore,
    world_id: str = "",
    limit: int = 0,
    *,
    _bindings: GraphReadsMetadataRuntime
) -> List[Dict[str, object]]:
    query = (
        "match $n isa ontology-node, "
        "has ontology-id $id, "
        "has ontology-label $label, "
        'has ontology-kind "abox-active-pointer", '
        'has ontology-box "ABoxControl", '
        + (
            "has ontology-world-id " + typedb_string(world_id) + ", "
            if str(world_id or "").strip()
            else ""
        )
        + "has ontology-snapshot-id $snapshotId, "
        "has ontology-updated-at $updatedAt, "
        "has ontology-json $json;" + _bindings.typeql_limit_clause(limit)
    )
    return _store.entity_rows_from_typeql(
        _store.read_rows(
            query,
            ["id", "label", "kind", "snapshotId", "updatedAt", "json"],
            label="typedb.abox-active-pointer",
        ),
        "ABoxControl",
    )


def abox_metadata_from_marker(
    _store: GraphReadsMetadataStore, marker: Dict[str, object]
) -> Dict[str, object]:
    snapshot_id = str(marker.get("aboxSnapshotId") or marker.get("snapshotId") or "").strip()
    expected_entities = number_or_none(marker.get("expectedAboxEntityCount"))
    expected_relations = number_or_none(marker.get("expectedAboxRelationCount"))
    if not snapshot_id or expected_entities is None or expected_relations is None:
        return {}
    try:
        actual = typedb_call_for_world(
            _store.box_snapshot_row_counts,
            "ABox",
            snapshot_id,
            world_id=str(marker.get("worldId") or ""),
        )
    except Exception as error:  # noqa: BLE001 - metadata must describe a read verification failure.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "aboxSnapshotId": snapshot_id,
            "materialFingerprint": str(marker.get("materialFingerprint") or "").strip(),
            "reason": str(error)[:180],
        }
    expected = {
        "entityCount": int(expected_entities),
        "relationCount": int(expected_relations),
    }
    complete = (
        actual["entityCount"] == expected["entityCount"] + 1
        and actual["relationCount"] == expected["relationCount"]
    )
    return {
        "configured": True,
        "status": "ok" if complete else "incomplete",
        "graphStore": "typedb",
        "aboxSnapshotId": snapshot_id,
        "materialFingerprint": str(marker.get("materialFingerprint") or "").strip(),
        "projectionRunId": str(marker.get("projectionRunId") or "").strip(),
        "asOf": str(marker.get("asOf") or ""),
        "expectedEntityCount": expected["entityCount"],
        "expectedRelationCount": expected["relationCount"],
        "actualEntityCount": actual["entityCount"] - 1 if actual["entityCount"] else 0,
        "actualRelationCount": actual["relationCount"],
        "completionMarkerId": str(marker.get("id") or ""),
    }


def active_abox_metadata(_store: GraphReadsMetadataStore, world_id: str = "") -> Dict[str, object]:
    try:
        manifests = sorted(
            _store.active_worldview_manifest_pointer_identity_rows(world_id, limit=1),
            key=lambda row: (str(row.get("updatedAt") or ""), str(row.get("id") or "")),
            reverse=True,
        )
    except Exception:
        # A rolling deployment can have a healthy legacy ABox while the
        # newer Manifest control attributes are not queryable yet.
        manifests = []
    if manifests:
        pointer = manifests[0]
        manifest_id = str(
            pointer.get("worldviewManifestId")
            or pointer.get("aboxSnapshotId")
            or pointer.get("snapshotId")
            or ""
        ).strip()
        cache_world_id = str(world_id or pointer.get("worldId") or "").strip()
        try:
            marker_identities = _store.worldview_manifest_marker_identity_rows(
                cache_world_id,
                manifest_id=manifest_id,
                limit=1,
            )
        except Exception:
            marker_identities = []
        marker_identity = next(
            (
                item
                for item in marker_identities
                if str(
                    item.get("worldviewManifestId")
                    or item.get("aboxSnapshotId")
                    or item.get("snapshotId")
                    or ""
                ).strip()
                == manifest_id
            ),
            marker_identities[0] if marker_identities else {},
        )
        marker_identity_id = str(marker_identity.get("id") or "").strip()
        marker_identity_manifest_id = str(
            marker_identity.get("worldviewManifestId")
            or marker_identity.get("aboxSnapshotId")
            or marker_identity.get("snapshotId")
            or ""
        ).strip()
        cache_key = (
            cache_world_id,
            str(pointer.get("id") or "").strip(),
            str(pointer.get("updatedAt") or "").strip(),
            manifest_id,
            marker_identity_id + "@" + str(marker_identity.get("updatedAt") or "").strip(),
        )
        if marker_identity_id and marker_identity_manifest_id == manifest_id:
            with _store._active_scoped_abox_metadata_cache_lock:
                cached_metadata = _store._active_scoped_abox_metadata_cache.get(cache_key)
            if cached_metadata is not None:
                # Callers have historically received a mutable top-level
                # payload. Preserve that contract while treating the
                # immutable Manifest substructures as read-only snapshots.
                return dict(cached_metadata)
        try:
            markers = _store.worldview_manifest_marker_rows(
                cache_world_id,
                manifest_id=manifest_id,
                limit=1,
            )
        except Exception:
            markers = []
        marker = next(
            (
                item
                for item in markers
                if str(
                    item.get("worldviewManifestId")
                    or item.get("aboxSnapshotId")
                    or item.get("snapshotId")
                    or ""
                ).strip()
                == manifest_id
            ),
            markers[0] if markers else {},
        )
        metadata = _store.scoped_abox_metadata_from_manifest_marker(marker)
        if metadata:
            metadata["activePointerId"] = str(pointer.get("id") or "")
            metadata.setdefault("worldId", cache_world_id)
            if marker_identity_id and marker_identity_manifest_id == manifest_id:
                with _store._active_scoped_abox_metadata_cache_lock:
                    # A repository follows at most a small number of live
                    # worlds. Discard superseded revisions for this world
                    # rather than retaining every historical Manifest.
                    for stale_key in [
                        key
                        for key in _store._active_scoped_abox_metadata_cache
                        if key[0] == cache_world_id and key != cache_key
                    ]:
                        _store._active_scoped_abox_metadata_cache.pop(stale_key, None)
                    _store._active_scoped_abox_metadata_cache[cache_key] = dict(metadata)
            return metadata
        return {
            "configured": True,
            "status": "incomplete",
            "graphStore": "typedb",
            "aboxSnapshotId": manifest_id,
            "worldviewManifestId": manifest_id,
            "activePointerId": str(pointer.get("id") or ""),
            "reason": "Active Worldview Manifest pointer has no complete manifest marker.",
        }
    pointers = sorted(
        _store.active_abox_pointer_rows(world_id, limit=1),
        key=lambda row: (str(row.get("updatedAt") or ""), str(row.get("id") or "")),
        reverse=True,
    )
    if pointers:
        pointer = pointers[0]
        snapshot_id = str(pointer.get("aboxSnapshotId") or pointer.get("snapshotId") or "").strip()
        try:
            markers = _store.abox_projection_marker_rows(world_id, snapshot_id=snapshot_id, limit=1)
        except Exception:
            markers = []
        marker = next(
            (
                item
                for item in markers
                if str(item.get("aboxSnapshotId") or item.get("snapshotId") or "").strip()
                == snapshot_id
            ),
            markers[0] if markers else None,
        )
        if marker:
            metadata = _store.abox_metadata_from_marker(marker)
            if metadata:
                metadata["activePointerId"] = str(pointer.get("id") or "")
                return metadata
        return {
            "configured": True,
            "status": "incomplete",
            "graphStore": "typedb",
            "aboxSnapshotId": snapshot_id,
            "materialFingerprint": str(pointer.get("materialFingerprint") or "").strip(),
            "activePointerId": str(pointer.get("id") or ""),
            "reason": "Active ABox pointer has no complete candidate marker.",
        }
    markers = sorted(
        _store.abox_projection_marker_rows(world_id, limit=1),
        key=lambda row: (str(row.get("updatedAt") or ""), str(row.get("id") or "")),
        reverse=True,
    )
    if markers:
        newest = _store.abox_metadata_from_marker(markers[0])
        return {
            "configured": True,
            "status": "empty",
            "graphStore": "typedb",
            "aboxSnapshotId": "",
            "materialFingerprint": "",
            "pendingAboxSnapshotId": str(newest.get("aboxSnapshotId") or ""),
            "reason": "ABox active pointer is missing.",
        }
    return {
        "configured": True,
        "status": "empty",
        "graphStore": "typedb",
        "aboxSnapshotId": "",
        "materialFingerprint": "",
    }


def active_inference_generation_marker_rows(
    _store: GraphReadsMetadataStore,
    world_id: str = "",
    limit: int = 1,
    *,
    _bindings: GraphReadsMetadataRuntime
) -> List[Dict[str, object]]:
    """Read the active InferenceBox marker without scanning its facts."""
    query = (
        "match $n isa ontology-node, "
        "has ontology-id $id, "
        "has ontology-label $label, "
        'has ontology-kind "inference-generation", '
        'has ontology-box "InferenceBox", '
        + (
            "has ontology-world-id " + typedb_string(world_id) + ", "
            if str(world_id or "").strip()
            else ""
        )
        + "has ontology-snapshot-id $snapshotId, "
        "has ontology-updated-at $updatedAt, "
        "has ontology-json $json;" + _bindings.typeql_limit_clause(limit)
    )
    return _store.entity_rows_from_typeql(
        _store.read_rows(
            query,
            ["id", "label", "kind", "snapshotId", "updatedAt", "json"],
            label="typedb.inference-active-generation-marker",
        ),
        "InferenceBox",
    )


def inferencebox_recovery_metadata(
    _store: GraphReadsMetadataStore, world_id: str = "", *, _bindings: GraphReadsMetadataRuntime
) -> Dict[str, object]:
    """Read only active InferenceBox generation provenance for recovery.

    This intentionally does not expand entities, relations, traces, or
    historical generations. A pending event is supposed to materialize a
    new generation after recovery, so old target coverage cannot be a
    precondition for reopening the projection circuit.
    """
    try:
        markers = sorted(
            _store.active_inference_generation_marker_rows(world_id, limit=1),
            key=lambda row: (str(row.get("updatedAt") or ""), str(row.get("id") or "")),
            reverse=True,
        )
    except (
        Exception
    ) as error:  # noqa: BLE001 - recovery diagnostics must not scan a full InferenceBox.
        return {
            "configured": True,
            "status": "error",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": "TypeDB active InferenceBox marker 조회 실패: " + str(error)[:180],
        }
    if not markers:
        return {
            "configured": True,
            "status": "missing",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "reason": "현재 활성 InferenceBox 세대 표식이 없습니다. 다음 추론에서 새 세대를 만듭니다.",
        }
    marker = markers[0]
    metadata = _bindings.inference_rulebox_metadata([marker], [])
    target_symbols = clean_symbols_from_payload(
        metadata.get("targetSymbols") or marker.get("targetSymbols") or []
    )
    full_completed = typedb_bool(metadata.get("nativeInferenceEvaluationComplete"))
    core_completed = typedb_bool(metadata.get("coreNativeInferenceEvaluationComplete"))
    decision_eligible = _bindings.native_inference_decision_eligible(metadata)
    return {
        "configured": True,
        "status": "ok",
        "graphStore": "typedb",
        "worldId": str(marker.get("worldId") or world_id or ""),
        "inferenceGenerationId": str(
            marker.get("inferenceGenerationId")
            or marker.get("snapshotId")
            or marker.get("aboxSnapshotId")
            or ""
        ).strip(),
        "sourceAboxSnapshotId": str(
            metadata.get("sourceAboxSnapshotId") or marker.get("sourceAboxSnapshotId") or ""
        ).strip(),
        "targetSymbols": target_symbols,
        # A support-only rule may fail after every core action rule has
        # completed.  That generation remains decision-safe, but it must
        # retain the partial-coverage provenance instead of pretending
        # every explanatory rule succeeded.
        "nativeTypeDbReasoningCompleted": decision_eligible,
        "nativeTypeDbFullReasoningCompleted": full_completed,
        "coreNativeInferenceEvaluationComplete": core_completed,
        "nativeCoverageStatus": str(metadata.get("nativeCoverageStatus") or ""),
        "supportingRuleFailureCount": int(
            number_or_none(metadata.get("supportingRuleFailureCount")) or 0
        ),
        "supportingRuleFailures": list(metadata.get("supportingRuleFailures") or [])[:20],
        "nativeInferenceOutcome": str(metadata.get("nativeInferenceOutcome") or ""),
        "reasoningMode": str(metadata.get("reasoningMode") or ""),
        "nativeRuleSelectionApplied": typedb_bool(metadata.get("nativeRuleSelectionApplied")),
        "nativeRuleSelectionCandidateCount": int(
            number_or_none(metadata.get("nativeRuleSelectionCandidateCount")) or 0
        ),
        "nativeRuleSelectionExecutedCount": int(
            number_or_none(metadata.get("nativeRuleSelectionExecutedCount")) or 0
        ),
        "nativeRuleSelectionDeferredCount": int(
            number_or_none(metadata.get("nativeRuleSelectionDeferredCount")) or 0
        ),
        "nativeRuleSelectionFullRuleCount": int(
            number_or_none(metadata.get("nativeRuleSelectionFullRuleCount")) or 0
        ),
        "nativeRuleSelectionExecutedRuleIds": list(
            metadata.get("nativeRuleSelectionExecutedRuleIds") or []
        )[:80],
        "nativeRuleSelectionDeferredRuleIds": list(
            metadata.get("nativeRuleSelectionDeferredRuleIds") or []
        )[:80],
        "typedbNativeRuleExecutedCount": int(
            number_or_none(metadata.get("typedbNativeRuleExecutedCount")) or 0
        ),
        "typedbNativeRuleMatchedCount": int(
            number_or_none(metadata.get("typedbNativeRuleMatchedCount")) or 0
        ),
        "typedbNativeRuleMatchedRuleIds": list(
            metadata.get("typedbNativeRuleMatchedRuleIds") or []
        )[:160],
        "typedbNativeRuleTimingProfile": (
            dict(metadata.get("typedbNativeRuleTimingProfile") or {})
            if isinstance(metadata.get("typedbNativeRuleTimingProfile"), dict)
            else {}
        ),
        "typedbNativeStageTimings": (
            dict(metadata.get("typedbNativeStageTimings") or {})
            if isinstance(metadata.get("typedbNativeStageTimings"), dict)
            else {}
        ),
        "matchedGraphSource": str(metadata.get("matchedGraphSource") or ""),
        "matchedGraphReuseStatus": str(metadata.get("matchedGraphReuseStatus") or ""),
        "matchedGraphReuseReason": str(metadata.get("matchedGraphReuseReason") or "")[:220],
        "querySource": "typedb-active-inference-generation-marker",
    }


def inferencebox_commit_proof(
    _store: GraphReadsMetadataStore,
    inference_generation_id: str,
    source_abox_snapshot_id: str,
    target_symbols: List[str] = None,
    world_id: str = "",
) -> Dict[str, object]:
    """Verify publication using only active markers and pointers.

    ``run_rulebox`` already has the materialized InferenceBox graph in
    memory.  Re-reading every entity, relation, and trace just to make an
    alert safe made the delivery path pay for a second expensive TypeDB
    traversal.  This proof deliberately reads only the active Inference
    generation marker and the active ABox pointer/candidate marker.  It
    proves generation identity, source ABox identity, native completion,
    and target coverage; detailed rows remain available to the durable
    background audit worker.
    """
    expected_generation_id = str(inference_generation_id or "").strip()
    expected_source_abox_id = str(source_abox_snapshot_id or "").strip()
    expected_symbols = clean_symbols_from_payload(target_symbols or [])
    clean_world_id = str(world_id or "").strip()
    metadata = _store.inferencebox_recovery_metadata(clean_world_id)
    active_abox = _store.active_abox_metadata(clean_world_id)

    actual_generation_id = str(metadata.get("inferenceGenerationId") or "").strip()
    actual_source_abox_id = str(metadata.get("sourceAboxSnapshotId") or "").strip()
    actual_symbols = clean_symbols_from_payload(metadata.get("targetSymbols") or [])
    active_abox_id = str(active_abox.get("aboxSnapshotId") or "").strip()
    native_completed = bool(metadata.get("nativeTypeDbReasoningCompleted"))
    outcome = str(metadata.get("nativeInferenceOutcome") or "").strip().lower()
    issues = []
    if str(metadata.get("status") or "") != "ok":
        issues.append("active-inference-generation-marker-unavailable")
    if not expected_generation_id:
        issues.append("expected-inference-generation-missing")
    elif actual_generation_id != expected_generation_id:
        issues.append("inference-generation-mismatch")
    if not expected_source_abox_id:
        issues.append("expected-source-abox-missing")
    elif actual_source_abox_id != expected_source_abox_id:
        issues.append("inference-source-abox-mismatch")
    if str(active_abox.get("status") or "") != "ok":
        issues.append("active-abox-pointer-unavailable")
    elif active_abox_id != expected_source_abox_id:
        issues.append("active-abox-pointer-mismatch")
    if not native_completed:
        issues.append("native-evaluation-not-complete")
    if outcome not in {"matched", "no-match"}:
        issues.append("native-inference-outcome-unknown")
    missing_symbols = sorted(set(expected_symbols).difference(actual_symbols))
    if missing_symbols:
        issues.append("target-symbol-coverage-missing")

    base = {
        "configured": True,
        "graphStore": "typedb",
        "worldId": str(metadata.get("worldId") or clean_world_id),
        "inferenceGenerationId": actual_generation_id,
        "expectedInferenceGenerationId": expected_generation_id,
        "sourceAboxSnapshotId": actual_source_abox_id,
        "expectedSourceAboxSnapshotId": expected_source_abox_id,
        "activeAboxSnapshotId": active_abox_id,
        "targetSymbols": actual_symbols,
        "expectedTargetSymbols": expected_symbols,
        "missingTargetSymbols": missing_symbols,
        "nativeTypeDbReasoningCompleted": native_completed,
        "typedbNativeRuleEvaluationCompleted": native_completed,
        "nativeInferenceOutcome": outcome,
        "generationAligned": not issues,
        "durableCommitProof": not issues,
        "durableReadback": False,
        "querySource": "typedb-active-inference-commit-proof",
        "typedbReadStatus": "commit-proof" if not issues else "commit-proof-failed",
    }
    if issues:
        return {
            **base,
            "status": "error",
            "verified": False,
            "issues": issues,
            "reason": "TypeDB active generation commit proof failed: " + ", ".join(issues),
        }
    matched = outcome == "matched"
    return {
        **base,
        "status": "ok" if matched else "empty",
        "verified": True,
        "issues": [],
        "nativeTypeDbReasoningUsed": matched,
        "typedbNativeRuleReasoningUsed": matched,
        "nativeInferenceNoMatch": not matched,
        "targetCoverageStatus": "complete" if expected_symbols else "not-requested",
        "reason": "",
    }


def list_ontology_worlds(_store: GraphReadsMetadataStore) -> List[Dict[str, object]]:
    """List independent active worlds without falling back to a global pointer.

    Legacy control records intentionally have no ``ontology-world-id``.
    They remain visible as a migration diagnostic but are never selected for
    a newly projected account world.
    """
    try:
        pointers = _store.active_worldview_manifest_pointer_rows()
    except Exception:
        return []
    world_ids = sorted(
        {
            str(row.get("worldId") or "").strip()
            for row in pointers
            if str(row.get("worldId") or "").strip()
        }
    )
    worlds: List[Dict[str, object]] = []
    for world_id in world_ids:
        metadata = _store.active_abox_metadata(world_id)
        worlds.append(
            {
                "worldId": world_id,
                "worldType": str(metadata.get("worldType") or ""),
                "tenantId": str(metadata.get("tenantId") or ""),
                "accountId": str(metadata.get("accountId") or ""),
                "marketId": str(metadata.get("marketId") or ""),
                "status": str(metadata.get("status") or ""),
                "worldviewManifestId": str(
                    metadata.get("worldviewManifestId") or metadata.get("aboxSnapshotId") or ""
                ),
                "activeScopeCount": int(number_or_none(metadata.get("activeScopeCount")) or 0),
            }
        )
    return worlds


def abox_pending_activation_rows(
    _store: GraphReadsMetadataStore, world_id: str = ""
) -> List[Dict[str, object]]:
    """Return durable ABox activation hand-offs awaiting native inference.

    The active pointer is intentionally switched only after a candidate
    ABox verifies. Native TypeDB inference follows in a separate operation,
    so the hand-off must survive a worker or server restart. A control row
    makes that otherwise transient state observable and recoverable.
    """
    clean_world_id = str(world_id or "").strip()
    # The pending-control entity has a deterministic ID per world.  The
    # old kind/world scan touched every ontology-node on each live
    # projection merely to prove that no interrupted activation existed.
    # A keyed lookup preserves the same journal semantics without making
    # an empty recovery check compete with market inference.
    if clean_world_id:
        world_suffix = ":world:" + hashlib.sha256(clean_world_id.encode("utf-8")).hexdigest()[:16]
        control_id = "abox-activation-pending" + world_suffix
        id_clause = "has ontology-id " + typedb_string(control_id) + ", "
    else:
        control_id = ""
        id_clause = "has ontology-id $id, "
    query = (
        "match $n isa ontology-node, " + id_clause + "has ontology-label $label, "
        'has ontology-kind "abox-activation-pending", '
        'has ontology-box "ABoxControl", '
        + (
            "has ontology-world-id " + typedb_string(clean_world_id) + ", "
            if clean_world_id
            else ""
        )
        + "has ontology-snapshot-id $snapshotId, "
        "has ontology-updated-at $updatedAt, "
        "has ontology-json $json;"
    )
    columns = (
        ["label", "kind", "snapshotId", "updatedAt", "json"]
        if control_id
        else [
            "id",
            "label",
            "kind",
            "snapshotId",
            "updatedAt",
            "json",
        ]
    )
    rows = _store.read_rows(
        query,
        columns,
        label="typedb.abox-activation-pending",
    )
    # A literal ``ontology-id`` is a keyed TypeQL lookup, so TypeDB does
    # not bind an ``$id`` variable for the generic row mapper. Restore the
    # known deterministic control id before mapping; otherwise the mapper
    # correctly filters the row as unidentified and a staged candidate
    # appears to have vanished.
    if control_id:
        rows = [{**dict(row or {}), "id": control_id} for row in rows or []]
    return _store.entity_rows_from_typeql(rows, "ABoxControl")


def pending_abox_activation(
    _store: GraphReadsMetadataStore, world_id: str = ""
) -> Dict[str, object]:
    rows = sorted(
        _store.abox_pending_activation_rows(world_id),
        key=lambda row: (str(row.get("updatedAt") or ""), str(row.get("id") or "")),
        reverse=True,
    )
    if not rows:
        return {
            "configured": True,
            "status": "empty",
            "graphStore": "typedb",
        }
    row = rows[0]
    candidate_snapshot_id = str(
        row.get("candidateAboxSnapshotId")
        or row.get("aboxSnapshotId")
        or row.get("snapshotId")
        or ""
    ).strip()
    return {
        "configured": True,
        "status": "pending" if candidate_snapshot_id else "invalid",
        "graphStore": "typedb",
        "candidateAboxSnapshotId": candidate_snapshot_id,
        "previousAboxSnapshotId": str(row.get("previousAboxSnapshotId") or "").strip(),
        "materialFingerprint": str(row.get("materialFingerprint") or "").strip(),
        "projectionRunId": str(row.get("projectionRunId") or "").strip(),
        "asOf": str(row.get("asOf") or ""),
        "targetSymbols": clean_symbols_from_payload(
            row.get("targetSymbols") or row.get("inferenceTargetSymbols") or []
        ),
        "activationStatus": str(row.get("activationStatus") or "pending-native-inference"),
        "candidateWorldviewManifestId": str(row.get("candidateWorldviewManifestId") or "").strip(),
        "worldId": str(row.get("worldId") or world_id or ""),
        "worldType": str(row.get("worldType") or ""),
        "tenantId": str(row.get("tenantId") or ""),
        "accountId": str(row.get("accountId") or ""),
        "controlId": str(row.get("id") or ""),
        "updatedAt": str(row.get("updatedAt") or ""),
    }


def read_inference_generation_records(
    _store: GraphReadsMetadataStore,
    published_only: bool = True,
    world_id: str = "",
    *,
    _bindings: GraphReadsMetadataRuntime
) -> List[Dict[str, object]]:
    world_clause = (
        "has ontology-world-id " + typedb_string(world_id) + ", "
        if str(world_id or "").strip()
        else ""
    )
    published_rows = _store.read_rows(
        (
            'match $n isa ontology-node, has ontology-box "InferenceBox", '
            'has ontology-kind "inference-generation", '
            + world_clause
            + "has ontology-snapshot-id $snapshotId, "
            + "has ontology-updated-at $updatedAt, "
            + "has ontology-json $json;"
        ),
        ["snapshotId", "updatedAt", "json"],
    )
    candidate_rows = _store.read_rows(
        (
            'match $n isa ontology-node, has ontology-box "InferenceBox", '
            'has ontology-kind "inference-generation-candidate", '
            + world_clause
            + "has ontology-snapshot-id $snapshotId, "
            + "has ontology-updated-at $updatedAt, "
            + "has ontology-json $json;"
        ),
        ["snapshotId", "updatedAt", "json"],
    )
    node_rows = _store.read_rows(
        (
            'match $n isa ontology-node, has ontology-box "InferenceBox", '
            + world_clause
            + "has ontology-snapshot-id $snapshotId, "
            + "has ontology-updated-at $updatedAt, "
            + "has ontology-json $json;"
        ),
        ["snapshotId", "updatedAt", "json"],
    )
    relation_rows = _store.read_rows(
        (
            'match $r isa ontology-assertion, has ontology-box "InferenceBox", '
            + world_clause
            + "has ontology-snapshot-id $snapshotId, "
            + "has ontology-updated-at $updatedAt, "
            + "has ontology-json $json;"
        ),
        ["snapshotId", "updatedAt", "json"],
    )
    indexed_rows = [
        {
            "snapshotId": row.get("snapshotId"),
            "updatedAt": row.get("updatedAt"),
            "propertiesJson": row.get("json"),
        }
        for row in node_rows
    ] + [
        {
            "snapshotId": row.get("snapshotId"),
            "updatedAt": row.get("updatedAt"),
            "propertiesJson": row.get("json"),
            "relationType": "ontology-assertion",
        }
        for row in relation_rows
    ]
    records = _bindings.inference_generation_records(indexed_rows, [])
    candidate_ids = {
        str(row.get("snapshotId") or "")
        for row in candidate_rows
        if str(row.get("snapshotId") or "").strip()
    }
    if not published_rows:
        if published_only:
            return []
        return [
            {
                **record,
                "publicationStatus": (
                    "candidate"
                    if str(record.get("generationId") or "") in candidate_ids
                    else "staging"
                ),
            }
            for record in records
        ]
    published = {
        str(row.get("snapshotId") or ""): str(row.get("updatedAt") or "")
        for row in published_rows
        if str(row.get("snapshotId") or "").strip()
        and _bindings.inference_marker_is_active(row.get("json"))
    }
    if not published_only:
        return [
            {
                **record,
                "latestAt": published.get(
                    str(record.get("generationId") or ""), record.get("latestAt")
                ),
                "publicationStatus": (
                    "active"
                    if str(record.get("generationId") or "") in published
                    else (
                        "candidate"
                        if str(record.get("generationId") or "") in candidate_ids
                        else "staging"
                    )
                ),
            }
            for record in records
        ]
    result = []
    for record in records:
        generation_id = str(record.get("generationId") or "")
        if generation_id not in published:
            continue
        result.append(
            {
                **record,
                "latestAt": published[generation_id] or record.get("latestAt"),
                "publicationStatus": "active",
            }
        )
    return sorted(result, key=lambda item: str(item.get("latestAt") or ""), reverse=True)
