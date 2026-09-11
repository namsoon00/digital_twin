"""Explicit, lazy contracts surface for the model_registry module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'MODEL_REVIEW_PROMPT_VERSION': ('digital_twin.modules.model_registry.domain.model_review',
                                 'MODEL_REVIEW_PROMPT_VERSION'),
 'ModelReviewJob': ('digital_twin.modules.model_registry.domain.model_review', 'ModelReviewJob')}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
