"""Explicit, lazy contracts surface for the outcomes module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'evaluate_hypothesis_outcome': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_evaluation',
                                 'evaluate_hypothesis_outcome'),
 'optional_number': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_evaluation',
                     'optional_number')}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
