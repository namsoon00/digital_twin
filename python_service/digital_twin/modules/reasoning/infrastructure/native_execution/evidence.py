"""native_execution: evidence through explicit injected capabilities."""

from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from digital_twin.modules.model_registry.contracts import GraphInferenceRule
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.abox_candidates.identity import (
    ontology_storage_id,
    relation_row_id,
)
from digital_twin.modules.reasoning.infrastructure.abox_persistence.world_calls import (
    typedb_call_for_world,
)
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import json_object
from digital_twin.modules.reasoning.infrastructure.manifest.index_values import (
    native_rule_matched_evidence_storage_plan,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import symbol_from_subject
from typing import Dict, Iterable, List
import copy
from .evidence_ports import NativeExecutionEvidenceStore


def projection_graph_for_native_matches(
    _store: NativeExecutionEvidenceStore,
    projection_graph: PortfolioOntology,
    native_match_result: Dict[str, object],
    rules: Iterable[GraphInferenceRule] = None,
    evidence_read_index: Dict[str, object] = None,
) -> Dict[str, object]:
    """Reuse a verified projection graph only when it contains exact evidence.

    TypeDB has already evaluated the direct TypeQL rules. This method does
    not evaluate a condition; it proves that the in-memory graph contains
    every physical ABox row the active Manifest says is needed to explain
    those matches. Any mismatch returns ``incomplete`` so the caller uses
    the durable TypeDB evidence read.
    """

    if not isinstance(projection_graph, PortfolioOntology):
        return {
            "status": "unavailable",
            "reason": "No verified projection graph is available.",
        }
    verified = dict(evidence_read_index or {})
    if str(verified.get("status") or "") != "verified":
        return {
            "status": "unavailable",
            "reason": "The active Manifest evidence index is not verified.",
        }
    index = dict(verified.get("index") or {})
    matches = [
        dict(item)
        for item in (native_match_result or {}).get("matches") or []
        if isinstance(item, dict) and str(item.get("sourceId") or "").strip()
    ]
    source_ids = sorted({str(item.get("sourceId") or "").strip() for item in matches})
    if not source_ids:
        return {
            "status": "unavailable",
            "reason": "No matched TypeDB source requires an evidence graph.",
        }
    evidence_plan = native_rule_matched_evidence_storage_plan(
        native_match_result,
        rules,
        verified,
    )
    relation_types = list(evidence_plan.get("relationTypes") or [])
    source_storage_by_id = dict(index.get("sourceStorageIdsBySourceId") or {})
    expected_source_storage_ids = {
        str(source_storage_by_id.get(source_id) or "").strip()
        for source_id in source_ids
        if str(source_storage_by_id.get(source_id) or "").strip()
    }
    if len(expected_source_storage_ids) != len(source_ids):
        return {
            "status": "incomplete",
            "reason": "The active Manifest is missing a matched source storage identity.",
        }
    expected_relation_storage_ids = {
        str(storage_id or "").strip()
        for storage_id in evidence_plan.get("relationStorageIds") or []
        if str(storage_id or "").strip()
    }
    node_rows, relation_rows = _store.graph_persistence_rows(projection_graph)
    available_node_storage_ids = {
        ontology_storage_id(row, row.get("id"), "node") for row in node_rows
    }
    relation_rows_by_storage_id = {
        ontology_storage_id(row, relation_row_id(row), "relation"): row for row in relation_rows
    }
    relation_storage_id_by_row_id = {
        relation_row_id(row): storage_id for storage_id, row in relation_rows_by_storage_id.items()
    }
    missing_source_storage_ids = sorted(expected_source_storage_ids - available_node_storage_ids)
    missing_relation_storage_ids = sorted(
        expected_relation_storage_ids - set(relation_rows_by_storage_id)
    )
    if missing_source_storage_ids or missing_relation_storage_ids:
        return {
            "status": "incomplete",
            "reason": (
                "The verified projection graph does not contain every active Manifest evidence row."
            ),
            "missingSourceStorageIds": missing_source_storage_ids[:20],
            "missingRelationStorageIds": missing_relation_storage_ids[:20],
        }
    graph = copy.deepcopy(projection_graph)
    selected_relations: List[OntologyRelation] = []
    selected_endpoint_ids = set(source_ids)
    for relation in graph.relations:
        properties = dict(relation.properties or {})
        row_id = relation_row_id(
            {
                "source": relation.source,
                "target": relation.target,
                "type": relation.relation_type,
                "ontologyBox": properties.get("ontologyBox") or "ABox",
                "worldId": properties.get("worldId") or "",
                "snapshotId": properties.get("snapshotId")
                or properties.get("aboxSnapshotId")
                or "",
                "ruleId": properties.get("ruleId") or "",
            }
        )
        properties.setdefault("_relationId", row_id)
        relation.properties = properties
        storage_id = str(relation_storage_id_by_row_id.get(row_id) or "")
        if storage_id in expected_relation_storage_ids:
            selected_relations.append(relation)
            selected_endpoint_ids.update([relation.source, relation.target])
    if len(selected_relations) != len(expected_relation_storage_ids):
        return {
            "status": "incomplete",
            "reason": "The projection graph cannot materialize every selected exact evidence relation.",
            "expectedRelationCount": len(expected_relation_storage_ids),
            "availableRelationCount": len(selected_relations),
        }
    graph.relations = selected_relations
    graph.entities = [
        entity for entity in graph.entities if str(entity.entity_id or "") in selected_endpoint_ids
    ]
    graph.evidence = []
    graph.beliefs = []
    graph.opinions = []
    graph.reasoning_cards = []
    graph.worldview["nativeEvidenceRead"] = {
        "status": "ok",
        "mode": "projection-verified-in-memory",
        "source": "active-manifest+projection-write-lease",
        "reason": "",
        "expectedSourceCount": len(source_ids),
        "loadedSourceCount": len(source_ids),
        "indexedRelationStorageCount": len(expected_relation_storage_ids),
        "loadedRelationCount": len(expected_relation_storage_ids),
        "relationReadScope": str(evidence_plan.get("relationReadScope") or "matched-rule-types"),
        "candidateRelationStorageCount": int(
            evidence_plan.get("candidateRelationStorageCount") or 0
        ),
        "selectedEvidenceStorageCount": int(evidence_plan.get("selectedEvidenceStorageCount") or 0),
        "evidenceNarrowingPct": float(evidence_plan.get("evidenceNarrowingPct") or 0.0),
        "evidenceFallbackConditionCount": int(evidence_plan.get("fallbackConditionCount") or 0),
    }
    return {
        "status": "ok",
        "graph": graph,
        "sourceCount": len(source_ids),
        "relationCount": len(expected_relation_storage_ids),
    }


def load_graph_for_native_matches(
    _store: NativeExecutionEvidenceStore,
    native_match_result: Dict[str, object],
    rules: Iterable[GraphInferenceRule] = None,
    include_all_rule_relation_types: bool = False,
    include_incoming_relations: bool = True,
    evidence_read_index: Dict[str, object] = None,
    world_id: str = "",
) -> PortfolioOntology:
    matches = [
        item for item in (native_match_result or {}).get("matches") or [] if isinstance(item, dict)
    ]
    source_ids = sorted(
        set(
            str(item.get("sourceId") or "").strip()
            for item in matches
            if str(item.get("sourceId") or "").strip()
        )
    )
    matched_rule_ids = {str(item.get("ruleId") or "").strip() for item in matches}
    if include_all_rule_relation_types:
        matched_rule_ids = {
            str(rule.rule_id or "").strip()
            for rule in rules or []
            if str(rule.rule_id or "").strip()
        }
    evidence_relation_types = sorted(
        set(
            str(condition.relation_type or "").upper().strip()
            for rule in rules or []
            if str(rule.rule_id or "").strip() in matched_rule_ids
            for condition in rule.conditions or []
            if str(condition.kind or "") == "relation"
            and str(condition.relation_type or "").strip()
        )
    )
    evidence_index = (
        dict(evidence_read_index or {}) if isinstance(evidence_read_index, dict) else {}
    )
    indexed_read = str(evidence_index.get("status") or "") == "verified"
    index_payload = dict(evidence_index.get("index") or {}) if indexed_read else {}
    evidence_plan = native_rule_matched_evidence_storage_plan(
        native_match_result,
        rules,
        evidence_index,
        include_all_rule_relation_types=include_all_rule_relation_types,
    )
    if indexed_read and str(evidence_plan.get("status") or "") == "ok":
        evidence_relation_types = list(evidence_plan.get("relationTypes") or [])
    source_storage_ids_by_source_id = {
        str(source_id or "").strip(): str(storage_id or "").strip()
        for source_id, storage_id in dict(
            index_payload.get("sourceStorageIdsBySourceId") or {}
        ).items()
        if str(source_id or "").strip() and str(storage_id or "").strip()
    }
    source_symbols_by_source_id = {
        str(source_id or "").strip(): str(symbol or "").upper().strip()
        for symbol, values in dict(index_payload.get("sourceIdsBySymbol") or {}).items()
        for source_id in values or []
        if str(source_id or "").strip() and str(symbol or "").strip()
    }
    rows: List[Dict[str, object]] = []
    relation_rows: List[Dict[str, object]] = []
    evidence_read = {
        "status": "ok",
        "mode": "manifest-storage-index" if indexed_read else "legacy-active-membership",
        "source": str(evidence_index.get("source") or "") if indexed_read else "legacy-query",
        "expectedSourceCount": len(source_ids),
        "indexedRelationStorageCount": 0,
        "candidateRelationStorageCount": int(
            evidence_plan.get("candidateRelationStorageCount") or 0
        ),
        "selectedEvidenceStorageCount": int(evidence_plan.get("selectedEvidenceStorageCount") or 0),
        "evidenceNarrowingPct": float(evidence_plan.get("evidenceNarrowingPct") or 0.0),
        "evidenceFallbackConditionCount": int(evidence_plan.get("fallbackConditionCount") or 0),
        "reason": "",
    }
    if indexed_read:
        missing_source_storage_ids = [
            source_id
            for source_id in source_ids
            if source_id not in source_storage_ids_by_source_id
        ]
        if missing_source_storage_ids:
            evidence_read.update(
                {
                    "status": "incomplete",
                    "reason": "Active Manifest evidence index is missing matched stock storage identities.",
                    "missingSourceIds": missing_source_storage_ids,
                }
            )
        elif source_ids:
            try:
                rows = typedb_call_for_world(
                    _store.read_abox_entity_rows_by_storage_ids,
                    [source_storage_ids_by_source_id[source_id] for source_id in source_ids],
                    world_id=world_id,
                )
            except (
                Exception
            ) as error:  # noqa: BLE001 - no partial evidence may drive materialization.
                evidence_read.update(
                    {
                        "status": "error",
                        "reason": "Manifest-indexed source evidence lookup failed: "
                        + str(error)[:180],
                    }
                )
        relation_storage_ids_by_symbol_type = dict(
            index_payload.get("relationStorageIdsBySymbolAndType") or {}
        )
        has_type_index = bool(relation_storage_ids_by_symbol_type)
        if has_type_index and evidence_relation_types:
            relation_storage_ids = list(evidence_plan.get("relationStorageIds") or [])
            evidence_read["relationReadScope"] = str(
                evidence_plan.get("relationReadScope") or "matched-rule-types"
            )
        elif has_type_index:
            relation_storage_ids = []
            evidence_read["relationReadScope"] = "no-relation-conditions"
        else:
            selected_symbols = {
                source_symbols_by_source_id.get(source_id) or symbol_from_subject(source_id)
                for source_id in source_ids
            }
            relation_storage_ids = sorted(
                {
                    str(storage_id or "").strip()
                    for symbol in selected_symbols
                    if str(symbol or "").strip()
                    for storage_id in list(
                        (index_payload.get("relationStorageIdsBySymbol") or {}).get(symbol, [])
                        or []
                    )
                    if str(storage_id or "").strip()
                }
            )
            evidence_read["relationReadScope"] = "legacy-symbol-relations"
        evidence_read["indexedRelationStorageCount"] = len(relation_storage_ids)
        if str(evidence_read.get("status") or "") == "ok" and relation_storage_ids:
            try:
                relation_rows = typedb_call_for_world(
                    _store.read_abox_relation_rows_by_storage_ids,
                    relation_storage_ids,
                    evidence_relation_types,
                    world_id=world_id,
                )
            except (
                Exception
            ) as error:  # noqa: BLE001 - preserve prior inference rather than omit evidence silently.
                evidence_read.update(
                    {
                        "status": "error",
                        "reason": "Manifest-indexed relation evidence lookup failed: "
                        + str(error)[:180],
                    }
                )
    elif source_ids:
        try:
            rows = typedb_call_for_world(
                _store.read_entity_rows_by_ids,
                source_ids,
                ["ABox"],
                world_id=world_id,
            )
        except (
            Exception
        ) as error:  # noqa: BLE001 - preserve legacy behavior but retain diagnostics.
            evidence_read.update(
                {
                    "status": "error",
                    "reason": "Legacy active-membership source lookup failed: " + str(error)[:180],
                }
            )
            rows = []
    if indexed_read and source_ids:
        loaded_source_ids = {
            str(row.get("id") or "").strip() for row in rows if str(row.get("id") or "").strip()
        }
        recovery_source_ids = sorted(set(source_ids) - loaded_source_ids)
        if recovery_source_ids:
            # The Manifest marker can be from a rolling deployment where
            # a target-only graph overwrote a self-consistent but partial
            # index. Recover only the matched active subjects instead of
            # invalidating the whole inference generation.
            recovery_error = ""
            try:
                recovery_rows = typedb_call_for_world(
                    _store.read_entity_rows_by_ids,
                    recovery_source_ids,
                    ["ABox"],
                    world_id=world_id,
                )
                rows = list(
                    {
                        str(row.get("id") or ""): row
                        for row in [*rows, *recovery_rows]
                        if str(row.get("id") or "").strip()
                    }.values()
                )
                recovery_relations = typedb_call_for_world(
                    _store.read_relation_rows_by_source_ids,
                    source_ids,
                    ["ABox"],
                    evidence_relation_types,
                    include_incoming=include_incoming_relations,
                    world_id=world_id,
                )
                relation_rows = list(
                    {
                        str(row.get("id") or ""): row
                        for row in [*relation_rows, *recovery_relations]
                        if str(row.get("id") or "").strip()
                    }.values()
                )
            except (
                Exception
            ) as error:  # noqa: BLE001 - retain an explicit incomplete status on recovery failure.
                recovery_error = str(error)[:180]
            loaded_after_recovery = {
                str(row.get("id") or "").strip() for row in rows if str(row.get("id") or "").strip()
            }
            remaining = sorted(set(source_ids) - loaded_after_recovery)
            if not recovery_error and not remaining:
                evidence_read.update(
                    {
                        "status": "ok",
                        "mode": "manifest-storage-index-with-active-source-recovery",
                        "source": "active-manifest+active-membership-recovery",
                        "reason": "",
                        "fallbackSourceIds": recovery_source_ids,
                        "relationReadScope": "active-membership-recovery",
                    }
                )
            else:
                evidence_read.update(
                    {
                        "status": "incomplete" if not recovery_error else "error",
                        "reason": (
                            "Active-membership recovery did not return every matched stock."
                            if not recovery_error
                            else "Active-membership recovery failed: " + recovery_error
                        ),
                        "missingSourceIds": remaining,
                        "fallbackSourceIds": recovery_source_ids,
                    }
                )
    rows_by_id = {str(row.get("id") or ""): row for row in rows}
    graph = PortfolioOntology("typedb-native-match-model")
    graph.worldview["worldId"] = str(world_id or "")
    graph.worldview["nativeEvidenceRead"] = evidence_read
    for match in matches:
        source_id = str(match.get("sourceId") or "").strip()
        if not source_id or any(item.entity_id == source_id for item in graph.entities):
            continue
        row = rows_by_id.get(source_id)
        if row:
            properties = json_object(row.get("propertiesJson"))
            properties.setdefault("ontologyBox", row.get("ontologyBox") or "ABox")
            properties.setdefault("symbol", row.get("symbol") or symbol_from_subject(source_id))
            properties.setdefault("tboxClass", row.get("tboxClass") or "")
            graph.entities.append(
                OntologyEntity(
                    source_id,
                    str(row.get("label") or source_id),
                    str(row.get("kind") or "stock"),
                    properties,
                )
            )
            continue
        graph.entities.append(
            OntologyEntity(
                source_id,
                str(match.get("sourceLabel") or symbol_from_subject(source_id) or source_id),
                "stock",
                {
                    "ontologyBox": "ABox",
                    "symbol": symbol_from_subject(source_id),
                    "source": "unknown",
                    "queryFallback": True,
                },
            )
        )
    if not indexed_read and source_ids:
        try:
            relation_rows = typedb_call_for_world(
                _store.read_relation_rows_by_source_ids,
                source_ids,
                ["ABox"],
                evidence_relation_types,
                include_incoming=include_incoming_relations,
                world_id=world_id,
            )
        except (
            Exception
        ) as error:  # noqa: BLE001 - do not silently materialize a partial legacy evidence graph.
            graph.worldview["nativeEvidenceRead"] = {
                **dict(graph.worldview.get("nativeEvidenceRead") or {}),
                "status": "error",
                "reason": "Legacy active-membership relation lookup failed: " + str(error)[:180],
            }
            relation_rows = []
    if indexed_read:
        source_entity_ids = {str(row.get("id") or "") for row in rows}
        missing_loaded_sources = [
            source_id for source_id in source_ids if source_id not in source_entity_ids
        ]
        if missing_loaded_sources:
            graph.worldview["nativeEvidenceRead"] = {
                **dict(graph.worldview.get("nativeEvidenceRead") or {}),
                "status": "incomplete",
                "reason": "Manifest-indexed source evidence did not return every matched stock.",
                "missingSourceIds": missing_loaded_sources,
            }
    graph.worldview["nativeEvidenceRead"]["loadedSourceCount"] = len(rows_by_id)
    graph.worldview["nativeEvidenceRead"]["loadedRelationCount"] = len(relation_rows)
    related_node_rows = {
        str(node.get("id") or ""): node
        for item in relation_rows
        for node in [item.get("sourceNode"), item.get("targetNode")]
        if isinstance(node, dict)
        and str(node.get("id") or "").strip()
        and str(node.get("id") or "").strip() not in source_ids
    }
    existing_entity_ids = {item.entity_id for item in graph.entities}
    for row in related_node_rows.values():
        entity_id_value = str(row.get("id") or "").strip()
        if not entity_id_value or entity_id_value in existing_entity_ids:
            continue
        properties = json_object(row.get("propertiesJson"))
        properties.setdefault("ontologyBox", row.get("ontologyBox") or "ABox")
        properties.setdefault("symbol", row.get("symbol") or symbol_from_subject(entity_id_value))
        properties.setdefault("tboxClass", row.get("tboxClass") or "")
        graph.entities.append(
            OntologyEntity(
                entity_id_value,
                str(row.get("label") or entity_id_value),
                str(row.get("kind") or "observation"),
                properties,
            )
        )
        existing_entity_ids.add(entity_id_value)
    for row in relation_rows:
        properties = json_object(row.get("propertiesJson"))
        properties.setdefault("ontologyBox", row.get("ontologyBox") or "ABox")
        properties["_relationId"] = str(row.get("id") or "")
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
