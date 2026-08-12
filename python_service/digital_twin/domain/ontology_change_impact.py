"""ABox scope-change and RuleBox dependency contracts.

The graph store remains the authority for investment judgement.  This module
only describes which factual scopes changed and which native rules may need
to be observed as a consequence.  It never evaluates a rule or derives an
investment conclusion in Python.
"""

from __future__ import annotations

from collections import defaultdict
import re
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set


# v12 keeps the v6 global quality/value distinction, makes dependency
# fingerprints distinguish structural changes from a value change inside the
# same kind or relation, and separates the current mailbox event from other
# shared facts that happened to be present in the latest persisted snapshot.
# TypeDB still evaluates every selected RuleBox function; Python only avoids
# scheduling rules whose actual inputs did not change for this event.
CHANGE_IMPACT_VERSION = "abox-change-impact-v14"
DEPENDENCY_FINGERPRINT_VERSION = "rule-input-v2"

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
    "company-valuation",
    "fundamental",
    "governance",
    "capital",
    "exposure",
    # Cross-scope TypeDB assertions live in a relation-only scope. Keeping
    # edges out of their endpoint fact scopes prevents one fresh quote from
    # rolling unrelated entity generations through endpoint storage IDs.
    "link",
}

# Evidence and link scopes are relation-local when their dependency scopes
# identify a symbol. Treating every article edge or support relation as a
# global change caused unrelated holdings to be recomputed.
GLOBAL_SCOPE_TYPES = {"macro", "portfolio", "policy", "reference", "episode"}
RELATION_LOCAL_SCOPE_TYPES = {"evidence", "link"}

_GLOBAL_SCOPE_TYPE_LABELS = {
    "macro": "shared-market-context",
    "portfolio": "portfolio-context",
    "policy": "runtime-policy-context",
    "reference": "reference-context",
    "episode": "history-context",
}

# Event fact types describe the collection/change source. They are not
# RuleBox conditions, but translating them to the matching ABox family makes
# it possible to compare the requested work with the immutable scope delta.
# Unknown types deliberately remain absent so they cannot narrow native
# evaluation.
_EVENT_FACT_TYPE_SCOPE_FAMILIES = {
    "marketquote": {"market"},
    "technicalindicator": {"temporal"},
    "executionflow": {"flow"},
    "orderbook": {"flow"},
    "researchevidence": {"evidence"},
    "evidencelifecycle": {"evidence"},
    "newsevent": {"evidence"},
    "verifiedclaim": {"evidence"},
    "verificationrun": {"evidence"},
    "investmentcalendarevent": {"temporal", "evidence"},
    "portfoliosnapshot": {"position", "portfolio"},
    "portfolioactivityepisode": {"position", "portfolio", "episode"},
    "portfoliostatesnapshot": {"position", "portfolio"},
    "decisionactionobservation": {"position", "portfolio", "episode"},
    "dataquality": {"quality"},
    "fxrate": {"macro-fx"},
    "interestrate": {"macro-rates"},
    "financialfact": {"fundamental"},
    "financialstatement": {"fundamental"},
    "companyprofile": {"profile"},
    "governancechange": {"governance"},
    "capitalstructurechange": {"capital"},
    "valuationobservation": {"company-valuation"},
}


def _clean(value: object) -> str:
    return str(value or "").strip()


def _lower(value: object) -> str:
    return _clean(value).lower()


def _list(value: object) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        return [_clean(item) for item in value if _clean(item)]
    text = _clean(value)
    return [text] if text else []


def requested_scope_families_for_event_fact_types(fact_types: Iterable[object]) -> List[str]:
    """Map collection provenance to ABox families for diagnostics only.

    This is intentionally conservative: unknown event types do not alter the
    candidate RuleBox list, and TypeDB remains the only evaluator.
    """
    values: Set[str] = set()
    known_families = set(SYMBOL_SCOPE_FAMILIES) | {
        "macro",
        "macro-market",
        "macro-fx",
        "macro-rates",
        "macro-crypto",
        "portfolio",
        "policy",
        "reference",
        "episode",
    }
    for item in fact_types or []:
        text = _lower(item)
        compact = "".join(character for character in text if character.isalnum())
        if text in known_families:
            values.add(text)
        values.update(_EVENT_FACT_TYPE_SCOPE_FAMILIES.get(compact, set()))
    return sorted(values)


def scope_type(scope_id: object) -> str:
    return _clean(scope_id).split(":", 1)[0] or "reference"


def scope_symbol(scope_id: object) -> str:
    parts = [item.strip() for item in _clean(scope_id).split(":")]
    if len(parts) >= 2 and parts[0] == "symbol" and parts[1]:
        return parts[1].upper()
    # v5 relation-only scopes retain the affected instrument for routing.
    # Any world suffix is appended after the family and leaves this prefix
    # unchanged.
    if (
        len(parts) >= 4
        and parts[0].lower() == "link"
        and parts[1].lower() == "symbol"
        and parts[2]
    ):
        return parts[2].upper()
    return ""


def scope_family(scope_id: object) -> str:
    """Return the stable factual family encoded by a scoped ABox id."""
    parts = [item.strip().lower() for item in _clean(scope_id).split(":") if item.strip()]
    if not parts:
        return "reference"
    if parts[0] == "symbol":
        return parts[2] if len(parts) >= 3 and parts[2] in SYMBOL_SCOPE_FAMILIES else "state"
    if parts[0] == "link":
        # v5 uses relation-only scopes of the form
        # ``link:symbol:<ticker>:<family>`` or
        # ``link:account:<account>:<family>``. Keep legacy ``link:<owner>``
        # readable while active v3 manifests drain.
        if len(parts) >= 4 and parts[1] in {"symbol", "account"}:
            family = parts[3]
            if family in SYMBOL_SCOPE_FAMILIES or family.startswith("macro-"):
                return family
        return "link"
    if parts[0] == "macro":
        family = parts[1] if len(parts) >= 2 else "market"
        return "macro-" + family if not family.startswith("macro-") else family
    if parts[0] in {"portfolio", "policy", "episode", "evidence", "reference"}:
        return parts[0]
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
        "mandateid",
        "policyversion",
        "policylimitratio",
        "limitvaluepct",
        "mincashweightpct",
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
    if _matches_any(value, ["executive", "governance", "ceoname", "board", "tenure"]):
        return "governance"
    if _matches_any(value, ["sharesoutstanding", "floatshares", "sharesshort", "capitalstructure", "dilution"]):
        return "capital"
    if _matches_any(value, ["revenue", "grossprofit", "operatingincome", "netincome", "freecashflow", "operatingcashflow", "totalassets", "totalliabilities", "totalequity", "equity", "cashconversion", "debttoequity", "liabilitiestoassets", "financialstatement", "financialstate"]):
        return "fundamental"
    if _matches_any(value, ["profitloss", "averageprice", "quantity", "marketvalue", "positionweight", "sellable", "holding"]):
        return "position"
    if _matches_any(value, ["foreign", "institution", "individual", "volume", "tradestrength", "bidask", "orderbook", "liquidity", "slippage", "execution"]):
        return "flow"
    if _matches_any(value, ["trend", "transition", "temporal", "previous", "pricepath", "acceleration", "window", "horizon"]):
        return "temporal"
    if _matches_any(value, ["fresh", "quality", "sourceasof", "sourcefetched", "missing", "coverage", "stale", "validity", "latency"]):
        return "quality"
    if _matches_any(value, ["valuation", "fairvalue", "targetprice", "per", "pbr", "eps", "earning"]):
        return "valuation"
    if _matches_any(value, ["news", "disclosure", "article", "research", "event", "claim", "filing"]):
        return "evidence"
    if _matches_any(value, ["fx", "usdkrw", "exchange", "yield", "interest", "policyrate", "dgs", "dff", "macro", "crypto", "vix", "benchmark"]):
        return "macro"
    if _matches_any(value, ["factor", "beta", "correlation", "sector", "currencyexposure", "exposure", "policydelta"]):
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
    if _matches_any(text, ["company-financial", "financial-state", "financial-fact", "financial-statement", "income-statement", "balance-sheet", "cash-flow-statement"]):
        return "fundamental"
    if _matches_any(text, ["company-governance", "governance-state", "executive-role", "board-membership", "person"]):
        return "governance"
    if _matches_any(text, ["company-capital", "capital-state", "capital-structure", "capital-event", "ownership-stake"]):
        return "capital"
    if _matches_any(text, ["company-valuation", "company-valuation-state"]):
        return "company-valuation"
    if _matches_any(text, ["valuation", "fair-value", "fairvalue", "fundamental", "margin-of-safety", "cross-market-premium", "adr-premium"]):
        return "valuation"
    if _matches_any(text, ["news", "disclosure", "filing", "research", "article", "document", "claim", "evidence", "external-signal", "corporate-action"]):
        return "evidence"
    if _matches_any(text, ["temporal", "trend-transition", "trend-phase", "price-path", "fact-change", "threshold-crossing", "event-cluster"]):
        field = props.get("field") or props.get("changedField") or ""
        family = family_for_field(field)
        return family if family not in {"unknown", "state"} else "temporal"
    if _matches_any(text, ["flow", "volume", "execution", "liquidity", "slippage", "smart-money", "investor", "orderbook", "rebalancing"]):
        return "flow"
    if _matches_any(text, ["data-quality", "missing-data", "coverage-gap", "freshness", "latency", "staleness", "source-reliability", "data-source"]):
        return "quality"
    if _matches_any(text, ["position", "holding-timing", "exit-exposure"]):
        return "position"
    if _matches_any(text, ["instrument-anchor", "instrumentanchor", "security-line", "instrument-profile", "instrument-identity", "company", "adr", "depositary", "leveraged-etf", "single-stock-etf", "risk-budget", "profit-policy", "risk-management", "strategy-profile", "investment-strategy", "investment-archetype", "account-delivery-profile", "investment-mandate", "position-limit", "sector-limit", "currency-limit", "cash-floor", "loss-budget"]):
        return "profile"
    if _matches_any(text, ["factor", "exposure", "peer", "correlation", "sensitivity", "sector", "relative-performance"]):
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
    source_value = _lower(source_family)
    target_value = _lower(target_family)
    # Rule dependency profiles do not have concrete ABox scope ids yet. A
    # typed target such as ``market-proxy-observation`` is still enough to
    # keep ``HAS_PRICE`` in its macro family instead of treating it as a
    # generic stock-price dependency.
    if not source_value and not target_value:
        declared_target_family = family_for_entity(target_kind)
        if declared_target_family.startswith("macro-") or declared_target_family == "evidence":
            if _matches_any(text, ["data_quality", "freshness", "coverage", "missing", "source_data_state"]):
                return "quality"
            return declared_target_family
    # Price/volume relations inside one macro sensor must remain macro facts.
    # Otherwise a fresh index or FX observation is incorrectly routed as a
    # generic stock market/flow change and reopens unrelated TypeDB rules.
    if source_value.startswith("macro-") and target_value.startswith("macro-"):
        if _matches_any(text, ["data_quality", "freshness", "coverage", "missing", "source_data_state"]):
            return "quality"
        return source_value
    # Explicit relation vocabulary is more reliable than the generic endpoint
    # fallback below. These relationships can be stored in a link scope even
    # when their subject is the stock state anchor.
    if _matches_any(text, ["has_risk_budget", "has_profit_policy", "has_instrument_profile", "has_archetype", "has_position_role", "evaluated_under_strategy", "account_delivery_profile"]):
        return "profile"
    if _matches_any(text, ["has_margin_of_safety", "has_adr_premium", "cross_market_premium", "adr_premium"]):
        return "valuation"
    if _matches_any(text, ["financial_state", "financial_report", "financial_statement", "financial_fact", "accounting_scope"]):
        return "fundamental"
    if _matches_any(text, ["governance_state", "executive_role", "role_held", "ownership_stake", "stake_held"]):
        return "governance"
    if _matches_any(text, ["capital_state", "capital_structure", "capital_event", "affects_share_count"]):
        return "capital"
    if _matches_any(text, ["company_valuation", "company-valuation"]):
        return "company-valuation"
    if _matches_any(text, ["has_beta_to", "has_crypto_exposure", "has_factor_exposure", "exposed_to"]):
        return "exposure"
    if _matches_any(text, ["exposed_to_fx", "has_fx", "fx_rate"]):
        return "exposure"
    if _matches_any(text, ["interest", "yield", "macro_regime", "market_proxy", "factor_exposure", "correlation", "sensitivity", "relative_performance"]):
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


def _dependency_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _lower(value)).strip("-")


def _dependency_field_key(value: object) -> str:
    token = re.sub(r"[^a-z0-9]", "", _lower(value))
    return "field:" + token if token else ""


def _dependency_kind_field_key(kind: object, field: object) -> str:
    kind_token = _dependency_token(kind)
    field_token = re.sub(r"[^a-z0-9]", "", _lower(field))
    if not kind_token or not field_token:
        return ""
    return "kind:" + kind_token + ":field:" + field_token


def _dependency_relation_field_key(relation_type: object, field: object) -> str:
    relation_token = _dependency_token(relation_type)
    field_token = re.sub(r"[^a-z0-9]", "", _lower(field))
    if not relation_token or not field_token:
        return ""
    return "relation:" + relation_token + ":field:" + field_token


def _filter_property_fields(filters: Mapping[str, object]) -> Set[str]:
    """Return stored properties read by a RuleBox filter expression.

    ``minValue`` and ``maxValue`` are RuleBox comparison operators over the
    stored ``value`` property, not independent ABox fields. Keeping that
    distinction lets a key-level value change rerun its rule without treating
    every key-level property update as structural churn.
    """
    helper_keys = {
        "equals", "operator", "values", "contains", "not", "any", "all",
        "oneof", "range",
    }
    fields: Set[str] = set()
    for raw_key in dict(filters or {}):
        normalized = re.sub(r"[^a-z0-9]", "", _lower(raw_key))
        if not normalized or normalized in helper_keys:
            continue
        if normalized in {"minvalue", "maxvalue", "value"}:
            fields.add("value")
        else:
            fields.add(_clean(raw_key))
    return {field for field in fields if field}


def _condition_dependency_keys(
    condition_kind: str,
    field: str,
    relation_type: str,
    target_kind: str,
    target_filters: Mapping[str, object],
    relation_filters: Mapping[str, object],
) -> Set[str]:
    """Map a RuleBox condition to value-free ABox dependency identities.

    The key set intentionally names only observable ABox structure, never a
    threshold or a conclusion. A rule with a missing structural identity
    remains conservative and is selected by its broad family.
    """
    keys: Set[str] = set()
    relation_token = _dependency_token(relation_type)
    kind_token = _dependency_token(target_kind)

    def add_property_key(value: object) -> None:
        if target_kind:
            key = _dependency_kind_field_key(target_kind, value)
        elif relation_type:
            key = _dependency_relation_field_key(relation_type, value)
        elif condition_kind in {"subject_property", "property", "field"}:
            # RuleBox subject-property conditions are evaluated on the stock
            # subject, rather than on an unrelated macro or evidence entity
            # that happens to expose a field with the same name.
            key = _dependency_kind_field_key("stock", value)
        else:
            key = _dependency_field_key(value)
        if key:
            keys.add(key)
    for value in _list(field):
        add_property_key(value)
    if relation_token:
        keys.add("relation:" + relation_token)
    if kind_token:
        keys.add("kind:" + kind_token)
        # A relation that names only a target kind has no narrower property
        # contract. Any value change on that exact kind can alter the native
        # result, while a change on another kind must not reopen it.
        if not field and not target_filters and not relation_filters:
            keys.add("kind:" + kind_token + ":values")
    for value in _filter_property_fields(target_filters):
        add_property_key(value)
    for value in _filter_property_fields(relation_filters):
        add_property_key(value)
    return keys


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
        "dependencyKeys": sorted(_condition_dependency_keys(
            kind,
            field,
            relation_type,
            target_kind,
            target_filters,
            relation_filters,
        )),
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
    dependency_keys = sorted({
        key
        for item in condition_profiles
        for key in item.get("dependencyKeys") or []
        if _clean(key)
    })
    conservative = any(bool(item.get("conservative")) for item in condition_profiles) or not families
    if conservative and "unknown" not in families:
        families.append("unknown")
    return {
        "ruleId": rule_id,
        "enabled": enabled,
        "scopeFamilies": sorted(families),
        "dependencyKeys": dependency_keys,
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


def _semantic_dependency_fingerprints(item: Mapping[str, object]) -> Dict[str, str]:
    if _clean(item.get("semanticDependencyFingerprintVersion")) != DEPENDENCY_FINGERPRINT_VERSION:
        return {}
    raw = (
        dict(item.get("semanticDependencyFingerprints") or {})
        if isinstance(item, Mapping)
        else {}
    )
    return {
        _clean(key): _clean(fingerprint)
        for key, fingerprint in raw.items()
        if _clean(key) and _clean(fingerprint)
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


def _semantic_dependency_changes(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> Optional[Set[str]]:
    """Return changed RuleBox dependency identities or ``None`` for legacy scopes."""
    before_fingerprints = _semantic_dependency_fingerprints(before)
    after_fingerprints = _semantic_dependency_fingerprints(after)
    if not before_fingerprints or not after_fingerprints:
        return None
    return {
        key
        for key in set(before_fingerprints) | set(after_fingerprints)
        if before_fingerprints.get(key) != after_fingerprints.get(key)
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
    dependency_changes_by_scope: Dict[str, List[str]] = {}
    dependency_fingerprint_missing_scope_ids: List[str] = []
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
        dependency_changes = _semantic_dependency_changes(before, after)
        if dependency_changes is None:
            dependency_fingerprint_missing_scope_ids.append(scope_id)
        elif dependency_changes:
            dependency_changes_by_scope[scope_id] = sorted(dependency_changes)
    direct_changed = sorted(set(added + removed + changed))
    for scope_id in added:
        after_dependencies = _semantic_dependency_fingerprints(current.get(scope_id) or {})
        if after_dependencies:
            dependency_changes_by_scope[scope_id] = sorted(after_dependencies)
        else:
            dependency_fingerprint_missing_scope_ids.append(scope_id)
    for scope_id in removed:
        before_dependencies = _semantic_dependency_fingerprints(previous.get(scope_id) or {})
        if before_dependencies:
            dependency_changes_by_scope[scope_id] = sorted(before_dependencies)
        else:
            dependency_fingerprint_missing_scope_ids.append(scope_id)
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
    relation_context_scope_ids: Set[str] = set()
    unresolved_relation_scope_ids: List[str] = []
    for scope_id in direct_changed:
        if scope_type(scope_id) not in RELATION_LOCAL_SCOPE_TYPES:
            continue
        item = current.get(scope_id) or previous.get(scope_id) or {}
        dependencies = {
            _clean(dependency)
            for dependency in item.get("dependencyScopeIds") or []
            if _clean(dependency)
        }
        symbol_dependencies = {dependency for dependency in dependencies if scope_symbol(dependency)}
        if symbol_dependencies:
            relation_context_scope_ids.update(symbol_dependencies)
        else:
            unresolved_relation_scope_ids.append(scope_id)
    active_relation_context_scope_ids = sorted(
        scope_id for scope_id in relation_context_scope_ids if scope_id in current
    )
    active_affected = sorted(set(active_affected) | set(active_relation_context_scope_ids))
    direct_families = sorted({
        token
        for scope_id in direct_changed
        for token in (
            set(semantic_changes_by_scope.get(scope_id) or [])
            or _scope_plan_family_tokens(scope_id, current.get(scope_id) or previous.get(scope_id) or {})
        )
    })
    direct_dependency_keys = sorted({
        key
        for scope_id in direct_changed
        for key in dependency_changes_by_scope.get(scope_id) or []
        if _clean(key)
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
    relation_context_symbols = sorted({
        symbol
        for scope_id in active_relation_context_scope_ids
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
        "semanticChangedDependencyKeysByScope": dependency_changes_by_scope,
        "directChangedDependencyKeys": direct_dependency_keys,
        "dependencyFingerprintCoverageComplete": bool(
            direct_changed and not dependency_fingerprint_missing_scope_ids
        ),
        "dependencyFingerprintMissingScopeIds": sorted(set(dependency_fingerprint_missing_scope_ids)),
        "dependencyAffectedScopeFamilies": sorted(set(affected_families) - set(direct_families)),
        "affectedScopeFamilies": affected_families,
        "changedSymbols": direct_symbols,
        "directChangedSymbols": direct_symbols,
        "dependencyAffectedSymbols": sorted(set(affected_symbols) - set(direct_symbols)),
        "relationContextScopeIds": active_relation_context_scope_ids,
        "relationContextSymbols": relation_context_symbols,
        "unresolvedRelationScopeIds": sorted(unresolved_relation_scope_ids),
    }


def _families_intersect(left: Iterable[object], right: Iterable[object]) -> bool:
    left_values = {str(value or "") for value in left or []}
    right_values = {str(value or "") for value in right or []}
    if left_values & right_values:
        return True
    if "macro" in right_values and any(value.startswith("macro-") or value == "macro" for value in left_values):
        return True
    return "macro" in left_values and any(value.startswith("macro-") for value in right_values)


def _rule_may_depend_on(
    profile: Mapping[str, object],
    changed_families: Set[str],
    changed_dependency_keys: Set[str] = None,
    dependency_fingerprint_coverage_complete: bool = False,
) -> bool:
    families = {str(value or "") for value in profile.get("scopeFamilies") or []}
    if not changed_families or "unknown" in families or "state" in changed_families:
        return True
    if not _families_intersect(families, changed_families):
        return False
    if not dependency_fingerprint_coverage_complete:
        return True
    changed_keys = {str(value or "") for value in changed_dependency_keys or [] if _clean(value)}
    if not changed_keys:
        return False
    conditions = [
        item for item in profile.get("conditionProfiles") or []
        if isinstance(item, Mapping)
        and _families_intersect(item.get("scopeFamilies") or [], changed_families)
    ]
    if not conditions:
        return False
    for condition in conditions:
        if bool(condition.get("conservative")):
            return True
        dependency_keys = {
            str(value or "")
            for value in condition.get("dependencyKeys") or []
            if _clean(value)
        }
        if not dependency_keys or dependency_keys & changed_keys:
            return True
    return False


def _clean_family_values(values: Iterable[object]) -> List[str]:
    """Normalize event provenance without treating it as a decision input."""
    known = set(SYMBOL_SCOPE_FAMILIES) | {
        "macro",
        "macro-market",
        "macro-fx",
        "macro-rates",
        "macro-crypto",
        "portfolio",
        "policy",
        "reference",
        "episode",
        "unknown",
    }
    cleaned = {
        _lower(value)
        for value in values or []
        if _lower(value) in known
    }
    return sorted(cleaned)


def _expanded_family_values(values: Iterable[object]) -> Set[str]:
    expanded = set(_clean_family_values(values))
    specific_macro_families = {
        value for value in expanded
        if value.startswith("macro-")
    }
    if specific_macro_families:
        # Scope deltas retain the generic ``macro`` token alongside the
        # concrete macro family for dependency routing.  For provenance
        # comparison the concrete value is more precise, so avoid treating a
        # single rate update as every macro family changing.
        expanded.discard("macro")
    elif "macro" in expanded:
        expanded.update({"macro-market", "macro-fx", "macro-rates", "macro-crypto"})
    return expanded


def global_scope_impact_partition(
    delta: Mapping[str, object],
    global_scope_ids: Iterable[object],
) -> Dict[str, List[str]]:
    """Classify global scopes without turning their values into a Python rule.

    A semantic fingerprint limited to ``quality`` means source freshness,
    coverage, or validation metadata changed while the represented market /
    portfolio value did not. Older manifests, additions, removals, and mixed
    changes remain value-impacting conservatively.
    """
    semantic_by_scope = delta.get("semanticChangedFamiliesByScope")
    semantic_by_scope = semantic_by_scope if isinstance(semantic_by_scope, Mapping) else {}
    quality_only: List[str] = []
    context_only: List[str] = []
    value: List[str] = []
    for raw_scope_id in global_scope_ids or []:
        scope_id = _clean(raw_scope_id)
        if not scope_id:
            continue
        semantic = {
            _lower(family)
            for family in semantic_by_scope.get(scope_id, [])
            if _clean(family)
        }
        current_scope_type = scope_type(scope_id)
        if semantic and semantic <= {"quality"}:
            quality_only.append(scope_id)
        elif current_scope_type in RELATION_LOCAL_SCOPE_TYPES:
            # A relation without a symbol endpoint can still be relevant to a
            # requested subject, but a link/evidence topology update is not a
            # changed macro, portfolio, or policy value. Keep it in the ABox
            # and route its dependent RuleBox subset without promoting it to a
            # whole-world value reconciliation.
            context_only.append(scope_id)
        elif current_scope_type in {"reference", "episode"} and semantic and not any(
            family in {"position", "portfolio", "policy"} or family.startswith("macro-")
            for family in semantic
        ):
            # Reference and historical facts often carry evidence/profile
            # metadata shared by many symbols. Their dependency rules still
            # run, but the metadata alone must not make every update look like
            # a changed market or portfolio value.
            context_only.append(scope_id)
        else:
            value.append(scope_id)
    return {
        "qualityOnlyGlobalScopeIds": sorted(set(quality_only)),
        "contextOnlyGlobalScopeIds": sorted(set(context_only)),
        "globalValueScopeIds": sorted(set(value)),
    }


def event_scoped_routing_inputs(
    delta: Mapping[str, object],
    explicit_target_symbols: Iterable[object],
    requested_fact_families: Iterable[object],
    requested_fact_families_by_symbol: Mapping[str, Iterable[object]] = None,
) -> Dict[str, object]:
    """Keep a target event from reopening unrelated shared snapshot facts.

    The persisted monitor snapshot is intentionally complete, so it can
    contain a new FX quote, portfolio aggregate, or reference fact while a
    mailbox request is only about a target's quote or news. Every directly
    changed target scope remains eligible. For shared scopes, provenance must
    explicitly name the affected family before it is included in this turn.
    Deferred shared facts retain their own durable source event and periodic
    reconciliation; this function never decides an investment outcome.
    """
    targets = {
        _clean(symbol).upper()
        for symbol in explicit_target_symbols or []
        if _clean(symbol)
    }
    requested = _expanded_family_values(requested_fact_families)
    requested_by_symbol: Dict[str, Set[str]] = {}
    for raw_symbol, values in dict(requested_fact_families_by_symbol or {}).items():
        symbol = _clean(raw_symbol).upper()
        families = _expanded_family_values(values)
        if symbol and families:
            requested_by_symbol[symbol] = families
    if not targets or (not requested and not requested_by_symbol):
        return {
            "enabled": False,
            "scopeIds": [],
            "scopeFamilies": [],
            "dependencyKeys": [],
            "dependencyKeysComplete": False,
            "targetScopeNarrowed": False,
            "deferredSharedScopeIds": [],
            "deferredSharedScopeFamilies": [],
        }

    semantic_by_scope = delta.get("semanticChangedFamiliesByScope")
    semantic_by_scope = semantic_by_scope if isinstance(semantic_by_scope, Mapping) else {}
    dependency_by_scope = delta.get("semanticChangedDependencyKeysByScope")
    dependency_by_scope = dependency_by_scope if isinstance(dependency_by_scope, Mapping) else {}
    direct_scope_ids = [
        _clean(scope_id)
        for scope_id in delta.get("directChangedScopeIds") or []
        if _clean(scope_id)
    ]
    selected_scope_ids: List[str] = []
    selected_families: Set[str] = set()
    selected_dependency_keys: Set[str] = set()
    deferred_shared_scope_ids: List[str] = []
    deferred_shared_families: Set[str] = set()
    target_scope_narrowed = False

    for scope_id in direct_scope_ids:
        semantic = {
            _lower(family)
            for family in semantic_by_scope.get(scope_id, []) or []
            if _clean(family)
        }
        if not semantic:
            semantic = scope_family_tokens(scope_id)
        scope_target = scope_symbol(scope_id).upper()
        is_target_scope = scope_target in targets
        target_requested = requested_by_symbol.get(scope_target, requested)
        matched_families = {
            family
            for family in semantic
            if _families_intersect({family}, target_requested)
        }
        if is_target_scope:
            # Legacy requests carry only a batch-wide provenance list, so
            # preserve the conservative target-wide path for them. New
            # verified snapshots bind fact types to each symbol. That lets a
            # market turn avoid reopening an unrelated evidence/flow change
            # that happened to be present in the same persisted snapshot.
            included_families = matched_families if scope_target in requested_by_symbol else semantic
            if scope_target in requested_by_symbol and semantic - included_families:
                target_scope_narrowed = True
        else:
            included_families = matched_families
        if included_families:
            selected_scope_ids.append(scope_id)
            selected_families.update(included_families)
            # Dependency fingerprints are currently scoped, not family-keyed.
            # When a new per-symbol request intentionally narrows one scope,
            # retain safe family-level selection instead of incorrectly using
            # dependency keys from the omitted facts.
            if not (is_target_scope and scope_target in requested_by_symbol and semantic - included_families):
                selected_dependency_keys.update(
                    _clean(key)
                    for key in dependency_by_scope.get(scope_id, []) or []
                    if _clean(key)
                )
        if not is_target_scope:
            omitted_families = semantic - included_families
            if omitted_families:
                deferred_shared_scope_ids.append(scope_id)
                deferred_shared_families.update(omitted_families)

    # Do not claim an event boundary if it could not select any direct scope.
    # The caller falls back to its conservative whole-delta route in that
    # case, for example with an older opaque manifest.
    if not selected_scope_ids:
        return {
            "enabled": False,
            "scopeIds": [],
            "scopeFamilies": [],
            "dependencyKeys": [],
            "dependencyKeysComplete": False,
            "targetScopeNarrowed": False,
            "deferredSharedScopeIds": [],
            "deferredSharedScopeFamilies": [],
        }
    return {
        "enabled": bool(deferred_shared_scope_ids or target_scope_narrowed),
        "scopeIds": sorted(set(selected_scope_ids)),
        "scopeFamilies": sorted(selected_families),
        "dependencyKeys": sorted(selected_dependency_keys),
        "dependencyKeysComplete": not target_scope_narrowed,
        "targetScopeNarrowed": target_scope_narrowed,
        "deferredSharedScopeIds": sorted(set(deferred_shared_scope_ids)),
        "deferredSharedScopeFamilies": sorted(deferred_shared_families),
    }


def inference_impact_diagnostics(
    *,
    delta: Mapping[str, object],
    global_scope_ids: Iterable[object],
    global_impact: bool,
    bounded_global_context: bool,
    quality_scoped_global_context: bool = False,
    context_scoped_global_context: bool = False,
    quality_only_global_scope_ids: Iterable[object] = None,
    context_only_global_scope_ids: Iterable[object] = None,
    global_value_scope_ids: Iterable[object] = None,
    target_symbols: Iterable[object],
    changed_families: Iterable[object],
    requested_fact_families: Iterable[object],
    candidate_rule_count: int,
    enabled_rule_count: int,
    selection_eligibility_reason: str,
    changed_dependency_key_count: int = 0,
    dependency_fingerprint_coverage_complete: bool = False,
    event_scoped_rule_selection: bool = False,
    event_scoped_scope_ids: Iterable[object] = None,
    deferred_shared_scope_ids: Iterable[object] = None,
    deferred_shared_scope_families: Iterable[object] = None,
) -> Dict[str, object]:
    """Explain why a native cycle is broad without deciding what it means.

    This provides a stable diagnostic contract for the scheduler and mobile
    status screen. TypeDB remains responsible for all RuleBox evaluation.
    """
    scope_ids = [
        _clean(scope_id)
        for scope_id in global_scope_ids or []
        if _clean(scope_id)
    ]
    scope_type_counts: Dict[str, int] = {}
    for scope_id in scope_ids:
        value = scope_type(scope_id)
        scope_type_counts[value] = int(scope_type_counts.get(value, 0) or 0) + 1
    requested = _clean_family_values(requested_fact_families)
    changed = _clean_family_values(changed_families)
    requested_expanded = _expanded_family_values(requested)
    changed_expanded = _expanded_family_values(changed)
    unexpected = sorted(changed_expanded - requested_expanded) if requested else []
    candidate_ratio = round((max(0, int(candidate_rule_count or 0)) / max(1, int(enabled_rule_count or 0))) * 100, 1)
    reason_codes: List[str] = []
    quality_scope_ids = [_clean(scope_id) for scope_id in quality_only_global_scope_ids or [] if _clean(scope_id)]
    context_scope_ids = [_clean(scope_id) for scope_id in context_only_global_scope_ids or [] if _clean(scope_id)]
    value_scope_ids = [_clean(scope_id) for scope_id in global_value_scope_ids or [] if _clean(scope_id)]
    if global_impact:
        reason_codes.append("global-context-changed")
    if quality_scoped_global_context:
        reason_codes.append("quality-scoped-global-context")
    if context_scoped_global_context:
        reason_codes.append("context-scoped-global-context")
    if bounded_global_context:
        reason_codes.append("target-scoped-global-context")
    if event_scoped_rule_selection:
        reason_codes.append("event-scoped-shared-context-routing")
    if len(changed) >= 8:
        reason_codes.append("broad-fact-family-delta")
    if enabled_rule_count and candidate_ratio >= 95:
        reason_codes.append("candidate-catalog-is-complete")
    if requested and unexpected:
        reason_codes.append("snapshot-change-broader-than-event")
    if dependency_fingerprint_coverage_complete:
        reason_codes.append("dependency-fingerprint-routing")
    if not reason_codes:
        reason_codes.append("local-fact-change")
    if not requested:
        event_agreement = "not-provided"
    elif unexpected:
        event_agreement = "snapshot-broader-than-event"
    else:
        event_agreement = "aligned"
    classification = (
        "target-scoped-global-context"
        if bounded_global_context
        else "context-scoped-global-context"
        if context_scoped_global_context
        else "quality-scoped-global-context"
        if quality_scoped_global_context
        else "global-reconciliation"
        if global_impact
        else "dependency-selected"
    )
    return {
        "classification": classification,
        "reasonCodes": reason_codes,
        "globalScopeCount": len(scope_ids),
        "qualityOnlyGlobalScopeCount": len(quality_scope_ids),
        "contextOnlyGlobalScopeCount": len(context_scope_ids),
        "globalValueScopeCount": len(value_scope_ids),
        "globalScopeTypes": [
            {
                "type": value,
                "label": _GLOBAL_SCOPE_TYPE_LABELS.get(value, value),
                "count": count,
            }
            for value, count in sorted(scope_type_counts.items())
        ],
        "directChangedScopeCount": len(delta.get("directChangedScopeIds") or []),
        "affectedScopeCount": len(delta.get("affectedScopeIds") or []),
        "changedFamilyCount": len(changed),
        "changedDependencyKeyCount": max(0, int(changed_dependency_key_count or 0)),
        "dependencyFingerprintCoverageComplete": bool(dependency_fingerprint_coverage_complete),
        "targetSymbolCount": len([symbol for symbol in target_symbols or [] if _clean(symbol)]),
        "candidateRuleCount": max(0, int(candidate_rule_count or 0)),
        "enabledRuleCount": max(0, int(enabled_rule_count or 0)),
        "candidateRuleRatioPct": candidate_ratio,
        "candidateSubsetAvailable": bool(
            candidate_rule_count
            and enabled_rule_count
            and int(candidate_rule_count) < int(enabled_rule_count)
        ),
        "selectionEligibilityReason": _clean(selection_eligibility_reason),
        "eventFactFamilies": requested,
        "eventScopeAgreement": event_agreement,
        "unexpectedChangedFamilies": unexpected,
        "eventScopedRuleSelection": bool(event_scoped_rule_selection),
        "eventScopedScopeCount": len([scope_id for scope_id in event_scoped_scope_ids or [] if _clean(scope_id)]),
        "deferredSharedScopeCount": len([scope_id for scope_id in deferred_shared_scope_ids or [] if _clean(scope_id)]),
        "deferredSharedScopeFamilies": _clean_family_values(deferred_shared_scope_families),
    }


def build_inference_impact_plan(
    previous_scope_plan: Iterable[object],
    next_scope_plan: Iterable[object],
    snapshot_symbols: Iterable[object],
    explicit_target_symbols: Iterable[object] = None,
    rules: Iterable[object] = None,
    requested_fact_families: Iterable[object] = None,
    requested_fact_families_by_symbol: Mapping[str, Iterable[object]] = None,
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
    event_routing = event_scoped_routing_inputs(
        delta,
        explicit_symbols,
        requested_fact_families,
        requested_fact_families_by_symbol,
    )
    changed_scope_ids = list(delta.get("directChangedScopeIds") or delta.get("affectedScopeIds") or [])
    snapshot_global_scope_ids = sorted({
        scope_id
        for scope_id in changed_scope_ids
        if scope_type(scope_id) in GLOBAL_SCOPE_TYPES and not scope_symbol(scope_id)
    } | set(delta.get("unresolvedRelationScopeIds") or []))
    event_scope_ids = {
        _clean(scope_id)
        for scope_id in event_routing.get("scopeIds") or []
        if _clean(scope_id)
    }
    event_boundary_authoritative = bool(
        explicit_symbols
        and event_scope_ids
        and (
            _clean_family_values(requested_fact_families)
            or any(
                _clean_family_values(families)
                for families in dict(requested_fact_families_by_symbol or {}).values()
            )
        )
    )
    # A complete monitor snapshot may contain unrelated shared changes while
    # the durable mailbox item names one subject/fact family. Preserve those
    # shared changes in diagnostics and their own queue revisions, but do not
    # promote this subject turn to a market-wide execution boundary.
    global_scope_ids = (
        sorted(set(snapshot_global_scope_ids).intersection(event_scope_ids))
        if event_boundary_authoritative
        else list(snapshot_global_scope_ids)
    )
    deferred_global_scope_ids = sorted(
        set(snapshot_global_scope_ids).difference(global_scope_ids)
    )
    snapshot_global_partition = global_scope_impact_partition(
        delta,
        snapshot_global_scope_ids,
    )
    global_partition = global_scope_impact_partition(delta, global_scope_ids)
    quality_only_global_scope_ids = list(global_partition["qualityOnlyGlobalScopeIds"])
    context_only_global_scope_ids = list(global_partition["contextOnlyGlobalScopeIds"])
    global_value_scope_ids = list(global_partition["globalValueScopeIds"])
    # A quality-only change can invalidate data-confidence conclusions but
    # cannot by itself represent a changed macro/portfolio value. It stays
    # global ABox context while allowing the quality-dependent RuleBox subset.
    global_impact = bool(global_value_scope_ids)
    snapshot_global_impact = bool(snapshot_global_partition["globalValueScopeIds"])
    quality_scoped_global_context = bool(quality_only_global_scope_ids)
    context_scoped_global_context = bool(context_only_global_scope_ids)
    bounded_global_context = bool((global_impact or context_scoped_global_context) and explicit_symbols)
    impact_scope = (
        "MARKET_CONTEXT"
        if global_impact or context_scoped_global_context or quality_scoped_global_context
        else "SUBJECT"
    )
    impacted_symbols = (
        set(delta.get("directChangedSymbols") or delta.get("changedSymbols") or [])
        | set(delta.get("dependencyAffectedSymbols") or [])
        | set(delta.get("relationContextSymbols") or [])
    )
    # A realtime worker intentionally chooses one target at a time.  Link
    # scopes retain endpoint generations for immutable storage rebinding, so
    # their dependency closure can contain unrelated holdings even though the
    # requested subject is the only one being evaluated in this cycle.  The
    # complete ABox remains available to TypeDB as context; do not turn those
    # storage dependencies into additional native-rule subjects.  Each other
    # symbol keeps its own durable mailbox/event turn.
    if explicit_symbols:
        target_symbols = [symbol for symbol in available_symbols if symbol in explicit_symbols]
    else:
        # Shared context is a market-level revision, not an instruction to
        # enqueue every known subject. Only subjects proven by the changed
        # relation closure are evaluated here; catalog reconciliation has its
        # own bounded maintenance lane.
        target_symbols = [symbol for symbol in available_symbols if symbol in impacted_symbols]
    if not target_symbols and not changed_scope_ids:
        target_symbols = list(available_symbols)
    profiles = rule_dependency_profiles(rules or [])
    enabled_profiles = [profile for profile in profiles if profile.get("enabled")]
    changed_families = set(delta.get("directChangedScopeFamilies") or delta.get("changedScopeFamilies") or [])
    changed_dependency_keys = set(delta.get("directChangedDependencyKeys") or [])
    dependency_fingerprint_coverage_complete = bool(delta.get("dependencyFingerprintCoverageComplete"))
    event_scoped_rule_selection = bool(
        event_routing.get("enabled")
        and event_routing.get("scopeFamilies")
    )
    routing_dependency_fingerprint_coverage_complete = bool(
        dependency_fingerprint_coverage_complete
        and (
            event_routing.get("dependencyKeysComplete", True)
            if event_scoped_rule_selection
            else True
        )
    )
    routing_families = (
        set(event_routing.get("scopeFamilies") or [])
        if event_scoped_rule_selection
        else changed_families
    )
    routing_dependency_keys = (
        set(event_routing.get("dependencyKeys") or [])
        if event_scoped_rule_selection
        else changed_dependency_keys
    )
    candidate_profiles = [
        profile for profile in enabled_profiles
        if _rule_may_depend_on(
            profile,
            routing_families,
            routing_dependency_keys,
            routing_dependency_fingerprint_coverage_complete,
        )
    ]
    deferred_profiles = [
        profile for profile in enabled_profiles
        if profile not in candidate_profiles
    ]
    selection_eligibility_reason = ""
    if not candidate_profiles:
        selection_eligibility_reason = "candidate-rules-unavailable"
    elif len(candidate_profiles) >= len(enabled_profiles):
        # A prior-proof read cannot improve a complete native catalog run.
        # Skip it rather than paying the extra TypeDB read on every broad
        # market/portfolio generation.
        selection_eligibility_reason = "candidate-rules-cover-complete-catalog"
    diagnostics = inference_impact_diagnostics(
        delta=delta,
        global_scope_ids=global_scope_ids,
        global_impact=global_impact,
        bounded_global_context=bounded_global_context,
        quality_scoped_global_context=quality_scoped_global_context,
        context_scoped_global_context=context_scoped_global_context,
        quality_only_global_scope_ids=quality_only_global_scope_ids,
        context_only_global_scope_ids=context_only_global_scope_ids,
        global_value_scope_ids=global_value_scope_ids,
        target_symbols=target_symbols,
        changed_families=changed_families,
        requested_fact_families=requested_fact_families,
        candidate_rule_count=len(candidate_profiles),
        enabled_rule_count=len(enabled_profiles),
        selection_eligibility_reason=selection_eligibility_reason,
        changed_dependency_key_count=len(routing_dependency_keys),
        dependency_fingerprint_coverage_complete=routing_dependency_fingerprint_coverage_complete,
        event_scoped_rule_selection=event_scoped_rule_selection,
        event_scoped_scope_ids=event_routing.get("scopeIds") or [],
        deferred_shared_scope_ids=event_routing.get("deferredSharedScopeIds") or [],
        deferred_shared_scope_families=event_routing.get("deferredSharedScopeFamilies") or [],
    )
    diagnostics.update({
        "eventBoundaryAuthoritative": event_boundary_authoritative,
        "snapshotGlobalImpact": snapshot_global_impact,
        "snapshotGlobalScopeCount": len(snapshot_global_scope_ids),
        "deferredGlobalScopeCount": len(deferred_global_scope_ids),
        "deferredGlobalScopeIds": deferred_global_scope_ids,
    })
    if deferred_global_scope_ids:
        diagnostics["reasonCodes"] = list(diagnostics.get("reasonCodes") or []) + [
            "event-boundary-deferred-unrelated-global-context"
        ]
    impact_domains: List[str] = []
    if target_symbols or explicit_symbols:
        impact_domains.append("SUBJECT")
    effective_global_types = {scope_type(scope_id) for scope_id in global_scope_ids}
    if "macro" in effective_global_types:
        impact_domains.append("MARKET_CONTEXT")
    if "portfolio" in effective_global_types:
        impact_domains.append("PORTFOLIO")
    if effective_global_types.intersection({"policy", "reference", "episode"}):
        impact_domains.append("SHARED_CONTEXT")
    if not impact_domains:
        impact_domains.append("SUBJECT")
    return {
        "version": CHANGE_IMPACT_VERSION,
        "scopeDelta": delta,
        # Compatibility field: this now represents only the effective event
        # boundary. The complete snapshot observation is retained separately
        # and must never widen a subject turn by itself.
        "globalImpact": global_impact,
        "snapshotGlobalImpact": snapshot_global_impact,
        "eventBoundaryAuthoritative": event_boundary_authoritative,
        "impactScope": impact_scope,
        "impactDomains": impact_domains,
        "qualityScopedGlobalContext": quality_scoped_global_context,
        "contextScopedGlobalContext": context_scoped_global_context,
        "boundedGlobalContext": bounded_global_context,
        "globalImpactScopeIds": global_scope_ids,
        "snapshotGlobalImpactScopeIds": snapshot_global_scope_ids,
        "deferredGlobalImpactScopeIds": deferred_global_scope_ids,
        "qualityOnlyGlobalScopeIds": quality_only_global_scope_ids,
        "contextOnlyGlobalScopeIds": context_only_global_scope_ids,
        "globalValueScopeIds": global_value_scope_ids,
        "explicitTargetSymbols": explicit_symbols,
        "inferenceTargetSymbols": target_symbols,
        "candidateRuleIds": [str(profile.get("ruleId") or "") for profile in candidate_profiles],
        "deferredRuleIds": [str(profile.get("ruleId") or "") for profile in deferred_profiles],
        "candidateRuleCount": len(candidate_profiles),
        "ruleDependencyCount": len(profiles),
        "enabledRuleCount": len(enabled_profiles),
        "changedScopeFamilies": sorted(changed_families),
        "changedDependencyKeys": sorted(changed_dependency_keys),
        "routingScopeFamilies": sorted(routing_families),
        "routingDependencyKeys": sorted(routing_dependency_keys),
        "dependencyFingerprintCoverageComplete": dependency_fingerprint_coverage_complete,
        "routingDependencyFingerprintCoverageComplete": routing_dependency_fingerprint_coverage_complete,
        "requestedFactFamilies": _clean_family_values(requested_fact_families),
        "requestedFactFamiliesBySymbol": {
            _clean(symbol).upper(): _clean_family_values(values)
            for symbol, values in dict(requested_fact_families_by_symbol or {}).items()
            if _clean(symbol) and _clean_family_values(values)
        },
        "eventScopedRuleSelection": event_scoped_rule_selection,
        "eventScopedScopeIds": list(event_routing.get("scopeIds") or []),
        "deferredSharedContextScopeIds": list(event_routing.get("deferredSharedScopeIds") or []),
        "deferredSharedContextFamilies": list(event_routing.get("deferredSharedScopeFamilies") or []),
        "relationContextSymbols": list(delta.get("relationContextSymbols") or []),
        "unresolvedRelationScopeIds": list(delta.get("unresolvedRelationScopeIds") or []),
        "ruleExecutionScope": (
            "market-context-dependency-selected-native-evaluation"
            if impact_scope == "MARKET_CONTEXT"
            else "subject-dependency-selected-native-evaluation"
        ),
        "nativeRuleSelectionEligible": bool(
            candidate_profiles
            and len(candidate_profiles) < len(enabled_profiles)
        ),
        "nativeRuleSelectionEligibilityReason": selection_eligibility_reason or "candidate-subset-within-safe-context",
        "nativeRuleSelectionApplied": False,
        "diagnostics": diagnostics,
        "reason": (
            "공유 시장 맥락은 별도 리비전으로 유지하고 관계로 연결된 종목의 관련 RuleBox만 TypeDB에서 재확인합니다."
            if impact_scope == "MARKET_CONTEXT"
            else "직접 변경된 ABox 사실군과 연결된 후보 RuleBox만 TypeDB에서 재확인합니다."
        ),
    }


def compact_inference_impact_plan(plan: Mapping[str, object], limit: int = 80) -> Dict[str, object]:
    """Keep audit, TypeDB metadata, and diagnostics bounded."""
    values = dict(plan or {})
    if not values:
        return {}
    delta = dict(values.get("scopeDelta") or {})
    diagnostics = values.get("diagnostics")
    diagnostics = dict(diagnostics or {}) if isinstance(diagnostics, Mapping) else {}
    bounded = max(1, int(limit or 80))
    return {
        "version": str(values.get("version") or CHANGE_IMPACT_VERSION),
        "globalImpact": bool(values.get("globalImpact")),
        "snapshotGlobalImpact": bool(values.get("snapshotGlobalImpact")),
        "eventBoundaryAuthoritative": bool(values.get("eventBoundaryAuthoritative")),
        "impactScope": str(values.get("impactScope") or "SUBJECT"),
        "impactDomains": list(values.get("impactDomains") or [])[:8],
        "qualityScopedGlobalContext": bool(values.get("qualityScopedGlobalContext")),
        "contextScopedGlobalContext": bool(values.get("contextScopedGlobalContext")),
        "boundedGlobalContext": bool(values.get("boundedGlobalContext")),
        "globalImpactScopeIds": list(values.get("globalImpactScopeIds") or [])[:bounded],
        "snapshotGlobalImpactScopeIds": list(values.get("snapshotGlobalImpactScopeIds") or [])[:bounded],
        "deferredGlobalImpactScopeIds": list(values.get("deferredGlobalImpactScopeIds") or [])[:bounded],
        "qualityOnlyGlobalScopeIds": list(values.get("qualityOnlyGlobalScopeIds") or [])[:bounded],
        "contextOnlyGlobalScopeIds": list(values.get("contextOnlyGlobalScopeIds") or [])[:bounded],
        "globalValueScopeIds": list(values.get("globalValueScopeIds") or [])[:bounded],
        "explicitTargetSymbols": list(values.get("explicitTargetSymbols") or [])[:bounded],
        "inferenceTargetSymbols": list(values.get("inferenceTargetSymbols") or [])[:bounded],
        "candidateRuleIds": list(values.get("candidateRuleIds") or [])[:bounded],
        "deferredRuleIds": list(values.get("deferredRuleIds") or [])[:bounded],
        "candidateRuleCount": int(values.get("candidateRuleCount") or 0),
        "ruleDependencyCount": int(values.get("ruleDependencyCount") or 0),
        "enabledRuleCount": int(values.get("enabledRuleCount") or 0),
        "changedScopeFamilies": list(values.get("changedScopeFamilies") or [])[:bounded],
        "changedDependencyKeys": list(values.get("changedDependencyKeys") or [])[:bounded],
        "routingScopeFamilies": list(values.get("routingScopeFamilies") or [])[:bounded],
        "routingDependencyKeys": list(values.get("routingDependencyKeys") or [])[:bounded],
        "dependencyFingerprintCoverageComplete": bool(values.get("dependencyFingerprintCoverageComplete")),
        "routingDependencyFingerprintCoverageComplete": bool(values.get("routingDependencyFingerprintCoverageComplete")),
        "requestedFactFamilies": list(values.get("requestedFactFamilies") or [])[:bounded],
        "requestedFactFamiliesBySymbol": {
            _clean(symbol).upper(): list(families or [])[:30]
            for symbol, families in dict(values.get("requestedFactFamiliesBySymbol") or {}).items()
            if _clean(symbol) and isinstance(families, (list, tuple, set))
        },
        "eventScopedRuleSelection": bool(values.get("eventScopedRuleSelection")),
        "eventScopedScopeIds": list(values.get("eventScopedScopeIds") or [])[:bounded],
        "deferredSharedContextScopeIds": list(values.get("deferredSharedContextScopeIds") or [])[:bounded],
        "deferredSharedContextFamilies": list(values.get("deferredSharedContextFamilies") or [])[:bounded],
        "relationContextSymbols": list(values.get("relationContextSymbols") or [])[:bounded],
        "unresolvedRelationScopeIds": list(values.get("unresolvedRelationScopeIds") or [])[:bounded],
        "ruleExecutionScope": str(
            values.get("ruleExecutionScope")
            or "subject-dependency-selected-native-evaluation"
        ),
        "nativeRuleSelectionEligible": bool(values.get("nativeRuleSelectionEligible")),
        "nativeRuleSelectionEligibilityReason": str(values.get("nativeRuleSelectionEligibilityReason") or ""),
        "nativeRuleSelectionApplied": bool(values.get("nativeRuleSelectionApplied")),
        "diagnostics": {
            "classification": str(diagnostics.get("classification") or ""),
            "reasonCodes": list(diagnostics.get("reasonCodes") or [])[:bounded],
            "globalScopeCount": int(diagnostics.get("globalScopeCount") or 0),
            "qualityOnlyGlobalScopeCount": int(diagnostics.get("qualityOnlyGlobalScopeCount") or 0),
            "contextOnlyGlobalScopeCount": int(diagnostics.get("contextOnlyGlobalScopeCount") or 0),
            "globalValueScopeCount": int(diagnostics.get("globalValueScopeCount") or 0),
            "globalScopeTypes": [
                {
                    "type": str(item.get("type") or ""),
                    "label": str(item.get("label") or ""),
                    "count": int(item.get("count") or 0),
                }
                for item in diagnostics.get("globalScopeTypes") or []
                if isinstance(item, Mapping)
            ][:bounded],
            "directChangedScopeCount": int(diagnostics.get("directChangedScopeCount") or 0),
            "affectedScopeCount": int(diagnostics.get("affectedScopeCount") or 0),
            "changedFamilyCount": int(diagnostics.get("changedFamilyCount") or 0),
            "changedDependencyKeyCount": int(diagnostics.get("changedDependencyKeyCount") or 0),
            "dependencyFingerprintCoverageComplete": bool(diagnostics.get("dependencyFingerprintCoverageComplete")),
            "targetSymbolCount": int(diagnostics.get("targetSymbolCount") or 0),
            "candidateRuleCount": int(diagnostics.get("candidateRuleCount") or 0),
            "enabledRuleCount": int(diagnostics.get("enabledRuleCount") or 0),
            "candidateRuleRatioPct": float(diagnostics.get("candidateRuleRatioPct") or 0.0),
            "candidateSubsetAvailable": bool(diagnostics.get("candidateSubsetAvailable")),
            "selectionEligibilityReason": str(diagnostics.get("selectionEligibilityReason") or ""),
            "eventFactFamilies": list(diagnostics.get("eventFactFamilies") or [])[:bounded],
            "eventScopeAgreement": str(diagnostics.get("eventScopeAgreement") or ""),
            "unexpectedChangedFamilies": list(diagnostics.get("unexpectedChangedFamilies") or [])[:bounded],
            "eventScopedRuleSelection": bool(diagnostics.get("eventScopedRuleSelection")),
            "eventScopedScopeCount": int(diagnostics.get("eventScopedScopeCount") or 0),
            "deferredSharedScopeCount": int(diagnostics.get("deferredSharedScopeCount") or 0),
            "deferredSharedScopeFamilies": list(diagnostics.get("deferredSharedScopeFamilies") or [])[:bounded],
            "eventBoundaryAuthoritative": bool(diagnostics.get("eventBoundaryAuthoritative")),
            "snapshotGlobalImpact": bool(diagnostics.get("snapshotGlobalImpact")),
            "snapshotGlobalScopeCount": int(diagnostics.get("snapshotGlobalScopeCount") or 0),
            "deferredGlobalScopeCount": int(diagnostics.get("deferredGlobalScopeCount") or 0),
            "deferredGlobalScopeIds": list(diagnostics.get("deferredGlobalScopeIds") or [])[:bounded],
        },
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
            "semanticChangedDependencyKeysByScope": {
                str(scope_id or ""): list(keys or [])[:bounded]
                for scope_id, keys in dict(delta.get("semanticChangedDependencyKeysByScope") or {}).items()
                if str(scope_id or "").strip()
            },
            "directChangedDependencyKeys": list(delta.get("directChangedDependencyKeys") or [])[:bounded],
            "dependencyFingerprintCoverageComplete": bool(delta.get("dependencyFingerprintCoverageComplete")),
            "dependencyFingerprintMissingScopeIds": list(delta.get("dependencyFingerprintMissingScopeIds") or [])[:bounded],
            "dependencyAffectedScopeFamilies": list(delta.get("dependencyAffectedScopeFamilies") or [])[:bounded],
            "affectedScopeFamilies": list(delta.get("affectedScopeFamilies") or [])[:bounded],
            "changedSymbols": list(delta.get("changedSymbols") or [])[:bounded],
            "directChangedSymbols": list(delta.get("directChangedSymbols") or [])[:bounded],
            "dependencyAffectedSymbols": list(delta.get("dependencyAffectedSymbols") or [])[:bounded],
        },
    }
