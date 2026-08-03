"""Operational fact-slot selection for scoped ABox persistence.

Fact slots route immutable facts to the next projection write. They are not
investment rules: TypeDB remains the only evaluator of RuleBox conditions.
The contract deliberately falls back to the full target-scoped candidate set
when a scope cannot be classified safely.
"""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Set

from .ontology_change_impact import scope_family, scope_symbol


FACT_SLOT_PROJECTION_VERSION = "fact-slot-projection-v1"

# A source event can update values derived into adjacent factual families.
# The closure keeps those derived facts coherent while excluding unrelated
# evidence/profile/exposure scopes from a quote or execution update.
FACT_SLOT_DEPENDENCY_FAMILIES = {
    "market": {"state", "market", "temporal", "flow", "position", "valuation", "quality", "link"},
    "temporal": {"state", "market", "temporal", "flow", "position", "valuation", "quality", "link"},
    "flow": {"state", "market", "flow", "position", "quality", "link"},
    "evidence": {"state", "evidence", "quality", "link"},
    "quality": {"state", "quality", "link"},
    "valuation": {"state", "market", "position", "valuation", "quality", "link"},
    "position": {"state", "market", "temporal", "flow", "position", "valuation", "quality", "link"},
    "profile": {"state", "profile", "position", "quality", "link"},
    "exposure": {"state", "market", "exposure", "quality", "link"},
    "portfolio": {"state", "market", "temporal", "flow", "position", "valuation", "quality", "link"},
    "macro": {"state", "market", "temporal", "flow", "position", "valuation", "exposure", "quality", "link"},
    "macro-market": {"state", "market", "temporal", "flow", "position", "valuation", "exposure", "quality", "link"},
    "macro-fx": {"state", "market", "position", "valuation", "exposure", "quality", "link", "macro-fx"},
    "macro-rates": {"state", "market", "valuation", "exposure", "quality", "link", "macro-rates"},
    "macro-crypto": {"state", "market", "temporal", "flow", "position", "valuation", "exposure", "quality", "link", "macro-crypto"},
}


def _clean(value: object) -> str:
    return str(value or "").strip()


def _family_values(values: Iterable[object]) -> Set[str]:
    if isinstance(values, str):
        values = [values]
    return {
        _clean(value).lower()
        for value in values or []
        if _clean(value)
    }


def _scope_families(scope_id: object, item: Mapping[str, object]) -> Set[str]:
    row = dict(item or {})
    values = _family_values(row.get("impactScopeFamilies") or [])
    semantic = row.get("semanticFingerprints")
    if isinstance(semantic, Mapping):
        values.update(_family_values(semantic.keys()))
    physical = _clean(row.get("scopeFamily")).lower() or scope_family(scope_id)
    if physical:
        values.add(physical)
    return values


def build_fact_slot_projection_plan(
    target_symbols: Iterable[object],
    requested_fact_families: Iterable[object],
    requested_fact_families_by_symbol: Mapping[str, Iterable[object]] = None,
) -> Dict[str, object]:
    """Build a conservative write-routing plan from mailbox provenance."""
    targets = sorted({
        _clean(symbol).upper()
        for symbol in target_symbols or []
        if _clean(symbol)
    })
    requested = sorted(_family_values(requested_fact_families))
    raw_by_symbol = (
        requested_fact_families_by_symbol
        if isinstance(requested_fact_families_by_symbol, Mapping)
        else {}
    )
    requested_by_symbol: Dict[str, Set[str]] = {}
    explicitly_classified_symbols: Set[str] = set()
    for raw_symbol, values in raw_by_symbol.items():
        symbol = _clean(raw_symbol).upper()
        if not symbol or symbol not in targets:
            continue
        explicitly_classified_symbols.add(symbol)
        requested_by_symbol[symbol] = _family_values(values)
    unknown = sorted({
        family
        for family in requested
        if family not in FACT_SLOT_DEPENDENCY_FAMILIES
    })
    if not targets:
        return {
            "version": FACT_SLOT_PROJECTION_VERSION,
            "enabled": False,
            "status": "disabled-no-targets",
            "targetSymbols": [],
            "requestedFactFamilies": requested,
            "requestedFactFamiliesBySymbol": {},
            "slotFamilies": [],
            "slotFamiliesBySymbol": {},
            "fallbackTargetSymbols": [],
            "fallbackReason": "no-target-symbols",
        }
    if not requested:
        return {
            "version": FACT_SLOT_PROJECTION_VERSION,
            "enabled": False,
            "status": "disabled-no-event-families",
            "targetSymbols": targets,
            "requestedFactFamilies": [],
            "requestedFactFamiliesBySymbol": {},
            "slotFamilies": [],
            "slotFamiliesBySymbol": {},
            "fallbackTargetSymbols": [],
            "fallbackReason": "event-fact-families-unavailable",
        }
    if unknown:
        return {
            "version": FACT_SLOT_PROJECTION_VERSION,
            "enabled": False,
            "status": "disabled-unknown-event-family",
            "targetSymbols": targets,
            "requestedFactFamilies": requested,
            "requestedFactFamiliesBySymbol": {
                symbol: sorted(values)
                for symbol, values in sorted(requested_by_symbol.items())
            },
            "slotFamilies": [],
            "slotFamiliesBySymbol": {},
            "fallbackTargetSymbols": [],
            "unknownFactFamilies": unknown,
            "fallbackReason": "unknown-event-fact-family",
        }
    slots: Set[str] = set()
    for family in requested:
        slots.update(FACT_SLOT_DEPENDENCY_FAMILIES[family])
    slots_by_symbol: Dict[str, Set[str]] = {}
    fallback_targets = []
    unknown_by_symbol: Dict[str, list] = {}
    for symbol in targets:
        # Older callers do not have granular provenance. Preserve their
        # existing batch-wide behavior rather than assuming a missing mapping
        # means the symbol has no relevant facts.
        symbol_requested = (
            requested_by_symbol.get(symbol, set(requested))
            if symbol in explicitly_classified_symbols
            else set(requested)
        )
        unknown_symbol_families = sorted(
            family
            for family in symbol_requested
            if family not in FACT_SLOT_DEPENDENCY_FAMILIES
        )
        if not symbol_requested or unknown_symbol_families:
            fallback_targets.append(symbol)
            if unknown_symbol_families:
                unknown_by_symbol[symbol] = unknown_symbol_families
            continue
        symbol_slots: Set[str] = set()
        for family in symbol_requested:
            symbol_slots.update(FACT_SLOT_DEPENDENCY_FAMILIES[family])
        slots_by_symbol[symbol] = symbol_slots
    return {
        "version": FACT_SLOT_PROJECTION_VERSION,
        "enabled": True,
        "status": "ready-with-target-fallback" if fallback_targets else "ready",
        "targetSymbols": targets,
        "requestedFactFamilies": requested,
        "requestedFactFamiliesBySymbol": {
            symbol: sorted(requested_by_symbol.get(symbol, set(requested)))
            for symbol in targets
        },
        "slotFamilies": sorted(slots),
        "slotFamiliesBySymbol": {
            symbol: sorted(values)
            for symbol, values in sorted(slots_by_symbol.items())
        },
        "fallbackTargetSymbols": sorted(fallback_targets),
        "unknownFactFamiliesBySymbol": unknown_by_symbol,
        "fallbackReason": "unclassified-target-event-family" if fallback_targets else "",
    }


def select_fact_slot_scope_ids(
    scope_plan_by_id: Mapping[str, Mapping[str, object]],
    candidate_scope_ids: Iterable[object],
    fact_slot_plan: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Choose compatible changed scopes, or retain all candidates safely."""
    plan = dict(fact_slot_plan or {})
    candidates = sorted({_clean(scope_id) for scope_id in candidate_scope_ids or [] if _clean(scope_id)})
    enabled = bool(plan.get("enabled"))
    slots = _family_values(plan.get("slotFamilies") or [])
    raw_slots_by_symbol = plan.get("slotFamiliesBySymbol")
    raw_slots_by_symbol = raw_slots_by_symbol if isinstance(raw_slots_by_symbol, Mapping) else {}
    slots_by_symbol = {
        _clean(symbol).upper(): _family_values(values)
        for symbol, values in raw_slots_by_symbol.items()
        if _clean(symbol)
    }
    fallback_targets = {
        _clean(symbol).upper()
        for symbol in plan.get("fallbackTargetSymbols") or []
        if _clean(symbol)
    }
    base = {
        "version": FACT_SLOT_PROJECTION_VERSION,
        "requestedFactFamilies": sorted(_family_values(plan.get("requestedFactFamilies") or [])),
        "requestedFactFamiliesBySymbol": {
            _clean(symbol).upper(): sorted(_family_values(values))
            for symbol, values in dict(plan.get("requestedFactFamiliesBySymbol") or {}).items()
            if _clean(symbol)
        },
        "slotFamilies": sorted(slots),
        "slotFamiliesBySymbol": {
            symbol: sorted(values)
            for symbol, values in sorted(slots_by_symbol.items())
        },
        "fallbackTargetSymbols": sorted(fallback_targets),
        "candidateScopeCount": len(candidates),
        "selectedScopeIds": list(candidates),
        "deferredScopeIds": [],
    }
    if not enabled or not slots:
        return {
            **base,
            "enabled": False,
            "status": str(plan.get("status") or "disabled"),
            "fallbackReason": str(plan.get("fallbackReason") or "fact-slot-disabled"),
        }

    selected = []
    deferred = []
    unknown = []
    for scope_id in candidates:
        item = scope_plan_by_id.get(scope_id) or {}
        families = _scope_families(scope_id, item)
        symbol = scope_symbol(scope_id)
        # An unknown source type for one target must never narrow that
        # target's ABox. Shared scopes are retained too because their facts
        # may be an input to the unknown target's native RuleBox evaluation.
        if symbol in fallback_targets or (not symbol and fallback_targets):
            selected.append(scope_id)
            continue
        # A legacy link scope without semantic/impact metadata cannot be
        # safely deferred because it can carry an endpoint dependency.
        if not families or (families == {"link"} and not item.get("impactScopeFamilies")):
            unknown.append(scope_id)
            continue
        applicable_slots = slots_by_symbol.get(symbol, slots) if symbol else slots
        if families & applicable_slots:
            selected.append(scope_id)
        else:
            deferred.append(scope_id)
    if unknown:
        return {
            **base,
            "enabled": False,
            "status": "fallback-unknown-scope-family",
            "unknownScopeIds": sorted(unknown),
            "fallbackReason": "scope-fact-family-unavailable",
        }
    if candidates and not selected:
        return {
            **base,
            "enabled": False,
            "status": "fallback-empty-slot-selection",
            "fallbackReason": "fact-slot-selected-no-changed-scope",
        }
    return {
        **base,
        "enabled": True,
        "status": "applied-with-target-fallback" if fallback_targets else "applied",
        "selectedScopeIds": sorted(selected),
        "deferredScopeIds": sorted(deferred),
        "fallbackReason": "",
    }
