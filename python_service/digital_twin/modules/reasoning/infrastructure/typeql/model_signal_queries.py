"""Model signal queries for TypeQL, without database execution."""

import re
from typing import Dict, Iterable, List, Set

from digital_twin.domain.model_signal_interpretation import (
    MODEL_SIGNAL_BRIDGE_VERSION,
    is_batchable_model_signal_interpretation_rule,
    is_model_signal_interpretation_rule,
    model_signal_bridge_conditions,
    model_signal_bridge_source_scope,
    model_signal_conditions,
    model_signal_interpretation_contract_id,
    model_signal_residual_conditions,
)
from digital_twin.modules.reasoning.infrastructure.typeql.condition_queries import (
    typedb_condition_pattern,
    typedb_entity_match_type,
)
from digital_twin.modules.reasoning.infrastructure.typeql.constants import NATIVE_RULE_INDEXED_QUERY_MAX_STORAGE_IDS
from digital_twin.modules.reasoning.infrastructure.typeql.literals import (
    typedb_expected_value,
    typedb_string,
    typedb_value_match,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
    symbol_from_subject,
    typedb_planned_candidate_symbols,
    typedb_source_kind_uses_symbol_scope,
)
from digital_twin.modules.reasoning.infrastructure.typeql.scope_clauses import (
    typedb_active_abox_member_clause,
    typedb_active_worldview_manifest_clause,
    typedb_scoped_manifest_member_clause,
)
from digital_twin.modules.reasoning.infrastructure.typeql.storage_schema import typedb_target_attribute


def typedb_model_signal_bridge_batch_plan(
    planned_entries: Iterable[Dict[str, object]],
    target_symbols: Iterable[str] = None,
) -> Dict[str, object]:
    """Collapse simple model-signal policies into source-scope bridge reads.

    The returned batches are an execution-routing plan only. TypeDB still
    proves the active ``HAS_MODEL_SIGNAL`` assertion. Python maps its immutable
    hypothesis contract to the existing governed rule lineage.
    """

    clean_symbols = clean_symbols_from_payload(list(target_symbols or []))
    regular_entries: List[Dict[str, object]] = []
    batchable_by_rule_id: Dict[str, Dict[str, object]] = {}
    constrained_rule_ids: Set[str] = set()
    logical_model_signal_rule_ids: Set[str] = set()
    for raw_entry in planned_entries or []:
        entry = dict(raw_entry or {})
        rule = entry.get("rule")
        if not rule:
            regular_entries.append(entry)
            continue
        rule_payload = rule.to_dict() if hasattr(rule, "to_dict") else dict(rule or {})
        rule_id = str(rule_payload.get("rule_id") or rule_payload.get("ruleId") or "").strip()
        if not is_model_signal_interpretation_rule(rule_payload):
            regular_entries.append(entry)
            continue
        if not bool(rule_payload.get("enabled", True)):
            # The caller normally filters disabled rules before planning. Keep
            # the shared dispatcher independently fail-safe for diagnostics
            # and unit use so a disabled contract can never be routed.
            continue
        logical_model_signal_rule_ids.add(rule_id)
        if not is_batchable_model_signal_interpretation_rule(rule_payload):
            constrained_rule_ids.add(rule_id)
            regular_entries.append(entry)
            continue
        candidate_symbols = typedb_planned_candidate_symbols(entry, clean_symbols)
        existing = batchable_by_rule_id.get(rule_id)
        if existing is None:
            entry["candidateSymbols"] = list(candidate_symbols)
            batchable_by_rule_id[rule_id] = entry
            continue
        existing["candidateSymbols"] = clean_symbols_from_payload(
            list(existing.get("candidateSymbols") or []) + list(candidate_symbols)
        )

    grouped: Dict[str, List[Dict[str, object]]] = {}
    for rule_id in sorted(batchable_by_rule_id):
        entry = batchable_by_rule_id[rule_id]
        rule = entry.get("rule")
        scope = model_signal_bridge_source_scope(rule)
        grouped.setdefault(scope, []).append(entry)
    batches = [
        {
            "sourceScope": scope,
            "entries": entries,
            "ruleIds": [
                str(getattr(entry.get("rule"), "rule_id", "") or "")
                for entry in entries
            ],
            "candidateSymbols": clean_symbols_from_payload([
                symbol
                for entry in entries
                for symbol in entry.get("candidateSymbols") or []
            ]),
        }
        for scope, entries in sorted(
            grouped.items(),
            key=lambda item: item[0],
        )
    ]
    batchable_count = len(batchable_by_rule_id)
    return {
        "status": "ok",
        "logicalModelSignalPolicyCount": len(logical_model_signal_rule_ids),
        "batchedSimplePolicyCount": batchable_count,
        "constrainedPolicyCount": len(constrained_rule_ids),
        "modelSignalBridgeReadCount": len(batches),
        "eliminatedModelSignalPolicyQueryCount": max(0, batchable_count - len(batches)),
        "legacyModelSignalPolicyQueryCount": len(logical_model_signal_rule_ids),
        "plannedModelSignalQueryCount": len(constrained_rule_ids) + len(batches),
        "bridgeSourceScopes": sorted({str(item.get("sourceScope") or "") for item in batches}),
        "batchableRuleIds": sorted(batchable_by_rule_id),
        "constrainedRuleIds": sorted(constrained_rule_ids),
        "batches": batches,
        "regularEntries": regular_entries,
    }


def typedb_model_signal_bridge_batch_plan_summary(
    plan: Dict[str, object],
    *,
    ignored_contract_ids: Iterable[str] = None,
    execution: Dict[str, object] = None,
) -> Dict[str, object]:
    payload = dict(plan or {})
    execution_payload = dict(execution or {})
    return {
        "status": str(
            execution_payload.get("status")
            or payload.get("status")
            or "not-planned"
        ),
        "logicalModelSignalPolicyCount": int(payload.get("logicalModelSignalPolicyCount") or 0),
        "batchedSimplePolicyCount": int(payload.get("batchedSimplePolicyCount") or 0),
        "constrainedPolicyCount": int(payload.get("constrainedPolicyCount") or 0),
        "modelSignalBridgeReadCount": int(payload.get("modelSignalBridgeReadCount") or 0),
        "eliminatedModelSignalPolicyQueryCount": int(
            payload.get("eliminatedModelSignalPolicyQueryCount") or 0
        ),
        "legacyModelSignalPolicyQueryCount": int(
            payload.get("legacyModelSignalPolicyQueryCount") or 0
        ),
        "plannedModelSignalQueryCount": int(payload.get("plannedModelSignalQueryCount") or 0),
        "bridgeSourceScopes": list(payload.get("bridgeSourceScopes") or []),
        "batchableRuleIds": list(payload.get("batchableRuleIds") or []),
        "constrainedRuleIds": list(payload.get("constrainedRuleIds") or []),
        "ignoredContractIds": sorted({
            str(item or "").strip()
            for item in ignored_contract_ids or []
            if str(item or "").strip()
        }),
        "sourceRowCount": int(execution_payload.get("sourceRowCount") or 0),
        "dispatchedMatchCount": int(
            execution_payload.get("dispatchedMatchCount") or 0
        ),
        "matchedContractIds": list(
            execution_payload.get("matchedContractIds") or []
        )[:80],
        "matchedSymbols": list(execution_payload.get("matchedSymbols") or [])[:80],
        "indexedEvidenceReadCount": int(
            execution_payload.get("indexedEvidenceReadCount") or 0
        ),
    }


def typedb_model_signal_bridge_batch_query(
    batch: Dict[str, object],
    *,
    world_id: str = "",
    scoped_manifest_only: bool = True,
    evidence_read_index: Dict[str, object] = None,
) -> Dict[str, object]:
    """Build one TypeDB read for all simple policies in one source scope."""

    entries = [dict(item or {}) for item in batch.get("entries") or []]
    rules = [item.get("rule") for item in entries if item.get("rule")]
    scope = str(batch.get("sourceScope") or "").strip().lower()
    if not rules:
        return {
            "status": "invalid",
            "query": "",
            "reason": "Model-signal bridge batch has no policies.",
        }
    if any(
        not is_batchable_model_signal_interpretation_rule(rule)
        or model_signal_bridge_source_scope(rule) != scope
        for rule in rules
    ):
        return {
            "status": "invalid",
            "query": "",
            "reason": "Model-signal bridge batch mixes incompatible policy shapes.",
        }
    first_rule = rules[0]
    first_payload = first_rule.to_dict() if hasattr(first_rule, "to_dict") else dict(first_rule or {})
    source_kind = str(first_payload.get("source_kind") or first_payload.get("sourceKind") or "stock")
    candidate_symbols = clean_symbols_from_payload(list(batch.get("candidateSymbols") or []))
    verified_index = dict(evidence_read_index or {})
    index = (
        dict(verified_index.get("index") or {})
        if str(verified_index.get("status") or "") == "verified"
        else {}
    )
    source_ids_by_symbol = dict(index.get("sourceIdsBySymbol") or {})
    source_storage_ids_by_source_id = dict(index.get("sourceStorageIdsBySourceId") or {})
    relation_storage_ids_by_symbol_and_type = dict(
        index.get("relationStorageIdsBySymbolAndType") or {}
    )
    source_storage_ids = sorted({
        str(source_storage_ids_by_source_id.get(str(source_id or "")) or "").strip()
        for symbol in candidate_symbols
        for source_id in source_ids_by_symbol.get(symbol, []) or []
        if str(source_storage_ids_by_source_id.get(str(source_id or "")) or "").strip()
    })
    signal_relation_storage_ids = sorted({
        str(storage_id or "").strip()
        for symbol in candidate_symbols
        for storage_id in dict(
            relation_storage_ids_by_symbol_and_type.get(symbol) or {}
        ).get("HAS_MODEL_SIGNAL", []) or []
        if str(storage_id or "").strip()
    })
    storage_identity_count = len({*source_storage_ids, *signal_relation_storage_ids})
    indexed_evidence = bool(
        source_storage_ids
        and signal_relation_storage_ids
        and storage_identity_count <= NATIVE_RULE_INDEXED_QUERY_MAX_STORAGE_IDS
    )
    clauses = [] if indexed_evidence else [
        typedb_active_worldview_manifest_clause(
            "$activeManifestPointer",
            "$activeManifestId",
            world_id,
        ),
    ]
    clauses.extend([
        (
            typedb_value_match(
                "$source",
                "ontology-storage-id",
                source_storage_ids,
                "==",
                "modelSignalSourceStorage",
            )
            if indexed_evidence
            else typedb_scoped_manifest_member_clause(
                "$source",
                "modelSignalSource",
                "$activeManifestId",
                world_id,
            ) if scoped_manifest_only else typedb_active_abox_member_clause(
                "$source",
                "modelSignalSource",
                world_id,
            )
        ),
        "$source isa " + typedb_entity_match_type(source_kind)
        + ", has ontology-kind " + typedb_string(source_kind) + ";",
    ])
    if candidate_symbols and typedb_source_kind_uses_symbol_scope(source_kind):
        clauses.append(typedb_value_match(
            "$source",
            "ontology-symbol",
            candidate_symbols,
            "==",
            "modelSignalSourceSymbol",
        ))
    for index, condition in enumerate(model_signal_bridge_conditions(first_rule)):
        condition_payload = (
            condition.to_dict()
            if hasattr(condition, "to_dict")
            else dict(condition or {})
        )
        pattern = typedb_condition_pattern(
            condition_payload,
            index,
            source_var="$source",
            variable_scope="modelSignalBridge" + str(index) + "_",
        )
        if pattern.get("reason"):
            return {
                "status": "invalid",
                "query": "",
                "reason": str(pattern.get("reason") or "Unsupported bridge condition."),
            }
        clauses.extend(str(item) for item in pattern.get("clauses") or [] if str(item or "").strip())

    signal_condition = model_signal_residual_conditions(first_rule)[0]
    signal_payload = (
        signal_condition.to_dict()
        if hasattr(signal_condition, "to_dict")
        else dict(signal_condition or {})
    )
    signal_payload.pop("target_property_filters", None)
    signal_payload.pop("targetPropertyFilters", None)
    signal_payload.pop("relation_property_filters", None)
    signal_payload.pop("relationPropertyFilters", None)
    signal_pattern = typedb_condition_pattern(
        signal_payload,
        0,
        source_var="$source",
        relation_prefix="modelSignalRelation",
        target_prefix="modelSignalEvidence",
        variable_scope="modelSignalBatch_",
        manifest_id_variable=(
            "$activeManifestId" if scoped_manifest_only and not indexed_evidence else ""
        ),
        world_id=world_id,
        active_relation_storage_ids=(
            signal_relation_storage_ids if indexed_evidence else None
        ),
        source_storage_indexed=indexed_evidence,
    )
    if signal_pattern.get("reason"):
        return {
            "status": "invalid",
            "query": "",
            "reason": str(signal_pattern.get("reason") or "Unsupported model-signal relation."),
        }
    clauses.extend(str(item) for item in signal_pattern.get("clauses") or [] if str(item or "").strip())
    signal_target = "$modelSignalEvidence0"
    selected_contract_ids = sorted({
        model_signal_interpretation_contract_id(rule)
        for rule in rules
        if model_signal_interpretation_contract_id(rule)
    })
    clauses.extend([
        "$source has ontology-id $sourceId, has ontology-label $sourceLabel, has ontology-symbol $sourceSymbol;",
        signal_target + " has ontology-id $signalEvidenceId;",
    ])
    if selected_contract_ids:
        clauses.append(typedb_value_match(
            signal_target,
            "ontology-hypothesis-contract-id",
            selected_contract_ids,
            "==",
            "selectedHypothesisContractId",
        ))
    filter_keys = sorted({
        str(filter_key)
        for rule in rules
        for condition in model_signal_conditions(rule)
        for filter_key in dict(
            (condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {})).get("target_property_filters")
            or (condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {})).get("targetPropertyFilters")
            or {}
        )
    })
    columns = ["sourceId", "sourceLabel", "sourceSymbol", "signalEvidenceId"]
    for filter_key in filter_keys:
        attribute = typedb_target_attribute(filter_key)
        if not attribute:
            return {
                "status": "invalid",
                "query": "",
                "reason": "Unsupported model-signal contract field: " + filter_key,
            }
        variable = re.sub(r"[^A-Za-z0-9_]", "", filter_key)
        clauses.append(signal_target + " has " + attribute + " $" + variable + ";")
        columns.append(variable)
    relation_id_column = str(signal_pattern.get("relationIdColumn") or "")
    if relation_id_column:
        columns.append(relation_id_column)
    return {
        "status": "ok",
        "query": "match " + " ".join(item for item in clauses if item),
        "columns": list(dict.fromkeys(columns)),
        "evidenceColumns": [relation_id_column] if relation_id_column else [],
        "relationIdColumn": relation_id_column,
        "queryMode": (
            "typedb-manifest-evidence-index-model-signal-bridge"
            if indexed_evidence
            else "typedb-shared-model-signal-bridge-direct-batch"
        ),
        "indexedEvidenceQuery": indexed_evidence,
        "storageIdentityCount": storage_identity_count,
        "indexedRelationStorageCount": len(signal_relation_storage_ids),
        "evidenceIndexFingerprint": str(verified_index.get("fingerprint") or ""),
        "sharedModelSignalBridge": True,
        "modelSignalBridgeVersion": MODEL_SIGNAL_BRIDGE_VERSION,
        "bridgeSourceScope": scope,
        "contractFields": filter_keys,
        "ruleIds": list(batch.get("ruleIds") or []),
    }


def typedb_dispatch_model_signal_bridge_rows(
    batch: Dict[str, object],
    rows: Iterable[Dict[str, object]],
) -> Dict[str, object]:
    """Route TypeDB evidence rows to exact governed policy contracts."""

    by_contract: Dict[str, Dict[str, object]] = {}
    for raw_entry in batch.get("entries") or []:
        entry = dict(raw_entry or {})
        rule = entry.get("rule")
        contract_id = model_signal_interpretation_contract_id(rule)
        if not contract_id:
            continue
        if contract_id in by_contract:
            return {
                "status": "invalid",
                "matches": [],
                "failures": ["Duplicate model-signal hypothesis contract: " + contract_id],
            }
        by_contract[contract_id] = entry
    matches = []
    failures = []
    ignored_contracts: Set[str] = set()
    for raw_row in rows or []:
        row = dict(raw_row or {})
        contract_id = str(row.get("hypothesisContractId") or "").strip()
        entry = by_contract.get(contract_id)
        if entry is None:
            if contract_id:
                ignored_contracts.add(contract_id)
            continue
        rule = entry.get("rule")
        source_symbol = str(row.get("sourceSymbol") or symbol_from_subject(row.get("sourceId")) or "").upper().strip()
        candidate_symbols = set(clean_symbols_from_payload(entry.get("candidateSymbols") or []))
        if candidate_symbols and source_symbol not in candidate_symbols:
            continue
        signal_condition = model_signal_conditions(rule)[0]
        signal_payload = (
            signal_condition.to_dict()
            if hasattr(signal_condition, "to_dict")
            else dict(signal_condition or {})
        )
        expected_filters = dict(
            signal_payload.get("target_property_filters")
            or signal_payload.get("targetPropertyFilters")
            or {}
        )
        mismatches = []
        for key, expected in expected_filters.items():
            expected_value = typedb_expected_value(expected)
            actual_value = row.get(str(key))
            if isinstance(expected_value, list):
                valid = str(actual_value) in {str(item) for item in expected_value}
            else:
                valid = str(actual_value) == str(expected_value)
            if not valid:
                mismatches.append(str(key))
        if mismatches:
            failures.append(
                contract_id + " contract fields do not match: " + ", ".join(sorted(mismatches))
            )
            continue
        matches.append({"entry": entry, "row": row})
    return {
        "status": "ok" if not failures else "invalid",
        "matches": matches,
        "failures": failures,
        "ignoredContractIds": sorted(ignored_contracts),
        "ignoredContractRowCount": len(ignored_contracts),
    }
