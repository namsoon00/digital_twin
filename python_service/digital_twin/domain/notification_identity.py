"""Canonical instrument identity for notification read models."""

from typing import Dict

from .position_identity import is_symbol_placeholder_name, preferred_instrument_name


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _symbol_like(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text or len(text) > 16 or any(not (char.isalnum() or char in {".", "-"}) for char in text):
        return ""
    return text


def notification_instrument_symbol(context: Dict[str, object]) -> str:
    values = _mapping(context)
    metadata = _mapping(values.get("metadata"))
    relations = [
        _mapping(values.get("ontologyRelationContext")),
        _mapping(metadata.get("ontologyRelationContext")),
    ]
    candidates = [values.get("rawSymbol")]
    candidates.extend(_mapping(relation.get("subject")).get("symbol") for relation in relations)
    candidates.extend([
        _mapping(values.get("ontologyInsight")).get("subject"),
        _mapping(metadata.get("ontologyInsight")).get("subject"),
        values.get("symbol"),
        values.get("rawTarget"),
    ])
    for candidate in candidates:
        symbol = _symbol_like(candidate)
        if symbol:
            return symbol
    return ""


def _replace_placeholder_headline(value: object, symbol: str, name: str) -> str:
    text = str(value or "").strip()
    if not text or name in text or symbol not in text:
        return text
    return text.replace(symbol, name, 1)


def _enrich_relation(value: object, symbol: str, name: str) -> Dict[str, object]:
    relation = _mapping(value)
    if not relation:
        return relation
    subject = _mapping(relation.get("subject"))
    if _symbol_like(subject.get("symbol")) == symbol:
        subject["name"] = preferred_instrument_name(symbol, subject.get("name"), name)
        relation["subject"] = subject
    facts = _mapping(relation.get("facts"))
    if _symbol_like(facts.get("symbol")) == symbol and is_symbol_placeholder_name(facts.get("name"), symbol):
        facts["name"] = name
        relation["facts"] = facts
    return relation


def _enrich_insight(value: object, symbol: str, name: str) -> Dict[str, object]:
    insight = _mapping(value)
    if not insight or _symbol_like(insight.get("subject")) != symbol:
        return insight
    insight["subjectName"] = preferred_instrument_name(symbol, insight.get("subjectName"), name)
    insight["thesis"] = _replace_placeholder_headline(insight.get("thesis"), symbol, name)
    return insight


def context_with_instrument_identity(
    context: Dict[str, object],
    identity: Dict[str, object],
) -> Dict[str, object]:
    values = _mapping(context)
    symbol = notification_instrument_symbol(values)
    resolved_name = str(_mapping(identity).get("name") or "").strip()
    if not symbol or is_symbol_placeholder_name(resolved_name, symbol):
        return values

    relation = _mapping(values.get("ontologyRelationContext"))
    subject = _mapping(relation.get("subject"))
    current_name = next(
        (
            str(value).strip()
            for value in (
                subject.get("name"),
                values.get("displaySymbolName"),
                values.get("symbolDisplayName"),
            )
            if str(value or "").strip() and not is_symbol_placeholder_name(value, symbol)
        ),
        "",
    )
    name = preferred_instrument_name(symbol, current_name, resolved_name)
    symbol_with_code = name + " / " + symbol

    values["displaySymbolName"] = name
    values["symbolDisplayName"] = name
    values["symbolWithCode"] = symbol_with_code
    if is_symbol_placeholder_name(values.get("displayTarget"), symbol):
        values["displayTarget"] = symbol_with_code
    if is_symbol_placeholder_name(values.get("title"), symbol):
        values["title"] = name
    values["symbolLine"] = "종목: " + symbol_with_code
    values["targetLine"] = "대상: " + symbol_with_code
    values["headline"] = _replace_placeholder_headline(values.get("headline"), symbol, name)
    values["instrumentIdentity"] = {
        "symbol": symbol,
        "name": name,
        "market": str(_mapping(identity).get("market") or ""),
        "source": str(_mapping(identity).get("source") or "symbol-universe"),
    }

    if relation:
        values["ontologyRelationContext"] = _enrich_relation(relation, symbol, name)
    insight = _enrich_insight(values.get("ontologyInsight"), symbol, name)
    if insight:
        values["ontologyInsight"] = insight

    metadata = _mapping(values.get("metadata"))
    if metadata:
        metadata_relation = _enrich_relation(metadata.get("ontologyRelationContext"), symbol, name)
        if metadata_relation:
            metadata["ontologyRelationContext"] = metadata_relation
        metadata_insight = _enrich_insight(metadata.get("ontologyInsight"), symbol, name)
        if metadata_insight:
            metadata["ontologyInsight"] = metadata_insight
        values["metadata"] = metadata
    return values
