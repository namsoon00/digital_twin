"""Indexed queries for TypeQL, without database execution."""

from typing import Dict, Iterable, List, Set

from digital_twin.modules.model_registry.contracts import is_model_signal_interpretation_rule, model_signal_bridge_source_scope
from digital_twin.modules.reasoning.infrastructure.typeql.constants import (
    NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION,
    NATIVE_RULE_INDEXED_QUERY_MAX_STORAGE_IDS,
)
from digital_twin.modules.reasoning.infrastructure.typeql.match_queries import typedb_native_match_query
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    clean_symbols_from_payload,
    normalized_condition_role,
    typedb_source_kind_uses_symbol_scope,
)


def typedb_native_indexed_evidence_match_query(
    rule: Dict[str, object],
    target_symbols: Iterable[str] = None,
    evidence_read_index: Dict[str, object] = None,
    world_id: str = "",
    compact_result_rows: bool = False,
) -> Dict[str, object]:
    """Build a TypeDB-native RuleBox predicate anchored by Manifest rows.

    The verified active-Manifest evidence index contains physical storage IDs,
    not a Python decision. Binding the stock and each required assertion by
    those IDs lets TypeDB evaluate the authored filters against the exact
    current facts without repeatedly expanding every active scope pointer.
    Negative predicates are also eligible when the verified Manifest index
    covers their relation type. The generated TypeQL keeps the absence check
    inside TypeDB while binding it to the exact active relation storage rows.
    """
    verified = dict(evidence_read_index or {})
    index = dict(verified.get("index") or {}) if str(verified.get("status") or "") == "verified" else {}
    symbols = clean_symbols_from_payload(list(target_symbols or []))
    rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "")
    if not index or not symbols:
        return {
            "status": "not-eligible",
            "ruleId": rule_id,
            "query": "",
            "reason": "A verified active-Manifest evidence index and explicit target symbol are required.",
        }
    conditions = [item for item in rule.get("conditions") or [] if isinstance(item, dict)]
    source_kind = str(rule.get("source_kind") or rule.get("sourceKind") or "stock")
    has_any_conditions = any(
        normalized_condition_role(condition) in {"any", "optional"}
        for condition in conditions
    )
    indexed_relation_types = sorted({
        str(condition.get("relation_type") or condition.get("relationType") or "").upper().strip()
        for condition in conditions
        if str(condition.get("kind") or "") == "relation"
        and normalized_condition_role(condition) in {"required", "not", "any", "optional"}
        and str(condition.get("relation_type") or condition.get("relationType") or "").strip()
    })
    if not indexed_relation_types:
        return {
            "status": "not-eligible",
            "ruleId": rule_id,
            "query": "",
            "reason": "Rule has no relation predicate to anchor by active evidence storage identity.",
        }
    source_ids_by_symbol = dict(index.get("sourceIdsBySymbol") or {})
    source_storage_by_id = dict(index.get("sourceStorageIdsBySourceId") or {})
    relation_ids_by_symbol_and_type = dict(index.get("relationStorageIdsBySymbolAndType") or {})
    relation_ids_by_symbol_type_field = dict(
        index.get("relationStorageIdsBySymbolAndTypeAndField") or {}
    )
    relation_ids_by_symbol_type_target_kind = dict(
        index.get("relationStorageIdsBySymbolAndTypeAndTargetKind") or {}
    )
    target_kind_index_available = bool(
        str(index.get("version") or "") == NATIVE_RULE_EVIDENCE_READ_INDEX_VERSION
        and isinstance(
            index.get("relationStorageIdsBySymbolAndTypeAndTargetKind"),
            dict,
        )
    )
    indexed_source_symbols = list(symbols)
    if not typedb_source_kind_uses_symbol_scope(source_kind):
        # The execution index has already been narrowed to the requested stock
        # plus its shared portfolio/market context. Select the authored
        # aggregate source by its stable physical identity prefix. Passing all
        # stock sources here makes a one-row portfolio rule form a broad
        # source/relation product before the ontology-kind predicate can prune
        # it, which is the dominant timeout shape for portfolio any-rules.
        source_prefix = source_kind.lower().strip() + ":"
        indexed_source_symbols = sorted({
            str(symbol or "").upper().strip()
            for symbol, source_ids in source_ids_by_symbol.items()
            if str(symbol or "").strip()
            and any(
                str(item or "").lower().strip().startswith(source_prefix)
                for item in source_ids or []
            )
        })
    source_storage_ids = sorted({
        str(source_storage_by_id.get(str(source_id or "")) or "").strip()
        for symbol in indexed_source_symbols
        for source_id in source_ids_by_symbol.get(symbol, []) or []
        if str(source_storage_by_id.get(str(source_id or "")) or "").strip()
    })
    if not source_storage_ids:
        return {
            "status": "not-eligible",
            "ruleId": rule_id,
            "query": "",
            "reason": "Active Manifest evidence index has no physical stock source for the requested symbols.",
        }
    relation_storage_ids_by_type: Dict[str, List[str]] = {}
    relation_storage_ids_by_condition: Dict[str, List[str]] = {}

    def condition_field_values(condition: Dict[str, object]) -> List[str]:
        filters = dict(
            condition.get("target_property_filters")
            or condition.get("targetPropertyFilters")
            or {}
        )
        expected = filters.get("field")
        if isinstance(expected, dict):
            if str(expected.get("operator") or "==").strip() != "==":
                return []
            expected = expected.get("value")
        if isinstance(expected, (list, tuple, set)):
            return sorted({str(value or "").strip() for value in expected if str(value or "").strip()})
        value = str(expected or "").strip()
        return [value] if value else []

    def type_storage_ids(relation_type: str) -> List[str]:
        return sorted({
            str(storage_id or "").strip()
            for symbol in indexed_source_symbols
            for storage_id in dict(relation_ids_by_symbol_and_type.get(symbol) or {}).get(relation_type, []) or []
            if str(storage_id or "").strip()
        })

    for relation_type in indexed_relation_types:
        storage_ids = type_storage_ids(relation_type)
        relation_is_required = any(
            str(condition.get("kind") or "") == "relation"
            and normalized_condition_role(condition) == "required"
            and str(condition.get("relation_type") or condition.get("relationType") or "").upper().strip()
            == relation_type
            for condition in conditions
        )
        if not storage_ids and relation_is_required:
            return {
                "status": "not-eligible",
                "ruleId": rule_id,
                "query": "",
                "reason": "Active Manifest evidence index has no " + relation_type + " relation for the requested symbols.",
            }
        if storage_ids:
            relation_storage_ids_by_type[relation_type] = storage_ids
    selector_indexed_condition_ids: Set[str] = set()
    for condition in conditions:
        if (
            str(condition.get("kind") or "") != "relation"
            or normalized_condition_role(condition) not in {"required", "not"}
        ):
            continue
        relation_type = str(condition.get("relation_type") or condition.get("relationType") or "").upper().strip()
        condition_id = str(condition.get("condition_id") or condition.get("conditionId") or "").strip()
        if not relation_type or not condition_id:
            continue
        base_storage_ids = set(relation_storage_ids_by_type.get(relation_type, []) or [])
        field_values = condition_field_values(condition)
        field_storage_ids = {
            str(storage_id or "").strip()
            for symbol in indexed_source_symbols
            for field_value in field_values
            for storage_id in dict(
                dict(relation_ids_by_symbol_type_field.get(symbol) or {}).get(relation_type, {})
            ).get(field_value, []) or []
            if str(storage_id or "").strip()
        }
        target_kind = str(
            condition.get("target_kind") or condition.get("targetKind") or ""
        ).strip()
        target_kind_storage_ids = {
            str(storage_id or "").strip()
            for symbol in indexed_source_symbols
            for storage_id in dict(
                dict(relation_ids_by_symbol_type_target_kind.get(symbol) or {}).get(
                    relation_type,
                    {},
                )
            ).get(target_kind, []) or []
            if str(storage_id or "").strip()
        }
        role = normalized_condition_role(condition)
        if field_values and not field_storage_ids and role == "required":
            return {
                "status": "not-eligible",
                "ruleId": rule_id,
                "query": "",
                "reason": (
                    "Active Manifest evidence index has no field-specific relation "
                    "storage identities for required condition " + condition_id + "."
                ),
            }
        if (
            target_kind
            and target_kind_index_available
            and not target_kind_storage_ids
            and role == "required"
        ):
            return {
                "status": "not-eligible",
                "ruleId": rule_id,
                "query": "",
                "reason": (
                    "Active Manifest evidence index has no target-kind relation "
                    "storage identities for required condition " + condition_id + "."
                ),
            }
        storage_ids = set(base_storage_ids)
        selector_applied = False
        if field_values:
            storage_ids.intersection_update(field_storage_ids)
            selector_applied = True
        if target_kind and target_kind_index_available:
            storage_ids.intersection_update(target_kind_storage_ids)
            selector_applied = True
        if storage_ids:
            relation_storage_ids_by_condition[condition_id] = sorted(storage_ids)
            if selector_applied:
                selector_indexed_condition_ids.add(condition_id)
    used_relation_storage_ids: Set[str] = set()
    for condition in conditions:
        if str(condition.get("kind") or "") != "relation":
            continue
        role = normalized_condition_role(condition)
        if role not in {"required", "not", "any", "optional"}:
            continue
        condition_id = str(
            condition.get("condition_id")
            or condition.get("conditionId")
            or ""
        ).strip()
        relation_type = str(
            condition.get("relation_type") or condition.get("relationType") or ""
        ).upper().strip()
        if condition_id in selector_indexed_condition_ids:
            used_relation_storage_ids.update(
                relation_storage_ids_by_condition.get(condition_id, [])
            )
        else:
            used_relation_storage_ids.update(
                relation_storage_ids_by_type.get(relation_type, [])
            )
    storage_identity_count = len({
        *source_storage_ids,
        *used_relation_storage_ids,
    })
    if storage_identity_count > NATIVE_RULE_INDEXED_QUERY_MAX_STORAGE_IDS:
        return {
            "status": "not-eligible",
            "ruleId": rule_id,
            "query": "",
            "reason": "Active Manifest evidence batch exceeds the bounded indexed native-query shape.",
            "storageIdentityCount": storage_identity_count,
        }
    plan = typedb_native_match_query(
        rule,
        [],
        scoped_manifest_only=False,
        include_any_conditions=has_any_conditions,
        world_id=world_id,
        active_source_storage_ids=source_storage_ids,
        active_relation_storage_ids_by_type=relation_storage_ids_by_type,
        active_relation_storage_ids_by_condition=relation_storage_ids_by_condition,
        compact_result_rows=compact_result_rows,
    )
    if not plan.get("query"):
        return {
            **plan,
            "status": "not-eligible",
            "indexedEvidenceQuery": False,
        }
    return {
        **plan,
        "status": "ok",
        "indexedEvidenceQuery": True,
        "queryMode": (
            "typedb-manifest-evidence-index-any-combined"
            if has_any_conditions
            else "typedb-manifest-evidence-index"
        ),
        "anyConditionsVerified": has_any_conditions,
        "storageIdentityCount": storage_identity_count,
        "activeEvidenceRelationTypes": indexed_relation_types,
        "activeEvidenceRelationStorageMode": (
            "condition-selector-index"
            if selector_indexed_condition_ids
            else "condition-field-index"
            if relation_ids_by_symbol_type_field
            else "relation-type-index"
        ),
        "targetKindIndexedConditionCount": len([
            condition_id
            for condition_id in selector_indexed_condition_ids
            if any(
                str(item.get("condition_id") or item.get("conditionId") or "").strip()
                == condition_id
                and str(item.get("target_kind") or item.get("targetKind") or "").strip()
                for item in conditions
            )
        ]),
    }


def typedb_native_rule_runtime_query_plan(
    rule: Dict[str, object],
    target_symbols: Iterable[str] = None,
    scoped_manifest_only: bool = False,
    world_id: str = "",
    evidence_read_index: Dict[str, object] = None,
    compact_result_rows: bool = False,
) -> Dict[str, object]:
    """Choose a TypeDB-owned predicate surface without Python evaluation."""
    model_signal_policy = is_model_signal_interpretation_rule(rule)
    interpretation_metadata = {
        "modelSignalInterpretationPolicy": model_signal_policy,
        "modelSignalInterpretationPolicyId": (
            "model-signal-interpretation:"
            + str(rule.get("rule_id") or rule.get("ruleId") or "")
            if model_signal_policy
            else ""
        ),
        "bridgeSourceScope": (
            model_signal_bridge_source_scope(rule) if model_signal_policy else ""
        ),
    }
    indexed_plan = typedb_native_indexed_evidence_match_query(
        rule,
        target_symbols,
        evidence_read_index,
        world_id,
        compact_result_rows,
    )
    if str(indexed_plan.get("status") or "") == "ok":
        return {**indexed_plan, **interpretation_metadata}
    return {
        **typedb_native_match_query(
            rule,
            target_symbols,
            scoped_manifest_only=scoped_manifest_only,
            include_any_conditions=False,
            world_id=world_id,
            compact_result_rows=compact_result_rows,
        ),
        "indexedEvidenceQuery": False,
        "queryMode": "typedb-scoped-typeql",
        "indexedEvidenceFallbackReason": str(indexed_plan.get("reason") or ""),
        **interpretation_metadata,
    }
