"""Explicit, lazy contracts surface for the reasoning module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
