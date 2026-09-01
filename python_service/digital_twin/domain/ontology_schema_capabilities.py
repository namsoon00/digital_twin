"""Rule-derived capability contract for the physical ontology schema.

The logical TBox intentionally contains a broad investment vocabulary.  The
physical TypeDB schema must not make every logical class inherit every query
attribute, however.  This module derives the small set of fields that the
active native RuleBox is allowed to bind directly.  Display-only properties
remain in the immutable JSON payload and can be promoted by a later schema
release when an approved rule starts querying them.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Dict, Iterable, Mapping, Sequence, Set, Tuple

from .ontology_rulebox_contracts import GraphInferenceRule
from .ontology_tbox import tbox_relation_def


RULE_DERIVED_SCHEMA_CONTRACT_VERSION = "rule-derived-schema-capabilities-v1"


def _text(value: object) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class RuleSchemaCapabilityManifest:
    rule_ids: Tuple[str, ...]
    subject_fields: Tuple[str, ...]
    target_fields_by_context: Mapping[str, Tuple[str, ...]]
    relation_fields: Tuple[str, ...]
    fingerprint: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "contractVersion": RULE_DERIVED_SCHEMA_CONTRACT_VERSION,
            "ruleIds": list(self.rule_ids),
            "subjectFields": list(self.subject_fields),
            "targetFieldsByContext": {
                key: list(values)
                for key, values in sorted(self.target_fields_by_context.items())
            },
            "relationFields": list(self.relation_fields),
            "fingerprint": self.fingerprint,
        }


def _condition_value(condition: object, snake: str, camel: str, default=None):
    if isinstance(condition, Mapping):
        return condition.get(snake, condition.get(camel, default))
    return getattr(condition, snake, default)


def _rule_value(rule: object, snake: str, camel: str, default=None):
    if isinstance(rule, Mapping):
        return rule.get(snake, rule.get(camel, default))
    return getattr(rule, snake, default)


def rule_schema_capability_manifest(
    rules: Sequence[GraphInferenceRule] | Iterable[object],
) -> RuleSchemaCapabilityManifest:
    """Return the exact direct-query field surface of one immutable RuleBox."""
    rule_ids: Set[str] = set()
    subject_fields: Set[str] = set()
    target_fields_by_context: Dict[str, Set[str]] = {}
    relation_fields: Set[str] = set()

    for rule in rules or []:
        if _rule_value(rule, "enabled", "enabled", True) is False:
            continue
        rule_id = _text(_rule_value(rule, "rule_id", "ruleId", ""))
        if rule_id:
            rule_ids.add(rule_id)
        conditions = _rule_value(rule, "conditions", "conditions", []) or []
        for condition in conditions:
            kind = _text(_condition_value(condition, "kind", "kind", ""))
            field = _text(_condition_value(condition, "field", "field", ""))
            if kind == "subject_property" and field:
                subject_fields.add(field)
            if kind != "relation":
                continue
            relation_type = _text(
                _condition_value(condition, "relation_type", "relationType", "")
            ).upper()
            definition = tbox_relation_def(relation_type)
            target_context = _text(definition.target_context if definition else "") or "observation-data"
            target_filters = dict(
                _condition_value(
                    condition,
                    "target_property_filters",
                    "targetPropertyFilters",
                    {},
                )
                or {}
            )
            target_fields_by_context.setdefault(target_context, set()).update(
                _text(key) for key in target_filters if _text(key)
            )
            relation_filters = dict(
                _condition_value(
                    condition,
                    "relation_property_filters",
                    "relationPropertyFilters",
                    {},
                )
                or {}
            )
            relation_fields.update(_text(key) for key in relation_filters if _text(key))

    material = {
        "contractVersion": RULE_DERIVED_SCHEMA_CONTRACT_VERSION,
        "ruleIds": sorted(rule_ids),
        "subjectFields": sorted(subject_fields),
        "targetFieldsByContext": {
            key: sorted(values)
            for key, values in sorted(target_fields_by_context.items())
        },
        "relationFields": sorted(relation_fields),
    }
    fingerprint = hashlib.sha256(
        json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return RuleSchemaCapabilityManifest(
        rule_ids=tuple(material["ruleIds"]),
        subject_fields=tuple(material["subjectFields"]),
        target_fields_by_context={
            key: tuple(values)
            for key, values in material["targetFieldsByContext"].items()
        },
        relation_fields=tuple(material["relationFields"]),
        fingerprint=fingerprint,
    )
