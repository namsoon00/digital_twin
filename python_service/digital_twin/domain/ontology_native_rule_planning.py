"""Immutable ABox topology hints for bounded native-rule scheduling.

The topology is produced from the exact graph that is persisted as the ABox.
It may exclude a TypeDB function that cannot have a required relation type,
but it never evaluates a rule condition or reports an investment judgement.
"""

import hashlib
import json
import math
from typing import Dict, Iterable, List, Mapping, Set

from .ontology_contracts import PortfolioOntology


NATIVE_RULE_PLANNER_TOPOLOGY_VERSION = "native-rule-planner-topology-v2-model-contracts"

# These are the raw source attributes referenced by the current TypeDB
# direct-TypeQL RuleBox. Persisting this bounded negative-planning index
# avoids opening an expensive function when the exact ABox source plainly
# contradicts a required condition.  It never contains derived judgements and
# never proves a rule match; TypeDB remains the sole rule evaluator.
NATIVE_RULE_PLANNER_SUBJECT_PROPERTY_FIELDS = {
    "bidAskImbalance",
    "foreignNetVolume",
    "individualNetVolume",
    "institutionNetVolume",
    "investmentStrategyProfile",
    "ma20Distance",
    "ma5Distance",
    "ma60Distance",
    "positionAccountWeight",
    "positionRole",
    "priceChangeRate",
    "profitLossRate",
    "smartMoneyNetVolume",
    "source",
    "timeAdjustedVolumeRatio",
    "tradeStrength",
    "volumeRatio",
}

# Relation evidence keeps only scalar fields that current and future RuleBox
# filters can inspect. A missing field remains unknown during planning, so an
# omitted value can never create a false negative rule decision.
NATIVE_RULE_PLANNER_RELATION_TARGET_PROPERTY_FIELDS = {
    "allowAddOnStrength",
    "avoidAveragingDown",
    "beta",
    "cashConversionPct",
    "change24h",
    "change7d",
    "changeRate",
    "classification",
    "consecutiveDeclineCount",
    "conservativeMarginOfSafetyPct",
    "correlationPolicyDelta",
    "correspondence",
    "coverageRatio",
    "cryptoSymbol",
    "dataScope",
    "dataState",
    "decisionEligibility",
    "debtToEquityPct",
    "delta",
    "delta5dBp",
    "deltaPct",
    "drawdownFromPeakPct",
    "drawdownPolicyDeltaPct",
    "documentAnalysisState",
    "documentVerificationState",
    "evidenceEligibilityState",
    "eventType",
    "factor",
    "field",
    "forwardPE",
    "freeCashFlowGrowthPct",
    "freeCashFlowMarginPct",
    "group",
    "hasSufficientHistory",
    "hypothesisContractId",
    "hypothesisFamilyId",
    "impactPolarity",
    "increaseCount20d",
    "instrumentArchetype",
    "levelType",
    "eligibilityStatus",
    "ma20Distance",
    "ma60Distance",
    "marginOfSafetyPct",
    "materialityPassed",
    "materialityState",
    "needsReview",
    "operatingIncomeGrowthPct",
    "operatingMarginPct",
    "pair",
    "pbr",
    "peRatio",
    "peakReturnPct",
    "polarity",
    "policyDeltaRatio",
    "previousValue",
    "priceChangePct",
    "priceVelocityChangePct",
    "priorPriceChangePct",
    "proxyChangeRate",
    "rateSeriesId",
    "readScope",
    "reboundFromTroughPct",
    "recentPriceChangePct",
    "reentered",
    "relationScope",
    "relativeReturnPct",
    "returnOnEquityPct",
    "revenueGrowthPct",
    "riskEventCount",
    "releaseId",
    "sensitivityLevel",
    "sharesOutstandingGrowthPct",
    "smartMoneyDistinctObservationCount",
    "smartMoneyNetChange",
    "smartMoneyNetLatest",
    "smartMoneyObservationCount",
    "sourceTrustState",
    "signalType",
    "staleObservationCount",
    "strengthBand",
    "supportEventCount",
    "surprisePercentage",
    "trailingEPS",
    "trimOnTrendBreak",
    "troughReturnPct",
    "validObservationRatio",
    "valuationDataState",
    "validationStatus",
    "valuationDecisionEligible",
    "value",
    "volatilityPolicyDeltaPct",
    "windowKey",
}
NATIVE_RULE_PLANNER_RELATION_PROPERTY_FIELDS = {
    "evidenceRole",
    "transitionType",
    "weight",
}
NATIVE_RULE_PLANNER_RELATION_EVIDENCE_LIMIT_PER_SYMBOL = 256


def _clean_symbol(value: object) -> str:
    return str(value or "").upper().strip()


def _native_subject_key(entity: object) -> str:
    kind = str(getattr(entity, "kind", "") or "")
    properties = dict(getattr(entity, "properties", {}) or {})
    if kind in {"stock", "crypto-asset"}:
        return _clean_symbol(properties.get("symbol"))
    if kind == "portfolio":
        return _clean_symbol(getattr(entity, "entity_id", ""))
    return ""


def _normalized_relation_types(values: Iterable[object]) -> List[str]:
    return sorted({
        str(value or "").upper().strip()
        for value in values or []
        if str(value or "").strip()
    })


def _planner_property_value(value: object):
    """Return a small JSON-safe scalar/list value or ``None`` when unsafe."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if not isinstance(value, float) or math.isfinite(value) else None
    if isinstance(value, str):
        return value[:240]
    if isinstance(value, (list, tuple, set)):
        values = [
            normalized
            for item in list(value)[:24]
            for normalized in [_planner_property_value(item)]
            if normalized is not None and not isinstance(normalized, list)
        ]
        return values
    return None


def _planner_subject_properties(entity: object, symbol: str) -> Dict[str, object]:
    properties = dict(getattr(entity, "properties", {}) or {})
    nested = properties.get("properties")
    if isinstance(nested, Mapping):
        properties.update(dict(nested))
    properties.setdefault("symbol", symbol)
    properties.setdefault("kind", str(getattr(entity, "kind", "") or ""))
    result: Dict[str, object] = {}
    for field in sorted(NATIVE_RULE_PLANNER_SUBJECT_PROPERTY_FIELDS | {
        "kind", "ontologyBox", "symbol", "tboxClass",
    }):
        normalized = _planner_property_value(properties.get(field))
        if normalized is not None:
            result[field] = normalized
    return result


def _normalized_subject_properties(value: object) -> Dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    result: Dict[str, object] = {}
    for field, raw_value in value.items():
        clean_field = str(field or "").strip()
        if clean_field not in NATIVE_RULE_PLANNER_SUBJECT_PROPERTY_FIELDS | {
            "kind", "ontologyBox", "symbol", "tboxClass",
        }:
            continue
        normalized = _planner_property_value(raw_value)
        if normalized is not None:
            result[clean_field] = normalized
    return result


def _planner_filtered_properties(
    value: object,
    allowed_fields: Set[str],
) -> Dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    properties = dict(value)
    nested = properties.get("properties")
    if isinstance(nested, Mapping):
        properties.update(dict(nested))
    result: Dict[str, object] = {}
    for field in sorted(allowed_fields):
        normalized = _planner_property_value(properties.get(field))
        if normalized is not None:
            result[field] = normalized
    return result


def _normalized_relation_evidence_entry(value: object) -> Dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    relation_type = str(value.get("relationType") or "").upper().strip()
    direction = str(value.get("direction") or "out").lower().strip()
    target_kind = str(value.get("targetKind") or "").strip()
    if not relation_type or direction not in {"in", "out"} or not target_kind:
        return {}
    return {
        "relationType": relation_type,
        "direction": direction,
        "targetKind": target_kind,
        "targetProperties": _planner_filtered_properties(
            value.get("targetProperties"),
            NATIVE_RULE_PLANNER_RELATION_TARGET_PROPERTY_FIELDS,
        ),
        "relationProperties": _planner_filtered_properties(
            value.get("relationProperties"),
            NATIVE_RULE_PLANNER_RELATION_PROPERTY_FIELDS,
        ),
    }


def _relation_evidence_for_subject(
    graph: PortfolioOntology,
    subject_id: str,
) -> Dict[str, object]:
    entities_by_id = {
        str(getattr(entity, "entity_id", "") or ""): entity
        for entity in list(getattr(graph, "entities", []) or [])
        if str(getattr(entity, "entity_id", "") or "")
    }
    entries: List[Dict[str, object]] = []
    complete = True
    for relation in list(getattr(graph, "relations", []) or []):
        source_id = str(getattr(relation, "source", "") or "")
        target_id = str(getattr(relation, "target", "") or "")
        if source_id == subject_id:
            direction, endpoint_id = "out", target_id
        elif target_id == subject_id:
            direction, endpoint_id = "in", source_id
        else:
            continue
        endpoint = entities_by_id.get(endpoint_id)
        if endpoint is None:
            complete = False
            continue
        entry = _normalized_relation_evidence_entry({
            "relationType": str(getattr(relation, "relation_type", "") or ""),
            "direction": direction,
            "targetKind": str(getattr(endpoint, "kind", "") or ""),
            "targetProperties": dict(getattr(endpoint, "properties", {}) or {}),
            "relationProperties": {
                **dict(getattr(relation, "properties", {}) or {}),
                "weight": getattr(relation, "weight", None),
            },
        })
        if entry:
            entries.append(entry)
    entries.sort(key=lambda item: json.dumps(item, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    if len(entries) > NATIVE_RULE_PLANNER_RELATION_EVIDENCE_LIMIT_PER_SYMBOL:
        complete = False
        entries = entries[:NATIVE_RULE_PLANNER_RELATION_EVIDENCE_LIMIT_PER_SYMBOL]
    return {"complete": complete, "entries": entries}


def _topology_fingerprint(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(dict(payload or {}), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return "native-rule-topology:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def native_rule_planner_topology(graph: PortfolioOntology) -> Dict[str, object]:
    """Return the relation-type index for native RuleBox subjects in one ABox graph.

    This is deliberately a structural index. The complete rule payload and
    every numeric/text/negative condition remains direct TypeQL work.
    """
    source_ids_by_symbol: Dict[str, Set[str]] = {}
    subject_entities_by_symbol: Dict[str, List[object]] = {}
    symbol_by_entity_id: Dict[str, str] = {}
    for entity in list(getattr(graph, "entities", []) or []):
        symbol = _native_subject_key(entity)
        entity_id = str(getattr(entity, "entity_id", "") or "").strip()
        if not symbol or not entity_id:
            continue
        source_ids_by_symbol.setdefault(symbol, set()).add(entity_id)
        subject_entities_by_symbol.setdefault(symbol, []).append(entity)
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

    relation_evidence_by_symbol: Dict[str, List[Dict[str, object]]] = {}
    relation_evidence_complete_by_symbol: Dict[str, bool] = {}
    for symbol, entities in sorted(subject_entities_by_symbol.items()):
        if len(entities) != 1:
            relation_evidence_complete_by_symbol[symbol] = False
            continue
        evidence = _relation_evidence_for_subject(
            graph,
            str(getattr(entities[0], "entity_id", "") or ""),
        )
        relation_evidence_by_symbol[symbol] = list(evidence.get("entries") or [])
        relation_evidence_complete_by_symbol[symbol] = bool(evidence.get("complete"))

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
        # A property entry is emitted only for an unambiguous source subject.
        # Duplicate source entities remain unknown and are never pre-pruned.
        "subjectPropertiesBySymbol": {
            symbol: _planner_subject_properties(entities[0], symbol)
            for symbol, entities in sorted(subject_entities_by_symbol.items())
            if len(entities) == 1
        },
        "relationEvidenceBySymbol": relation_evidence_by_symbol,
        "relationEvidenceCompleteBySymbol": relation_evidence_complete_by_symbol,
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
    subject_property_index_available = (
        bool(raw.get("subjectPropertyIndexAvailable"))
        if "subjectPropertyIndexAvailable" in raw
        else "subjectPropertiesBySymbol" in raw
    )
    raw_subject_properties = (
        raw.get("subjectPropertiesBySymbol")
        if isinstance(raw.get("subjectPropertiesBySymbol"), Mapping)
        else {}
    )
    relation_evidence_index_available = (
        bool(raw.get("relationEvidenceIndexAvailable"))
        if "relationEvidenceIndexAvailable" in raw
        else (
            "relationEvidenceBySymbol" in raw
            and "relationEvidenceCompleteBySymbol" in raw
        )
    )
    raw_relation_evidence = (
        raw.get("relationEvidenceBySymbol")
        if isinstance(raw.get("relationEvidenceBySymbol"), Mapping)
        else {}
    )
    raw_relation_evidence_complete = (
        raw.get("relationEvidenceCompleteBySymbol")
        if isinstance(raw.get("relationEvidenceCompleteBySymbol"), Mapping)
        else {}
    )
    source_ids_by_symbol: Dict[str, List[str]] = {}
    relation_types_by_symbol: Dict[str, List[str]] = {}
    subject_properties_by_symbol: Dict[str, Dict[str, object]] = {}
    relation_evidence_by_symbol: Dict[str, List[Dict[str, object]]] = {}
    relation_evidence_complete_by_symbol: Dict[str, bool] = {}
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
    if subject_property_index_available:
        for raw_symbol, raw_values in raw_subject_properties.items():
            symbol = _clean_symbol(raw_symbol)
            if not symbol or not isinstance(raw_values, Mapping):
                continue
            subject_properties_by_symbol[symbol] = _normalized_subject_properties(raw_values)
    if relation_evidence_index_available:
        for raw_symbol, raw_values in raw_relation_evidence.items():
            symbol = _clean_symbol(raw_symbol)
            if not symbol or not isinstance(raw_values, (list, tuple)):
                continue
            entries = [
                normalized
                for item in list(raw_values)[:NATIVE_RULE_PLANNER_RELATION_EVIDENCE_LIMIT_PER_SYMBOL]
                for normalized in [_normalized_relation_evidence_entry(item)]
                if normalized
            ]
            entries.sort(key=lambda item: json.dumps(item, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
            relation_evidence_by_symbol[symbol] = entries
            relation_evidence_complete_by_symbol[symbol] = bool(
                raw_relation_evidence_complete.get(raw_symbol)
                if raw_symbol in raw_relation_evidence_complete
                else raw_relation_evidence_complete.get(symbol)
            ) and len(raw_values) <= NATIVE_RULE_PLANNER_RELATION_EVIDENCE_LIMIT_PER_SYMBOL
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
    # Preserve fingerprints written before the optional property index was
    # introduced. Their missing index means unknown, never an empty source.
    if subject_property_index_available:
        payload["subjectPropertiesBySymbol"] = {
            symbol: subject_properties_by_symbol[symbol]
            for symbol in sorted(subject_properties_by_symbol)
            if symbol in symbols
        }
    if relation_evidence_index_available:
        payload["relationEvidenceBySymbol"] = {
            symbol: relation_evidence_by_symbol.get(symbol, [])
            for symbol in sorted(symbols)
        }
        payload["relationEvidenceCompleteBySymbol"] = {
            symbol: bool(relation_evidence_complete_by_symbol.get(symbol))
            for symbol in sorted(symbols)
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
        "subjectPropertiesBySymbol": {
            symbol: dict(payload.get("subjectPropertiesBySymbol", {}).get(symbol) or {})
            for symbol in selected
            if symbol in payload.get("subjectPropertiesBySymbol", {})
        },
        "subjectPropertyIndexAvailable": subject_property_index_available,
        "relationEvidenceBySymbol": {
            symbol: list(payload.get("relationEvidenceBySymbol", {}).get(symbol) or [])
            for symbol in selected
            if symbol in payload.get("relationEvidenceBySymbol", {})
        },
        "relationEvidenceCompleteBySymbol": {
            symbol: bool(payload.get("relationEvidenceCompleteBySymbol", {}).get(symbol))
            for symbol in selected
            if symbol in payload.get("relationEvidenceCompleteBySymbol", {})
        },
        "relationEvidenceIndexAvailable": relation_evidence_index_available,
    }


def merge_native_rule_planner_topology(
    active_topology: Mapping[str, object] = None,
    incoming_topology: Mapping[str, object] = None,
    replacement_symbols: Iterable[object] = None,
) -> Dict[str, object]:
    """Merge a target-scoped topology into the active Manifest topology.

    A target-scoped ABox projection only contains the symbols selected for the
    current mailbox turn.  Its structural index must therefore replace those
    symbols while retaining every other active Manifest subject.  This is a
    persistence contract only; it does not inspect rule conditions or choose
    an investment outcome.
    """
    active = normalize_native_rule_planner_topology(active_topology)
    incoming = normalize_native_rule_planner_topology(incoming_topology)
    if str(incoming.get("status") or "") != "ok":
        return {
            "status": "invalid-incoming",
            "reason": str(incoming.get("reason") or "Incoming planner topology is invalid."),
            "topology": {},
            "replacedSymbols": [],
            "retainedSymbols": [],
        }
    if str(active.get("status") or "") != "ok":
        return {
            "status": "active-unavailable",
            "reason": str(active.get("reason") or "Active planner topology is unavailable."),
            "topology": {},
            "replacedSymbols": [],
            "retainedSymbols": [],
        }

    requested = {
        _clean_symbol(symbol)
        for symbol in replacement_symbols or []
        if _clean_symbol(symbol)
    }
    active_sources = dict(active.get("sourceIdsBySymbol") or {})
    active_relations = dict(active.get("relationTypesBySymbol") or {})
    active_properties = dict(active.get("subjectPropertiesBySymbol") or {})
    active_evidence = dict(active.get("relationEvidenceBySymbol") or {})
    active_evidence_complete = dict(active.get("relationEvidenceCompleteBySymbol") or {})
    incoming_sources = dict(incoming.get("sourceIdsBySymbol") or {})
    incoming_relations = dict(incoming.get("relationTypesBySymbol") or {})
    incoming_properties = dict(incoming.get("subjectPropertiesBySymbol") or {})
    incoming_evidence = dict(incoming.get("relationEvidenceBySymbol") or {})
    incoming_evidence_complete = dict(incoming.get("relationEvidenceCompleteBySymbol") or {})
    incoming_symbols = set(incoming_sources) | set(incoming_relations) | set(incoming_evidence)

    # A partial input may intentionally omit a source while it waits for a
    # follow-up observation.  Do not erase the active source in that case.
    # An explicit retirement is handled by the scoped Manifest plan instead.
    replaced = {
        symbol
        for symbol in incoming_symbols
        if not requested or symbol in requested or symbol not in active_sources
    }
    merged_sources = {
        symbol: list(values or [])
        for symbol, values in active_sources.items()
        if symbol not in replaced
    }
    merged_relations = {
        symbol: list(values or [])
        for symbol, values in active_relations.items()
        if symbol not in replaced
    }
    merged_properties = {
        symbol: dict(values or {})
        for symbol, values in active_properties.items()
        if symbol not in replaced
    }
    merged_evidence = {
        symbol: list(values or [])
        for symbol, values in active_evidence.items()
        if symbol not in replaced
    }
    merged_evidence_complete = {
        symbol: bool(value)
        for symbol, value in active_evidence_complete.items()
        if symbol not in replaced
    }
    for symbol in replaced:
        merged_sources[symbol] = list(incoming_sources.get(symbol) or [])
        merged_relations[symbol] = list(incoming_relations.get(symbol) or [])
        if symbol in incoming_properties:
            merged_properties[symbol] = dict(incoming_properties.get(symbol) or {})
        else:
            merged_properties.pop(symbol, None)
        if symbol in incoming_evidence:
            merged_evidence[symbol] = list(incoming_evidence.get(symbol) or [])
            merged_evidence_complete[symbol] = bool(
                incoming_evidence_complete.get(symbol)
            )
        else:
            merged_evidence.pop(symbol, None)
            merged_evidence_complete[symbol] = False

    payload = {
        "version": NATIVE_RULE_PLANNER_TOPOLOGY_VERSION,
        "complete": True,
        "source": "projection-graph",
        "sourceIdsBySymbol": {
            symbol: sorted({str(value or "").strip() for value in values or [] if str(value or "").strip()})
            for symbol, values in sorted(merged_sources.items())
        },
        "relationTypesBySymbol": {
            symbol: _normalized_relation_types(merged_relations.get(symbol) or [])
            for symbol in sorted(merged_sources)
        },
    }
    if bool(active.get("subjectPropertyIndexAvailable")) or bool(
        incoming.get("subjectPropertyIndexAvailable")
    ):
        payload["subjectPropertiesBySymbol"] = {
            symbol: _normalized_subject_properties(values)
            for symbol, values in sorted(merged_properties.items())
            if symbol in merged_sources
        }
    if bool(active.get("relationEvidenceIndexAvailable")) or bool(
        incoming.get("relationEvidenceIndexAvailable")
    ):
        payload["relationEvidenceBySymbol"] = {
            symbol: [
                normalized
                for item in list(merged_evidence.get(symbol) or [])[
                    :NATIVE_RULE_PLANNER_RELATION_EVIDENCE_LIMIT_PER_SYMBOL
                ]
                for normalized in [_normalized_relation_evidence_entry(item)]
                if normalized
            ]
            for symbol in sorted(merged_sources)
        }
        payload["relationEvidenceCompleteBySymbol"] = {
            symbol: bool(merged_evidence_complete.get(symbol))
            for symbol in sorted(merged_sources)
        }
    topology = {
        **payload,
        "fingerprint": _topology_fingerprint(payload),
    }
    verified = normalize_native_rule_planner_topology(topology)
    if str(verified.get("status") or "") != "ok":
        return {
            "status": "invalid-merged",
            "reason": str(verified.get("reason") or "Merged planner topology is invalid."),
            "topology": {},
            "replacedSymbols": sorted(replaced),
            "retainedSymbols": sorted(set(merged_sources) - replaced),
        }
    return {
        "status": "ok",
        "reason": "",
        "topology": topology,
        "replacedSymbols": sorted(replaced),
        "retainedSymbols": sorted(set(merged_sources) - replaced),
        "activeSymbolCount": len(active_sources),
        "incomingSymbolCount": len(incoming_symbols),
        "mergedSymbolCount": len(merged_sources),
    }
