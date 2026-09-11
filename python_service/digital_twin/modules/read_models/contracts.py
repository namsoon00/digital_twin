"""Explicit, lazy contracts surface for the read_models module."""

from digital_twin.modules._exports import resolve_export


_EXPORTS = {'InstrumentTimelineQuery': ('digital_twin.modules.read_models.domain.instrument_timeline',
                             'InstrumentTimelineQuery'),
 'normalize_instrument_symbol': ('digital_twin.modules.read_models.domain.instrument_timeline',
                                 'normalize_instrument_symbol')}


_EXPORTS.update({
    'CustomerInvestmentDocument': ('digital_twin.modules.read_models.domain.customer_investment_document', 'CustomerInvestmentDocument'),
    'CustomerInvestmentLink': ('digital_twin.modules.read_models.domain.customer_investment_document', 'CustomerInvestmentLink'),
    'CustomerInvestmentSection': ('digital_twin.modules.read_models.domain.customer_investment_document', 'CustomerInvestmentSection'),
    'FIELD_LABELS': ('digital_twin.modules.read_models.domain.customer_evidence_explanation', 'FIELD_LABELS'),
    'INVESTMENT_FLOW_VERSION': ('digital_twin.modules.read_models.domain.investment_flow', 'INVESTMENT_FLOW_VERSION'),
    'build_customer_evidence_explanations': ('digital_twin.modules.read_models.domain.customer_evidence_explanation', 'build_customer_evidence_explanations'),
    'customer_evidence_rows': ('digital_twin.modules.read_models.domain.customer_evidence_explanation', 'customer_evidence_rows'),
    'customer_follow_up_condition_clause': ('digital_twin.modules.read_models.domain.customer_investment_document', 'customer_follow_up_condition_clause'),
    'customer_investment_document_from_dict': ('digital_twin.modules.read_models.domain.customer_investment_document', 'customer_investment_document_from_dict'),
    'customer_investment_document_quality': ('digital_twin.modules.read_models.domain.customer_investment_document', 'customer_investment_document_quality'),
    'customer_safe_text': ('digital_twin.modules.read_models.domain.customer_evidence_explanation', 'customer_safe_text'),
    'customer_text_quality_issues': ('digital_twin.modules.read_models.domain.customer_evidence_explanation', 'customer_text_quality_issues'),
    'enforce_customer_message_quality': ('digital_twin.modules.read_models.domain.customer_evidence_explanation', 'enforce_customer_message_quality'),
    'investment_decision_key': ('digital_twin.modules.read_models.domain.investment_analysis', 'investment_decision_key'),
    'investment_flow_id': ('digital_twin.modules.read_models.domain.investment_flow', 'investment_flow_id'),
    'investment_product_readiness': ('digital_twin.modules.read_models.domain.investment_product_readiness', 'investment_product_readiness'),
    'is_non_final_publication': ('digital_twin.modules.read_models.domain.customer_evidence_explanation', 'is_non_final_publication'),
    'non_final_publication_summary': ('digital_twin.modules.read_models.domain.customer_evidence_explanation', 'non_final_publication_summary'),
    'normalized_customer_investment_document': ('digital_twin.modules.read_models.domain.customer_investment_document', 'normalized_customer_investment_document'),
    'publication_outcome_kind': ('digital_twin.modules.read_models.domain.customer_evidence_explanation', 'publication_outcome_kind'),
    'reasoning_detail_snapshot': ('digital_twin.modules.read_models.domain.investment_reasoning_detail', 'reasoning_detail_snapshot'),
    'subject_reasoning_lineage': ('digital_twin.modules.read_models.domain.investment_reasoning_detail', 'subject_reasoning_lineage'),
})

__all__ = list(_EXPORTS)


def __getattr__(name):
    return resolve_export(__name__, _EXPORTS, name)
