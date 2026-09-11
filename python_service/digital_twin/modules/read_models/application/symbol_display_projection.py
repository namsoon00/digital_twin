import re
from typing import Dict, Iterable, List


def _symbol_payload(value) -> Dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict"):
        return dict(value.to_dict() or {})
    return {
        "symbol": str(getattr(value, "symbol", "") or ""),
        "name": str(getattr(value, "name", "") or ""),
        "market": str(getattr(value, "market", "") or ""),
        "exchange": str(getattr(value, "exchange", "") or ""),
        "currency": str(getattr(value, "currency", "") or ""),
        "sector": str(getattr(value, "sector", "") or ""),
    }


def _display_title(title: object, details: Iterable[Dict[str, object]]) -> str:
    text = str(title or "")
    for detail in details or []:
        symbol = str(detail.get("symbol") or "").strip().upper()
        name = str(detail.get("name") or "").strip()
        if not symbol or not name or name.upper() == symbol:
            continue
        text = re.sub(
            r"(?<![A-Za-z0-9])" + re.escape(symbol) + r"(?![A-Za-z0-9])",
            name,
            text,
        )
    return text


def enrich_symbol_display_records(records: Iterable[Dict[str, object]], symbol_repository=None) -> List[Dict[str, object]]:
    rows = [dict(record or {}) for record in records or []]
    if not rows or symbol_repository is None or not hasattr(symbol_repository, "get"):
        return rows
    symbols = []
    for row in rows:
        for symbol in row.get("symbols") or []:
            normalized = str(symbol or "").strip().upper()
            if normalized and normalized not in symbols:
                symbols.append(normalized)
    resolved = {}
    for symbol in symbols:
        try:
            payload = _symbol_payload(symbol_repository.get(symbol))
        except Exception:  # noqa: BLE001 - display metadata must not block calendar reads.
            payload = {}
        name = str(payload.get("name") or "").strip()
        if not name:
            continue
        resolved[symbol] = {
            "symbol": symbol,
            "name": name,
            "market": str(payload.get("market") or ""),
            "exchange": str(payload.get("exchange") or ""),
            "currency": str(payload.get("currency") or ""),
            "sector": str(payload.get("sector") or ""),
        }
    enriched = []
    for row in rows:
        details = [
            resolved[symbol]
            for symbol in [str(value or "").strip().upper() for value in row.get("symbols") or []]
            if symbol in resolved
        ]
        if details:
            row["symbolDetails"] = details
            row["displayName"] = details[0]["name"]
            row["displayTitle"] = _display_title(row.get("title"), details)
        enriched.append(row)
    return enriched
