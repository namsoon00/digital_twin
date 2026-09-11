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


_EXPORTS.update({
    'COMPANY_VALUATION_CONTEXT_VERSION': ('digital_twin.modules.news_intelligence.domain.company_knowledge', 'COMPANY_VALUATION_CONTEXT_VERSION'),
    'DISCLOSURE_ANALYSIS_PROMPT_VERSION': ('digital_twin.modules.news_intelligence.domain.disclosure_analysis', 'DISCLOSURE_ANALYSIS_PROMPT_VERSION'),
    'DisclosureAnalysisResult': ('digital_twin.modules.news_intelligence.domain.disclosure_analysis', 'DisclosureAnalysisResult'),
    'HypothesisResearchBrief': ('digital_twin.modules.news_intelligence.domain.investment_evidence_governance', 'HypothesisResearchBrief'),
    'MaterialityAssessment': ('digital_twin.modules.news_intelligence.domain.materiality', 'MaterialityAssessment'),
    'NEWS_MATERIALITY_STATE_LABELS': ('digital_twin.modules.news_intelligence.domain.news_analysis', 'NEWS_MATERIALITY_STATE_LABELS'),
    'NEWS_RELEVANCE_STATE_LABELS': ('digital_twin.modules.news_intelligence.domain.news_analysis', 'NEWS_RELEVANCE_STATE_LABELS'),
    'NEWS_SOURCE_TRUST_STATE_LABELS': ('digital_twin.modules.news_intelligence.domain.news_analysis', 'NEWS_SOURCE_TRUST_STATE_LABELS'),
    'NewsCollectionTarget': ('digital_twin.modules.news_intelligence.domain.investment_research', 'NewsCollectionTarget'),
    'RESEARCH_EVIDENCE_COLLECTED': ('digital_twin.modules.news_intelligence.domain.event_types', 'RESEARCH_EVIDENCE_COLLECTED'),
    'ReasoningGeneration': ('digital_twin.modules.news_intelligence.domain.investment_evidence_governance', 'ReasoningGeneration'),
    'ResearchEvidence': ('digital_twin.modules.news_intelligence.domain.investment_research', 'ResearchEvidence'),
    'ResearchReasoningHandoff': ('digital_twin.modules.news_intelligence.domain.investment_evidence_governance', 'ResearchReasoningHandoff'),
    'ResearchRun': ('digital_twin.modules.news_intelligence.domain.investment_evidence_governance', 'ResearchRun'),
    'active_company_valuation_rule_ids': ('digital_twin.modules.news_intelligence.domain.company_knowledge', 'active_company_valuation_rule_ids'),
    'build_active_investment_opinion': ('digital_twin.modules.news_intelligence.domain.investment_research', 'build_active_investment_opinion'),
    'claim_policy': ('digital_twin.modules.news_intelligence.domain.investment_evidence_governance', 'claim_policy'),
    'clean_article_summary_noise': ('digital_twin.modules.news_intelligence.domain.news_analysis', 'clean_article_summary_noise'),
    'clean_summary_text': ('digital_twin.modules.news_intelligence.domain.news_ai_analysis', 'clean_summary_text'),
    'compact_evidence_delta_event_payloads': ('digital_twin.modules.news_intelligence.domain.event_payloads', 'compact_evidence_delta_event_payloads'),
    'compact_materiality_assessment_event_payloads': ('digital_twin.modules.news_intelligence.domain.event_payloads', 'compact_materiality_assessment_event_payloads'),
    'compact_research_item_for_event_storage': ('digital_twin.modules.news_intelligence.domain.event_payloads', 'compact_research_item_for_event_storage'),
    'company_knowledge_by_symbol': ('digital_twin.modules.news_intelligence.domain.company_knowledge', 'company_knowledge_by_symbol'),
    'company_prompt_context': ('digital_twin.modules.news_intelligence.domain.company_knowledge', 'company_prompt_context'),
    'company_valuation_context': ('digital_twin.modules.news_intelligence.domain.company_knowledge', 'company_valuation_context'),
    'complete_reasoning_handoff': ('digital_twin.modules.news_intelligence.domain.investment_evidence_governance', 'complete_reasoning_handoff'),
    'configured_event_max_age_minutes': ('digital_twin.modules.news_intelligence.domain.evidence_time', 'configured_event_max_age_minutes'),
    'disclosure_analysis_payload': ('digital_twin.modules.news_intelligence.domain.disclosure_analysis', 'disclosure_analysis_payload'),
    'disclosure_reasoning_eligibility': ('digital_twin.modules.news_intelligence.domain.disclosure_quality', 'disclosure_reasoning_eligibility'),
    'event_time_contract': ('digital_twin.modules.news_intelligence.domain.evidence_time', 'event_time_contract'),
    'evidence_inference_signature': ('digital_twin.modules.news_intelligence.domain.evidence_delta', 'evidence_inference_signature'),
    'evidence_materiality': ('digital_twin.modules.news_intelligence.domain.materiality', 'evidence_materiality'),
    'governed_evidence': ('digital_twin.modules.news_intelligence.domain.investment_evidence_governance', 'governed_evidence'),
    'inference_eligible': ('digital_twin.modules.news_intelligence.domain.evidence_delta', 'inference_eligible'),
    'latest_source_as_of': ('digital_twin.modules.news_intelligence.domain.company_knowledge', 'latest_source_as_of'),
    'local_disclosure_analysis': ('digital_twin.modules.news_intelligence.domain.disclosure_analysis', 'local_disclosure_analysis'),
    'market_change_materiality': ('digital_twin.modules.news_intelligence.domain.materiality', 'market_change_materiality'),
    'merge_company_knowledge_rows': ('digital_twin.modules.news_intelligence.domain.company_knowledge', 'merge_company_knowledge_rows'),
    'news_state_payload': ('digital_twin.modules.news_intelligence.domain.news_analysis', 'news_state_payload'),
    'news_state_rank': ('digital_twin.modules.news_intelligence.domain.news_analysis', 'news_state_rank'),
    'normalized_article_title': ('digital_twin.modules.news_intelligence.domain.news_analysis', 'normalized_article_title'),
    'relation_scope_is_excluded': ('digital_twin.modules.news_intelligence.domain.news_analysis', 'relation_scope_is_excluded'),
    'relation_scope_is_investable': ('digital_twin.modules.news_intelligence.domain.news_analysis', 'relation_scope_is_investable'),
    'research_evidence_collected_event': ('digital_twin.modules.news_intelligence.domain.integration_events', 'research_evidence_collected_event'),
    'research_evidence_from_external_signals': ('digital_twin.modules.news_intelligence.domain.investment_research', 'research_evidence_from_external_signals'),
    'research_evidence_from_facts': ('digital_twin.modules.news_intelligence.domain.investment_research', 'research_evidence_from_facts'),
    'research_evidence_from_payload': ('digital_twin.modules.news_intelligence.domain.investment_research', 'research_evidence_from_payload'),
    'split_labeled_text': ('digital_twin.modules.news_intelligence.domain.disclosure_analysis', 'split_labeled_text'),
    'summary_texts_similar': ('digital_twin.modules.news_intelligence.domain.news_ai_analysis', 'summary_texts_similar'),
    'unique_texts': ('digital_twin.modules.news_intelligence.domain.investment_evidence_governance', 'unique_texts'),
})

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
