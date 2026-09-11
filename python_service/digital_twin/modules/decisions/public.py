"""Explicit, lazy public surface for the decisions module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'AIInferenceQueueRunner': ('digital_twin.modules.decisions.application.ai_inference_queue_service',
                            'AIInferenceQueueRunner'),
 'DecisionContinuityService': ('digital_twin.modules.decisions.application.decision_continuity_service',
                               'DecisionContinuityService'),
 'DecisionEpisodeReconciliationService': ('digital_twin.modules.decisions.application.decision_episode_reconciliation_service',
                                          'DecisionEpisodeReconciliationService'),
 'InvestmentAIInsightHandoffService': ('digital_twin.modules.decisions.application.investment_ai_insight_service',
                                       'InvestmentAIInsightHandoffService'),
 'InvestmentAlertCoverageNotificationEnqueuer': ('digital_twin.modules.decisions.application.investment_alert_coverage_service',
                                                 'InvestmentAlertCoverageNotificationEnqueuer'),
 'InvestmentAlertCoverageService': ('digital_twin.modules.decisions.application.investment_alert_coverage_service',
                                    'InvestmentAlertCoverageService'),
 'InvestmentBrainService': ('digital_twin.modules.decisions.application.investment_brain_service',
                            'InvestmentBrainService'),
 'InvestmentInsightDispatchService': ('digital_twin.modules.decisions.application.investment_insight_dispatch_service',
                                      'InvestmentInsightDispatchService'),
 'NotificationAIContractError': ('digital_twin.modules.decisions.application.notification_ai_judgement_service',
                                 'NotificationAIContractError'),
 'NotificationAIDecisionContextEnricher': ('digital_twin.modules.decisions.application.notification_ai_decision_context',
                                           'NotificationAIDecisionContextEnricher'),
 'NotificationAIJudgementService': ('digital_twin.modules.decisions.application.notification_ai_judgement_service',
                                    'NotificationAIJudgementService'),
 'NotificationAIRequestEnqueuer': ('digital_twin.modules.decisions.application.ai_inference_queue_service',
                                   'NotificationAIRequestEnqueuer'),
 'context_with_previous_investment_decision': ('digital_twin.modules.decisions.application.notification_decision_memory',
                                               'context_with_previous_investment_decision'),
 'context_with_validated_ai_response': ('digital_twin.modules.decisions.application.notification_ai_gate_audit',
                                        'context_with_validated_ai_response'),
 'NotificationAIValidatedGateEnricher': ('digital_twin.modules.decisions.application.notification_ai_validated_gate',
                                         'NotificationAIValidatedGateEnricher')}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
