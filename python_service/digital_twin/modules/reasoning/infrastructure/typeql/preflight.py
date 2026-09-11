"""Preflight for TypeQL, without database execution."""

from typing import Dict, List, Tuple

from digital_twin.modules.reasoning.domain.ontology_contracts import OntologyEntity, PortfolioOntology
from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_expected_value
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    normalized_condition_role,
    typedb_rule_condition_payloads,
    typedb_source_kind_uses_symbol_scope,
)


def typedb_native_rule_required_relation_types(rule: object) -> set:
    """Return required relation dependencies without evaluating their filters.

    A missing required relation type makes a TypeDB rule impossible for a
    subject in the current ABox generation.  Values, operators, and optional
    branches remain exclusively inside the native TypeDB function.
    """
    required_types = set()
    for condition in typedb_rule_condition_payloads(rule):
        if str(condition.get("kind") or "") != "relation":
            continue
        if normalized_condition_role(condition) != "required":
            continue
        relation_type = str(condition.get("relation_type") or condition.get("relationType") or "").upper().strip()
        if relation_type:
            required_types.add(relation_type)
    return required_types


def typedb_native_rule_any_relation_requirement(rule: object) -> tuple:
    """Return topology-only alternatives when every ``any`` branch is a relation.

    This is only a planner shortcut. Attribute filters and the actual minimum
    match count are still evaluated by direct TypeQL.
    """
    any_conditions = [
        condition
        for condition in typedb_rule_condition_payloads(rule)
        if normalized_condition_role(condition) == "any"
    ]
    if not any_conditions or any(str(condition.get("kind") or "") != "relation" for condition in any_conditions):
        return [], 0
    relation_types = [
        str(condition.get("relation_type") or condition.get("relationType") or "").upper().strip()
        for condition in any_conditions
    ]
    if any(not relation_type for relation_type in relation_types):
        return [], 0
    raw_minimum = getattr(rule, "any_condition_min_count", None)
    if raw_minimum is None and isinstance(rule, dict):
        raw_minimum = rule.get("any_condition_min_count") or rule.get("anyConditionMinCount")
    return relation_types, max(1, int(number_or_none(raw_minimum) or 1))


def typedb_preflight_properties(properties: Dict[str, object]) -> Dict[str, object]:
    """Flatten a persisted ontology JSON object for a conservative planner."""
    payload = dict(properties or {})
    nested = payload.get("properties")
    if isinstance(nested, dict):
        payload.update(nested)
    return payload


def typedb_preflight_scalar_equal(actual: object, expected: object) -> bool:
    """Compare values as TypeDB's promoted string/numeric attributes do."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return str(actual).strip().lower() == str(expected).strip().lower()
    actual_number = number_or_none(actual)
    expected_number = number_or_none(expected)
    if actual_number is not None and expected_number is not None:
        return float(actual_number) == float(expected_number)
    return actual == expected or str(actual) == str(expected)


def typedb_preflight_value_matches(actual: object, operator: object, expected: object):
    """Return ``False`` only when one RuleBox comparison is provably false.

    This is an execution planner, not a second rule engine: unknown/missing
    values return ``None`` and leave the rule for TypeDB to decide.  Values
    and operators are read from the persisted RuleBox condition unchanged.
    """
    if actual in (None, ""):
        return None
    op = str(operator or "==").strip().lower()
    if op in {"exists", "present"}:
        return True
    expected = typedb_expected_value(expected)
    if expected in (None, "", [], {}):
        # TypeQL emits no predicate for an empty expected value.
        return True
    if isinstance(expected, (list, tuple, set)):
        outcomes = [typedb_preflight_value_matches(actual, "==", item) for item in expected]
        if any(item is True for item in outcomes):
            return True
        return False if outcomes and all(item is False for item in outcomes) else None
    if isinstance(actual, (list, tuple, set)):
        outcomes = [typedb_preflight_value_matches(item, op, expected) for item in actual]
        if op in {"!=", "ne"}:
            if any(item is True for item in outcomes):
                return True
            return False if outcomes and all(item is False for item in outcomes) else None
        if any(item is True for item in outcomes):
            return True
        return False if outcomes and all(item is False for item in outcomes) else None
    if op in {"==", "eq", "in"}:
        return typedb_preflight_scalar_equal(actual, expected)
    if op in {"!=", "ne"}:
        return not typedb_preflight_scalar_equal(actual, expected)
    if op in {">", "gt", ">=", "gte", "<", "lt", "<=", "lte"}:
        actual_number = number_or_none(actual)
        expected_number = number_or_none(expected)
        if actual_number is None or expected_number is None:
            return None
        if op in {">", "gt"}:
            return float(actual_number) > float(expected_number)
        if op in {">=", "gte"}:
            return float(actual_number) >= float(expected_number)
        if op in {"<", "lt"}:
            return float(actual_number) < float(expected_number)
        return float(actual_number) <= float(expected_number)
    return None


def typedb_preflight_filter_key_and_operator(filter_key: object, expected: object) -> Tuple[str, str, object]:
    """Mirror the supported target/relation filter form used by TypeQL."""
    key = str(filter_key or "")
    operator = "=="
    if key == "minValue":
        key, operator = "value", ">="
    elif key == "maxValue":
        key, operator = "value", "<="
    elif key.startswith("min") and len(key) > 3:
        key, operator = key[3].lower() + key[4:], ">="
    elif key.startswith("max") and len(key) > 3:
        key, operator = key[3].lower() + key[4:], "<="
    if isinstance(expected, dict) and expected.get("operator"):
        operator = str(expected.get("operator") or operator)
    return key, operator, expected


def typedb_preflight_filters_match(properties: Dict[str, object], filters: Dict[str, object]):
    """Return a conservative result for persisted relation/target filters."""
    values = typedb_preflight_properties(properties)
    unknown = False
    for filter_key, expected in dict(filters or {}).items():
        key, operator, raw_expected = typedb_preflight_filter_key_and_operator(filter_key, expected)
        verdict = typedb_preflight_value_matches(values.get(key), operator, raw_expected)
        if verdict is False:
            return False
        if verdict is None:
            unknown = True
    return None if unknown else True


def typedb_rule_condition_value(condition: object, field: str, default=None):
    """Read one persisted RuleBox condition field from object or payload form."""
    if isinstance(condition, dict):
        return condition.get(field, default)
    return getattr(condition, field, default)


def typedb_preflight_relation_condition_matches(
    graph: PortfolioOntology,
    subject: OntologyEntity,
    condition: object,
):
    """Check whether a required relation condition is still possible in ABox.

    Returning ``None`` preserves the native TypeDB call when the planner does
    not have enough endpoint data.  A ``False`` result is therefore a strict
    proof that no persisted direct relation can satisfy this required clause.
    """
    relation_type = str(typedb_rule_condition_value(condition, "relation_type", "") or "").upper().strip()
    if not relation_type:
        return None
    direction = str(typedb_rule_condition_value(condition, "direction", "") or "out").lower()
    target_kind = str(typedb_rule_condition_value(condition, "target_kind", "") or "")
    targets = {item.entity_id: item for item in graph.entities}
    unknown = False
    for relation in graph.relations:
        if str(relation.relation_type or "").upper().strip() != relation_type:
            continue
        if direction == "in":
            if str(relation.target or "") != str(subject.entity_id or ""):
                continue
            target_id = str(relation.source or "")
        else:
            if str(relation.source or "") != str(subject.entity_id or ""):
                continue
            target_id = str(relation.target or "")
        target = targets.get(target_id)
        if target is None:
            unknown = True
            continue
        if target_kind and str(target.kind or "") != target_kind:
            continue
        target_verdict = typedb_preflight_filters_match(
            target.properties or {},
            typedb_rule_condition_value(condition, "target_property_filters", {}) or {},
        )
        relation_properties = dict(relation.properties or {})
        relation_properties.setdefault("weight", getattr(relation, "weight", None))
        relation_verdict = typedb_preflight_filters_match(
            relation_properties,
            typedb_rule_condition_value(condition, "relation_property_filters", {}) or {},
        )
        if target_verdict is False or relation_verdict is False:
            continue
        if target_verdict is None or relation_verdict is None:
            unknown = True
            continue
        return True
    if unknown:
        return None
    # A complete preflight graph can prove absence both when no relation of
    # the required type exists and when every endpoint/filter candidate fails.
    return False


def typedb_native_rule_required_conditions_preflight(
    graph: PortfolioOntology,
    rule: object,
    symbol: str,
    incoming_relations_complete: bool = True,
) -> Dict[str, object]:
    """Prove only impossible RuleBox clauses for one RuleBox source symbol.

    This is a negative planner, not a second decision engine. Besides a
    failed required condition, it can reject an N-of-M group only when the
    exact active ABox proves that fewer than N alternatives can possibly
    match. Unknown values and incomplete incoming relation reads always leave
    the rule to TypeDB.
    """
    clean_symbol = str(symbol or "").upper().strip()
    source_kind = str(
        getattr(rule, "source_kind", "")
        or (rule.get("source_kind") or rule.get("sourceKind") if isinstance(rule, dict) else "")
        or "stock"
    )
    symbol_scoped_source = typedb_source_kind_uses_symbol_scope(source_kind)
    candidates = [
        item for item in graph.entities
        if str(item.kind or "") == source_kind
        and (
            not symbol_scoped_source
            or str((item.properties or {}).get("symbol") or "").upper().strip() == clean_symbol
        )
    ]
    if len(candidates) != 1:
        return {"status": "unknown", "reason": "ABox preflight has no unambiguous RuleBox source subject.", "failedConditionIds": []}
    subject = candidates[0]
    properties = typedb_preflight_properties(subject.properties or {})
    if symbol_scoped_source:
        properties.setdefault("symbol", clean_symbol)
    properties.setdefault("kind", subject.kind)
    properties.setdefault("ontologyBox", (subject.properties or {}).get("ontologyBox") or "ABox")
    failed_condition_ids: List[str] = []
    unknown = False
    any_conditions = []
    negative_conditions = []
    rule_conditions = getattr(rule, "conditions", None)
    if rule_conditions is None and isinstance(rule, dict):
        rule_conditions = rule.get("conditions") or []

    def condition_verdict(condition: object):
        kind = str(typedb_rule_condition_value(condition, "kind", "") or "")
        if kind == "subject_property":
            return typedb_preflight_value_matches(
                properties.get(str(typedb_rule_condition_value(condition, "field", "") or "")),
                typedb_rule_condition_value(condition, "operator", "=="),
                typedb_rule_condition_value(condition, "value", None),
            )
        if kind == "relation":
            direction = str(typedb_rule_condition_value(condition, "direction", "") or "out").lower()
            if direction == "in" and not incoming_relations_complete:
                return None
            return typedb_preflight_relation_condition_matches(graph, subject, condition)
        return None

    for condition in rule_conditions or []:
        condition_payload = condition.to_dict() if hasattr(condition, "to_dict") else dict(condition or {})
        role = normalized_condition_role(condition_payload)
        if role in {"any", "optional"}:
            any_conditions.append(condition)
            continue
        if role == "not":
            negative_conditions.append(condition)
            continue
        if role != "required":
            continue
        condition_id = str(typedb_rule_condition_value(condition, "condition_id", "") or "")
        verdict = condition_verdict(condition)
        if verdict is False:
            failed_condition_ids.append(condition_id)
        elif verdict is None:
            unknown = True
    if failed_condition_ids:
        return {
            "status": "impossible",
            "reason": "Active ABox cannot satisfy required RuleBox conditions: " + ", ".join(failed_condition_ids[:4]),
            "failedConditionIds": failed_condition_ids,
        }
    negative_matches = []
    for condition in negative_conditions:
        # A negative condition means the rule requires this fact to be
        # absent. When the exact active ABox proves it is present, TypeDB
        # cannot materialize the rule. Unknown remains executable so the
        # planner never substitutes its own judgement for TypeDB.
        if condition_verdict(condition) is True:
            negative_matches.append(
                str(typedb_rule_condition_value(condition, "condition_id", "") or "negative-condition")
            )
    if negative_matches:
        return {
            "status": "impossible",
            "reason": "Active ABox satisfies a RuleBox negative condition that must be absent.",
            "failedConditionIds": ["not:" + item for item in negative_matches],
        }
    if any_conditions:
        raw_minimum = getattr(rule, "any_condition_min_count", None)
        if raw_minimum is None and isinstance(rule, dict):
            raw_minimum = rule.get("any_condition_min_count") or rule.get("anyConditionMinCount")
        minimum = max(1, int(number_or_none(raw_minimum) or 1))
        possible_count = 0
        unknown_count = 0
        for condition in any_conditions:
            verdict = condition_verdict(condition)
            if verdict is True:
                possible_count += 1
            elif verdict is None:
                unknown_count += 1
        if possible_count + unknown_count < minimum:
            rule_id = str(getattr(rule, "rule_id", "") or (rule.get("rule_id") if isinstance(rule, dict) else "") or "")
            return {
                "status": "impossible",
                "reason": (
                    "Active ABox cannot satisfy the RuleBox any-condition minimum "
                    + str(minimum) + "."
                ),
                "failedConditionIds": ["any-group:" + rule_id] if rule_id else ["any-group"],
                "anyConditionMinimum": minimum,
                "anyConditionPossibleCount": possible_count,
                "anyConditionUnknownCount": unknown_count,
            }
    return {
        "status": "unknown" if unknown else "possible",
        "reason": "" if not unknown else "Some required ABox values were unavailable to the preflight planner.",
        "failedConditionIds": [],
    }


def typedb_native_rule_subject_properties_preflight(
    rule: object,
    symbol: str,
    subject_properties_by_symbol: Dict[str, Dict[str, object]] = None,
) -> Dict[str, object]:
    """Use an exact Manifest source-property index only to prove impossibility.

    The index is generated from the same immutable graph as the active ABox.
    Missing legacy/index values remain unknown, and a positive comparison
    never materializes an inference.  The surviving rule is still evaluated
    in full by its direct TypeQL rule.
    """
    clean_symbol = str(symbol or "").upper().strip()
    property_index = dict(subject_properties_by_symbol or {})
    if not clean_symbol or clean_symbol not in property_index:
        return {
            "status": "unknown",
            "reason": "Active Manifest has no exact source-property index for this subject.",
            "failedConditionIds": [],
        }
    properties = typedb_preflight_properties(
        dict(property_index.get(clean_symbol) or {})
    )
    properties.setdefault("symbol", clean_symbol)
    source_kind = str(
        getattr(rule, "source_kind", "")
        or (rule.get("source_kind") or rule.get("sourceKind") if isinstance(rule, dict) else "")
        or "stock"
    )
    indexed_kind = str(properties.get("kind") or "").strip()
    if indexed_kind and indexed_kind != source_kind:
        return {
            "status": "impossible",
            "reason": "Active Manifest source kind does not match the RuleBox source kind.",
            "failedConditionIds": ["source-kind"],
        }

    any_conditions = []
    unknown = False
    for condition in typedb_rule_condition_payloads(rule):
        role = normalized_condition_role(condition)
        if role in {"any", "optional"}:
            any_conditions.append(condition)
            continue
        if str(condition.get("kind") or "") != "subject_property":
            continue
        condition_id = str(condition.get("condition_id") or condition.get("conditionId") or "")
        verdict = typedb_preflight_value_matches(
            properties.get(str(condition.get("field") or "")),
            condition.get("operator") or "==",
            condition.get("value"),
        )
        if role == "not":
            if verdict is True:
                return {
                    "status": "impossible",
                    "reason": "Active Manifest satisfies a RuleBox negative source condition.",
                    "failedConditionIds": ["not:" + (condition_id or "negative-condition")],
                }
            if verdict is None:
                unknown = True
            continue
        if role == "required" and verdict is False:
            return {
                "status": "impossible",
                "reason": "Active Manifest contradicts a required RuleBox source condition.",
                "failedConditionIds": [condition_id] if condition_id else [],
            }
        if role == "required" and verdict is None:
            unknown = True

    if any_conditions:
        raw_minimum = getattr(rule, "any_condition_min_count", None)
        if raw_minimum is None and isinstance(rule, dict):
            raw_minimum = rule.get("any_condition_min_count") or rule.get("anyConditionMinCount")
        minimum = max(1, int(number_or_none(raw_minimum) or 1))
        possible_count = 0
        unknown_count = 0
        for condition in any_conditions:
            if str(condition.get("kind") or "") != "subject_property":
                unknown_count += 1
                continue
            verdict = typedb_preflight_value_matches(
                properties.get(str(condition.get("field") or "")),
                condition.get("operator") or "==",
                condition.get("value"),
            )
            if verdict is True:
                possible_count += 1
            elif verdict is None:
                unknown_count += 1
        if possible_count + unknown_count < minimum:
            return {
                "status": "impossible",
                "reason": "Active Manifest cannot satisfy the RuleBox any-condition minimum.",
                "failedConditionIds": ["any-group"],
                "anyConditionMinimum": minimum,
                "anyConditionPossibleCount": possible_count,
                "anyConditionUnknownCount": unknown_count,
            }
    return {
        "status": "unknown" if unknown else "possible",
        "reason": "" if not unknown else "Some indexed source values were unavailable.",
        "failedConditionIds": [],
    }


def typedb_native_rule_manifest_evidence_preflight(
    rule: object,
    symbol: str,
    subject_properties_by_symbol: Dict[str, Dict[str, object]] = None,
    relation_evidence_by_symbol: Dict[str, List[Dict[str, object]]] = None,
    relation_evidence_complete_by_symbol: Dict[str, bool] = None,
) -> Dict[str, object]:
    """Prove an impossible rule from one exact Manifest evidence index.

    Subject and relation alternatives are evaluated together so an N-of-M
    group can be rejected only when every indexed branch is conclusively
    false. A positive result remains merely possible and still goes to TypeDB.
    """
    clean_symbol = str(symbol or "").upper().strip()
    property_index = dict(subject_properties_by_symbol or {})
    relation_index = dict(relation_evidence_by_symbol or {})
    relation_completeness = dict(relation_evidence_complete_by_symbol or {})
    subject_available = clean_symbol in property_index
    relation_available = clean_symbol in relation_index
    relation_complete = bool(relation_completeness.get(clean_symbol))
    if not subject_available and not relation_available:
        return {
            "status": "unknown",
            "reason": "Active Manifest has no exact subject or relation evidence index.",
            "failedConditionIds": [],
        }
    properties = typedb_preflight_properties(
        dict(property_index.get(clean_symbol) or {})
    ) if subject_available else {}
    properties.setdefault("symbol", clean_symbol)
    source_kind = str(
        getattr(rule, "source_kind", "")
        or (rule.get("source_kind") or rule.get("sourceKind") if isinstance(rule, dict) else "")
        or "stock"
    )
    indexed_kind = str(properties.get("kind") or "").strip()
    if indexed_kind and indexed_kind != source_kind:
        return {
            "status": "impossible",
            "reason": "Active Manifest source kind does not match the RuleBox source kind.",
            "failedConditionIds": ["source-kind"],
        }
    evidence_entries = [
        dict(item)
        for item in relation_index.get(clean_symbol) or []
        if isinstance(item, dict)
    ]

    def relation_verdict(condition: Dict[str, object]):
        if not relation_available or not relation_complete:
            return None
        relation_type = str(
            condition.get("relation_type") or condition.get("relationType") or ""
        ).upper().strip()
        direction = str(condition.get("direction") or "out").lower().strip()
        target_kind = str(
            condition.get("target_kind") or condition.get("targetKind") or ""
        ).strip()
        if not relation_type:
            return None
        unknown = False
        for entry in evidence_entries:
            if str(entry.get("relationType") or "").upper().strip() != relation_type:
                continue
            if str(entry.get("direction") or "out").lower().strip() != direction:
                continue
            if target_kind and str(entry.get("targetKind") or "") != target_kind:
                continue
            target_match = typedb_preflight_filters_match(
                dict(entry.get("targetProperties") or {}),
                condition.get("target_property_filters")
                or condition.get("targetPropertyFilters")
                or {},
            )
            relation_match = typedb_preflight_filters_match(
                dict(entry.get("relationProperties") or {}),
                condition.get("relation_property_filters")
                or condition.get("relationPropertyFilters")
                or {},
            )
            if target_match is False or relation_match is False:
                continue
            if target_match is None or relation_match is None:
                unknown = True
                continue
            return True
        return None if unknown else False

    def condition_verdict(condition: Dict[str, object]):
        kind = str(condition.get("kind") or "")
        if kind == "subject_property":
            if not subject_available:
                return None
            return typedb_preflight_value_matches(
                properties.get(str(condition.get("field") or "")),
                condition.get("operator") or "==",
                condition.get("value"),
            )
        if kind == "relation":
            return relation_verdict(condition)
        return None

    required_failures = []
    negative_matches = []
    any_conditions = []
    unknown = False
    for condition in typedb_rule_condition_payloads(rule):
        role = normalized_condition_role(condition)
        condition_id = str(
            condition.get("condition_id") or condition.get("conditionId") or ""
        )
        if role in {"any", "optional"}:
            any_conditions.append(condition)
            continue
        verdict = condition_verdict(condition)
        if role == "not":
            if verdict is True:
                negative_matches.append(condition_id or "negative-condition")
            elif verdict is None:
                unknown = True
            continue
        if verdict is False:
            required_failures.append(condition_id)
        elif verdict is None:
            unknown = True
    if required_failures:
        return {
            "status": "impossible",
            "reason": "Active Manifest contradicts required RuleBox evidence.",
            "failedConditionIds": [item for item in required_failures if item],
        }
    if negative_matches:
        return {
            "status": "impossible",
            "reason": "Active Manifest satisfies a RuleBox negative condition that must be absent.",
            "failedConditionIds": ["not:" + item for item in negative_matches],
        }
    if any_conditions:
        raw_minimum = getattr(rule, "any_condition_min_count", None)
        if raw_minimum is None and isinstance(rule, dict):
            raw_minimum = rule.get("any_condition_min_count") or rule.get("anyConditionMinCount")
        minimum = max(1, int(number_or_none(raw_minimum) or 1))
        possible_count = 0
        unknown_count = 0
        for condition in any_conditions:
            verdict = condition_verdict(condition)
            if verdict is True:
                possible_count += 1
            elif verdict is None:
                unknown_count += 1
        if possible_count + unknown_count < minimum:
            return {
                "status": "impossible",
                "reason": "Active Manifest cannot satisfy the RuleBox any-condition minimum.",
                "failedConditionIds": ["any-group"],
                "anyConditionMinimum": minimum,
                "anyConditionPossibleCount": possible_count,
                "anyConditionUnknownCount": unknown_count,
            }
    return {
        "status": "unknown" if unknown else "possible",
        "reason": "" if not unknown else "Some indexed Manifest evidence remains unknown.",
        "failedConditionIds": [],
    }
