"""Governed scheduling boundaries for TypeDB rule execution.

The values in this module select *where* and *when* a RuleBox function runs.
They never evaluate a rule condition or choose an investment action.  Keeping
the boundary in one domain contract prevents account-wide rules from being
repeated once per instrument and gives change routing, TypeDB execution and
the operator UI the same vocabulary.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping


RULE_EXECUTION_UNIT_VERSION = "ontology-rule-execution-unit-v1"
EVENT_CHANGE_CLASS_VERSION = "ontology-event-change-class-v1"

INSTRUMENT_GRAIN = "instrument"
ACCOUNT_GRAIN = "account"
MACRO_GRAIN = "macro"
WORLD_GRAIN = "world"

EXECUTION_GRAINS = frozenset({
    INSTRUMENT_GRAIN,
    ACCOUNT_GRAIN,
    MACRO_GRAIN,
    WORLD_GRAIN,
})

FAMILY_EVENT_CLASSES = {
    "market": "market-observation",
    "flow": "market-microstructure",
    "temporal": "market-observation",
    "evidence": "issuer-event",
    "fundamental": "issuer-fundamentals",
    "profile": "issuer-reference",
    "governance": "issuer-governance",
    "capital": "issuer-capital",
    "quality": "data-quality",
    "position": "portfolio-state",
    "portfolio": "portfolio-state",
    "policy": "portfolio-policy",
    "exposure": "portfolio-exposure",
    "episode": "portfolio-activity",
    "macro": "macro-observation",
    "macro-market": "macro-observation",
    "macro-rates": "macro-observation",
    "macro-fx": "macro-observation",
    "macro-crypto": "crypto-observation",
    "valuation": "issuer-valuation",
    "company-valuation": "issuer-valuation",
}

PORTFOLIO_STRUCTURAL_FIELDS = frozenset({
    "accountid", "averageprice", "availablequantity", "cash", "cashbalance",
    "currency", "holdingquantity", "mandate", "maxpositionweightpct",
    "maxsectorweightpct", "policy", "position", "quantity", "sellablequantity",
    "strategyprofile", "targetweight", "weightlimit",
})
PORTFOLIO_VALUATION_FIELDS = frozenset({
    "currentprice", "exchangerate", "fxrate", "marketvalue", "profitloss",
    "profitlossamount", "profitlossrate", "valuation", "valuationamount",
})


def _text(value: object) -> str:
    return str(value or "").strip()


def _value(subject: object, *names: str):
    if isinstance(subject, Mapping):
        for name in names:
            if name in subject:
                return subject.get(name)
        return None
    for name in names:
        if hasattr(subject, name):
            return getattr(subject, name)
    return None


def _field_token(value: object) -> str:
    text = _text(value)
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return "".join(character for character in text.lower() if character.isalnum())


def event_change_classes(
    families: Iterable[object],
    changed_fields: Iterable[object] = None,
) -> List[str]:
    """Classify factual changes without interpreting their market direction."""

    clean_families = {
        _text(value).lower().replace("_", "-")
        for value in families or []
        if _text(value)
    }
    classes = {
        FAMILY_EVENT_CLASSES[family]
        for family in clean_families
        if family in FAMILY_EVENT_CLASSES
    }
    if clean_families.intersection({"position", "portfolio", "exposure", "episode"}):
        fields = {_field_token(value) for value in changed_fields or [] if _field_token(value)}
        structural = bool(fields & PORTFOLIO_STRUCTURAL_FIELDS)
        valuation = bool(fields & PORTFOLIO_VALUATION_FIELDS)
        if structural:
            classes.add("portfolio-structure")
        if valuation and not structural:
            classes.add("portfolio-valuation")
    return sorted(classes)


def revision_vector_for_change(
    revision: object,
    families: Iterable[object],
    changed_fields: Iterable[object] = None,
) -> Dict[str, str]:
    """Build the smallest durable revision vector for result-slot reuse."""

    clean_revision = _text(revision)[:191]
    if not clean_revision:
        return {}
    classes = event_change_classes(families, changed_fields)
    if not classes:
        return {"unknown": clean_revision}
    return {change_class: clean_revision for change_class in classes}


def rule_execution_unit(
    rule: object,
    families: Iterable[object] = None,
    module: object = "",
    assessment_scope: object = "",
) -> Dict[str, object]:
    """Return one immutable execution owner for a RuleBox rule.

    ``source_kind`` is the primary ownership signal.  A portfolio source is
    account-wide and therefore runs once per account generation.  A stock or
    security source remains instrument-scoped even when macro context is one
    of its inputs; the macro observation only invalidates the affected rule.
    """

    source_kind = _text(_value(rule, "source_kind", "sourceKind") or "stock").lower()
    rule_id = _text(_value(rule, "rule_id", "ruleId"))
    clean_families = sorted({
        _text(value).lower().replace("_", "-")
        for value in families or []
        if _text(value)
    })
    module_text = _text(module).lower()
    scope_text = _text(assessment_scope).lower()

    if source_kind in {"portfolio", "account"}:
        grain = ACCOUNT_GRAIN
        owner_world = "portfolio-overlay"
    elif source_kind in {"macro", "market-context"}:
        grain = MACRO_GRAIN
        owner_world = "macro-context"
    elif source_kind in {"stock", "security", "instrument", "company"}:
        grain = INSTRUMENT_GRAIN
        owner_world = "shared-instrument"
    elif clean_families and all(value.startswith("macro") for value in clean_families):
        grain = MACRO_GRAIN
        owner_world = "macro-context"
    else:
        grain = WORLD_GRAIN
        owner_world = "shared-premise"

    trigger_classes = event_change_classes(clean_families)
    cadence = "on-change"
    if grain == ACCOUNT_GRAIN and (
        "rebalance" in rule_id or module_text == "allocation-rebalance"
    ):
        cadence = "portfolio-review-window"
    elif grain == MACRO_GRAIN:
        cadence = "macro-release-or-threshold-change"

    single_evaluation_key = {
        ACCOUNT_GRAIN: "accountId",
        INSTRUMENT_GRAIN: "symbol",
        MACRO_GRAIN: "marketId",
        WORLD_GRAIN: "worldId",
    }[grain]
    return {
        "version": RULE_EXECUTION_UNIT_VERSION,
        "evaluationGrain": grain,
        "ownerWorld": owner_world,
        "triggerEventClasses": trigger_classes,
        "cadence": cadence,
        "incrementalEligible": bool(grain != WORLD_GRAIN and trigger_classes),
        "singleEvaluationKey": single_evaluation_key,
        "keyFields": [single_evaluation_key],
        "subjectFanoutAllowed": grain == INSTRUMENT_GRAIN,
        "assessmentScope": scope_text,
    }


def rule_evaluation_grain(rule: object) -> str:
    manifest = _value(rule, "domain_manifest", "domainManifest")
    if isinstance(manifest, Mapping):
        grain = _text(manifest.get("evaluationGrain") or manifest.get("evaluation_grain"))
        if grain in EXECUTION_GRAINS:
            return grain
    resolved = getattr(rule, "resolved_domain_manifest", None)
    if isinstance(resolved, Mapping):
        grain = _text(resolved.get("evaluationGrain"))
        if grain in EXECUTION_GRAINS:
            return grain
    return _text(rule_execution_unit(rule).get("evaluationGrain")) or WORLD_GRAIN


def rules_allow_subject_fanout(rules: Iterable[object]) -> bool:
    """Subject fanout is safe only when every rule is instrument-owned."""

    values = [rule for rule in rules or []]
    return bool(values) and all(
        rule_evaluation_grain(rule) == INSTRUMENT_GRAIN
        for rule in values
    )
