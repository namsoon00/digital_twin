"""manifest: read_index through explicit injected capabilities."""

from digital_twin.modules.reasoning.domain.ontology_change_impact import scope_symbol
from digital_twin.modules.reasoning.domain.ontology_native_rule_planning import normalize_native_rule_planner_topology
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    native_rule_evidence_read_index_from_components,
    normalize_native_rule_evidence_read_index,
)
from digital_twin.modules.reasoning.infrastructure.typeql.constants import (
    NATIVE_RULE_EVIDENCE_READ_INDEX_BATCH_SIZE,
    NATIVE_RULE_INDEXED_QUERY_MAX_STORAGE_IDS,
)
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_value_match
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
)
from digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses import (
    typedb_world_id_constraint,
)
from typing import Dict, Iterable, List, Tuple
import copy
import math
import time
from .read_index_ports import ManifestReadIndexStore, ManifestReadIndexRuntime


def rebuild_active_manifest_native_rule_evidence_read_index(
    _store: ManifestReadIndexStore, active_metadata: Dict[str, object], world_id: str = ""
) -> Dict[str, object]:
    """Reconstruct one active Manifest's exact physical evidence index.

    This is a rolling-recovery path, not an inference path. It reads only
    the source identities declared by the verified planner topology and
    active relations touching those sources. No RuleBox condition or
    investment threshold is evaluated here.
    """
    started_at = time.perf_counter()
    active = dict(active_metadata or {})
    clean_world_id = str(world_id or active.get("worldId") or "").strip()
    manifest_id = str(
        active.get("worldviewManifestId") or active.get("aboxSnapshotId") or ""
    ).strip()
    topology = normalize_native_rule_planner_topology(active.get("nativeRulePlannerTopology"))
    if (
        str(active.get("status") or "") != "ok"
        or not manifest_id
        or str(active.get("scopedAboxManifestVersion") or "") != SCOPED_ABOX_MANIFEST_VERSION
        or str(topology.get("status") or "") != "ok"
    ):
        return {
            "status": "invalid-active-manifest",
            "reason": str(
                topology.get("reason")
                or "A complete scoped Manifest and verified planner topology are required."
            )[:220],
            "manifestId": manifest_id,
            "readQueryCount": 0,
            "durationMs": int((time.perf_counter() - started_at) * 1000),
        }

    source_ids_by_symbol = {
        str(symbol or "")
        .upper()
        .strip(): sorted(
            {
                str(source_id or "").strip()
                for source_id in source_ids or []
                if str(source_id or "").strip()
            }
        )
        for symbol, source_ids in dict(topology.get("sourceIdsBySymbol") or {}).items()
        if str(symbol or "").strip()
    }
    expected_source_ids = sorted(
        {source_id for source_ids in source_ids_by_symbol.values() for source_id in source_ids}
    )
    if not expected_source_ids:
        return {
            "status": "invalid-active-manifest",
            "reason": "The active planner topology contains no native RuleBox source identities.",
            "manifestId": manifest_id,
            "readQueryCount": 0,
            "durationMs": int((time.perf_counter() - started_at) * 1000),
        }
    active_generation_ids = {
        str(generation_id or "").strip()
        for generation_id in dict(active.get("scopeGenerationIds") or {}).values()
        if str(generation_id or "").strip()
    }
    if not active_generation_ids:
        return {
            "status": "invalid-active-manifest",
            "reason": "The active Manifest contains no immutable scope generations.",
            "manifestId": manifest_id,
            "readQueryCount": 0,
            "durationMs": int((time.perf_counter() - started_at) * 1000),
        }

    recovery_timeout = min(
        _store.write_operation_timeout_seconds(),
        _store.native_rule_execution_budget_seconds(),
    )
    source_storage_ids: Dict[str, set] = {source_id: set() for source_id in expected_source_ids}
    relation_storage_ids_by_symbol: Dict[str, set] = {
        symbol: set() for symbol in source_ids_by_symbol
    }
    relation_storage_ids_by_symbol_and_type: Dict[str, Dict[str, set]] = {
        symbol: {} for symbol in source_ids_by_symbol
    }
    symbols_by_source_id: Dict[str, set] = {}
    for symbol, source_ids in source_ids_by_symbol.items():
        for source_id in source_ids:
            symbols_by_source_id.setdefault(source_id, set()).add(symbol)

    scope_plan = list(active.get("scopePlan") or [])
    generation_ids_by_symbol: Dict[str, set] = {}
    for symbol, source_ids in source_ids_by_symbol.items():
        portfolio_source = symbol.startswith("PORTFOLIO:") or any(
            str(source_id or "").startswith("portfolio:") for source_id in source_ids
        )
        relevant = {
            str(item.get("generationId") or "").strip()
            for item in scope_plan
            if isinstance(item, dict)
            and str(item.get("generationId") or "").strip()
            and (portfolio_source or scope_symbol(item.get("scopeId")) == symbol)
        }
        # A legacy scoped Manifest can omit symbol ownership on a link
        # scope. Falling back to all active generations is slower but
        # retains exactness during the one-time metadata repair.
        generation_ids_by_symbol[symbol] = relevant or set(active_generation_ids)

    read_query_count = 0
    for symbol in sorted(source_ids_by_symbol):
        source_batch = list(source_ids_by_symbol.get(symbol) or [])
        generation_ids = sorted(generation_ids_by_symbol.get(symbol) or set())
        for offset in range(0, len(generation_ids), NATIVE_RULE_EVIDENCE_READ_INDEX_BATCH_SIZE):
            generation_batch = generation_ids[
                offset : offset + NATIVE_RULE_EVIDENCE_READ_INDEX_BATCH_SIZE
            ]
            source_query = (
                "match "
                "$subject isa ontology-node, has ontology-id $sourceId, "
                "has ontology-storage-id $sourceStorageId, "
                'has ontology-box "ABox"'
                + typedb_world_id_constraint(clean_world_id)
                + ", has ontology-snapshot-id $sourceSnapshotId; "
                + typedb_value_match(
                    "$subject",
                    "ontology-id",
                    source_batch,
                    "==",
                    "evidenceIndexSourceIdFilter",
                )
                + typedb_value_match(
                    "$subject",
                    "ontology-snapshot-id",
                    generation_batch,
                    "==",
                    "evidenceIndexSourceGenerationFilter",
                )
            )
            source_rows = _store.read_rows(
                source_query,
                ["sourceId", "sourceStorageId", "sourceSnapshotId"],
                label="typedb.manifest-evidence-index-repair.sources",
                timeout_seconds=recovery_timeout,
            )
            read_query_count += 1
            for row in source_rows:
                source_id = str(row.get("sourceId") or "").strip()
                storage_id = str(row.get("sourceStorageId") or "").strip()
                snapshot_id = str(row.get("sourceSnapshotId") or "").strip()
                if (
                    source_id in source_storage_ids
                    and storage_id
                    and snapshot_id in active_generation_ids
                ):
                    source_storage_ids[source_id].add(storage_id)

            for role, links_clause in [
                ("source", "links (source: $subject, target: $other)"),
                ("target", "links (source: $other, target: $subject)"),
            ]:
                relation_query = (
                    "match "
                    "$subject isa ontology-node, has ontology-id $sourceId; "
                    "$other isa ontology-node; "
                    "$r isa ontology-assertion, " + links_clause + ", "
                    "has ontology-storage-id $relationStorageId, "
                    "has ontology-relation-type $relationType, "
                    'has ontology-box "ABox"'
                    + typedb_world_id_constraint(clean_world_id)
                    + ", has ontology-snapshot-id $relationSnapshotId; "
                    + typedb_value_match(
                        "$subject",
                        "ontology-id",
                        source_batch,
                        "==",
                        "evidenceIndexRelationSourceFilter" + role.title(),
                    )
                    + typedb_value_match(
                        "$r",
                        "ontology-snapshot-id",
                        generation_batch,
                        "==",
                        "evidenceIndexRelationGenerationFilter" + role.title(),
                    )
                )
                relation_rows = _store.read_rows(
                    relation_query,
                    ["sourceId", "relationStorageId", "relationType", "relationSnapshotId"],
                    label="typedb.manifest-evidence-index-repair.relations:" + role,
                    timeout_seconds=recovery_timeout,
                )
                read_query_count += 1
                for row in relation_rows:
                    source_id = str(row.get("sourceId") or "").strip()
                    storage_id = str(row.get("relationStorageId") or "").strip()
                    relation_type = str(row.get("relationType") or "").upper().strip()
                    snapshot_id = str(row.get("relationSnapshotId") or "").strip()
                    if not storage_id or snapshot_id not in active_generation_ids:
                        continue
                    for source_symbol in symbols_by_source_id.get(source_id, set()):
                        relation_storage_ids_by_symbol.setdefault(source_symbol, set()).add(
                            storage_id
                        )
                        if relation_type:
                            relation_storage_ids_by_symbol_and_type.setdefault(
                                source_symbol,
                                {},
                            ).setdefault(relation_type, set()).add(storage_id)

            # Projection persistence also indexes symbol-tagged evidence
            # whose stable endpoints are not the stock node itself (for
            # example a security anchor linked to an evidence document).
            # Retain that exact coverage in addition to endpoint reads.
            symbol_relation_query = (
                "match $r isa ontology-assertion, "
                "has ontology-storage-id $relationStorageId, "
                "has ontology-relation-type $relationType, "
                'has ontology-box "ABox"'
                + typedb_world_id_constraint(clean_world_id)
                + ", has ontology-symbol $relationSymbol, "
                "has ontology-snapshot-id $relationSnapshotId; "
                + typedb_value_match(
                    "$r",
                    "ontology-symbol",
                    symbol,
                    "==",
                    "evidenceIndexRelationSymbolFilter",
                )
                + typedb_value_match(
                    "$r",
                    "ontology-snapshot-id",
                    generation_batch,
                    "==",
                    "evidenceIndexSymbolRelationGenerationFilter",
                )
            )
            symbol_relation_rows = _store.read_rows(
                symbol_relation_query,
                ["relationStorageId", "relationType", "relationSnapshotId"],
                label="typedb.manifest-evidence-index-repair.relations:symbol",
                timeout_seconds=recovery_timeout,
            )
            read_query_count += 1
            for row in symbol_relation_rows:
                storage_id = str(row.get("relationStorageId") or "").strip()
                relation_type = str(row.get("relationType") or "").upper().strip()
                snapshot_id = str(row.get("relationSnapshotId") or "").strip()
                if not storage_id or snapshot_id not in active_generation_ids:
                    continue
                relation_storage_ids_by_symbol.setdefault(symbol, set()).add(storage_id)
                if relation_type:
                    relation_storage_ids_by_symbol_and_type.setdefault(symbol, {}).setdefault(
                        relation_type,
                        set(),
                    ).add(storage_id)

    ambiguous_sources = {
        source_id: sorted(storage_ids)
        for source_id, storage_ids in source_storage_ids.items()
        if len(storage_ids) != 1
    }
    if ambiguous_sources:
        return {
            "status": "source-storage-coverage-mismatch",
            "reason": (
                "The active Manifest did not resolve every RuleBox source to exactly one physical row."
            ),
            "manifestId": manifest_id,
            "sourceIds": sorted(ambiguous_sources)[:20],
            "readQueryCount": read_query_count,
            "durationMs": int((time.perf_counter() - started_at) * 1000),
        }

    index = native_rule_evidence_read_index_from_components(
        source_ids_by_symbol,
        {
            source_id: next(iter(storage_ids))
            for source_id, storage_ids in source_storage_ids.items()
        },
        relation_storage_ids_by_symbol,
        relation_storage_ids_by_symbol_and_type,
    )
    verified = normalize_native_rule_evidence_read_index(index, topology)
    if str(verified.get("status") or "") != "ok":
        return {
            "status": "rebuilt-index-invalid",
            "reason": str(verified.get("reason") or "Rebuilt evidence index is invalid.")[:220],
            "manifestId": manifest_id,
            "readQueryCount": read_query_count,
            "durationMs": int((time.perf_counter() - started_at) * 1000),
        }
    return {
        "status": "ok",
        "manifestId": manifest_id,
        "index": index,
        "fingerprint": str(verified.get("fingerprint") or ""),
        "sourceCount": len(expected_source_ids),
        "relationCount": len(
            {
                storage_id
                for storage_ids in relation_storage_ids_by_symbol.values()
                for storage_id in storage_ids
            }
        ),
        "readQueryCount": read_query_count,
        "durationMs": int((time.perf_counter() - started_at) * 1000),
    }


def hydrate_native_rule_evidence_field_index(
    _store: ManifestReadIndexStore,
    evidence_read_index: Dict[str, object] = None,
    target_symbols: Iterable[str] = None,
    relation_types: Iterable[str] = None,
    *,
    _bindings: ManifestReadIndexRuntime
) -> Dict[str, object]:
    """Add a bounded relation-field lookup to a verified Manifest index.

    The persisted index already proves which immutable assertion rows are
    active. This small TypeDB read only labels those rows by their stored
    ``ontology-field`` value, allowing a RuleBox condition such as
    ``field=positionToTradingValuePct`` to bind one physical assertion
    rather than every execution-metric relation for the stock. It never
    evaluates thresholds or chooses a rule outcome.
    """
    evidence = dict(evidence_read_index or {})
    if str(evidence.get("status") or "") != "verified":
        return {
            "status": "not-available",
            "evidence": evidence,
            "readQueryCount": 0,
            "readTransactionCount": 0,
        }
    index = copy.deepcopy(dict(evidence.get("index") or {}))
    if not index:
        return {
            "status": "not-available",
            "evidence": evidence,
            "readQueryCount": 0,
            "readTransactionCount": 0,
        }
    existing = index.get("relationStorageIdsBySymbolAndTypeAndField")
    if isinstance(existing, dict) and existing:
        evidence["index"] = index
        return {
            "status": "cached",
            "evidence": evidence,
            "readQueryCount": 0,
            "readTransactionCount": 0,
        }
    symbols = clean_symbols_from_payload(target_symbols or index.get("symbols") or [])
    requested_relation_types = {
        str(relation_type or "").upper().strip()
        for relation_type in relation_types or []
        if str(relation_type or "").strip()
    }
    relation_ids_by_symbol_and_type = dict(index.get("relationStorageIdsBySymbolAndType") or {})
    storage_membership: Dict[str, List[Tuple[str, str]]] = {}
    for symbol in symbols:
        for relation_type, storage_ids in dict(
            relation_ids_by_symbol_and_type.get(symbol) or {}
        ).items():
            clean_relation_type = str(relation_type or "").upper().strip()
            if not clean_relation_type or (
                requested_relation_types and clean_relation_type not in requested_relation_types
            ):
                continue
            for storage_id in storage_ids or []:
                clean_storage_id = str(storage_id or "").strip()
                if clean_storage_id:
                    storage_membership.setdefault(clean_storage_id, []).append(
                        (symbol, clean_relation_type)
                    )
    storage_ids = sorted(storage_membership)
    if not storage_ids:
        evidence["index"] = index
        return {
            "status": "empty",
            "evidence": evidence,
            "readQueryCount": 0,
            "readTransactionCount": 0,
        }
    rows: List[Dict[str, object]] = []
    read_query_count = 0
    for offset in range(0, len(storage_ids), NATIVE_RULE_INDEXED_QUERY_MAX_STORAGE_IDS):
        storage_id_chunk = storage_ids[offset : offset + NATIVE_RULE_INDEXED_QUERY_MAX_STORAGE_IDS]
        query = (
            "match $relation isa ontology-assertion, has ontology-storage-id $storageId, "
            "has ontology-field $field; "
            + typedb_value_match(
                "$relation",
                "ontology-storage-id",
                storage_id_chunk,
                "==",
                "nativeEvidenceRelationStorage",
            )
        )
        try:
            rows.extend(
                _store.read_rows(
                    query,
                    ["storageId", "field"],
                    label="typedb.native-rule-evidence-field-index",
                    timeout_seconds=min(5.0, _store.native_rule_query_timeout_seconds()),
                )
            )
            read_query_count += 1
        except (
            Exception
        ) as error:  # noqa: BLE001 - relation-type index remains safe but less selective.
            evidence["index"] = index
            return {
                "status": "error",
                "evidence": evidence,
                "readQueryCount": read_query_count + 1,
                "readTransactionCount": read_query_count + 1,
                "storageIdentityCount": len(storage_ids),
                "chunkCount": int(
                    math.ceil(len(storage_ids) / NATIVE_RULE_INDEXED_QUERY_MAX_STORAGE_IDS)
                ),
                "relationTypes": sorted(requested_relation_types),
                "reasonCode": _bindings.typedb_error_code(error),
                "reason": str(error)[:180],
            }
    field_index: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
    for row in rows:
        storage_id = str(row.get("storageId") or "").strip()
        field = str(row.get("field") or "").strip()
        if not storage_id or not field:
            continue
        for symbol, relation_type in storage_membership.get(storage_id, []):
            field_index.setdefault(symbol, {}).setdefault(relation_type, {}).setdefault(
                field, []
            ).append(storage_id)
    index["relationStorageIdsBySymbolAndTypeAndField"] = {
        symbol: {
            relation_type: {
                field: sorted(set(storage_ids)) for field, storage_ids in fields.items()
            }
            for relation_type, fields in relation_types.items()
        }
        for symbol, relation_types in field_index.items()
    }
    evidence["index"] = index
    return {
        "status": "verified",
        "evidence": evidence,
        "readQueryCount": read_query_count,
        "readTransactionCount": read_query_count,
        "storageIdentityCount": len(storage_ids),
        "chunkCount": read_query_count,
        "fieldRowCount": len(rows),
        "relationTypes": sorted(requested_relation_types),
    }
