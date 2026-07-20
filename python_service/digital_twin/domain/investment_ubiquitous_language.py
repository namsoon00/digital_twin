"""Canonical user-facing investment language backed by ontology identifiers."""

import re
from typing import Iterable, List

from .ontology_tbox import CLASS_DEFS, tbox_class_def


POSITION_INTENT_LABELS = {
    "core": "핵심 보유",
    "growth": "성장 투자",
    "trading": "기회 대응",
    "income": "배당·현금흐름",
    "market-signal": "시장 흐름 확인",
}

POSITION_INTENT_SENTENCES = {
    "core": "계좌에서는 오래 가져갈 핵심 보유 종목으로 관리합니다.",
    "growth": "계좌에서는 성장 가능성을 보고 투자하는 종목으로 관리합니다.",
    "trading": "계좌에서는 가격 변화에 맞춰 비중을 조절하는 종목으로 관리합니다.",
    "income": "계좌에서는 배당과 현금흐름을 기대하는 종목으로 관리합니다.",
    "market-signal": "계좌에서는 직접 매매보다 시장 흐름을 확인하는 지표로 사용합니다.",
}


def _investment_archetype_definitions():
    definitions = {item.name: item for item in CLASS_DEFS}
    rows = []
    for item in CLASS_DEFS:
        name = item.name
        while name and name in definitions:
            if name == "InvestmentArchetype":
                rows.append(item)
                break
            name = definitions[name].parent
    for extra in ("MarketProxyInstrument",):
        definition = definitions.get(extra)
        if definition and definition not in rows:
            rows.append(definition)
    return rows


ARCHETYPE_DEFINITIONS = _investment_archetype_definitions()


def _contains_hangul(value: str) -> bool:
    return any("가" <= char <= "힣" for char in str(value or ""))


def investment_archetype_label(identifier: object) -> str:
    """Return the TBox domain label without exposing an internal class identifier."""
    key = str(identifier or "").strip()
    if not key:
        return ""
    definition = tbox_class_def(key)
    if definition and str(definition.label or "").strip():
        return str(definition.label).strip()
    if _contains_hangul(key):
        return key
    return "사용자 정의 종목 성격"


def investment_archetype_labels(identifiers: Iterable[object]) -> List[str]:
    rows: List[str] = []
    for identifier in identifiers or []:
        label = investment_archetype_label(identifier)
        if label and label not in rows:
            rows.append(label)
    return rows


def position_intent_label(identifier: object) -> str:
    key = str(identifier or "").strip()
    if not key:
        return ""
    if key in POSITION_INTENT_LABELS:
        return POSITION_INTENT_LABELS[key]
    if _contains_hangul(key):
        return key
    return "사용자 정의 역할"


def position_intent_sentence(identifier: object) -> str:
    key = str(identifier or "").strip()
    if not key:
        return ""
    if key in POSITION_INTENT_SENTENCES:
        return POSITION_INTENT_SENTENCES[key]
    return "계좌에서는 " + position_intent_label(key) + " 종목으로 관리합니다."


def user_facing_investment_language(value: object) -> str:
    """Remove ontology implementation IDs from text before it reaches a user."""
    text = str(value or "")
    for definition in sorted(ARCHETYPE_DEFINITIONS, key=lambda item: len(item.name), reverse=True):
        if definition.name and definition.label:
            text = text.replace(definition.name, definition.label)
    text = text.replace("종목 타입", "종목 성격").replace("계좌 안 역할", "계좌에서의 역할")
    for identifier, label in POSITION_INTENT_LABELS.items():
        patterns = [
            r"(계좌에서의 역할\s*[:=]\s*)" + re.escape(identifier) + r"\b",
            r"(positionIntent\s*[:=]\s*)" + re.escape(identifier) + r"\b",
        ]
        for pattern in patterns:
            text = re.sub(pattern, lambda match: match.group(1) + label, text, flags=re.IGNORECASE)
    return text
