"""Ownership scoping for TypeDB-backed investment hypotheses.

This module does not evaluate a RuleBox condition or choose an investment
action.  It only records whether the *configured inputs* of an already
materialized TypeDB path are shareable market observations, account-private
context, or too ambiguous to promote as a shared market hypothesis.

The distinction protects the multi-account boundary: a loss rate, a holding
role, or an account risk budget must never become a market-wide fact merely
because the same ticker is held by several accounts.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Dict, Iterable, List, Mapping, Sequence


HYPOTHESIS_SCOPE_VERSION = "typedb-hypothesis-scope-v1"

MARKET_SHARED_SCOPE = "market-shared"
ACCOUNT_ONLY_SCOPE = "account-only"
MIXED_SCOPE = "mixed"
UNVERIFIED_SCOPE = "unverified"
SYSTEM_SAFETY_SCOPE = "system-safety"


def _clean(value: object) -> str:
    return str(value or "").strip()


def _normalized(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean(value).lower())


def _upper(value: object) -> str:
    return _clean(value).upper()


def _unique(values: Iterable[object], limit: int = 48) -> List[str]:
    result: List[str] = []
    for value in values or []:
        text = _clean(value)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


# These are ownership terms, not investment-rule terms.  A condition that
# reads one of them is private even when its TypeDB source node is a stock.
ACCOUNT_FIELDS = {
    "accountid",
    "portfolioid",
    "source",
    "isholding",
    "iswatchlist",
    "averageprice",
    "quantity",
    "sellablequantity",
    "marketvalue",
    "marketvaluekrw",
    "profitloss",
    "profitlosskrw",
    "profitlossrate",
    "positionweight",
    "positionaccountweight",
    "positionrole",
    "positionintent",
    "investmentstrategyprofile",
    "strategylosstolerancepct",
    "riskbudget",
    "cash",
    "cashweight",
    "deliveryprofile",
    "notificationpreference",
    "executionplan",
    "decisionstage",
    "allowedactions",
    "blockedactions",
}

MARKET_FIELDS = {
    "currentprice",
    "pricechangerate",
    "changerate",
    "ma5",
    "ma20",
    "ma60",
    "ma5distance",
    "ma20distance",
    "ma60distance",
    "ma20slope",
    "ma60slope",
    "trendcurve",
    "volume",
    "volumeratio",
    "timeadjustedvolumeratio",
    "rawvolumeratio",
    "tradestrength",
    "bidaskimbalance",
    "smartmoneynetvolume",
    "foreignnetvolume",
    "institutionnetvolume",
    "individualnetvolume",
    "directnewscount",
    "directrisknewscount",
    "usdkrwrate",
    "us10yrate",
    "us2yrate",
    "fedfundsrate",
    "btcchange24h",
    "btcchange7d",
    "valuationfairvalue",
    "marginofsafetypct",
    "peratio",
    "pbrratio",
    "eps",
    "revenue",
    "latesttradingday",
    "adrpremiumpct",
    "adrratio",
}

ACCOUNT_RELATION_TYPES = {
    "HOLDS",
    "WATCHES",
    "HAS_POSITION",
    "HAS_CASH",
    "HAS_RISK_BUDGET",
    "HAS_INVESTMENT_STRATEGY",
    "HAS_DELIVERY_PROFILE",
    "HAS_POSITION_ROLE",
    "HAS_PROFIT_POLICY",
    "FITS_INVESTOR_RISK_PROFILE",
    "MATCHES_INVESTOR_PROFILE",
    "VIOLATES_RISK_TOLERANCE",
    "VIOLATES_STRATEGY_FIT",
    "ALLOWS_ACTION",
    "BLOCKS_ACTION",
    "HAS_ACTION_CANDIDATE",
    "CREATES_NOTIFICATION_INTENT",
}

MARKET_RELATION_TYPES = {
    "AFFECTS",
    "BREAKS_LEVEL",
    "CONFIRMS_EVENT_IMPACT",
    "CONFIRMS_RECOVERY",
    "CONFIRMS_WITH_FLOW",
    "DERIVES_TREND_EPISODE",
    "DIVERGES_FROM_FLOW",
    "EXPOSED_TO",
    "FAILS_RECOVERY",
    "HAS_ADR_PREMIUM",
    "HAS_ARCHETYPE",
    "HAS_BETA_TO",
    "HAS_CRYPTO_EXPOSURE",
    "HAS_DATA_QUALITY",
    "HAS_DILUTION_RISK",
    "HAS_EXTERNAL_SIGNAL",
    "HAS_FACTOR_EXPOSURE",
    "HAS_FACTOR_SENSITIVITY",
    "HAS_INSTRUMENT_PROFILE",
    "HAS_INVESTOR_FLOW_SENTIMENT",
    "HAS_LEVERAGED_FLOW_SIGNAL",
    "HAS_MACRO_REGIME",
    "HAS_MARGIN_OF_SAFETY",
    "HAS_OBSERVATION",
    "HAS_RATE_SENSITIVITY",
    "HAS_TECHNICAL_INDICATOR",
    "HAS_TEMPORAL_WINDOW",
    "HAS_TRADE_FLOW",
    "HAS_TREND_TRANSITION",
    "HAS_VALUATION",
    "HAS_VALUATION_OPPORTUNITY",
    "HAS_VALUATION_RISK",
    "RECLAIMS_LEVEL",
    "RETESTS_LEVEL",
    "SUPPORTS_THESIS",
    "WEAKENS_THESIS",
}

ACCOUNT_TARGET_KINDS = {
    "account",
    "portfolio",
    "position",
    "watchlist",
    "cash",
    "risk-budget",
    "risk-budget-breach",
    "investment-strategy-profile",
    "position-role",
    "profit-policy",
    "account-delivery-profile",
    "execution-plan",
    "action-candidate",
    "blocked-action",
    "execution-capacity",
    "loss-defense-evidence",
    "strategy-fit-assessment",
    "strategy-mismatch-risk",
}

MARKET_TARGET_KINDS = {
    "article-ai-analysis",
    "article-analysis-conflict",
    "article-quality-risk",
    "benchmark-index",
    "corporate-action",
    "coverage-gap",
    "cross-market-premium",
    "crypto-exposure",
    "disclosure-dilution-risk",
    "event-impact-confirmation",
    "execution-metric",
    "external-signal",
    "fact-change",
    "factor",
    "factor-sensitivity",
    "flow-confirmation",
    "flow-metric",
    "instrument-profile",
    "interest-rate",
    "investment-archetype",
    "investor-flow-sentiment",
    "key-level",
    "leveraged-flow-signal",
    "macro-regime",
    "margin-of-safety",
    "market-proxy-observation",
    "market-proxy-support",
    "missing-data",
    "opportunity",
    "recovery-confirmation",
    "recovery-failure",
    "research-evidence",
    "risk",
    "signal-conflict",
    "smart-money-flow",
    "technical-metric",
    "temporal-coverage-gap",
    "temporal-window",
    "trend-episode",
    "trend-transition",
    "validation-assessment",
    "valuation-assumption",
}


def _canonical(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple, set)):
        canonical_items = [_canonical(item) for item in value]
        return sorted(
            canonical_items,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    if isinstance(value, str):
        return " ".join(value.split()).lower()
    return value


def condition_shape_signature(condition: Mapping[str, object]) -> str:
    """Stable signature for ownership analysis, excluding observed values."""
    payload = {
        "kind": _normalized(condition.get("kind")),
        "role": _normalized(condition.get("role") or condition.get("conditionRole") or "required"),
        "field": _normalized(condition.get("field")),
        "operator": _clean(condition.get("operator") or "=="),
        "value": _canonical(condition.get("value")),
        "relationType": _upper(condition.get("relationType") or condition.get("relation_type")),
        "direction": _normalized(condition.get("direction") or "out"),
        "targetKind": _normalized(condition.get("targetKind") or condition.get("target_kind")),
        "targetPropertyFilters": _canonical(condition.get("targetPropertyFilters") or condition.get("target_property_filters") or {}),
        "relationPropertyFilters": _canonical(condition.get("relationPropertyFilters") or condition.get("relation_property_filters") or {}),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _field_references(value: object) -> List[str]:
    if not isinstance(value, Mapping):
        return []
    result: List[str] = []
    for key, item in value.items():
        normalized_key = _normalized(key)
        if normalized_key in ACCOUNT_FIELDS or normalized_key in MARKET_FIELDS:
            result.append(_clean(key))
        if normalized_key in {"field", "sourcefield", "targetfield"}:
            if isinstance(item, (list, tuple, set)):
                result.extend(_clean(candidate) for candidate in item)
            else:
                result.append(_clean(item))
        if isinstance(item, Mapping):
            result.extend(_field_references(item))
        elif isinstance(item, (list, tuple, set)):
            for candidate in item:
                result.extend(_field_references(candidate))
    return result


def _scope_override(condition: Mapping[str, object]) -> str:
    value = _normalized(
        condition.get("hypothesisScope")
        or condition.get("hypothesis_scope")
        or condition.get("inputScope")
        or condition.get("input_scope")
    )
    return {
        "market": "market",
        "marketshared": "market",
        "account": "account",
        "accountonly": "account",
        "mixed": "mixed",
        "unverified": "unverified",
    }.get(value, "")


def condition_scope_profile(condition: Mapping[str, object], index: int = 0) -> Dict[str, object]:
    """Classify one RuleBox condition by data ownership.

    Unknown shape is intentionally not treated as a market condition. A RuleBox
    editor can set ``hypothesisScope`` only when an otherwise opaque custom
    condition has a reviewed ownership contract.
    """
    condition = dict(condition or {})
    condition_id = _clean(condition.get("conditionId") or condition.get("condition_id")) or "condition-" + str(index + 1)
    field_values = [_clean(condition.get("field"))]
    for value in [
        condition.get("value"),
        condition.get("targetPropertyFilters") or condition.get("target_property_filters"),
        condition.get("relationPropertyFilters") or condition.get("relation_property_filters"),
    ]:
        field_values.extend(_field_references(value))
    normalized_fields = {_normalized(value) for value in field_values if _normalized(value)}
    relation_type = _upper(condition.get("relationType") or condition.get("relation_type"))
    target_kind = _normalized(condition.get("targetKind") or condition.get("target_kind"))
    explicit_scope = _scope_override(condition)
    has_account_structure = bool(
        normalized_fields.intersection(ACCOUNT_FIELDS)
        or relation_type in ACCOUNT_RELATION_TYPES
        or target_kind in ACCOUNT_TARGET_KINDS
    )
    has_market_structure = bool(
        normalized_fields.intersection(MARKET_FIELDS)
        or relation_type in MARKET_RELATION_TYPES
        or target_kind in MARKET_TARGET_KINDS
    )
    if explicit_scope == "mixed":
        scope = "mixed"
        source = "rulebox-explicit"
    elif explicit_scope == "market" and has_account_structure:
        scope = "unverified"
        source = "rulebox-scope-conflict"
    elif explicit_scope:
        scope = explicit_scope
        source = "rulebox-explicit"
    elif has_account_structure and has_market_structure:
        scope = "mixed"
        source = "structural"
    elif has_account_structure:
        scope = "account"
        source = "structural"
    elif has_market_structure:
        scope = "market"
        source = "structural"
    else:
        scope = "unverified"
        source = "structural-unknown"
    return {
        "conditionId": condition_id,
        "scope": scope,
        "scopeSource": source,
        "kind": _clean(condition.get("kind")),
        "role": _clean(condition.get("role") or condition.get("conditionRole") or "required"),
        "field": _clean(condition.get("field")),
        "relationType": relation_type,
        "targetKind": target_kind,
        "signature": condition_shape_signature(condition),
    }


def _condition_shapes(rows: Iterable[Mapping[str, object]]) -> List[Dict[str, object]]:
    shapes: List[Dict[str, object]] = []
    seen = set()
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        for condition in row.get("ruleConditionShapes") or row.get("rule_condition_shapes") or []:
            if not isinstance(condition, Mapping):
                continue
            signature = condition_shape_signature(condition)
            if signature in seen:
                continue
            seen.add(signature)
            shapes.append(dict(condition))
    return shapes


def _primary_relation_types(rows: Iterable[Mapping[str, object]]) -> List[str]:
    ignored = {
        "EXPLAINED_BY_TRACE",
        "HAS_INFERENCE_TRACE",
        "HAS_SIGNAL_CONFLICT",
        "HAS_WHY_NOW",
        "TRIGGERED_INFERENCE",
    }
    return sorted({
        _upper(row.get("type") or row.get("relationType") or row.get("relation_type"))
        for row in rows or []
        if isinstance(row, Mapping)
        and _upper(row.get("type") or row.get("relationType") or row.get("relation_type"))
        and _upper(row.get("type") or row.get("relationType") or row.get("relation_type")) not in ignored
    })


def inference_scope_assessment(
    traces: Iterable[Mapping[str, object]],
    matches: Iterable[Mapping[str, object]],
    relations: Iterable[Mapping[str, object]],
    stance: object = "",
) -> Dict[str, object]:
    """Return an auditable ownership assessment for an active TypeDB path."""
    shapes = _condition_shapes(list(traces or []) + list(matches or []))
    profiles = [condition_scope_profile(condition, index) for index, condition in enumerate(shapes)]
    scopes = {str(item.get("scope") or "unverified") for item in profiles}
    if not profiles:
        scope_state = UNVERIFIED_SCOPE
    elif "mixed" in scopes or ("market" in scopes and "account" in scopes):
        scope_state = MIXED_SCOPE
    elif "account" in scopes:
        scope_state = ACCOUNT_ONLY_SCOPE
    elif "unverified" in scopes:
        scope_state = UNVERIFIED_SCOPE
    else:
        scope_state = MARKET_SHARED_SCOPE
    market_profiles = [item for item in profiles if item.get("scope") == "market"]
    account_profiles = [item for item in profiles if item.get("scope") in {"account", "mixed"}]
    unverified_profiles = [item for item in profiles if item.get("scope") == "unverified"]
    relation_types = _primary_relation_types(relations)
    market_signature = ""
    if scope_state == MARKET_SHARED_SCOPE:
        signature_payload = {
            "version": HYPOTHESIS_SCOPE_VERSION,
            "stance": _normalized(stance),
            "relationTypes": relation_types,
            "conditions": sorted(item.get("signature") for item in market_profiles if item.get("signature")),
        }
        market_signature = "typedb-market-structural:" + hashlib.sha256(
            json.dumps(signature_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]
    return {
        "scopeVersion": HYPOTHESIS_SCOPE_VERSION,
        "scopeState": scope_state,
        "marketConditionCount": len(market_profiles),
        "accountConditionCount": len(account_profiles),
        "unverifiedConditionCount": len(unverified_profiles),
        "marketConditionIds": [item["conditionId"] for item in market_profiles],
        "accountConditionIds": [item["conditionId"] for item in account_profiles],
        "unverifiedConditionIds": [item["conditionId"] for item in unverified_profiles],
        "accountFields": sorted({item["field"] for item in account_profiles if item.get("field")}),
        "accountRelationTypes": sorted({item["relationType"] for item in account_profiles if item.get("relationType")}),
        "accountTargetKinds": sorted({item["targetKind"] for item in account_profiles if item.get("targetKind")}),
        "marketRelationTypes": relation_types,
        "marketCausalSignature": market_signature,
        "conditionProfiles": profiles,
    }
