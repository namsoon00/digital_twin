"""Rule shape for TypeQL, without database execution."""

from typing import Dict, Iterable, List


def clean_symbols_from_payload(value: object) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    elif value in (None, ""):
        raw_values = []
    else:
        raw_values = [value]
    return sorted(set(str(item or "").upper().strip() for item in raw_values if str(item or "").strip()))


def typedb_native_rule_id(rule_id: object) -> str:
    value = str(rule_id or "").strip()
    if not value:
        return ""
    if value.startswith("typedb.native."):
        return value
    return "typedb.native." + value


def normalized_condition_role(condition: Dict[str, object]) -> str:
    role = str(condition.get("role") or condition.get("conditionRole") or "required").strip().lower()
    return role if role in {"required", "any", "optional", "not"} else "required"


def typedb_rule_condition_payloads(rule: object) -> List[Dict[str, object]]:
    """Normalize persisted RuleBox conditions for execution planning only."""
    raw_conditions = getattr(rule, "conditions", None)
    if raw_conditions is None and isinstance(rule, dict):
        raw_conditions = rule.get("conditions") or []
    payloads: List[Dict[str, object]] = []
    for condition in raw_conditions or []:
        if hasattr(condition, "to_dict"):
            payload = condition.to_dict()
        elif isinstance(condition, dict):
            payload = dict(condition)
        else:
            continue
        payloads.append(payload)
    return payloads


def typedb_source_kind_uses_symbol_scope(source_kind: object) -> bool:
    """Return whether a RuleBox source is addressed by an instrument symbol."""

    return str(source_kind or "").strip().lower() not in {"portfolio", "account"}


def typedb_planned_candidate_symbols(
    planned: Dict[str, object],
    fallback_symbols: Iterable[str],
) -> List[str]:
    """Preserve an explicit empty scope for portfolio/account rule sources."""

    if isinstance(planned, dict) and "candidateSymbols" in planned:
        return clean_symbols_from_payload(planned.get("candidateSymbols") or [])
    return clean_symbols_from_payload(fallback_symbols)


def typedb_rule_is_enabled(rule: object) -> bool:
    """Return whether a stored RuleBox row belongs to the executable slice."""

    if isinstance(rule, dict):
        return rule.get("enabled") is not False
    return bool(getattr(rule, "enabled", True))


def symbol_from_subject(value: object) -> str:
    raw = str(value or "")
    if raw.startswith(("stock:", "crypto-asset:")):
        return raw.split(":", 1)[1].upper()
    return ""
