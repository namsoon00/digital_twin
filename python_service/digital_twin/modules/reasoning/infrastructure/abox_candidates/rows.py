"""Reconcile a candidate image and validate exact endpoint/generation closure."""

from typing import Dict, Iterable, List, Set

import json
from digital_twin.modules.reasoning.domain.ontology_current_state import CURRENT_STATE_ABOX_PERSISTENCE_MODE
from digital_twin.modules.reasoning.domain.ontology_scopes import SCOPE_NODE_INVENTORY_VERSION
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import json_object
from .identity import ontology_storage_id, ontology_row_content_fingerprint, relation_row_id


def scoped_abox_candidate_persistence_rows(current_node_rows: Iterable[Dict[str, object]], current_relation_rows: Iterable[Dict[str, object]], active_scope_rows: Dict[str, object], physical_scope_plan: Iterable[Dict[str, object]], semantic_changed_scope_ids: Iterable[str], physical_changed_scope_ids: Iterable[str], deferred_scope_ids: Iterable[str], candidate_manifest_id: str) -> Dict[str, object]:
    """Reconcile source rows with the active semantic image before writes.

    The current projection graph is allowed to be newer than the event
    being persisted. Deferred scopes therefore come from the active
    Manifest, while semantically selected scopes come from the source
    graph. Relations changed only for endpoint rebinding preserve their
    active assertion and receive only new physical identities.
    """

    current_nodes = [dict(row or {}) for row in current_node_rows or []]
    current_relations = [dict(row or {}) for row in current_relation_rows or []]
    active_context = dict(active_scope_rows or {})
    active_nodes = [
        dict(row or {})
        for row in (
            list(active_context.get("nodeRows") or [])
            + list(active_context.get("endpointNodeRows") or [])
        )
    ]
    active_relations = [
        dict(row or {})
        for row in active_context.get("relationRows") or []
    ]
    plan_by_scope = {
        str(item.get("scopeId") or "").strip(): dict(item or {})
        for item in physical_scope_plan or []
        if str((item or {}).get("scopeId") or "").strip()
    }
    semantic_changed = {
        str(value or "").strip()
        for value in semantic_changed_scope_ids or []
        if str(value or "").strip()
    }
    physical_changed = {
        str(value or "").strip()
        for value in physical_changed_scope_ids or []
        if str(value or "").strip()
    }
    deferred = {
        str(value or "").strip()
        for value in deferred_scope_ids or []
        if str(value or "").strip()
    }
    rebind_only = {
        scope_id
        for scope_id in physical_changed - semantic_changed
        if int(number_or_none((plan_by_scope.get(scope_id) or {}).get("relationCount")) or 0) > 0
    }
    current_nodes_by_id = {
        str(row.get("id") or ""): row
        for row in current_nodes
        if str(row.get("id") or "")
    }

    def active_endpoint(row: Dict[str, object], prefix: str) -> Dict[str, object]:
        storage_id = str(row.get(prefix + "StorageId") or "").strip()
        canonical_id = str(row.get(prefix) or "").strip()
        for candidate in active_nodes:
            if storage_id and str(candidate.get("storageId") or "") == storage_id:
                return candidate
        for candidate in active_nodes:
            if canonical_id and str(candidate.get("id") or "") == canonical_id:
                return candidate
        return {}

    def rebound_relation(row: Dict[str, object]) -> Dict[str, object]:
        scope_id = str(row.get("scopeId") or "").strip()
        physical = plan_by_scope.get(scope_id) or {}
        generation_id = str(physical.get("generationId") or "").strip()
        if not generation_id:
            raise ValueError("Rebound relation scope has no physical generation: " + scope_id)
        endpoint_storage_ids = {}
        for prefix in ("source", "target"):
            endpoint = active_endpoint(row, prefix)
            endpoint_id = str(row.get(prefix) or "").strip()
            endpoint_scope_id = str(endpoint.get("scopeId") or "").strip()
            current_endpoint = current_nodes_by_id.get(endpoint_id) or {}
            if endpoint_scope_id in physical_changed:
                if not current_endpoint:
                    raise ValueError(
                        "A rebound relation endpoint changed generation but is absent from the current graph: "
                        + endpoint_id
                    )
                endpoint_storage_ids[prefix + "StorageId"] = ontology_storage_id(
                    current_endpoint,
                    endpoint_id,
                    "node",
                )
            else:
                endpoint_storage_ids[prefix + "StorageId"] = str(
                    row.get(prefix + "StorageId")
                    or endpoint.get("storageId")
                    or ""
                ).strip()
            if not endpoint_storage_ids[prefix + "StorageId"]:
                raise ValueError(
                    "A rebound relation endpoint has no verified storage identity: "
                    + endpoint_id
                )

        properties = json_object(row.get("propertiesJson"))
        properties.update({
            "aboxScopeId": scope_id,
            "aboxScopeType": str(
                physical.get("scopeType")
                or row.get("scopeType")
                or properties.get("aboxScopeType")
                or "link"
            ),
            "logicalScopeGenerationId": str(
                physical.get("logicalGenerationId")
                or properties.get("logicalScopeGenerationId")
                or ""
            ),
            "physicalGenerationId": generation_id,
            "scopeGenerationId": generation_id,
            "snapshotId": generation_id,
            "aboxSnapshotId": generation_id,
            "physicalStateMode": str(
                physical.get("physicalStateMode")
                or properties.get("physicalStateMode")
                or CURRENT_STATE_ABOX_PERSISTENCE_MODE
            ),
        })
        if candidate_manifest_id:
            properties["manifestId"] = candidate_manifest_id
            properties["worldviewManifestId"] = candidate_manifest_id
        rebound = {
            key: value
            for key, value in row.items()
            if key not in {
                "contentFingerprint", "id", "relationStorageId", "sourceNode",
                "storageId", "targetNode", "updatedAt",
            }
        }
        rebound.update({
            **endpoint_storage_ids,
            "scopeId": scope_id,
            "scopeType": str(properties.get("aboxScopeType") or "link"),
            "snapshotId": generation_id,
            "aboxSnapshotId": generation_id,
            "scopeGenerationId": generation_id,
            "physicalGenerationId": generation_id,
            "logicalScopeGenerationId": str(
                properties.get("logicalScopeGenerationId") or ""
            ),
            "physicalStateMode": str(properties.get("physicalStateMode") or ""),
            "manifestId": candidate_manifest_id,
            "propertiesJson": json.dumps(
                properties,
                ensure_ascii=False,
                sort_keys=True,
            ),
        })
        rebound["contentFingerprint"] = ontology_row_content_fingerprint(
            rebound,
            "relation",
        )
        return rebound

    active_relations_by_scope: Dict[str, List[Dict[str, object]]] = {}
    for row in active_relations:
        active_relations_by_scope.setdefault(
            str(row.get("scopeId") or "").strip(),
            [],
        ).append(row)
    current_relations_by_scope: Dict[str, List[Dict[str, object]]] = {}
    for row in current_relations:
        current_relations_by_scope.setdefault(
            str(row.get("scopeId") or "").strip(),
            [],
        ).append(row)

    # Resolve relation endpoints against the exact candidate node image,
    # not against whichever generation happened to produce the incoming
    # row. A fact-slot patch can defer one endpoint scope while a related
    # link is physically rebound. In that case the active node storage ID
    # is authoritative; a brand-new endpoint that is not part of the
    # candidate must fail before any TypeDB write starts.
    def row_generation_id(row: Dict[str, object]) -> str:
        return str(
            row.get("scopeGenerationId")
            or row.get("physicalGenerationId")
            or row.get("snapshotId")
            or row.get("aboxSnapshotId")
            or ""
        ).strip()

    def node_matches_candidate_plan(row: Dict[str, object]) -> bool:
        scope_id = str(row.get("scopeId") or "").strip()
        if not scope_id:
            # Static TBox/RuleBox endpoints can participate in an ABox
            # relation without belonging to the scoped ABox Manifest.
            return True
        planned = plan_by_scope.get(scope_id) or {}
        planned_generation = str(planned.get("generationId") or "").strip()
        return bool(
            planned_generation
            and row_generation_id(row) == planned_generation
        )

    all_nodes_by_id: Dict[str, List[Dict[str, object]]] = {}
    for row in [*active_nodes, *current_nodes]:
        node_id = str(row.get("id") or "").strip()
        if node_id:
            all_nodes_by_id.setdefault(node_id, []).append(row)

    candidate_relation_endpoint_ids = {
        str(row.get(prefix) or "").strip()
        for row in [*current_relations, *active_relations]
        for prefix in ("source", "target")
        if str(row.get("scopeId") or "").strip() in (
            semantic_changed | deferred | rebind_only
        )
        and str(row.get(prefix) or "").strip()
    }

    def node_declared_by_candidate_manifest(row: Dict[str, object]) -> bool:
        scope_id = str(row.get("scopeId") or "").strip()
        node_id = str(row.get("id") or "").strip()
        planned = plan_by_scope.get(scope_id) or {}
        return bool(
            node_id
            and str(planned.get("nodeInventoryVersion") or "").strip()
            == SCOPE_NODE_INVENTORY_VERSION
            and node_id in {
                str(value or "").strip()
                for value in planned.get("nodeIds") or []
                if str(value or "").strip()
            }
        )

    candidate_nodes_by_id: Dict[str, Dict[str, object]] = {}
    for row in active_nodes:
        node_id = str(row.get("id") or "").strip()
        if node_id and node_matches_candidate_plan(row):
            # Active endpoint rows are loaded only for scopes needed by
            # semantic reuse or relation rebinding.  An unchanged endpoint
            # can be reached through one of those relations even when its
            # own scope is neither changed nor explicitly deferred.  Its
            # exact planned generation is sufficient proof that it belongs
            # to the candidate Manifest.
            candidate_nodes_by_id[node_id] = row
    integrity_companion_node_ids_set: Set[str] = set()
    for row in current_nodes:
        node_id = str(row.get("id") or "").strip()
        scope_id = str(row.get("scopeId") or "").strip()
        integrity_companion = bool(
            scope_id not in physical_changed
            and node_id in candidate_relation_endpoint_ids
            and node_declared_by_candidate_manifest(row)
        )
        if (
            node_id
            and (
                scope_id in physical_changed
                or integrity_companion
            )
            and scope_id not in deferred
            and node_matches_candidate_plan(row)
        ):
            if integrity_companion and node_id not in candidate_nodes_by_id:
                integrity_companion_node_ids_set.add(node_id)
            candidate_nodes_by_id[node_id] = row
    integrity_companion_node_ids = sorted(integrity_companion_node_ids_set)

    class CandidateRelationEndpointError(ValueError):
        def __init__(self, details: Dict[str, object]):
            self.details = dict(details or {})
            super().__init__(
                str(
                    self.details.get("reason")
                    or "Candidate relation endpoint is invalid."
                )
            )

    def candidate_endpoint_storage_id(
        row: Dict[str, object],
        prefix: str,
    ) -> str:
        endpoint_id = str(row.get(prefix) or "").strip()
        endpoint = candidate_nodes_by_id.get(endpoint_id) or {}
        if endpoint:
            return str(
                endpoint.get("storageId")
                or ontology_storage_id(endpoint, endpoint_id, "node")
            ).strip()
        existing_storage_id = str(
            row.get(prefix + "StorageId") or ""
        ).strip()
        known_rows = list(all_nodes_by_id.get(endpoint_id) or [])
        if existing_storage_id and not known_rows:
            # No scoped ABox row owns this canonical id. Keep the storage
            # identity for an external static endpoint; the TypeDB
            # inventory check still proves that it exists before writes.
            return existing_storage_id
        known_scope_generations = sorted({
            str(item.get("scopeId") or "").strip()
            + "@" + row_generation_id(item)
            for item in known_rows
            if str(item.get("scopeId") or "").strip()
        })
        raise CandidateRelationEndpointError({
            "reason": (
                "A relation endpoint is absent from the exact candidate graph: "
                + endpoint_id
                + "; known=" + ",".join(known_scope_generations[:3])
            ),
            "scopeId": str(row.get("scopeId") or "").strip(),
            "relationType": str(row.get("type") or "").strip(),
            "source": str(row.get("source") or "").strip(),
            "target": str(row.get("target") or "").strip(),
            "endpointRole": prefix,
            "endpointId": endpoint_id,
            "endpointStorageId": existing_storage_id,
            "knownEndpointScopeIds": sorted({
                str(item.get("scopeId") or "").strip()
                for item in known_rows
                if str(item.get("scopeId") or "").strip()
            }),
            "knownEndpointGenerationIds": sorted({
                row_generation_id(item)
                for item in known_rows
                if row_generation_id(item)
            }),
            "candidateManifestId": candidate_manifest_id,
        })

    def relation_with_candidate_endpoints(
        row: Dict[str, object],
    ) -> Dict[str, object]:
        resolved = dict(row or {})
        for prefix in ("source", "target"):
            resolved[prefix + "StorageId"] = candidate_endpoint_storage_id(
                resolved,
                prefix,
            )
        resolved["contentFingerprint"] = ontology_row_content_fingerprint(
            resolved,
            "relation",
        )
        return resolved

    rebound_relations: List[Dict[str, object]] = []
    current_fallback_relation_scope_ids: List[str] = []
    for scope_id in sorted(rebind_only):
        rows = list(active_relations_by_scope.get(scope_id) or [])
        expected = int(
            number_or_none((plan_by_scope.get(scope_id) or {}).get("relationCount"))
            or 0
        )
        if len(rows) != expected:
            return {
                "status": "active-rebind-relation-count-mismatch",
                "reason": "A physical relation rebind could not recover its complete active semantic image.",
                "scopeId": scope_id,
                "expectedRelationCount": expected,
                "actualRelationCount": len(rows),
            }
        try:
            rebound_scope_rows = [rebound_relation(row) for row in rows]
        except ValueError as error:
            # Derived evidence can be replaced when its owning fact scope
            # changes. Prefer the active assertion, but use the complete
            # current relation scope when the old endpoint no longer has a
            # representation in the new generation.
            current_scope_rows = list(
                current_relations_by_scope.get(scope_id) or []
            )
            try:
                candidate_endpoint_rows = [
                    relation_with_candidate_endpoints(row)
                    for row in current_scope_rows
                ]
            except CandidateRelationEndpointError as candidate_error:
                candidate_endpoint_rows = []
                error = candidate_error
            if (
                len(current_scope_rows) != expected
                or len(candidate_endpoint_rows) != expected
            ):
                return {
                    "status": "active-rebind-endpoint-invalid",
                    "reason": str(error),
                    "scopeId": scope_id,
                    "expectedRelationCount": expected,
                    "currentRelationCount": len(current_scope_rows),
                }
            rebound_scope_rows = candidate_endpoint_rows
            current_fallback_relation_scope_ids.append(scope_id)
        rebound_relations.extend(rebound_scope_rows)

    # The complete in-memory source can contain facts newer than this
    # mailbox event.  Only semantically selected current relation scopes
    # belong to this candidate; unchanged scopes remain represented by
    # the active Manifest/index.  Including every current relation made an
    # unrelated article or portfolio fact fail a target-symbol write when
    # its endpoint scope was intentionally deferred.
    candidate_relations: List[Dict[str, object]] = [
        row
        for row in current_relations
        if str(row.get("scopeId") or "").strip() in semantic_changed
        and str(row.get("scopeId") or "").strip() not in deferred
        and str(row.get("scopeId") or "").strip() not in rebind_only
    ]
    candidate_relations.extend(
        row
        for row in active_relations
        if str(row.get("scopeId") or "").strip() in deferred
        and str(row.get("scopeId") or "").strip() not in rebind_only
    )
    candidate_relations.extend(rebound_relations)

    normalized_candidate_relations: List[Dict[str, object]] = []
    for row in candidate_relations:
        scope_id = str(row.get("scopeId") or "").strip()
        planned_generation = str(
            (plan_by_scope.get(scope_id) or {}).get("generationId") or ""
        ).strip()
        if planned_generation and row_generation_id(row) != planned_generation:
            return {
                "status": "candidate-relation-generation-mismatch",
                "reason": "A candidate relation belongs to a different physical scope generation.",
                "scopeId": scope_id,
                "relationType": str(row.get("type") or "").strip(),
                "source": str(row.get("source") or "").strip(),
                "target": str(row.get("target") or "").strip(),
                "expectedGenerationId": planned_generation,
                "actualGenerationId": row_generation_id(row),
            }
        try:
            normalized_candidate_relations.append(
                relation_with_candidate_endpoints(row)
            )
        except CandidateRelationEndpointError as error:
            return {
                "status": "candidate-relation-endpoint-missing",
                **error.details,
            }
    candidate_relations_by_storage_id = {
        ontology_storage_id(row, relation_row_id(row), "relation"): row
        for row in normalized_candidate_relations
        if str(row.get("source") or "") and str(row.get("target") or "")
    }
    candidate_node_rows = sorted(
        candidate_nodes_by_id.values(),
        key=lambda row: (str(row.get("scopeId") or ""), str(row.get("id") or "")),
    )
    candidate_relation_rows = sorted(
        candidate_relations_by_storage_id.values(),
        key=lambda row: (
            str(row.get("scopeId") or ""),
            str(row.get("source") or ""),
            str(row.get("type") or ""),
            str(row.get("target") or ""),
        ),
    )
    persistence_node_rows = [
        row
        for row in current_nodes
        if str(row.get("scopeId") or "").strip() in physical_changed
        and node_matches_candidate_plan(row)
    ]
    persistence_relation_rows = [
        row
        for row in candidate_relation_rows
        if str(row.get("scopeId") or "").strip() in physical_changed
    ]

    expected_counts = {
        scope_id: {
            # Physical node counts include separately modelled evidence.
            "entityCount": (
                int(number_or_none(plan.get("entityCount")) or 0)
                + int(number_or_none(plan.get("evidenceCount")) or 0)
            ),
            "relationCount": int(number_or_none(plan.get("relationCount")) or 0),
        }
        for scope_id, plan in plan_by_scope.items()
        if scope_id in physical_changed
    }
    actual_counts: Dict[str, Dict[str, int]] = {}
    for row, field in [
        *((row, "entityCount") for row in persistence_node_rows),
        *((row, "relationCount") for row in persistence_relation_rows),
    ]:
        scope_id = str(row.get("scopeId") or "").strip()
        actual_counts.setdefault(
            scope_id,
            {"entityCount": 0, "relationCount": 0},
        )[field] += 1
    failed_scopes = [
        {
            "scopeId": scope_id,
            "expected": expected,
            "actual": actual_counts.get(
                scope_id,
                {"entityCount": 0, "relationCount": 0},
            ),
        }
        for scope_id, expected in sorted(expected_counts.items())
        if actual_counts.get(
            scope_id,
            {"entityCount": 0, "relationCount": 0},
        ) != expected
    ]
    if failed_scopes:
        return {
            "status": "candidate-scope-row-count-mismatch",
            "reason": "The reconciled candidate rows do not match the physical scope plan.",
            "failedScopes": failed_scopes,
        }
    return {
        "status": "ok",
        "nodeRows": persistence_node_rows,
        "relationRows": persistence_relation_rows,
        "candidateNodeRows": candidate_node_rows,
        "candidateRelationRows": candidate_relation_rows,
        "semanticChangedScopeIds": sorted(semantic_changed),
        "physicalChangedScopeIds": sorted(physical_changed),
        "rebindOnlyRelationScopeIds": sorted(rebind_only),
        "currentFallbackRelationScopeIds": sorted(
            current_fallback_relation_scope_ids
        ),
        "deferredScopeIds": sorted(deferred),
        "reusedActiveNodeCount": len(active_nodes),
        "reusedActiveRelationCount": len(active_relations),
        "reboundRelationCount": len(rebound_relations),
        "integrityCompanionNodeIds": integrity_companion_node_ids,
        "integrityCompanionNodeCount": len(integrity_companion_node_ids),
    }
