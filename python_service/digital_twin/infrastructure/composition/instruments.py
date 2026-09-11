"""Instruments runtime composition, loaded only when requested."""

from __future__ import annotations

from typing import Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from digital_twin.modules.instruments.public import SymbolUniverseService


def build_account_watchlist_service(settings=None, event_publisher=None, refresh_requester=None):
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.event_bus import default_event_bus
    from digital_twin.modules.instruments.public import AccountWatchlistService

    publisher = event_publisher if event_publisher is not None else default_event_bus()
    return AccountWatchlistService(
        stores.account_watchlist_repository(settings),
        event_publisher=publisher,
        refresh_requester=refresh_requester,
    )


def build_symbol_universe_service(settings=None) -> SymbolUniverseService:
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings
    from digital_twin.modules.instruments.infrastructure.symbol_sources import RemoteSymbolSourceGateway
    from digital_twin.modules.instruments.public import SymbolUniverseService

    configured_settings = settings or runtime_settings()
    return SymbolUniverseService(
        store=stores.symbol_universe_store(configured_settings),
        source_gateway=RemoteSymbolSourceGateway(configured_settings),
        settings=configured_settings,
        quote_cache=stores.market_quote_cache(configured_settings),
    )


def ontology_reasoning_priority_symbols(account_repository, settings=None) -> Dict[str, list]:
    """Return the latest account focus set for worker scheduling only.

    Holdings are handled before watchlist names and both are handled before
    background universe ticks. The snapshot remains the source of truth for
    positions; this helper does not create investment facts or decisions.
    """
    from digital_twin.infrastructure import operational_store as stores
    from digital_twin.infrastructure.settings import runtime_settings

    configured_settings = settings or runtime_settings()
    roles = {"holdingSymbols": [], "watchlistSymbols": []}

    def add(role: str, value: object) -> None:
        symbol = str(value or "").upper().strip()
        if symbol and symbol not in roles[role]:
            roles[role].append(symbol)

    try:
        previous = stores.monitor_store(configured_settings).previous
    except Exception:  # noqa: BLE001 - a missing snapshot simply leaves account config priorities.
        previous = {}
    for state in (previous or {}).values():
        if not isinstance(state, dict):
            continue
        for container, role in (("positions", "holdingSymbols"), ("watchlist", "watchlistSymbols")):
            items = state.get(container)
            if isinstance(items, dict):
                for key, item in items.items():
                    add(role, item.get("symbol") if isinstance(item, dict) else key)
            elif isinstance(items, list):
                for item in items:
                    add(role, item.get("symbol") if isinstance(item, dict) else item)
    try:
        accounts = account_repository.load()
    except Exception:  # noqa: BLE001 - the live snapshot above is still sufficient when available.
        accounts = []
    for account in accounts:
        for symbol in getattr(account, "watchlist_symbols", []) or []:
            add("watchlistSymbols", symbol)
    return roles
