from dataclasses import replace
from typing import Dict

from .portfolio import Position


def position_with_symbol_identity(position: Position, identity: Dict[str, object] = None) -> Position:
    """Fill provider placeholder identity fields from the local symbol universe."""
    if not position or not position.symbol or not isinstance(identity, dict):
        return position
    symbol = str(position.symbol or "").strip().upper()
    name = str(position.name or "").strip()
    resolved_name = str(identity.get("name") or "").strip()
    use_resolved_name = bool(resolved_name and (not name or name.upper() == symbol))
    market = str(position.market or "").strip() or str(identity.get("market") or "").strip()
    currency = str(position.currency or "").strip() or str(identity.get("currency") or "").strip()
    sector = str(position.sector or "").strip()
    if not sector or sector == "기타":
        sector = str(identity.get("sector") or sector).strip() or "기타"
    if not use_resolved_name and market == position.market and currency == position.currency and sector == position.sector:
        return position
    return replace(
        position,
        name=resolved_name if use_resolved_name else position.name,
        market=market,
        currency=currency,
        sector=sector,
    )
