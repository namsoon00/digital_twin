"""graph_reads: inventory through explicit injected capabilities."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from digital_twin.domain.ontology_change_impact import scope_family, scope_symbol
from digital_twin.domain.ontology_scopes import (
    SCOPED_ABOX_MANIFEST_VERSION,
    SCOPED_ABOX_PERSISTENCE_MODE,
)
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.abox_persistence.world_calls import (
    typedb_call_for_world,
)
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import json_object
from digital_twin.modules.reasoning.infrastructure.typeql.constants import (
    NATIVE_RULE_EVIDENCE_READ_INDEX_BATCH_SIZE,
)
from digital_twin.modules.reasoning.infrastructure.typeql.literals import (
    typedb_string,
    typedb_value_match,
)
from digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses import (
    typedb_active_abox_member_clause,
    typedb_active_worldview_manifest_clause,
    typedb_scoped_manifest_member_clause,
)
from typing import Dict, Iterable, List, Tuple
from .inventory_ports import GraphReadsInventoryStore, GraphReadsInventoryRuntime


def active_abox_uses_scoped_manifest(_store: GraphReadsInventoryStore, world_id: str = "") -> bool:
    """Whether live ABox reads must resolve through scope pointers.

    This deliberately reads the durable manifest marker instead of
    inferring the mode from a transient worker setting.  A failed
    activation therefore continues to read the previous complete world.
    """
    try:
        metadata = _store.active_abox_metadata(world_id)
    except Exception:  # noqa: BLE001 - retain legacy reads during recovery.
        return False
    return (
        str(metadata.get("status") or "") == "ok"
        and str(metadata.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION
    )


def active_abox_members_clause(
    _store: GraphReadsInventoryStore, members: Iterable[Tuple[str, str]], world_id: str = ""
) -> str:
    """Build one active-world constraint for a TypeQL query.

    Runtime reads must not repeat the scoped-or-legacy activation branch
    for every endpoint. Once a scoped Manifest is active, one control
    lookup plus active scope-pointer membership predicates is sufficient.
    The legacy branch remains available only until the first scoped
    migration has completed.
    """
    normalized = [
        (str(variable or "$item"), str(prefix or "item")) for variable, prefix in members or []
    ]
    if not normalized:
        return ""
    if _store.active_abox_uses_scoped_manifest(world_id):
        manifest_id = "$activeManifestId"
        return " ".join(
            [
                typedb_active_worldview_manifest_clause(
                    "$activeManifestPointer", manifest_id, world_id
                ),
                *[
                    typedb_scoped_manifest_member_clause(variable, prefix, manifest_id, world_id)
                    for variable, prefix in normalized
                ],
            ]
        )
    return " ".join(
        typedb_active_abox_member_clause(variable, prefix, world_id)
        for variable, prefix in normalized
    )


def scoped_abox_manifest_inventory(
    _store: GraphReadsInventoryStore, world_id: str = ""
) -> Dict[str, object]:
    """Read the retired Manifest count without scanning physical ABox rows.

    The maintenance scheduler needs to select the most backlogged world
    before it tries to obtain TypeDB's global writer lease.  The full
    storage diagnostic also counts every physical ABox row, which is
    useful for an operator screen but too expensive for that recurring
    selection step.
    """
    clean_world_id = str(world_id or "").strip()
    try:
        active_pointers = sorted(
            _store.active_worldview_manifest_pointer_identity_rows(
                clean_world_id,
                limit=1,
            ),
            key=lambda row: (str(row.get("updatedAt") or ""), str(row.get("id") or "")),
            reverse=True,
        )
    except Exception as error:  # noqa: BLE001 - maintenance can fall back to round robin.
        return {
            "configured": bool(getattr(_store, "address", "")),
            "status": "error",
            "graphStore": "typedb",
            "reason": str(error)[:180],
        }
    if not active_pointers:
        return {
            "configured": bool(getattr(_store, "address", "")),
            "status": "legacy",
            "graphStore": "typedb",
            "persistenceMode": "immutable-complete-generation",
            "reason": "Active ABox has not yet been migrated to a scoped Worldview Manifest.",
        }
    active = active_pointers[0]
    manifest_id = str(
        active.get("worldviewManifestId")
        or active.get("aboxSnapshotId")
        or active.get("snapshotId")
        or ""
    ).strip()
    try:
        active_marker = _store.worldview_manifest_marker_identity_rows(
            clean_world_id,
            manifest_id=manifest_id,
            limit=1,
        )
        stored_manifest_count = _store.worldview_manifest_marker_count(clean_world_id)
        if not active_marker or stored_manifest_count <= 0:
            return {
                "configured": bool(getattr(_store, "address", "")),
                "status": "error",
                "graphStore": "typedb",
                "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
                "worldviewManifestId": manifest_id,
                "reason": "Active Worldview Manifest marker is unavailable.",
            }
        return {
            "configured": bool(getattr(_store, "address", "")),
            "status": "ok",
            "graphStore": "typedb",
            "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
            "worldviewManifestId": manifest_id,
            "storedManifestCount": stored_manifest_count,
            "inactiveManifestCount": max(0, stored_manifest_count - 1),
        }
    except Exception as error:  # noqa: BLE001 - a later retention turn can retry inventory.
        return {
            "configured": bool(getattr(_store, "address", "")),
            "status": "error",
            "graphStore": "typedb",
            "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
            "reason": str(error)[:180],
        }


def scoped_abox_integrity_audit(
    _store: GraphReadsInventoryStore,
    world_id: str = "",
    cursor: int = 0,
    limit: int = 20,
    scope_ids: Iterable[str] = None,
) -> Dict[str, object]:
    """Verify a bounded slice of the active Manifest without rewriting it.

    A recurring whole-world projection used to hide physical drift by
    rebuilding every scope. The scoped Manifest already records the exact
    generation and expected row counts, so two grouped TypeQL reductions
    can verify a rotating slice instead. Any mismatch is returned as an
    explicit repair target; this read-only audit never changes the active
    pointer or creates an investment inference.
    """

    try:
        active = dict(_store.active_abox_metadata(world_id) or {})
    except Exception as error:  # noqa: BLE001 - diagnostics must remain bounded and observable.
        return {
            "configured": bool(getattr(_store, "address", "")),
            "status": "error",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "reason": str(error)[:220],
        }
    scope_plan = [
        dict(item)
        for item in active.get("scopePlan") or []
        if isinstance(item, dict)
        and str(item.get("scopeId") or "").strip()
        and str(item.get("generationId") or "").strip()
    ]
    if (
        str(active.get("status") or "") != "ok"
        or str(active.get("scopedAboxManifestVersion") or "") != SCOPED_ABOX_MANIFEST_VERSION
        or not scope_plan
    ):
        return {
            "configured": bool(getattr(_store, "address", "")),
            "status": "unavailable",
            "graphStore": "typedb",
            "worldId": str(world_id or ""),
            "reason": "Active scoped ABox Manifest is unavailable.",
            "checkedScopeCount": 0,
            "activeScopeCount": len(scope_plan),
        }
    ordered = sorted(scope_plan, key=lambda item: str(item.get("scopeId") or ""))
    bounded_limit = max(1, min(200, int(limit or 20)))
    requested_scope_ids = list(
        dict.fromkeys(
            str(item or "").strip() for item in scope_ids or [] if str(item or "").strip()
        )
    )[:bounded_limit]
    targeted_verification = bool(requested_scope_ids)
    if targeted_verification:
        requested_scope_id_set = set(requested_scope_ids)
        selected = [
            item for item in ordered if str(item.get("scopeId") or "") in requested_scope_id_set
        ]
        start = max(0, int(cursor or 0)) % len(ordered)
    else:
        start = max(0, int(cursor or 0)) % len(ordered)
        selected = ordered[start : start + bounded_limit]
    counts = _store.scoped_abox_scope_row_counts_batch(
        selected,
        world_id=str(world_id or ""),
    )
    mismatches = []
    for item in selected:
        scope_id = str(item.get("scopeId") or "")
        actual = dict(counts.get(scope_id) or {})
        # OntologyEvidence is stored as an ``ontology-node`` beside
        # regular entities. The Manifest exposes the logical counts
        # separately, while this physical reduction returns their sum.
        expected_entities = int(number_or_none(item.get("entityCount")) or 0) + int(
            number_or_none(item.get("evidenceCount")) or 0
        )
        expected_relations = int(number_or_none(item.get("relationCount")) or 0)
        actual_entities = int(actual.get("entityCount") or 0)
        actual_relations = int(actual.get("relationCount") or 0)
        if expected_entities == actual_entities and expected_relations == actual_relations:
            continue
        mismatches.append(
            {
                "scopeId": scope_id,
                "generationId": str(item.get("generationId") or ""),
                "symbol": scope_symbol(scope_id),
                "scopeFamily": str(item.get("scopeFamily") or scope_family(scope_id)),
                "expectedEntityCount": expected_entities,
                "actualEntityCount": actual_entities,
                "expectedRelationCount": expected_relations,
                "actualRelationCount": actual_relations,
            }
        )
    reached_cycle_end = start + len(selected) >= len(ordered)
    next_cursor = (
        start if targeted_verification else 0 if reached_cycle_end else start + len(selected)
    )
    checked_scope_ids = (
        requested_scope_ids
        if targeted_verification
        else [str(item.get("scopeId") or "") for item in selected]
    )
    return {
        "configured": True,
        "status": "repair-required" if mismatches else "ok",
        "graphStore": "typedb",
        "worldId": str(world_id or active.get("worldId") or ""),
        "worldType": str(active.get("worldType") or ""),
        "accountId": str(active.get("accountId") or ""),
        "worldviewManifestId": str(
            active.get("worldviewManifestId") or active.get("aboxSnapshotId") or ""
        ),
        "activeScopeCount": len(ordered),
        "checkedScopeCount": len(selected),
        "checkedScopeIds": checked_scope_ids,
        "mismatchCount": len(mismatches),
        "mismatches": mismatches[:50],
        "cursor": start,
        "nextCursor": next_cursor,
        "cycleCompleted": targeted_verification or reached_cycle_end,
        "targetedVerification": targeted_verification,
        "readOnly": True,
        "automaticFullProjectionUsed": False,
    }


def scoped_abox_storage_diagnostics(
    _store: GraphReadsInventoryStore, world_id: str = ""
) -> Dict[str, object]:
    """Describe active logical scopes separately from physical ABox rows.

    Operators previously saw one large ABox count and could not tell
    whether it was the current investment world or retained immutable
    history.  This compact diagnostic avoids exporting graph payloads and
    makes that distinction explicit.
    """
    try:
        active = _store.active_abox_metadata(world_id)
    except Exception as error:  # noqa: BLE001 - status endpoints must stay available.
        return {
            "configured": bool(getattr(_store, "address", "")),
            "status": "error",
            "graphStore": "typedb",
            "reason": str(error)[:180],
        }
    scoped = str(active.get("scopedAboxManifestVersion") or "") == SCOPED_ABOX_MANIFEST_VERSION
    if not scoped:
        return {
            "configured": bool(getattr(_store, "address", "")),
            "status": str(active.get("status") or "legacy"),
            "graphStore": "typedb",
            "persistenceMode": "immutable-complete-generation",
            "activeAboxSnapshotId": str(active.get("aboxSnapshotId") or ""),
            "reason": "Active ABox has not yet been migrated to a scoped Worldview Manifest.",
        }
    scope_plan = list(active.get("scopePlan") or [])
    logical_entities = sum(
        int(number_or_none(item.get("entityCount")) or 0)
        for item in scope_plan
        if isinstance(item, dict)
    )
    logical_relations = sum(
        int(number_or_none(item.get("relationCount")) or 0)
        for item in scope_plan
        if isinstance(item, dict)
    )
    scope_type_counts: Dict[str, int] = {}
    scope_family_counts: Dict[str, int] = {}
    for item in scope_plan:
        if not isinstance(item, dict):
            continue
        scope_type = str(
            item.get("scopeType") or str(item.get("scopeId") or "").split(":", 1)[0] or "reference"
        )
        scope_type_counts[scope_type] = scope_type_counts.get(scope_type, 0) + 1
        scope_family = str(item.get("scopeFamily") or "").strip()
        if not scope_family:
            parts = [part for part in str(item.get("scopeId") or "").split(":") if part]
            scope_family = (
                parts[2]
                if len(parts) >= 3 and parts[0] == "symbol"
                else (parts[0] if parts else "reference")
            )
        scope_family_counts[scope_family] = scope_family_counts.get(scope_family, 0) + 1
    result = {
        "configured": bool(getattr(_store, "address", "")),
        "status": str(active.get("status") or "ok"),
        "graphStore": "typedb",
        "persistenceMode": SCOPED_ABOX_PERSISTENCE_MODE,
        "worldviewManifestId": str(
            active.get("worldviewManifestId") or active.get("aboxSnapshotId") or ""
        ),
        "activeScopeCount": len(scope_plan),
        "scopeTypeCounts": dict(sorted(scope_type_counts.items())),
        "scopeTopologyVersion": str(active.get("scopeTopologyVersion") or ""),
        "scopeFamilyCounts": dict(sorted(scope_family_counts.items())),
        "logicalActiveEntityCount": logical_entities,
        "logicalActiveRelationCount": logical_relations,
        "scopeIds": [
            str(item.get("scopeId") or "") for item in scope_plan if isinstance(item, dict)
        ][:120],
        "keepInactiveManifestCount": _store.abox_inactive_generation_keep_count(),
        "maxInactiveManifestsPrunedPerRun": _store.abox_inactive_generation_max_prune_per_save(),
        # Internal hand-off for the diagnostics service. Its public
        # summary deliberately omits this manifest payload, while the
        # coverage calculation can reuse the verified index without a
        # second control-plane read.
        "_activeAboxMetadata": active,
    }
    try:
        markers = list(_store.worldview_manifest_marker_rows(world_id))
        manifest_ids = {
            str(
                item.get("worldviewManifestId")
                or item.get("aboxSnapshotId")
                or item.get("snapshotId")
                or ""
            )
            for item in markers
        }
        manifest_ids.discard("")
        generation_references: Dict[str, int] = {}
        for marker in markers:
            for generation_id in dict(marker.get("scopeGenerationIds") or {}).values():
                clean_generation_id = str(generation_id or "")
                if clean_generation_id:
                    generation_references[clean_generation_id] = (
                        generation_references.get(clean_generation_id, 0) + 1
                    )
        result.update(
            {
                "storedManifestCount": len(manifest_ids),
                "inactiveManifestCount": max(0, len(manifest_ids) - 1),
                "storedScopeGenerationCount": len(generation_references),
                "sharedHistoricalScopeGenerationCount": len(
                    [
                        generation_id
                        for generation_id, count in generation_references.items()
                        if count > 1
                    ]
                ),
            }
        )
    except Exception as error:  # noqa: BLE001 - physical counts are diagnostic only.
        result["manifestInventoryStatus"] = "error"
        result["manifestInventoryReason"] = str(error)[:180]
    try:
        physical = typedb_call_for_world(_store.box_row_counts, "ABox", world_id=world_id)
        result.update(
            {
                "physicalAboxEntityCount": int(physical.get("entityCount") or 0),
                "physicalAboxRelationCount": int(physical.get("relationCount") or 0),
            }
        )
    except Exception as error:  # noqa: BLE001 - preserve logical lifecycle status.
        result["physicalCountStatus"] = "error"
        result["physicalCountReason"] = str(error)[:180]
    try:
        result["writeLease"] = _store.scoped_abox_write_lease_status(world_id)
    except Exception as error:  # noqa: BLE001 - lease visibility must not hide active world state.
        result["writeLease"] = {
            "status": "error",
            "reason": str(error)[:180],
        }
    return result


def current_state_slot_inventory(
    _store: GraphReadsInventoryStore, driver, imported, physical_generation_ids: Iterable[str]
) -> Dict[str, Dict[str, Dict[str, object]]]:
    """Read bounded physical slot identities and their semantic hashes."""

    generation_ids = sorted(
        {
            str(value or "").strip()
            for value in physical_generation_ids or []
            if str(value or "").startswith("abox-current:")
        }
    )
    result = {"nodes": {}, "relations": {}}
    if not generation_ids:
        return result
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    batch_size = _store.current_state_inventory_batch_size()

    def read_type_inventory(type_label: str, key: str):
        type_inventory: Dict[str, Dict[str, object]] = {}
        for offset in range(0, len(generation_ids), batch_size):
            batch = generation_ids[offset : offset + batch_size]
            slot_filter = typedb_value_match(
                "$item",
                "ontology-snapshot-id",
                batch,
                "==",
                "slotFilter",
            )
            base_query = (
                "match $item isa "
                + type_label
                + ', has ontology-box "ABox", '
                + "has ontology-storage-id $storageId, "
                + "has ontology-scope-id $scopeId, "
                + "has ontology-snapshot-id $snapshotId; "
                + slot_filter
            )
            with driver.transaction(
                _store.database,
                TransactionType.READ,
                options=_store.read_transaction_options(),
            ) as tx:
                identity_rows = _store.read_rows_in_transaction(
                    tx,
                    base_query,
                    ["storageId", "scopeId", "snapshotId"],
                    label="typedb.current-state-slot-inventory",
                )
                fingerprint_rows = _store.read_rows_in_transaction(
                    tx,
                    base_query.replace(
                        "has ontology-snapshot-id $snapshotId; ",
                        "has ontology-snapshot-id $snapshotId, "
                        "has ontology-content-fingerprint $contentFingerprint; ",
                    ),
                    [
                        "storageId",
                        "scopeId",
                        "snapshotId",
                        "contentFingerprint",
                    ],
                    label="typedb.current-state-slot-content",
                )
            fingerprints = {
                str(item.get("storageId") or ""): str(item.get("contentFingerprint") or "")
                for item in fingerprint_rows or []
                if str(item.get("storageId") or "")
            }
            for item in identity_rows or []:
                storage_id = str(item.get("storageId") or "").strip()
                if not storage_id:
                    continue
                type_inventory[storage_id] = {
                    "storageId": storage_id,
                    "scopeId": str(item.get("scopeId") or ""),
                    "physicalGenerationId": str(item.get("snapshotId") or ""),
                    "contentFingerprint": fingerprints.get(storage_id, ""),
                }
        return key, type_inventory

    # Entity and relation inventories are independent read-only TypeDB
    # traversals. Running exactly these two branches concurrently halves
    # the current-state read critical path without increasing write
    # concurrency or changing the legacy-row visibility contract.
    inventory_types = [
        ("ontology-node", "nodes"),
        ("ontology-assertion", "relations"),
    ]
    with ThreadPoolExecutor(max_workers=len(inventory_types)) as executor:
        futures = [
            executor.submit(read_type_inventory, type_label, key)
            for type_label, key in inventory_types
        ]
        for future in as_completed(futures):
            key, type_inventory = future.result()
            result[key].update(type_inventory)
    return result


def current_state_storage_inventory(
    _store: GraphReadsInventoryStore,
    driver,
    imported,
    node_storage_ids: Iterable[str] = None,
    relation_storage_ids: Iterable[str] = None,
) -> Dict[str, Dict[str, Dict[str, object]]]:
    """Verify newly written current-state rows by exact storage identity.

    Newly inserted rows always own a semantic content fingerprint. Legacy
    rows are handled by ``current_state_slot_inventory`` before the delta
    write, so this post-write read can stay exact and avoid rescanning the
    complete physical slots a second time.
    """

    result = {"nodes": {}, "relations": {}}
    storage_ids_by_key = {
        "nodes": sorted(
            {
                str(value or "").strip()
                for value in node_storage_ids or []
                if str(value or "").strip()
            }
        ),
        "relations": sorted(
            {
                str(value or "").strip()
                for value in relation_storage_ids or []
                if str(value or "").strip()
            }
        ),
    }
    if not any(storage_ids_by_key.values()):
        return result
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    batch_size = _store.current_state_inventory_batch_size()

    def read_type_inventory(type_label: str, key: str):
        type_inventory: Dict[str, Dict[str, object]] = {}
        storage_ids = storage_ids_by_key[key]
        for offset in range(0, len(storage_ids), batch_size):
            batch = storage_ids[offset : offset + batch_size]
            query = (
                "match $item isa "
                + type_label
                + ', has ontology-box "ABox", '
                + "has ontology-storage-id $storageId, "
                + "has ontology-scope-id $scopeId, "
                + "has ontology-snapshot-id $snapshotId, "
                + "has ontology-content-fingerprint $contentFingerprint; "
                + typedb_value_match(
                    "$item",
                    "ontology-storage-id",
                    batch,
                    "==",
                    "storageIdFilter",
                )
            )
            with driver.transaction(
                _store.database,
                TransactionType.READ,
                options=_store.read_transaction_options(),
            ) as tx:
                rows = _store.read_rows_in_transaction(
                    tx,
                    query,
                    [
                        "storageId",
                        "scopeId",
                        "snapshotId",
                        "contentFingerprint",
                    ],
                    label="typedb.current-state-storage-verification",
                )
            for item in rows or []:
                storage_id = str(item.get("storageId") or "").strip()
                if not storage_id:
                    continue
                type_inventory[storage_id] = {
                    "storageId": storage_id,
                    "scopeId": str(item.get("scopeId") or ""),
                    "physicalGenerationId": str(item.get("snapshotId") or ""),
                    "contentFingerprint": str(item.get("contentFingerprint") or ""),
                }
        return key, type_inventory

    storage_types = [
        ("ontology-node", "nodes"),
        ("ontology-assertion", "relations"),
    ]
    with ThreadPoolExecutor(max_workers=len(storage_types)) as executor:
        futures = [
            executor.submit(read_type_inventory, type_label, key)
            for type_label, key in storage_types
        ]
        for future in as_completed(futures):
            key, type_inventory = future.result()
            result[key].update(type_inventory)
    return result


def read_active_scoped_abox_rows(
    _store: GraphReadsInventoryStore,
    active_metadata: Dict[str, object],
    scope_ids: Iterable[str],
    world_id: str = "",
    *,
    _bindings: GraphReadsInventoryRuntime
) -> Dict[str, object]:
    """Read exact active rows for a bounded set of scope generations.

    A target-scoped source graph may already contain facts produced by a
    different mailbox event. When those facts are deferred, the active
    Manifest is the only authoritative source for their current semantic
    image. Generation-keyed reads keep this recovery bounded and avoid the
    full active-membership join used by general graph readers.
    """

    active = dict(active_metadata or {})
    generations = {
        str(scope_id or "").strip(): str(generation_id or "").strip()
        for scope_id, generation_id in dict(active.get("scopeGenerationIds") or {}).items()
        if str(scope_id or "").strip() and str(generation_id or "").strip()
    }
    active_plan = {
        str(item.get("scopeId") or "").strip(): dict(item or {})
        for item in active.get("scopePlan") or []
        if str((item or {}).get("scopeId") or "").strip()
    }
    requested = sorted(
        {
            str(scope_id or "").strip()
            for scope_id in scope_ids or []
            if str(scope_id or "").strip() in generations
        }
    )
    if not requested:
        return {
            "status": "ok",
            "scopeIds": [],
            "nodeRows": [],
            "relationRows": [],
            "endpointNodeRows": [],
            "countsByScope": {},
        }

    expected_generation_by_scope = {scope_id: generations[scope_id] for scope_id in requested}
    requested_generations = sorted(set(expected_generation_by_scope.values()))
    generation_scope = {
        generation_id: scope_id for scope_id, generation_id in expected_generation_by_scope.items()
    }
    node_rows: List[Dict[str, object]] = []
    relation_rows: List[Dict[str, object]] = []
    endpoint_rows_by_storage_id: Dict[str, Dict[str, object]] = {}
    batch_size = _store.current_state_inventory_batch_size()
    world_clause = (
        "has ontology-world-id " + typedb_string(world_id) + ", "
        if str(world_id or "").strip()
        else ""
    )

    for offset in range(0, len(requested_generations), batch_size):
        batch = requested_generations[offset : offset + batch_size]
        generation_filter = typedb_value_match(
            "$n",
            "ontology-snapshot-id",
            batch,
            "==",
            "activeScopeGenerationFilter",
        )
        node_query = (
            "match $n isa ontology-node, "
            "has ontology-id $id, "
            "has ontology-storage-id $storageId, "
            "has ontology-label $label, "
            "has ontology-kind $kind, "
            'has ontology-box "ABox", ' + world_clause + "has ontology-scope-id $scopeId, "
            "has ontology-snapshot-id $generationId, "
            "has ontology-updated-at $updatedAt, "
            "has ontology-json $json; " + generation_filter
        )
        raw_nodes = _store.read_rows(
            node_query,
            [
                "id",
                "storageId",
                "label",
                "kind",
                "scopeId",
                "generationId",
                "updatedAt",
                "json",
            ],
            label="typedb.scoped-abox.active-node-rows",
        )
        for raw in raw_nodes:
            scope_id = str(raw.get("scopeId") or "").strip()
            generation_id = str(raw.get("generationId") or "").strip()
            if (
                expected_generation_by_scope.get(scope_id) != generation_id
                or generation_scope.get(generation_id) != scope_id
            ):
                return {
                    "status": "scope-generation-mismatch",
                    "reason": "An active node row did not match the requested scope generation.",
                    "scopeId": scope_id,
                    "generationId": generation_id,
                    "nodeRows": [],
                    "relationRows": [],
                    "endpointNodeRows": [],
                }
            properties = json_object(raw.get("json"))
            mapped = _store.entity_row_from_typeql(raw, "ABox")
            mapped.update(
                {
                    "storageId": str(raw.get("storageId") or ""),
                    "scopeId": scope_id,
                    "scopeType": str(
                        properties.get("aboxScopeType")
                        or (active_plan.get(scope_id) or {}).get("scopeType")
                        or ""
                    ),
                    "snapshotId": generation_id,
                    "aboxSnapshotId": generation_id,
                    "scopeGenerationId": generation_id,
                    "manifestId": str(
                        properties.get("worldviewManifestId") or properties.get("manifestId") or ""
                    ),
                    "worldId": str(properties.get("worldId") or world_id or ""),
                }
            )
            node_rows.append(mapped)

        relation_generation_filter = typedb_value_match(
            "$r",
            "ontology-snapshot-id",
            batch,
            "==",
            "activeRelationGenerationFilter",
        )
        relation_query = (
            "match "
            "$source isa ontology-node, has ontology-id $sourceId, "
            "has ontology-storage-id $sourceStorageId, "
            "has ontology-label $sourceLabel, has ontology-kind $sourceKind, "
            "has ontology-scope-id $sourceScopeId, "
            "has ontology-snapshot-id $sourceGenerationId, "
            "has ontology-updated-at $sourceUpdatedAt, has ontology-json $sourceJson; "
            "$target isa ontology-node, has ontology-id $targetId, "
            "has ontology-storage-id $targetStorageId, "
            "has ontology-label $targetLabel, has ontology-kind $targetKind, "
            "has ontology-scope-id $targetScopeId, "
            "has ontology-snapshot-id $targetGenerationId, "
            "has ontology-updated-at $targetUpdatedAt, has ontology-json $targetJson; "
            "$r isa ontology-assertion, links (source: $source, target: $target), "
            "has ontology-id $id, has ontology-storage-id $storageId, "
            "has ontology-relation-type $type, "
            'has ontology-box "ABox", ' + world_clause + "has ontology-scope-id $scopeId, "
            "has ontology-snapshot-id $generationId, "
            "has ontology-updated-at $updatedAt, has ontology-json $json, "
            "has ontology-weight $weight; " + relation_generation_filter
        )
        raw_relations = _store.read_rows(
            relation_query,
            [
                "id",
                "storageId",
                "sourceId",
                "sourceStorageId",
                "sourceLabel",
                "sourceKind",
                "sourceScopeId",
                "sourceGenerationId",
                "sourceUpdatedAt",
                "sourceJson",
                "targetId",
                "targetStorageId",
                "targetLabel",
                "targetKind",
                "targetScopeId",
                "targetGenerationId",
                "targetUpdatedAt",
                "targetJson",
                "type",
                "scopeId",
                "generationId",
                "updatedAt",
                "json",
                "weight",
            ],
            label="typedb.scoped-abox.active-relation-rows",
        )
        for raw in raw_relations:
            scope_id = str(raw.get("scopeId") or "").strip()
            generation_id = str(raw.get("generationId") or "").strip()
            if (
                expected_generation_by_scope.get(scope_id) != generation_id
                or generation_scope.get(generation_id) != scope_id
            ):
                return {
                    "status": "scope-generation-mismatch",
                    "reason": "An active relation row did not match the requested scope generation.",
                    "scopeId": scope_id,
                    "generationId": generation_id,
                    "nodeRows": [],
                    "relationRows": [],
                    "endpointNodeRows": [],
                }
            properties = json_object(raw.get("json"))
            mapped = _store.relation_row_from_typeql(raw, "ABox")
            mapped.update(
                {
                    "storageId": str(raw.get("storageId") or ""),
                    "sourceStorageId": str(raw.get("sourceStorageId") or ""),
                    "targetStorageId": str(raw.get("targetStorageId") or ""),
                    "scopeId": scope_id,
                    "scopeType": str(
                        properties.get("aboxScopeType")
                        or (active_plan.get(scope_id) or {}).get("scopeType")
                        or ""
                    ),
                    "snapshotId": generation_id,
                    "aboxSnapshotId": generation_id,
                    "scopeGenerationId": generation_id,
                    "manifestId": str(
                        properties.get("worldviewManifestId") or properties.get("manifestId") or ""
                    ),
                    "worldId": str(properties.get("worldId") or world_id or ""),
                }
            )
            for prefix in ("source", "target"):
                endpoint = _bindings.endpoint_node_row(raw, prefix, "ABox")
                endpoint_properties = json_object(raw.get(prefix + "Json"))
                endpoint.update(
                    {
                        "storageId": str(raw.get(prefix + "StorageId") or ""),
                        "scopeId": str(raw.get(prefix + "ScopeId") or ""),
                        "scopeType": str(endpoint_properties.get("aboxScopeType") or ""),
                        "snapshotId": str(raw.get(prefix + "GenerationId") or ""),
                        "aboxSnapshotId": str(raw.get(prefix + "GenerationId") or ""),
                        "scopeGenerationId": str(raw.get(prefix + "GenerationId") or ""),
                        "manifestId": str(
                            endpoint_properties.get("worldviewManifestId")
                            or endpoint_properties.get("manifestId")
                            or ""
                        ),
                        "worldId": str(endpoint_properties.get("worldId") or world_id or ""),
                    }
                )
                endpoint_storage_id = str(endpoint.get("storageId") or "").strip()
                if endpoint_storage_id:
                    endpoint_rows_by_storage_id[endpoint_storage_id] = endpoint
            relation_rows.append(mapped)

    counts_by_scope = _store.scoped_abox_counts_by_scope(node_rows, relation_rows)
    failed_scopes = []
    for scope_id in requested:
        plan = active_plan.get(scope_id) or {}
        actual = counts_by_scope.get(scope_id) or {}
        # OntologyEvidence is persisted as an ontology-node. The scoped
        # Manifest keeps logical entities and evidence separate, while a
        # physical TypeDB read returns both as node rows.
        expected_entity_count = int(number_or_none(plan.get("entityCount")) or 0) + int(
            number_or_none(plan.get("evidenceCount")) or 0
        )
        expected_relation_count = int(number_or_none(plan.get("relationCount")) or 0)
        if (
            int(actual.get("entityCount") or 0) != expected_entity_count
            or int(actual.get("relationCount") or 0) != expected_relation_count
        ):
            failed_scopes.append(
                {
                    "scopeId": scope_id,
                    "generationId": expected_generation_by_scope.get(scope_id, ""),
                    "expectedEntityCount": expected_entity_count,
                    "actualEntityCount": int(actual.get("entityCount") or 0),
                    "expectedRelationCount": expected_relation_count,
                    "actualRelationCount": int(actual.get("relationCount") or 0),
                }
            )
    return {
        "status": "ok" if not failed_scopes else "active-scope-row-count-mismatch",
        "reason": (
            ""
            if not failed_scopes
            else "The active Manifest did not return every row required for semantic reuse."
        ),
        "scopeIds": requested,
        "nodeRows": node_rows,
        "relationRows": relation_rows,
        "endpointNodeRows": list(endpoint_rows_by_storage_id.values()),
        "countsByScope": counts_by_scope,
        "failedScopes": failed_scopes,
    }


def scoped_abox_manifest_generation_references(
    _store: GraphReadsInventoryStore, world_id: str = ""
) -> Dict[str, object]:
    """Return generations protected by a durable Worldview Manifest."""
    manifests = set()
    generations = set()
    for marker in _store.worldview_manifest_marker_rows(world_id):
        manifest_id = str(
            marker.get("worldviewManifestId")
            or marker.get("aboxSnapshotId")
            or marker.get("snapshotId")
            or ""
        ).strip()
        if manifest_id:
            manifests.add(manifest_id)
            generations.add(manifest_id)
        for generation_id in dict(marker.get("scopeGenerationIds") or {}).values():
            clean_generation_id = str(generation_id or "").strip()
            if clean_generation_id:
                generations.add(clean_generation_id)
    try:
        active = _store.active_abox_metadata(world_id)
    except Exception:  # noqa: BLE001 - caller still protects marker-backed generations.
        active = {}
    active_manifest_id = str(
        active.get("worldviewManifestId") or active.get("aboxSnapshotId") or ""
    ).strip()
    if active_manifest_id:
        manifests.add(active_manifest_id)
    for generation_id in dict(active.get("scopeGenerationIds") or {}).values():
        clean_generation_id = str(generation_id or "").strip()
        if clean_generation_id:
            generations.add(clean_generation_id)
    return {
        "manifestIds": manifests,
        "generationIds": generations,
        "activeManifestId": active_manifest_id,
    }


def scoped_abox_orphan_candidate_inventory(
    _store: GraphReadsInventoryStore, world_id: str = ""
) -> Dict[str, object]:
    """Find staged scoped rows not owned by any complete Manifest.

    Interrupted writes cannot have a manifest marker because the marker is
    inserted only after per-scope row verification. They are therefore
    safe to reclaim, except for a generation already referenced by a
    complete active or retained historical Manifest.
    """
    protected = _store.scoped_abox_manifest_generation_references(world_id)
    protected_manifests = set(protected.get("manifestIds") or set())
    protected_generations = set(protected.get("generationIds") or set())
    candidate_manifests = set()
    candidate_generations = set()
    for type_label in ["ontology-node", "ontology-assertion"]:
        rows = _store.read_rows(
            "match $item isa "
            + type_label
            + ', has ontology-box "ABox", has ontology-manifest-id $manifestId, '
            + (
                "has ontology-world-id " + typedb_string(world_id) + ", "
                if str(world_id or "").strip()
                else ""
            )
            + "has ontology-snapshot-id $snapshotId;",
            ["manifestId", "snapshotId"],
            label="typedb.scoped-abox-orphan-inventory",
        )
        for row in rows:
            manifest_id = str(row.get("manifestId") or "").strip()
            generation_id = str(row.get("snapshotId") or "").strip()
            if not manifest_id.startswith("abox-manifest:") or not generation_id.startswith(
                (
                    "abox-scope:",
                    "abox-current:",
                    "abox-current-cow:",
                )
            ):
                continue
            if manifest_id in protected_manifests or generation_id in protected_generations:
                continue
            candidate_manifests.add(manifest_id)
            candidate_generations.add(generation_id)
    return {
        "candidateManifestIds": sorted(candidate_manifests),
        "candidateGenerationIds": sorted(candidate_generations),
        "protectedManifestIds": sorted(protected_manifests),
        "protectedGenerationIds": sorted(protected_generations),
    }


def scoped_abox_scope_row_counts(
    _store: GraphReadsInventoryStore, scope_id: str, generation_id: str
) -> Dict[str, int]:
    clean_scope = str(scope_id or "").strip()
    clean_generation = str(generation_id or "").strip()
    if not clean_scope or not clean_generation:
        return {"entityCount": 0, "relationCount": 0}

    def count(type_label: str) -> int:
        query = (
            "match $item isa "
            + type_label
            + ', has ontology-box "ABox"'
            + ", has ontology-scope-id "
            + typedb_string(clean_scope)
            + ", has ontology-snapshot-id "
            + typedb_string(clean_generation)
            + "; reduce $count = count;"
        )
        rows = _store.read_rows(query, ["count"], label="typedb.scoped-abox-count")
        return int(number_or_none((rows[0] if rows else {}).get("count")) or 0)

    return {
        "entityCount": count("ontology-node"),
        "relationCount": count("ontology-assertion"),
    }


def scoped_abox_scope_row_counts_batch(
    _store: GraphReadsInventoryStore,
    scope_rows: Iterable[Dict[str, object]],
    manifest_id: str = "",
    world_id: str = "",
) -> Dict[str, Dict[str, int]]:
    """Read persisted counts for every staged scope with two TypeQL reductions.

    The initial migration from a broad legacy scope layout can change well
    over one hundred scopes.  Issuing two independent TypeQL reads for
    each scope made that correctness check dominate the migration.  The
    scope generation remains part of the grouping key, so this is still
    an exact physical-write verification rather than an in-memory proxy.
    The active ABox can contain many historical scope generations; a
    staged Manifest must only count its own physical rows.
    """
    expected_pairs = {
        (
            str(item.get("scopeId") or "").strip(),
            str(item.get("generationId") or "").strip(),
        )
        for item in scope_rows or []
        if isinstance(item, dict)
        and str(item.get("scopeId") or "").strip()
        and str(item.get("generationId") or "").strip()
    }
    counts = {
        scope_id: {"entityCount": 0, "relationCount": 0}
        for scope_id, _generation_id in expected_pairs
    }
    if not expected_pairs:
        return counts
    clean_manifest_id = str(manifest_id or "").strip()
    clean_world_id = str(world_id or "").strip()
    # The staged Manifest and World form an exact immutable partition.
    # Adding hundreds of ``or`` branches for every scope/generation pair
    # made TypeDB spend minutes compiling a verification query after a
    # 20-second ABox write. Read the exact partition once and reject any
    # unexpected pair in Python. Legacy callers without both identities
    # retain the explicit pair filter.
    manifest_partitioned = bool(clean_manifest_id and clean_world_id)
    pair_patterns = (
        []
        if manifest_partitioned
        else [
            "$item has ontology-scope-id "
            + typedb_string(scope_id)
            + ", has ontology-snapshot-id "
            + typedb_string(generation_id)
            + ";"
            for scope_id, generation_id in sorted(expected_pairs)
        ]
    )
    pair_filter = (
        ""
        if manifest_partitioned
        else (
            pair_patterns[0]
            if len(pair_patterns) == 1
            else " or ".join("{ " + pattern + " }" for pattern in pair_patterns) + ";"
        )
    )

    def collect(type_label: str, count_key: str) -> None:
        query = (
            "match $item isa "
            + type_label
            + ', has ontology-box "ABox"'
            + (
                ", has ontology-manifest-id " + typedb_string(clean_manifest_id)
                if clean_manifest_id
                else ""
            )
            + (", has ontology-world-id " + typedb_string(clean_world_id) if clean_world_id else "")
            + ", has ontology-scope-id $scopeId"
            + ", has ontology-snapshot-id $generationId"
            + "; "
            + pair_filter
            + " reduce $count = count groupby $scopeId, $generationId;"
        )
        rows = _store.read_rows(
            query,
            ["scopeId", "generationId", "count"],
            label="typedb.scoped-abox-count-batch",
        )
        for row in rows or []:
            scope_id = str(row.get("scopeId") or "").strip()
            generation_id = str(row.get("generationId") or "").strip()
            if (scope_id, generation_id) not in expected_pairs:
                if manifest_partitioned:
                    raise RuntimeError(
                        "Scoped ABox Manifest verification found an unexpected scope generation: "
                        + scope_id
                        + " / "
                        + generation_id
                    )
                continue
            counts.setdefault(scope_id, {"entityCount": 0, "relationCount": 0})[count_key] = int(
                number_or_none(row.get("count")) or 0
            )

    collect("ontology-node", "entityCount")
    collect("ontology-assertion", "relationCount")
    return counts


def scoped_abox_storage_rows_by_id(
    _store: GraphReadsInventoryStore,
    node_storage_ids: Iterable[str],
    relation_storage_ids: Iterable[str],
) -> Dict[str, Dict[str, Dict[str, object]]]:
    """Read immutable scoped rows by physical storage ID.

    This deliberately avoids Manifest provenance in the query. A reused
    scope generation retains the Manifest that first staged it, while the
    active Manifest proves present membership through its scope plan.
    """

    def storage_ids(values: Iterable[str]) -> List[str]:
        return sorted(
            {str(value or "").strip() for value in values or [] if str(value or "").strip()}
        )

    def collect(type_label: str, values: Iterable[str], label: str) -> Dict[str, Dict[str, object]]:
        rows_by_storage_id: Dict[str, Dict[str, object]] = {}
        ids = storage_ids(values)
        for offset in range(0, len(ids), NATIVE_RULE_EVIDENCE_READ_INDEX_BATCH_SIZE):
            batch = ids[offset : offset + NATIVE_RULE_EVIDENCE_READ_INDEX_BATCH_SIZE]
            query = (
                "match $item isa " + type_label + ", "
                "has ontology-storage-id $storageId, "
                "has ontology-id $id, "
                "has ontology-box $ontologyBox, "
                "has ontology-snapshot-id $snapshotId; "
                + typedb_value_match("$item", "ontology-storage-id", batch, "==", "storageIdFilter")
            )
            rows = _store.read_rows(
                query,
                ["storageId", "id", "ontologyBox", "snapshotId"],
                label=label,
            )
            for item in rows or []:
                storage_id = str(item.get("storageId") or "").strip()
                if storage_id:
                    rows_by_storage_id[storage_id] = dict(item)
        return rows_by_storage_id

    return {
        "nodes": collect(
            "ontology-node",
            node_storage_ids,
            "typedb.scoped-abox.node-storage-identity",
        ),
        "relations": collect(
            "ontology-assertion",
            relation_storage_ids,
            "typedb.scoped-abox.relation-storage-identity",
        ),
    }


def scoped_manifest_metadata(
    _store: GraphReadsInventoryStore, manifest_id: str, world_id: str = ""
) -> Dict[str, object]:
    """Load one verified scoped Manifest without consulting the live pointer."""
    clean_manifest_id = str(manifest_id or "").strip()
    if not clean_manifest_id:
        return {}
    try:
        # A Manifest marker contains the complete scoped persistence
        # index.  Reading every historical marker here made a recovery
        # of one interrupted target deserialize every retained ABox
        # generation before it could make progress.  The immutable
        # manifest id is already a precise TypeQL lookup, so keep this
        # recovery read bounded to the requested generation.
        markers = _store.worldview_manifest_marker_rows(
            world_id,
            manifest_id=clean_manifest_id,
            limit=1,
        )
    except Exception:  # noqa: BLE001 - callers retain the current Manifest on lookup failure.
        return {}
    candidates = [
        item
        for item in markers
        if str(
            item.get("worldviewManifestId")
            or item.get("aboxSnapshotId")
            or item.get("snapshotId")
            or ""
        ).strip()
        == clean_manifest_id
    ]
    if not candidates:
        return {}
    marker = sorted(
        candidates,
        key=lambda item: (str(item.get("updatedAt") or ""), str(item.get("id") or "")),
        reverse=True,
    )[0]
    return _store.scoped_abox_metadata_from_manifest_marker(marker)
