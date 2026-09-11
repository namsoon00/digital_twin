"""Explicit, lazy contracts surface for the instruments module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'ListedSymbol': ('digital_twin.modules.instruments.domain.symbol_universe', 'ListedSymbol'),
 'SUPPORTED_MARKETS': ('digital_twin.modules.instruments.domain.symbol_universe', 'SUPPORTED_MARKETS'),
 'normalize_market': ('digital_twin.modules.instruments.domain.symbol_universe', 'normalize_market'),
 'normalize_symbol': ('digital_twin.modules.instruments.domain.symbol_universe', 'normalize_symbol'),
 'symbol_search_symbol_candidates': ('digital_twin.modules.instruments.domain.symbol_universe',
                                     'symbol_search_symbol_candidates'),
 'utc_now_iso': ('digital_twin.modules.instruments.domain.symbol_universe', 'utc_now_iso'),
 'ACCOUNT_WATCHLIST_CHANGED': ('digital_twin.modules.instruments.domain.watchlist',
                               'ACCOUNT_WATCHLIST_CHANGED'),
 'watchlist_changed_event': ('digital_twin.modules.instruments.domain.watchlist',
                             'watchlist_changed_event')}

_EXPORTS['SymbolUniverseRepository'] = ('digital_twin.modules.instruments.domain.repositories', 'SymbolUniverseRepository')
_EXPORTS['SymbolSourceGateway'] = ('digital_twin.modules.instruments.domain.repositories', 'SymbolSourceGateway')

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
