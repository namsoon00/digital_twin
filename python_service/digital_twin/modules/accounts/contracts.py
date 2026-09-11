"""Explicit, lazy contracts surface for the accounts module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'AccountDomainProfile': ('digital_twin.modules.accounts.domain.account_identity', 'AccountDomainProfile')}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
