"""Explicit, lazy contracts surface for the investment_calendar module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'InvestmentCalendarEvent': ('digital_twin.modules.investment_calendar.domain.investment_calendar',
                             'InvestmentCalendarEvent')}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
