"""native_execution: context through explicit injected capabilities."""

from digital_twin.modules.model_registry.contracts import is_model_signal_interpretation_rule
from digital_twin.modules.model_registry.contracts import GraphInferenceRule
from digital_twin.modules.reasoning.infrastructure.typeql.condition_queries import (
    typedb_native_condition_check_query,
)
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    normalized_condition_role,
    typedb_native_rule_id,
)
from typing import Dict, Iterable, List
from .context_ports import NativeExecutionContextStore, NativeExecutionContextRuntime


def merge_native_match_rows(
    _store: NativeExecutionContextStore,
    rule: GraphInferenceRule,
    query_plan: Dict[str, object],
    rows: Iterable[Dict[str, object]],
    match_index: Dict[str, Dict[str, object]],
    matches: List[Dict[str, object]],
    world_id: str = "",
    *,
    _bindings: NativeExecutionContextRuntime
) -> None:
    for row in rows or []:
        source_id = str(row.get("sourceId") or "").strip()
        if not source_id:
            continue
        evidence_relation_ids = [
            str(row.get(column) or "")
            for column in (query_plan.get("evidenceColumns") or [])
            if str(row.get(column) or "").strip()
        ]
        match_key = str(rule.rule_id or "") + "|" + source_id
        existing = match_index.get(match_key)
        if existing:
            existing["evidenceRelationIds"] = sorted(
                set(list(existing.get("evidenceRelationIds") or []) + evidence_relation_ids)
            )
            existing_conditions = list(existing.get("matchedConditions") or [])
            existing_condition_ids = {
                str(item.get("conditionId") or "")
                for item in existing_conditions
                if isinstance(item, dict)
            }
            for item in _bindings.typedb_native_matched_conditions(rule, row, query_plan):
                if str(item.get("conditionId") or "") not in existing_condition_ids:
                    existing_conditions.append(item)
            existing["matchedConditions"] = existing_conditions
            continue
        condition_context = _store.typedb_rule_condition_context(
            rule, source_id, query_plan, row, world_id
        )
        if query_plan.get("indexedEvidenceQuery"):
            condition_context["conditionDetailSource"] = "typedb-manifest-evidence-index-match"
        evidence_relation_ids = sorted(
            set(evidence_relation_ids + list(condition_context.get("evidenceRelationIds") or []))
        )
        match = {
            "ruleId": rule.rule_id,
            "nativeRuleId": typedb_native_rule_id(rule.rule_id),
            "typeqlExecutionMode": "direct-typeql",
            "queryMode": str(query_plan.get("queryMode") or "direct-typeql"),
            "worldId": str(world_id or ""),
            "sourceId": source_id,
            "sourceLabel": str(row.get("sourceLabel") or ""),
            "sourceKind": str(rule.source_kind or ""),
            "matchedConditions": list(condition_context.get("matchedConditions") or []),
            "evidenceRelationIds": sorted(set(evidence_relation_ids)),
            "conditionDetailSource": str(
                condition_context.get("conditionDetailSource") or "direct-typeql-match"
            ),
            "modelSignalInterpretationPolicy": is_model_signal_interpretation_rule(rule),
            "modelSignalInterpretationPolicyId": (
                "model-signal-interpretation:" + str(rule.rule_id or "")
                if is_model_signal_interpretation_rule(rule)
                else ""
            ),
            "sharedModelSignalBridge": bool(query_plan.get("sharedModelSignalBridge")),
            "modelSignalBridgeVersion": str(query_plan.get("modelSignalBridgeVersion") or ""),
            "bridgeSourceScope": str(query_plan.get("bridgeSourceScope") or ""),
        }
        match_index[match_key] = match
        matches.append(match)


def typedb_rule_condition_context(
    _store: NativeExecutionContextStore,
    rule: GraphInferenceRule,
    source_id: str,
    query_plan: Dict[str, object] = None,
    row: Dict[str, object] = None,
    world_id: str = "",
    *,
    _bindings: NativeExecutionContextRuntime
) -> Dict[str, object]:
    if not _store.condition_detail_queries_enabled():
        return _bindings.typedb_static_rule_condition_context(rule, query_plan or {}, row or {})
    source_query_plan = dict(query_plan or {})
    interpretation_policy = bool(source_query_plan.get("modelSignalInterpretationPolicy"))
    shared_bridge = bool(source_query_plan.get("sharedModelSignalBridge"))
    bridge_condition_ids = set(source_query_plan.get("bridgeConditionIds") or [])
    residual_condition_ids = set(source_query_plan.get("residualConditionIds") or [])
    matched_conditions: List[Dict[str, object]] = []
    evidence_relation_ids: List[str] = []
    for index, condition in enumerate(getattr(rule, "conditions", []) or []):
        condition_payload = (
            condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {})
        )
        condition_id = str(
            condition_payload.get("condition_id")
            or condition_payload.get("conditionId")
            or "condition-" + str(index)
        )
        role = normalized_condition_role(condition_payload)
        condition_query_plan = typedb_native_condition_check_query(
            condition_payload,
            source_id,
            index,
            world_id=world_id,
        )
        rows: List[Dict[str, object]] = []
        if condition_query_plan.get("query"):
            rows = _store.read_rows(
                str(condition_query_plan.get("query")),
                condition_query_plan.get("columns") or [],
            )
        condition_matched = bool(rows)
        if role == "not":
            if not condition_matched:
                matched_conditions.append(
                    {
                        "conditionId": condition_id,
                        "kind": condition_payload.get("kind"),
                        "role": role,
                        "absenceSatisfied": True,
                    }
                )
            continue
        if role in {"any", "optional"} and not condition_matched:
            continue
        if not condition_matched:
            matched_conditions.append(
                {
                    "conditionId": condition_id,
                    "kind": condition_payload.get("kind"),
                    "role": role,
                    "matched": False,
                }
            )
            continue
        payload = {
            "conditionId": condition_id,
            "kind": condition_payload.get("kind"),
            "role": role,
            "matchedByTypeDB": True,
        }
        if interpretation_policy:
            payload["matchedByModelSignalInterpretationPolicy"] = True
        if shared_bridge and condition_id in bridge_condition_ids:
            payload["matchedBySharedModelSignalBridge"] = True
        if shared_bridge and condition_id in residual_condition_ids:
            payload["matchedByInterpretationPolicyQuery"] = True
        if condition_payload.get("kind") == "subject_property":
            payload.update(
                {
                    "field": condition_payload.get("field"),
                    "operator": condition_payload.get("operator"),
                    "value": condition_payload.get("value"),
                }
            )
        elif condition_payload.get("kind") == "relation":
            relation_id_column = str(condition_query_plan.get("relationIdColumn") or "")
            relation_id = str((rows[0] if rows else {}).get(relation_id_column) or "")
            if relation_id:
                payload["relationId"] = relation_id
                evidence_relation_ids.append(relation_id)
            payload.update(
                {
                    "relationType": condition_payload.get("relation_type")
                    or condition_payload.get("relationType"),
                }
            )
        matched_conditions.append(payload)
    return {
        "matchedConditions": matched_conditions,
        "evidenceRelationIds": sorted(set(evidence_relation_ids)),
        "conditionDetailSource": (
            "typedb-model-signal-interpretation-policy-detail"
            if interpretation_policy
            else "direct-typeql-detail-query"
        ),
    }
