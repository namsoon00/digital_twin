"""Explicit, lazy public surface for the accounts module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'AccountApplicationService': ('digital_twin.modules.accounts.application.account_service',
                               'AccountApplicationService')}

_EXPORTS["AccountReader"] = ("digital_twin.modules.accounts.application.ports", "AccountReader")

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
