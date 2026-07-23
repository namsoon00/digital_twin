"""ABox scope-change and RuleBox dependency contracts.

The graph store remains the authority for investment judgement.  This module
only describes which factual scopes changed and which native rules may need
to be observed as a consequence.  It never evaluates a rule or derives an
investment conclusion in Python.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set


# v4 also distinguishes typed fact-family changes from generic storage
# metadata. The distinction is required for safe native RuleBox reuse when a
# refreshed quote changes one observation without reopening unrelated rules.
CHANGE_IMPACT_VERSION = "abox-change-impact-v4"

SYMBOL_SCOPE_FAMILIES = {
    "state",
    "profile",
    "position",
    "market",
    "flow",
    "temporal",
    "evidence",
    "quality",
    "valuation",
    "exposure",
    # Cross-scope TypeDB assertions live in a relation-only scope. Keeping
    # edges out of their endpoint fact scopes prevents one fresh quote from
    # rolling unrelated entity generations through endpoint storage IDs.
    "link",
}

GLOBAL_SCOPE_TYPES = {"macro", "portfolio", "policy", "reference", "episode", "evidence", "link"}


def _clean(value: object) -> str:
    return str(value or "").strip()


def _lower(value: object) -> str:
    return _clean(value).lower()


def _list(value: object) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        return [_clean(item) for item in value if _clean(item)]
    text = _clean(value)
    return [text] if text else []


def scope_type(scope_id: object) -> str:
    return _clean(scope_id).split(":", 1)[0] or "reference"


def scope_symbol(scope_id: object) -> str:
    parts = [item.strip() for item in _clean(scope_id).split(":")]
    if len(parts) >= 2 and parts[0] == "symbol" and parts[1]:
        return parts[1].upper()
    return ""


def scope_family(scope_id: object) -> str:
    """Return the stable factual family encoded by a scoped ABox id."""
    parts = [item.strip().lower() for item in _clean(scope_id).split(":") if item.strip()]
    if not parts:
        return "reference"
    if parts[0] == "symbol":
        return parts[2] if len(parts) >= 3 and parts[2] in SYMBOL_SCOPE_FAMILIES else "state"
    if parts[0] == "macro":
        family = parts[1] if len(parts) >= 2 else "market"
        return "macro-" + family if not family.startswith("macro-") else family
    if parts[0] in {"portfolio", "policy", "episode", "evidence", "reference"}:
        return parts[0]
    if parts[0] == "link":
        return "link"
    return parts[0]


def scope_family_tokens(scope_id: object) -> Set[str]:
    family = scope_family(scope_id)
    values = {family}
    if family.startswith("macro-"):
        values.add("macro")
    return values


def symbol_scope_id(symbol: object, family: object = "state") -> str:
    clean_symbol = _clean(symbol).upper() or "UNKNOWN"
    clean_family = _lower(family)
    if clean_family not in SYMBOL_SCOPE_FAMILIES:
        clean_family = "state"
    return "symbol:" + clean_symbol + ":" + clean_family


def macro_scope_id(family: object = "market") -> str:
    clean_family = _lower(family).replace("macro-", "") or "market"
    return "macro:" + clean_family


def _matches_any(text: str, values: Sequence[str]) -> bool:
    return any(value in text for value in values)


def family_for_field(field: object) -> str:
    value = _lower(field).replace("_", "").replace("-", "")
    if not value:
        return "unknown"
    # These fields describe an account/instrument policy or identity. They are
    # carried by the compact stock anchor, but do not change with each quote.
    # Keep them out of the generic state family so a price refresh does not
    # requeue every rule that merely limits itself to holdings or watchlists.
    if value in {
        "source",
        "symbol",
        "market",
        "currency",
        "sector",
        "label",
        "provider",
        "positionrole",
        "targetpositionrole",
        "defaultholdingrole",
        "investmentstrategyprofile",
        "investmentstrategyprofilelabel",
        "holdingactionpolicy",
        "addbuypolicy",
        "archetype",
        "archetypelabel",
        "instrumentarchetype",
        "profile",
        "riskbudget",
        "profitpolicy",
    }:
        return "profile"
    if _matches_any(value, ["tboxclass", "tboxclasses", "boundedcontext", "sourcecontext", "targetcontext", "activetbox", "tboxversion", "box"]):
        return "profile"
    if value in {
        "cash",
        "cashratio",
        "total",
        "invested",
        "concentration",
        "candidatecount",
        "positionaccountweight",
        "positionweight",
    }:
        return "position"
    if value in {
        "positiontotradingvaluepct",
        "positiontobiddepthpct",
        "exitdaysattenpctadv",
        "retaildipbuyingrisk",
    }:
        return "flow"
    if _matches_any(value, ["tradingvalue", "tradevalue", "adv", "turnover"]):
        return "flow"
    if _matches_any(value, ["quote", "sourcetimestamp", "sourcetrust", "observationsource", "judgementevidence", "dataquality", "datastate", "validationstate"]):
        return "quality"
    if value in {"adrpremiumpct", "marginofsafety", "premium", "discount"}:
        return "valuation"
    if _matches_any(value, ["profitloss", "averageprice", "quantity", "marketvalue", "positionweight", "sellable", "holding"]):
        return "position"
    if _matches_any(value, ["foreign", "institution", "individual", "volume", "tradestrength", "bidask", "orderbook", "liquidity", "slippage", "execution"]):
        return "flow"
    if _matches_any(value, ["trend", "transition", "temporal", "previous", "pricepath", "acceleration", "window", "horizon"]):
        return "temporal"
    if _matches_any(value, ["fresh", "quality", "sourceasof", "sourcefetched", "missing", "coverage", "stale", "validity", "latency"]):
        return "quality"
    if _matches_any(value, ["valuation", "fairvalue", "targetprice", "per", "pbr", "eps", "revenue", "earning", "fundamental"]):
        return "valuation"
    if _matches_any(value, ["news", "disclosure", "article", "research", "event", "claim", "filing"]):
        return "evidence"
    if _matches_any(value, ["fx", "usdkrw", "exchange", "yield", "interest", "policyrate", "dgs", "dff", "macro", "crypto", "vix", "benchmark"]):
        return "macro"
    if _matches_any(value, ["factor", "beta", "correlation", "sector", "currencyexposure", "exposure"]):
        return "exposure"
    if _matches_any(value, ["currentprice", "price", "ma", "high", "low", "changerate", "technical", "keylevel"]):
        return "market"
    return "state"


def family_for_entity(kind: object, properties: Mapping[str, object] = None, entity_id: object = "") -> str:
    """Classify an ABox entity without looking at its current values."""
    props = dict(properties or {})
    text = " ".join([_lower(kind), _lower(entity_id), _lower(props.get("tboxClass")), " ".join(_lower(item) for item in _list(props.get("tboxClasses")))])
    if _matches_any(text, ["market-proxy", "market-index"]):
        return "macro-market"
    if _matches_any(text, ["fx-rate", "fxpair", "currency-rate"]):
        return "macro-fx"
    if _matches_any(text, ["interest-rate", "yield-curve", "yieldcurve", "macro-rate"]):
        return "macro-rates"
    if _matches_any(text, ["crypto-asset", "cryptoasset", "crypto-market"]):
        return "macro-crypto"
    if _matches_any(text, ["macro-indicator", "macro-regime", "market-regime"]):
        return "macro-market"
    if _matches_any(text, ["benchmark-index", "benchmark-proxy"]):
        return "macro-market"
    if _matches_any(text, ["valuation", "fair-value", "fairvalue", "fundamental", "margin-of-safety", "cross-market-premium", "adr-premium"]):
        return "valuation"
    if _matches_any(text, ["news", "disclosure", "filing", "research", "article", "document", "claim", "evidence", "external-signal", "corporate-action"]):
        return "evidence"
    if _matches_any(text, ["temporal", "trend-transition", "trend-phase", "price-path", "fact-change", "threshold-crossing", "event-cluster"]):
        field = props.get("field") or props.get("changedField") or ""
        family = family_for_field(field)
        return family if family not in {"unknown", "state"} else "temporal"
    if _matches_any(text, ["flow", "volume", "execution", "liquidity", "smart-money", "investor", "orderbook", "rebalancing"]):
        return "flow"
    if _matches_any(text, ["data-quality", "missing-data", "coverage-gap", "freshness", "latency", "staleness", "source-reliability"]):
        return "quality"
    if _matches_any(text, ["position", "holding-timing", "exit-exposure"]):
        return "position"
    if _matches_any(text, ["security-line", "instrument-profile", "instrument-identity", "company", "adr", "depositary", "leveraged-etf", "single-stock-etf", "risk-budget", "profit-policy", "risk-management", "strategy-profile", "investment-strategy", "investment-archetype", "account-delivery-profile"]):
        return "profile"
    if _matches_any(text, ["factor", "exposure", "peer", "correlation", "sensitivity"]):
        return "exposure"
    if _matches_any(text, ["price", "technical", "key-level", "market-microstructure", "trend-scenario", "scenario"]):
        return "market"
    # Stock holds the native RuleBox subject properties. It is intentionally a
    # compact state anchor rather than a static profile record.
    if _matches_any(text, ["stock", "instrument"]):
        return "state"
    return "state"


def family_for_relation(
    relation_type: object,
    properties: Mapping[str, object] = None,
    source_family: object = "",
    target_family: object = "",
    source_kind: object = "",
    target_kind: object = "",
) -> str:
    props = dict(properties or {})
    text = " ".join([
        _lower(relation_type),
        _lower(source_kind),
        _lower(target_kind),
        " ".join(_lower(item) for item in _list(props.get("fields"))),
        _lower(props.get("field")),
    ])
    # AFFECTS is written from a fact/event to the affected stock. Its factual
    # family is therefore owned by the source rather than the stock anchor.
    # Without this exception macro, FX, and evidence updates fall back to the
    # target's generic state scope and reopen the entire rule catalog.
    if _matches_any(text, ["affects"]):
        source_value = _lower(source_family)
        if source_value in SYMBOL_SCOPE_FAMILIES or source_value.startswith("macro-"):
            return source_value
        inferred_source = family_for_entity(source_kind)
        if inferred_source != "state":
            return inferred_source
    # Explicit relation vocabulary is more reliable than the generic endpoint
    # fallback below. These relationships can be stored in a link scope even
    # when their subject is the stock state anchor.
    if _matches_any(text, ["has_risk_budget", "has_profit_policy", "has_instrument_profile", "has_archetype", "has_position_role", "evaluated_under_strategy", "account_delivery_profile"]):
        return "profile"
    if _matches_any(text, ["has_margin_of_safety", "has_adr_premium", "cross_market_premium", "adr_premium"]):
        return "valuation"
    if _matches_any(text, ["has_beta_to", "has_crypto_exposure", "has_factor_exposure", "exposed_to"]):
        return "exposure"
    if _matches_any(text, ["exposed_to_fx", "has_fx", "fx_rate"]):
        return "exposure"
    if _matches_any(text, ["interest", "yield", "macro_regime", "market_proxy", "factor_exposure", "correlation", "sensitivity"]):
        return "exposure"
    if _matches_any(text, ["external_signal", "evidence", "news", "disclosure", "research", "provenance", "mentions", "asserts", "verified"]):
        return "evidence"
    if _matches_any(text, ["temporal", "trend", "price_path", "fact_change", "changes_fact", "threshold_crossing", "event_cluster"]):
        field = props.get("field") or ""
        field_family = family_for_field(field)
        return field_family if field_family not in {"unknown", "state"} else "temporal"
    if _matches_any(text, ["trade_flow", "volume", "execution", "liquidity", "smart_money", "investor", "orderbook", "slippage"]):
        return "flow"
    if _matches_any(text, ["data_quality", "freshness", "coverage", "missing", "source_data_state"]):
        return "quality"
    if _matches_any(text, ["valuation", "fair_value", "fundamental"]):
        return "valuation"
    if _matches_any(text, ["position", "holds", "watches", "sellable"]):
        return "position"
    if _matches_any(text, ["price", "key_level", "key-level", "technical", "breaks_level", "reclaims_level", "retests_level", "above", "below"]):
        return "market"
    for candidate in [target_family, source_family]:
        value = _lower(candidate)
        if value in SYMBOL_SCOPE_FAMILIES or value.startswith("macro-"):
            return value
    for kind in [target_kind, source_kind]:
        inferred = family_for_entity(kind)
        if inferred != "state":
            return inferred
    return "state"


def _condition_value(condition: object, snake: str, camel: str = "") -> object:
    if isinstance(condition, Mapping):
        return condition.get(snake, condition.get(camel))
    return getattr(condition, snake, getattr(condition, camel, None))


def rule_condition_dependency_profile(condition: object) -> Dict[str, object]:
    """Describe the factual scope families a RuleBox condition may consume."""
    condition_id = _clean(_condition_value(condition, "condition_id", "conditionId"))
    kind = _lower(_condition_value(condition, "kind"))
    field = _clean(_condition_value(condition, "field"))
    relation_type = _clean(_condition_value(condition, "relation_type", "relationType"))
    target_kind = _clean(_condition_value(condition, "target_kind", "targetKind"))
    target_filters = _condition_value(condition, "target_property_filters", "targetPropertyFilters")
    target_filters = dict(target_filters or {}) if isinstance(target_filters, Mapping) else {}
    relation_filters = _condition_value(condition, "relation_property_filters", "relationPropertyFilters")
    relation_filters = dict(relation_filters or {}) if isinstance(relation_filters, Mapping) else {}
    families: Set[str] = set()
    for value in [field, target_filters.get("field"), relation_filters.get("field")]:
        for item in _list(value):
            family = family_for_field(item)
            if family != "unknown":
                families.add(family)
    if relation_type or kind in {"relation", "relation_exists", "relation_property"}:
        relation_family = family_for_relation(relation_type, relation_filters, target_kind=target_kind)
        has_specific_relation_input = bool(
            target_kind
            or field
            or target_filters
            or relation_filters
        )
        if relation_family == "state" and relation_type and not has_specific_relation_input:
            families.add("unknown")
        elif relation_family:
            families.add(relation_family)
    if target_kind:
        target_family = family_for_entity(target_kind, target_filters)
        # A concrete relation (for example HAS_EXECUTION_METRIC) already
        # defines the fact family. Do not add the generic state fallback from
        # an intentionally broad target type such as ``risk``; doing so would
        # make every quote update select the rule again.
        if target_family and not (
            target_family == "state"
            and relation_type
            and relation_family not in {"", "state"}
        ):
            families.add(target_family)
    if kind in {"subject_property", "property", "field"} and not field:
        families.add("state")
    families.discard("")
    conservative = not families or "unknown" in families
    if conservative:
        families.discard("unknown")
        families.add("unknown")
    return {
        "conditionId": condition_id,
        "conditionKind": kind,
        "scopeFamilies": sorted(families),
        "field": field,
        "relationType": relation_type,
        "targetKind": target_kind,
        "role": _clean(_condition_value(condition, "role", "conditionRole")) or "required",
        "conservative": conservative,
    }


def rule_dependency_profile(rule: object) -> Dict[str, object]:
    if isinstance(rule, Mapping):
        rule_id = _clean(rule.get("rule_id") or rule.get("ruleId"))
        conditions = rule.get("conditions") or []
        enabled = bool(rule.get("enabled", True))
    else:
        rule_id = _clean(getattr(rule, "rule_id", ""))
        conditions = getattr(rule, "conditions", []) or []
        enabled = bool(getattr(rule, "enabled", True))
    condition_profiles = [rule_condition_dependency_profile(item) for item in conditions]
    families = sorted({family for item in condition_profiles for family in item["scopeFamilies"]})
    conservative = any(bool(item.get("conservative")) for item in condition_profiles) or not families
    if conservative and "unknown" not in families:
        families.append("unknown")
    return {
        "ruleId": rule_id,
        "enabled": enabled,
        "scopeFamilies": sorted(families),
        "conditionProfiles": condition_profiles,
        "conservative": conservative,
    }


def rule_dependency_profiles(rules: Iterable[object]) -> List[Dict[str, object]]:
    return [
        profile
        for profile in (rule_dependency_profile(rule) for rule in rules or [])
        if profile.get("ruleId")
    ]


def _scope_plan_index(scope_plan: Iterable[object]) -> Dict[str, Dict[str, object]]:
    result: Dict[str, Dict[str, object]] = {}
    for item in scope_plan or []:
        if not isinstance(item, Mapping):
            continue
        scope_id = _clean(item.get("scopeId"))
        if scope_id:
            result[scope_id] = dict(item)
    return result


def _scope_plan_family_tokens(scope_id: str, item: Mapping[str, object]) -> Set[str]:
    """Return semantic families carried by one scope-plan row.

    Relation-only ``link`` scopes can carry market, flow, evidence, or macro
    assertions. Their physical owner is deliberately separate from endpoint
    entity scopes, so routing must use the relation's semantic family rather
    than treat every changed link as an opaque state change.
    """

    raw_families = item.get("impactScopeFamilies") if isinstance(item, Mapping) else []
    values = {
        _clean(value)
        for value in raw_families or []
        if _clean(value)
    }
    if not values:
        values = scope_family_tokens(scope_id)
    expanded = set(values)
    for family in list(values):
        if family.startswith("macro-"):
            expanded.add("macro")
    return expanded


def _semantic_fingerprints(item: Mapping[str, object]) -> Dict[str, str]:
    raw = dict(item.get("semanticFingerprints") or {}) if isinstance(item, Mapping) else {}
    return {
        _clean(family): _clean(fingerprint)
        for family, fingerprint in raw.items()
        if _clean(family) and _clean(fingerprint)
    }


def _semantic_scope_changes(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> Optional[Set[str]]:
    """Return changed factual families or ``None`` for an older opaque scope."""
    before_fingerprints = _semantic_fingerprints(before)
    after_fingerprints = _semantic_fingerprints(after)
    if not before_fingerprints or not after_fingerprints:
        return None
    return {
        family
        for family in set(before_fingerprints) | set(after_fingerprints)
        if before_fingerprints.get(family) != after_fingerprints.get(family)
    }


def scope_delta(previous_scope_plan: Iterable[object], next_scope_plan: Iterable[object]) -> Dict[str, object]:
    """Compare immutable scope generations and retain dependency impact."""
    previous = _scope_plan_index(previous_scope_plan)
    current = _scope_plan_index(next_scope_plan)
    previous_ids = set(previous)
    current_ids = set(current)
    added = sorted(current_ids - previous_ids)
    removed = sorted(previous_ids - current_ids)
    changed = []
    rebound = []
    generation_changed = []
    unchanged = []
    semantic_changes_by_scope: Dict[str, List[str]] = {}
    for scope_id in sorted(current_ids & previous_ids):
        before = previous[scope_id]
        after = current[scope_id]
        before_identity = _clean(before.get("generationId") or before.get("fingerprint") or before.get("baseFingerprint"))
        after_identity = _clean(after.get("generationId") or after.get("fingerprint") or after.get("baseFingerprint"))
        if before_identity and before_identity == after_identity:
            unchanged.append(scope_id)
            continue
        generation_changed.append(scope_id)
        semantic_changes = _semantic_scope_changes(before, after)
        if semantic_changes is not None and not semantic_changes:
            rebound.append(scope_id)
            continue
        changed.append(scope_id)
        if semantic_changes:
            semantic_changes_by_scope[scope_id] = sorted(semantic_changes)
    direct_changed = sorted(set(added + removed + changed))
    dependency_graph: Dict[str, Set[str]] = defaultdict(set)
    for source in [previous, current]:
        for scope_id, item in source.items():
            for dependency in item.get("dependencyScopeIds") or []:
                dependency_id = _clean(dependency)
                if dependency_id:
                    dependency_graph[dependency_id].add(scope_id)
    affected = set(direct_changed)
    pending = list(direct_changed)
    while pending:
        scope_id = pending.pop()
        for dependent in dependency_graph.get(scope_id, set()):
            if dependent not in affected:
                affected.add(dependent)
                pending.append(dependent)
    active_affected = sorted(scope_id for scope_id in affected if scope_id in current)
    active_direct = sorted(scope_id for scope_id in direct_changed if scope_id in current)
    direct_families = sorted({
        token
        for scope_id in direct_changed
        for token in (
            set(semantic_changes_by_scope.get(scope_id) or [])
            or _scope_plan_family_tokens(scope_id, current.get(scope_id) or previous.get(scope_id) or {})
        )
    })
    affected_families = sorted({
        token
        for scope_id in affected
        for item in [current.get(scope_id) or previous.get(scope_id) or {}]
        for token in _scope_plan_family_tokens(scope_id, item)
    })
    direct_symbols = sorted({
        symbol
        for scope_id in direct_changed
        for symbol in [scope_symbol(scope_id)]
        if symbol
    })
    affected_symbols = sorted({
        symbol
        for scope_id in affected
        for symbol in [scope_symbol(scope_id)]
        if symbol
    })
    return {
        "version": CHANGE_IMPACT_VERSION,
        "previousScopeCount": len(previous),
        "nextScopeCount": len(current),
        "addedScopeIds": added,
        "removedScopeIds": removed,
        "changedScopeIds": sorted(set(added + changed)),
        "generationChangedScopeIds": sorted(set(added + changed + rebound)),
        "reboundScopeIds": rebound,
        "unchangedScopeIds": unchanged,
        "directChangedScopeIds": active_direct,
        "affectedScopeIds": active_affected,
        "dependencyAffectedScopeIds": sorted(set(active_affected) - set(added + changed)),
        # Keep the historical field name, but give it the precise meaning:
        # factual scopes that actually changed, not their dependents.
        "changedScopeFamilies": direct_families,
        "directChangedScopeFamilies": direct_families,
        "semanticChangedFamiliesByScope": semantic_changes_by_scope,
        "dependencyAffectedScopeFamilies": sorted(set(affected_families) - set(direct_families)),
        "affectedScopeFamilies": affected_families,
        "changedSymbols": direct_symbols,
        "directChangedSymbols": direct_symbols,
        "dependencyAffectedSymbols": sorted(set(affected_symbols) - set(direct_symbols)),
    }


def _rule_may_depend_on(profile: Mapping[str, object], changed_families: Set[str]) -> bool:
    families = {str(value or "") for value in profile.get("scopeFamilies") or []}
    if not changed_families or "unknown" in families or "state" in changed_families:
        return True
    if families & changed_families:
        return True
    if "macro" in changed_families and any(value.startswith("macro-") or value == "macro" for value in families):
        return True
    if "macro" in families and any(value.startswith("macro-") for value in changed_families):
        return True
    return False


def build_inference_impact_plan(
    previous_scope_plan: Iterable[object],
    next_scope_plan: Iterable[object],
    snapshot_symbols: Iterable[object],
    explicit_target_symbols: Iterable[object] = None,
    rules: Iterable[object] = None,
) -> Dict[str, object]:
    """Build a conservative routing plan for a native TypeDB inference run.

    The plan routes operational work only. TypeDB remains the sole evaluator
    of investment rules. A runtime can execute candidate rules together with
    previously matched unaffected rules, preserving a complete InferenceBox
    while avoiding known non-match queries.
    """
    delta = scope_delta(previous_scope_plan, next_scope_plan)
    available_symbols = sorted({_clean(item).upper() for item in snapshot_symbols or [] if _clean(item)})
    explicit_symbols = sorted({_clean(item).upper() for item in explicit_target_symbols or [] if _clean(item)})
    changed_scope_ids = list(delta.get("directChangedScopeIds") or delta.get("affectedScopeIds") or [])
    global_scope_ids = sorted({
        scope_id
        for scope_id in changed_scope_ids
        if scope_type(scope_id) in GLOBAL_SCOPE_TYPES and not scope_symbol(scope_id)
    })
    global_impact = bool(global_scope_ids)
    bounded_global_context = bool(global_impact and explicit_symbols)
    impacted_symbols = set(delta.get("directChangedSymbols") or delta.get("changedSymbols") or []) | set(explicit_symbols)
    if global_impact:
        impacted_symbols.update(available_symbols)
    target_symbols = [symbol for symbol in available_symbols if symbol in impacted_symbols]
    if not target_symbols and explicit_symbols:
        target_symbols = [symbol for symbol in available_symbols if symbol in explicit_symbols]
    if not target_symbols and not changed_scope_ids:
        target_symbols = list(available_symbols)
    profiles = rule_dependency_profiles(rules or [])
    changed_families = set(delta.get("directChangedScopeFamilies") or delta.get("changedScopeFamilies") or [])
    candidate_profiles = [
        profile for profile in profiles
        if profile.get("enabled") and _rule_may_depend_on(profile, changed_families)
    ]
    deferred_profiles = [
        profile for profile in profiles
        if profile.get("enabled") and profile not in candidate_profiles
    ]
    return {
        "version": CHANGE_IMPACT_VERSION,
        "scopeDelta": delta,
        "globalImpact": global_impact,
        "boundedGlobalContext": bounded_global_context,
        "globalImpactScopeIds": global_scope_ids,
        "explicitTargetSymbols": explicit_symbols,
        "inferenceTargetSymbols": target_symbols,
        "candidateRuleIds": [str(profile.get("ruleId") or "") for profile in candidate_profiles],
        "deferredRuleIds": [str(profile.get("ruleId") or "") for profile in deferred_profiles],
        "candidateRuleCount": len(candidate_profiles),
        "ruleDependencyCount": len(profiles),
        "changedScopeFamilies": sorted(changed_families),
        "ruleExecutionScope": (
            "target-scoped-global-context-native-evaluation"
            if bounded_global_context
            else "global-native-reconciliation"
            if global_impact
            else "dependency-selected-native-evaluation"
        ),
        "nativeRuleSelectionEligible": bool(
            candidate_profiles and (not global_impact or bounded_global_context)
        ),
        "nativeRuleSelectionApplied": False,
        "reason": (
            "전역 사실은 바뀌었지만 요청된 종목 범위에서 관련 규칙과 기존 성립 규칙만 다시 확인합니다."
            if bounded_global_context
            else "전역 범위 변경이 감지되어 전체 투자 대상을 재검토합니다."
            if global_impact
            else "직접 변경된 ABox 사실군으로 후보 RuleBox를 계산하고, 이전에 성립한 비변경 규칙을 함께 TypeDB에서 재확인합니다."
        ),
    }


def compact_inference_impact_plan(plan: Mapping[str, object], limit: int = 80) -> Dict[str, object]:
    """Keep audit, TypeDB metadata, and diagnostics bounded."""
    values = dict(plan or {})
    if not values:
        return {}
    delta = dict(values.get("scopeDelta") or {})
    bounded = max(1, int(limit or 80))
    return {
        "version": str(values.get("version") or CHANGE_IMPACT_VERSION),
        "globalImpact": bool(values.get("globalImpact")),
        "boundedGlobalContext": bool(values.get("boundedGlobalContext")),
        "globalImpactScopeIds": list(values.get("globalImpactScopeIds") or [])[:bounded],
        "explicitTargetSymbols": list(values.get("explicitTargetSymbols") or [])[:bounded],
        "inferenceTargetSymbols": list(values.get("inferenceTargetSymbols") or [])[:bounded],
        "candidateRuleIds": list(values.get("candidateRuleIds") or [])[:bounded],
        "deferredRuleIds": list(values.get("deferredRuleIds") or [])[:bounded],
        "candidateRuleCount": int(values.get("candidateRuleCount") or 0),
        "ruleDependencyCount": int(values.get("ruleDependencyCount") or 0),
        "changedScopeFamilies": list(values.get("changedScopeFamilies") or [])[:bounded],
        "ruleExecutionScope": str(values.get("ruleExecutionScope") or "dependency-selected-native-evaluation"),
        "nativeRuleSelectionEligible": bool(values.get("nativeRuleSelectionEligible")),
        "nativeRuleSelectionApplied": bool(values.get("nativeRuleSelectionApplied")),
        "scopeDelta": {
            "previousScopeCount": int(delta.get("previousScopeCount") or 0),
            "nextScopeCount": int(delta.get("nextScopeCount") or 0),
            "addedScopeIds": list(delta.get("addedScopeIds") or [])[:bounded],
            "removedScopeIds": list(delta.get("removedScopeIds") or [])[:bounded],
            "changedScopeIds": list(delta.get("changedScopeIds") or [])[:bounded],
            "generationChangedScopeIds": list(delta.get("generationChangedScopeIds") or [])[:bounded],
            "reboundScopeIds": list(delta.get("reboundScopeIds") or [])[:bounded],
            "directChangedScopeIds": list(delta.get("directChangedScopeIds") or [])[:bounded],
            "affectedScopeIds": list(delta.get("affectedScopeIds") or [])[:bounded],
            "dependencyAffectedScopeIds": list(delta.get("dependencyAffectedScopeIds") or [])[:bounded],
            "changedScopeFamilies": list(delta.get("changedScopeFamilies") or [])[:bounded],
            "directChangedScopeFamilies": list(delta.get("directChangedScopeFamilies") or [])[:bounded],
            "semanticChangedFamiliesByScope": {
                str(scope_id or ""): list(families or [])[:bounded]
                for scope_id, families in dict(delta.get("semanticChangedFamiliesByScope") or {}).items()
                if str(scope_id or "").strip()
            },
            "dependencyAffectedScopeFamilies": list(delta.get("dependencyAffectedScopeFamilies") or [])[:bounded],
            "affectedScopeFamilies": list(delta.get("affectedScopeFamilies") or [])[:bounded],
            "changedSymbols": list(delta.get("changedSymbols") or [])[:bounded],
            "directChangedSymbols": list(delta.get("directChangedSymbols") or [])[:bounded],
            "dependencyAffectedSymbols": list(delta.get("dependencyAffectedSymbols") or [])[:bounded],
        },
    }
