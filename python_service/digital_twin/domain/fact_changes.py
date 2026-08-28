import hashlib
import json
import math
from typing import Dict, Iterable, List, Mapping, Optional, Set

from .ontology_execution_units import (
    EVENT_CHANGE_CLASS_VERSION,
    event_change_classes,
)



VOLATILE_FACT_KEYS = {
    "collectedAt",
    "collected_at",
    "collectionSource",
    "collectionPurpose",
    "createdAt",
    "created_at",
    "firstSeenAt",
    "first_seen_at",
    "lastSeenAt",
    "last_seen_at",
    "observedAt",
    "observed_at",
    "updatedAt",
    "updated_at",
}

MARKET_FACT_FIELDS = (
    "currentPrice",
    "changeRate",
    "volume",
    "volumeRatio",
    "tradingValue",
    "tradeStrength",
    "bidAskImbalance",
    "orderbookImbalance",
    "ma5",
    "ma20",
    "ma60",
    "ma120",
    "ma200",
    "ma20Slope",
    "ma60Slope",
    "ma20Distance",
    "ma60Distance",
    "quoteStatus",
    "dataQuality",
    # A source-validity state is evidence, unlike an ingestion timestamp.
    # It lets the ABox distinguish an actual tick from a last-close or
    # unavailable reference without treating every poll as a new fact.
    "freshnessStatus",
    "sourceTimestampState",
    "latencyStatus",
    "marketSession",
    "marketSessionLabel",
    "realTime",
)


# Collection adapters use provider/domain class names while ABox persistence
# is routed by stable factual families. Keep that translation in one domain
# contract so a new transport name cannot silently reopen every ABox scope.
FACT_CHANGE_CONTRACT_VERSION = "fact-change-contract-v6-event-class-routing"

FACT_TYPE_SCOPE_FAMILIES = {
    "marketquote": {"market"},
    "pricemetric": {"market"},
    "technicalindicator": {"temporal"},
    "tradeflow": {"flow"},
    "executionflow": {"flow"},
    "investorflow": {"flow"},
    "orderbook": {"flow"},
    "researchevidence": {"evidence"},
    "newsarticle": {"evidence"},
    "newsevent": {"evidence"},
    "newsarticleanalysis": {"evidence"},
    "evidencelifecycle": {"evidence"},
    "disclosure": {"evidence"},
    "disclosureevent": {"evidence"},
    "verifiedclaim": {"evidence"},
    "verificationrun": {"evidence"},
    "earningscalendarevent": {"temporal", "evidence"},
    "investmentcalendarevent": {"temporal", "evidence"},
    "account": {"portfolio"},
    "portfolio": {"portfolio"},
    "position": {"position", "portfolio"},
    "portfoliosnapshot": {"position", "portfolio"},
    "portfolioactivityepisode": {"position", "portfolio", "episode"},
    "portfoliostatesnapshot": {"position", "portfolio"},
    "decisionactionobservation": {"position", "portfolio", "episode"},
    "portfoliorisksnapshot": {"portfolio", "exposure"},
    "positionriskmetric": {"position", "exposure"},
    "exposuresnapshot": {"portfolio", "exposure"},
    "rebalanceproposal": {"portfolio", "exposure"},
    "rebalancescenario": {"portfolio", "exposure"},
    "rebalancestate": {"portfolio", "exposure"},
    "dataquality": {"quality"},
    "fxrate": {"macro-fx"},
    "interestrate": {"macro-rates"},
    "macroindicator": {"macro-market"},
    "marketproxy": {"macro-market"},
    "marketproxyinstrument": {"macro-market"},
    "cryptomarket": {"macro-crypto"},
    "financialfact": {"fundamental"},
    "financialstatement": {"fundamental"},
    "companyprofile": {"profile"},
    "governancechange": {"governance"},
    "capitalstructurechange": {"capital"},
    "valuationobservation": {"company-valuation"},
}

# Exact RuleBox-readable ABox kinds carried by event-style source facts.  A
# scope family answers "which part of the world changed"; these keys answer
# "which object inside that part changed".  The values are structural
# identities only and never contain an investment threshold or conclusion.
# Fact types that are not listed retain the existing family-level fallback.
FACT_TYPE_DEPENDENCY_KEYS = {
    "researchevidence": {"kind:research-evidence"},
    "newsarticle": {"kind:research-evidence"},
    "newsevent": {"kind:research-evidence", "kind:news-event-type"},
    "newsarticleanalysis": {
        "kind:article-ai-analysis",
        "kind:article-analysis-conflict",
    },
    "evidencelifecycle": {"kind:research-evidence", "kind:verified-claim"},
    "disclosure": {"kind:disclosure-filing"},
    "disclosureevent": {"kind:disclosure-filing"},
    "earningscalendarevent": {"kind:earnings-calendar-event"},
    "investmentcalendarevent": {"kind:investment-calendar-event"},
    "verifiedclaim": {"kind:verified-claim"},
    "verificationrun": {"kind:verification-run"},
}

# These source facts are projected onto stock properties. Their exact RuleBox
# dependency is determined by the changed-field vector carried by the same
# durable event, instead of reopening every rule in the broad family.
FIELD_ROUTED_FACT_TYPES = {
    "marketquote",
    "pricemetric",
    "technicalindicator",
    "tradeflow",
    "executionflow",
    "investorflow",
    "orderbook",
}

FIELD_DEPENDENCY_ALIASES = {
    "changerate": {"pricechangerate"},
    "currentprice": {"currentprice", "ma5distance", "ma20distance", "ma60distance"},
    "foreignbuyvolume": {"foreignbuyvolume", "foreignnetvolume", "smartmoneynetvolume"},
    "foreignsellvolume": {"foreignsellvolume", "foreignnetvolume", "smartmoneynetvolume"},
    "foreignnetvolume": {"foreignnetvolume", "smartmoneynetvolume"},
    "institutionbuyvolume": {"institutionbuyvolume", "institutionnetvolume", "smartmoneynetvolume"},
    "institutionsellvolume": {"institutionsellvolume", "institutionnetvolume", "smartmoneynetvolume"},
    "institutionnetvolume": {"institutionnetvolume", "smartmoneynetvolume"},
    "volume": {"volume", "volumeratio"},
}

QUALITY_STATE_FIELDS = {
    "dataquality",
    "freshnessstatus",
    "latencystatus",
    "quotestatus",
    "sourcetimestampstate",
}

KNOWN_SCOPE_FAMILIES = {
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
    "link",
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


def normalized_fact_type(value: object) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


def scope_families_for_fact_types(fact_types: Iterable[object]) -> List[str]:
    """Translate source fact names to stable ABox families."""
    families: Set[str] = set()
    for fact_type in fact_types or []:
        text = str(fact_type or "").strip().lower()
        if text in KNOWN_SCOPE_FAMILIES:
            families.add(text)
        families.update(FACT_TYPE_SCOPE_FAMILIES.get(normalized_fact_type(fact_type), set()))
    return sorted(families)


def unclassified_fact_types(fact_types: Iterable[object]) -> List[str]:
    """Return transport fact names that have no declared ABox dependency."""
    return sorted({
        str(fact_type or "").strip()
        for fact_type in fact_types or []
        if str(fact_type or "").strip()
        and str(fact_type or "").strip().lower() not in KNOWN_SCOPE_FAMILIES
        and normalized_fact_type(fact_type) not in FACT_TYPE_SCOPE_FAMILIES
    })


def dependency_keys_for_fact_types(fact_types: Iterable[object]) -> List[str]:
    """Return exact ABox dependency identities declared by source facts."""
    keys: Set[str] = set()
    for fact_type in fact_types or []:
        keys.update(
            FACT_TYPE_DEPENDENCY_KEYS.get(normalized_fact_type(fact_type), set())
        )
    return sorted(keys)


def dependency_keys_complete_for_fact_types(fact_types: Iterable[object]) -> bool:
    """Whether every supplied source fact has an exact dependency contract."""
    values = [
        normalized_fact_type(fact_type)
        for fact_type in fact_types or []
        if str(fact_type or "").strip()
    ]
    return bool(values) and all(
        value in FACT_TYPE_DEPENDENCY_KEYS
        for value in values
    )


def _field_token(value: object) -> str:
    text = str(value or "").strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return "".join(character for character in text.lower() if character.isalnum())


def dependency_keys_for_changed_fields(fields: Iterable[object]) -> List[str]:
    keys: Set[str] = set()
    for field in fields or []:
        token = _field_token(field)
        if not token or token in {"marketobservationfollowup", "cryptomarkettransition"}:
            continue
        aliases = FIELD_DEPENDENCY_ALIASES.get(token, {token})
        keys.update("kind:stock:field:" + alias for alias in aliases if alias)
        if token in QUALITY_STATE_FIELDS:
            keys.update({
                "kind:data-availability-assessment",
                "kind:data-availability-assessment:field:datastate",
                "kind:data-availability-assessment:field:field",
                "relation:has-data-quality",
            })
    return sorted(keys)


def _symbol_dependency_contract(
    fact_types: Iterable[object],
    changed_fields: Iterable[object],
) -> Dict[str, object]:
    normalized_types = [
        normalized_fact_type(value)
        for value in fact_types or []
        if str(value or "").strip()
    ]
    static_keys = set(dependency_keys_for_fact_types(fact_types))
    field_routed = any(value in FIELD_ROUTED_FACT_TYPES for value in normalized_types)
    field_keys = set(dependency_keys_for_changed_fields(changed_fields)) if field_routed else set()
    complete = bool(normalized_types) and all(
        value in FACT_TYPE_DEPENDENCY_KEYS or value in FIELD_ROUTED_FACT_TYPES
        for value in normalized_types
    ) and (not field_routed or bool(field_keys))
    return {
        "dependencyKeys": sorted(static_keys | field_keys),
        "complete": complete,
    }


def fact_change_contract(
    fact_types: Iterable[object],
    fact_types_by_symbol: Mapping[str, Iterable[object]] = None,
    changed_fields_by_symbol: Mapping[str, Iterable[object]] = None,
) -> Dict[str, object]:
    """Build the auditable routing contract carried by a reasoning event."""
    clean_types = sorted({str(value or "").strip() for value in fact_types or [] if str(value or "").strip()})
    by_symbol = {}
    event_classes_by_symbol = {}
    dependency_keys_by_symbol = {}
    dependency_keys_complete_by_symbol = {}
    unclassified_by_symbol = {}
    raw_changed_fields = {
        str(symbol or "").upper().strip(): list(values or [])
        for symbol, values in dict(changed_fields_by_symbol or {}).items()
        if str(symbol or "").strip()
    }
    for raw_symbol, values in dict(fact_types_by_symbol or {}).items():
        symbol = str(raw_symbol or "").upper().strip()
        if not symbol:
            continue
        symbol_types = list(values or [])
        families = scope_families_for_fact_types(symbol_types)
        unknown = unclassified_fact_types(symbol_types)
        if families:
            by_symbol[symbol] = families
        symbol_event_classes = event_change_classes(
            families,
            raw_changed_fields.get(symbol, []),
        )
        if symbol_event_classes:
            event_classes_by_symbol[symbol] = symbol_event_classes
        symbol_contract = _symbol_dependency_contract(
            symbol_types,
            raw_changed_fields.get(symbol, []),
        )
        symbol_dependency_keys = symbol_contract["dependencyKeys"]
        if symbol_dependency_keys:
            dependency_keys_by_symbol[symbol] = symbol_dependency_keys
        dependency_keys_complete_by_symbol[symbol] = bool(symbol_contract["complete"])
        if unknown:
            unclassified_by_symbol[symbol] = unknown
    unknown = unclassified_fact_types(clean_types)
    global_dependency_keys = set(dependency_keys_for_fact_types(clean_types))
    for values in dependency_keys_by_symbol.values():
        global_dependency_keys.update(values)
    if dependency_keys_complete_by_symbol:
        dependency_keys_complete = all(dependency_keys_complete_by_symbol.values())
    else:
        dependency_keys_complete = dependency_keys_complete_for_fact_types(clean_types)
    return {
        "version": FACT_CHANGE_CONTRACT_VERSION,
        "status": "blocked-unclassified" if unknown or unclassified_by_symbol else "ready",
        "factTypes": clean_types,
        "scopeFamilies": scope_families_for_fact_types(clean_types),
        "scopeFamiliesBySymbol": by_symbol,
        "eventChangeClassVersion": EVENT_CHANGE_CLASS_VERSION,
        "eventClasses": event_change_classes(
            scope_families_for_fact_types(clean_types),
            [field for values in raw_changed_fields.values() for field in values],
        ),
        "eventClassesBySymbol": event_classes_by_symbol,
        "dependencyKeys": sorted(global_dependency_keys),
        "dependencyKeysBySymbol": dependency_keys_by_symbol,
        "dependencyKeysComplete": dependency_keys_complete,
        "dependencyKeysCompleteBySymbol": dependency_keys_complete_by_symbol,
        "unclassifiedFactTypes": unknown,
        "unclassifiedFactTypesBySymbol": unclassified_by_symbol,
    }


def canonical_fact_payload(payload: Dict[str, object], ignore_keys: Iterable[str] = None) -> Dict[str, object]:
    ignored = set(VOLATILE_FACT_KEYS)
    ignored.update(str(key) for key in (ignore_keys or []))
    clean: Dict[str, object] = {}
    for key, value in sorted((payload or {}).items()):
        if str(key) in ignored:
            continue
        if isinstance(value, dict):
            clean[key] = canonical_fact_payload(value, ignored)
        elif isinstance(value, list):
            clean[key] = [
                canonical_fact_payload(item, ignored) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            clean[key] = value
    return clean


def fact_signature(payload: Dict[str, object], ignore_keys: Iterable[str] = None) -> str:
    return json.dumps(canonical_fact_payload(payload, ignore_keys), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fact_revision_id(
    fact_type: str,
    subject: str,
    payload: Dict[str, object],
    ignore_keys: Iterable[str] = None,
) -> str:
    """Return a durable identity for one normalized fact revision.

    A collection timestamp is intentionally excluded.  The ID represents the
    actual fact content for one subject, so scheduler code can suppress a
    duplicate observation without deciding anything about the investment.
    """
    identity = "|".join([
        str(fact_type or "Fact").strip(),
        str(subject or "").upper().strip(),
        fact_signature(payload or {}, ignore_keys),
    ])
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return "fact-revision:" + digest


def _numeric_value(value: object) -> Optional[float]:
    """Return a finite numeric value without coercing semantic strings to zero."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError:
            return None
    else:
        return None
    return parsed if math.isfinite(parsed) else None


def _values_equal(previous: object, current: object, numeric_tolerance: float = 0.0001) -> bool:
    previous_number = _numeric_value(previous)
    current_number = _numeric_value(current)
    if previous_number is not None or current_number is not None:
        if previous_number is None or current_number is None:
            return False
        return abs(previous_number - current_number) <= numeric_tolerance
    return str(previous or "").strip() == str(current or "").strip()


def changed_fields(previous: Dict[str, object], current: Dict[str, object], fields: Iterable[str]) -> List[str]:
    if not previous:
        return [str(field) for field in fields if current.get(field) not in (None, "")]
    changed = []
    for field in fields:
        key = str(field)
        if key not in current and key not in previous:
            continue
        if not _values_equal(previous.get(key), current.get(key)):
            changed.append(key)
    return changed


def market_fact_change(previous: Dict[str, object], current: Dict[str, object]) -> Dict[str, object]:
    fields = changed_fields(previous or {}, current or {}, MARKET_FACT_FIELDS)
    payload = {key: (current or {}).get(key) for key in MARKET_FACT_FIELDS}
    if not previous:
        reason = "new-market-fact"
    elif fields:
        reason = "market-fact-fields-changed"
    else:
        reason = "market-fact-refresh-only"
    return {
        "changed": bool(fields),
        "reason": reason,
        "fields": fields,
        "signature": fact_signature(payload),
        "revisionId": fact_revision_id("MarketQuote", str((current or {}).get("symbol") or ""), payload),
    }


def research_evidence_fact_payload(payload: Dict[str, object]) -> Dict[str, object]:
    return canonical_fact_payload(payload or {})
