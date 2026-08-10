from dataclasses import replace
from typing import Dict

from .portfolio import Position


def is_symbol_placeholder_name(value: object, symbol: object) -> bool:
    name = str(value or "").strip().upper()
    normalized_symbol = str(symbol or "").strip().upper()
    return not name or bool(normalized_symbol and name == normalized_symbol)


def preferred_instrument_name(symbol: object, current_name: object, candidate_name: object) -> str:
    """Keep a real name when a downstream provider supplies only the symbol."""
    current = str(current_name or "").strip()
    candidate = str(candidate_name or "").strip()
    if current and not is_symbol_placeholder_name(current, symbol):
        return current
    if candidate and not is_symbol_placeholder_name(candidate, symbol):
        return candidate
    return current or candidate or str(symbol or "").strip()


def position_with_symbol_identity(position: Position, identity: Dict[str, object] = None) -> Position:
    """Fill provider placeholder identity fields from the local symbol universe."""
    if not position or not position.symbol or not isinstance(identity, dict):
        return position
    symbol = str(position.symbol or "").strip().upper()
    name = str(position.name or "").strip()
    resolved_name = str(identity.get("name") or "").strip()
    preferred_name = preferred_instrument_name(symbol, name, resolved_name)
    use_resolved_name = bool(preferred_name and preferred_name != name)
    market = str(position.market or "").strip() or str(identity.get("market") or "").strip()
    currency = str(position.currency or "").strip() or str(identity.get("currency") or "").strip()
    sector = str(position.sector or "").strip()
    if not sector or sector == "기타":
        sector = str(identity.get("sector") or sector).strip() or "기타"
    if not use_resolved_name and market == position.market and currency == position.currency and sector == position.sector:
        return position
    return replace(
        position,
        name=preferred_name if use_resolved_name else position.name,
        market=market,
        currency=currency,
        sector=sector,
    )
