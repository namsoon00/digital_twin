"""Explicit, lazy contracts surface for the outcomes module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'evaluate_hypothesis_outcome': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_evaluation',
                                 'evaluate_hypothesis_outcome'),
 'optional_number': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_evaluation',
                     'optional_number')}


_EXPORTS.update({
    'frozen_outcome_facts': ('digital_twin.modules.outcomes.domain.outcome_recovery', 'frozen_outcome_facts'),
    'observation_facts': ('digital_twin.modules.outcomes.domain.outcome_recovery', 'observation_facts'),
    'outcome_evaluation_history': ('digital_twin.modules.outcomes.domain.outcome_recovery', 'outcome_evaluation_history'),
    'outcome_needs_data': ('digital_twin.modules.outcomes.domain.outcome_recovery', 'outcome_needs_data'),
    'validate_outcome_repair': ('digital_twin.modules.outcomes.domain.outcome_recovery', 'validate_outcome_repair'),
    'DecisionReview': ('digital_twin.modules.outcomes.domain.investment_outcomes', 'DecisionReview'),
    'HYPOTHESIS_OUTCOME_CONTRACT_VERSION': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'HYPOTHESIS_OUTCOME_CONTRACT_VERSION'),
    'HypothesisOutcomeContract': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'HypothesisOutcomeContract'),
    'HypothesisOutcomeCriterion': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'HypothesisOutcomeCriterion'),
    'INVESTMENT_DECISION_REVIEWED': ('digital_twin.modules.outcomes.domain.event_types', 'INVESTMENT_DECISION_REVIEWED'),
    'INVESTMENT_PERFORMANCE_ATTRIBUTED': ('digital_twin.modules.outcomes.domain.event_types', 'INVESTMENT_PERFORMANCE_ATTRIBUTED'),
    'PerformanceAttribution': ('digital_twin.modules.outcomes.domain.investment_outcomes', 'PerformanceAttribution'),
    'SUPPORTED_OBSERVATION_DOMAINS': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'SUPPORTED_OBSERVATION_DOMAINS'),
    'SUPPORTED_OUTCOME_CRITERION_FAILURE_OUTCOMES': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'SUPPORTED_OUTCOME_CRITERION_FAILURE_OUTCOMES'),
    'SUPPORTED_OUTCOME_CRITERION_METRICS': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'SUPPORTED_OUTCOME_CRITERION_METRICS'),
    'SUPPORTED_OUTCOME_CRITERION_OPERATORS': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'SUPPORTED_OUTCOME_CRITERION_OPERATORS'),
    'SUPPORTED_OUTCOME_CRITERION_ROLES': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'SUPPORTED_OUTCOME_CRITERION_ROLES'),
    'ShadowHypothesisObservationEpisode': ('digital_twin.modules.outcomes.domain.hypothesis_observation', 'ShadowHypothesisObservationEpisode'),
    'TRADE_EXECUTION_RECORDED': ('digital_twin.modules.outcomes.domain.event_types', 'TRADE_EXECUTION_RECORDED'),
    'action_adjusted_return': ('digital_twin.modules.outcomes.domain.decision_performance', 'action_adjusted_return'),
    'action_return_state': ('digital_twin.modules.outcomes.domain.decision_performance', 'action_return_state'),
    'apply_ai_research_guidance': ('digital_twin.modules.outcomes.domain.hypothesis_research_planning', 'apply_ai_research_guidance'),
    'attach_abox_hypothesis_calibrations': ('digital_twin.modules.outcomes.domain.hypothesis_calibration', 'attach_abox_hypothesis_calibrations'),
    'baseline_research_plan': ('digital_twin.modules.outcomes.domain.hypothesis_research_planning', 'baseline_research_plan'),
    'binomial_confidence_interval': ('digital_twin.modules.outcomes.domain.decision_performance', 'binomial_confidence_interval'),
    'claim_revision_identity': ('digital_twin.modules.outcomes.domain.hypothesis_calibration_identity', 'claim_revision_identity'),
    'claim_validation_fingerprint': ('digital_twin.modules.outcomes.domain.hypothesis_calibration_identity', 'claim_validation_fingerprint'),
    'default_directional_criteria': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'default_directional_criteria'),
    'evaluate_decision_performance': ('digital_twin.modules.outcomes.domain.decision_performance', 'evaluate_decision_performance'),
    'freeze_outcome_baseline': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_facts', 'freeze_outcome_baseline'),
    'hypothesis_calibration_snapshot_from_abox_rows': ('digital_twin.modules.outcomes.domain.hypothesis_calibration', 'hypothesis_calibration_snapshot_from_abox_rows'),
    'hypothesis_observation_bucket': ('digital_twin.modules.outcomes.domain.hypothesis_observation', 'hypothesis_observation_bucket'),
    'investment_follow_up_transitioned_event': ('digital_twin.modules.outcomes.domain.events', 'investment_follow_up_transitioned_event'),
    'list_values': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'list_values'),
    'merge_outcome_contracts': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'merge_outcome_contracts'),
    'normalize_follow_up_conditions': ('digital_twin.modules.outcomes.domain.decision_follow_up', 'normalize_follow_up_conditions'),
    'number': ('digital_twin.modules.outcomes.domain.decision_performance', 'number'),
    'outcome_assessments_from_episodes': ('digital_twin.modules.outcomes.domain.hypothesis_review', 'outcome_assessments_from_episodes'),
    'outcome_contract_completeness': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'outcome_contract_completeness'),
    'outcome_contract_fingerprint': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'outcome_contract_fingerprint'),
    'research_planner_input': ('digital_twin.modules.outcomes.domain.hypothesis_research_planning', 'research_planner_input'),
    'resolve_market_outcome_benchmarks': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'resolve_market_outcome_benchmarks'),
    'uncovered_outcome_horizons': ('digital_twin.modules.outcomes.domain.hypothesis_outcome_contract', 'uncovered_outcome_horizons'),
})

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
