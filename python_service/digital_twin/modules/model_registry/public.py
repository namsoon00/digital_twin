"""Explicit, lazy public surface for the model_registry module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'HypothesisDevelopmentService': ('digital_twin.modules.model_registry.application.hypothesis_development_service',
                                  'HypothesisDevelopmentService'),
 'HypothesisLifecyclePolicyService': ('digital_twin.modules.model_registry.application.hypothesis_lifecycle_policy_service',
                                      'HypothesisLifecyclePolicyService'),
 'HypothesisPolicyGovernanceService': ('digital_twin.modules.model_registry.application.hypothesis_policy_governance_service',
                                       'HypothesisPolicyGovernanceService'),
 'HypothesisProposalQueueRunner': ('digital_twin.modules.model_registry.application.hypothesis_proposal_service',
                                   'HypothesisProposalQueueRunner'),
 'HypothesisProposalService': ('digital_twin.modules.model_registry.application.hypothesis_proposal_service',
                               'HypothesisProposalService'),
 'InvestmentStrategyProposalService': ('digital_twin.modules.model_registry.application.investment_strategy_proposal_service',
                                       'InvestmentStrategyProposalService'),
 'ModelReviewRunner': ('digital_twin.modules.model_registry.application.model_review_service',
                       'ModelReviewRunner'),
 'OntologyLabService': ('digital_twin.modules.model_registry.application.ontology_lab_service',
                        'OntologyLabService'),
 'RuleChangeCandidateProposalService': ('digital_twin.modules.model_registry.application.ontology_rule_candidate_service',
                                        'RuleChangeCandidateProposalService')}

_EXPORTS['OntologyEvolutionService'] = ('digital_twin.modules.model_registry.application.ontology_evolution_service', 'OntologyEvolutionService')

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
