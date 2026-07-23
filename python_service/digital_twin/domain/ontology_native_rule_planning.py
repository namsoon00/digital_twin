"""Immutable ABox topology hints for bounded native-rule scheduling.

The topology is produced from the exact graph that is persisted as the ABox.
It may exclude a TypeDB function that cannot have a required relation type,
but it never evaluates a rule condition or reports an investment judgement.
"""

import hashlib
import json
from typing import Dict, Iterable, List, Mapping, Set

from .ontology_contracts import PortfolioOntology


NATIVE_RULE_PLANNER_TOPOLOGY_VERSION = "native-rule-planner-topology-v1"


def _clean_symbol(value: object) -> str:
    return str(value or "").upper().strip()


def _normalized_relation_types(values: Iterable[object]) -> List[str]:
    return sorted({
        str(value or "").upper().strip()
        for value in values or []
        if str(value or "").strip()
    })


def _topology_fingerprint(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(dict(payload or {}), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return "native-rule-topology:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def native_rule_planner_topology(graph: PortfolioOntology) -> Dict[str, object]:
    """Return the relation-type index for stock subjects in one ABox graph.

    This is deliberately a structural index. The complete rule payload and
    every numeric/text/negative condition remain TypeDB schema-function work.
    """
    source_ids_by_symbol: Dict[str, Set[str]] = {}
    symbol_by_entity_id: Dict[str, str] = {}
    for entity in list(getattr(graph, "entities", []) or []):
        if str(getattr(entity, "kind", "") or "") != "stock":
            continue
        properties = dict(getattr(entity, "properties", {}) or {})
        symbol = _clean_symbol(properties.get("symbol"))
        entity_id = str(getattr(entity, "entity_id", "") or "").strip()
        if not symbol or not entity_id:
            continue
        source_ids_by_symbol.setdefault(symbol, set()).add(entity_id)
        symbol_by_entity_id[entity_id] = symbol

    relation_types_by_symbol: Dict[str, Set[str]] = {
        symbol: set()
        for symbol in source_ids_by_symbol
    }
    for relation in list(getattr(graph, "relations", []) or []):
        relation_type = str(getattr(relation, "relation_type", "") or "").upper().strip()
        if not relation_type:
            continue
        properties = dict(getattr(relation, "properties", {}) or {})
        candidates = {
            symbol_by_entity_id.get(str(getattr(relation, "source", "") or ""), ""),
            symbol_by_entity_id.get(str(getattr(relation, "target", "") or ""), ""),
            _clean_symbol(properties.get("symbol")),
        }
        for symbol in candidates:
            if symbol and symbol in relation_types_by_symbol:
                relation_types_by_symbol[symbol].add(relation_type)

    payload = {
        "version": NATIVE_RULE_PLANNER_TOPOLOGY_VERSION,
        "complete": True,
        "source": "projection-graph",
        "sourceIdsBySymbol": {
            symbol: sorted(values)
            for symbol, values in sorted(source_ids_by_symbol.items())
        },
        "relationTypesBySymbol": {
            symbol: _normalized_relation_types(values)
            for symbol, values in sorted(relation_types_by_symbol.items())
        },
    }
    return {
        **payload,
        "fingerprint": _topology_fingerprint(payload),
    }


def native_rule_planner_manifest_fingerprint(
    material_fingerprint: object,
    topology: Mapping[str, object],
) -> str:
    """Bind the active-manifest contract to its verified planner topology."""
    normalized = normalize_native_rule_planner_topology(topology)
    if str(normalized.get("status") or "") != "ok":
        raise ValueError(str(normalized.get("reason") or "Native rule planner topology is invalid."))
    seed = "|".join([
        "native-rule-planner-manifest-v1",
        str(material_fingerprint or ""),
        str(normalized.get("fingerprint") or ""),
    ])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def normalize_native_rule_planner_topology(
    value: Mapping[str, object] = None,
    target_symbols: Iterable[object] = None,
) -> Dict[str, object]:
    """Validate a persisted topology hint before native scheduling uses it."""
    raw = dict(value or {}) if isinstance(value, Mapping) else {}
    if str(raw.get("version") or "") != NATIVE_RULE_PLANNER_TOPOLOGY_VERSION:
        return {"status": "invalid", "reason": "Native rule planner topology version is unsupported."}
    if raw.get("complete") is not True or str(raw.get("source") or "") != "projection-graph":
        return {"status": "invalid", "reason": "Native rule planner topology is not a complete projection graph index."}
    raw_sources = raw.get("sourceIdsBySymbol") if isinstance(raw.get("sourceIdsBySymbol"), Mapping) else {}
    raw_relations = raw.get("relationTypesBySymbol") if isinstance(raw.get("relationTypesBySymbol"), Mapping) else {}
    source_ids_by_symbol: Dict[str, List[str]] = {}
    relation_types_by_symbol: Dict[str, List[str]] = {}
    symbols = set()
    for raw_symbol, raw_values in raw_sources.items():
        symbol = _clean_symbol(raw_symbol)
        if not symbol:
            continue
        values = sorted({str(item or "").strip() for item in (raw_values or []) if str(item or "").strip()})
        if values:
            source_ids_by_symbol[symbol] = values
            symbols.add(symbol)
    for raw_symbol, raw_values in raw_relations.items():
        symbol = _clean_symbol(raw_symbol)
        if not symbol:
            continue
        relation_types_by_symbol[symbol] = _normalized_relation_types(raw_values or [])
        symbols.add(symbol)
    for symbol in symbols:
        source_ids_by_symbol.setdefault(symbol, [])
        relation_types_by_symbol.setdefault(symbol, [])
    payload = {
        "version": NATIVE_RULE_PLANNER_TOPOLOGY_VERSION,
        "complete": True,
        "source": "projection-graph",
        "sourceIdsBySymbol": {
            symbol: source_ids_by_symbol[symbol]
            for symbol in sorted(symbols)
        },
        "relationTypesBySymbol": {
            symbol: relation_types_by_symbol[symbol]
            for symbol in sorted(symbols)
        },
    }
    fingerprint = _topology_fingerprint(payload)
    if str(raw.get("fingerprint") or "") != fingerprint:
        return {"status": "invalid", "reason": "Native rule planner topology fingerprint does not match its contents."}
    requested = {_clean_symbol(symbol) for symbol in target_symbols or [] if _clean_symbol(symbol)}
    selected = sorted(requested) if requested else sorted(symbols)
    return {
        "status": "ok",
        **payload,
        "fingerprint": fingerprint,
        "symbols": selected,
        "sourceIdsBySymbol": {
            symbol: list(payload["sourceIdsBySymbol"].get(symbol, []))
            for symbol in selected
        },
        "relationTypesBySymbol": {
            symbol: list(payload["relationTypesBySymbol"].get(symbol, []))
            for symbol in selected
        },
    }
