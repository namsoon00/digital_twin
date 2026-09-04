"""Operational fact-slot selection for scoped ABox persistence.

Fact slots route immutable facts to the next projection write. They are not
investment rules: TypeDB remains the only evaluator of RuleBox conditions.
The contract deliberately falls back to the full target-scoped candidate set
when a scope cannot be classified safely.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, Mapping, Set

from .ontology_change_impact import (
    scope_family,
    scope_symbol,
    unpack_semantic_dependency_fingerprints,
)


FACT_SLOT_PROJECTION_VERSION = "fact-slot-projection-v3-semantic-dependency-routing"

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
    "fundamental": {"state", "fundamental", "valuation", "quality", "link"},
    "governance": {"state", "governance", "evidence", "quality", "link"},
    "capital": {"state", "capital", "valuation", "evidence", "quality", "link"},
    "company-valuation": {"state", "company-valuation", "valuation", "quality", "link"},
    "exposure": {"state", "market", "exposure", "quality", "link"},
    "portfolio": {"state", "market", "temporal", "flow", "position", "valuation", "quality", "link"},
    "macro": {"state", "market", "temporal", "flow", "position", "valuation", "exposure", "quality", "link"},
    "macro-market": {"state", "market", "temporal", "flow", "position", "valuation", "exposure", "quality", "link"},
    "macro-fx": {"state", "market", "position", "valuation", "exposure", "quality", "link", "macro-fx"},
    "macro-rates": {"state", "market", "valuation", "exposure", "quality", "link", "macro-rates"},
    "macro-crypto": {"state", "market", "temporal", "flow", "position", "valuation", "exposure", "quality", "link", "macro-crypto"},
}

# New verified events carry the exact fields that changed.  These field
# groups are projection dependencies, not investment rules: they decide which
# factual slots need a new immutable generation and never whether a RuleBox
# condition matched.
FACT_SLOT_DIRECT_FAMILIES = {
    "market": {"market"},
    "temporal": {"temporal"},
    "flow": {"flow"},
    "evidence": {"evidence"},
    "quality": {"quality"},
    "valuation": {"valuation"},
    "position": {"position"},
    "profile": {"profile"},
    "fundamental": {"fundamental"},
    "governance": {"governance"},
    "capital": {"capital"},
    # Company knowledge uses a dedicated semantic family, while reusable
    # market-comparison and margin-of-safety facts retain the physical
    # ``valuation`` family. One issuer-valuation event owns both slots.
    "company-valuation": {"company-valuation", "valuation"},
    "exposure": {"exposure"},
    "portfolio": {"portfolio", "position"},
    "macro": {"macro"},
    "macro-market": {"macro-market"},
    "macro-fx": {"macro-fx"},
    "macro-rates": {"macro-rates"},
    "macro-crypto": {"macro-crypto"},
}

FIELD_SLOT_FAMILIES = {
    # Quote and session facts.
    "currentprice": {"market"},
    "changerate": {"market"},
    "market": {"profile"},
    "currency": {"profile"},
    "sector": {"profile"},
    "symbol": {"profile"},
    # Position and mark-to-market facts.
    "source": {"position", "profile"},
    "quantity": {"position"},
    "sellablequantity": {"position"},
    "averageprice": {"position"},
    "exchangerate": {"position", "valuation", "exposure"},
    "marketvalue": {"position"},
    "profitloss": {"position"},
    "profitlossrate": {"position"},
    "positionremoved": {"position"},
    "portfoliorisk": {"portfolio", "exposure"},
    "positionrisk": {"position", "exposure"},
    "rebalancescenario": {"portfolio", "exposure"},
    # Provider quality is versioned independently from quote values.
    "quotestatus": {"quality"},
    "dataquality": {"quality"},
    "sourcetimestampstate": {"quality"},
    "freshnessstatus": {"quality"},
    "latencystatus": {"quality"},
    "marketsession": {"market", "quality"},
    "marketsessionlabel": {"market", "quality"},
    "realtime": {"quality"},
    # Technical and flow observations.
    "ma5": {"temporal"},
    "ma20": {"temporal"},
    "ma60": {"temporal"},
    "ma120": {"temporal"},
    "ma200": {"temporal"},
    "ma20slope": {"temporal"},
    "ma60slope": {"temporal"},
    "ma5distance": {"temporal"},
    "ma20distance": {"temporal"},
    "ma60distance": {"temporal"},
    "tradestrength": {"flow"},
    "tradingvalue": {"flow"},
    "volume": {"flow"},
    "volumeratio": {"flow"},
    "buyvolume": {"flow"},
    "sellvolume": {"flow"},
    "orderbookbidvolume": {"flow"},
    "orderbookaskvolume": {"flow"},
    "bidaskimbalance": {"flow"},
    "foreignbuyvolume": {"flow"},
    "foreignsellvolume": {"flow"},
    "foreignnetvolume": {"flow"},
    "foreignnetamount": {"flow"},
    "institutionbuyvolume": {"flow"},
    "institutionsellvolume": {"flow"},
    "institutionnetvolume": {"flow"},
    "institutionnetamount": {"flow"},
    "individualbuyvolume": {"flow"},
    "individualsellvolume": {"flow"},
    "individualnetvolume": {"flow"},
    "individualnetamount": {"flow"},
    # Explicit aggregate changes intentionally retain their broader factual
    # surface. They are infrequent and can change several account facts.
    "portfoliocontext": {"portfolio", "position", "valuation", "exposure", "quality"},
}

FOLLOWUP_FIELD = "marketobservationfollowup"


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


def _field_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", _clean(value).lower())


def _field_slot_families(value: object) -> Set[str]:
    text = _clean(value)
    compact = _field_key(text)
    if compact in FIELD_SLOT_FAMILIES:
        return set(FIELD_SLOT_FAMILIES[compact])
    external = text.lower().removeprefix("external.")
    if external in {
        "secilings", "secfilings", "newsheadlines", "dartdisclosures",
        "earningsreports", "researchevidence", "verifiedclaims",
    }:
        return {"evidence"}
    if external in {"companyoverviews", "yfinancedata"}:
        return {"evidence", "profile", "valuation"}
    if external == "companyknowledge.profile":
        return {"profile"}
    if external == "companyknowledge.valuation":
        return {"company-valuation", "valuation"}
    if external == "companyknowledge.financials":
        return {"fundamental"}
    if external in {"companyknowledge.governance", "companyknowledge.ownership"}:
        return {"governance"}
    if external == "companyknowledge.capital":
        return {"capital"}
    if external == "companyknowledge.coverage":
        return {"quality"}
    if external == "companyknowledge":
        return {"profile", "company-valuation", "fundamental", "governance", "capital", "quality"}
    if external in {"quality", "freshness", "provenance", "statuses"}:
        return {"quality"}
    if external in {"macro"}:
        return {"macro-rates"}
    if external in {"fxrates"}:
        return {"macro-fx"}
    if external in {"cryptomarkets"} or compact == "cryptomarkettransition":
        return {"market", "macro-crypto", "exposure"}
    return set()


def _scope_families(
    scope_id: object,
    item: Mapping[str, object],
    include_impact_families: bool = True,
    include_semantic_families: bool = True,
) -> Set[str]:
    row = dict(item or {})
    values = (
        _family_values(row.get("impactScopeFamilies") or [])
        if include_impact_families
        else set()
    )
    if include_semantic_families:
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
    changed_fields_by_symbol: Mapping[str, Iterable[object]] = None,
    event_boundary_authoritative: bool = False,
    requested_dependency_keys: Iterable[object] = None,
    requested_dependency_keys_by_symbol: Mapping[str, Iterable[object]] = None,
    dependency_boundary_authoritative: bool = False,
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
    raw_changed_fields = (
        changed_fields_by_symbol
        if isinstance(changed_fields_by_symbol, Mapping)
        else {}
    )
    changed_fields: Dict[str, Set[str]] = {
        _clean(symbol).upper(): {
            _clean(value)
            for value in values or []
            if _clean(value)
        }
        for symbol, values in raw_changed_fields.items()
        if _clean(symbol).upper() in targets
    }
    dependency_keys = _family_values(requested_dependency_keys or [])
    raw_dependency_keys_by_symbol = (
        requested_dependency_keys_by_symbol
        if isinstance(requested_dependency_keys_by_symbol, Mapping)
        else {}
    )
    dependency_keys_by_symbol: Dict[str, Set[str]] = {
        _clean(symbol).upper(): _family_values(values)
        for symbol, values in raw_dependency_keys_by_symbol.items()
        if _clean(symbol).upper() in targets
    }
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
        slots.update(
            FACT_SLOT_DIRECT_FAMILIES.get(family, {family})
            if event_boundary_authoritative
            else FACT_SLOT_DEPENDENCY_FAMILIES[family]
        )
    # Link ownership is derived from the selected fact scopes below. Treating
    # every link as a direct fact slot turns one valuation or calendar event
    # into a rewrite of every relation touching the stock.
    slots_by_symbol: Dict[str, Set[str]] = {}
    fallback_targets = []
    unknown_by_symbol: Dict[str, list] = {}
    precise_field_routing_symbols: Set[str] = set()
    unclassified_fields_by_symbol: Dict[str, list] = {}
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
        symbol_fields = changed_fields.get(symbol, set())
        compact_fields = {_field_key(value) for value in symbol_fields}
        unclassified_fields = sorted(
            value
            for value in symbol_fields
            if not _field_slot_families(value)
            and _field_key(value) != FOLLOWUP_FIELD
        )
        use_precise_fields = bool(symbol_fields and not unclassified_fields)
        symbol_slots: Set[str] = set()
        if event_boundary_authoritative:
            for family in symbol_requested:
                symbol_slots.update(
                    FACT_SLOT_DIRECT_FAMILIES.get(family, {family})
                )
        elif use_precise_fields and FOLLOWUP_FIELD not in compact_fields:
            for family in symbol_requested:
                symbol_slots.update(
                    FACT_SLOT_DIRECT_FAMILIES.get(family, {family})
                )
            for field in symbol_fields:
                symbol_slots.update(_field_slot_families(field))
            precise_field_routing_symbols.add(symbol)
        else:
            for family in symbol_requested:
                symbol_slots.update(FACT_SLOT_DEPENDENCY_FAMILIES[family])
            if unclassified_fields:
                unclassified_fields_by_symbol[symbol] = unclassified_fields
        slots_by_symbol[symbol] = symbol_slots
    # Shared scopes must follow the same field-level contract as symbol scopes.
    # Keeping the legacy batch-wide closure here caused precise market events to
    # re-select unrelated macro, valuation, and state scopes through shared
    # dependency families.
    if (
        targets
        and precise_field_routing_symbols == set(targets)
        and not fallback_targets
    ):
        slots = {
            family
            for symbol_slots in slots_by_symbol.values()
            for family in symbol_slots
        }
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
        "changedFieldsBySymbol": {
            symbol: sorted(values)
            for symbol, values in sorted(changed_fields.items())
        },
        "requestedDependencyKeys": sorted(dependency_keys),
        "requestedDependencyKeysBySymbol": {
            symbol: sorted(dependency_keys_by_symbol.get(symbol, dependency_keys))
            for symbol in targets
        },
        "dependencyBoundaryAuthoritative": bool(
            dependency_boundary_authoritative and dependency_keys
        ),
        "preciseFieldRoutingSymbols": sorted(precise_field_routing_symbols),
        "unclassifiedChangedFieldsBySymbol": unclassified_fields_by_symbol,
        "fallbackTargetSymbols": sorted(fallback_targets),
        "unknownFactFamiliesBySymbol": unknown_by_symbol,
        "fallbackReason": "unclassified-target-event-family" if fallback_targets else "",
        "eventBoundaryAuthoritative": bool(event_boundary_authoritative),
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
    precise_field_routing_symbols = {
        _clean(symbol).upper()
        for symbol in plan.get("preciseFieldRoutingSymbols") or []
        if _clean(symbol)
    }
    dependency_keys = _family_values(plan.get("requestedDependencyKeys") or [])
    raw_dependency_keys_by_symbol = plan.get("requestedDependencyKeysBySymbol")
    raw_dependency_keys_by_symbol = (
        raw_dependency_keys_by_symbol
        if isinstance(raw_dependency_keys_by_symbol, Mapping)
        else {}
    )
    dependency_keys_by_symbol = {
        _clean(symbol).upper(): _family_values(values)
        for symbol, values in raw_dependency_keys_by_symbol.items()
        if _clean(symbol)
    }
    dependency_boundary_authoritative = bool(
        plan.get("dependencyBoundaryAuthoritative") and dependency_keys
    )
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
        "preciseFieldRoutingSymbols": sorted(precise_field_routing_symbols),
        "changedFieldsBySymbol": {
            _clean(symbol).upper(): sorted({_clean(value) for value in values or [] if _clean(value)})
            for symbol, values in dict(plan.get("changedFieldsBySymbol") or {}).items()
            if _clean(symbol)
        },
        "unclassifiedChangedFieldsBySymbol": {
            _clean(symbol).upper(): sorted({_clean(value) for value in values or [] if _clean(value)})
            for symbol, values in dict(plan.get("unclassifiedChangedFieldsBySymbol") or {}).items()
            if _clean(symbol)
        },
        "requestedDependencyKeys": sorted(dependency_keys),
        "requestedDependencyKeysBySymbol": {
            symbol: sorted(values)
            for symbol, values in sorted(dependency_keys_by_symbol.items())
        },
        "dependencyBoundaryAuthoritative": dependency_boundary_authoritative,
        "dependencyMatchedScopeIds": [],
        "directSelectedScopeIds": list(candidates),
        "reverseDependencySelectedScopeIds": [],
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
    dependency_matched = []
    unchanged_dependency_matched = []
    reverse_dependency_selected = set()

    def dependency_key_matches(scope_key: str, requested_key: str) -> bool:
        return bool(
            scope_key == requested_key
            or scope_key.startswith(requested_key + ":")
            or requested_key.startswith(scope_key + ":")
        )

    def scope_matches_requested_dependency(scope_id: str, item: Mapping[str, object]) -> bool:
        symbol = scope_symbol(scope_id)
        applicable_dependency_keys = (
            dependency_keys_by_symbol.get(symbol, dependency_keys)
            if symbol
            else dependency_keys
        )
        scope_dependency_keys = _family_values(
            unpack_semantic_dependency_fingerprints(item).keys()
        )
        return bool(
            applicable_dependency_keys
            and scope_dependency_keys
            and any(
                dependency_key_matches(scope_key, requested_key)
                for scope_key in scope_dependency_keys
                for requested_key in applicable_dependency_keys
            )
        )

    def reverse_dependency_matches_event_family(
        scope_id: str,
        item: Mapping[str, object],
    ) -> bool:
        """Keep reverse relation closure inside the authoritative event slice."""

        symbol = scope_symbol(scope_id)
        applicable_slots = slots_by_symbol.get(symbol, slots) if symbol else slots
        precise_scope = bool(
            plan.get("eventBoundaryAuthoritative")
            or symbol in precise_field_routing_symbols
            or (
                not symbol
                and precise_field_routing_symbols
                == set(plan.get("targetSymbols") or [])
            )
        )
        families = _scope_families(
            scope_id,
            item,
            include_impact_families=not precise_scope,
            include_semantic_families=not precise_scope,
        )
        return bool(families & applicable_slots)

    for scope_id in candidates:
        item = scope_plan_by_id.get(scope_id) or {}
        symbol = scope_symbol(scope_id)
        precise_scope = bool(
            plan.get("eventBoundaryAuthoritative")
            or
            symbol in precise_field_routing_symbols
            or (
                not symbol
                and precise_field_routing_symbols
                == set(plan.get("targetSymbols") or [])
            )
        )
        families = _scope_families(
            scope_id,
            item,
            include_impact_families=not precise_scope,
            include_semantic_families=not precise_scope,
        )
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
        applicable_dependency_keys = (
            dependency_keys_by_symbol.get(symbol, dependency_keys)
            if symbol
            else dependency_keys
        )
        scope_dependency_keys = _family_values(
            unpack_semantic_dependency_fingerprints(item).keys()
        )
        dependency_match = bool(
            dependency_boundary_authoritative
            and applicable_dependency_keys
            and scope_dependency_keys
            and any(
                dependency_key_matches(scope_key, requested_key)
                for scope_key in scope_dependency_keys
                for requested_key in applicable_dependency_keys
            )
        )
        # Exact dependency identities describe the field that changed, while
        # scope families describe its semantic use. A stock anchor is stored
        # in the physical ``state`` scope even though currentPrice and volume
        # route market/flow events. Requiring both classifications to match
        # rejects valid exact dependencies. Use the family boundary only for
        # events that do not carry an authoritative dependency contract.
        selected_by_boundary = (
            dependency_match
            if dependency_boundary_authoritative
            else bool(families & applicable_slots)
        )
        if selected_by_boundary:
            selected.append(scope_id)
            if dependency_match:
                dependency_matched.append(scope_id)
        else:
            deferred.append(scope_id)
    direct_selected = set(selected)
    if dependency_boundary_authoritative and not dependency_matched:
        candidate_set = set(candidates)
        unchanged_dependency_matched = sorted(
            scope_id
            for scope_id, item in scope_plan_by_id.items()
            if scope_id not in candidate_set
            and scope_matches_requested_dependency(scope_id, item)
        )
    if dependency_boundary_authoritative and dependency_matched:
        # Relation scopes normally own links while their dependency list owns
        # the event entity. Include changed reverse dependants so an exact
        # event slice remains connected to the instrument in the active world.
        selected_set = set(selected)
        changed = True
        while changed:
            changed = False
            for scope_id in candidates:
                if scope_id in selected_set:
                    continue
                item = scope_plan_by_id.get(scope_id) or {}
                dependencies = {
                    _clean(value)
                    for value in item.get("dependencyScopeIds") or []
                    if _clean(value)
                }
                if (
                    dependencies.intersection(selected_set)
                    and reverse_dependency_matches_event_family(scope_id, item)
                ):
                    selected_set.add(scope_id)
                    reverse_dependency_selected.add(scope_id)
                    changed = True
        selected = sorted(selected_set)
        deferred = sorted(set(candidates) - selected_set)
    elif dependency_boundary_authoritative and unchanged_dependency_matched:
        # Candidate scope IDs contain only fragments whose semantic identity
        # differs from the active Manifest. If the exact event dependency is
        # indexed by an incoming target scope outside that set, the requested
        # value is already current. Treat this as a proven semantic no-op and
        # defer unrelated volatile changes instead of reporting a contract
        # failure or widening the write to a whole fact family.
        return {
            **base,
            "enabled": True,
            "status": "applied-noop-dependency-already-current",
            "selectedScopeIds": [],
            "deferredScopeIds": sorted(candidates),
            "dependencyMatchedScopeIds": [],
            "unchangedDependencyMatchedScopeIds": unchanged_dependency_matched,
            "fallbackReason": "",
        }
    elif dependency_boundary_authoritative and candidates:
        # An authoritative event key is useful only when the compiled scope
        # metadata can prove a match. Widening to a fact family would rewrite
        # unrelated facts and could publish an inference that never consumed
        # its cause, so this is a fail-closed contract violation.
        return {
            **base,
            "enabled": False,
            "status": "blocked-dependency-key-no-scope-match",
            "selectedScopeIds": [],
            "deferredScopeIds": sorted(candidates),
            "fallbackReason": "event-dependency-key-not-indexed-in-scope",
        }
    elif plan.get("eventBoundaryAuthoritative") and selected:
        # A direct fact scope may be owned by a relation scope. Follow only
        # changed reverse dependants of the selected facts. Endpoint scopes
        # are staged later by the manifest integrity closure; they must not
        # become seeds that re-select every other relation of the instrument.
        selected_set = set(selected)
        changed = True
        while changed:
            changed = False
            for scope_id in candidates:
                if scope_id in selected_set:
                    continue
                item = scope_plan_by_id.get(scope_id) or {}
                dependencies = {
                    _clean(value)
                    for value in item.get("dependencyScopeIds") or []
                    if _clean(value)
                }
                if (
                    dependencies.intersection(selected_set)
                    and reverse_dependency_matches_event_family(scope_id, item)
                ):
                    selected_set.add(scope_id)
                    reverse_dependency_selected.add(scope_id)
                    changed = True
        selected = sorted(selected_set)
        deferred = sorted(set(candidates) - selected_set)
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
        "dependencyMatchedScopeIds": sorted(dependency_matched),
        "directSelectedScopeIds": sorted(direct_selected),
        "reverseDependencySelectedScopeIds": sorted(reverse_dependency_selected),
        "fallbackReason": "",
    }
