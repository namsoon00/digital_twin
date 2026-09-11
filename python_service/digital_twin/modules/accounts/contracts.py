"""Explicit, lazy contracts surface for the accounts module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'AccountDomainProfile': ('digital_twin.modules.accounts.domain.account_identity', 'AccountDomainProfile')}

_EXPORTS['configured'] = ('digital_twin.modules.accounts.domain.configuration', 'configured')
_EXPORTS['split_symbols'] = ('digital_twin.modules.accounts.domain.configuration', 'split_symbols')
_EXPORTS['AccountConfig'] = ('digital_twin.modules.accounts.domain.configuration', 'AccountConfig')

_EXPORTS["WatchlistAccount"] = ("digital_twin.modules.accounts.domain.watchlist_account", "WatchlistAccount")
_EXPORTS["WatchlistAccountReader"] = ("digital_twin.modules.accounts.domain.watchlist_account", "WatchlistAccountReader")

_EXPORTS["AccountReader"] = ("digital_twin.modules.accounts.domain.ports", "AccountReader")
_EXPORTS["AccountRepository"] = ("digital_twin.modules.accounts.domain.ports", "AccountRepository")

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
