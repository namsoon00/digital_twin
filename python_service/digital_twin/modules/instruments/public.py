"""Explicit, lazy public surface for the instruments module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'AccountWatchlistService': ('digital_twin.modules.instruments.application.account_watchlist_service',
                             'AccountWatchlistService'),
 'DEFAULT_SYMBOL_SEEDS': ('digital_twin.modules.instruments.application.symbol_universe_service',
                          'DEFAULT_SYMBOL_SEEDS'),
 'SUPPORTED_MARKETS': ('digital_twin.modules.instruments.application.symbol_universe_service',
                       'SUPPORTED_MARKETS'),
 'SymbolUniverseService': ('digital_twin.modules.instruments.application.symbol_universe_service',
                           'SymbolUniverseService'),
 'seed_symbol': ('digital_twin.modules.instruments.application.symbol_universe_service', 'seed_symbol')}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
