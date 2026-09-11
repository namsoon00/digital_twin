"""Pure Manifest topology and exact evidence-index planning."""

from digital_twin.domain.ontology_contracts import PortfolioOntology
from digital_twin.domain.ontology_native_rule_planning import normalize_native_rule_planner_topology
from digital_twin.domain.ontology_rulebox_contracts import GraphInferenceRule
from digital_twin.domain.ontology_worlds import KNOWLEDGE_WORLD_TYPE
from digital_twin.domain.ontology_worlds import MARKET_WORLD_TYPE
from digital_twin.modules.reasoning.infrastructure.abox_candidates.identity import ontology_storage_id
from digital_twin.modules.reasoning.infrastructure.abox_candidates.identity import relation_row_id
from digital_twin.modules.reasoning.infrastructure.backend_constants import NATIVE_RULE_EVIDENCE_READ_INDEX_LEGACY_VERSION
from digital_twin.modules.reasoning.infrastructure.backend_constants import NATIVE_RULE_EVIDENCE_READ_INDEX_TYPED_VERSION
from digital_twin.modules.reasoning.infrastructure.inference_publication.values import json_object
from digital_twin.modules.reasoning.infrastructure.typeql.constants import NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import clean_symbols_from_payload
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import normalized_condition_role
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import symbol_from_subject
from typing import Dict
from typing import Iterable
from typing import List
import hashlib
import json


def native_rule_manifest_index_required(worldview: Dict[str, object] = None) -> bool:
    """Require physical rule indexes only in worlds that execute RuleBox rules.

    MarketWorld and KnowledgeWorld are durable source-of-fact projections.
    They are read while SharedPremiseWorld is assembled, but native investment
    rules never execute against their manifests directly. Requiring planner
    topology there made every otherwise valid source projection fail closed.
    """

    values = dict(worldview or {})
    world_type = str(values.get("worldType") or values.get("world_type") or "").strip().lower()
    world_id = str(values.get("worldId") or values.get("world_id") or "").strip().lower()
    if world_type in {MARKET_WORLD_TYPE, KNOWLEDGE_WORLD_TYPE, "marketworld", "knowledgeworld"}:
        return False
    if world_id.startswith("market:") or world_id.startswith("knowledge:"):
        return False
    return True

def typedb_native_rule_planner_topology_for_execution(
    active_abox_metadata: Dict[str, object] = None,
    supplied_topology: Dict[str, object] = None,
    target_symbols: Iterable[str] = None,
) -> Dict[str, object]:
    """Use only the active manifest's structural index for rule scheduling.

    A caller-provided topology is accepted only when it has the exact same
    fingerprint as the topology persisted on the active ABox manifest. This
    preserves TypeDB as the rule evaluator while avoiding a costly TypeQL
    topology scan before every direct TypeQL rule query.
    """
    active = dict(active_abox_metadata or {})
    # Keep the immutable, full topology payload intact for the direct-query
    # matcher.  ``normalize_native_rule_planner_topology(..., target_symbols)``
    # returns a selected view whose fingerprint still refers to the full
    # payload; passing that selected view back into the matcher makes its
    # integrity check fail and forces an expensive TypeQL fallback scan.
    stored_full = normalize_native_rule_planner_topology(active.get("nativeRulePlannerTopology"))
    if str(stored_full.get("status") or "") != "ok":
        return {
            "status": "fallback",
            "source": "typedb-active-abox-read",
            "reason": str(stored_full.get("reason") or "Active ABox manifest has no verified planner topology."),
            "topology": {},
            "relationTypesBySymbol": {},
            "sourceIdsBySymbol": {},
            "subjectPropertiesBySymbol": {},
            "relationEvidenceBySymbol": {},
            "relationEvidenceCompleteBySymbol": {},
        }
    requested_symbols = clean_symbols_from_payload(target_symbols or [])
    missing_requested_symbols = [
        symbol
        for symbol in requested_symbols
        if not list((stored_full.get("sourceIdsBySymbol") or {}).get(symbol) or [])
    ]
    if missing_requested_symbols:
        # A rolling deployment can encounter an older partial marker whose
        # self-consistent topology lacks unrelated active symbols. Fall back
        # to the bounded active-membership topology read for this target rather
        # than treating a missing index entry as evidence that no rules apply.
        return {
            "status": "fallback",
            "source": "typedb-active-abox-read",
            "reason": "Active ABox planner topology does not cover requested symbols.",
            "missingSymbols": missing_requested_symbols,
            "topology": {},
            "relationTypesBySymbol": {},
            "sourceIdsBySymbol": {},
            "subjectPropertiesBySymbol": {},
            "relationEvidenceBySymbol": {},
            "relationEvidenceCompleteBySymbol": {},
        }
    stored = normalize_native_rule_planner_topology(
        active.get("nativeRulePlannerTopology"),
        target_symbols=target_symbols,
    )
    supplied = normalize_native_rule_planner_topology(
        supplied_topology,
    ) if supplied_topology else {}
    supplied_status = str(supplied.get("status") or "")
    supplied_matches = (
        supplied_status == "ok"
        and str(supplied.get("fingerprint") or "") == str(stored_full.get("fingerprint") or "")
    )
    execution_topology = {
        key: stored_full.get(key)
        for key in [
            "version", "complete", "source", "fingerprint",
            "sourceIdsBySymbol", "relationTypesBySymbol",
        ]
    }
    if bool(stored_full.get("subjectPropertyIndexAvailable")):
        execution_topology["subjectPropertiesBySymbol"] = dict(
            stored_full.get("subjectPropertiesBySymbol") or {}
        )
    if bool(stored_full.get("relationEvidenceIndexAvailable")):
        execution_topology["relationEvidenceBySymbol"] = dict(
            stored_full.get("relationEvidenceBySymbol") or {}
        )
        execution_topology["relationEvidenceCompleteBySymbol"] = dict(
            stored_full.get("relationEvidenceCompleteBySymbol") or {}
        )
    return {
        "status": "verified",
        "source": "projection-payload-verified" if supplied_matches else "active-manifest",
        "reason": (
            ""
            if not supplied_topology or supplied_matches
            else "Caller planner topology did not match the active ABox manifest; active manifest topology was used."
        ),
        "fingerprint": str(stored_full.get("fingerprint") or ""),
        "topology": execution_topology,
        "relationTypesBySymbol": dict(stored.get("relationTypesBySymbol") or {}),
        "sourceIdsBySymbol": dict(stored.get("sourceIdsBySymbol") or {}),
        "subjectPropertiesBySymbol": dict(stored.get("subjectPropertiesBySymbol") or {}),
        "subjectPropertyIndexAvailable": bool(stored.get("subjectPropertyIndexAvailable")),
        "relationEvidenceBySymbol": dict(stored.get("relationEvidenceBySymbol") or {}),
        "relationEvidenceCompleteBySymbol": dict(
            stored.get("relationEvidenceCompleteBySymbol") or {}
        ),
        "relationEvidenceIndexAvailable": bool(stored.get("relationEvidenceIndexAvailable")),
        "symbols": list(stored.get("symbols") or []),
    }

def native_rule_evidence_read_index_from_rows(
    node_rows: Iterable[Dict[str, object]],
    relation_rows: Iterable[Dict[str, object]],
) -> Dict[str, object]:
    """Build an exact physical-read index for active RuleBox source evidence.

    Scoped ABox facts are immutable and can be shared by several historical
    Manifests.  The index records the physical storage identities selected by
    the candidate Manifest; it does not evaluate a condition or derive an
    investment judgement.  Native TypeDB functions remain the only rule
    evaluator.  The index exists solely so post-function materialization can
    retrieve the same evidence without a costly active-scope pointer join.
    """
    subjects_by_id: Dict[str, str] = {}
    nodes_by_id: Dict[str, Dict[str, object]] = {}
    source_storage_ids_by_source_id: Dict[str, str] = {}
    source_ids_by_symbol: Dict[str, List[str]] = {}
    for row in node_rows or []:
        if str(row.get("ontologyBox") or "ABox") != "ABox":
            continue
        node_id = str(row.get("id") or "").strip()
        if node_id:
            nodes_by_id[node_id] = row
        # BTC/ETH use the same native RuleBox execution path as stocks but
        # are materialized as crypto-asset sources. Do not make their direct
        # market rules fall back to an unbounded TypeDB topology scan.
        kind = str(row.get("kind") or "")
        if kind not in {"stock", "crypto-asset", "portfolio"}:
            continue
        source_id = str(row.get("id") or "").strip()
        symbol = str(
            row.get("symbol")
            or (source_id if kind == "portfolio" else "")
        ).upper().strip()
        if not source_id or not symbol:
            continue
        subjects_by_id[source_id] = symbol
        source_storage_ids_by_source_id[source_id] = ontology_storage_id(row, source_id, "node")
        source_ids_by_symbol.setdefault(symbol, []).append(source_id)

    relation_storage_ids_by_symbol: Dict[str, set] = {
        symbol: set()
        for symbol in source_ids_by_symbol
    }
    relation_storage_ids_by_symbol_and_type: Dict[str, Dict[str, set]] = {
        symbol: {}
        for symbol in source_ids_by_symbol
    }
    relation_storage_ids_by_symbol_type_field: Dict[str, Dict[str, Dict[str, set]]] = {
        symbol: {}
        for symbol in source_ids_by_symbol
    }
    relation_storage_ids_by_symbol_type_target_kind: Dict[str, Dict[str, Dict[str, set]]] = {
        symbol: {}
        for symbol in source_ids_by_symbol
    }
    for row in relation_rows or []:
        if str(row.get("ontologyBox") or "ABox") != "ABox":
            continue
        source_id = str(row.get("source") or "").strip()
        target_id = str(row.get("target") or "").strip()
        source_symbol = subjects_by_id.get(source_id, "")
        target_symbol = subjects_by_id.get(target_id, "")
        candidates = {
            source_symbol,
            target_symbol,
            str(row.get("symbol") or "").upper().strip(),
        }
        relation_storage_id = ontology_storage_id(row, relation_row_id(row), "relation")
        relation_type = str(row.get("type") or row.get("relationType") or "").upper().strip()
        for symbol in candidates:
            if symbol and symbol in relation_storage_ids_by_symbol:
                evidence_node_id = target_id
                if target_symbol == symbol and source_symbol != symbol:
                    evidence_node_id = source_id
                evidence_node = dict(nodes_by_id.get(evidence_node_id) or {})
                evidence_properties = json_object(evidence_node.get("propertiesJson"))
                relation_properties = json_object(row.get("propertiesJson"))
                evidence_field = str(
                    evidence_node.get("field")
                    or evidence_properties.get("field")
                    or row.get("field")
                    or relation_properties.get("field")
                    or ""
                ).strip()
                evidence_target_kind = str(
                    evidence_node.get("kind")
                    or evidence_properties.get("kind")
                    or ""
                ).strip()
                relation_storage_ids_by_symbol[symbol].add(relation_storage_id)
                if relation_type:
                    relation_storage_ids_by_symbol_and_type[symbol].setdefault(relation_type, set()).add(
                        relation_storage_id
                    )
                    if evidence_field:
                        relation_storage_ids_by_symbol_type_field[symbol].setdefault(
                            relation_type, {}
                        ).setdefault(evidence_field, set()).add(relation_storage_id)
                    if evidence_target_kind:
                        relation_storage_ids_by_symbol_type_target_kind[symbol].setdefault(
                            relation_type, {}
                        ).setdefault(evidence_target_kind, set()).add(relation_storage_id)

    payload = {
        "version": NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION,
        "complete": True,
        "source": "projection-persistence-rows",
        "sourceIdsBySymbol": {
            symbol: sorted(set(source_ids_by_symbol.get(symbol) or []))
            for symbol in sorted(source_ids_by_symbol)
        },
        "sourceStorageIdsBySourceId": {
            source_id: source_storage_ids_by_source_id[source_id]
            for source_id in sorted(source_storage_ids_by_source_id)
        },
        "relationStorageIdsBySymbol": {
            symbol: sorted(relation_storage_ids_by_symbol.get(symbol) or set())
            for symbol in sorted(source_ids_by_symbol)
        },
        "relationStorageIdsBySymbolAndType": {
            symbol: {
                relation_type: sorted(storage_ids)
                for relation_type, storage_ids in sorted(
                    relation_storage_ids_by_symbol_and_type.get(symbol, {}).items()
                )
            }
            for symbol in sorted(source_ids_by_symbol)
        },
        "relationStorageIdsBySymbolAndTypeAndField": {
            symbol: {
                relation_type: {
                    field: sorted(storage_ids)
                    for field, storage_ids in sorted(fields.items())
                }
                for relation_type, fields in sorted(
                    relation_storage_ids_by_symbol_type_field.get(symbol, {}).items()
                )
            }
            for symbol in sorted(source_ids_by_symbol)
        },
        "relationStorageIdsBySymbolAndTypeAndTargetKind": {
            symbol: {
                relation_type: {
                    target_kind: sorted(storage_ids)
                    for target_kind, storage_ids in sorted(target_kinds.items())
                }
                for relation_type, target_kinds in sorted(
                    relation_storage_ids_by_symbol_type_target_kind.get(symbol, {}).items()
                )
            }
            for symbol in sorted(source_ids_by_symbol)
        },
    }
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return {
        **payload,
        "fingerprint": "native-rule-evidence-index:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24],
    }

def native_rule_evidence_read_index_from_components(
    source_ids_by_symbol: Dict[str, Iterable[str]],
    source_storage_ids_by_source_id: Dict[str, object],
    relation_storage_ids_by_symbol: Dict[str, Iterable[str]],
    relation_storage_ids_by_symbol_and_type: Dict[str, Dict[str, Iterable[str]]] = None,
    relation_storage_ids_by_symbol_type_field: Dict[str, Dict[str, Dict[str, Iterable[str]]]] = None,
    relation_storage_ids_by_symbol_type_target_kind: Dict[str, Dict[str, Dict[str, Iterable[str]]]] = None,
) -> Dict[str, object]:
    """Build the canonical persisted evidence index from verified components."""
    sources = {
        str(symbol or "").upper().strip(): sorted({
            str(source_id or "").strip()
            for source_id in source_ids or []
            if str(source_id or "").strip()
        })
        for symbol, source_ids in dict(source_ids_by_symbol or {}).items()
        if str(symbol or "").strip()
    }
    sources = {
        symbol: source_ids
        for symbol, source_ids in sorted(sources.items())
        if source_ids
    }
    storage_ids = {
        source_id: str(source_storage_ids_by_source_id.get(source_id) or "").strip()
        for source_id in sorted({
            source_id
            for source_ids in sources.values()
            for source_id in source_ids
        })
        if str(source_storage_ids_by_source_id.get(source_id) or "").strip()
    }
    relation_ids = {
        symbol: sorted({
            str(storage_id or "").strip()
            for storage_id in list((relation_storage_ids_by_symbol or {}).get(symbol) or [])
            if str(storage_id or "").strip()
        })
        for symbol in sources
    }
    typed_relation_ids = {
        symbol: {
            str(relation_type or "").upper().strip(): sorted({
                str(storage_id or "").strip()
                for storage_id in storage_ids or []
                if str(storage_id or "").strip()
            })
            for relation_type, storage_ids in sorted(
                dict((relation_storage_ids_by_symbol_and_type or {}).get(symbol) or {}).items()
            )
            if str(relation_type or "").strip()
        }
        for symbol in sources
    }
    def normalized_selector_index(values: Dict[str, object]) -> Dict[str, object]:
        return {
            symbol: {
                str(relation_type or "").upper().strip(): {
                    str(selector or "").strip(): sorted({
                        str(storage_id or "").strip()
                        for storage_id in storage_ids or []
                        if str(storage_id or "").strip()
                    })
                    for selector, storage_ids in sorted(dict(selectors or {}).items())
                    if str(selector or "").strip()
                }
                for relation_type, selectors in sorted(
                    dict((values or {}).get(symbol) or {}).items()
                )
                if str(relation_type or "").strip()
            }
            for symbol in sources
        }

    field_relation_ids = normalized_selector_index(
        relation_storage_ids_by_symbol_type_field or {}
    )
    target_kind_relation_ids = normalized_selector_index(
        relation_storage_ids_by_symbol_type_target_kind or {}
    )
    payload = {
        "version": NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION,
        "complete": True,
        "source": "projection-persistence-rows",
        "sourceIdsBySymbol": sources,
        "sourceStorageIdsBySourceId": storage_ids,
        "relationStorageIdsBySymbol": relation_ids,
        "relationStorageIdsBySymbolAndType": typed_relation_ids,
        "relationStorageIdsBySymbolAndTypeAndField": field_relation_ids,
        "relationStorageIdsBySymbolAndTypeAndTargetKind": target_kind_relation_ids,
    }
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return {
        **payload,
        "fingerprint": "native-rule-evidence-index:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24],
    }

def merge_native_rule_evidence_read_index(
    active_index: Dict[str, object],
    active_topology: Dict[str, object],
    incoming_index: Dict[str, object],
    incoming_topology: Dict[str, object],
    merged_topology: Dict[str, object],
    replacement_symbols: Iterable[object] = None,
    incoming_index_is_candidate_subset: bool = False,
) -> Dict[str, object]:
    """Merge target-scoped physical identities into an active Manifest index.

    The active and incoming indexes are each verified against their own
    topology before their per-symbol rows are combined.  This avoids replacing
    the active Manifest's unrelated source identities with the target-only
    graph that happened to trigger the current projection.
    """
    active_topology_normalized = normalize_native_rule_planner_topology(active_topology)
    incoming_topology_normalized = normalize_native_rule_planner_topology(incoming_topology)
    merged_topology_normalized = normalize_native_rule_planner_topology(merged_topology)
    if str(active_topology_normalized.get("status") or "") != "ok":
        return {"status": "active-topology-unavailable", "reason": str(active_topology_normalized.get("reason") or "")}
    if str(incoming_topology_normalized.get("status") or "") != "ok":
        return {"status": "incoming-topology-invalid", "reason": str(incoming_topology_normalized.get("reason") or "")}
    if str(merged_topology_normalized.get("status") or "") != "ok":
        return {"status": "merged-topology-invalid", "reason": str(merged_topology_normalized.get("reason") or "")}
    active = normalize_native_rule_evidence_read_index(active_index, active_topology_normalized)
    incoming = normalize_native_rule_evidence_read_index(
        incoming_index,
        (
            merged_topology_normalized
            if incoming_index_is_candidate_subset
            else incoming_topology_normalized
        ),
        allow_topology_subset=incoming_index_is_candidate_subset,
    )
    if str(active.get("status") or "") != "ok":
        return {"status": "active-index-unavailable", "reason": str(active.get("reason") or "")}
    if str(incoming.get("status") or "") != "ok":
        return {"status": "incoming-index-invalid", "reason": str(incoming.get("reason") or "")}

    requested = {
        str(symbol or "").upper().strip()
        for symbol in replacement_symbols or []
        if str(symbol or "").strip()
    }
    active_sources = dict(active.get("sourceIdsBySymbol") or {})
    incoming_sources = dict(incoming.get("sourceIdsBySymbol") or {})
    incoming_symbols = set(incoming_sources)
    missing_requested_symbols = sorted({
        symbol
        for symbol in requested
        if symbol in dict(merged_topology_normalized.get("sourceIdsBySymbol") or {})
        and symbol not in incoming_symbols
    })
    if incoming_index_is_candidate_subset and missing_requested_symbols:
        return {
            "status": "incoming-target-source-missing",
            "reason": (
                "Candidate persistence rows do not contain every requested target source."
            ),
            "missingSymbols": missing_requested_symbols,
        }
    # A stock-targeted portfolio projection carries both the changed stock
    # scopes and a freshly computed account aggregate. The caller's requested
    # symbols list names only the stocks, so retaining the old PORTFOLIO key
    # here would make native risk and exposure rules read a prior aggregate
    # even though the new physical rows were persisted successfully.
    incoming_portfolio_symbols = {
        symbol
        for symbol, source_ids in incoming_sources.items()
        if symbol.startswith("PORTFOLIO:")
        or any(str(source_id or "").startswith("portfolio:") for source_id in source_ids or [])
    }
    replacements = {
        symbol
        for symbol in incoming_symbols
        if (
            not requested
            or symbol in requested
            or symbol not in active_sources
            or symbol in incoming_portfolio_symbols
        )
    }
    expected_sources = dict(merged_topology_normalized.get("sourceIdsBySymbol") or {})
    source_ids_by_symbol: Dict[str, Iterable[str]] = {}
    storage_ids_by_source_id: Dict[str, object] = {}
    relation_ids_by_symbol: Dict[str, Iterable[str]] = {}
    typed_relation_ids_by_symbol: Dict[str, Dict[str, Iterable[str]]] = {}
    field_relation_ids_by_symbol: Dict[str, Dict[str, Dict[str, Iterable[str]]]] = {}
    target_kind_relation_ids_by_symbol: Dict[str, Dict[str, Dict[str, Iterable[str]]]] = {}
    for symbol, expected_ids in expected_sources.items():
        selected = incoming if symbol in replacements else active
        actual_ids = sorted({str(item or "").strip() for item in selected.get("sourceIdsBySymbol", {}).get(symbol, []) or [] if str(item or "").strip()})
        expected_ids = sorted({str(item or "").strip() for item in expected_ids or [] if str(item or "").strip()})
        if actual_ids != expected_ids:
            return {
                "status": "source-coverage-mismatch",
                "reason": "Merged evidence index source coverage does not match the merged planner topology.",
                "symbol": symbol,
                "replacedSymbols": sorted(replacements),
            }
        source_ids_by_symbol[symbol] = actual_ids
        selected_storage = dict(selected.get("sourceStorageIdsBySourceId") or {})
        for source_id in actual_ids:
            storage_id = str(selected_storage.get(source_id) or "").strip()
            if not storage_id:
                return {
                    "status": "missing-source-storage-id",
                    "reason": "Merged evidence index is missing a source storage identity.",
                    "symbol": symbol,
                    "sourceId": source_id,
                }
            storage_ids_by_source_id[source_id] = storage_id
        relation_ids_by_symbol[symbol] = list(
            (selected.get("relationStorageIdsBySymbol") or {}).get(symbol) or []
        )
        typed_relation_ids_by_symbol[symbol] = dict(
            (selected.get("relationStorageIdsBySymbolAndType") or {}).get(symbol) or {}
        )
        field_relation_ids_by_symbol[symbol] = dict(
            (selected.get("relationStorageIdsBySymbolAndTypeAndField") or {}).get(symbol) or {}
        )
        target_kind_relation_ids_by_symbol[symbol] = dict(
            (selected.get("relationStorageIdsBySymbolAndTypeAndTargetKind") or {}).get(symbol) or {}
        )
    index = native_rule_evidence_read_index_from_components(
        source_ids_by_symbol,
        storage_ids_by_source_id,
        relation_ids_by_symbol,
        typed_relation_ids_by_symbol,
        field_relation_ids_by_symbol,
        target_kind_relation_ids_by_symbol,
    )
    verified = normalize_native_rule_evidence_read_index(index, merged_topology_normalized)
    if str(verified.get("status") or "") != "ok":
        return {
            "status": "merged-index-invalid",
            "reason": str(verified.get("reason") or "Merged evidence index is invalid."),
        }
    return {
        "status": "ok",
        "reason": "",
        "index": index,
        "replacedSymbols": sorted(replacements),
        "mergedSymbolCount": len(source_ids_by_symbol),
    }

def normalize_native_rule_evidence_read_index(
    value: Dict[str, object] = None,
    planner_topology: Dict[str, object] = None,
    target_symbols: Iterable[str] = None,
    allow_topology_subset: bool = False,
) -> Dict[str, object]:
    """Validate a Manifest's exact evidence-read index before using it.

    The index is accepted only when its fingerprint is self-consistent and its
    stock subjects exactly match the verified structural planner topology on
    the same active Manifest.  A stale or tampered index therefore fails
    closed instead of reading a historical ABox relation as current evidence.
    """
    raw = dict(value or {}) if isinstance(value, dict) else {}
    index_version = str(raw.get("version") or "")
    if index_version not in {
        NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION,
        NATIVE_RULE_EVIDENCE_READ_INDEX_TYPED_VERSION,
        NATIVE_RULE_EVIDENCE_READ_INDEX_LEGACY_VERSION,
    }:
        return {"status": "invalid", "reason": "Native rule evidence read index version is unsupported."}
    if raw.get("complete") is not True or str(raw.get("source") or "") != "projection-persistence-rows":
        return {"status": "invalid", "reason": "Native rule evidence read index is not a complete projection persistence index."}
    raw_sources = raw.get("sourceIdsBySymbol") if isinstance(raw.get("sourceIdsBySymbol"), dict) else {}
    raw_storage_ids = raw.get("sourceStorageIdsBySourceId") if isinstance(raw.get("sourceStorageIdsBySourceId"), dict) else {}
    raw_relation_ids = raw.get("relationStorageIdsBySymbol") if isinstance(raw.get("relationStorageIdsBySymbol"), dict) else {}
    raw_relation_ids_by_type = (
        raw.get("relationStorageIdsBySymbolAndType")
        if isinstance(raw.get("relationStorageIdsBySymbolAndType"), dict)
        else {}
    )
    raw_relation_ids_by_type_field = (
        raw.get("relationStorageIdsBySymbolAndTypeAndField")
        if isinstance(raw.get("relationStorageIdsBySymbolAndTypeAndField"), dict)
        else {}
    )
    raw_relation_ids_by_type_target_kind = (
        raw.get("relationStorageIdsBySymbolAndTypeAndTargetKind")
        if isinstance(raw.get("relationStorageIdsBySymbolAndTypeAndTargetKind"), dict)
        else {}
    )
    source_ids_by_symbol: Dict[str, List[str]] = {}
    source_storage_ids_by_source_id: Dict[str, str] = {}
    relation_storage_ids_by_symbol: Dict[str, List[str]] = {}
    relation_storage_ids_by_symbol_and_type: Dict[str, Dict[str, List[str]]] = {}
    relation_storage_ids_by_symbol_type_field: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
    relation_storage_ids_by_symbol_type_target_kind: Dict[str, Dict[str, Dict[str, List[str]]]] = {}

    def normalized_selector_index(
        raw_values: Dict[str, object],
        symbol: str,
    ) -> Dict[str, Dict[str, List[str]]]:
        result: Dict[str, Dict[str, List[str]]] = {}
        for raw_relation_type, raw_selectors in dict(raw_values.get(symbol) or {}).items():
            relation_type = str(raw_relation_type or "").upper().strip()
            selectors = {
                str(selector or "").strip(): sorted({
                    str(storage_id or "").strip()
                    for storage_id in storage_ids or []
                    if str(storage_id or "").strip()
                })
                for selector, storage_ids in dict(raw_selectors or {}).items()
                if str(selector or "").strip()
            }
            if relation_type and selectors:
                result[relation_type] = selectors
        return result
    for raw_symbol, raw_ids in raw_sources.items():
        symbol = str(raw_symbol or "").upper().strip()
        ids = sorted({str(item or "").strip() for item in raw_ids or [] if str(item or "").strip()})
        if symbol and ids:
            source_ids_by_symbol[symbol] = ids
    for source_id in sorted({item for values in source_ids_by_symbol.values() for item in values}):
        storage_id = str(raw_storage_ids.get(source_id) or "").strip()
        if not storage_id:
            return {"status": "invalid", "reason": "Native rule evidence read index is missing a stock storage identity."}
        source_storage_ids_by_source_id[source_id] = storage_id
    for symbol in source_ids_by_symbol:
        relation_storage_ids_by_symbol[symbol] = sorted({
            str(item or "").strip()
            for item in raw_relation_ids.get(symbol, []) or []
            if str(item or "").strip()
        })
        typed_relation_ids: Dict[str, List[str]] = {}
        for raw_relation_type, raw_storage_ids in dict(raw_relation_ids_by_type.get(symbol) or {}).items():
            relation_type = str(raw_relation_type or "").upper().strip()
            storage_ids = sorted({
                str(item or "").strip()
                for item in raw_storage_ids or []
                if str(item or "").strip()
            })
            if relation_type and storage_ids:
                typed_relation_ids[relation_type] = storage_ids
        relation_storage_ids_by_symbol_and_type[symbol] = typed_relation_ids
        relation_storage_ids_by_symbol_type_field[symbol] = normalized_selector_index(
            raw_relation_ids_by_type_field,
            symbol,
        )
        relation_storage_ids_by_symbol_type_target_kind[symbol] = normalized_selector_index(
            raw_relation_ids_by_type_target_kind,
            symbol,
        )
    payload = {
        "version": index_version,
        "complete": True,
        "source": "projection-persistence-rows",
        "sourceIdsBySymbol": {
            symbol: source_ids_by_symbol[symbol]
            for symbol in sorted(source_ids_by_symbol)
        },
        "sourceStorageIdsBySourceId": {
            source_id: source_storage_ids_by_source_id[source_id]
            for source_id in sorted(source_storage_ids_by_source_id)
        },
        "relationStorageIdsBySymbol": {
            symbol: relation_storage_ids_by_symbol[symbol]
            for symbol in sorted(source_ids_by_symbol)
        },
    }
    if index_version in {
        NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION,
        NATIVE_RULE_EVIDENCE_READ_INDEX_TYPED_VERSION,
    }:
        payload["relationStorageIdsBySymbolAndType"] = {
            symbol: relation_storage_ids_by_symbol_and_type[symbol]
            for symbol in sorted(source_ids_by_symbol)
        }
    if index_version == NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION:
        payload["relationStorageIdsBySymbolAndTypeAndField"] = {
            symbol: relation_storage_ids_by_symbol_type_field[symbol]
            for symbol in sorted(source_ids_by_symbol)
        }
        payload["relationStorageIdsBySymbolAndTypeAndTargetKind"] = {
            symbol: relation_storage_ids_by_symbol_type_target_kind[symbol]
            for symbol in sorted(source_ids_by_symbol)
        }
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    fingerprint = "native-rule-evidence-index:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    if str(raw.get("fingerprint") or "") != fingerprint:
        return {"status": "invalid", "reason": "Native rule evidence read index fingerprint does not match its contents."}
    topology = normalize_native_rule_planner_topology(planner_topology)
    if str(topology.get("status") or "") != "ok":
        return {"status": "invalid", "reason": "Native rule evidence read index has no verified planner topology."}
    expected_sources = {
        symbol: sorted({str(item or "").strip() for item in values or [] if str(item or "").strip()})
        for symbol, values in dict(topology.get("sourceIdsBySymbol") or {}).items()
    }
    if allow_topology_subset:
        unexpected_symbols = sorted(
            set(payload["sourceIdsBySymbol"]) - set(expected_sources)
        )
        if unexpected_symbols:
            return {
                "status": "invalid",
                "reason": "Native rule evidence read index contains subjects outside the candidate planner topology.",
                "unexpectedSymbols": unexpected_symbols,
            }
        expected_sources = {
            symbol: expected_sources[symbol]
            for symbol in payload["sourceIdsBySymbol"]
        }
    if expected_sources != payload["sourceIdsBySymbol"]:
        return {
            "status": "invalid",
            "reason": "Native rule evidence read index stock subjects do not match the active planner topology.",
            "expectedSymbols": sorted(expected_sources),
            "actualSymbols": sorted(payload["sourceIdsBySymbol"]),
            "mismatchedSymbols": sorted({
                symbol
                for symbol in set(expected_sources).union(payload["sourceIdsBySymbol"])
                if expected_sources.get(symbol) != payload["sourceIdsBySymbol"].get(symbol)
            }),
        }
    requested_symbols = clean_symbols_from_payload(target_symbols or [])
    # Portfolio work items still carry their affected stock symbols so the
    # downstream explanation can remain target-scoped. Portfolio RuleBox
    # sources are aggregate subjects, however, and would disappear from this
    # execution view if it retained only those stock symbols. Keep aggregate
    # portfolio keys alongside every requested symbol; this is an identity
    # projection only and does not decide whether a portfolio rule matches.
    portfolio_symbols = sorted({
        symbol
        for symbol, source_ids in source_ids_by_symbol.items()
        if symbol.startswith("PORTFOLIO:")
        or any(str(source_id or "").startswith("portfolio:") for source_id in source_ids or [])
    })
    selected_symbols = (
        sorted(set(requested_symbols).union(portfolio_symbols))
        if requested_symbols
        else sorted(source_ids_by_symbol)
    )
    selected_source_ids = {
        source_id
        for symbol in selected_symbols
        for source_id in source_ids_by_symbol.get(symbol, [])
    }
    return {
        "status": "ok",
        **payload,
        "fingerprint": fingerprint,
        "symbols": selected_symbols,
        "sourceStorageIdsBySourceId": {
            source_id: source_storage_ids_by_source_id[source_id]
            for source_id in sorted(selected_source_ids)
        },
        "relationStorageIdsBySymbol": {
            symbol: list(relation_storage_ids_by_symbol.get(symbol, []))
            for symbol in selected_symbols
        },
        "relationStorageIdsBySymbolAndType": {
            symbol: dict(relation_storage_ids_by_symbol_and_type.get(symbol, {}))
            for symbol in selected_symbols
        } if index_version in {
            NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION,
            NATIVE_RULE_EVIDENCE_READ_INDEX_TYPED_VERSION,
        } else {},
        "relationStorageIdsBySymbolAndTypeAndField": {
            symbol: dict(relation_storage_ids_by_symbol_type_field.get(symbol, {}))
            for symbol in selected_symbols
        } if index_version == NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION else {},
        "relationStorageIdsBySymbolAndTypeAndTargetKind": {
            symbol: dict(relation_storage_ids_by_symbol_type_target_kind.get(symbol, {}))
            for symbol in selected_symbols
        } if index_version == NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION else {},
    }

def typedb_native_rule_evidence_read_index_for_execution(
    active_abox_metadata: Dict[str, object] = None,
    target_symbols: Iterable[str] = None,
) -> Dict[str, object]:
    """Return only an active-Manifest verified evidence-read index."""
    active = dict(active_abox_metadata or {})
    topology = normalize_native_rule_planner_topology(active.get("nativeRulePlannerTopology"))
    requested_symbols = clean_symbols_from_payload(target_symbols or [])
    missing_requested_symbols = [
        symbol
        for symbol in requested_symbols
        if not list((topology.get("sourceIdsBySymbol") or {}).get(symbol) or [])
    ] if str(topology.get("status") or "") == "ok" else []
    if missing_requested_symbols:
        return {
            "status": "fallback",
            "source": "typedb-active-abox-membership-recovery",
            "reason": "Active ABox evidence index does not cover requested symbols.",
            "missingSymbols": missing_requested_symbols,
            "index": {},
        }
    normalized = normalize_native_rule_evidence_read_index(
        active.get("nativeRuleEvidenceReadIndex"),
        planner_topology=topology,
        target_symbols=target_symbols,
    )
    if str(normalized.get("status") or "") != "ok":
        return {
            "status": "fallback",
            "source": "typedb-active-abox-manifest",
            "reason": str(normalized.get("reason") or "Active ABox Manifest has no verified evidence read index."),
            "index": {},
        }
    return {
        "status": "verified",
        "source": "active-manifest",
        "reason": "",
        "fingerprint": str(normalized.get("fingerprint") or ""),
        "index": normalized,
    }

def typedb_native_rule_evidence_read_allows_active_membership_recovery(
    evidence_read_index: Dict[str, object] = None,
) -> bool:
    """Allow only the explicit partial-manifest recovery read path.

    A missing target in an otherwise valid historical Manifest topology is a
    known rolling-deployment condition. The active-membership read is bounded
    to that target and the active ABox pointer, so it is safe to use for the
    explanation graph. Other invalid indexes remain fail-closed.
    """
    value = dict(evidence_read_index or {}) if isinstance(evidence_read_index, dict) else {}
    return (
        str(value.get("status") or "") == "verified"
        or (
            str(value.get("status") or "") == "fallback"
            and str(value.get("source") or "") == "typedb-active-abox-membership-recovery"
        )
    )

def native_rule_matched_evidence_storage_plan(
    native_match_result: Dict[str, object],
    rules: Iterable[GraphInferenceRule] = None,
    evidence_read_index: Dict[str, object] = None,
    include_all_rule_relation_types: bool = False,
) -> Dict[str, object]:
    """Select exact ABox evidence rows for TypeDB-matched rules.

    TypeDB has already decided whether each rule matches. This planner only
    maps the matched rule's authored relation, target-kind, and field
    contracts to immutable Manifest storage IDs. It cannot add a match or
    change an investment action; it prevents explanation materialization from
    rereading every relation of the same broad type for the stock.
    """
    evidence = dict(evidence_read_index or {}) if isinstance(evidence_read_index, dict) else {}
    index = dict(evidence.get("index") or {}) if str(evidence.get("status") or "") == "verified" else {}
    matches = [
        dict(item)
        for item in (native_match_result or {}).get("matches") or []
        if isinstance(item, dict) and str(item.get("sourceId") or "").strip()
    ]
    rules_by_id = {
        str(getattr(rule, "rule_id", "") or (rule.get("rule_id") if isinstance(rule, dict) else "") or "").strip(): rule
        for rule in rules or []
        if str(getattr(rule, "rule_id", "") or (rule.get("rule_id") if isinstance(rule, dict) else "") or "").strip()
    }
    source_symbols_by_source_id = {
        str(source_id or "").strip(): str(symbol or "").upper().strip()
        for symbol, source_ids in dict(index.get("sourceIdsBySymbol") or {}).items()
        for source_id in source_ids or []
        if str(source_id or "").strip() and str(symbol or "").strip()
    }
    by_type = dict(index.get("relationStorageIdsBySymbolAndType") or {})
    by_field = dict(index.get("relationStorageIdsBySymbolAndTypeAndField") or {})
    by_target_kind = dict(
        index.get("relationStorageIdsBySymbolAndTypeAndTargetKind") or {}
    )

    def equality_values(filters: Dict[str, object], key: str) -> List[str]:
        expected = dict(filters or {}).get(key)
        if isinstance(expected, dict):
            operator = str(expected.get("operator") or "==").strip().lower()
            if operator not in {"==", "in", "one-of", "one_of"}:
                return []
            expected = expected.get("value")
        if isinstance(expected, (list, tuple, set)):
            return sorted({str(item or "").strip() for item in expected if str(item or "").strip()})
        value = str(expected or "").strip()
        return [value] if value else []

    selected_storage_ids: set = set()
    candidate_storage_ids: set = set()
    selected_relation_types: set = set()
    exact_condition_count = 0
    fallback_condition_count = 0
    relation_condition_count = 0
    fallback_conditions: List[str] = []
    all_rule_ids = sorted(rules_by_id)
    for match in matches:
        source_id = str(match.get("sourceId") or "").strip()
        symbol = source_symbols_by_source_id.get(source_id) or symbol_from_subject(source_id)
        requested_rule_ids = all_rule_ids if include_all_rule_relation_types else [
            str(match.get("ruleId") or "").strip()
        ]
        for rule_id in requested_rule_ids:
            rule = rules_by_id.get(rule_id)
            if not rule:
                continue
            conditions = (
                list(getattr(rule, "conditions", []) or [])
                if not isinstance(rule, dict)
                else list(rule.get("conditions") or [])
            )
            for raw_condition in conditions:
                condition = (
                    raw_condition.to_dict()
                    if hasattr(raw_condition, "to_dict")
                    else dict(vars(raw_condition))
                    if hasattr(raw_condition, "__dict__")
                    else dict(raw_condition or {})
                )
                if str(condition.get("kind") or "") != "relation":
                    continue
                if normalized_condition_role(condition) == "not":
                    continue
                relation_type = str(
                    condition.get("relation_type")
                    or condition.get("relationType")
                    or ""
                ).upper().strip()
                if not relation_type:
                    continue
                relation_condition_count += 1
                selected_relation_types.add(relation_type)
                base_ids = {
                    str(storage_id or "").strip()
                    for storage_id in dict(by_type.get(symbol) or {}).get(relation_type, []) or []
                    if str(storage_id or "").strip()
                }
                candidate_storage_ids.update(base_ids)
                condition_ids = set(base_ids)
                selector_used = False
                selector_missing = False
                target_filters = dict(
                    condition.get("target_property_filters")
                    or condition.get("targetPropertyFilters")
                    or {}
                )
                relation_filters = dict(
                    condition.get("relation_property_filters")
                    or condition.get("relationPropertyFilters")
                    or {}
                )
                field_values = sorted(set(
                    equality_values(target_filters, "field")
                    + equality_values(relation_filters, "field")
                ))
                if field_values:
                    field_ids = {
                        str(storage_id or "").strip()
                        for field in field_values
                        for storage_id in dict(
                            dict(by_field.get(symbol) or {}).get(relation_type) or {}
                        ).get(field, []) or []
                        if str(storage_id or "").strip()
                    }
                    if field_ids:
                        condition_ids.intersection_update(field_ids)
                        selector_used = True
                    else:
                        selector_missing = True
                target_kind = str(
                    condition.get("target_kind")
                    or condition.get("targetKind")
                    or ""
                ).strip()
                if target_kind:
                    kind_ids = {
                        str(storage_id or "").strip()
                        for storage_id in dict(
                            dict(by_target_kind.get(symbol) or {}).get(relation_type) or {}
                        ).get(target_kind, []) or []
                        if str(storage_id or "").strip()
                    }
                    if kind_ids:
                        condition_ids.intersection_update(kind_ids)
                        selector_used = True
                    else:
                        selector_missing = True
                if selector_missing:
                    # A legacy or partially repaired Manifest may not have
                    # every selector index. Keep any selector that was proved
                    # exact; otherwise fall back to the bounded relation-type
                    # slice for this condition.
                    if not selector_used:
                        condition_ids = set(base_ids)
                    fallback_condition_count += 1
                    fallback_conditions.append(rule_id + ":" + str(condition.get("condition_id") or condition.get("conditionId") or relation_type))
                elif selector_used:
                    exact_condition_count += 1
                else:
                    fallback_condition_count += 1
                selected_storage_ids.update(condition_ids)
    candidate_count = len(candidate_storage_ids)
    selected_count = len(selected_storage_ids)
    return {
        "status": "ok" if index else "not-available",
        "source": "typedb-matched-rule-evidence-contract",
        "relationStorageIds": sorted(selected_storage_ids),
        "relationTypes": sorted(selected_relation_types),
        "candidateRelationStorageCount": candidate_count,
        "selectedEvidenceStorageCount": selected_count,
        "evidenceNarrowingPct": round(
            max(0.0, (1.0 - (selected_count / candidate_count)) * 100.0),
            1,
        ) if candidate_count else 0.0,
        "relationConditionCount": relation_condition_count,
        "exactSelectorConditionCount": exact_condition_count,
        "fallbackConditionCount": fallback_condition_count,
        "fallbackConditions": sorted(set(fallback_conditions))[:40],
        "relationReadScope": (
            "matched-rule-exact-evidence"
            if exact_condition_count and not fallback_condition_count
            else "matched-rule-exact-evidence-with-type-fallback"
            if exact_condition_count
            else "matched-rule-types"
        ),
    }

def typedb_projection_preflight_graph_for_execution(
    projection_graph: object,
    projection_manifest_id: object,
    active_abox_metadata: Dict[str, object] = None,
    planner_topology: Dict[str, object] = None,
    target_symbols: Iterable[str] = None,
    world_id: str = "",
) -> Dict[str, object]:
    """Accept a just-persisted graph as a verified native-rule input surface.

    The graph is valid for negative preflight after its Manifest, World, and
    planner topology match the active TypeDB ABox. A later exact storage-id
    check decides whether the same graph can explain materialized matches;
    any missing physical evidence falls back to the durable TypeDB read.
    """
    if not isinstance(projection_graph, PortfolioOntology):
        return {
            "status": "unavailable",
            "mode": "projection-verified-in-memory",
            "reason": "No projection graph was supplied by the active ABox writer.",
        }
    active = dict(active_abox_metadata or {})
    active_manifest_id = str(
        active.get("worldviewManifestId") or active.get("aboxSnapshotId") or ""
    ).strip()
    supplied_manifest_id = str(projection_manifest_id or "").strip()
    graph_worldview = dict(getattr(projection_graph, "worldview", {}) or {})
    graph_manifest_id = str(
        graph_worldview.get("worldviewManifestId")
        or graph_worldview.get("aboxSnapshotId")
        or ""
    ).strip()
    if (
        not active_manifest_id
        or not supplied_manifest_id
        or supplied_manifest_id != active_manifest_id
        or graph_manifest_id != active_manifest_id
    ):
        return {
            "status": "incomplete",
            "mode": "projection-verified-in-memory",
            "reason": "Projection graph manifest does not exactly match the active TypeDB ABox.",
        }
    requested_world_id = str(world_id or "").strip()
    active_world_id = str(active.get("worldId") or requested_world_id or "").strip()
    graph_world_id = str(graph_worldview.get("worldId") or "").strip()
    if requested_world_id and (graph_world_id != requested_world_id or active_world_id != requested_world_id):
        return {
            "status": "incomplete",
            "mode": "projection-verified-in-memory",
            "reason": "Projection graph world does not match the active TypeDB inference world.",
        }
    if str(graph_worldview.get("runtimeProjectionMode") or "") != "abox-facts-only-typedb-native-rules":
        return {
            "status": "incomplete",
            "mode": "projection-verified-in-memory",
            "reason": "Projection graph is not the verified ABox-native-rule input surface.",
        }
    supplied_topology = normalize_native_rule_planner_topology(
        graph_worldview.get("nativeRulePlannerTopology")
    )
    expected_topology = dict(planner_topology or {})
    if (
        str(expected_topology.get("status") or "") != "verified"
        or str(supplied_topology.get("status") or "") != "ok"
        or str(supplied_topology.get("fingerprint") or "")
        != str(expected_topology.get("fingerprint") or "")
    ):
        return {
            "status": "incomplete",
            "mode": "projection-verified-in-memory",
            "reason": "Projection graph planner topology does not match the active ABox Manifest.",
        }
    clean_symbols = clean_symbols_from_payload(target_symbols)
    expected_source_ids = {
        str(source_id or "").strip()
        for symbol in clean_symbols
        for source_id in dict(expected_topology.get("sourceIdsBySymbol") or {}).get(symbol, []) or []
        if str(source_id or "").strip()
    }
    graph_source_ids = {
        str(entity.entity_id or "").strip()
        for entity in list(getattr(projection_graph, "entities", []) or [])
        if str(entity.entity_id or "").strip()
    }
    loaded_source_ids = expected_source_ids & graph_source_ids
    if not expected_source_ids or not loaded_source_ids:
        return {
            "status": "incomplete",
            "mode": "projection-verified-in-memory",
            "reason": "Projection graph contains none of the active target stock sources.",
            "sourceCount": len(expected_source_ids),
            "loadedSourceCount": len(loaded_source_ids),
        }
    if not expected_source_ids.issubset(graph_source_ids):
        return {
            "status": "partial",
            "mode": "projection-verified-in-memory-partial",
            "reason": (
                "Projection graph contains a verified subset of routed target stocks; "
                "missing subjects remain unknown and cannot be negatively pruned."
            ),
            "sourceCount": len(expected_source_ids),
            "loadedSourceCount": len(loaded_source_ids),
            "missingSourceIds": sorted(expected_source_ids - graph_source_ids)[:40],
            "entityCount": len(list(getattr(projection_graph, "entities", []) or [])),
            "relationCount": len(list(getattr(projection_graph, "relations", []) or [])),
            "graph": projection_graph,
        }
    return {
        "status": "ok",
        "mode": "projection-verified-in-memory",
        "reason": "",
        "sourceCount": len(expected_source_ids),
        "loadedSourceCount": len(expected_source_ids),
        "entityCount": len(list(getattr(projection_graph, "entities", []) or [])),
        "relationCount": len(list(getattr(projection_graph, "relations", []) or [])),
        "graph": projection_graph,
    }
