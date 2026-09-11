"""Explicit, lazy public surface for the news_intelligence module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'HypothesisResearchPlanningService': ('digital_twin.modules.news_intelligence.application.hypothesis_research_planner_service',
                                       'HypothesisResearchPlanningService'),
 'InvestmentResearchOrchestrationService': ('digital_twin.modules.news_intelligence.application.investment_research_orchestration_service',
                                            'InvestmentResearchOrchestrationService'),
 'InvestmentResearchQueueRunner': ('digital_twin.modules.news_intelligence.application.investment_research_orchestration_service',
                                   'InvestmentResearchQueueRunner'),
 'NEWS_INTELLIGENCE_VERSION': ('digital_twin.modules.news_intelligence.domain.version',
                               'NEWS_INTELLIGENCE_VERSION'),
 'NewsAiAnalysisService': ('digital_twin.modules.news_intelligence.application.news_ai_analysis_service',
                           'NewsAiAnalysisService'),
 'NewsAnalysisEnrichmentRunner': ('digital_twin.modules.news_intelligence.application.news_analysis_enrichment_service',
                                  'NewsAnalysisEnrichmentRunner'),
 'NewsCollectionRunner': ('digital_twin.modules.news_intelligence.application.news_collection_service',
                          'NewsCollectionRunner'),
 'NewsDigestEnqueuer': ('digital_twin.modules.news_intelligence.application.news_digest_service',
                        'NewsDigestEnqueuer'),
 'NewsDigestEventReconciler': ('digital_twin.modules.news_intelligence.application.news_digest_service',
                               'NewsDigestEventReconciler'),
 'NewsPipelineRepairService': ('digital_twin.modules.news_intelligence.application.news_pipeline_repair_service',
                               'NewsPipelineRepairService'),
 'ResearchEvidenceGovernanceService': ('digital_twin.modules.news_intelligence.application.research_evidence_governance_service',
                                       'ResearchEvidenceGovernanceService'),
 'RevalidateNewsIntelligenceService': ('digital_twin.modules.news_intelligence.application.revalidate_articles',
                                       'RevalidateNewsIntelligenceService'),
 'SOURCE_REGISTRY_VERSION': ('digital_twin.modules.news_intelligence.domain.source',
                             'SOURCE_REGISTRY_VERSION'),
 'SourceRegistry': ('digital_twin.modules.news_intelligence.domain.source', 'SourceRegistry'),
 'annotate_evidence_eligibility': ('digital_twin.modules.news_intelligence.application.analyze_article',
                                   'annotate_evidence_eligibility'),
 'annotate_news_eligibility': ('digital_twin.modules.news_intelligence.domain.eligibility',
                               'annotate_news_eligibility'),
 'annotate_source_provenance': ('digital_twin.modules.news_intelligence.domain.provenance',
                                'annotate_source_provenance'),
 'assess_news_eligibility': ('digital_twin.modules.news_intelligence.domain.eligibility',
                             'assess_news_eligibility'),
 'evidence_eligibility': ('digital_twin.modules.news_intelligence.application.analyze_article',
                          'evidence_eligibility'),
 'inspect_article_body': ('digital_twin.modules.news_intelligence.domain.article_quality',
                          'inspect_article_body'),
 'publisher_identity': ('digital_twin.modules.news_intelligence.domain.provenance', 'publisher_identity'),
 'resolve_source_provenance': ('digital_twin.modules.news_intelligence.domain.provenance',
                               'resolve_source_provenance'),
 'resolve_target_entity': ('digital_twin.modules.news_intelligence.domain.entity_resolution',
                           'resolve_target_entity'),
 'story_identity': ('digital_twin.modules.news_intelligence.domain.story', 'story_identity')}

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
