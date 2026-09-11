"""Shared world implementation; facade-independent dependencies."""

from __future__ import annotations
from .shared_world_ports import SharedWorldPort


def shared_market_world_retention_hours(_store: SharedWorldPort) -> float:
    try:
        value = float(
            str(_store.settings.get("ontologySharedMarketWorldRetentionHours") or "72")
        )
    except (TypeError, ValueError):
        value = 72.0
    return max(1.0, min(24.0 * 90.0, value))


def shared_knowledge_world_retention_hours(_store: SharedWorldPort) -> float:
    """Keep durable real-world topology longer than quote observations."""
    try:
        value = float(
            str(
                _store.settings.get("ontologySharedKnowledgeWorldRetentionHours")
                or str(24.0 * 365.0)
            )
        )
    except (TypeError, ValueError):
        value = 24.0 * 365.0
    return max(24.0, min(24.0 * 3650.0, value))


def shared_world_retention_hours(
    _store: SharedWorldPort, projection_kind: str
) -> float:
    if str(projection_kind or "").strip().lower() == "knowledge":
        return _store.shared_knowledge_world_retention_hours()
    return _store.shared_market_world_retention_hours()


def shared_market_world_symbol_limit(_store: SharedWorldPort) -> int:
    try:
        value = int(
            float(
                str(
                    _store.settings.get("ontologySharedMarketWorldMaxSymbols") or "1200"
                )
            )
        )
    except (TypeError, ValueError):
        value = 1200
    return max(50, min(20000, value))


def shared_market_world_async_projection_enabled(_store: SharedWorldPort) -> bool:
    value = _store.settings.get("ontologySharedMarketWorldAsyncProjectionEnabled")
    if value is None:
        # Keep direct recorder/unit-test construction deterministic.  The
        # production runtime explicitly enables this setting.
        return False
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}
