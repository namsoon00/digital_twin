"""TypeDB-only input presence proofs for conditional research abstentions."""
from copy import deepcopy
from hashlib import sha256
import time

from ..typeql.indexed_queries import typedb_native_rule_runtime_query_plan
from ..typeql.rule_shape import normalized_condition_role
from ..typeql.storage_schema import typedb_subject_attribute, typedb_target_attribute, typedb_relation_attribute


def selection_input_rule(rule):
    """Require each referenced input; unsupported absence shapes stay unknown."""
    design = (rule.get("model_input_contract") or {}).get("researchDesign") or {}
    if (design.get("contract") != "registered-hypothesis-design-v2"
            or not design.get("modelRuleId")
            or design.get("modelRuleId") != design.get("comparisonRuleId")):
        return None
    result = deepcopy(rule)
    if (rule.get("source_kind") or "stock") != "stock":
        return None
    if not result.get("conditions") or len(result["conditions"]) > 32:
        return None
    for condition in result["conditions"]:
        if normalized_condition_role(condition) == "not":
            return None
        condition["role"] = "required"
        condition["condition_role"] = "required"
        if condition.get("kind") == "subject_property":
            if isinstance(condition.get("value"), dict) or not typedb_subject_attribute(str(condition.get("field") or "")):
                return None
            condition.update(operator="exists", value=True)
        elif condition.get("kind") == "relation":
            for key in ("target_property_filters", "targetPropertyFilters", "relation_property_filters", "relationPropertyFilters"):
                for field, value in (condition.get(key) or {}).items():
                    attribute = typedb_target_attribute(field) if key.startswith("target") else typedb_relation_attribute(field)
                    if not attribute:
                        return None
                    if isinstance(value, dict) and (value.get("field") or "default" in value):
                        return None
                    condition[key][field] = {"operator": "exists", "value": True}
        else:
            return None
    return result


def read_selection_receipts(store, driver, transaction_type, rule, symbols, matched_rows,
                            world_id, scoped_manifest_only, evidence_index, deadline):
    """Read after a complete predicate query, under the same ABox write lease."""
    presence_rule = selection_input_rule(rule)
    if not presence_rule or not symbols or len(symbols) > 20:
        return []
    matched = {str(row.get("sourceId") or "") for row in matched_rows}
    if all("stock:" + symbol in matched for symbol in symbols):
        return []
    budget = min(2.0, deadline - time.monotonic())
    if budget < .5:
        return []
    try:
        plan = typedb_native_rule_runtime_query_plan(presence_rule, symbols,
            scoped_manifest_only=scoped_manifest_only, world_id=world_id,
            evidence_read_index=evidence_index, compact_result_rows=True)
        if not plan.get("query") or not plan.get("anyConditionsVerified", True):
            return []
        with driver.transaction(store.database, transaction_type.READ, store.read_transaction_options(budget)) as tx:
            rows = store.read_rows_in_transaction(tx, plan["query"], plan.get("columns") or ["sourceId"],
                label="selectionInputPresence:" + rule["rule_id"], timeout_seconds=budget)
    except Exception:
        return []
    query_fingerprint = sha256(plan["query"].encode()).hexdigest()
    available = {str(row.get("sourceId") or "") for row in rows}
    return [{"ruleId": rule["rule_id"], "subjectId": "stock:" + symbol,
             "queryFingerprint": query_fingerprint, "inputAvailability": "typedb-proven",
             "failureReason": "condition-not-met"}
            for symbol in symbols if "stock:" + symbol in available and "stock:" + symbol not in matched]
