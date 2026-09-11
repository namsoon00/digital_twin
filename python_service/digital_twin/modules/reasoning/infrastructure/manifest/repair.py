"""manifest: repair through explicit injected capabilities."""

from digital_twin.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.domain.ontology_scopes import SCOPED_ABOX_MANIFEST_VERSION
from digital_twin.modules.reasoning.infrastructure.abox_candidates.identity import (
    ontology_storage_id,
)
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import json_object
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    normalize_native_rule_evidence_read_index,
)
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from typing import Dict
from .repair_ports import ManifestRepairStore, ManifestRepairRuntime


def replace_scoped_manifest_marker_graph(
    _store: ManifestRepairStore,
    marker_graph: PortfolioOntology,
    *,
    _bindings: ManifestRepairRuntime
) -> Dict[str, object]:
    """Replace one immutable Manifest marker without touching ABox facts.

    A marker can gain a new operational read index during a rolling
    deployment. The index is derived from already staged ABox rows and is
    not an investment fact, so replacing only this control record is safe
    when the Manifest id and every scope generation still match.
    """
    if not getattr(_store, "address", ""):
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "graphStore": "typedb",
            "reason": "TypeDB ontology storage is not configured.",
        }
    node_rows = _store.node_rows(marker_graph)
    if len(node_rows) != 1:
        return {
            "configured": True,
            "saved": False,
            "status": "invalid-marker",
            "graphStore": "typedb",
            "reason": "Scoped Manifest marker replacement requires exactly one marker node.",
        }
    row = node_rows[0]
    marker_id = str(row.get("id") or "").strip()
    storage_id = ontology_storage_id(row, marker_id, "node")
    if not marker_id or not storage_id:
        return {
            "configured": True,
            "saved": False,
            "status": "invalid-marker",
            "graphStore": "typedb",
            "reason": "Scoped Manifest marker identity is incomplete.",
        }
    imported = _store.driver_imports()
    if imported[0] is None:
        return {
            "configured": True,
            "saved": False,
            "status": "driver-missing",
            "graphStore": "typedb",
            "reason": str(imported[1])[:180],
        }
    _TypeDB, _Credentials, _DriverOptions, _DriverTlsConfig, TransactionType = imported[0]
    delete_query = (
        "match $n isa ontology-node, has ontology-storage-id "
        + typedb_string(storage_id)
        + "; delete $n;"
    )
    insert_query = _store.node_insert_query(row, _bindings.utc_now())

    def operation():
        driver = _store.open_driver(imported)
        try:
            _store.ensure_database(driver)
            _store.ensure_schema(driver, imported)
            with _bindings.typedb_operation_timeout(
                _store.write_operation_timeout_seconds(), "TypeDB scoped Manifest marker upgrade"
            ):
                with driver.transaction(
                    _store.database,
                    TransactionType.WRITE,
                    options=_store.write_transaction_options(),
                ) as tx:
                    tx.query(delete_query).resolve()
                    tx.query(insert_query).resolve()
                    tx.commit()
        finally:
            _store.close_driver(driver)

    try:
        _store.with_typedb_retries(operation)
    except (
        Exception
    ) as error:  # noqa: BLE001 - keep the prior active ABox and retry on the next projection.
        return {
            "configured": True,
            "saved": False,
            "status": "error",
            "graphStore": "typedb",
            "reasonCode": _bindings.typedb_error_code(error),
            "reason": str(error)[:220],
        }
    return {
        "configured": True,
        "saved": True,
        "status": "ok",
        "graphStore": "typedb",
        "markerId": marker_id,
        "markerStorageId": storage_id,
    }


def repair_active_manifest_native_rule_evidence_index(
    _store: ManifestRepairStore,
    active_metadata: Dict[str, object] = None,
    world_id: str = "",
    expected_manifest_id: str = "",
    stable_write_lease_held: bool = False,
    *,
    _bindings: ManifestRepairRuntime
) -> Dict[str, object]:
    """Repair only the control-plane index of an unchanged active ABox.

    Callers that already own the scoped ABox write lease can adopt it.
    Other callers acquire the lease before reading physical membership and
    replacing the Manifest marker. The active pointer and every immutable
    ABox generation remain unchanged.
    """
    active = dict(active_metadata or {})
    clean_world_id = str(world_id or active.get("worldId") or "").strip()
    expected_id = str(expected_manifest_id or "").strip()
    current_manifest_id = str(
        active.get("worldviewManifestId") or active.get("aboxSnapshotId") or ""
    ).strip()
    if expected_id and current_manifest_id and expected_id != current_manifest_id:
        return {
            "configured": True,
            "saved": False,
            "status": "stale-manifest",
            "graphStore": "typedb",
            "manifestId": expected_id,
            "activeManifestId": current_manifest_id,
            "reason": "The active Manifest changed before its evidence index could be repaired.",
        }
    existing = normalize_native_rule_evidence_read_index(
        active.get("nativeRuleEvidenceReadIndex"),
        planner_topology=active.get("nativeRulePlannerTopology"),
    )
    if str(existing.get("status") or "") == "ok":
        return {
            "configured": True,
            "saved": False,
            "status": "unchanged",
            "graphStore": "typedb",
            "manifestId": current_manifest_id,
            "fingerprint": str(existing.get("fingerprint") or ""),
        }

    lease: Dict[str, object] = {}
    if not stable_write_lease_held:
        lease = _store.acquire_scoped_abox_write_lease(
            "manifest-index:" + (current_manifest_id or expected_id),
            world_id=clean_world_id,
        )
        if not lease.get("acquired"):
            return {
                "configured": True,
                "saved": False,
                "status": "deferred-scoped-write-lease",
                "graphStore": "typedb",
                "manifestId": current_manifest_id or expected_id,
                "reason": (
                    "A scoped ABox writer is active; the evidence index repair will retry after it releases the lease."
                ),
                "writeLease": _bindings.typedb_projection_coordinator_summary(lease),
            }
    try:
        current = dict(_store.active_abox_metadata(clean_world_id) or {})
        current_manifest_id = str(
            current.get("worldviewManifestId") or current.get("aboxSnapshotId") or ""
        ).strip()
        if str(current.get("status") or "") != "ok" or (
            expected_id and current_manifest_id != expected_id
        ):
            return {
                "configured": True,
                "saved": False,
                "status": "stale-manifest",
                "graphStore": "typedb",
                "manifestId": expected_id,
                "activeManifestId": current_manifest_id,
                "reason": "The active Manifest changed before its evidence index could be repaired.",
            }
        current_existing = normalize_native_rule_evidence_read_index(
            current.get("nativeRuleEvidenceReadIndex"),
            planner_topology=current.get("nativeRulePlannerTopology"),
        )
        if str(current_existing.get("status") or "") == "ok":
            return {
                "configured": True,
                "saved": False,
                "status": "unchanged",
                "graphStore": "typedb",
                "manifestId": current_manifest_id,
                "fingerprint": str(current_existing.get("fingerprint") or ""),
            }

        rebuilt = _store.rebuild_active_manifest_native_rule_evidence_read_index(
            current,
            world_id=clean_world_id,
        )
        if str(rebuilt.get("status") or "") != "ok":
            return {
                "configured": True,
                "saved": False,
                "status": str(rebuilt.get("status") or "repair-read-failed"),
                "graphStore": "typedb",
                "manifestId": current_manifest_id,
                "reason": str(
                    rebuilt.get("reason") or "Active evidence rows could not be indexed."
                )[:220],
                "rebuild": rebuilt,
            }
        markers = _store.worldview_manifest_marker_rows(
            clean_world_id,
            manifest_id=current_manifest_id,
            limit=1,
        )
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
                == current_manifest_id
            ),
            {},
        )
        if not marker:
            return {
                "configured": True,
                "saved": False,
                "status": "manifest-marker-missing",
                "graphStore": "typedb",
                "manifestId": current_manifest_id,
                "reason": "The active Manifest marker is unavailable for evidence-index repair.",
            }
        properties = json_object(marker.get("propertiesJson"))
        properties.update(
            {
                "ontologyBox": "ABox",
                "worldId": clean_world_id or str(current.get("worldId") or ""),
                "worldType": str(current.get("worldType") or properties.get("worldType") or ""),
                "tenantId": str(current.get("tenantId") or properties.get("tenantId") or ""),
                "accountId": str(current.get("accountId") or properties.get("accountId") or ""),
                "tboxClass": "WorldviewManifest",
                "snapshotId": current_manifest_id,
                "aboxSnapshotId": current_manifest_id,
                "worldviewManifestId": current_manifest_id,
                "aboxScopeId": str(
                    properties.get("aboxScopeId") or "manifest:" + current_manifest_id
                ),
                "aboxScopeType": "manifest",
                "scopeGenerationId": str(
                    properties.get("scopeGenerationId") or current_manifest_id
                ),
                "scopePlan": list(current.get("scopePlan") or properties.get("scopePlan") or []),
                "scopeGenerationIds": dict(
                    current.get("scopeGenerationIds") or properties.get("scopeGenerationIds") or {}
                ),
                "nativeRulePlannerTopology": dict(
                    current.get("nativeRulePlannerTopology")
                    or properties.get("nativeRulePlannerTopology")
                    or {}
                ),
                "nativeRuleEvidenceReadIndex": dict(rebuilt.get("index") or {}),
                "nativeRuleEvidenceReadIndexMerge": {
                    "status": "recovered-active-membership",
                    "sourceCount": int(rebuilt.get("sourceCount") or 0),
                    "relationCount": int(rebuilt.get("relationCount") or 0),
                    "readQueryCount": int(rebuilt.get("readQueryCount") or 0),
                    "durationMs": int(rebuilt.get("durationMs") or 0),
                },
                "projectionStatus": "complete",
                "scopedAboxManifestVersion": SCOPED_ABOX_MANIFEST_VERSION,
            }
        )
        marker_graph = PortfolioOntology(
            str(properties.get("accountId") or "typedb-scoped-manifest"),
            entities=[
                OntologyEntity(
                    entity_id=str(
                        marker.get("id") or "worldview-manifest-marker:" + current_manifest_id
                    ),
                    label=str(marker.get("label") or "Worldview Manifest " + current_manifest_id),
                    kind="worldview-manifest-marker",
                    properties=properties,
                )
            ],
        )
        replacement = _store.replace_scoped_manifest_marker_graph(marker_graph)
        if not replacement.get("saved"):
            return {
                **dict(replacement or {}),
                "manifestId": current_manifest_id,
                "reason": str(
                    replacement.get("reason")
                    or "Manifest evidence-index marker replacement failed."
                )[:220],
                "rebuild": rebuilt,
            }
        with _store._active_scoped_abox_metadata_cache_lock:
            for cache_key in [
                key for key in _store._active_scoped_abox_metadata_cache if key[0] == clean_world_id
            ]:
                _store._active_scoped_abox_metadata_cache.pop(cache_key, None)
        verified_active = dict(_store.active_abox_metadata(clean_world_id) or {})
        verified_manifest_id = str(
            verified_active.get("worldviewManifestId")
            or verified_active.get("aboxSnapshotId")
            or ""
        ).strip()
        verified_index = normalize_native_rule_evidence_read_index(
            verified_active.get("nativeRuleEvidenceReadIndex"),
            planner_topology=verified_active.get("nativeRulePlannerTopology"),
        )
        if (
            verified_manifest_id != current_manifest_id
            or str(verified_index.get("status") or "") != "ok"
        ):
            return {
                "configured": True,
                "saved": False,
                "status": "verification-failed",
                "graphStore": "typedb",
                "manifestId": current_manifest_id,
                "activeManifestId": verified_manifest_id,
                "reason": str(
                    verified_index.get("reason")
                    or "The repaired evidence index could not be verified."
                )[:220],
                "rebuild": rebuilt,
            }
        return {
            "configured": True,
            "saved": True,
            "status": "ok",
            "graphStore": "typedb",
            "manifestId": current_manifest_id,
            "fingerprint": str(verified_index.get("fingerprint") or ""),
            "replacement": replacement,
            "rebuild": rebuilt,
        }
    finally:
        if lease:
            try:
                _store.release_scoped_abox_write_lease(lease)
            except Exception:
                pass


def ensure_scoped_manifest_evidence_read_index(
    _store: ManifestRepairStore,
    graph: PortfolioOntology,
    active_metadata: Dict[str, object] = None,
    world_id: str = "",
) -> Dict[str, object]:
    """Backfill a verified evidence-read index for one unchanged Manifest.

    The ABox generation and its TypeDB native rule semantics stay exactly
    the same. Only the marker metadata changes, allowing an upgraded
    runtime to read the already active physical evidence rows without a
    legacy Manifest-wide join.
    """
    if not getattr(_store, "address", ""):
        return {
            "configured": False,
            "saved": False,
            "status": "disabled",
            "graphStore": "typedb",
        }
    worldview = dict(getattr(graph, "worldview", {}) or {})
    manifest_id = str(
        worldview.get("worldviewManifestId") or worldview.get("aboxSnapshotId") or ""
    ).strip()
    if not manifest_id:
        return {
            "configured": True,
            "saved": False,
            "status": "invalid-scoped-manifest",
            "graphStore": "typedb",
            "reason": "Scoped Manifest metadata is unavailable for evidence-index upgrade.",
        }
    requested_world_id = str(world_id or worldview.get("worldId") or "").strip()
    active = dict(active_metadata or {})
    if not active:
        try:
            active = dict(_store.active_abox_metadata(requested_world_id) or {})
        except (
            Exception
        ) as error:  # noqa: BLE001 - do not write against an uncertain active pointer.
            return {
                "configured": True,
                "saved": False,
                "status": "active-manifest-unreadable",
                "graphStore": "typedb",
                "reason": str(error)[:220],
            }
    return _store.repair_active_manifest_native_rule_evidence_index(
        active,
        world_id=requested_world_id,
        expected_manifest_id=manifest_id,
        stable_write_lease_held=False,
    )
