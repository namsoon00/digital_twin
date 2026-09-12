"""Explicit, lazy public surface for the reasoning module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'IndependentReasoningComparisonService': ('digital_twin.modules.reasoning.application.independent_reasoning_comparison_service',
                                           'IndependentReasoningComparisonService'),
 'IndependentReasoningInputAssembler': ('digital_twin.modules.reasoning.application.independent_reasoning_engine',
                                        'IndependentReasoningInputAssembler'),
 'IndependentReasoningJobRunner': ('digital_twin.modules.reasoning.application.independent_reasoning_engine',
                                   'IndependentReasoningJobRunner'),
 'InvestmentReasoningOrchestrator': ('digital_twin.modules.reasoning.application.investment_reasoning',
                                     'InvestmentReasoningOrchestrator'),
 'OntologyInferenceDetailRunner': ('digital_twin.modules.reasoning.application.ontology_inference_detail_service',
                                   'OntologyInferenceDetailRunner'),
 'OntologyMaintenanceRunner': ('digital_twin.modules.reasoning.application.ontology_maintenance_service',
                               'OntologyMaintenanceRunner'),
 'OntologyPortfolioRebuildRunner': ('digital_twin.modules.reasoning.application.ontology_portfolio_rebuild_service',
                                    'OntologyPortfolioRebuildRunner'),
 'OntologyPortfolioScopeRepairRunner': ('digital_twin.modules.reasoning.application.ontology_portfolio_rebuild_service',
                                        'OntologyPortfolioScopeRepairRunner'),
 'OntologyReasoningProofService': ('digital_twin.modules.reasoning.application.ontology_reasoning_proof_service',
                                   'OntologyReasoningProofService'),
 'OntologyReasoningQueueHealthNotificationEnqueuer': ('digital_twin.modules.reasoning.application.ontology_reasoning_queue_health_service',
                                                      'OntologyReasoningQueueHealthNotificationEnqueuer'),
 'OntologyReasoningQueueHealthService': ('digital_twin.modules.reasoning.application.ontology_reasoning_queue_health_service',
                                         'OntologyReasoningQueueHealthService'),
 'OntologyReasoningRunner': ('digital_twin.modules.reasoning.application.ontology_reasoning_service',
                             'OntologyReasoningRunner'),
 'OntologyScopeRepairRouter': ('digital_twin.modules.reasoning.application.ontology_portfolio_rebuild_service',
                               'OntologyScopeRepairRouter'),
 'OntologyWorldProjectionRunner': ('digital_twin.modules.reasoning.application.ontology_world_projection_service',
                                   'OntologyWorldProjectionRunner'),
 'ReasoningEnginePlatformService': ('digital_twin.modules.reasoning.application.reasoning_engine_platform',
                                    'ReasoningEnginePlatformService'),
 'ReasoningEngineShadowRunner': ('digital_twin.modules.reasoning.application.reasoning_shadow_service',
                                 'ReasoningEngineShadowRunner'),
 'ReasoningShadowScheduler': ('digital_twin.modules.reasoning.application.reasoning_shadow_service',
                              'ReasoningShadowScheduler'),
 'ScopedTypeDBInferenceExecutor': ('digital_twin.modules.reasoning.application.independent_reasoning_engine',
                                   'ScopedTypeDBInferenceExecutor'),
 'SharedInstrumentInferenceService': ('digital_twin.modules.reasoning.application.shared_instrument_inference_service',
                                      'SharedInstrumentInferenceService'),
 'StatisticalSignalPipelineService': ('digital_twin.modules.reasoning.application.statistical_signals',
                                      'StatisticalSignalPipelineService'),
 'V2GraphDecisionCandidateBuilder': ('digital_twin.modules.reasoning.application.investment_reasoning',
                                     'V2GraphDecisionCandidateBuilder'),
 'V2ReasoningEngine': ('digital_twin.modules.reasoning.application.independent_reasoning_engine',
                       'V2ReasoningEngine'),
 'lightweight_ontology_reasoning_queue_state': ('digital_twin.modules.reasoning.application.ontology_reasoning_service',
                                                'lightweight_ontology_reasoning_queue_state')}

_EXPORTS['append_rule_to_release_artifact'] = ('digital_twin.modules.reasoning.infrastructure.static_seed.artifact', 'append_rule_to_release_artifact')

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
