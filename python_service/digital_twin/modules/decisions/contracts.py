"""Explicit, lazy contracts surface for the decisions module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'compact_decision_continuity_packet': ('digital_twin.modules.decisions.domain.decision_continuity',
                                        'compact_decision_continuity_packet')}

_EXPORTS['DecisionEpisodeRepository'] = ('digital_twin.modules.decisions.domain.repositories', 'DecisionEpisodeRepository')

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
