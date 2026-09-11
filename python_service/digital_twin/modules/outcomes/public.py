"""Explicit, lazy public surface for the outcomes module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'HistoricalDecisionReplayService': ('digital_twin.modules.outcomes.application.historical_decision_replay_service',
                                     'HistoricalDecisionReplayService'),
 'HistoricalReplayJobService': ('digital_twin.modules.outcomes.application.historical_replay_job_service',
                                'HistoricalReplayJobService'),
 'HypothesisLifecycleService': ('digital_twin.modules.outcomes.application.hypothesis_lifecycle_service',
                                'HypothesisLifecycleService'),
 'HypothesisOutcomeReplayService': ('digital_twin.modules.outcomes.application.hypothesis_outcome_replay_service',
                                    'HypothesisOutcomeReplayService'),
 'HypothesisQualityReviewService': ('digital_twin.modules.outcomes.application.hypothesis_quality_review_service',
                                    'HypothesisQualityReviewService'),
 'HypothesisReviewService': ('digital_twin.modules.outcomes.application.hypothesis_review_service',
                             'HypothesisReviewService'),
 'InvestmentOutcomeObservationService': ('digital_twin.modules.outcomes.application.investment_outcome_observation_service',
                                         'InvestmentOutcomeObservationService')}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
