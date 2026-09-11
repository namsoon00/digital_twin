"""Explicit, lazy contracts surface for the news_intelligence module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'COMPANY_ALIASES': ('digital_twin.modules.news_intelligence.domain.entity', 'COMPANY_ALIASES'),
 'NEWS_INTELLIGENCE_VERSION': ('digital_twin.modules.news_intelligence.domain.version',
                               'NEWS_INTELLIGENCE_VERSION'),
 'alias_pattern': ('digital_twin.modules.news_intelligence.domain.entity_resolution', 'alias_pattern'),
 'annotate_news_eligibility': ('digital_twin.modules.news_intelligence.domain.eligibility',
                               'annotate_news_eligibility'),
 'apply_enrichment_snapshot': ('digital_twin.modules.news_intelligence.domain.article',
                               'apply_enrichment_snapshot'),
 'article_enrichment_revision': ('digital_twin.modules.news_intelligence.domain.article',
                                 'article_enrichment_revision'),
 'article_source_revision': ('digital_twin.modules.news_intelligence.domain.article',
                             'article_source_revision'),
 'assess_news_eligibility': ('digital_twin.modules.news_intelligence.domain.eligibility',
                             'assess_news_eligibility'),
 'authoritative_enrichment': ('digital_twin.modules.news_intelligence.domain.article',
                              'authoritative_enrichment'),
 'authoritative_event_takeaway': ('digital_twin.modules.news_intelligence.domain.article',
                                  'authoritative_event_takeaway'),
 'clear_resolved_analysis_conflict': ('digital_twin.modules.news_intelligence.domain.article',
                                      'clear_resolved_analysis_conflict'),
 'enrichment_payload_snapshot': ('digital_twin.modules.news_intelligence.domain.article',
                                 'enrichment_payload_snapshot'),
 'event_episode_identity': ('digital_twin.modules.news_intelligence.domain.story',
                            'event_episode_identity'),
 'inspect_article_body': ('digital_twin.modules.news_intelligence.domain.article_quality',
                          'inspect_article_body'),
 'news_event_fingerprint': ('digital_twin.modules.news_intelligence.domain.story',
                            'news_event_fingerprint'),
 'resolve_source_provenance': ('digital_twin.modules.news_intelligence.domain.provenance',
                               'resolve_source_provenance'),
 'resolve_target_entity': ('digital_twin.modules.news_intelligence.domain.entity_resolution',
                           'resolve_target_entity'),
 'story_identity': ('digital_twin.modules.news_intelligence.domain.story', 'story_identity')}

_EXPORTS['InvestmentResearchRepository'] = ('digital_twin.modules.news_intelligence.domain.repositories', 'InvestmentResearchRepository')
_EXPORTS['ResearchEvidenceRepository'] = ('digital_twin.modules.news_intelligence.domain.repositories', 'ResearchEvidenceRepository')
_EXPORTS['ResearchEvidenceGateway'] = ('digital_twin.modules.news_intelligence.domain.repositories', 'ResearchEvidenceGateway')

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
