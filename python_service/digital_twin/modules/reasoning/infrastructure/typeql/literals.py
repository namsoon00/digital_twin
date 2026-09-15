"""Literals for TypeQL, without database execution."""

import json
import math
from decimal import Decimal, InvalidOperation

from digital_twin.infrastructure.graph_store_payloads import number_or_none
from digital_twin.modules.reasoning.infrastructure.typeql.constants import (
    TYPEDB_NUMERIC_ATTRIBUTES,
    TYPEDB_STRING_ATTRIBUTES,
)


def typedb_string(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def typedb_number(value: object):
    parsed = number_or_none(value)
    if parsed is None:
        return None
    numeric = float(parsed)
    return numeric if math.isfinite(numeric) else None


def typedb_number_literal(value: object) -> str:
    """Return a TypeQL-compatible finite double literal.

    Python renders small floats using scientific notation (for example
    ``6e-05``), but TypeQL accepts fixed-point numeric literals only. Keeping
    this conversion at the TypeDB boundary prevents one small market metric
    from invalidating an entire ABox generation.
    """

    numeric = typedb_number(value)
    if numeric is None:
        return ""
    try:
        literal = format(Decimal(str(numeric)), "f")
    except (InvalidOperation, ValueError):
        literal = format(numeric, ".17f")
    if "." not in literal:
        literal += ".0"
    if literal in {"-0", "-0.0"}:
        return "0.0"
    return literal


def typedb_value_match(owner_var: str, attribute: str, expected: object, operator: str, value_var: str,
                      expected_attribute: str = "") -> str:
    op = str(operator or "==").strip().lower()
    if isinstance(expected, dict) and expected.get("field"):
        if not expected_attribute:
            raise ValueError("unresolved TypeQL field reference: " + str(expected["field"]))
        typeql_op = {"eq": "==", "in": "==", "ne": "!=", "lte": "<=", "gte": ">=", "lt": "<", "gt": ">"}.get(op, op)
        if typeql_op not in {"==", "!=", "<=", ">=", "<", ">"}:
            raise ValueError("unsupported TypeQL field comparison: " + op)
        actual = "$" + value_var
        reference = actual + "Expected"
        bound = owner_var + " has " + attribute + " " + actual + "; "
        comparison = owner_var + " has " + expected_attribute + " " + reference + "; " + actual + " " + typeql_op + " " + reference + ";"
        default = expected.get("default")
        if default in (None, "", [], {}):
            return bound + comparison
        # Absence, not a failed comparison, is the only default branch.
        absent = "not { " + owner_var + " has " + expected_attribute + " " + reference + "Absent; }; "
        return (bound + "{ " + comparison + " } or { " + absent
                + actual + " " + typeql_op + " " + typedb_literal_for_attribute(attribute, default) + "; };")
    expected = typedb_expected_value(expected)
    if op in {"exists", "present"}:
        return owner_var + " has " + attribute + " $" + value_var + ";"
    if isinstance(expected, list):
        values = [item for item in expected if item not in (None, "", [], {})]
        if not values:
            return ""
        return " or ".join(["{ " + owner_var + " has " + attribute + " " + typedb_literal_for_attribute(attribute, value) + "; }" for value in values]) + ";"
    if expected in (None, "", [], {}):
        return ""
    if op in {"==", "eq", "in"}:
        return owner_var + " has " + attribute + " " + typedb_literal_for_attribute(attribute, expected) + ";"
    if op in {"!=", "ne", "<=", "lte", ">=", "gte", "<", "lt", ">", "gt"}:
        typeql_op = {"ne": "!=", "lte": "<=", "gte": ">=", "lt": "<", "gt": ">"}.get(op, op)
        return owner_var + " has " + attribute + " $" + value_var + "; $" + value_var + " " + typeql_op + " " + typedb_literal_for_attribute(attribute, expected) + ";"
    return owner_var + " has " + attribute + " " + typedb_literal_for_attribute(attribute, expected) + ";"


def typedb_literal_for_attribute(attribute: str, value: object) -> str:
    value = typedb_expected_value(value)
    if attribute in TYPEDB_STRING_ATTRIBUTES:
        return typedb_string("true" if value is True else "false" if value is False else value)
    if attribute in TYPEDB_NUMERIC_ATTRIBUTES:
        literal = typedb_number_literal(value)
        if literal and not isinstance(value, bool):
            return literal
    return typedb_literal(value)


def typedb_literal(value: object) -> str:
    value = typedb_expected_value(value)
    literal = typedb_number_literal(value)
    if literal and not isinstance(value, bool):
        return literal
    return typedb_string("true" if value is True else "false" if value is False else value)


def typedb_expected_value(value: object) -> object:
    if isinstance(value, dict):
        if value.get("field"):
            raise ValueError("unresolved TypeQL field reference: " + str(value["field"]))
        if value.get("default") not in (None, "", [], {}):
            return value.get("default")
        if value.get("value") not in (None, "", [], {}):
            return value.get("value")
        return ""
    return value
